"""
Edit individual VirtualDJ cue/loop markers via surgical Song XML rewrite.

Supports delete, color, rename, move, loop resize, and adding a cue.
Keeps beatgrid, Num=0 hotcues, automix points, and CRLF line endings intact.
"""

from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .autocue_path import ensure_autocue_on_path
from .config import CUES_ROOT, LIBRARIES, VDJ_DATABASE, assert_existing_audio
from .ml_training import schedule_training_drop, schedule_training_update
from .relocate import is_virtualdj_running, summarize_cues
from .db_lock import vdj_db_write

ensure_autocue_on_path()

from vdj_database_safety import (  # noqa: E402
    _POI_LINE_RE,
    _find_song_span,
    _is_manual_cue_or_loop_poi,
    _poi_attr,
    format_vdj_poi_line,
    normalize_database_path,
    parse_manual_poi_tag,
    read_vdj_database_text,
    rewrite_song_xml_in_database,
)

POS_TOLERANCE = 0.02  # seconds


def _schedule_ml_after_cue_change(path: Path, summary: Any) -> None:
    if summary is None:
        return
    if getattr(summary, "cue_count", 0) or getattr(summary, "loop_count", 0):
        schedule_training_update(path, summary)
    else:
        schedule_training_drop(path)


def _assert_allowed(path: Path) -> Path:
    return assert_existing_audio(path)


def _poi_matches(
    tag: str,
    *,
    kind: str,
    pos: float,
    num: str | None,
    name: str | None,
    slot: str | None = None,
    require_pos: bool = True,
) -> bool:
    if not _is_manual_cue_or_loop_poi(tag):
        return False
    parsed = parse_manual_poi_tag(tag)
    if parsed is None:
        return False
    want_kind = "loop" if kind == "loop" else "cue"
    if parsed["kind"] != want_kind:
        return False
    if require_pos:
        if abs(float(parsed["position"]) - float(pos)) > POS_TOLERANCE:
            return False
    elif want_kind == "cue":
        if num is None or str(num) in {"", "-1", "0"}:
            return False
    elif want_kind == "loop":
        if slot is None or str(slot) in {"", "None"}:
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


def _match_attempts(
    kind: str,
    num: str | None,
    name: str | None,
    slot: str | None,
) -> list[tuple[str | None, str | None, str | None, bool]]:
    """Pos match first; then cue Num / loop Slot after a drag moved Pos."""
    attempts: list[tuple[str | None, str | None, str | None, bool]] = [
        (num, name, slot, True),
    ]
    if kind == "loop" and (num is not None or slot is not None or name is not None):
        attempts.append((None, None, None, True))
        if slot is not None and str(slot) not in {"", "None"}:
            attempts.append((None, None, slot, False))
    elif kind == "cue" and (num is not None or name is not None):
        attempts.append((None, None, None, True))
        if num is not None and str(num) not in {"", "-1", "0"}:
            attempts.append((num, None, None, False))
    return attempts


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

    last_error: Optional[KeyError] = None
    for try_num, try_name, try_slot, require_pos in _match_attempts(
        kind, num, name, slot
    ):
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
                require_pos=require_pos,
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


_SIZE_ATTR_RE = re.compile(r'(\bSize\s*=\s*")([^"]+)(")', re.IGNORECASE)
_COLOR_ATTR_RE = re.compile(r'(\bColor\s*=\s*")([^"]+)(")', re.IGNORECASE)
_POS_ATTR_RE = re.compile(r'(\bPos\s*=\s*")([^"]+)(")', re.IGNORECASE)

# Practical loop bounds in beats (VDJ typically uses powers of two).
MIN_LOOP_BEATS = 1.0
MAX_LOOP_BEATS = 256.0

# AutoCue / Music Sorter palette (ARGB ints as stored in database.xml).
VDJ_CUE_COLORS: dict[str, str] = {
    "blue": "4278190335",
    "green": "4278255360",
    "purple": "4288020735",
    "yellow": "4294967040",
    "orange": "4294934272",
}
VDJ_CUE_COLOR_NAMES: dict[str, str] = {v: k for k, v in VDJ_CUE_COLORS.items()}


