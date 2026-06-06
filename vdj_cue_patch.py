#!/usr/bin/env python3
"""Apply curated VirtualDJ cue/loop corrections with database safety checks."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List

from vdj_database_safety import (
    MANUAL_CUE_TYPES,
    VDJ_DATABASE_ROOT,
    database_integrity_stats,
    serialize_vdj_database,
    validate_database_replacement,
)


DEFAULT_DATABASE = (
    Path.home() / "Library" / "Application Support" / "VirtualDJ" / "database.xml"
)
DEFAULT_PATCH = (
    Path(__file__).resolve().parent
    / "cue_fixes"
    / "screenshots_6_6_26_manual.json"
)

COLOR_NAMES = {
    "4278190335": "blue",
    "4278255360": "green",
    "4288020735": "purple",
    "4294967040": "yellow",
    "4294934272": "orange",
}
POI_COMPARE_KEYS = ("Type", "Name", "Pos", "Num", "Color", "Size", "Slot")


@dataclass
class TrackPatchResult:
    path: str
    title: str
    removed: int
    added: int
    comment: str


def is_virtualdj_running() -> bool:
    result = subprocess.run(
        ["pgrep", "-fl", "VirtualDJ|virtualdj"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and any(
        "virtualdj" in line.lower() for line in result.stdout.splitlines()
    )


def load_patch(path: os.PathLike | str) -> Dict[str, Any]:
    patch_path = Path(path)
    with patch_path.open("r", encoding="utf-8") as handle:
        patch = json.load(handle)

    tracks = patch.get("tracks")
    if not isinstance(tracks, list) or not tracks:
        raise ValueError(f"Patch file has no tracks: {patch_path}")

    for track in tracks:
        if not track.get("path"):
            raise ValueError("Every track patch must include a path")
        pois = track.get("pois")
        if not isinstance(pois, list) or not pois:
            raise ValueError(f"Track patch has no POIs: {track.get('path')}")
        for poi in pois:
            normalize_poi_spec(poi)

    return patch


def normalize_path(path: str) -> str:
    return unicodedata.normalize("NFC", os.path.normpath(os.path.expanduser(path)))


def find_song(root: ET.Element, path: str) -> ET.Element | None:
    target = normalize_path(path)
    for song in root.findall("Song"):
        if normalize_path(song.get("FilePath", "")) == target:
            return song
    return None


def manual_pois(song: ET.Element) -> List[ET.Element]:
    return [
        poi
        for poi in song.findall("Poi")
        if poi.get("Type") in MANUAL_CUE_TYPES and poi.get("Num", "0") != "0"
    ]


def poi_sort_key(attrs: Dict[str, str]) -> tuple[float, int]:
    try:
        position = float(attrs.get("Pos", "0"))
    except ValueError:
        position = 0.0
    return position, 1 if attrs.get("Type") == "loop" else 0


def normalize_poi_spec(spec: Dict[str, Any]) -> Dict[str, str]:
    required = ("Type", "Name", "Pos", "Num", "Color")
    missing = [key for key in required if key not in spec]
    if missing:
        raise ValueError(f"POI patch is missing required fields: {missing}")

    poi_type = str(spec["Type"])
    if poi_type not in MANUAL_CUE_TYPES:
        raise ValueError(f"Unsupported POI type in patch: {poi_type}")

    try:
        float(spec["Pos"])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid POI position: {spec.get('Pos')}") from exc

    attrs = {key: str(spec[key]) for key in required}
    if poi_type == "loop":
        for key in ("Size", "Slot"):
            if key not in spec:
                raise ValueError(f"Loop POI patch is missing {key}: {spec}")
            attrs[key] = str(spec[key])
    return attrs


def expected_poi_attrs(track_patch: Dict[str, Any]) -> List[Dict[str, str]]:
    attrs = [normalize_poi_spec(poi) for poi in track_patch["pois"]]
    attrs.sort(key=poi_sort_key)

    loop_slot = 1
    for item in attrs:
        if item["Type"] == "loop":
            item["Slot"] = str(loop_slot)
            loop_slot += 1
    return attrs


def poi_signature(poi: ET.Element) -> Dict[str, str]:
    return {key: poi.get(key, "") for key in POI_COMPARE_KEYS if poi.get(key) is not None}


def track_title(song: ET.Element) -> str:
    tags = song.find("Tags")
    if tags is not None and tags.get("Title"):
        return tags.get("Title", "")
    return Path(song.get("FilePath", "")).name


def comment_from_pois(attrs: Iterable[Dict[str, str]]) -> str:
    colors = {
        COLOR_NAMES[color]
        for color in (item.get("Color", "") for item in attrs)
        if color in COLOR_NAMES
    }
    return " ".join(sorted(colors))


def set_comment(song: ET.Element, comment: str) -> None:
    for existing in song.findall("Comment"):
        song.remove(existing)
    comment_element = ET.Element("Comment")
    comment_element.text = comment
    song.append(comment_element)


def verify_track_readback(root: ET.Element, track_patch: Dict[str, Any]) -> None:
    song = find_song(root, track_patch["path"])
    if song is None:
        raise ValueError(f"Patched track is missing after write: {track_patch['path']}")

    expected = expected_poi_attrs(track_patch)
    actual = [poi_signature(poi) for poi in sorted(manual_pois(song), key=lambda p: poi_sort_key(poi_signature(p)))]
    if actual != expected:
        raise ValueError(
            "Patched track readback did not match expected POIs: "
            f"{track_patch['path']}"
        )


def apply_track_patch(root: ET.Element, track_patch: Dict[str, Any]) -> TrackPatchResult:
    song = find_song(root, track_patch["path"])
    if song is None:
        raise ValueError(f"Track not found in database: {track_patch['path']}")

    existing = manual_pois(song)
    for poi in existing:
        song.remove(poi)

    attrs = expected_poi_attrs(track_patch)
    for item in attrs:
        song.append(ET.Element("Poi", item))

    comment = track_patch.get("comment") or comment_from_pois(attrs)
    set_comment(song, comment)

    return TrackPatchResult(
        path=track_patch["path"],
        title=track_title(song),
        removed=len(existing),
        added=len(attrs),
        comment=comment,
    )


def parse_database(path: os.PathLike | str) -> ET.ElementTree:
    tree = ET.parse(path)
    root = tree.getroot()
    if root.tag != VDJ_DATABASE_ROOT:
        raise ValueError(f"Unexpected VirtualDJ database root: {root.tag}")
    return tree


def write_checked_database(
    tree: ET.ElementTree,
    database_path: Path,
    patch: Dict[str, Any],
    original_stats: Dict[str, int],
) -> Dict[str, int]:
    temp_path = Path(f"{database_path}.tmp")
    try:
        temp_path.write_text(
            serialize_vdj_database(tree.getroot()),
            encoding="utf-8",
            newline="",
        )
        candidate_stats = validate_database_replacement(temp_path, original_stats)
        candidate_root = parse_database(temp_path).getroot()
        for track_patch in patch["tracks"]:
            verify_track_readback(candidate_root, track_patch)
        return candidate_stats
    except Exception:
        if temp_path.exists():
            temp_path.unlink()
        raise


def apply_patch_file(
    database_path: os.PathLike | str,
    patch_path: os.PathLike | str,
    dry_run: bool = False,
    allow_vdj_running: bool = False,
) -> tuple[List[TrackPatchResult], str | None, Dict[str, int]]:
    database = Path(database_path).expanduser()
    patch = load_patch(patch_path)

    if not dry_run and not allow_vdj_running and is_virtualdj_running():
        raise RuntimeError("VirtualDJ is running; close it before writing database.xml")

    original_stats = database_integrity_stats(database)
    tree = parse_database(database)
    root = tree.getroot()
    results = [apply_track_patch(root, track_patch) for track_patch in patch["tracks"]]

    if dry_run:
        return results, None, original_stats

    candidate_stats = write_checked_database(tree, database, patch, original_stats)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{database}.backup.{timestamp}.cuepatch"
    shutil.copy2(database, backup_path)

    temp_path = Path(f"{database}.tmp")
    shutil.move(temp_path, database)

    final_stats = database_integrity_stats(database)
    if final_stats != candidate_stats:
        raise ValueError(
            "Live database stats differ from checked candidate after replacement: "
            f"{final_stats} != {candidate_stats}"
        )

    live_root = parse_database(database).getroot()
    for track_patch in patch["tracks"]:
        verify_track_readback(live_root, track_patch)

    return results, backup_path, final_stats


def print_results(
    results: List[TrackPatchResult],
    backup_path: str | None,
    stats: Dict[str, int],
    dry_run: bool,
) -> None:
    mode = "DRY RUN" if dry_run else "APPLIED"
    print(f"{mode}: {len(results)} track patch(es)")
    for result in results:
        print(
            f"- {result.title}: removed {result.removed}, added {result.added}, "
            f"comment={result.comment}"
        )
    if backup_path:
        print(f"backup={backup_path}")
    print(
        "database_stats="
        f"size:{stats['size_bytes']} songs:{stats['song_count']} "
        f"cue_loop_pois:{stats['cue_loop_count']}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply curated cue corrections to VirtualDJ database.xml."
    )
    parser.add_argument("--database", default=str(DEFAULT_DATABASE))
    parser.add_argument("--patch", default=str(DEFAULT_PATCH))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--allow-vdj-running",
        action="store_true",
        help="override the VirtualDJ process guard",
    )
    args = parser.parse_args()

    try:
        results, backup_path, stats = apply_patch_file(
            args.database,
            args.patch,
            dry_run=args.dry_run,
            allow_vdj_running=args.allow_vdj_running,
        )
    except Exception as exc:
        print(f"error={exc}", file=sys.stderr)
        return 1

    print_results(results, backup_path, stats, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
