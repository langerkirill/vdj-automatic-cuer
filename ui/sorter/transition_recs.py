"""Live transition recommendations: history + cued library filter + Gemini ranking."""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from .llm import DEFAULT_MODEL, ask_json, load_api_key, models_to_try

from .autocue_path import ensure_autocue_on_path
from .config import (
    AUDIO_EXTENSIONS,
    CUES_SORTED,
    LIBRARIES,
    LIBRARY_SKIP_DIR_NAMES,
    READY_FOR_SORT,
    VDJ_DATABASE,
)
from .musical_key import (
    camelot_compatible,
    energy_bucket_from_folder,
    genre_family,
    genres_compatible,
    key_to_camelot,
    unescape_xml_text,
    vibe_label_from_path,
)
from .genre_guess import resolve_source_genre
from .relocate import summarize_cues, vdj_bpm_to_actual
from .transition_timing import (
    best_timing,
    markers_from_cue_summary,
    mix_out_windows,
    parse_markers,
    parse_pois_from_song_xml,
)
from .transitions_db import lookup_options, normalize_key
from .vdj_now_playing import get_now_playing, todays_history_plays

ensure_autocue_on_path()

MODEL_FALLBACKS = models_to_try(DEFAULT_MODEL)

BPM_TOLERANCE = float(os.getenv("MUSIC_SORTER_REC_BPM_TOLERANCE", "5"))
MAX_CANDIDATES_TO_GEMINI = int(os.getenv("MUSIC_SORTER_REC_CANDIDATE_CAP", "48"))
MAX_SCAN_SONGS = int(os.getenv("MUSIC_SORTER_REC_SCAN_CAP", "4000"))
PICKS_PER_BUCKET = int(os.getenv("MUSIC_SORTER_REC_PICKS_PER_BUCKET", "5"))


class EnergyPickSchema(BaseModel):
    path: str = Field(description="Exact file path from the candidate list")
    title: str = ""
    artist: str = ""
    reason: str = Field(description="Why this works after the current track")
    confidence: float = Field(ge=0.0, le=1.0, default=0.7)


class TransitionRecSchema(BaseModel):
    higher_energy: list[EnergyPickSchema] = Field(default_factory=list)
    same_energy: list[EnergyPickSchema] = Field(default_factory=list)
    lower_energy: list[EnergyPickSchema] = Field(default_factory=list)
    notes: str = Field(
        default="",
        description="Optional overall guidance for the DJ",
    )


@dataclass
class Candidate:
    path: str
    name: str
    artist: str
    title: str
    bpm: Optional[float]
    key: str
    camelot: str
    cue_count: int
    library: str
    relative_path: str
    history_count: int = 0
    history_sources: list[str] = field(default_factory=list)
    energy_hint: str = "same"
    genre: str = ""
    vibe: str = ""
    score: float = 0.0
    timing_score: float = 0.0
    timing: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RecJob:
    id: str
    status: str  # queued | running | ok | error
    created_at: float
    finished_at: Optional[float] = None
    message: str = ""
    source_path: str = ""
    source: dict[str, Any] = field(default_factory=dict)
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "message": self.message,
            "source_path": self.source_path,
            "source": self.source,
            "result": self.result,
            "error": self.error,
        }


_jobs: dict[str, RecJob] = {}
_jobs_lock = threading.Lock()
_scan_cache: dict[str, Any] = {"ts": 0.0, "songs": []}
_scan_lock = threading.Lock()


def _load_api_key() -> str:
    return load_api_key()


def _optional_float(value: Optional[str]) -> Optional[float]:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def audio_file_exists(path: str) -> bool:
    """True when path is a real audio file on disk."""
    if not path:
        return False
    try:
        return Path(path).is_file()
    except OSError:
        return False