def normalize_cue_color(color: str) -> tuple[str, str]:
    """
    Accept a color name (blue) or raw VDJ Color int string.

    Returns (name, raw_color_int_string).
    """
    raw = (color or "").strip()
    if not raw:
        raise ValueError("Color is required")
    key = raw.lower()
    if key in VDJ_CUE_COLORS:
        return key, VDJ_CUE_COLORS[key]
    if raw in VDJ_CUE_COLOR_NAMES:
        return VDJ_CUE_COLOR_NAMES[raw], raw
    # Numeric-ish unknown — keep as-is under name "custom"
    if raw.isdigit():
        return VDJ_CUE_COLOR_NAMES.get(raw, "unknown"), raw
    raise ValueError(
        f"Unknown color {color!r}; use blue/green/purple/yellow/orange"
    )


def _format_loop_size(beats: float) -> str:
    """Match common VDJ Size formatting (e.g. 16.0, 8.0)."""
    if abs(beats - round(beats)) < 1e-6:
        return f"{beats:.1f}" if beats < 1000 else f"{beats:.0f}"
    s = f"{beats:.6f}".rstrip("0").rstrip(".")
    return s


def scale_loop_size_in_song_xml(
    song_xml: str,
    *,
    pos: float,
    factor: float,
    num: str | None = None,
    name: str | None = None,
    slot: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """
    Multiply a loop POI Size by factor (0.5 = half, 2.0 = double).

    Returns (new_xml, change_info).
    """
    if factor <= 0:
        raise ValueError(f"Invalid size factor: {factor}")

    attempts: list[tuple[str | None, str | None, str | None]] = [
        (num, name, slot),
    ]
    if num is not None or slot is not None or name is not None:
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
                kind="loop",
                pos=pos,
                num=try_num,
                name=try_name,
                slot=try_slot,
            ):
                return tag
            size_raw = _poi_attr(tag, "Size")
            if size_raw is None:
                return tag
            try:
                old_beats = float(size_raw)
            except ValueError:
                return tag
            if old_beats <= 0:
                return tag
            new_beats = old_beats * float(factor)
            new_beats = max(MIN_LOOP_BEATS, min(MAX_LOOP_BEATS, new_beats))
            # Prefer clean whole-beat sizes when very close (e.g. 15.999 → 16).
            if abs(new_beats - round(new_beats)) < 0.05:
                new_beats = float(round(new_beats))
            new_s = _format_loop_size(new_beats)
            if not _SIZE_ATTR_RE.search(tag):
                return tag
            new_tag = _SIZE_ATTR_RE.sub(
                lambda m: f"{m.group(1)}{new_s}{m.group(3)}", tag, count=1
            )
            parsed = parse_manual_poi_tag(tag) or {}
            changed = {
                "kind": "loop",
                "name": parsed.get("name"),
                "pos": parsed.get("position"),
                "num": parsed.get("num"),
                "slot": _poi_attr(tag, "Slot"),
                "size_before": size_raw,
                "size_after": new_s,
                "beats_before": old_beats,
                "beats_after": new_beats,
                "factor": factor,
            }
            return new_tag

        new_xml = _POI_LINE_RE.sub(repl, song_xml)
        if changed is not None:
            return new_xml, changed
        last_error = KeyError(
            f"No matching loop at pos≈{pos}"
            + (f" num={try_num}" if try_num is not None else "")
            + (f" slot={try_slot}" if try_slot is not None else "")
        )

    raise last_error or KeyError(f"No matching loop at pos≈{pos}")


def _resolve_song_span(
    content: str,
    audio: Path,
    source_path: str | Path,
) -> tuple[str, int, int]:
    candidates = [
        normalize_database_path(str(audio)),
        normalize_database_path(str(Path(source_path))),
    ]
    for cand in candidates:
        span = _find_song_span(content, cand)
        if span is not None:
            return cand, span[0], span[1]
    raise KeyError(f"Song not found in database: {audio}")


