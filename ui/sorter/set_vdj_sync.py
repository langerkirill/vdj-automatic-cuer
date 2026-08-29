"""Keep Sets/Pajamathon VDJ rows in sync with disk.

Remove drops the FilePath from database.xml, not just the audio.
Whites get UserColor only from a mapped library sibling that already
has a real 8-lane color — never from folder-name guesses.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from .autocue_path import ensure_autocue_on_path
from .config import SETS_ROOT, VDJ_DATABASE
from .db_lock import vdj_db_write
from .lanes import LANE_USER_COLORS, lane_from_user_color
from .library import (
    cached_placement_indexes,
    find_library_matches,
    is_pajamathon_event,
    is_pajamathon_set_audio,
)
from .relocate import (
    backup_database,
    is_virtualdj_running,
    summarize_cues,
)

ensure_autocue_on_path()

from song_lane_color import apply_user_color_to_infos, current_user_color, iter_song_spans  # noqa: E402
from vdj_database_safety import (  # noqa: E402
    _FILEPATH_RE,
    _lightweight_content_stats,
    _lightweight_rewrite_stats,
    _unescape_xml_attr,
    atomic_replace_database_parts,
    normalize_database_path,
    read_vdj_database_text,
)

LANE_TO_USER_COLOR = {lane: color for color, lane in LANE_USER_COLORS.items()}


def _song_filepath(song_xml: str) -> Optional[str]:
    match = _FILEPATH_RE.search(song_xml)
    if match is None:
        return None
    return normalize_database_path(_unescape_xml_attr(match.group(1)))


def _is_pajamathon_set_filepath(path: str | None) -> bool:
    if not path:
        return False
    raw = Path(path)
    try:
        rel = raw.expanduser().relative_to(SETS_ROOT.expanduser().resolve())
    except ValueError:
        try:
            rel = raw.relative_to(SETS_ROOT)
        except ValueError:
            return False
    return bool(rel.parts) and is_pajamathon_event(rel.parts[0])


def _swallow_trailing_newline(content: str, end: int) -> int:
    if end < len(content) and content.startswith("\r\n", end):
        return end + 2
    if end < len(content) and content[end] == "\n":
        return end + 1
    return end


def _sibling_lane_color(filename: str, placement_index) -> tuple[Optional[str], int]:
    """(UserColor, lane_count) from mapped crate siblings. count 1 is safe."""
    lanes: set[str] = set()
    colors: dict[str, str] = {}
    for hit in find_library_matches(filename, index=placement_index):
        path = hit.get("path") or ""
        if not path or not Path(path).is_file():
            continue
        if is_pajamathon_set_audio(path):
            continue
        summary = summarize_cues(path)
        lane = lane_from_user_color(getattr(summary, "user_color", "") or "")
        if not lane:
            continue
        lanes.add(lane)
        colors[lane] = LANE_TO_USER_COLOR[lane]
    if len(lanes) != 1:
        return None, len(lanes)
    lane = next(iter(lanes))
    return colors[lane], 1


def sync_pajamathon_vdj(
    *,
    database_path: Path | None = None,
    dry_run: bool = False,
    allow_vdj_running: bool = False,
    paint: bool = True,
    create_backup: bool = True,
) -> dict[str, Any]:
    """Drop stale Sets/Pajamathon FilePaths; paint whites from sibling crates."""
    if is_virtualdj_running() and not dry_run and not allow_vdj_running:
        raise RuntimeError(
            "VirtualDJ is running. Close it before rewriting database rows."
        )

    db = Path(database_path) if database_path else VDJ_DATABASE
    content = read_vdj_database_text(db)
    placement_index, _set_index = cached_placement_indexes()

    out: list[str] = []
    pos = 0
    dropped = 0
    dropped_cues = 0
    painted = 0
    skipped_multi = 0
    skipped_no_sib = 0
    dropped_paths: list[str] = []
    painted_paths: list[str] = []

    for start, end in iter_song_spans(content):
        song = content[start:end]
        filepath = _song_filepath(song)
        if not _is_pajamathon_set_filepath(filepath):
            continue
        audio = Path(filepath).expanduser()
        if not audio.is_file():
            out.append(content[pos:start])
            pos = _swallow_trailing_newline(content, end)
            dropped += 1
            dropped_cues += song.count('Type="cue"') + song.count("Type='cue'")
            dropped_cues += song.count('Type="loop"') + song.count("Type='loop'")
            dropped_paths.append(str(audio))
            continue
        if not paint:
            continue
        existing = current_user_color(song) or ""
        if lane_from_user_color(existing):
            continue
        color, lane_n = _sibling_lane_color(audio.name, placement_index)
        if color is None:
            if lane_n > 1:
                skipped_multi += 1
            else:
                skipped_no_sib += 1
            continue
        new_song = apply_user_color_to_infos(song, color)
        if new_song == song:
            skipped_no_sib += 1
            continue
        out.append(content[pos:start])
        out.append(new_song)
        pos = end
        painted += 1
        painted_paths.append(str(audio))

    out.append(content[pos:])
    new_content = "".join(out)

    result: dict[str, Any] = {
        "ok": True,
        "dry_run": dry_run,
        "dropped": dropped,
        "painted": painted,
        "skipped_conflict": skipped_multi,
        "skipped_no_sibling": skipped_no_sib,
        "dropped_paths": dropped_paths[:20],
        "painted_paths": painted_paths[:20],
        "database_backup": None,
    }
    if dry_run or (dropped == 0 and painted == 0):
        return result

    original_stats = _lightweight_rewrite_stats(content, db)
    removed_bytes = max(0, len(content.encode("utf-8")) - len(new_content.encode("utf-8")))
    adjusted = {
        "size_bytes": max(0, int(original_stats["size_bytes"]) - removed_bytes),
        "song_count": max(0, int(original_stats["song_count"]) - dropped),
        "cue_loop_count": max(0, int(original_stats["cue_loop_count"]) - dropped_cues),
    }
    backup = backup_database(db) if create_backup else None
    with vdj_db_write():
        atomic_replace_database_parts(
            db,
            (new_content,),
            original_stats=adjusted,
            stats_fn=_lightweight_content_stats,
        )
    result["database_backup"] = backup
    return result