def _scan_library_songs_from_database(
    *,
    force: bool = False,
) -> list[dict[str, Any]]:
    """
    Lightweight pass over database.xml for songs under House/Zouk/Cues Sorted/Ready.

    Returns dicts: path, artist, title, bpm, key, genre, vibe, cue_count, library, relative_path
    """
    now = time.time()
    with _scan_lock:
        cached = _scan_cache.get("songs") or []
        # Bust cache when genre field missing (older scans)
        cache_ok = (
            not force
            and cached
            and now - float(_scan_cache["ts"]) < 90
            and ("genre" in cached[0] if cached else False)
            and ("cues" in cached[0] if cached else False)
            and ("song_length" in cached[0] if cached else False)
        )
        if cache_ok:
            return [
                s
                for s in cached
                if audio_file_exists(s.get("path") or "") and int(s.get("cue_count") or 0) > 0
            ]

    db = VDJ_DATABASE
    if not db.is_file():
        return []

    roots: list[tuple[str, Path]] = [
        ("House", LIBRARIES["House"].resolve()),
        ("Zouk", LIBRARIES["Zouk"].resolve()),
        ("Cues Sorted", CUES_SORTED.resolve()),
        ("Ready for Sort", READY_FOR_SORT.resolve()),
    ]
    root_strs = [(name, str(p)) for name, p in roots if p.is_dir()]

    try:
        from vdj_database_safety import read_vdj_database_text

        content = read_vdj_database_text(db)
    except Exception:
        content = db.read_text(encoding="utf-8", errors="replace")

    songs: list[dict[str, Any]] = []
    # Split on Song open tags to avoid loading full DOM
    parts = re.split(r"(?=<Song\b)", content)
    for chunk in parts:
        if not chunk.startswith("<Song"):
            continue
        fp_m = re.search(r'FilePath="([^"]+)"', chunk[:500])
        if not fp_m:
            continue
        path = unescape_xml_text(fp_m.group(1))
        lib = ""
        rel = ""
        for name, root in root_strs:
            if path.startswith(root + "/") or path.startswith(root + "\\"):
                lib = name
                try:
                    rel = str(Path(path).relative_to(root))
                except ValueError:
                    rel = Path(path).name
                break
        if not lib:
            continue
        if Path(path).suffix.lower() not in AUDIO_EXTENSIONS:
            continue
        if not audio_file_exists(path):
            continue
        if any(part in LIBRARY_SKIP_DIR_NAMES for part in Path(path).parts):
            continue

        cue_n = len(re.findall(r'Type="cue"', chunk, flags=re.I))
        # Require at least one real cue (Num often present; still count Type=cue)
        if cue_n <= 0:
            # also allow Type='cue'
            cue_n = len(re.findall(r"Type='cue'", chunk, flags=re.I))
        if cue_n <= 0:
            continue

        tags_m = re.search(r"<Tags\b([^>]*)/?>", chunk)
        attrs = tags_m.group(1) if tags_m else ""
        author_m = re.search(r'Author="([^"]*)"', attrs)
        title_m = re.search(r'Title="([^"]*)"', attrs)
        genre_m = re.search(r'Genre="([^"]*)"', attrs)
        key_m = re.search(r'Key="([^"]*)"', attrs) or re.search(
            r'Key="([^"]*)"', chunk[:2000]
        )
        bpm_m = re.search(r'<Scan\b[^>]*\bBpm="([^"]+)"', chunk) or re.search(
            r'\bBpm="([^"]+)"', attrs
        )
        bpm = vdj_bpm_to_actual(_optional_float(bpm_m.group(1) if bpm_m else None))
        key = (key_m.group(1) if key_m else "").strip()
        artist = author_m.group(1) if author_m else ""
        title = title_m.group(1) if title_m else Path(path).stem
        genre = unescape_xml_text(genre_m.group(1) if genre_m else "")
        rel_norm = rel.replace("\\", "/")
        vibe = vibe_label_from_path(rel_norm, lib)
        pois = parse_pois_from_song_xml(chunk)
        length_m = re.search(r'SongLength="([^"]+)"', chunk)
        try:
            song_length = float(length_m.group(1)) if length_m else None
        except (TypeError, ValueError):
            song_length = None
        songs.append(
            {
                "path": path,
                "name": Path(path).name,
                "artist": artist,
                "title": title,
                "bpm": bpm,
                "key": key,
                "camelot": key_to_camelot(key) or "",
                "genre": genre,
                "vibe": vibe,
                "cue_count": cue_n,
                "library": lib,
                "relative_path": rel_norm,
                "energy_hint": energy_bucket_from_folder(rel_norm),
                "cues": pois,
                "song_length": song_length,
            }
        )
        if len(songs) >= MAX_SCAN_SONGS:
            break

    with _scan_lock:
        _scan_cache["ts"] = time.time()
        _scan_cache["songs"] = songs
    return list(songs)


def _history_counts_for(from_label: str) -> dict[str, dict[str, Any]]:
    """Map normalized destination label → history/note option."""
    opts = lookup_options(from_label, limit=40)
    out: dict[str, dict[str, Any]] = {}
    for o in opts:
        k = normalize_key(o.get("to_label") or "")
        if k:
            out[k] = o
    return out


def track_identity_key(
    *,
    path: str = "",
    artist: str = "",
    title: str = "",
    name: str = "",
) -> str:
    """Stable id for deduping the same song across libraries / Gemini picks."""
    label = normalize_key(f"{artist} {title}".strip())
    if label:
        return f"label:{label}"
    base = (name or Path(path).name or "").strip().lower()
    # Strip leading "31. " style index prefixes for identity
    base = re.sub(r"^\d+\.\s*", "", base)
    base = re.sub(r"\.(m4a|mp3|flac|wav|aiff?)$", "", base, flags=re.I)
    if base:
        return f"name:{normalize_key(base)}"
    if path:
        return f"path:{path.lower()}"
    return ""


