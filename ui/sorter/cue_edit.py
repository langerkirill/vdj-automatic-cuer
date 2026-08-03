"""
Delete individual VirtualDJ cue/loop markers via surgical Song XML rewrite.

Keeps beatgrid, Num=0 hotcues, automix points, and CRLF line endings intact.
"""

from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .autocue_path import ensure_autocue_on_path
from .config import CUES_ROOT, LIBRARIES, VDJ_DATABASE
from .relocate import is_virtualdj_running, summarize_cues

ensure_autocue_on_path()

from vdj_database_safety import (  # noqa: E402
    _POI_LINE_RE,
    _find_song_span,
    _is_manual_cue_or_loop_poi,
    _poi_attr,
    normalize_database_path,
    parse_manual_poi_tag,
    read_vdj_database_text,
    rewrite_song_xml_in_database,
)

POS_TOLERANCE = 0.02  # seconds


def _assert_allowed(path: Path) -> Path:
    audio = path.expanduser().resolve()
    if not audio.is_file():
        raise FileNotFoundError(f"Audio not found: {audio}")
    roots = [CUES_ROOT.resolve(), *[p.resolve() for p in LIBRARIES.values()]]
    for root in roots:
        try:
            audio.relative_to(root)
            return audio
        except ValueError:
            continue
    raise ValueError("Cue edit is only allowed under Cues/ or House/Zouk libraries")


def _poi_matches(
    tag: str,
    *,
    kind: str,
    pos: float,
    num: str | None,
    name: str | None,
    slot: str | None = None,
) -> bool:
    if not _is_manual_cue_or_loop_poi(tag):
        return False
    parsed = parse_manual_poi_tag(tag)
    if parsed is None:
        return False
    want_kind = "loop" if kind == "loop" else "cue"
    if parsed["kind"] != want_kind:
        return False
    if abs(float(parsed["position"]) - float(pos)) > POS_TOLERANCE:
        return False
    # Loops almost always share Num="-1"; use Slot when provided to disambiguate.
    tag_slot = _poi_attr(tag, "Slot")
    if (
        want_kind == "loop"
        and slot is not None
        and str(slot) not in {"", "None"}
        and tag_slot is not None
        and str(tag_slot) != str(slot)
    ):
        return False
    if num is not None and str(parsed.get("num") or "") != str(num):
        # Loops often share Num="-1"; still require match when provided.
        if want_kind == "cue" or str(num) not in {"", "-1"}:
            return False
    if name is not None and name != "" and str(parsed.get("name") or "") != str(name):
        # Name is soft match only when pos+num already match — skip strict name.
        pass
    return True


def remove_manual_poi_from_song_xml(
    song_xml: str,
    *,
    kind: str,
    pos: float,
    num: str | None = None,
    name: str | None = None,
    slot: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """
    Remove the first matching manual cue/loop POI line from a Song block.

    Returns (new_xml, removed_info).
    """
    kind = "loop" if str(kind).lower() == "loop" else "cue"

    # Try strict match first; for loops fall back to pos-only (Num is usually -1).
    attempts: list[tuple[str | None, str | None, str | None]] = [
        (num, name, slot),
    ]
    if kind == "loop" and (num is not None or slot is not None or name is not None):
        attempts.append((None, None, None))

    last_error: Optional[KeyError] = None
    for try_num, try_name, try_slot in attempts:
        removed: Optional[dict[str, Any]] = None

        def repl(match: re.Match[str]) -> str:
            nonlocal removed
            tag = match.group(0)
            if removed is not None:
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
            parsed = parse_manual_poi_tag(tag) or {}
            removed = {
                "kind": parsed.get("kind", kind),
                "name": parsed.get("name"),
                "pos": parsed.get("position"),
                "num": parsed.get("num"),
                "size": _poi_attr(tag, "Size"),
                "slot": _poi_attr(tag, "Slot"),
                "raw": tag.strip(),
            }
            return ""

        new_xml = _POI_LINE_RE.sub(repl, song_xml)
        if removed is not None:
            return new_xml, removed
        last_error = KeyError(
            f"No matching {kind} at pos≈{pos}"
            + (f" num={try_num}" if try_num is not None else "")
            + (f" slot={try_slot}" if try_slot is not None else "")
        )

    raise last_error or KeyError(f"No matching {kind} at pos≈{pos}")


def delete_cue_point(
    source_path: str | Path,
    *,
    kind: str,
    pos: float,
    num: str | None = None,
    name: str | None = None,
    slot: str | None = None,
    database_path: Path | None = None,
    dry_run: bool = False,
    allow_vdj_running: bool = False,
    create_backup: bool = True,
) -> dict[str, Any]:
    """
    Delete one cue or loop from VirtualDJ database.xml for this audio path.
    """
    audio = _assert_allowed(Path(source_path))
    db = Path(database_path) if database_path else VDJ_DATABASE
    if not db.is_file():
        raise FileNotFoundError(f"VDJ database not found: {db}")

    if is_virtualdj_running() and not dry_run and not allow_vdj_running:
        raise RuntimeError(
            "VirtualDJ is running. Close it before deleting cues, or pass "
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
    new_song, removed = remove_manual_poi_from_song_xml(
        song_xml,
        kind=kind,
        pos=float(pos),
        num=num,
        name=name,
        slot=slot,
    )

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "path": str(audio),
            "removed": removed,
            "cue_count_before": before.cue_count,
            "loop_count_before": before.loop_count,
            "database_backup": None,
        }

    backup: Optional[str] = None
    if create_backup:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = f"{db}.backup.{ts}.music-sorter-cue-del"
        shutil.copy2(db, backup)

    rewrite_song_xml_in_database(db, path_in_db, new_song, validate=True)
    after = summarize_cues(audio, db)

    return {
        "ok": True,
        "dry_run": False,
        "path": str(audio),
        "name": audio.name,
        "removed": removed,
        "cue_count_before": before.cue_count,
        "loop_count_before": before.loop_count,
        "cue_count_after": after.cue_count,
        "loop_count_after": after.loop_count,
        "cues": after.to_dict(),
        "database_backup": backup,
    }
