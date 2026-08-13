"""Rename VirtualDJ cue/loop POI Name attributes (surgical Song rewrite)."""

from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .autocue_path import ensure_autocue_on_path
from .config import CUES_ROOT, LIBRARIES, VDJ_DATABASE
from .relocate import is_virtualdj_running, summarize_cues
from .db_lock import vdj_db_write
from .cue_edit import (  # reuse matchers / song span helpers
    POS_TOLERANCE,
    _assert_allowed,
    _poi_matches,
    _resolve_song_span,
)

ensure_autocue_on_path()

from vdj_database_safety import (  # noqa: E402
    _POI_LINE_RE,
    _poi_attr,
    parse_manual_poi_tag,
    read_vdj_database_text,
    rewrite_song_xml_in_database,
)

_NAME_ATTR_RE = re.compile(r'(\bName\s*=\s*")([^"]*)(")', re.IGNORECASE)


def _escape_xml_attr(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def set_poi_name_in_song_xml(
    song_xml: str,
    *,
    kind: str,
    pos: float,
    new_name: str,
    num: str | None = None,
    name: str | None = None,
    slot: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Set Name= on a matching cue or loop POI."""
    kind = "loop" if str(kind).lower() == "loop" else "cue"
    cleaned = (new_name or "").strip()
    if not cleaned:
        raise ValueError("Name cannot be empty")
    if len(cleaned) > 120:
        raise ValueError("Name too long (max 120 characters)")
    escaped = _escape_xml_attr(cleaned)

    attempts: list[tuple[str | None, str | None, str | None]] = [
        (num, name, slot),
    ]
    if kind == "loop" and (num is not None or slot is not None or name is not None):
        attempts.append((None, None, None))
    elif kind == "cue" and (num is not None or name is not None):
        attempts.append((None, None, None))

    last_error: Optional[KeyError] = None
    for try_num, try_name, try_slot in attempts:
        changed: Optional[dict[str, Any]] = None

        def repl(match: re.Match[str]) -> str:
            nonlocal changed
            tag = match.group(0)
            if changed is not None:
                return tag
            if not _poi_matches(
                tag,
                kind=kind,
                pos=pos,
                num=try_num,
                name=try_name,
                slot=try_slot,
            ):
                return tag
            old_name = _poi_attr(tag, "Name")
            if _NAME_ATTR_RE.search(tag):
                new_tag = _NAME_ATTR_RE.sub(
                    lambda m: f"{m.group(1)}{escaped}{m.group(3)}",
                    tag,
                    count=1,
                )
            else:
                new_tag = re.sub(
                    r"<Poi\b",
                    f'<Poi Name="{escaped}"',
                    tag,
                    count=1,
                    flags=re.IGNORECASE,
                )
            parsed = parse_manual_poi_tag(tag) or {}
            changed = {
                "kind": kind,
                "pos": parsed.get("position"),
                "num": parsed.get("num"),
                "slot": _poi_attr(tag, "Slot"),
                "name_before": old_name,
                "name_after": cleaned,
            }
            return new_tag

        new_xml = _POI_LINE_RE.sub(repl, song_xml)
        if changed is not None:
            return new_xml, changed
        last_error = KeyError(
            f"No matching {kind} at pos≈{pos}"
            + (f" num={try_num}" if try_num is not None else "")
        )

    raise last_error or KeyError(f"No matching {kind} at pos≈{pos}")


def set_poi_name(
    source_path: str | Path,
    *,
    kind: str,
    pos: float,
    new_name: str,
    num: str | None = None,
    name: str | None = None,
    slot: str | None = None,
    database_path: Path | None = None,
    dry_run: bool = False,
    allow_vdj_running: bool = False,
    create_backup: bool = True,
) -> dict[str, Any]:
    """Rename one cue or loop in VirtualDJ database.xml."""
    audio = _assert_allowed(Path(source_path))
    db = Path(database_path) if database_path else VDJ_DATABASE
    if not db.is_file():
        raise FileNotFoundError(f"VDJ database not found: {db}")

    if is_virtualdj_running() and not dry_run and not allow_vdj_running:
        raise RuntimeError(
            "VirtualDJ is running. Close it before renaming markers, or pass "
            "allow_vdj_running=true (not recommended)."
        )

    before = summarize_cues(audio, db)
    if not before.in_database:
        raise KeyError(f"Track is not in VirtualDJ database: {audio}")

    content = read_vdj_database_text(db)
    path_in_db, start, end = _resolve_song_span(content, audio, source_path)
    song_xml = content[start:end]
    new_song, change = set_poi_name_in_song_xml(
        song_xml,
        kind=kind,
        pos=float(pos),
        new_name=new_name,
        num=num,
        name=name,
        slot=slot,
    )

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "path": str(audio),
            "change": change,
            "cues": before.to_dict(),
            "database_backup": None,
        }

    backup: Optional[str] = None
    if create_backup:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = f"{db}.backup.{ts}.music-sorter-poi-rename"
        shutil.copy2(db, backup)

    with vdj_db_write():
        rewrite_song_xml_in_database(db, path_in_db, new_song, validate=True)
    after = summarize_cues(audio, db)

    return {
        "ok": True,
        "dry_run": False,
        "path": str(audio),
        "name": audio.name,
        "change": change,
        "cues": after.to_dict(),
        "database_backup": backup,
        "loop_count": after.loop_count,
        "cue_count": after.cue_count,
    }