def track_block_keys(
    *,
    path: str = "",
    artist: str = "",
    title: str = "",
    name: str = "",
) -> set[str]:
    """Identity keys used to block the same song (any library copy / filename)."""
    keys: set[str] = set()
    ident = track_identity_key(path=path, artist=artist, title=title, name=name)
    if ident:
        keys.add(ident)
    fname = name or (Path(path).name if path else "")
    stem = Path(fname).stem
    stem = re.sub(r"^\d+[\s.\-]+", "", stem).strip()
    if stem:
        keys.add(f"name:{normalize_key(stem)}")
        if any(sep in stem for sep in (" - ", " – ", " — ")):
            keys.add(f"label:{normalize_key(stem)}")
    if path:
        keys.add(f"path:{Path(path).name.lower()}")
        keys.add(f"path:{path.lower()}")
    return {k for k in keys if k and not k.endswith(":")}


def played_today_block_keys(
    plays: list[tuple[int, str, str, str]] | None = None,
) -> set[str]:
    """Block keys for every track already played today in VirtualDJ."""
    blocked: set[str] = set()
    for _lp, path, artist, title in plays if plays is not None else todays_history_plays():
        blocked |= track_block_keys(
            path=path,
            artist=artist,
            title=title,
            name=Path(path).name if path else "",
        )
    return blocked


def is_same_track(
    *,
    source_path: str = "",
    source_artist: str = "",
    source_title: str = "",
    source_name: str = "",
    path: str = "",
    artist: str = "",
    title: str = "",
    name: str = "",
) -> bool:
    """True when candidate is the currently playing song (any library copy)."""
    src_id = track_identity_key(
        path=source_path,
        artist=source_artist,
        title=source_title,
        name=source_name or (Path(source_path).name if source_path else ""),
    )
    cand_id = track_identity_key(
        path=path,
        artist=artist,
        title=title,
        name=name or (Path(path).name if path else ""),
    )
    if src_id and cand_id and src_id == cand_id:
        return True
    if source_path and path:
        try:
            if str(Path(source_path).expanduser().resolve()) == str(
                Path(path).expanduser().resolve()
            ):
                return True
        except OSError:
            if source_path == path:
                return True
        src_base = Path(source_path).name.lower()
        if src_base and Path(path).name.lower() == src_base:
            return True
    return False


