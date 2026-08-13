"""Assemble a vibe-curated Zouk playlist with Gemini chunk scoring."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from .config import (
    ADD_CUES,
    ASSEMBLE_SKIP_DIR_NAMES,
    AUDIO_EXTENSIONS,
    CUES_ROOT,
    DJ_NOTES_ROOT,
    LIBRARIES,
    LIBRARY_SKIP_DIR_NAMES,
    SETS_ROOT,
    VDJ_DATABASE,
)
from .musical_key import (
    genre_family,
    key_to_camelot,
    unescape_xml_text,
    vibe_label_from_path,
)
from .audio_meta import MIN_BITRATE_KBPS, track_meets_bitrate_floor
from .relocate import vdj_bpm_to_actual
from .song_web import lookup_blurbs_for_tracks
from .transition_recs import track_block_keys
from .transitions_db import normalize_key

DEFAULT_EVENT = {
    "name": "Pajamathon 2026",
    "brief": (
        "Pajamathon is a late-month zouk marathon: several hours of social "
        "dancing in pajamas. Cozy, sensual, playful, late-night. Comfortable "
        "grooves you can live in — warm vocals, urban kiz / lounge zouk, "
        "beautiful-sound, mid-energy bodies. Not a peak-time club banger set, "
        "not aggressive tribal/psy, not joke tracks that break the spell. "
        "Prefer songs that feel like a long pajama party: intimate, groovy, "
        "repeatable, slightly dreamy."
    ),
}

DEFAULT_LIBRARY = "Zouk"
DEFAULT_CHUNK = 12
LISTEN_SECONDS = 28.0
LISTEN_BITRATE = "96k"
SCORE_CACHE_VERSION = 3  # 3 = heard a clip + web blurb
DEFAULT_TARGET = 400
MIN_TARGET = 300
MAX_TARGET = 500
MIN_CHUNK = 10
MAX_CHUNK = 20
NEWEST_GUARANTEE = 40
KEEP_FIT = 0.60
MAYBE_FIT = 0.45
DEFAULT_MIN_FIT = 0.60


def normalize_min_fit(raw: Any = None) -> float:
    """Accept 0–1 fractions or 0–100 percents. Default is the keep floor (60%)."""
    if raw is None or raw == "":
        return DEFAULT_MIN_FIT
    try:
        num = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_MIN_FIT
    if num > 1.5:
        num = num / 100.0
    return max(0.0, min(1.0, num))

DEFAULT_MODEL = os.getenv("MUSIC_SORTER_GEMINI_MODEL") or os.getenv(
    "GEMINI_MODEL", "gemini-3.5-flash"
)
MODEL_FALLBACKS = [
    DEFAULT_MODEL,
    "gemini-3.5-flash",
    "gemini-flash-latest",
    "gemini-3-flash-preview",
]

CACHE_PATH = DJ_NOTES_ROOT / "playlist_assemble_scores.json"
MIX_PREFS_PATH = DJ_NOTES_ROOT / "playlist_assemble_mix.json"
JOB_SNAPSHOT_PATH = DJ_NOTES_ROOT / "playlist_assemble_job.json"
LISTEN_CLIP_TIMEOUT_SEC = 25
_jobs: dict[str, "AssembleJob"] = {}
_live_workers: set[str] = set()
_jobs_lock = threading.Lock()
_cache_lock = threading.Lock()
_mix_prefs_lock = threading.Lock()
_job_snapshot_lock = threading.Lock()


class ChunkPickSchema(BaseModel):
    path: str = Field(description="Exact file path from the chunk")
    fit: float = Field(ge=0.0, le=1.0, description="How well it fits the event")
    verdict: str = Field(description="keep, maybe, or skip")
    reason: str = Field(description="One short DJ-facing clause")


class ChunkScoreSchema(BaseModel):
    picks: list[ChunkPickSchema] = Field(default_factory=list)


@dataclass
class AssembleJob:
    id: str
    status: str
    created_at: float
    event_name: str
    brief: str
    library: str
    chunk_size: int
    target: int
    message: str = ""
    finished_at: Optional[float] = None
    total: int = 0
    scored: int = 0
    kept: int = 0
    skipped: int = 0
    cancel: bool = False
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    lane_shares: Optional[dict[str, float]] = None
    min_fit: float = DEFAULT_MIN_FIT

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "event_name": self.event_name,
            "brief": self.brief,
            "library": self.library,
            "chunk_size": self.chunk_size,
            "target": self.target,
            "message": self.message,
            "total": self.total,
            "scored": self.scored,
            "kept": self.kept,
            "skipped": self.skipped,
            "result": self.result,
            "error": self.error,
            "lane_shares": normalize_lane_shares(self.lane_shares),
            "min_fit": normalize_min_fit(self.min_fit),
        }


def job_from_dict(raw: dict[str, Any]) -> AssembleJob:
    return AssembleJob(
        id=str(raw.get("id") or uuid.uuid4().hex[:12]),
        status=str(raw.get("status") or "ok"),
        created_at=float(raw.get("created_at") or time.time()),
        event_name=str(raw.get("event_name") or DEFAULT_EVENT["name"]),
        brief=str(raw.get("brief") or DEFAULT_EVENT["brief"]),
        library=str(raw.get("library") or DEFAULT_LIBRARY),
        chunk_size=clamp_chunk(raw.get("chunk_size")),
        target=clamp_target(raw.get("target")),
        message=str(raw.get("message") or ""),
        finished_at=(
            float(raw["finished_at"]) if raw.get("finished_at") is not None else None
        ),
        total=int(raw.get("total") or 0),
        scored=int(raw.get("scored") or 0),
        kept=int(raw.get("kept") or 0),
        skipped=int(raw.get("skipped") or 0),
        result=_strip_assemble_excluded_result(
            raw.get("result") if isinstance(raw.get("result"), dict) else None
        ),
        error=raw.get("error"),
        lane_shares=normalize_lane_shares(raw.get("lane_shares")),
        min_fit=normalize_min_fit(raw.get("min_fit")),
    )


def persist_job(job: AssembleJob) -> None:
    """Survive a UI/server restart so scoring can be resumed."""
    payload = job.to_dict()
    with _job_snapshot_lock:
        JOB_SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = JOB_SNAPSHOT_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(JOB_SNAPSHOT_PATH)


def load_job_snapshot() -> Optional[AssembleJob]:
    with _job_snapshot_lock:
        if not JOB_SNAPSHOT_PATH.is_file():
            return None
        try:
            raw = json.loads(JOB_SNAPSHOT_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
    if not isinstance(raw, dict) or not raw.get("id"):
        return None
    try:
        return job_from_dict(raw)
    except (TypeError, ValueError):
        return None


def _mark_orphaned_job(job: AssembleJob) -> AssembleJob:
    if job.status in {"running", "queued"} and job.id not in _live_workers:
        job.status = "ok"
        job.finished_at = job.finished_at or time.time()
        job.message = (
            "Scoring stopped — cached evals are saved. "
            "Click Assemble to hear the next songs."
        )
        persist_job(job)
    return job


def slug_event(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return slug or "event"


def event_folder_name(event_name: str) -> str:
    """Folder name under Music/DJ/Music/Sets — e.g. Pajamathon 2026."""
    name = (event_name or "").strip() or "Pajamathon"
    if re.fullmatch(r"pajama(?:thon)?", name, flags=re.I):
        return "Pajamathon 2026"
    if not re.search(r"20\d{2}", name):
        return f"{name} 2026"
    return name


def _safe_filename(raw: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', " ", raw or "").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:120] or "track"


def clone_cues_for_set_paths(
    pairs: list[tuple[str, str]],
    *,
    database_path: Path | None = None,
) -> dict[str, Any]:
    """
    Clone VDJ Song blocks (cues, loops, beatgrid) onto the set-folder copies.

    One database read + one write. Skips if VirtualDJ is open.
    """
    from .db_lock import vdj_db_write
    from .relocate import is_virtualdj_running

    empty = {
        "cloned": 0,
        "already_present": 0,
        "missing": 0,
        "skipped_vdj_open": False,
        "message": "",
    }
    if not pairs:
        return empty
    if is_virtualdj_running():
        return {
            **empty,
            "skipped_vdj_open": True,
            "message": (
                "VirtualDJ is open — audio is in the set folder, but cues were "
                "not cloned. Close VirtualDJ and click Write set folder."
            ),
        }

    from vdj_database_safety import (
        _find_song_span,
        directory_sort_label,
        normalize_database_path,
        read_vdj_database_text,
        song_xml_with_new_filepath,
    )

    db = Path(database_path or VDJ_DATABASE)
    if not db.is_file():
        return {**empty, "missing": len(pairs), "message": "No VirtualDJ database.xml"}

    def _named_poi_count(xml: str) -> int:
        return len(re.findall(r"<Poi\b[^>]*\bName=\"", xml))

    def _user2(xml: str) -> str:
        hit = re.search(r'\bUser2="([^"]*)"', xml)
        return hit.group(1) if hit else ""

    with vdj_db_write():
        content = read_vdj_database_text(db)
        inserts: list[str] = []
        replacements: list[tuple[int, int, str]] = []
        cloned = 0
        replaced = 0
        already = 0
        missing = 0
        for source, dest in pairs:
            dest_norm = normalize_database_path(dest)
            src_span = _find_song_span(content, source)
            if src_span is None:
                missing += 1
                continue
            src_xml = content[src_span[0] : src_span[1]]
            origin_label = directory_sort_label(source)
            cloned_xml = song_xml_with_new_filepath(
                src_xml, dest_norm, directory_sort_path=source
            )
            dest_span = _find_song_span(content, dest_norm)
            if dest_span is None:
                inserts.append(cloned_xml)
                cloned += 1
                continue
            dest_xml = content[dest_span[0] : dest_span[1]]
            cues_ok = _named_poi_count(dest_xml) >= _named_poi_count(src_xml)
            sort_ok = (not origin_label) or _user2(dest_xml) == origin_label
            if cues_ok and sort_ok:
                already += 1
                continue
            replacements.append((dest_span[0], dest_span[1], cloned_xml))
            replaced += 1
        dirty = False
        if replacements:
            replacements.sort(key=lambda item: item[0], reverse=True)
            pieces: list[str] = []
            cursor = len(content)
            for start, end, xml in replacements:
                pieces.append(content[end:cursor])
                pieces.append(xml)
                cursor = start
            pieces.append(content[:cursor])
            content = "".join(reversed(pieces))
            dirty = True
        if inserts:
            close_tag = "</VirtualDJ_Database>"
            close_idx = content.rfind(close_tag)
            if close_idx < 0:
                raise ValueError("Database is missing </VirtualDJ_Database>")
            prefix = content[:close_idx].rstrip(" \t")
            if not prefix.endswith("\n"):
                prefix += "\n"
            content = prefix + "\n".join(inserts) + "\n" + content[close_idx:]
            dirty = True
        if dirty:
            tmp = db.with_suffix(db.suffix + ".tmp")
            tmp.write_text(content, encoding="utf-8")
            tmp.replace(db)
    total = cloned + replaced
    return {
        "cloned": total,
        "inserted": cloned,
        "replaced": replaced,
        "already_present": already,
        "missing": missing,
        "skipped_vdj_open": False,
        "message": (
            f"Cloned cues for {total} songs"
            + (f" · {replaced} refreshed" if replaced else "")
            + (f" · {already} already complete" if already else "")
            + (f" · {missing} had no source cues" if missing else "")
        ),
    }


def _manual_cue_count(song_xml: str) -> int:
    """Count numbered Type=cue POIs (same rule as Add Cues readiness)."""
    count = 0
    for match in re.finditer(r"<Poi\b([^>]*)/?>", song_xml):
        attrs = match.group(1)
        kind = re.search(r'\bType="([^"]*)"', attrs)
        num = re.search(r'\bNum="([^"]*)"', attrs)
        if (
            kind
            and kind.group(1).lower() == "cue"
            and num
            and num.group(1) not in {"", "0"}
        ):
            count += 1
    return count


def stage_uncued_playlist_tracks(
    pairs: list[tuple[str, str]],
    *,
    folder_name: str = "Pajamathon",
    add_cues_root: Path | None = None,
    database_path: Path | None = None,
    clone_db: bool = True,
) -> dict[str, Any]:
    """
    Copy playlist songs that have no numbered cues into Add Cues/<folder>.

    Uses the library original (source) so you can cue it in Add Cues.
    """
    dest_root = (add_cues_root or ADD_CUES) / folder_name
    dest_root.mkdir(parents=True, exist_ok=True)

    from vdj_database_safety import _find_song_span, read_vdj_database_text

    db = Path(database_path or VDJ_DATABASE)
    content = read_vdj_database_text(db) if db.is_file() else ""

    staged: list[dict[str, str]] = []
    skipped_cued = 0
    missing_src = 0
    already = 0
    clone_pairs: list[tuple[str, str]] = []

    for source, _dest in pairs:
        src = Path(source)
        if not src.is_file():
            missing_src += 1
            continue
        span = _find_song_span(content, source) if content else None
        xml = content[span[0] : span[1]] if span else ""
        if _manual_cue_count(xml) > 0:
            skipped_cued += 1
            continue
        target = dest_root / src.name
        if target.exists():
            already += 1
            clone_pairs.append((str(src), str(target)))
            staged.append({"source": str(src), "add_cues": str(target), "existed": "1"})
            continue
        try:
            os.link(src, target)
        except OSError:
            import shutil

            shutil.copy2(src, target)
        stems = Path(f"{src}.vdjstems")
        if stems.is_file():
            stem_dest = Path(f"{target}.vdjstems")
            if not stem_dest.exists():
                try:
                    os.link(stems, stem_dest)
                except OSError:
                    import shutil

                    shutil.copy2(stems, stem_dest)
        clone_pairs.append((str(src), str(target)))
        staged.append({"source": str(src), "add_cues": str(target), "existed": "0"})

    cues = {"cloned": 0, "message": "Database clone skipped"}
    if clone_db and clone_pairs:
        cues = clone_cues_for_set_paths(clone_pairs, database_path=db)

    return {
        "ok": True,
        "folder": str(dest_root),
        "staged": len(staged),
        "already_there": already,
        "skipped_cued": skipped_cued,
        "missing_src": missing_src,
        "tracks": staged,
        "cues": cues,
    }


def materialize_set_directory(
    playlist: list[dict[str, Any]],
    *,
    event_name: str,
    sets_root: Path | None = None,
    clone_cues: bool = True,
    database_path: Path | None = None,
) -> dict[str, Any]:
    """
    Copy/hardlink the crate into Sets/<Event>, same place as Moon / Silesian.
    Then clone VirtualDJ cues onto those new paths.
    """
    root = Path(sets_root or SETS_ROOT)
    folder = root / event_folder_name(event_name)
    folder.mkdir(parents=True, exist_ok=True)
    written: list[dict[str, Any]] = []
    errors: list[str] = []
    cue_pairs: list[tuple[str, str]] = []
    for i, track in enumerate(playlist, 1):
        if track_is_assemble_excluded(track):
            continue
        src = Path(track.get("source_path") or track.get("path") or "")
        if not src.is_file():
            errors.append(str(src))
            continue
        artist = (track.get("artist") or "").strip()
        title = (track.get("title") or track.get("name") or src.stem).strip()
        label = f"{artist} - {title}" if artist else title
        dest = folder / f"{i:03d}. {_safe_filename(label)}{src.suffix.lower()}"
        if dest.exists():
            dest.unlink()
        try:
            os.link(src, dest)
        except OSError:
            try:
                import shutil

                shutil.copy2(src, dest)
            except OSError as exc:
                errors.append(f"{src.name}: {exc}")
                continue
        row = dict(track)
        row["source_path"] = str(src)
        row["set_path"] = str(dest)
        row["path"] = str(dest)
        written.append(row)
        cue_pairs.append((str(src), str(dest)))
    cues = (
        clone_cues_for_set_paths(cue_pairs, database_path=database_path)
        if clone_cues
        else {
            "cloned": 0,
            "already_present": 0,
            "missing": 0,
            "skipped_vdj_open": False,
            "message": "",
        }
    )
    return {
        "ok": True,
        "folder": str(folder),
        "count": len(written),
        "missing": errors,
        "tracks": written,
        "cues": cues,
    }


def clamp_target(n: int | float | None) -> int:
    try:
        value = int(n or DEFAULT_TARGET)
    except (TypeError, ValueError):
        value = DEFAULT_TARGET
    return max(MIN_TARGET, min(MAX_TARGET, value))


def clamp_chunk(n: int | float | None) -> int:
    try:
        value = int(n or DEFAULT_CHUNK)
    except (TypeError, ValueError):
        value = DEFAULT_CHUNK
    return max(MIN_CHUNK, min(MAX_CHUNK, value))


def chunk_tracks(tracks: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    n = max(1, int(size))
    return [tracks[i : i + n] for i in range(0, len(tracks), n)]


def recency_score(first_seen: float | None, *, now: float | None = None) -> float:
    if not first_seen:
        return 0.12
    age_days = max(0.0, (now or time.time()) - float(first_seen)) / 86400.0
    if age_days <= 14:
        return 1.0
    if age_days <= 45:
        return 0.78
    if age_days <= 90:
        return 0.45
    if age_days <= 180:
        return 0.22
    return 0.1


def rank_score(track: dict[str, Any], *, now: float | None = None) -> float:
    fit = float(track.get("fit") or 0.0)
    recency = recency_score(track.get("first_seen") or track.get("mtime"), now=now)
    return round(0.72 * fit + 0.28 * recency, 4)


LANE_SHARE = {
    "chill": 0.24,
    "energy": 0.14,
    "rnb": 0.12,
    "kizouk": 0.10,
    "lamba": 0.08,
    "trancy": 0.05,
    "hiphop": 0.04,
    "remixes": 0.06,
    "classics": 0.05,
    "nostalgia": 0.04,
    "tribal": 0.0,
    "bassy": 0.0,
    "experimental": 0.0,
    "intense": 0.0,
    "beautiful": 0.0,
    "neo_zouk": 0.0,
    "pop": 0.0,
    "reggaeton": 0.0,
    "trippy": 0.0,
    "world": 0.0,
    "other": 0.08,
}
LANE_MAX = 0.34
LANE_LABELS = {
    "chill": "Chill",
    "energy": "Energy",
    "rnb": "R&B",
    "kizouk": "Kizouk",
    "lamba": "Lamba",
    "trancy": "Trancy / house",
    "hiphop": "Hip-hop",
    "remixes": "Remixes",
    "tribal": "Tribal",
    "bassy": "Bassy",
    "experimental": "Experimental",
    "intense": "Intense",
    "beautiful": "Beautiful",
    "classics": "Classics",
    "neo_zouk": "Neo Zouk",
    "pop": "Pop",
    "nostalgia": "Nostalgia",
    "reggaeton": "Reggaeton",
    "trippy": "Trippy",
    "world": "World",
    "other": "Other",
}

# First Zouk folder → lane (top crates by count + requested vibes).
_TOP_FOLDER_LANE = {
    "chill": "chill",
    "energy": "energy",
    "jr&b": "rnb",
    "jrb": "rnb",
    "r&b": "rnb",
    "rnb": "rnb",
    "neo soul": "rnb",
    "kizouk": "kizouk",
    "lamba": "lamba",
    "brazillian": "lamba",
    "brazilian": "lamba",
    "brazilian matter": "lamba",
    "trancy": "trancy",
    "hip hoppy": "hiphop",
    "trappy": "hiphop",
    "remixes": "remixes",
    "edits": "remixes",
    "tribal": "tribal",
    "india": "tribal",
    "bassy": "bassy",
    "experimental": "experimental",
    "intense": "intense",
    "beautiful sound": "beautiful",
    "classics": "classics",
    "neo zouk": "neo_zouk",
    "neozouk": "neo_zouk",
    "pop": "pop",
    "nostalgia": "nostalgia",
    "reggatonish": "reggaeton",
    "trippy party": "trippy",
    "middle east": "world",
    "asian": "world",
    "foreign": "world",
}


def normalize_lane_shares(raw: Optional[dict[str, Any]] = None) -> dict[str, float]:
    """Coerce UI percents or fractions. Keep typed values; only scale if over 100%."""
    out = {lane: 0.0 for lane in LANE_SHARE}
    src = raw if isinstance(raw, dict) else {}
    any_set = False
    for lane in LANE_SHARE:
        if lane not in src:
            continue
        try:
            num = float(src[lane])
        except (TypeError, ValueError):
            continue
        if num > 1.5:
            num = num / 100.0
        out[lane] = max(0.0, min(1.0, num))
        any_set = True
    if not any_set:
        return dict(LANE_SHARE)
    total = sum(out.values())
    if total <= 0:
        return dict(LANE_SHARE)
    if total > 1.0 + 1e-6:
        return {lane: round(out[lane] / total, 4) for lane in LANE_SHARE}
    return {lane: round(out[lane], 4) for lane in LANE_SHARE}


def _shares_were_provided(raw: Optional[dict[str, Any]]) -> bool:
    if not isinstance(raw, dict):
        return False
    return any(lane in raw for lane in LANE_SHARE)


def load_mix_prefs() -> dict[str, Any]:
    """Last Genre mix the user typed. Missing/corrupt file → factory, saved=False."""
    with _mix_prefs_lock:
        if not MIX_PREFS_PATH.is_file():
            return {
                "saved": False,
                "lane_shares": dict(LANE_SHARE),
                "min_fit": DEFAULT_MIN_FIT,
            }
        try:
            raw = json.loads(MIX_PREFS_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {
                "saved": False,
                "lane_shares": dict(LANE_SHARE),
                "min_fit": DEFAULT_MIN_FIT,
            }
    if not isinstance(raw, dict):
        return {
            "saved": False,
            "lane_shares": dict(LANE_SHARE),
            "min_fit": DEFAULT_MIN_FIT,
        }
    shares_raw = raw.get("lane_shares")
    if not _shares_were_provided(shares_raw):
        return {
            "saved": False,
            "lane_shares": dict(LANE_SHARE),
            "min_fit": DEFAULT_MIN_FIT,
        }
    min_raw = raw.get("min_fit")
    return {
        "saved": True,
        "lane_shares": normalize_lane_shares(shares_raw),
        "min_fit": normalize_min_fit(
            DEFAULT_MIN_FIT if min_raw is None or min_raw == "" else min_raw
        ),
    }


def save_mix_prefs(
    shares: Optional[dict[str, Any]] = None,
    min_fit: Any = None,
) -> dict[str, Any]:
    """Persist typed lane percents and min fit. Never rewrite with factory defaults."""
    current = load_mix_prefs()
    lane_shares = (
        normalize_lane_shares(shares)
        if _shares_were_provided(shares)
        else current["lane_shares"]
    )
    fit = (
        current["min_fit"]
        if min_fit is None or min_fit == ""
        else normalize_min_fit(min_fit)
    )
    payload = {
        "lane_shares": lane_shares,
        "min_fit": fit,
        "updated_at": time.time(),
    }
    with _mix_prefs_lock:
        MIX_PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = MIX_PREFS_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(MIX_PREFS_PATH)
    return {"saved": True, "lane_shares": lane_shares, "min_fit": fit}


def resolve_mix(
    shares: Optional[dict[str, Any]] = None,
    min_fit: Any = None,
    *,
    persist: bool = False,
) -> tuple[dict[str, float], float]:
    """Request values win; else last saved mix; else factory. Optionally persist."""
    prefs = load_mix_prefs()
    has_shares = _shares_were_provided(shares)
    if has_shares:
        lane_shares = normalize_lane_shares(shares)
    elif prefs["saved"]:
        lane_shares = dict(prefs["lane_shares"])
    else:
        lane_shares = dict(LANE_SHARE)
    if min_fit is None or min_fit == "":
        fit = prefs["min_fit"] if prefs["saved"] else DEFAULT_MIN_FIT
    else:
        fit = normalize_min_fit(min_fit)
    if persist:
        save_mix_prefs(lane_shares, fit)
    return lane_shares, fit


def crate_lane(track: dict[str, Any]) -> str:
    """Crate lane from top folder first, then vibe/genre keywords."""
    rel = str(track.get("relative_path") or "").replace("\\", "/")
    top = rel.split("/", 1)[0].strip().lower()
    if top in _TOP_FOLDER_LANE:
        return _TOP_FOLDER_LANE[top]
    blob = f"{rel} {track.get('vibe') or ''} {track.get('genre') or ''}".lower()
    if any(k in blob for k in ("jr&b", "jrb", "r&b", "rnb", "neo soul", "kizouk r")):
        return "rnb"
    if any(k in blob for k in ("kizouk", "kizomba", "urban kiz")):
        return "kizouk"
    if any(k in blob for k in ("lamba", "brazil", "brazilian", "brazillian")):
        return "lamba"
    if any(k in blob for k in ("tribal", "india", "shaman", "downtemple", "psytrance")):
        return "tribal"
    if any(k in blob for k in ("hip hop", "hiphop", "trappy", "trap")):
        return "hiphop"
    if any(k in blob for k in ("/remixes/", "remixes/", "edits/")):
        return "remixes"
    if any(k in blob for k in ("trancy", "trance", "housey", "deep house", "tech house")):
        return "trancy"
    if "bassy" in blob:
        return "bassy"
    if "experimental" in blob:
        return "experimental"
    if "intense" in blob:
        return "intense"
    if "beautiful" in blob:
        return "beautiful"
    if "classic" in blob:
        return "classics"
    if "neo zouk" in blob or "neozouk" in blob:
        return "neo_zouk"
    if any(k in blob for k in ("reggaeton", "reggaton")):
        return "reggaeton"
    if "nostalgia" in blob:
        return "nostalgia"
    if "trippy" in blob:
        return "trippy"
    if any(k in blob for k in ("middle east", "asian", "foreign", "world")):
        return "world"
    if any(k in blob for k in ("energy", "light", "party", "peak", "openers")):
        return "energy"
    if any(
        k in blob
        for k in ("chill", "mystical", "journey", "fire", "lounge", "closers")
    ):
        return "chill"
    if blob.strip().startswith("pop") or "/pop/" in blob or blob.endswith(" pop"):
        return "pop"
    fam = genre_family(track.get("genre"), track.get("vibe"))
    if fam == "rnb_soul_zouk":
        return "rnb"
    if fam == "house_dance":
        return "trancy"
    if fam == "hiphop":
        return "hiphop"
    if fam == "psy_tribal_world":
        return "tribal"
    return "other"


def interleave_by_lane(tracks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rotate lanes so Gemini does not hear 12 Chill in a row."""
    buckets: dict[str, list[dict[str, Any]]] = {lane: [] for lane in LANE_SHARE}
    for track in tracks:
        buckets.setdefault(crate_lane(track), []).append(track)
    out: list[dict[str, Any]] = []
    while any(buckets.values()):
        for lane in LANE_SHARE:
            if buckets.get(lane):
                out.append(buckets[lane].pop(0))
    return out


