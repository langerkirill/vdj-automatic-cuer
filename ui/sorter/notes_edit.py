"""
Read/write VirtualDJ <Comment> notes on a Song entry (surgical XML rewrite).
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
    _COMMENT_RE,
    _detect_newline,
    _escape_xml_attr,
    _find_song_span,
    normalize_database_path,
    read_vdj_database_text,
    rewrite_song_xml_in_database,
)


def _assert_allowed(path: Path) -> Path:
    return assert_existing_audio(path)


def apply_comment_to_song_xml(song_xml: str, comment: str) -> str:
    """
    Set or remove the Song <Comment> element while preserving other markup.

    Empty comment removes the Comment node entirely.
    """
    newline = _detect_newline(song_xml)
    cleaned = _COMMENT_RE.sub("", song_xml)
    text = (comment or "").strip()
    if not text:
        return cleaned

    close_idx = cleaned.rfind("</Song>")
    if close_idx < 0:
        raise ValueError("Song XML is missing </Song>")

    body = cleaned[:close_idx].rstrip(" \t")
    if not body.endswith("\n"):
        body += newline
    escaped = _escape_xml_attr(text)
    # Also escape newlines as spaces? VDJ comments are usually single-line but
    # multi-line is fine as text content; keep newlines as-is (not in attrs).
    # For element text, & < > need escaping; _escape_xml_attr also does quotes.
    insertion = f"  <Comment>{escaped}</Comment>{newline}"
    return body + insertion + cleaned[close_idx:]


def set_track_comment(
    source_path: str | Path,
    comment: str,
    *,
    database_path: Path | None = None,
    dry_run: bool = False,
    allow_vdj_running: bool = False,
    create_backup: bool = False,
) -> dict[str, Any]:
    """
    Write VirtualDJ Comment notes for a track.

    create_backup defaults False for live typing (frequent saves); callers can
    enable for explicit Save actions.
    """
    audio = _assert_allowed(Path(source_path))
    db = Path(database_path) if database_path else VDJ_DATABASE
    if not db.is_file():
        raise FileNotFoundError(f"VDJ database not found: {db}")

    if is_virtualdj_running() and not dry_run and not allow_vdj_running:
        raise RuntimeError(
            "VirtualDJ is running. Close it before writing notes, or pass "
            "allow_vdj_running=true (notes may be overwritten when VDJ quits)."
        )

    before = summarize_cues(audio, db)
    if not before.in_database:
        raise KeyError(
            f"Track is not in VirtualDJ database — open it in VDJ once first: {audio.name}"
        )

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
    new_comment = (comment or "").strip()
    new_song = apply_comment_to_song_xml(song_xml, new_comment)

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "path": str(audio),
            "comment_before": before.comment,
            "comment": new_comment,
            "database_backup": None,
        }

    if new_song == song_xml:
        return {
            "ok": True,
            "dry_run": False,
            "path": str(audio),
            "name": audio.name,
            "comment_before": before.comment,
            "comment": new_comment,
            "unchanged": True,
            "database_backup": None,
        }

    backup: Optional[str] = None
    if create_backup:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = f"{db}.backup.{ts}.music-sorter-notes"
        shutil.copy2(db, backup)

    with vdj_db_write():
        rewrite_song_xml_in_database(db, path_in_db, new_song, validate=True)
    after = summarize_cues(audio, db)

    return {
        "ok": True,
        "dry_run": False,
        "path": str(audio),
        "name": audio.name,
        "comment_before": before.comment,
        "comment": after.comment,
        "unchanged": False,
        "database_backup": backup,
    }