def sanitize_recommendation_buckets(
    recs: dict[str, Any],
    *,
    source: dict[str, Any],
    allowed_paths: set[str] | None = None,
    blocked_idents: set[str] | None = None,
) -> dict[str, Any]:
    """
    Drop the current track, reject unknown paths, and ensure each song appears
    in at most one energy bucket (higher → same → lower priority).
    """
    src_path = source.get("path") or ""
    src_artist = source.get("artist") or ""
    src_title = source.get("title") or ""
    src_name = source.get("name") or (Path(src_path).name if src_path else "")
    blocked = set(blocked_idents or ())

    seen: set[str] = set()
    out = dict(recs)

    def clean(picks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        cleaned: list[dict[str, Any]] = []
        for p in picks or []:
            path = (p.get("path") or "").strip()
            if not path:
                continue
            if allowed_paths is not None and path not in allowed_paths:
                continue
            if not audio_file_exists(path):
                continue
            artist = p.get("artist") or ""
            title = p.get("title") or ""
            name = p.get("name") or Path(path).name
            if is_same_track(
                source_path=src_path,
                source_artist=src_artist,
                source_title=src_title,
                source_name=src_name,
                path=path,
                artist=artist,
                title=title,
                name=name,
            ):
                continue
            if blocked and track_block_keys(
                path=path, artist=artist, title=title, name=name
            ) & blocked:
                continue
            ident = track_identity_key(
                path=path, artist=artist, title=title, name=name
            )
            if not ident or ident in seen:
                continue
            seen.add(ident)
            cleaned.append(p)
        return cleaned

    limit = max(1, PICKS_PER_BUCKET)
    out["higher_energy"] = clean(list(recs.get("higher_energy") or []))[:limit]
    out["same_energy"] = clean(list(recs.get("same_energy") or []))[:limit]
    out["lower_energy"] = clean(list(recs.get("lower_energy") or []))[:limit]
    return out


def build_candidates(
    *,
    source_path: str,
    source_bpm: Optional[float],
    source_key: str,
    source_artist: str = "",
    source_title: str = "",
    source_genre: str = "",
    source_vibe: str = "",
    source_genre_family: str = "",
    source_cues: list[dict[str, Any]] | None = None,
    source_length: float | None = None,
    bpm_tolerance: float = BPM_TOLERANCE,
) -> list[Candidate]:
    """Filter cued library tracks by BPM ±tol and Camelot-compatible key."""
    songs = _scan_library_songs_from_database()
    from_label = f"{source_artist} {source_title}".strip() or Path(source_path).stem
    hist = _history_counts_for(from_label)
    src_name = Path(source_path).name if source_path else ""
    # Prefer the family resolve_source_genre already chose (Gemini/tag/path).
    # Do not re-scan artist/title — "India Arie" must not become tribal.
    src_fam = (source_genre_family or "").strip() or genre_family(
        source_genre, source_vibe
    )
    source_markers = parse_markers(source_cues or [])
    played_today = played_today_block_keys()

    cands: list[Candidate] = []
    for s in songs:
        path = s["path"]
        artist = s.get("artist") or ""
        title = s.get("title") or ""
        name = s.get("name") or Path(path).name
        if not audio_file_exists(path):
            continue
        if int(s.get("cue_count") or 0) <= 0:
            continue
        if is_same_track(
            source_path=source_path,
            source_artist=source_artist,
            source_title=source_title,
            source_name=src_name,
            path=path,
            artist=artist,
            title=title,
            name=name,
        ):
            continue
        if played_today and track_block_keys(
            path=path, artist=artist, title=title, name=name
        ) & played_today:
            continue

        bpm = s.get("bpm")
        if source_bpm and bpm:
            if abs(float(bpm) - float(source_bpm)) > bpm_tolerance:
                continue
        elif source_bpm and not bpm:
            # keep unscored BPM only if history strongly supports
            pass

        key = s.get("key") or ""
        if source_key and key:
            if not camelot_compatible(source_key, key):
                continue
        elif source_key and not key:
            # drop unknown keys when source has a key (strict harmonic filter)
            continue

        genre = s.get("genre") or ""
        vibe = s.get("vibe") or ""
        label = f"{artist} {title}".strip() or name
        h = hist.get(normalize_key(label)) or hist.get(normalize_key(name))
        history_count = int((h or {}).get("count") or 0)
        history_sources: list[str] = []
        if h:
            history_sources.append(str(h.get("source") or "history"))
            if h.get("note"):
                history_sources.append("note")

        score = 0.0
        if history_count:
            score += 20 + min(history_count, 30)
        if source_bpm and bpm and abs(float(bpm) - float(source_bpm)) <= 2:
            score += 5
        if key_to_camelot(source_key) and key_to_camelot(key) == key_to_camelot(
            source_key
        ):
            score += 4
        score += min(int(s.get("cue_count") or 0), 8)
        # Genre / folder vibe affinity (soft — not a hard filter)
        cand_fam = genre_family(genre, vibe, artist=artist, title=title)
        # Prefer a descriptive genre label for UI when tag empty but family known
        display_genre = genre
        if not display_genre and cand_fam == "psy_tribal_world":
            display_genre = "Tribal / psychedelic"
        elif not display_genre and cand_fam == "rnb_soul_zouk":
            display_genre = "Zouk / R&B-adjacent"
        elif not display_genre and cand_fam == "house_dance":
            display_genre = "House / dance"

        src_vibe_l = (source_vibe or "").lower()
        weak_source = (not src_fam) and (
            not source_genre
            or "add cues" in src_vibe_l
            or "ready for sort" in src_vibe_l
        )
        if src_fam and cand_fam:
            if src_fam == cand_fam:
                score += 16
            else:
                score -= 14  # key/BPM ok, but different set room
        elif weak_source and cand_fam == "psy_tribal_world":
            # Unlabeled modern vocals in Add Cues rarely want India/tribal as "same"
            score -= 18
        elif weak_source and cand_fam == "rnb_soul_zouk":
            # Soft default for Add Cues vocal tracks → zouk/R&B/kiz room
            score += 10
        elif genres_compatible(source_genre, source_vibe, genre, vibe):
            score += 8
        if source_genre and genre and source_genre.lower() == genre.lower():
            score += 4

        try:
            cand_length = float(s["song_length"]) if s.get("song_length") else None
        except (TypeError, ValueError):
            cand_length = None
        timing = best_timing(
            source_markers,
            parse_markers(s.get("cues") or []),
            source_length=source_length,
            cand_length=cand_length,
        )
        timing_score = float((timing or {}).get("score") or 0.0)
        if timing_score:
            score += min(18.0, timing_score)

        cands.append(
            Candidate(
                path=path,
                name=name,
                artist=artist,
                title=title,
                bpm=float(bpm) if bpm else None,
                key=key,
                camelot=s.get("camelot") or key_to_camelot(key) or "",
                cue_count=int(s.get("cue_count") or 0),
                library=s.get("library") or "",
                relative_path=s.get("relative_path") or "",
                history_count=history_count,
                history_sources=history_sources,
                energy_hint=s.get("energy_hint") or "same",
                genre=display_genre,
                vibe=vibe,
                score=score,
                timing_score=timing_score,
                timing=timing,
            )
        )

    # Prefer one copy of each track when it lives in multiple libs:
    # existing file first, then Cues Sorted, then richest genre/score.
    _lib_rank = {"Cues Sorted": 3, "House": 2, "Zouk": 2, "Ready for Sort": 1}
    best_by_label: dict[str, Candidate] = {}
    for c in cands:
        k = track_identity_key(
            path=c.path, artist=c.artist, title=c.title, name=c.name
        )
        prev = best_by_label.get(k)
        if prev is None:
            best_by_label[k] = c
            continue

        def _rich(x: Candidate) -> tuple:
            exists = 1 if audio_file_exists(x.path) else 0
            return (
                exists,
                _lib_rank.get(x.library, 0),
                1 if x.genre else 0,
                1
                if genre_family(x.genre, x.vibe, artist=x.artist, title=x.title)
                else 0,
                x.score,
                x.cue_count,
            )

        if _rich(c) > _rich(prev):
            best_by_label[k] = c
    cands = list(best_by_label.values())
    cands.sort(key=lambda c: c.score, reverse=True)
    return cands


def _gemini_rank(
    *,
    source: dict[str, Any],
    candidates: list[Candidate],
) -> dict[str, Any]:
    # Cap payload
    top = candidates[:MAX_CANDIDATES_TO_GEMINI]
    cand_lines = []
    for i, c in enumerate(top, 1):
        hist = (
            f" history×{c.history_count}"
            if c.history_count
            else ""
        )
        genre_bit = c.genre or "—"
        vibe_bit = c.vibe or "—"
        timing_bit = ""
        if c.timing and c.timing.get("summary"):
            timing_bit = f" | mix={c.timing['summary']}"
        cand_lines.append(
            f"{i}. path={c.path}\n"
            f"   {c.artist} — {c.title} | {c.bpm or '?'} BPM | key {c.key or '?'} "
            f"({c.camelot or '?'}) | genre={genre_bit} | vibe/folder={vibe_bit} | "
            f"{c.library}/{c.relative_path} | cues={c.cue_count} | "
            f"folder_energy_hint={c.energy_hint}{hist}{timing_bit}"
        )
    src_genre = source.get("genre") or "—"
    src_vibe = source.get("vibe") or "—"
    src_origin = {
        "gemini": " (model guess — treat this as the working genre; ignore inbox folders)",
        "tag": " (VDJ Genre tag)",
        "path": " (from library folder)",
    }.get(str(source.get("genre_source") or ""), "")
    src_family = source.get("genre_family") or "—"
    mix_windows = source.get("mix_windows") or []
    if mix_windows:
        hole_lines = []
        for w in mix_windows[:3]:
            present = ", ".join(w.get("present") or []) or "—"
            missing = ", ".join(w.get("missing") or []) or "—"
            hole_lines.append(
                f"  - {w.get('time')} {w.get('label')} · present {present} · missing {missing}"
            )
        mix_hole_block = "MIX-OUT HOLES (prefer these times on the current track):\n" + "\n".join(
            hole_lines
        )
    else:
        mix_hole_block = "MIX-OUT HOLES: unknown (cue names were too thin to infer)."
    prompt = f"""You are a working DJ coach for harmonic mixing AND genre/vibe continuity.

CURRENT TRACK (on deck / just played):
  path: {source.get('path')}
  {source.get('artist')} — {source.get('title')}
  BPM: {source.get('bpm')}
  Key: {source.get('key')} (Camelot {source.get('camelot')})
  Genre: {src_genre}{src_origin}
  Genre family: {src_family}
  Vibe / folder: {src_vibe}
  cues: {source.get('cue_count')}
{mix_hole_block}

CANDIDATES (hard-filtered pool — ONLY these are legal):
- Mixable Camelot key (same, relative major/minor, or ±1 adjacent on the wheel)
- BPM within ±{BPM_TOLERANCE} of the current track
- Cued library tracks the DJ can actually transition to
- Each candidate lists genre (tag) and vibe/folder (library path context)

From this filtered pool, pick the BEST next tracks in THREE energy buckets:

1) higher_energy — lift the floor / build without leaving the harmonic pocket
2) same_energy — hold the groove, vibe, GENRE FAMILY, and pocket
3) lower_energy — cool down / reset while still in key and tempo range

Genre / vibe rules (critical):
- BPM + key match is NOT enough. A psychedelic/tribal/organic track (e.g. Desert
  Dwellers, India folder, Tribal tag) is NOT "same energy" as contemporary R&B,
  neo-soul, urban kiz, or pop-R&B — even at the same BPM/key.
- If Genre is a model guess or a VDJ tag, TRUST it for family continuity.
- If Genre is empty, INFER from artist + title only. Ignore inbox folders
  (Add Cues, Ready for Sort, AC Low Quality, Cues Sorted/Energy).
  Example: Rubí "Seadoo" → modern R&B / alternative R&B; not tribal/psy.
- same_energy: strongly prefer the same genre family and similar vibe/folder
  (e.g. R&B→R&B/soul/zouk/kiz; tribal/psy→tribal/organic/world; house→house/dance).
- higher_energy / lower_energy: energy can shift, but still prefer a plausible
  genre bridge (don't jump from intimate R&B into festival psy without reason).
- If you pick a genre contrast, say so honestly in reason and lower confidence.
- Prefer closer genre/vibe over a "perfect" key that sounds like a different set.
- In every reason, mention genre/vibe fit (or mismatch) in plain language.

Other rules:
- ONLY use paths exactly as listed in candidates (copy path string verbatim).
- NEVER recommend the CURRENT TRACK (or any library copy of it).
- NEVER recommend a track already played today (those are already removed from the pool).
- Each track path may appear in AT MOST ONE bucket total (no repeats across higher/same/lower).
- Every pick must stay in-key and within ±{BPM_TOLERANCE} BPM (already true of the list).
- Prefer tracks with history×N when musical fit is equal.
- Prefer closer BPM and exact Camelot match when deciding confidence.
- Return exactly up to {PICKS_PER_BUCKET} picks per bucket (never more; fewer only if the pool is thin). Prefer distinct songs.
- reason: one short DJ-facing sentence that mentions energy move + genre/vibe fit + the mix times when a mix= line exists (not only key/BPM).

Frequency-puzzle timing (critical):
- Treat cue names as arrangement + frequency layers (drums / bass / melody / vocals).
- A melodic downsection / breakdown / outro is MISSING drums — prefer an incoming
  drum section, drums-in, intro bed, or drop that fills those holes.
- A drum bed / drums-in is MISSING melody or vocals — prefer incoming melody/vocals,
  not another drum-only pile-up.
- Do not stack two vocal leads. Mention what frequency the incoming part supplies.
- Prefer the candidate mix= times when they look complementary.

CANDIDATE LIST:
{chr(10).join(cand_lines) if cand_lines else "(no candidates)"}
"""

    try:
        data = ask_json(prompt, TransitionRecSchema, temperature=0.4)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Gemini ranking failed: {exc}") from exc

    # Attach candidate metadata by path — reject anything not in the pool
    by_path = {c.path: c for c in top}
    allowed = set(by_path.keys())

    def _enrich(picks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for p in picks or []:
            path = (p.get("path") or "").strip()
            if not path or path not in allowed:
                continue
            if not audio_file_exists(path):
                continue
            c = by_path[path]
            if is_same_track(
                source_path=str(source.get("path") or ""),
                source_artist=str(source.get("artist") or ""),
                source_title=str(source.get("title") or ""),
                source_name=str(source.get("name") or ""),
                path=path,
                artist=c.artist,
                title=c.title,
                name=c.name,
            ):
                continue
            out.append(
                {
                    "path": path,
                    "title": p.get("title") or c.title,
                    "artist": p.get("artist") or c.artist,
                    "reason": p.get("reason") or "",
                    "confidence": float(p.get("confidence") or 0.7),
                    "bpm": c.bpm,
                    "key": c.key,
                    "camelot": c.camelot,
                    "genre": c.genre,
                    "vibe": c.vibe,
                    "library": c.library,
                    "relative_path": c.relative_path,
                    "cue_count": c.cue_count,
                    "history_count": c.history_count,
                    "name": c.name,
                    "timing": c.timing,
                    "timing_score": c.timing_score,
                }
            )
        return out

    raw_recs = {
        "higher_energy": _enrich(data.get("higher_energy") or []),
        "same_energy": _enrich(data.get("same_energy") or []),
        "lower_energy": _enrich(data.get("lower_energy") or []),
        "notes": data.get("notes") or "",
        "model": DEFAULT_MODEL,
        "candidate_count": len(top),
    }
    return sanitize_recommendation_buckets(
        raw_recs, source=source, allowed_paths=allowed
    )


def _fallback_buckets(
    candidates: list[Candidate],
    *,
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """No-Gemini fallback: history-first, then folder energy hints (unique songs)."""
    higher, same, lower = [], [], []
    used: set[str] = set()
    for c in candidates[:36]:
        ident = track_identity_key(
            path=c.path, artist=c.artist, title=c.title, name=c.name
        )
        if not ident or ident in used:
            continue
        timing_summary = (c.timing or {}).get("summary") if c.timing else ""
        if c.history_count:
            reason = f"History ×{c.history_count}"
        elif timing_summary:
            reason = timing_summary
        else:
            reason = f"In-key · {c.bpm or '?'} BPM · {c.library}"
        item = {
            **c.to_dict(),
            "reason": reason,
            "confidence": min(0.95, 0.45 + c.score / 80.0),
        }
        bucket = c.energy_hint if c.energy_hint in {"higher", "same", "lower"} else "same"
        if c.history_count and bucket == "same":
            same.append(item)
        elif bucket == "higher":
            higher.append(item)
        elif bucket == "lower":
            lower.append(item)
        else:
            same.append(item)
        used.add(ident)

    def take(xs: list, n: int | None = None) -> list:
        return xs[: (n if n is not None else PICKS_PER_BUCKET)]

    raw = {
        "higher_energy": take(higher),
        "same_energy": take(same),
        "lower_energy": take(lower),
        "notes": "Gemini unavailable — ranked by history + folder energy heuristics.",
        "model": "fallback-heuristic",
        "candidate_count": len(candidates),
    }
    # If a bucket is empty, fill from remaining unique candidates without reusing
    claimed = {
        track_identity_key(
            path=p.get("path") or "",
            artist=p.get("artist") or "",
            title=p.get("title") or "",
            name=p.get("name") or "",
        )
        for bucket in ("higher_energy", "same_energy", "lower_energy")
        for p in raw[bucket]
    }
    leftovers = [
        c
        for c in candidates
        if track_identity_key(
            path=c.path, artist=c.artist, title=c.title, name=c.name
        )
        not in claimed
    ]
    for bucket in ("higher_energy", "same_energy", "lower_energy"):
        while len(raw[bucket]) < PICKS_PER_BUCKET and leftovers:
            c = leftovers.pop(0)
            raw[bucket].append(
                {
                    **c.to_dict(),
                    "reason": f"In-key · {c.bpm or '?'} BPM · {c.library}",
                    "confidence": min(0.9, 0.4 + c.score / 80.0),
                }
            )

    return sanitize_recommendation_buckets(
        raw,
        source=source or {},
        allowed_paths={c.path for c in candidates},
    )


def recommend_transitions(
    *,
    path: str | None = None,
    use_gemini: bool = True,
    force_rescan: bool = False,
) -> dict[str, Any]:
    """
    Build energy-bucketed next-track recommendations for the current (or given) track.
    """
    if force_rescan:
        _scan_library_songs_from_database(force=True)

    if path:
        cues = summarize_cues(path)
        from .vdj_now_playing import _song_key_from_database

        key = _song_key_from_database(path)
        source = {
            "path": str(Path(path).expanduser()),
            "name": Path(path).name,
            "artist": cues.author or "",
            "title": cues.title or Path(path).stem,
            "bpm": cues.bpm,
            "key": key,
            "camelot": key_to_camelot(key) or "",
            "cue_count": cues.cue_count,
            "is_cued": cues.is_cued,
            "source": "manual",
        }
    else:
        np = get_now_playing(enrich=True)
        if np is None:
            raise FileNotFoundError(
                "No recent VirtualDJ history play found. Play a track in VDJ first."
            )
        source = np.to_dict()
        try:
            cues = summarize_cues(source["path"])
        except Exception:
            cues = None

    source_markers, source_length = ([], None)
    if cues is not None:
        source_markers, source_length = markers_from_cue_summary(cues)
        if source_length and not source.get("song_length"):
            source["song_length"] = source_length
    if not source.get("mix_windows"):
        source["mix_windows"] = mix_out_windows(
            source_markers, song_length=source_length
        )
    source_cues = [
        {"name": m.name, "pos": m.pos, "kind": m.kind} for m in source_markers
    ]

    # Ensure genre/vibe on source (manual path may lack them)
    if not source.get("genre") or not source.get("vibe"):
        try:
            from .vdj_now_playing import _song_genre_and_vibe

            g, v = _song_genre_and_vibe(source["path"])
            if not source.get("genre"):
                source["genre"] = g
            if not source.get("vibe"):
                source["vibe"] = v
        except Exception:
            source.setdefault("genre", "")
            source.setdefault("vibe", "")

    resolved = resolve_source_genre(
        genre=source.get("genre") or "",
        vibe=source.get("vibe") or "",
        artist=source.get("artist") or "",
        title=source.get("title") or "",
        path=source.get("path") or "",
        name=source.get("name") or "",
        use_gemini=use_gemini,
    )
    source["genre"] = resolved["genre"]
    source["vibe"] = resolved["vibe"]
    source["genre_source"] = resolved["genre_source"]
    source["genre_family"] = resolved["genre_family"]
    if resolved.get("genre_guess"):
        source["genre_guess"] = resolved["genre_guess"]

    cands = build_candidates(
        source_path=source["path"],
        source_bpm=source.get("bpm"),
        source_key=source.get("key") or "",
        source_artist=source.get("artist") or "",
        source_title=source.get("title") or "",
        source_genre=source.get("genre") or "",
        source_vibe=source.get("vibe") or "",
        source_genre_family=source.get("genre_family") or "",
        source_cues=source_cues,
        source_length=source_length or source.get("song_length"),
    )

    history_preview = lookup_options(
        f"{source.get('artist','')} {source.get('title','')}".strip()
        or source.get("name", ""),
        limit=8,
    )

    if not cands:
        empty = {
            "ok": True,
            "source": source,
            "candidates_considered": 0,
            "history_options": history_preview,
            "recommendations": {
                "higher_energy": [],
                "same_energy": [],
                "lower_energy": [],
                "notes": "No in-key, ±BPM cued candidates found in House/Zouk/Cues Sorted.",
                "model": "",
                "candidate_count": 0,
            },
        }
        try:
            from .vdj_sideview_recs import write_sideview_recs

            empty["vdj_sideview"] = write_sideview_recs(empty)
        except Exception as exc:  # noqa: BLE001
            empty["vdj_sideview"] = {"ok": False, "error": str(exc)}
        return empty

    allowed = {c.path for c in cands}
    if use_gemini:
        try:
            recs = _gemini_rank(source=source, candidates=cands)
        except Exception as exc:  # noqa: BLE001
            recs = _fallback_buckets(cands, source=source)
            recs["notes"] = f"{recs.get('notes','')} ({exc})"
    else:
        recs = _fallback_buckets(cands, source=source)

    # Final hard guarantee: no current track, no today's repeats, no dupes
    recs = sanitize_recommendation_buckets(
        recs,
        source=source,
        allowed_paths=allowed,
        blocked_idents=played_today_block_keys(),
    )

    payload = {
        "ok": True,
        "source": source,
        "candidates_considered": len(cands),
        "candidates_preview": [c.to_dict() for c in cands[:40]],
        "history_options": history_preview,
        "recommendations": recs,
        "filters": {
            "bpm_tolerance": BPM_TOLERANCE,
            "require_key_compatible": True,
            "require_cued": True,
            "consider_genre": True,
            "consider_timing": True,
            "genre_source": source.get("genre_source") or "",
            "genre_family": source.get("genre_family") or "",
            "libraries": ["House", "Zouk", "Cues Sorted", "Ready for Sort"],
            "label": (
                f"In-key · ±{int(BPM_TOLERANCE)} BPM · genre-aware"
                + (
                    " (guessed)"
                    if source.get("genre_source") == "gemini"
                    else ""
                )
                + " · timing · no repeats today · cued"
            ),
        },
    }
    try:
        from .vdj_sideview_recs import write_sideview_recs

        payload["vdj_sideview"] = write_sideview_recs(payload)
    except Exception as exc:  # noqa: BLE001
        payload["vdj_sideview"] = {"ok": False, "error": str(exc)}
    return payload


def get_job(job_id: str) -> Optional[RecJob]:
    with _jobs_lock:
        return _jobs.get(job_id)


def start_recommend_job(
    *,
    path: str | None = None,
    use_gemini: bool = True,
    force_rescan: bool = False,
) -> RecJob:
    job = RecJob(
        id=uuid.uuid4().hex[:12],
        status="queued",
        created_at=time.time(),
        message="Queued transition recommendations…",
        source_path=path or "",
    )
    with _jobs_lock:
        _jobs[job.id] = job

    def worker() -> None:
        job.status = "running"
        job.message = "Scanning cued library + history…"
        try:
            result = recommend_transitions(
                path=path or None,
                use_gemini=use_gemini,
                force_rescan=force_rescan,
            )
            job.result = result
            job.status = "ok"
            job.message = (
                f"Ready · {result.get('candidates_considered', 0)} candidates"
            )
            job.source = result.get("source") or {}
        except Exception as exc:  # noqa: BLE001
            job.status = "error"
            job.error = str(exc)
            job.message = str(exc)
        finally:
            job.finished_at = time.time()

    threading.Thread(target=worker, daemon=True, name=f"tx-recs-{job.id}").start()
    return job
