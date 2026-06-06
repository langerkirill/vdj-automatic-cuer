"""Safety checks for replacing a VirtualDJ database.xml file."""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict


VDJ_DATABASE_ROOT = "VirtualDJ_Database"
MANUAL_CUE_TYPES = {"cue", "loop"}


def database_integrity_stats(database_path: os.PathLike | str) -> Dict[str, int]:
    """Return structural stats used to guard VirtualDJ database writes."""
    path = Path(database_path)
    tree = ET.parse(path)
    root = tree.getroot()
    if root.tag != VDJ_DATABASE_ROOT:
        raise ValueError(f"Unexpected VirtualDJ database root: {root.tag}")

    songs = root.findall("Song")
    cue_loop_count = 0
    for song in songs:
        for poi in song.findall("Poi"):
            if poi.get("Type") in MANUAL_CUE_TYPES and poi.get("Num", "0") != "0":
                cue_loop_count += 1

    return {
        "size_bytes": path.stat().st_size,
        "song_count": len(songs),
        "cue_loop_count": cue_loop_count,
    }


def serialize_vdj_database(root: ET.Element) -> str:
    """Serialize database XML in the same no-declaration style VirtualDJ accepts."""
    xml_str = ET.tostring(root, encoding="unicode")
    if "\r\n" not in xml_str and "\n" in xml_str:
        xml_str = xml_str.replace("\n", "\r\n")
    ET.fromstring(xml_str)
    return xml_str


def validate_database_replacement(
    candidate_path: os.PathLike | str, original_stats: Dict[str, int]
) -> Dict[str, int]:
    """Reject parseable but structurally broken replacement databases."""
    candidate_stats = database_integrity_stats(candidate_path)

    if candidate_stats["song_count"] < original_stats["song_count"]:
        raise ValueError(
            "Generated database failed integrity check: song count dropped "
            f"from {original_stats['song_count']} to "
            f"{candidate_stats['song_count']}"
        )

    original_cues = original_stats["cue_loop_count"]
    candidate_cues = candidate_stats["cue_loop_count"]
    if original_cues >= 20 and candidate_cues < int(original_cues * 0.75):
        raise ValueError(
            "Generated database failed integrity check: cue/loop count dropped "
            f"from {original_cues} to {candidate_cues}"
        )
    if 0 < original_cues < 20 and candidate_cues == 0:
        raise ValueError(
            "Generated database failed integrity check: cue/loop count dropped "
            f"from {original_cues} to 0"
        )

    original_size = original_stats["size_bytes"]
    candidate_size = candidate_stats["size_bytes"]
    if original_size >= 1_000_000 and candidate_size < int(original_size * 0.75):
        raise ValueError(
            "Generated database failed integrity check: file size dropped "
            f"from {original_size} to {candidate_size} bytes"
        )

    return candidate_stats
