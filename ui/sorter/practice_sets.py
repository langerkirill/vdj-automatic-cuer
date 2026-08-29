"""Practice mix discovery + tracklists from VirtualDJ database cues."""

from __future__ import annotations

import re
import subprocess
from dataclasses import asdict, dataclass, field
from html import unescape
from pathlib import Path
from typing import Any, Optional

from .config import MIXES_ROOT, VDJ_DATABASE, AUDIO_EXTENSIONS
from .transitions_db import (
    is_practice_mix_excluded,
    lookup_options,
    load_practice_scores,
    normalize_key,
)


@dataclass
class PracticeTrack:
    index: int
    name: str
    pos_sec: float
    num: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PracticeTransition:
    index: int
    from_track: str
    to_track: str
    at_sec: float
    duration_est_sec: float = 0.0
    alternatives: list[dict[str, Any]] = field(default_factory=list)
    score: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _ffprobe_duration(path: Path) -> Optional[float]:
    try:
        out = subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                str(path),
            ],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return float(out) if out else None
    except (subprocess.CalledProcessError, ValueError, FileNotFoundError, OSError):
        return None


def list_practice_mixes(
    mixes_root: Path | None = None,
    *,
    max_age_days: Optional[int] = None,
) -> list[dict[str, Any]]:
    """List practice mix audio files (newest first)."""
    root = Path(mixes_root or MIXES_ROOT)
    if not root.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for path in root.iterdir():
        if not path.is_file():
            continue
        if path.suffix.lower() not in AUDIO_EXTENSIONS:
            continue
        # Prefer pj* practice naming but include other recent large mixes
        name = path.name
        try:
            st = path.stat()
        except OSError:
            continue
        size = st.st_size
        if size < 1_000_000:  # skip tiny files
            continue
        items.append(
            {
                "path": str(path.resolve()),
                "name": name,
                "size_bytes": size,
                "mtime": st.st_mtime,
                "is_practice": name.lower().startswith("pj")
                or "practice" in name.lower(),
            }
        )
    items.sort(key=lambda x: x["mtime"], reverse=True)
    # Attach duration + track counts lazily is expensive; do light duration only
    for it in items[:30]:
        dur = _ffprobe_duration(Path(it["path"]))
        it["duration_sec"] = dur
    return items


def _parse_song_pois(body: str) -> list[dict[str, Any]]:
    pois: list[dict[str, Any]] = []
    for m in re.finditer(r"<Poi\b([^/>]*)/?>", body):
        attrs = m.group(1)

        def attr(k: str, default: str = "") -> str:
            mm = re.search(rf'{k}="([^"]*)"', attrs)
            return unescape(mm.group(1)) if mm else default

        name = attr("Name")
        ptype = attr("Type")
        if ptype and ptype != "cue" and not name:
            continue
        if not name and ptype != "cue":
            continue
        try:
            pos = float(attr("Pos") or "0")
        except ValueError:
            pos = 0.0
        pois.append(
            {
                "name": name,
                "pos": pos,
                "num": attr("Num") or None,
                "type": ptype or "cue",
            }
        )
    # Dedup
    seen: set[tuple[float, str]] = set()
    out: list[dict[str, Any]] = []
    for p in sorted(pois, key=lambda x: (x["pos"], x["name"])):
        key = (p["pos"], p["name"])
        if key in seen:
            continue
        seen.add(key)
        if p["name"] or p["type"] == "cue":
            out.append(p)
    return out


def tracklist_from_vdj(
    mix_path: str | Path,
    database_path: Path | None = None,
) -> list[PracticeTrack]:
    """Read named cue points on a mix file from VirtualDJ database.xml."""
    path = Path(mix_path).expanduser().resolve()
    db = Path(database_path or VDJ_DATABASE)
    if not db.is_file():
        return []
    # Stream-ish: search for FilePath containing the basename
    text = db.read_text(encoding="utf-8", errors="replace")
    # Escape for regex
    basenames = {path.name, path.name.replace("&", "&amp;")}
    # Find Song blocks that reference this mix
    for m in re.finditer(r"<Song\b([^>]*)>(.*?)</Song>", text, re.DOTALL):
        attrs, body = m.group(1), m.group(2)
        fp_m = re.search(r'FilePath="([^"]+)"', attrs)
        if not fp_m:
            continue
        fp = unescape(fp_m.group(1))
        if Path(fp).name not in basenames and str(path) not in fp:
            # also match by resolved path tail
            if path.name not in fp:
                continue
        pois = _parse_song_pois(body)
        tracks: list[PracticeTrack] = []
        named = [p for p in pois if p.get("name")]
        if not named:
            return []
        for i, p in enumerate(named):
            tracks.append(
                PracticeTrack(
                    index=i,
                    name=p["name"],
                    pos_sec=float(p["pos"] or 0),
                    num=p.get("num"),
                )
            )
        return tracks
    return []