def mix_summary(playlist: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {lane: 0 for lane in LANE_SHARE}
    for track in playlist:
        lane = track.get("lane") or crate_lane(track)
        counts[lane] = counts.get(lane, 0) + 1
    return counts


def heuristic_fit(track: dict[str, Any]) -> tuple[float, str, str]:
    """Folder-vibe fallback when Gemini is unavailable."""
    blob = f"{track.get('relative_path') or ''} {track.get('vibe') or ''} {track.get('genre') or ''}".lower()
    if any(k in blob for k in ("beautiful", "chill", "mystical", "kizouk", "favs", "lounge")):
        return 0.62, "keep", "Cozy / late-night zouk folder"
    if any(k in blob for k in ("light", "fire", "lamba", "energy")):
        return 0.48, "maybe", "Danceable but watch the energy for a pajama marathon"
    if any(k in blob for k in ("tribal", "trancy", "psy", "india")):
        return 0.28, "skip", "More ritual/peak than pajama social"
    if any(k in blob for k in ("hip hop", "trappy", "bassy")):
        return 0.38, "maybe", "Possible spice, not the core pajama bed"
    return 0.42, "maybe", "Unlabeled — not a default keep"


def assemble_playlist(
    scored: list[dict[str, Any]],
    *,
    target: int = DEFAULT_TARGET,
    newest_guarantee: int = NEWEST_GUARANTEE,
    now: float | None = None,
    shares: Optional[dict[str, Any]] = None,
    min_fit: float | None = None,
) -> list[dict[str, Any]]:
    """Pick a 300–500 crate: pajama-fit only, newest thrown in, lanes balanced."""
    target = clamp_target(target)
    explicit = shares is not None
    lane_shares = normalize_lane_shares(shares)
    fit_floor = normalize_min_fit(min_fit if min_fit is not None else DEFAULT_MIN_FIT)
    usable: list[dict[str, Any]] = []
    for raw in scored:
        if track_is_assemble_excluded(raw):
            continue
        if float(raw.get("fit") or 0) < fit_floor or (raw.get("verdict") or "") == "skip":
            continue
        item = dict(raw)
        if not track_meets_bitrate_floor(item, MIN_BITRATE_KBPS, probe=True):
            continue
        item["lane"] = crate_lane(item)
        item["rank"] = rank_score(item, now=now)
        usable.append(item)

    quotas: dict[str, int] = {}
    max_n: dict[str, int] = {}
    for lane, share in lane_shares.items():
        if share <= 0:
            quotas[lane] = 0
            max_n[lane] = 0
            continue
        quota = max(1, int(round(target * share)))
        quotas[lane] = quota
        slack = max(1, int(round(target * 0.02)))
        if explicit:
            max_n[lane] = quota + slack
        elif 0.08 <= share <= 0.40:
            max_n[lane] = max(quota + slack, int(target * LANE_MAX))
        else:
            max_n[lane] = quota + slack
    newest = sorted(
        usable,
        key=lambda t: float(t.get("first_seen") or t.get("mtime") or 0),
        reverse=True,
    )
    by_lane: dict[str, list[dict[str, Any]]] = {lane: [] for lane in LANE_SHARE}
    for item in usable:
        by_lane.setdefault(item["lane"], []).append(item)
    for lane in by_lane:
        by_lane[lane].sort(key=lambda t: float(t.get("rank") or 0), reverse=True)

    picked: list[dict[str, Any]] = []
    seen: set[str] = set()
    counts: dict[str, int] = {lane: 0 for lane in LANE_SHARE}

    def take(item: dict[str, Any], *, respect_max: bool = True) -> bool:
        path = item.get("path") or ""
        if not path:
            return False
        keys = _track_idents(item)
        if (keys and keys & seen) or path in seen:
            return False
        if len(picked) >= target:
            return False
        lane = item.get("lane") or crate_lane(item)
        if respect_max and counts.get(lane, 0) >= max_n.get(lane, int(target * LANE_MAX)):
            return False
        seen.add(path)
        seen.update(keys)
        counts[lane] = counts.get(lane, 0) + 1
        row = dict(item)
        row["lane"] = lane
        picked.append(row)
        return True

    newest_cap = max(8, newest_guarantee)
    for item in newest[: max(newest_cap * 2, newest_cap)]:
        if sum(1 for p in picked if p.get("newest")) >= newest_cap:
            break
        row = dict(item)
        row["newest"] = True
        take(row, respect_max=True)

    for lane, quota in quotas.items():
        for item in by_lane.get(lane) or []:
            if counts.get(lane, 0) >= quota:
                break
            take(dict(item), respect_max=True)

    leftovers = sorted(usable, key=lambda t: float(t.get("rank") or 0), reverse=True)

    def lane_of(item: dict[str, Any]) -> str:
        return item.get("lane") or crate_lane(item)

    allocated = sum(lane_shares.values())
    rem_n = int(round(target * max(0.0, 1.0 - allocated))) if explicit else 0

    for item in leftovers:
        if len(picked) >= target:
            break
        if counts.get(lane_of(item), 0) < quotas.get(lane_of(item), 0):
            take(dict(item), respect_max=True)
    if rem_n > 0:
        rem_taken = 0
        for item in leftovers:
            if len(picked) >= target or rem_taken >= rem_n:
                break
            if lane_shares.get(lane_of(item), 0) > 0:
                continue
            if take(dict(item), respect_max=False):
                rem_taken += 1
    for item in leftovers:
        if len(picked) >= target:
            break
        if lane_shares.get(lane_of(item), 0) <= 0:
            continue
        take(dict(item), respect_max=True)
    if len(picked) < min(MIN_TARGET, len(usable)):
        need = min(target, MIN_TARGET)
        for item in leftovers:
            if len(picked) >= need:
                break
            if lane_shares.get(lane_of(item), 0) <= 0:
                continue
            take(dict(item), respect_max=True)
        under = sorted(
            leftovers,
            key=lambda t: (
                counts.get(lane_of(t), 0) / max(1, quotas.get(lane_of(t), 1)),
                -float(t.get("rank") or 0),
            ),
        )
        for item in under:
            if len(picked) >= need:
                break
            if lane_shares.get(lane_of(item), 0) <= 0:
                continue
            take(dict(item), respect_max=False)

    return collapse_duplicate_songs(picked)[:target]


def collapse_duplicate_songs(tracks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Last-line defense: one row per song even if folder copies slipped in."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for track in tracks:
        keys = _track_idents(track)
        display = (
            f"show:{normalize_key(track.get('artist') or '')} "
            f"{_core_title(str(track.get('title') or ''), str(track.get('name') or ''))}"
        )
        keys = set(keys)
        keys.add(display)
        if keys & seen:
            continue
        seen.update(keys)
        out.append(track)
    return out


def _load_api_key() -> str:
    ui_root = Path(__file__).resolve().parents[1]
    repo_root = Path(__file__).resolve().parents[2]
    load_dotenv(ui_root / ".env")
    load_dotenv(repo_root / ".env")
    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or ""
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    return key


def path_is_assemble_excluded(path: str | Path) -> bool:
    """True when a file lives under a DJ-utility Transitions folder."""
    if not path:
        return False
    skip = {name.casefold() for name in ASSEMBLE_SKIP_DIR_NAMES}
    return any(part.casefold() in skip for part in Path(path).parts)


def track_is_assemble_excluded(track: dict[str, Any] | None) -> bool:
    """True when mix creation must ignore this row (path or relative folder)."""
    if not track:
        return False
    for key in ("source_path", "path", "relative_path"):
        value = track.get(key)
        if value and path_is_assemble_excluded(value):
            return True
    return False


def _strip_assemble_excluded_result(
    result: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Drop Transitions-folder rows from a saved assemble payload."""
    if not isinstance(result, dict):
        return result
    out = dict(result)
    for key in ("playlist", "ranked"):
        rows = out.get(key)
        if isinstance(rows, list):
            out[key] = [row for row in rows if not track_is_assemble_excluded(row)]
    if isinstance(out.get("playlist"), list):
        out["mix"] = mix_summary(out["playlist"])
    return out


def _should_skip_dir(name: str) -> bool:
    if name in LIBRARY_SKIP_DIR_NAMES or name.startswith(".") or name.endswith(".vdjstems"):
        return True
    return name.casefold() in {item.casefold() for item in ASSEMBLE_SKIP_DIR_NAMES}


def _optional_float(value: Optional[str]) -> Optional[float]:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _db_meta_for_root(root: Path) -> dict[str, dict[str, Any]]:
    db = VDJ_DATABASE
    if not db.is_file() or not root.is_dir():
        return {}
    try:
        from vdj_database_safety import read_vdj_database_text

        content = read_vdj_database_text(db)
    except Exception:
        try:
            content = db.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return {}
    root_s = str(root.resolve())
    out: dict[str, dict[str, Any]] = {}
    for chunk in re.split(r"(?=<Song\b)", content):
        if not chunk.startswith("<Song"):
            continue
        fp_m = re.search(r'FilePath="([^"]+)"', chunk[:500])
        if not fp_m:
            continue
        path = unescape_xml_text(fp_m.group(1))
        if not (path.startswith(root_s + "/") or path.startswith(root_s + "\\")):
            continue
        tags_m = re.search(r"<Tags\b([^>]*)/?>", chunk)
        attrs = tags_m.group(1) if tags_m else ""
        author_m = re.search(r'Author="([^"]*)"', attrs)
        title_m = re.search(r'Title="([^"]*)"', attrs)
        genre_m = re.search(r'Genre="([^"]*)"', attrs)
        key_m = re.search(r'Key="([^"]*)"', attrs) or re.search(
            r'Key="([^"]*)"', chunk[:2000]
        )
        bpm_m = re.search(r'<Scan\b[^>]*\bBpm="([^"]+)"', chunk)
        seen_m = re.search(r'FirstSeen="(\d+)"', chunk)
        last_m = re.search(r'LastPlay="(\d+)"', chunk)
        try:
            first_seen = int(seen_m.group(1)) if seen_m else None
        except ValueError:
            first_seen = None
        try:
            last_play = int(last_m.group(1)) if last_m else None
        except ValueError:
            last_play = None
        out[path] = {
            "artist": unescape_xml_text(author_m.group(1) if author_m else ""),
            "title": unescape_xml_text(title_m.group(1) if title_m else ""),
            "genre": unescape_xml_text(genre_m.group(1) if genre_m else ""),
            "key": (key_m.group(1) if key_m else "").strip(),
            "bpm": vdj_bpm_to_actual(_optional_float(bpm_m.group(1) if bpm_m else None)),
            "first_seen": first_seen,
            "last_play": last_play,
        }
    return out


def list_library_tracks(library: str = DEFAULT_LIBRARY) -> list[dict[str, Any]]:
    root = LIBRARIES.get(library)
    if root is None or not Path(root).is_dir():
        return []
    root = Path(root).resolve()
    meta = _db_meta_for_root(root)
    tracks: list[dict[str, Any]] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in AUDIO_EXTENSIONS:
            continue
        if any(_should_skip_dir(part) for part in path.parts):
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        rel = path.relative_to(root).as_posix()
        db = meta.get(str(path), {})
        first_seen = db.get("first_seen") or mtime
        tracks.append(
            {
                "path": str(path),
                "name": path.name,
                "artist": db.get("artist") or "",
                "title": db.get("title") or path.stem,
                "genre": db.get("genre") or "",
                "key": db.get("key") or "",
                "camelot": key_to_camelot(db.get("key") or "") or "",
                "bpm": db.get("bpm"),
                "vibe": vibe_label_from_path(rel, library),
                "relative_path": rel,
                "library": library,
                "first_seen": float(first_seen),
                "mtime": float(mtime),
                "last_play": db.get("last_play"),
            }
        )
    tracks.sort(key=lambda t: float(t.get("first_seen") or 0), reverse=True)
    return tracks


_MIX_TAIL = re.compile(
    r"\b(original mix|extended mix|radio edit|club mix|remix|mix|edit|version)\b",
    re.I,
)
_ARTIST_SPLIT = re.compile(
    r"\s*(?:,|&|/|\+| x | vs\.? | feat\.? | ft\.? | featuring | and )\s*",
    re.I,
)


def _core_title(title: str, name: str = "") -> str:
    raw = (title or "").strip()
    if not raw:
        stem = Path(name).stem if name else ""
        stem = re.sub(r"^\d+[\s.\-]+", "", stem)
        for sep in (" - ", " – ", " — "):
            if sep in stem:
                stem = stem.split(sep)[-1]
                break
        raw = stem
    raw = _MIX_TAIL.sub(" ", raw)
    return normalize_key(raw)


def _artist_parts(artist: str) -> list[str]:
    if not artist:
        return []
    parts = [_MIX_TAIL.sub(" ", p) for p in _ARTIST_SPLIT.split(artist)]
    return [normalize_key(p) for p in parts if normalize_key(p)]


def _track_idents(track: dict[str, Any]) -> set[str]:
    """Identity keys that collapse folder copies and artist-string variants."""
    path = str(track.get("path") or "")
    artist = str(track.get("artist") or "")
    title = str(track.get("title") or "")
    name = str(track.get("name") or Path(path).name)
    keys = track_block_keys(path=path, artist=artist, title=title, name=name)
    artists = list(_artist_parts(artist))
    core = _core_title(title, name)
    for sep in (" - ", " – ", " — "):
        stem = re.sub(r"^\d+[\s.\-]+", "", Path(name).stem)
        if sep in stem:
            left, right = stem.split(sep, 1)
            artists.extend(_artist_parts(left))
            if not core:
                core = _core_title(right, "")
            break
    artists = [a for a in dict.fromkeys(artists) if a]
    if core:
        for part in artists:
            keys.add(f"core:{part} {core}")
        if artists:
            keys.add(f"core:{' '.join(sorted(artists))} {core}")
    return {k for k in keys if k and not k.endswith(":")}


def dedupe_tracks_for_eval(tracks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep one copy of each song (newest first) so Gemini never hears it twice."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for track in tracks:
        keys = _track_idents(track)
        if keys and keys & seen:
            continue
        seen |= keys
        if track.get("path"):
            seen.add(f"path:{(track.get('path') or '').lower()}")
        out.append(track)
    return out


def _cache_covers_track(cache: dict[str, dict[str, Any]], track: dict[str, Any]) -> bool:
    path = track.get("path") or ""
    if path in cache:
        return True
    keys = _track_idents(track)
    if not keys:
        return False
    for cpath, entry in cache.items():
        stored = set(entry.get("idents") or [])
        if not stored:
            stored = _track_idents({"path": cpath, "name": Path(cpath).name})
        if keys & stored:
            return True
    return False


def _score_cache_key(event_name: str) -> str:
    return slug_event(event_name)


def _usable_score_entry(path: str, entry: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Keep any prior Gemini/heuristic verdict so we do not re-hit the LLM."""
    if not isinstance(entry, dict):
        return None
    if entry.get("fit") is None or not entry.get("verdict"):
        return None
    row = dict(entry)
    if not row.get("idents"):
        row["idents"] = sorted(_track_idents({"path": path, "name": Path(path).name}))
    return row


def load_score_cache(event_name: str) -> dict[str, dict[str, Any]]:
    with _cache_lock:
        if not CACHE_PATH.is_file():
            return {}
        try:
            raw = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        bucket = raw.get(_score_cache_key(event_name))
        if not isinstance(bucket, dict):
            return {}
        out: dict[str, dict[str, Any]] = {}
        for key, value in bucket.items():
            usable = _usable_score_entry(str(key), value)
            if usable:
                out[str(key)] = usable
        return out


def save_score_cache(event_name: str, scores: dict[str, dict[str, Any]]) -> None:
    with _cache_lock:
        data: dict[str, Any] = {}
        if CACHE_PATH.is_file():
            try:
                data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = {}
        if not isinstance(data, dict):
            data = {}
        data[_score_cache_key(event_name)] = scores
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(CACHE_PATH)


def clip_start_sec(duration: float | None, clip_len: float = LISTEN_SECONDS) -> float:
    """Start past the intro when the file is long enough."""
    if not duration or duration <= clip_len + 2:
        return 0.0
    start = max(18.0, float(duration) * 0.28)
    return min(start, float(duration) - clip_len)


def extract_listen_clip(
    audio_path: str | Path,
    *,
    start_sec: float = 0.0,
    duration_sec: float = LISTEN_SECONDS,
    out_dir: Path,
) -> Path:
    """Cut a short mp3 excerpt for Gemini to hear."""
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"listen_{uuid.uuid4().hex[:10]}.mp3"
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-y",
        "-ss",
        f"{max(0.0, start_sec):.3f}",
        "-t",
        f"{max(8.0, duration_sec):.3f}",
        "-i",
        str(audio_path),
        "-vn",
        "-ac",
        "2",
        "-ar",
        "44100",
        "-b:a",
        LISTEN_BITRATE,
        str(out),
    ]
    try:
        proc = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            timeout=LISTEN_CLIP_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"listen clip timed out for {Path(audio_path).name}"
        ) from exc
    if proc.returncode != 0 or not out.is_file() or out.stat().st_size < 800:
        err = (proc.stderr or b"").decode("utf-8", "replace")[-300:]
        raise RuntimeError(f"listen clip failed for {Path(audio_path).name}: {err}")
    return out


def _probe_duration(path: str) -> float | None:
    try:
        from .audio_meta import probe_audio_meta

        meta = probe_audio_meta(path)
        dur = meta.get("duration")
        return float(dur) if dur else None
    except Exception:
        return None


def _clip_bytes_for_track(track: dict[str, Any], out_dir: Path) -> Optional[bytes]:
    try:
        duration = _probe_duration(track["path"])
        start = clip_start_sec(duration)
        clip = extract_listen_clip(
            track["path"], start_sec=start, duration_sec=LISTEN_SECONDS, out_dir=out_dir
        )
        data = clip.read_bytes()
        return data if len(data) >= 800 else None
    except Exception:
        return None


def score_chunk_heuristic(chunk: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for t in chunk:
        fit, verdict, reason = heuristic_fit(t)
        row = dict(t)
        row.update({"fit": fit, "verdict": verdict, "reason": reason, "model": "heuristic"})
        out.append(row)
    return out


def score_chunk_gemini(
    chunk: list[dict[str, Any]],
    *,
    event_name: str,
    brief: str,
) -> list[dict[str, Any]]:
    api_key = _load_api_key()
    client = genai.Client(api_key=api_key)
    blurbs = lookup_blurbs_for_tracks(chunk)
    lines = []
    for i, t in enumerate(chunk, 1):
        web = (blurbs.get(t["path"]) or "").strip()
        web_line = f"\n   web: {web}" if web else "\n   web: —"
        lines.append(
            f"{i}. path={t['path']}\n"
            f"   {t.get('artist') or '—'} — {t.get('title') or t.get('name')}\n"
            f"   {t.get('bpm') or '?'} BPM · {t.get('key') or '?'} · "
            f"genre={t.get('genre') or '—'} · folder={t.get('vibe') or t.get('relative_path')}"
            f"{web_line}"
        )
    prompt = f"""You are programming a DJ crate for one event. You will HEAR a ~{int(LISTEN_SECONDS)}s excerpt of each track (usually past the intro). Score from the AUDIO first. Use the web blurb as context (genre, how the song is described publicly) — do not let a Wikipedia bio override what you hear.

EVENT: {event_name}
BRIEF: {brief}

Score ONLY these tracks. Return every path exactly as given.
- keep: only a clear pajama-marathon lock (fit >= 0.60). Be stingy — most tracks are maybe or skip.
- maybe: usable spice / bridge (0.45–0.59). Do not mark keep if you hesitate.
- skip: fit < 0.45, wrong energy, joke, harsh peak, or it breaks the pajama spell.

Crate is a LONG social zouk / pajamas party, not a 60-minute peak set.
When unsure between keep and maybe, choose maybe. When unsure between maybe and skip, choose skip.
If a track has no audio attached, say so in reason and lower confidence.

TRACKS:
{chr(10).join(lines)}
"""
    last_err: Optional[Exception] = None
    with tempfile.TemporaryDirectory(prefix="assemble-listen-") as tmp:
        tmp_dir = Path(tmp)
        heard = 0
        contents: list[Any] = [prompt]
        for i, t in enumerate(chunk, 1):
            contents.append(f"AUDIO {i} path={t['path']}")
            data = _clip_bytes_for_track(t, tmp_dir)
            if data:
                contents.append(types.Part.from_bytes(data=data, mime_type="audio/mpeg"))
                heard += 1
            else:
                contents.append("(no audio clip — score from metadata only)")
        if heard == 0:
            fallback = score_chunk_heuristic(chunk)
            for row in fallback:
                row["reason"] = f"{row['reason']} (no listen clips)"
                row["heard"] = False
            return fallback

        for model in MODEL_FALLBACKS:
            if not model:
                continue
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        temperature=0.2,
                        response_mime_type="application/json",
                        response_schema=ChunkScoreSchema,
                        http_options=types.HttpOptions(timeout=180_000),
                    ),
                )
                raw = getattr(response, "parsed", None)
                if raw is None:
                    data = json.loads(getattr(response, "text", None) or "")
                elif hasattr(raw, "model_dump"):
                    data = raw.model_dump()
                else:
                    data = dict(raw)
                by_path = {t["path"]: t for t in chunk}
                out: list[dict[str, Any]] = []
                used: set[str] = set()
                for p in data.get("picks") or []:
                    path = (p.get("path") or "").strip()
                    if path not in by_path or path in used:
                        continue
                    used.add(path)
                    row = dict(by_path[path])
                    try:
                        fit = max(0.0, min(1.0, float(p.get("fit") or 0)))
                    except (TypeError, ValueError):
                        fit = 0.0
                    verdict = (p.get("verdict") or "maybe").strip().lower()
                    if verdict not in {"keep", "maybe", "skip"}:
                        verdict = "maybe"
                    row.update(
                        {
                            "fit": fit,
                            "verdict": verdict,
                            "reason": (p.get("reason") or "").strip(),
                            "model": model,
                            "heard": True,
                        }
                    )
                    out.append(row)
                for t in chunk:
                    if t["path"] not in used:
                        fit, verdict, reason = heuristic_fit(t)
                        row = dict(t)
                        row.update(
                            {
                                "fit": fit,
                                "verdict": verdict,
                                "reason": reason,
                                "model": f"{model}+heuristic",
                                "heard": False,
                            }
                        )
                        out.append(row)
                return out
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                continue
        fallback = score_chunk_heuristic(chunk)
        for row in fallback:
            row["reason"] = f"{row['reason']} (Gemini unavailable: {last_err})"
            row["heard"] = False
        return fallback