def scale_loop_point(
    source_path: str | Path,
    *,
    pos: float,
    factor: float,
    num: str | None = None,
    name: str | None = None,
    slot: str | None = None,
    database_path: Path | None = None,
    dry_run: bool = False,
    allow_vdj_running: bool = False,
    create_backup: bool = True,
) -> dict[str, Any]:
    """
    Halve or double one loop's Size (beats) in VirtualDJ for this audio path.
    """
    audio = _assert_allowed(Path(source_path))
    db = Path(database_path) if database_path else VDJ_DATABASE
    if not db.is_file():
        raise FileNotFoundError(f"VDJ database not found: {db}")

    if is_virtualdj_running() and not dry_run and not allow_vdj_running:
        raise RuntimeError(
            "VirtualDJ is running. Close it before resizing loops, or pass "
            "allow_vdj_running=true (not recommended)."
        )

    before = summarize_cues(audio, db)
    if not before.in_database:
        raise KeyError(f"Track is not in VirtualDJ database: {audio}")

    content = read_vdj_database_text(db)
    path_in_db, start, end = _resolve_song_span(content, audio, source_path)
    song_xml = content[start:end]
    new_song, change = scale_loop_size_in_song_xml(
        song_xml,
        pos=float(pos),
        factor=float(factor),
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
        backup = f"{db}.backup.{ts}.music-sorter-loop-size"
        shutil.copy2(db, backup)

    with vdj_db_write():
        rewrite_song_xml_in_database(db, path_in_db, new_song, validate=True)
    after = summarize_cues(audio, db)
    _schedule_ml_after_cue_change(audio, after)

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


def set_poi_color_in_song_xml(
    song_xml: str,
    *,
    kind: str,
    pos: float,
    color: str,
    num: str | None = None,
    name: str | None = None,
    slot: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Set Color= on a matching cue or loop POI. Returns (xml, change_info)."""
    kind = "loop" if str(kind).lower() == "loop" else "cue"
    color_name, color_raw = normalize_cue_color(color)

    last_error: Optional[KeyError] = None
    for try_num, try_name, try_slot, require_pos in _match_attempts(
        kind, num, name, slot
    ):
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
                require_pos=require_pos,
            ):
                return tag
            old_color = _poi_attr(tag, "Color")
            if _COLOR_ATTR_RE.search(tag):
                new_tag = _COLOR_ATTR_RE.sub(
                    lambda m: f"{m.group(1)}{color_raw}{m.group(3)}",
                    tag,
                    count=1,
                )
            else:
                # Insert Color after opening <Poi
                new_tag = re.sub(
                    r"<Poi\b",
                    f'<Poi Color="{color_raw}"',
                    tag,
                    count=1,
                    flags=re.IGNORECASE,
                )
            parsed = parse_manual_poi_tag(tag) or {}
            changed = {
                "kind": kind,
                "name": parsed.get("name"),
                "pos": parsed.get("position"),
                "num": parsed.get("num"),
                "slot": _poi_attr(tag, "Slot"),
                "color_before": old_color,
                "color_after": color_raw,
                "color_name": color_name,
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


def fill_missing_poi_colors_in_song_xml(
    song_xml: str,
    *,
    default_color: str = "green",
    color_for_tag: Optional[Any] = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Insert Color= on cue/loop POIs that have none. Leaves existing colors."""
    default_name, default_raw = normalize_cue_color(default_color)
    changes: list[dict[str, Any]] = []

    def repl(match: re.Match[str]) -> str:
        tag = match.group(0)
        if not _is_manual_cue_or_loop_poi(tag):
            return tag
        existing = _poi_attr(tag, "Color")
        if existing and existing in VDJ_CUE_COLOR_NAMES:
            return tag
        parsed_name = (_poi_attr(tag, "Name") or "").strip()
        if parsed_name.lower().startswith("energy "):
            return tag
        parsed = parse_manual_poi_tag(tag) or {}
        chosen = default_name
        if color_for_tag is not None:
            hinted = color_for_tag(parsed, tag)
            if hinted:
                chosen = str(hinted)
        name, raw = normalize_cue_color(chosen)
        if existing:
            new_tag = _COLOR_ATTR_RE.sub(
                lambda m: f"{m.group(1)}{raw}{m.group(3)}",
                tag,
                count=1,
            )
        else:
            new_tag = re.sub(
                r"<Poi\b",
                f'<Poi Color="{raw}"',
                tag,
                count=1,
                flags=re.IGNORECASE,
            )
        changes.append(
            {
                "kind": parsed.get("kind"),
                "name": parsed.get("name"),
                "pos": parsed.get("position"),
                "color_name": name,
            }
        )
        return new_tag

    return _POI_LINE_RE.sub(repl, song_xml), changes


def fill_missing_poi_colors(
    source_path: str | Path,
    *,
    default_color: str = "green",
    database_path: Path | None = None,
    dry_run: bool = False,
    allow_vdj_running: bool = False,
    create_backup: bool = False,
    color_for_tag: Optional[Any] = None,
) -> dict[str, Any]:
    """Write Color= onto every cue/loop that is missing one."""
    audio = _assert_allowed(Path(source_path))
    db = Path(database_path) if database_path else VDJ_DATABASE
    if not db.is_file():
        raise FileNotFoundError(f"VDJ database not found: {db}")
    if is_virtualdj_running() and not dry_run and not allow_vdj_running:
        raise RuntimeError(
            "VirtualDJ is running. Close it before coloring cues, or pass "
            "allow_vdj_running=true (not recommended)."
        )
    content = read_vdj_database_text(db)
    path_in_db, start, end = _resolve_song_span(content, audio, source_path)
    new_song, changes = fill_missing_poi_colors_in_song_xml(
        content[start:end],
        default_color=default_color,
        color_for_tag=color_for_tag,
    )
    if dry_run or not changes:
        return {
            "ok": True,
            "dry_run": dry_run,
            "path": str(audio),
            "painted": len(changes),
            "changes": changes,
            "database_backup": None,
        }
    backup = None
    if create_backup:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = f"{db}.backup.{ts}.music-sorter-fill-color"
        shutil.copy2(db, backup)
    with vdj_db_write():
        rewrite_song_xml_in_database(db, path_in_db, new_song, validate=False)
    return {
        "ok": True,
        "dry_run": False,
        "path": str(audio),
        "name": audio.name,
        "painted": len(changes),
        "changes": changes,
        "database_backup": backup,
    }


def set_poi_color(
    source_path: str | Path,
    *,
    kind: str,
    pos: float,
    color: str,
    num: str | None = None,
    name: str | None = None,
    slot: str | None = None,
    database_path: Path | None = None,
    dry_run: bool = False,
    allow_vdj_running: bool = False,
    create_backup: bool = False,
) -> dict[str, Any]:
    """Change Color on one cue or loop in VirtualDJ database.xml."""
    audio = _assert_allowed(Path(source_path))
    db = Path(database_path) if database_path else VDJ_DATABASE
    if not db.is_file():
        raise FileNotFoundError(f"VDJ database not found: {db}")

    if is_virtualdj_running() and not dry_run and not allow_vdj_running:
        raise RuntimeError(
            "VirtualDJ is running. Close it before changing cue colors, or pass "
            "allow_vdj_running=true (not recommended)."
        )

    before = summarize_cues(audio, db)
    if not before.in_database:
        raise KeyError(f"Track is not in VirtualDJ database: {audio}")

    content = read_vdj_database_text(db)
    path_in_db, start, end = _resolve_song_span(content, audio, source_path)
    song_xml = content[start:end]
    new_song, change = set_poi_color_in_song_xml(
        song_xml,
        kind=kind,
        pos=float(pos),
        color=color,
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
        backup = f"{db}.backup.{ts}.music-sorter-cue-color"
        shutil.copy2(db, backup)

    with vdj_db_write():
        rewrite_song_xml_in_database(db, path_in_db, new_song, validate=False)

    return {
        "ok": True,
        "dry_run": False,
        "path": str(audio),
        "name": audio.name,
        "change": change,
        "database_backup": backup,
        "loop_count": before.loop_count,
        "cue_count": before.cue_count,
    }


def _format_poi_pos(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    s = f"{seconds:.6f}".rstrip("0").rstrip(".")
    if "." not in s:
        s = f"{seconds:.1f}"
    return s


def _existing_cue_near(
    song_xml: str, pos: float, *, tolerance: float = POS_TOLERANCE
) -> Optional[dict[str, Any]]:
    for match in _POI_LINE_RE.finditer(song_xml):
        parsed = parse_manual_poi_tag(match.group(0))
        if parsed is None or parsed.get("kind") != "cue":
            continue
        try:
            existing = float(parsed.get("position"))
        except (TypeError, ValueError):
            continue
        if abs(existing - float(pos)) <= tolerance:
            return parsed
    return None


def _next_cue_num(song_xml: str) -> str:
    used: set[int] = set()
    for match in _POI_LINE_RE.finditer(song_xml):
        tag = match.group(0)
        parsed = parse_manual_poi_tag(tag)
        if parsed is None or parsed.get("kind") != "cue":
            continue
        try:
            num = int(str(parsed.get("num") or "0"))
        except ValueError:
            continue
        if 1 <= num <= 8:
            used.add(num)
    for candidate in range(1, 9):
        if candidate not in used:
            return str(candidate)
    raise ValueError("All 8 VirtualDJ cue slots are used — delete one first")


_NUM_ATTR_RE = re.compile(r'(Num=")([^"]*)(")', re.I)
_NAME_ATTR_RE = re.compile(r'(Name=")([^"]*)(")', re.I)


def _is_info_cue(parsed: dict[str, Any] | None) -> bool:
    if not parsed:
        return False
    name = str(parsed.get("name") or "")
    num = str(parsed.get("num") or "")
    return num == "0" or name.lower().startswith("info ")


def set_cue_jumpable_in_song_xml(
    song_xml: str,
    *,
    pos: float,
    jumpable: bool,
    num: str | None = None,
    name: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Flip a cue between pad jump (Num 1-8) and info-only (Num=0)."""
    attempts = [(num, name), (None, None)]
    last_error: Optional[KeyError] = None
    for try_num, try_name in attempts:
        changed: Optional[dict[str, Any]] = None

        def repl(match: re.Match[str]) -> str:
            nonlocal changed
            tag = match.group(0)
            if changed is not None:
                return tag
            if not _poi_matches(
                tag, kind="cue", pos=pos, num=try_num, name=try_name
            ):
                return tag
            parsed = parse_manual_poi_tag(tag) or {}
            old_num = str(parsed.get("num") or "")
            old_name = str(parsed.get("name") or "")
            if jumpable:
                new_num = old_num if old_num not in {"", "0"} else _next_cue_num(song_xml)
                new_name = old_name
                if new_name.lower().startswith("info "):
                    new_name = new_name[5:].strip() or "Cue"
                elif new_name.lower() == "info":
                    new_name = "Cue"
            else:
                new_num = "0"
                new_name = old_name
                if not new_name.lower().startswith("info"):
                    new_name = f"Info {new_name}".strip() if new_name else "Info"
            new_tag = tag
            if _NUM_ATTR_RE.search(new_tag):
                new_tag = _NUM_ATTR_RE.sub(
                    lambda m: f"{m.group(1)}{new_num}{m.group(3)}", new_tag, count=1
                )
            else:
                new_tag = re.sub(
                    r"<Poi\b",
                    f'<Poi Num="{new_num}"',
                    new_tag,
                    count=1,
                    flags=re.IGNORECASE,
                )
            if new_name and _NAME_ATTR_RE.search(new_tag):
                new_tag = _NAME_ATTR_RE.sub(
                    lambda m: f"{m.group(1)}{new_name}{m.group(3)}", new_tag, count=1
                )
            changed = {
                "kind": "cue",
                "pos": parsed.get("position"),
                "num_before": old_num,
                "num": new_num,
                "name_before": old_name,
                "name": new_name,
                "jumpable": bool(jumpable),
            }
            return new_tag

        new_xml = _POI_LINE_RE.sub(repl, song_xml)
        if changed is not None:
            return new_xml, changed
        last_error = KeyError(f"No matching cue at pos≈{pos}")
    raise last_error or KeyError(f"No matching cue at pos≈{pos}")


def set_cue_jumpable(
    source_path: str | Path,
    *,
    pos: float,
    jumpable: bool,
    num: str | None = None,
    name: str | None = None,
    database_path: Path | None = None,
    dry_run: bool = False,
    allow_vdj_running: bool = False,
    create_backup: bool = False,
) -> dict[str, Any]:
    audio = _assert_allowed(Path(source_path))
    db = Path(database_path) if database_path else VDJ_DATABASE
    if not db.is_file():
        raise FileNotFoundError(f"VDJ database not found: {db}")
    if is_virtualdj_running() and not dry_run and not allow_vdj_running:
        raise RuntimeError(
            "VirtualDJ is running. Close it before changing cue jump/info, or pass allow_vdj_running=true."
        )
    content = read_vdj_database_text(db)
    path_in_db, start, end = _resolve_song_span(content, audio, source_path)
    new_song, change = set_cue_jumpable_in_song_xml(
        content[start:end], pos=float(pos), jumpable=bool(jumpable), num=num, name=name
    )
    if dry_run:
        return {"ok": True, "dry_run": True, "path": str(audio), "change": change}
    backup = None
    if create_backup:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = f"{db}.backup.{ts}.music-sorter-jumpable"
        shutil.copy2(db, backup)
    with vdj_db_write():
        rewrite_song_xml_in_database(db, path_in_db, new_song, validate=False)
    after = summarize_cues(audio, db)
    _schedule_ml_after_cue_change(audio, after)
    return {
        "ok": True,
        "dry_run": False,
        "path": str(audio),
        "change": change,
        "cues": after.to_dict(),
        "database_backup": backup,
    }


def add_cue_poi_in_song_xml(
    song_xml: str,
    *,
    pos: float,
    name: str | None = None,
    color: str = "green",
) -> tuple[str, dict[str, Any]]:
    """Insert one manual cue POI. Does not strip existing markers."""
    if pos < 0:
        raise ValueError("pos must be >= 0")
    occupied = _existing_cue_near(song_xml, float(pos))
    if occupied is not None:
        raise ValueError(
            f"A cue already sits at {float(occupied.get('position') or pos):.3f}s "
            f"({occupied.get('name') or 'Cue'})"
        )
    num = _next_cue_num(song_xml)
    color_name, color_raw = normalize_cue_color(color)
    label = (name or f"Cue {num}").strip() or f"Cue {num}"
    label = label.replace("\n", " ").replace("\r", " ")[:120]
    newline = "\r\n" if "\r\n" in song_xml else "\n"
    line = format_vdj_poi_line(
        pos=float(pos),
        poi_type="cue",
        num=num,
        color=color_raw,
        name=label,
        newline=newline,
    )
    close_idx = song_xml.rfind("</Song>")
    if close_idx < 0:
        raise ValueError("Song XML is missing </Song>")
    body = song_xml[:close_idx].rstrip(" \t")
    if not body.endswith("\n"):
        body += newline
    out = body + line + song_xml[close_idx:]
    return out, {
        "kind": "cue",
        "name": label,
        "num": num,
        "pos": float(pos),
        "color_name": color_name,
    }


def add_cue_point(
    source_path: str | Path,
    *,
    pos: float,
    name: str | None = None,
    color: str = "green",
    database_path: Path | None = None,
    dry_run: bool = False,
    allow_vdj_running: bool = False,
    create_backup: bool = True,
) -> dict[str, Any]:
    """Add one cue at pos (seconds) in VirtualDJ for this audio file."""
    audio = _assert_allowed(Path(source_path))
    db = Path(database_path) if database_path else VDJ_DATABASE
    if not db.is_file():
        raise FileNotFoundError(f"VDJ database not found: {db}")

    if is_virtualdj_running() and not dry_run and not allow_vdj_running:
        raise RuntimeError(
            "VirtualDJ is running. Close it before adding a cue, or pass "
            "allow_vdj_running=true (not recommended)."
        )

    before = summarize_cues(audio, db)
    if not before.in_database:
        raise KeyError(f"Track is not in VirtualDJ database: {audio}")

    if dry_run:
        content = read_vdj_database_text(db)
        _path_in_db, start, end = _resolve_song_span(content, audio, source_path)
        _new_song, change = add_cue_poi_in_song_xml(
            content[start:end], pos=float(pos), name=name, color=color
        )
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
        backup = f"{db}.backup.{ts}.music-sorter-cue-add"
        shutil.copy2(db, backup)

    with vdj_db_write():
        content = read_vdj_database_text(db)
        path_in_db, start, end = _resolve_song_span(content, audio, source_path)
        new_song, change = add_cue_poi_in_song_xml(
            content[start:end], pos=float(pos), name=name, color=color
        )
        rewrite_song_xml_in_database(db, path_in_db, new_song, validate=True)
    after = summarize_cues(audio, db)
    _schedule_ml_after_cue_change(audio, after)

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


DEFAULT_LOOP_BEATS = 8.0


def _existing_loop_near(
    song_xml: str, pos: float, *, tolerance: float = POS_TOLERANCE
) -> Optional[dict[str, Any]]:
    for match in _POI_LINE_RE.finditer(song_xml):
        parsed = parse_manual_poi_tag(match.group(0))
        if parsed is None or parsed.get("kind") != "loop":
            continue
        try:
            existing = float(parsed.get("position"))
        except (TypeError, ValueError):
            continue
        if abs(existing - float(pos)) <= tolerance:
            return parsed
    return None


def _next_loop_slot(song_xml: str) -> str:
    used: set[int] = set()
    for match in _POI_LINE_RE.finditer(song_xml):
        tag = match.group(0)
        parsed = parse_manual_poi_tag(tag)
        if parsed is None or parsed.get("kind") != "loop":
            continue
        raw = _poi_attr(tag, "Slot")
        if raw is None:
            continue
        try:
            slot = int(str(raw))
        except ValueError:
            continue
        if 1 <= slot <= 8:
            used.add(slot)
    for candidate in range(1, 9):
        if candidate not in used:
            return str(candidate)
    raise ValueError("All 8 VirtualDJ loop slots are used — delete one first")


def add_loop_poi_in_song_xml(
    song_xml: str,
    *,
    pos: float,
    name: str | None = None,
    color: str = "green",
    beats: float = DEFAULT_LOOP_BEATS,
) -> tuple[str, dict[str, Any]]:
    """Insert one manual loop POI. Does not strip existing markers."""
    if pos < 0:
        raise ValueError("pos must be >= 0")
    occupied = _existing_loop_near(song_xml, float(pos))
    if occupied is not None:
        raise ValueError(
            f"A loop already starts at {float(occupied.get('position') or pos):.3f}s "
            f"({occupied.get('name') or 'Loop'})"
        )
    length = max(MIN_LOOP_BEATS, min(MAX_LOOP_BEATS, float(beats)))
    if abs(length - round(length)) < 0.05:
        length = float(round(length))
    slot = _next_loop_slot(song_xml)
    color_name, color_raw = normalize_cue_color(color)
    label = (name or f"Loop {slot}").strip() or f"Loop {slot}"
    label = label.replace("\n", " ").replace("\r", " ")[:120]
    newline = "\r\n" if "\r\n" in song_xml else "\n"
    line = format_vdj_poi_line(
        pos=float(pos),
        poi_type="loop",
        num="-1",
        color=color_raw,
        name=label,
        size=_format_loop_size(length),
        slot=slot,
        newline=newline,
    )
    close_idx = song_xml.rfind("</Song>")
    if close_idx < 0:
        raise ValueError("Song XML is missing </Song>")
    body = song_xml[:close_idx].rstrip(" \t")
    if not body.endswith("\n"):
        body += newline
    out = body + line + song_xml[close_idx:]
    return out, {
        "kind": "loop",
        "name": label,
        "num": "-1",
        "slot": slot,
        "pos": float(pos),
        "beats": length,
        "color_name": color_name,
    }


def add_loop_point(
    source_path: str | Path,
    *,
    pos: float,
    name: str | None = None,
    color: str = "green",
    beats: float = DEFAULT_LOOP_BEATS,
    database_path: Path | None = None,
    dry_run: bool = False,
    allow_vdj_running: bool = False,
    create_backup: bool = True,
) -> dict[str, Any]:
    """Add one loop at pos (seconds) in VirtualDJ for this audio file."""
    audio = _assert_allowed(Path(source_path))
    db = Path(database_path) if database_path else VDJ_DATABASE
    if not db.is_file():
        raise FileNotFoundError(f"VDJ database not found: {db}")

    if is_virtualdj_running() and not dry_run and not allow_vdj_running:
        raise RuntimeError(
            "VirtualDJ is running. Close it before adding a loop, or pass "
            "allow_vdj_running=true (not recommended)."
        )

    before = summarize_cues(audio, db)
    if not before.in_database:
        raise KeyError(f"Track is not in VirtualDJ database: {audio}")

    if dry_run:
        content = read_vdj_database_text(db)
        _path_in_db, start, end = _resolve_song_span(content, audio, source_path)
        _new_song, change = add_loop_poi_in_song_xml(
            content[start:end], pos=float(pos), name=name, color=color, beats=beats
        )
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
        backup = f"{db}.backup.{ts}.music-sorter-loop-add"
        shutil.copy2(db, backup)

    with vdj_db_write():
        content = read_vdj_database_text(db)
        path_in_db, start, end = _resolve_song_span(content, audio, source_path)
        new_song, change = add_loop_poi_in_song_xml(
            content[start:end], pos=float(pos), name=name, color=color, beats=beats
        )
        rewrite_song_xml_in_database(db, path_in_db, new_song, validate=True)
    after = summarize_cues(audio, db)
    _schedule_ml_after_cue_change(audio, after)

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


def set_poi_position_in_song_xml(
    song_xml: str,
    *,
    kind: str,
    pos: float,
    new_pos: float,
    num: str | None = None,
    name: str | None = None,
    slot: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Move a cue/loop POI to new_pos (seconds). Returns (xml, change_info)."""
    kind = "loop" if str(kind).lower() == "loop" else "cue"
    if new_pos < 0:
        raise ValueError("new_pos must be >= 0")
    new_s = _format_poi_pos(float(new_pos))

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
            if not _POS_ATTR_RE.search(tag):
                return tag
            old_pos = _poi_attr(tag, "Pos")
            new_tag = _POS_ATTR_RE.sub(
                lambda m: f"{m.group(1)}{new_s}{m.group(3)}", tag, count=1
            )
            parsed = parse_manual_poi_tag(tag) or {}
            changed = {
                "kind": kind,
                "name": parsed.get("name"),
                "num": parsed.get("num"),
                "slot": _poi_attr(tag, "Slot"),
                "size": _poi_attr(tag, "Size"),
                "pos_before": float(old_pos) if old_pos else pos,
                "pos_after": float(new_pos),
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


def set_poi_position(
    source_path: str | Path,
    *,
    kind: str,
    pos: float,
    new_pos: float,
    num: str | None = None,
    name: str | None = None,
    slot: str | None = None,
    database_path: Path | None = None,
    dry_run: bool = False,
    allow_vdj_running: bool = False,
    create_backup: bool = True,
) -> dict[str, Any]:
    """Move one cue or loop to a new time position in VirtualDJ."""
    audio = _assert_allowed(Path(source_path))
    db = Path(database_path) if database_path else VDJ_DATABASE
    if not db.is_file():
        raise FileNotFoundError(f"VDJ database not found: {db}")

    if is_virtualdj_running() and not dry_run and not allow_vdj_running:
        raise RuntimeError(
            "VirtualDJ is running. Close it before moving markers, or pass "
            "allow_vdj_running=true (not recommended)."
        )

    before = summarize_cues(audio, db)
    if not before.in_database:
        raise KeyError(f"Track is not in VirtualDJ database: {audio}")

    content = read_vdj_database_text(db)
    path_in_db, start, end = _resolve_song_span(content, audio, source_path)
    song_xml = content[start:end]
    new_song, change = set_poi_position_in_song_xml(
        song_xml,
        kind=kind,
        pos=float(pos),
        new_pos=float(new_pos),
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
        backup = f"{db}.backup.{ts}.music-sorter-poi-move"
        shutil.copy2(db, backup)

    with vdj_db_write():
        rewrite_song_xml_in_database(db, path_in_db, new_song, validate=True)
    after = summarize_cues(audio, db)
    _schedule_ml_after_cue_change(audio, after)

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
    create_backup: bool = False,
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
    path_in_db, start, end = _resolve_song_span(content, audio, source_path)
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

    with vdj_db_write():
        rewrite_song_xml_in_database(db, path_in_db, new_song, validate=False)
    after = summarize_cues(audio, db)
    _schedule_ml_after_cue_change(audio, after)
    after_cues = after.cue_count
    after_loops = after.loop_count
    return {
        "ok": True,
        "dry_run": False,
        "path": str(audio),
        "name": audio.name,
        "removed": removed,
        "cue_count_before": before.cue_count,
        "loop_count_before": before.loop_count,
        "cue_count_after": after_cues,
        "loop_count_after": after_loops,
        "database_backup": backup,
    }