def build_transitions(
    tracks: list[PracticeTrack],
    *,
    mix_duration: Optional[float] = None,
    include_alternatives: bool = True,
    mix_path: str | None = None,
) -> list[PracticeTransition]:
    transitions: list[PracticeTransition] = []
    scores_by_idx: dict[int, dict[str, Any]] = {}
    if mix_path:
        for s in load_practice_scores(mix_path):
            scores_by_idx[int(s["transition_index"])] = {
                "overall": s.get("overall"),
                "smoothness": s.get("smoothness"),
                "creativity": s.get("creativity"),
                "flow": s.get("flow"),
                "energy_match": s.get("energy_match"),
                "comments": s.get("comments") or "",
                "save_for_set": bool(s.get("save_for_set")),
                "model": s.get("model") or "",
                "strengths": s.get("strengths") or [],
                "improvements": s.get("improvements") or [],
                "analyzed_at": s.get("analyzed_at"),
                "better_option_track": s.get("better_option_track") or "",
                "better_option_reason": s.get("better_option_reason") or "",
                "better_option_source": s.get("better_option_source") or "",
                "better_option_confidence": s.get("better_option_confidence"),
                "clip_start_sec": s.get("clip_start_sec"),
                "clip_duration_sec": s.get("clip_duration_sec"),
            }

    for i in range(len(tracks) - 1):
        a = tracks[i]
        b = tracks[i + 1]
        dur_est = max(0.0, b.pos_sec - a.pos_sec)
        alts: list[dict[str, Any]] = []
        if include_alternatives:
            alts = lookup_options(a.name, limit=10)
            to_key = normalize_key(b.name)
            for opt in alts:
                opt["is_actual"] = normalize_key(opt.get("to_label") or "") == to_key or (
                    _token_hit(b.name, opt.get("to_label") or "")
                )
        score = scores_by_idx.get(i)
        transitions.append(
            PracticeTransition(
                index=i,
                from_track=a.name,
                to_track=b.name,
                at_sec=float(b.pos_sec),
                duration_est_sec=float(dur_est),
                alternatives=alts,
                score=score,
            )
        )
    return transitions


def _token_hit(a: str, b: str) -> bool:
    ta = set(normalize_key(a).split())
    tb = set(normalize_key(b).split())
    if not ta or not tb:
        return False
    return len(ta & tb) >= min(2, len(ta), len(tb)) and (
        len(ta & tb) / max(len(ta), len(tb)) >= 0.5
    )


def get_practice_set_detail(
    mix_path: str | Path,
    *,
    include_alternatives: bool = True,
) -> dict[str, Any]:
    path = Path(mix_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Mix not found: {path}")
    duration = _ffprobe_duration(path)
    tracks = tracklist_from_vdj(path)
    transitions = build_transitions(
        tracks,
        mix_duration=duration,
        include_alternatives=include_alternatives,
        mix_path=str(path),
    )
    # Unique track names across set
    unique = []
    seen = set()
    for t in tracks:
        k = normalize_key(t.name)
        if k in seen:
            continue
        seen.add(k)
        unique.append(t.name)

    return {
        "path": str(path),
        "name": path.name,
        "duration_sec": duration,
        "size_bytes": path.stat().st_size,
        "mtime": path.stat().st_mtime,
        "tracks": [t.to_dict() for t in tracks],
        "track_count": len(tracks),
        "unique_tracks": unique,
        "transitions": [t.to_dict() for t in transitions],
        "transition_count": len(transitions),
        "exclude_from_best": is_practice_mix_excluded(str(path)),
    }


def all_tracks_across_mixes(
    mixes: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Union of tracks played in listed practice mixes."""
    mixes = mixes if mixes is not None else list_practice_mixes()
    freq: dict[str, dict[str, Any]] = {}
    for m in mixes:
        if not m.get("is_practice") and not str(m.get("name", "")).lower().startswith(
            "pj"
        ):
            # still include if recent pj naming missed
            pass
        try:
            tracks = tracklist_from_vdj(m["path"])
        except Exception:
            continue
        for t in tracks:
            k = normalize_key(t.name)
            if not k:
                continue
            if k not in freq:
                freq[k] = {"name": t.name, "count": 0, "mixes": []}
            freq[k]["count"] += 1
            if m["name"] not in freq[k]["mixes"]:
                freq[k]["mixes"].append(m["name"])
    ranked = sorted(freq.values(), key=lambda x: (-x["count"], x["name"].lower()))
    return {"tracks": ranked, "total_unique": len(ranked)}
