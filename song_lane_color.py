"""Map Zouk folder lanes onto VirtualDJ song-name UserColor.

Color on the title is Infos UserColor, not cue Color. VirtualDJ has no
view-setting for this — it has to be written onto songs. Classification
uses every copy of a track; writes default to cued songs only.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from vdj_database_safety import (
    _lightweight_content_stats,
    _lightweight_rewrite_stats,
    atomic_replace_database,
    normalize_database_path,
    read_vdj_database_text,
)


# Standard VirtualDJ ARGB ints (alpha 0xFF).
LANE_COLORS = {
    "blue": "4278190335",  # #0000ff  chill / beautiful
    "cyan": "4278255615",  # #00ffff  trancy / float
    "green": "4278255360",  # #00ff00  energy body
    "yellow": "4294967040",  # #ffff00  intense / peak
    "orange": "4294934272",  # #ff7f00  lamba / afro / world
    "magenta": "4294902015",  # #ff00ff  kizouk / neo / sensual
    "pink": "4294941081",  # #ff9999  r&b / hip-hop / urban vocal
    "red": "4294901760",  # #ff0000  remix / nostalgia / classics
}

# Cued, but no vibe/type folder yet. White so it is obvious and not a lane.
PENDING_LANE = "pending"
PENDING_COLOR = "4294967295"  # #ffffff

# Lower number wins when a song lives in more than one lane folder.
LANE_PRIORITY = {
    "pink": 0,
    "magenta": 1,
    "orange": 2,
    "red": 3,
    "cyan": 4,
    "yellow": 5,
    "green": 6,
    "blue": 7,
}

FOLDER_LANES = {
    "r&b": "pink",
    "rnb": "pink",
    "jr&b": "pink",
    "hip hoppy": "pink",
    "hiphop": "pink",
    "hip hop": "pink",
    "gouyad": "pink",
    "gouyad_kompa": "pink",
    "kiz": "pink",
    "kizme": "pink",
    "neo soul": "pink",
    "trappy": "pink",
    "reggatonish": "pink",
    "kizouk": "magenta",
    "neo zouk": "magenta",
    "neozouk": "magenta",
    "lamba": "orange",
    "tribal": "orange",
    "world": "orange",
    "middle east": "orange",
    "india": "orange",
    "asian": "orange",
    "foreign": "orange",
    "reggae": "orange",
    "brazilian": "orange",
    "brazillian": "orange",
    "brazilian matter": "orange",
    "remixes": "red",
    "classics": "red",
    "nostalgia": "red",
    "pop": "red",
    "kpop": "red",
    "trancy": "cyan",
    "trippy": "cyan",
    "trippy party": "cyan",
    "experimental": "cyan",
    "intense": "yellow",
    "energy": "green",
    "groovy": "green",
    "chill": "blue",
    "beautiful sound": "blue",
    "beautiful": "blue",
    "longing": "blue",
    "mellow": "blue",
    "lounge": "blue",
    "jazzy": "blue",
}

IGNORE_FOLDERS = {
    "sets",
    "cues",
    "cues sorted",
    "cues backup",
    "cuedbackup",
    "cuedbackup (no comments)",
    "cued and sorted",
    "cues 2",
    "mmm",
    "z4f",
    "z4",
    "favs",
    "gotos",
    "closers",
    "openers",
    "transitions",
    "planned transitions",
    "not sorted",
    "stefan folders",
    "stefan gems",
    "low_quality_backups",
    "all chill",
    "all energy",
    "start",
    "claude_scripts",
    "__pycache__",
    "ready for sort",
    "add cues",
    "no cues found",
    "ac low quality",
    "low quality skip",
    "pajamathon 2026",
    "silesian",
    "silesian planned transitions",
    "silesian vibe match",
    "goth",
    "tokyo",
    "zm",
    "music meeting of the minds",
}

_ANCHOR_FOLDERS = {
    "zouk",
    "cues sorted",
    "cuessorted",
    "sets",
    "kiz",
    "zoukadelic 2 sorted",
}

_LEGEND_COLOR_VALUES = frozenset(LANE_COLORS.values()) | {PENDING_COLOR}

AUDIO_EXTENSIONS = {".mp3", ".flac", ".m4a", ".wav", ".aiff", ".aif", ".ogg", ".opus"}

_FILEPATH_RE = re.compile(r'\bFilePath\s*=\s*"([^"]*)"', re.IGNORECASE)
_USERCOLOR_RE = re.compile(r'\bUserColor\s*=\s*"([^"]*)"', re.IGNORECASE)
_INFOS_RE = re.compile(r"<Infos\b[^>]*>")
_MANUAL_CUE_RE = re.compile(r'\bType\s*=\s*"(?:cue|loop)"', re.IGNORECASE)
_CONFLICTED_RE = re.compile(r"\s*\(conflicted\)", re.IGNORECASE)
_SET_INDEX_RE = re.compile(r"^\d{1,3}\.\s+")
_USER2_RE = re.compile(r'\bUser2\s*=\s*"([^"]*)"', re.IGNORECASE)


def _unescape_xml_attr(value: str) -> str:
    return (
        value.replace("&quot;", '"')
        .replace("&apos;", "'")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&amp;", "&")
    )


def _norm_folder(name: str) -> str:
    return unicodedata.normalize("NFC", name).strip().lower()


def _folder_parts(file_path: str) -> list[str]:
    return [_norm_folder(part) for part in Path(file_path.replace("\\", "/")).parts]


def _is_excluded_tree(folders: list[str]) -> bool:
    """Backup / copy / staging trees must not classify or receive colors."""
    for folder in folders:
        if folder in {"low_quality_backups", "cuedbackup"}:
            return True
        if folder.startswith("cuedbackup"):
            return True
        if "backup" in folder:
            return True
        if folder != "cues sorted" and folder.startswith("cues sorted"):
            return True
        if folder.endswith(" copy") or " copy " in f" {folder} ":
            return True
    return False


def path_in_scope(file_path: str) -> bool:
    """Live Zouk / Kiz / Sets / Cues Sorted trees only (not backups or House)."""
    parts = _folder_parts(file_path)
    if not parts:
        return False
    folders = parts[:-1]
    if _is_excluded_tree(folders):
        return False
    for index, folder in enumerate(folders):
        parent = folders[index - 1] if index else ""
        if folder == "zoukadelic 2 sorted":
            return True
        if folder == "cuessorted":
            return True
        if folder == "cues sorted" and parent == "cues":
            return True
        if folder == "zouk" and parent == "music":
            return True
        if folder == "kiz" and parent == "music":
            return True
        if folder == "sets" and parent == "music":
            return True
    return False


_PLACEMENT_ROOTS = _ANCHOR_FOLDERS | {"house"}


def classify_placement_path(file_path: str) -> Optional[str]:
    """Lane from a just-sorted destination (House, Zouk, or Cues Sorted)."""
    parts = _folder_parts(file_path)
    if not parts:
        return None
    folders = parts[:-1]
    if "sets" in folders:
        return None
    start = 0
    for index, folder in enumerate(folders):
        if folder in _PLACEMENT_ROOTS:
            start = index + 1
            break
    for folder in folders[start:]:
        if folder in IGNORE_FOLDERS or folder in _PLACEMENT_ROOTS:
            continue
        lane = FOLDER_LANES.get(folder)
        if lane:
            return lane
    return None


def classify_placements(paths: Iterable[str]) -> str:
    """Lane for a sort move. Pending if the dest folder is not a type."""
    lanes = {classify_placement_path(path) for path in paths}
    lanes.discard(None)
    if not lanes:
        return PENDING_LANE
    return min(lanes, key=lambda lane: LANE_PRIORITY[lane])


def color_for_lane(lane: str) -> str:
    if lane == PENDING_LANE:
        return PENDING_COLOR
    return LANE_COLORS[lane]


def classify_path(file_path: str) -> Optional[str]:
    """Lane for a single path. Parent crate wins over mood subfolders."""
    if not path_in_scope(file_path):
        return None
    parts = [_norm_folder(part) for part in Path(file_path).parts]
    if not parts:
        return None
    folders = parts[:-1]
    # Event setlists are mixed — never read a lane from under Sets.
    if "sets" in folders:
        return None
    start = 0
    for index, folder in enumerate(folders):
        if folder in _ANCHOR_FOLDERS:
            start = index + 1
            break
    for folder in folders[start:]:
        if folder in IGNORE_FOLDERS or folder in _ANCHOR_FOLDERS:
            continue
        lane = FOLDER_LANES.get(folder)
        if lane:
            return lane
    return None


def classify_user2(user2: str) -> Optional[str]:
    """Lane from VirtualDJ Directory Sort / User2 (e.g. Chill/Shaman)."""
    if not user2:
        return None
    text = _unescape_xml_attr(user2)
    for part in text.replace("\\", "/").split("/"):
        folder = _norm_folder(part)
        if folder in IGNORE_FOLDERS or folder in _ANCHOR_FOLDERS:
            continue
        lane = FOLDER_LANES.get(folder)
        if lane:
            return lane
    return None


def classify_song(
    paths: Iterable[str],
    user2_values: Iterable[str] = (),
) -> Optional[str]:
    """Best lane across copies. Folder paths beat Directory Sort labels."""
    lanes = {classify_path(path) for path in paths}
    lanes.discard(None)
    if lanes:
        return min(lanes, key=lambda lane: LANE_PRIORITY[lane])
    extra = {classify_user2(value) for value in user2_values}
    extra.discard(None)
    if not extra:
        return None
    return min(extra, key=lambda lane: LANE_PRIORITY[lane])


def song_identity(file_path: str) -> Optional[str]:
    path = Path(file_path)
    suffixes = [suffix.lower() for suffix in path.suffixes]
    if suffixes[-2:] == [".flac", ".vdjstems"] or path.suffix.lower() == ".vdjstems":
        return None
    if path.suffix.lower() not in AUDIO_EXTENSIONS:
        return None
    name = unicodedata.normalize("NFC", path.name).lower()
    name = _CONFLICTED_RE.sub("", name)
    for _ in range(2):
        stripped = _SET_INDEX_RE.sub("", name)
        if stripped == name:
            break
        name = stripped
    return name


def song_has_manual_cues(song_xml: str) -> bool:
    return _MANUAL_CUE_RE.search(song_xml) is not None


def current_user_color(xml: str) -> Optional[str]:
    match = _USERCOLOR_RE.search(xml)
    return match.group(1) if match else None


def apply_user_color_to_infos(xml: str, color_value: Optional[str]) -> str:
    """Set or remove Infos UserColor. Leaves cue Color attributes alone."""

    if _INFOS_RE.search(xml) is None:
        if not color_value:
            return xml
        newline = "\r\n" if "\r\n" in xml else "\n"
        infos = f'  <Infos UserColor="{color_value}" />{newline}'
        tags_close = xml.find("</Tags>")
        if tags_close >= 0:
            insert_at = tags_close + len("</Tags>")
            if xml.startswith(newline, insert_at):
                insert_at += len(newline)
            elif insert_at < len(xml) and xml[insert_at] == "\n":
                insert_at += 1
            return xml[:insert_at] + infos + xml[insert_at:]
        song_gt = xml.find(">")
        if song_gt >= 0:
            insert_at = song_gt + 1
            if xml.startswith(newline, insert_at):
                insert_at += len(newline)
            return xml[:insert_at] + infos + xml[insert_at:]
        return xml

    def replace_infos(match: re.Match[str]) -> str:
        tag = _USERCOLOR_RE.sub("", match.group(0))
        tag = re.sub(r"\s{2,}", " ", tag)
        if not color_value:
            return tag
        if re.search(r"\bCover\s*=", tag):
            return re.sub(
                r"(\bCover\s*=)",
                f'UserColor="{color_value}" \\1',
                tag,
                count=1,
            )
        return re.sub(
            r"(\s*)(/?>)$",
            f' UserColor="{color_value}"\\1\\2',
            tag,
            count=1,
        )

    return _INFOS_RE.sub(replace_infos, xml, count=1)


def iter_song_spans(content: str) -> Iterable[tuple[int, int]]:
    pos = 0
    while True:
        start = content.find("<Song", pos)
        if start < 0:
            return
        end = content.find("</Song>", start)
        if end < 0:
            return
        end += len("</Song>")
        yield start, end
        pos = end


def _song_path(song_xml: str) -> Optional[str]:
    match = _FILEPATH_RE.search(song_xml)
    if match is None:
        return None
    return _unescape_xml_attr(match.group(1))


@dataclass(frozen=True)
class ColorUpdate:
    path: str
    lane: Optional[str]
    color_value: Optional[str]
    previous: Optional[str]


def plan_color_updates(content: str, cued_only: bool = True) -> list[ColorUpdate]:
    """Plan Infos UserColor writes. Same identity shares one lane."""
    grouped: dict[str, list[tuple[str, str, bool, Optional[str], str]]] = {}
    ungrouped: list[tuple[str, str, bool, Optional[str], str]] = []
    for start, end in iter_song_spans(content):
        song_xml = content[start:end]
        path = _song_path(song_xml)
        if not path:
            continue
        identity = song_identity(path)
        cued = song_has_manual_cues(song_xml)
        previous = current_user_color(song_xml)
        user2_match = _USER2_RE.search(song_xml)
        user2 = _unescape_xml_attr(user2_match.group(1)) if user2_match else ""
        record = (path, song_xml, cued, previous, user2)
        if identity is None:
            ungrouped.append(record)
            continue
        grouped.setdefault(identity, []).append(record)

    updates: list[ColorUpdate] = []

    def consider(
        records: list[tuple[str, str, bool, Optional[str], str]],
    ) -> None:
        paths = [record[0] for record in records]
        if not any(path_in_scope(path) for path in paths):
            return
        user2_values = [record[4] for record in records if record[4]]
        lane = classify_song(paths, user2_values)
        if lane is None:
            lane = PENDING_LANE
            color_value = PENDING_COLOR
        else:
            color_value = LANE_COLORS[lane]
        for path, _xml, cued, previous, _user2 in records:
            if cued_only and not cued:
                continue
            if not path_in_scope(path):
                if (
                    lane
                    and previous in _LEGEND_COLOR_VALUES
                    and previous is not None
                ):
                    updates.append(
                        ColorUpdate(
                            path=path,
                            lane=None,
                            color_value=None,
                            previous=previous,
                        )
                    )
                continue
            if previous == color_value:
                continue
            updates.append(
                ColorUpdate(
                    path=path,
                    lane=lane,
                    color_value=color_value,
                    previous=previous,
                )
            )

    for records in grouped.values():
        consider(records)
    for record in ungrouped:
        consider([record])
    return updates


def apply_plan_to_content(
    content: str, plan: list[ColorUpdate]
) -> tuple[str, dict[str, int]]:
    wanted = {item.path: item for item in plan}
    pieces: list[str] = []
    cursor = 0
    updated = 0
    for start, end in iter_song_spans(content):
        pieces.append(content[cursor:start])
        song_xml = content[start:end]
        path = _song_path(song_xml)
        item = wanted.get(path) if path else None
        if item is None:
            pieces.append(song_xml)
        else:
            pieces.append(apply_user_color_to_infos(song_xml, item.color_value))
            updated += 1
        cursor = end
    pieces.append(content[cursor:])
    return "".join(pieces), {
        "updated": updated,
        "planned": len(plan),
        "song_count": content.count("<Song"),
    }


def apply_lane_color_after_move(
    database_path: Path | str,
    dest_paths: Iterable[str | Path],
) -> dict[str, object]:
    """Paint just-sorted destinations from the folders they landed in."""
    targets = [
        normalize_database_path(str(Path(path)))
        for path in dest_paths
        if path
    ]
    if not targets:
        return {"lane": None, "color": None, "updated": 0}
    lane = classify_placements(targets)
    color = color_for_lane(lane)
    wanted = set(targets)
    db_path = Path(database_path)
    content = read_vdj_database_text(db_path)
    plan: list[ColorUpdate] = []
    for start, end in iter_song_spans(content):
        song_xml = content[start:end]
        song_path = _song_path(song_xml)
        if not song_path:
            continue
        if normalize_database_path(song_path) not in wanted:
            continue
        previous = current_user_color(song_xml)
        if previous == color:
            continue
        plan.append(
            ColorUpdate(
                path=song_path,
                lane=lane,
                color_value=color,
                previous=previous,
            )
        )
    if not plan:
        return {"lane": lane, "color": color, "updated": 0}
    original_stats = _lightweight_rewrite_stats(content, db_path)
    new_content, stats = apply_plan_to_content(content, plan)
    atomic_replace_database(
        db_path,
        new_content,
        original_stats,
        stats_fn=_lightweight_content_stats,
    )
    return {"lane": lane, "color": color, **stats}


def rewrite_database_user_colors(
    database_path: Path | str,
    cued_only: bool = True,
) -> dict[str, int]:
    """Write planned UserColor values. Requires VirtualDJ to be closed."""
    path = Path(database_path)
    content = read_vdj_database_text(path)
    original_stats = _lightweight_rewrite_stats(content, path)
    plan = plan_color_updates(content, cued_only=cued_only)
    new_content, stats = apply_plan_to_content(content, plan)
    atomic_replace_database(
        path,
        new_content,
        original_stats,
        stats_fn=_lightweight_content_stats,
    )
    return stats


def summarize_plan(plan: list[ColorUpdate]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in plan:
        key = item.lane or "clear"
        counts[key] = counts.get(key, 0) + 1
    return counts