def _lookup_cache_entry(
    cache: dict[str, dict[str, Any]], track: dict[str, Any]
) -> Optional[dict[str, Any]]:
    hit = cache.get(track.get("path") or "")
    if hit:
        return hit
    keys = _track_idents(track)
    if not keys:
        return None
    for cpath, entry in cache.items():
        stored = set(entry.get("idents") or [])
        if not stored:
            stored = _track_idents({"path": cpath, "name": Path(cpath).name})
        if keys & stored:
            return entry
    return None


def _merge_scored(
    tracks: list[dict[str, Any]], cache: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    merged = []
    used: set[str] = set()
    for t in tracks:
        hit = _lookup_cache_entry(cache, t)
        if not hit:
            continue
        keys = _track_idents(t)
        if keys & used:
            continue
        used |= keys
        row = dict(t)
        row.update(
            {
                "fit": float(hit.get("fit") or 0),
                "verdict": hit.get("verdict") or "maybe",
                "reason": hit.get("reason") or "",
                "model": hit.get("model") or "",
            }
        )
        merged.append(row)
    return merged


def tracks_from_score_cache(cache: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Rebuild track rows from saved evals so the UI can list them without a Zouk scan."""
    out: list[dict[str, Any]] = []
    for path, entry in cache.items():
        usable = _usable_score_entry(path, entry)
        if not usable:
            continue
        if path_is_assemble_excluded(path) or path_is_assemble_excluded(
            str(usable.get("relative_path") or "")
        ):
            continue
        name = Path(path).name
        artist = str(usable.get("artist") or "").strip()
        title = str(usable.get("title") or "").strip()
        if not artist or not title:
            stem = Path(name).stem
            for sep in (" - ", " – ", " — "):
                if sep in stem:
                    left, right = stem.split(sep, 1)
                    artist = artist or left.strip()
                    title = title or right.strip()
                    break
            if not title:
                title = stem
        first_seen = usable.get("first_seen")
        out.append(
            {
                "path": path,
                "name": name,
                "artist": artist,
                "title": title,
                "vibe": usable.get("vibe") or "",
                "relative_path": usable.get("relative_path") or "",
                "genre": usable.get("genre") or "",
                "bpm": usable.get("bpm"),
                "first_seen": first_seen,
                "mtime": first_seen,
                "fit": usable.get("fit"),
                "verdict": usable.get("verdict"),
                "reason": usable.get("reason") or "",
                "model": usable.get("model") or "",
            }
        )
    return out


def _assemble_result(
    *,
    event_name: str,
    brief: str,
    library: str,
    target: int,
    merged: list[dict[str, Any]],
    files: Optional[dict[str, Any]] = None,
    from_cache: bool = False,
    shares: Optional[dict[str, Any]] = None,
    min_fit: float | None = None,
) -> dict[str, Any]:
    scored = [
        dict(track) for track in merged if not track_is_assemble_excluded(track)
    ]
    for track in scored:
        track["rank"] = rank_score(track)
    lane_shares = normalize_lane_shares(shares)
    fit_floor = normalize_min_fit(min_fit)
    playlist = assemble_playlist(
        scored, target=target, shares=lane_shares, min_fit=fit_floor
    )
    keep = sum(1 for t in scored if float(t.get("fit") or 0) >= KEEP_FIT)
    payload: dict[str, Any] = {
        "event_name": event_name,
        "brief": brief,
        "library": library,
        "playlist": playlist,
        "ranked": sorted(scored, key=lambda t: float(t.get("rank") or 0), reverse=True),
        "scored_total": len(scored),
        "keep_count": keep,
        "mix": mix_summary(playlist),
        "mix_targets": lane_shares,
        "min_fit": fit_floor,
        "files": files,
    }
    if from_cache:
        payload["from_cache"] = True
    return payload


def _publish_job_result(
    job: AssembleJob,
    merged: list[dict[str, Any]],
    *,
    files: Optional[dict[str, Any]] = None,
    from_cache: bool = False,
) -> dict[str, Any]:
    payload = _assemble_result(
        event_name=job.event_name,
        brief=job.brief,
        library=job.library,
        target=job.target,
        merged=merged,
        files=files,
        from_cache=from_cache,
        shares=job.lane_shares,
        min_fit=job.min_fit,
    )
    job.result = payload
    job.scored = len(merged)
    job.kept = int(payload.get("keep_count") or 0)
    job.skipped = sum(1 for t in merged if t.get("verdict") == "skip")
    return payload


def _merged_from_cache(
    cache: dict[str, dict[str, Any]],
    tracks: Optional[list[dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    if tracks:
        merged = _merge_scored(tracks, cache)
        if merged:
            return merged
    cache_tracks = dedupe_tracks_for_eval(tracks_from_score_cache(cache))
    return _merge_scored(cache_tracks, cache)


def write_playlist_files(
    playlist: list[dict[str, Any]],
    *,
    event_name: str,
    sets_root: Path | None = None,
) -> dict[str, Any]:
    from .vdj_sideview_recs import VDJ_MYLISTS, build_virtual_folder_xml, _atomic_write

    materialized = materialize_set_directory(
        playlist, event_name=event_name, sets_root=sets_root
    )
    crate = materialized.get("tracks") or playlist
    slug = slug_event(event_folder_name(event_name))
    title = event_folder_name(event_name)
    xml = build_virtual_folder_xml(crate)
    cues_path = CUES_ROOT / f"{title}.vdjfolder"
    mylists_path = VDJ_MYLISTS / f"{title}.vdjfolder"
    notes_dir = DJ_NOTES_ROOT / "playlists"
    m3u_path = notes_dir / f"{slug}.m3u"
    folder_m3u = Path(materialized["folder"]) / f"{title}.m3u"
    _atomic_write(cues_path, xml)
    try:
        _atomic_write(mylists_path, xml)
        mylists_ok = True
    except OSError as exc:
        mylists_ok = False
        mylists_path = Path(str(mylists_path) + f" ({exc})")
    lines = ["#EXTM3U"]
    for p in crate:
        artist = p.get("artist") or ""
        track_title = p.get("title") or p.get("name") or ""
        lines.append(f"#EXTINF:-1,{artist} - {track_title}".strip(" -"))
        lines.append(p["path"])
    notes_dir.mkdir(parents=True, exist_ok=True)
    m3u_text = "\n".join(lines) + "\n"
    m3u_path.write_text(m3u_text, encoding="utf-8")
    folder_m3u.write_text(m3u_text, encoding="utf-8")
    return {
        "ok": True,
        "folder": materialized.get("folder") or "",
        "cues": str(cues_path),
        "mylists": str(mylists_path) if mylists_ok else "",
        "m3u": str(m3u_path),
        "count": materialized.get("count") or len(crate),
        "missing": materialized.get("missing") or [],
        "cues": materialized.get("cues") or {},
    }


def result_from_cache(
    *,
    event_name: str | None = None,
    library: str = DEFAULT_LIBRARY,
    target: int = DEFAULT_TARGET,
    brief: str = "",
    tracks: Optional[list[dict[str, Any]]] = None,
    shares: Optional[dict[str, Any]] = None,
    min_fit: float | None = None,
) -> Optional[dict[str, Any]]:
    """Rebuild playlist + rank from saved evals (survives server restart)."""
    name = (event_name or DEFAULT_EVENT["name"]).strip() or DEFAULT_EVENT["name"]
    cache = load_score_cache(name)
    if not cache:
        return None
    library_tracks = tracks
    if library_tracks is None:
        library_tracks = dedupe_tracks_for_eval(list_library_tracks(library))
    merged = _merged_from_cache(cache, library_tracks)
    if not merged:
        return None
    return _assemble_result(
        event_name=name,
        brief=brief or DEFAULT_EVENT["brief"],
        library=library,
        target=target,
        merged=merged,
        from_cache=True,
        shares=shares,
        min_fit=min_fit,
    )


def preview_library(library: str = DEFAULT_LIBRARY) -> dict[str, Any]:
    tracks = list_library_tracks(library)
    newest = tracks[:12]
    cache = load_score_cache(DEFAULT_EVENT["name"])
    unique = dedupe_tracks_for_eval(tracks)
    cached = sum(1 for t in unique if _cache_covers_track(cache, t))
    result = (
        result_from_cache(library=library, tracks=unique) if cache else None
    )
    return {
        "ok": True,
        "library": library,
        "total": len(tracks),
        "unique_songs": len(unique),
        "cached_evals": cached,
        "newest": newest,
        "event": DEFAULT_EVENT,
        "cache_path": str(CACHE_PATH),
        "result": result,
        "defaults": {
            "chunk_size": DEFAULT_CHUNK,
            "target": DEFAULT_TARGET,
            "min_target": MIN_TARGET,
            "max_target": MAX_TARGET,
            "lane_shares": dict(LANE_SHARE),
            "lane_labels": dict(LANE_LABELS),
            "min_fit": DEFAULT_MIN_FIT,
        },
        "mix_prefs": load_mix_prefs(),
    }


def get_job(job_id: str) -> Optional[AssembleJob]:
    with _jobs_lock:
        return _jobs.get(job_id)


def latest_job() -> Optional[AssembleJob]:
    with _jobs_lock:
        live = max(_jobs.values(), key=lambda j: j.created_at) if _jobs else None
    if live is None:
        live = load_job_snapshot()
        if live is not None:
            with _jobs_lock:
                _jobs.setdefault(live.id, live)
    if live is None:
        return None
    return _mark_orphaned_job(live)


def start_assemble_job(
    *,
    event_name: str | None = None,
    brief: str | None = None,
    library: str = DEFAULT_LIBRARY,
    chunk_size: int = DEFAULT_CHUNK,
    target: int = DEFAULT_TARGET,
    use_gemini: bool = True,
    scan_all: bool = False,
    lane_shares: Optional[dict[str, Any]] = None,
    min_fit: float | None = None,
) -> AssembleJob:
    name = (event_name or DEFAULT_EVENT["name"]).strip() or DEFAULT_EVENT["name"]
    text = (brief or DEFAULT_EVENT["brief"]).strip() or DEFAULT_EVENT["brief"]
    with _jobs_lock:
        for existing in _jobs.values():
            if existing.status in {"running", "queued"} and existing.id in _live_workers:
                return existing
    resolved_shares, resolved_fit = resolve_mix(
        lane_shares,
        min_fit,
        persist=_shares_were_provided(lane_shares)
        or (min_fit is not None and min_fit != ""),
    )
    job = AssembleJob(
        id=uuid.uuid4().hex[:12],
        status="queued",
        created_at=time.time(),
        event_name=name,
        brief=text,
        library=library or DEFAULT_LIBRARY,
        chunk_size=clamp_chunk(chunk_size),
        target=clamp_target(target),
        message="Queued Zouk scan…",
        lane_shares=resolved_shares,
        min_fit=resolved_fit,
    )
    seed_cache = load_score_cache(name)
    if seed_cache:
        seed_merged = _merged_from_cache(seed_cache)
        if seed_merged:
            _publish_job_result(job, seed_merged, from_cache=True)
            job.message = (
                f"{job.scored} cached evals loaded — listing Zouk…"
            )
    with _jobs_lock:
        _jobs[job.id] = job
    _live_workers.add(job.id)
    persist_job(job)

    def worker() -> None:
        job.status = "running"
        if not job.result:
            job.message = "Listing Zouk library (newest first)…"
        persist_job(job)
        try:
            tracks = dedupe_tracks_for_eval(list_library_tracks(job.library))
            job.total = len(tracks)
            cache = load_score_cache(job.event_name)
            # Persist backfilled identity keys so folder copies stay skipped.
            if cache:
                save_score_cache(job.event_name, cache)
            merged = _merged_from_cache(cache, tracks)
            if merged:
                _publish_job_result(job, merged, from_cache=True)
            pending = interleave_by_lane(
                [t for t in tracks if not _cache_covers_track(cache, t)]
            )
            job.scored = len(merged) if merged else (len(tracks) - len(pending))
            job.message = (
                f"{job.scored} cached · {len(pending)} still to hear "
                f"in chunks of {job.chunk_size}…"
            )
            chunks = chunk_tracks(pending, job.chunk_size)
            for i, chunk in enumerate(chunks, 1):
                if job.cancel:
                    job.status = "cancelled"
                    job.message = "Stopped — playlist uses scores so far."
                    persist_job(job)
                    break
                job.message = (
                    f"Hearing chunk {i}/{len(chunks)} · {len(chunk)} songs · "
                    f"{job.scored} scored so far…"
                )
                persist_job(job)
                if use_gemini:
                    scored = score_chunk_gemini(
                        chunk, event_name=job.event_name, brief=job.brief
                    )
                else:
                    scored = score_chunk_heuristic(chunk)
                now = time.time()
                for row in scored:
                    cache[row["path"]] = {
                        "fit": row.get("fit"),
                        "verdict": row.get("verdict"),
                        "reason": row.get("reason"),
                        "model": row.get("model"),
                        "scored_at": now,
                        "heard": bool(row.get("heard")),
                        "version": SCORE_CACHE_VERSION,
                        "idents": sorted(_track_idents(row)),
                        "artist": row.get("artist") or "",
                        "title": row.get("title") or "",
                        "vibe": row.get("vibe") or "",
                        "relative_path": row.get("relative_path") or "",
                        "bpm": row.get("bpm"),
                        "first_seen": row.get("first_seen"),
                    }
                save_score_cache(job.event_name, cache)
                merged = _merge_scored(tracks, cache)
                payload = _publish_job_result(job, merged)
                playlist = payload["playlist"]
                job.message = (
                    f"Chunk {i}/{len(chunks)} · {job.scored} scored · "
                    f"{job.kept} keep · playlist {len(playlist)}"
                )
                persist_job(job)
                mix = payload.get("mix") or mix_summary(playlist)
                lanes_ready = sum(1 for n in mix.values() if n >= 12)
                chill_share = (mix.get("chill") or 0) / max(1, len(playlist))
                chill_target = normalize_lane_shares(job.lane_shares).get("chill") or 0.28
                if (
                    not scan_all
                    and len(playlist) >= job.target
                    and job.scored >= job.target
                    and lanes_ready >= 3
                    and chill_share <= chill_target + 0.08
                ):
                    job.message = (
                        f"Crate is full and mixed · {len(playlist)} songs · "
                        f"{lanes_ready} lanes · scored {job.scored}."
                    )
                    break
            merged = _merge_scored(tracks, cache)
            playlist = assemble_playlist(
                merged,
                target=job.target,
                shares=job.lane_shares,
                min_fit=job.min_fit,
            )
            files = write_playlist_files(playlist, event_name=job.event_name)
            _publish_job_result(job, merged, files=files)
            if job.status != "cancelled":
                job.status = "ok"
                job.message = (
                    f"Ready · {len(playlist)} songs for {job.event_name} · "
                    f"{len(merged)} scored"
                )
            job.finished_at = time.time()
        except Exception as exc:  # noqa: BLE001
            job.status = "error"
            job.error = str(exc)
            job.message = str(exc)
            job.finished_at = time.time()
        finally:
            _live_workers.discard(job.id)
            persist_job(job)

    threading.Thread(target=worker, name=f"assemble-{job.id}", daemon=True).start()
    return job


def rebalance_latest_playlist(
    *,
    shares: Optional[dict[str, Any]] = None,
    target: int | None = None,
    event_name: str | None = None,
    min_fit: float | None = None,
) -> dict[str, Any]:
    """Rebuild Playlist from already-scored tracks with new lane targets."""
    job = latest_job()
    persist = _shares_were_provided(shares) or (min_fit is not None and min_fit != "")
    if min_fit is None and job is not None and not persist:
        lane_shares, fit_floor = resolve_mix(
            shares, getattr(job, "min_fit", None), persist=False
        )
    else:
        lane_shares, fit_floor = resolve_mix(shares, min_fit, persist=persist)
    if job and job.result and job.result.get("ranked"):
        job.lane_shares = lane_shares
        job.min_fit = fit_floor
        if target is not None:
            job.target = clamp_target(target)
        payload = _assemble_result(
            event_name=job.event_name,
            brief=job.brief,
            library=job.library,
            target=job.target,
            merged=job.result["ranked"],
            shares=lane_shares,
            min_fit=fit_floor,
            files=None,
        )
        job.result = payload
        return {"ok": True, "result": payload, "job": job.to_dict()}
    result = result_from_cache(
        event_name=event_name,
        target=clamp_target(target or DEFAULT_TARGET),
        shares=lane_shares,
        min_fit=fit_floor,
    )
    if not result:
        raise FileNotFoundError("No assembled playlist yet. Run Assemble first.")
    return {
        "ok": True,
        "result": result,
        "job": job.to_dict() if job else None,
    }


def export_latest_playlist() -> dict[str, Any]:
    """Write the latest assembled crate into Sets/<Event>."""
    job = latest_job()
    if not job or not job.result or not job.result.get("playlist"):
        raise FileNotFoundError("No assembled playlist yet. Run Assemble first.")
    files = write_playlist_files(
        job.result["playlist"], event_name=job.event_name or DEFAULT_EVENT["name"]
    )
    job.result = {**job.result, "files": files}
    return files


def cancel_job(job_id: str) -> Optional[AssembleJob]:
    job = get_job(job_id)
    if job and job.status in {"queued", "running"}:
        job.cancel = True
        job.message = "Stopping after this chunk…"
        persist_job(job)
    return job
