"""Paths and library definitions for the music sorter."""

from __future__ import annotations

from pathlib import Path

MUSIC_ROOT = Path.home() / "Music" / "DJ" / "Music"
CUES_ROOT = MUSIC_ROOT / "Cues"
READY_FOR_SORT = CUES_ROOT / "Ready For Sort"
ADD_CUES = CUES_ROOT / "Add Cues"
CUES_SORTED = CUES_ROOT / "Cues Sorted"
NO_CUES_FOUND = CUES_ROOT / "No Cues Found"
AC_LOW_QUALITY = CUES_ROOT / "AC Low Quality"
LOW_QUALITY_SKIP = CUES_ROOT / "Low Quality Skip"
VDJ_DATABASE = (
    Path.home() / "Library" / "Application Support" / "VirtualDJ" / "database.xml"
)

# Cue-pipeline stages used by the Add Cues review view.
CUE_STAGES: dict[str, Path] = {
    "add_cues": ADD_CUES,
    "ready_for_sort": READY_FOR_SORT,
    "no_cues_found": NO_CUES_FOUND,
    "ac_low_quality": AC_LOW_QUALITY,
    "low_quality_skip": LOW_QUALITY_SKIP,
}

# Subfolders under Add Cues that are not music crates.
ADD_CUES_SKIP_DIR_NAMES = {
    ".temp_download",
    "Playlists",
    "low_quality_backups",
    ".backups",
    "__pycache__",
}

# Emotion / vibe destination libraries shown in the UI.
LIBRARIES: dict[str, Path] = {
    "House": MUSIC_ROOT / "House",
    "Zouk": MUSIC_ROOT / "Zouk",
}

# Directories that are tooling / junk — never shown as sort destinations.
LIBRARY_SKIP_DIR_NAMES = {
    ".git",
    ".claude",
    "__pycache__",
    "claude_scripts",
    "start",
    "low_quality_backups",
    ".backups",
    "Blvck spotdl",
    "Not Sorted",
    "Stefan Folders",
}

AUDIO_EXTENSIONS = {".mp3", ".flac", ".m4a", ".wav", ".aiff", ".aif", ".ogg", ".opus"}

# Zouk top-level folders that are primarily emotional/vibe crates (UI grouping).
ZOUK_VIBE_FOLDERS = {
    "Bassy",
    "Beautiful Sound",
    "Chill",
    "Classics",
    "Closers",
    "Energy",
    "Experimental",
    "Favs",
    "Filth",
    "Foreign",
    "Gotos",
    "Hip Hoppy",
    "Intense",
    "Jazzy",
    "JR&B",
    "Neo Soul",
    "Nostalgia",
    "Openers",
    "Pop",
    "R&B",
    "Reggae",
    "Reggatonish",
    "Rock",
    "Trancy",
    "Trappy",
    "Tribal",
    "Trippy Party",
    "Troll",
}
