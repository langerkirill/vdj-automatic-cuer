"""
Set VirtualDJ beatgrid downbeat ("1") for a track.

Updates:
  - <Scan Phase="..."/> (VDJ's primary '1' for many tracks)
  - <Poi Type="beatgrid" Pos="..."/> when present; creates one if missing
"""

from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .autocue_path import ensure_autocue_on_path
from .config import CUES_ROOT, LIBRARIES, VDJ_DATABASE, assert_existing_audio
from .relocate import is_virtualdj_running, summarize_cues
from .db_lock import vdj_db_write

ensure_autocue_on_path()

from vdj_database_safety import (  # noqa: E402
    _find_song_span,
    normalize_database_path,
    read_vdj_database_text,
    rewrite_song_xml_in_database,
)

_SCAN_TAG_RE = re.compile(r"<Scan\b[^/]*?/?>", re.IGNORECASE | re.DOTALL)
_PHASE_ATTR_RE = re.compile(r'(\bPhase\s*=\s*")([^"]+)(")', re.IGNORECASE)
_BEATGRID_POI_RE = re.compile(
    r"[ \t]*<Poi\b[^>]*\bType\s*=\s*[\"']beatgrid[\"'][^>]*/?>[ \t]*(?:\r?\n)?",
    re.IGNORECASE,
)
_POI_POS_RE = re.compile(r'(\bPos\s*=\s*")([^"]+)(")', re.IGNORECASE)


def _assert_allowed(path: Path) -> Path:
    return assert_existing_audio(path)


def _format_pos(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    # Match VDJ-ish precision
    s = f"{seconds:.6f}".rstrip("0").rstrip(".")
    if "." not in s:
        s = f"{seconds:.1f}"
    return s


def apply_beatgrid_anchor_to_song_xml(
    song_xml: str,
    anchor_seconds: float,
) -> tuple[str, dict[str, Any]]:
    """
    Rewrite Scan Phase + beatgrid POI to the given downbeat time.

    Returns (new_xml, meta).
    """
    if not (0.0 <= float(anchor_seconds) < 24 * 3600):
        raise ValueError(f"Anchor out of range: {anchor_seconds}")
    pos_s = _format_pos(float(anchor_seconds))
    meta: dict[str, Any] = {
        "anchor": float(anchor_seconds),
        "scan_phase_updated": False,
        "beatgrid_poi_updated": False,
        "beatgrid_poi_created": False,
        "phase_before": None,
        "beatgrid_before": None,
    }

    # --- Scan Phase ---
    def scan_sub(m: re.Match[str]) -> str:
        tag = m.group(0)
        pm = _PHASE_ATTR_RE.search(tag)
        if pm:
            try:
                meta["phase_before"] = float(pm.group(2))
            except ValueError:
                meta["phase_before"] = pm.group(2)
            meta["scan_phase_updated"] = True
            return _PHASE_ATTR_RE.sub(
                lambda am: f"{am.group(1)}{pos_s}{am.group(3)}", tag, count=1
            )
        # Insert Phase before closing
        if tag.rstrip().endswith("/>"):
            meta["scan_phase_updated"] = True
            return tag[:-2].rstrip() + f' Phase="{pos_s}" />'
        return tag

    out = _SCAN_TAG_RE.sub(scan_sub, song_xml, count=1)

    # --- beatgrid POI ---
    bg = _BEATGRID_POI_RE.search(out)
    if bg:
        tag = bg.group(0)
        pos_m = _POI_POS_RE.search(tag)
        if pos_m:
            try:
                meta["beatgrid_before"] = float(pos_m.group(2))
            except ValueError:
                meta["beatgrid_before"] = pos_m.group(2)
            new_tag = _POI_POS_RE.sub(
                lambda am: f"{am.group(1)}{pos_s}{am.group(3)}", tag, count=1
            )
        else:
            # Rare: beatgrid without Pos
            new_tag = tag.replace("<Poi", f'<Poi Pos="{pos_s}"', 1)
        meta["beatgrid_poi_updated"] = True
        out = out[: bg.start()] + new_tag + out[bg.end() :]
    else:
        # Insert after Scan line if possible, else before </Song>
        newline = "\r\n" if "\r\n" in song_xml else "\n"
        poi_line = f'  <Poi Pos="{pos_s}" Type="beatgrid" />{newline}'
        scan_m = _SCAN_TAG_RE.search(out)
        if scan_m:
            insert_at = scan_m.end()
            # If Scan match didn't include trailing newline, keep tidy
            out = out[:insert_at] + newline + poi_line + out[insert_at:]
        else:
            close = re.search(r"</Song>", out, re.IGNORECASE)
            if close is None:
                raise ValueError("Song XML missing </Song>")
            out = out[: close.start()] + poi_line + out[close.start() :]
        meta["beatgrid_poi_created"] = True

    if not meta["scan_phase_updated"] and not (
        meta["beatgrid_poi_updated"] or meta["beatgrid_poi_created"]
    ):
        raise ValueError("Could not update Scan Phase or beatgrid POI on this Song")

    return out, meta


def set_beatgrid_anchor(
    source_path: str | Path,
    *,
    anchor_seconds: float,
    database_path: Path | None = None,
    dry_run: bool = False,
    allow_vdj_running: bool = False,
    create_backup: bool = True,
) -> dict[str, Any]:
    """Write a new downbeat time into VirtualDJ for this track."""
    audio = _assert_allowed(Path(source_path))
    db = Path(database_path) if database_path else VDJ_DATABASE
    if not db.is_file():
        raise FileNotFoundError(f"VDJ database not found: {db}")

    if is_virtualdj_running() and not dry_run and not allow_vdj_running:
        raise RuntimeError(
            "VirtualDJ is running. Close it before fixing the beatgrid, or pass "
            "allow_vdj_running=true (not recommended)."
        )

    before = summarize_cues(audio, db)
    if not before.in_database:
        raise KeyError(f"Track is not in VirtualDJ database: {audio}")

    content = read_vdj_database_text(db)
    candidates = [
        normalize_database_path(str(audio)),
        normalize_database_path(str(Path(source_path))),
    ]
    path_in_db = None
    span = None
    for cand in candidates:
        span = _find_song_span(content, cand)
        if span is not None:
            path_in_db = cand
            break
    if span is None or path_in_db is None:
        raise KeyError(f"Song not found in database: {audio}")

    start, end = span
    song_xml = content[start:end]
    new_song, meta = apply_beatgrid_anchor_to_song_xml(song_xml, float(anchor_seconds))

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "path": str(audio),
            "anchor": float(anchor_seconds),
            "changes": meta,
            "bpm": before.bpm,
            "database_backup": None,
            "cues": before.to_dict(),
        }

    backup: Optional[str] = None
    if create_backup:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = f"{db}.backup.{ts}.music-sorter-grid"
        shutil.copy2(db, backup)

    with vdj_db_write():
        rewrite_song_xml_in_database(db, path_in_db, new_song, validate=True)
    after = summarize_cues(audio, db)

    return {
        "ok": True,
        "dry_run": False,
        "path": str(audio),
        "name": audio.name,
        "anchor": float(anchor_seconds),
        "changes": meta,
        "bpm": after.bpm or before.bpm,
        "beatgrid_pos": after.beatgrid_pos,
        "scan_phase": after.scan_phase,
        "cues": after.to_dict(),
        "database_backup": backup,
    }
