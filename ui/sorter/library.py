"""Discover Ready-for-Sort tracks and nested House/Zouk destination folders."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from .config import (
    ADD_CUES,
    ADD_CUES_SKIP_DIR_NAMES,
    AUDIO_EXTENSIONS,
    CUES_SORTED,
    LIBRARIES,
    LIBRARY_SKIP_DIR_NAMES,
    READY_FOR_SORT,
    ZOUK_VIBE_FOLDERS,
)


@dataclass(frozen=True)
class TrackInfo:
    path: str
    name: str
    stems_path: Optional[str]
    size_bytes: int
    relative_path: str = ""
    group: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FolderNode:
    """One destination folder, possibly with children (e.g. Zouk/Chill/Mystical)."""

    name: str
    relative_path: str  # POSIX-style path under library root
    absolute_path: str
    track_count: int
    children: list["FolderNode"]
    group: str  # "vibe" | "artist" | "nested"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "relative_path": self.relative_path,
            "absolute_path": self.absolute_path,
            "track_count": self.track_count,
            "group": self.group,
            "children": [child.to_dict() for child in self.children],
        }


def _is_audio_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS


def _should_skip_dir(name: str) -> bool:
    if name in LIBRARY_SKIP_DIR_NAMES:
        return True
    if name.startswith("."):
        return True
    if name.endswith(".vdjstems"):
        return True
    return False


def _count_audio_files(directory: Path) -> int:
    try:
        return sum(1 for p in directory.iterdir() if _is_audio_file(p))
    except OSError:
        return 0


def find_matches_by_filename(
    filename: str,
    roots: Iterable[Path],
    *,
    max_hits: int = 8,
) -> list[dict[str, str]]:
    """Locate files with the same basename under one or more roots."""
    hits: list[dict[str, str]] = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob(filename):
            if not path.is_file():
                continue
            # Skip stems sidecars and junk dirs.
            if path.name.endswith(".vdjstems"):
                continue
            if any(_should_skip_dir(part) for part in path.relative_to(root).parts[:-1]):
                continue
            rel = path.relative_to(root).as_posix()
            hits.append(
                {
                    "path": str(path),
                    "relative_path": rel,
                    "root": str(root),
                    "root_name": root.name,
                }
            )
            if len(hits) >= max_hits:
                return hits
    return hits


def find_cues_sorted_matches(filename: str) -> list[dict[str, str]]:
    return find_matches_by_filename(filename, [CUES_SORTED])


def find_library_matches(filename: str) -> list[dict[str, str]]:
    return find_matches_by_filename(filename, list(LIBRARIES.values()))


def list_ready_tracks(source_dir: Path | None = None) -> list[TrackInfo]:
    root = source_dir or READY_FOR_SORT
    if not root.is_dir():
        return []

    tracks: list[TrackInfo] = []
    for path in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not _is_audio_file(path):
            continue
        stems = Path(f"{path}.vdjstems")
        tracks.append(
            TrackInfo(
                path=str(path),
                name=path.name,
                stems_path=str(stems) if stems.is_file() else None,
                size_bytes=path.stat().st_size,
                relative_path=path.name,
                group="Ready for Sort",
            )
        )
    return tracks


def list_add_cues_tracks(source_dir: Path | None = None) -> list[TrackInfo]:
    """
    Recursive audio under Add Cues for cue-quality review.

    Skips tooling folders; keeps batch folders like Screenshots 7-15-26.
    """
    root = source_dir or ADD_CUES
    if not root.is_dir():
        return []

    tracks: list[TrackInfo] = []
    for path in sorted(root.rglob("*"), key=lambda p: str(p).lower()):
        if not _is_audio_file(path):
            continue
        # Skip junk directories anywhere in the path.
        parts = set(path.relative_to(root).parts[:-1])
        if parts & ADD_CUES_SKIP_DIR_NAMES:
            continue
        if any(part.startswith(".") for part in path.relative_to(root).parts[:-1]):
            continue

        rel = path.relative_to(root).as_posix()
        group = path.parent.name if path.parent != root else "Add Cues"
        if path.parent == root:
            group = "Add Cues"
        else:
            # Prefer top-level batch/artist folder for grouping.
            group = path.relative_to(root).parts[0]

        stems = Path(f"{path}.vdjstems")
        tracks.append(
            TrackInfo(
                path=str(path),
                name=path.name,
                stems_path=str(stems) if stems.is_file() else None,
                size_bytes=path.stat().st_size,
                relative_path=rel,
                group=group,
            )
        )
    return tracks


def _build_folder_tree(
    directory: Path,
    library_root: Path,
    *,
    depth: int,
    max_depth: int,
    library_name: str,
) -> list[FolderNode]:
    if depth > max_depth:
        return []

    nodes: list[FolderNode] = []
    try:
        children = sorted(directory.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return []

    for child in children:
        if not child.is_dir() or _should_skip_dir(child.name):
            continue

        rel = child.relative_to(library_root).as_posix()
        nested = _build_folder_tree(
            child,
            library_root,
            depth=depth + 1,
            max_depth=max_depth,
            library_name=library_name,
        )
        if depth == 0 and library_name == "Zouk":
            group = "vibe" if child.name in ZOUK_VIBE_FOLDERS else "artist"
        elif depth == 0:
            group = "vibe"
        else:
            group = "nested"

        nodes.append(
            FolderNode(
                name=child.name,
                relative_path=rel,
                absolute_path=str(child),
                track_count=_count_audio_files(child),
                children=nested,
                group=group,
            )
        )
    return nodes


def expand_library_mode(library_mode: str) -> list[str]:
    """Map UI sort path (House / Zouk / Both) to concrete library names."""
    mode = (library_mode or "").strip()
    key = mode.lower()
    if key == "both":
        return ["House", "Zouk"]
    if mode in LIBRARIES:
        return [mode]
    # Accept case-insensitive single library.
    for name in LIBRARIES:
        if name.lower() == key:
            return [name]
    raise KeyError(f"Unknown library mode: {library_mode}")


def list_library_tree(
    library_name: str,
    *,
    max_depth: int = 4,
) -> dict[str, Any]:
    # Both returns nested trees for the dual-path UI.
    if library_name.strip().lower() == "both":
        return {
            "library": "Both",
            "root": None,
            "folders": [],
            "trees": {
                name: list_library_tree(name, max_depth=max_depth)
                for name in expand_library_mode("Both")
            },
        }

    if library_name not in LIBRARIES:
        # case-insensitive
        matched = None
        for name in LIBRARIES:
            if name.lower() == library_name.lower():
                matched = name
                break
        if matched is None:
            raise KeyError(f"Unknown library: {library_name}")
        library_name = matched

    root = LIBRARIES[library_name]
    if not root.is_dir():
        raise FileNotFoundError(f"Library root missing: {root}")

    tree = _build_folder_tree(
        root,
        root,
        depth=0,
        max_depth=max_depth,
        library_name=library_name,
    )
    return {
        "library": library_name,
        "root": str(root),
        "folders": [node.to_dict() for node in tree],
    }


def list_libraries() -> list[dict[str, Any]]:
    result = []
    for name, path in LIBRARIES.items():
        result.append(
            {
                "name": name,
                "path": str(path),
                "exists": path.is_dir(),
            }
        )
    result.append(
        {
            "name": "Both",
            "path": None,
            "exists": all(p.is_dir() for p in LIBRARIES.values()),
        }
    )
    return result


_SAFE_FOLDER_NAME = re.compile(r"^[^/\\:\0]+$")


def _validate_folder_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        raise ValueError("Folder name cannot be empty")
    if cleaned in {".", ".."}:
        raise ValueError("Invalid folder name")
    if not _SAFE_FOLDER_NAME.match(cleaned):
        raise ValueError("Folder name cannot contain path separators or colons")
    if cleaned.startswith("."):
        raise ValueError("Hidden folder names are not allowed")
    if cleaned in LIBRARY_SKIP_DIR_NAMES:
        raise ValueError(f"Reserved folder name: {cleaned}")
    return cleaned


def resolve_destination(
    library_name: str,
    relative_path: str,
    *,
    create: bool = False,
) -> Path:
    """Resolve a library-relative path and ensure it stays inside the library root."""
    if library_name not in LIBRARIES:
        raise KeyError(f"Unknown library: {library_name}")
    root = LIBRARIES[library_name].resolve()
    rel = relative_path.strip().strip("/")
    if not rel:
        raise ValueError("Destination folder path is required")
    dest = (root / rel).resolve()
    try:
        dest.relative_to(root)
    except ValueError as exc:
        raise ValueError("Destination escapes library root") from exc
    if create:
        dest.mkdir(parents=True, exist_ok=True)
    return dest


def create_folder(
    library_name: str,
    *,
    name: str,
    parent_relative_path: str = "",
) -> dict[str, Any]:
    """
    Create a new sort destination under House/Zouk (or under a nested parent).

    library_name may be House, Zouk, or Both (creates under every library).

    Examples:
      create_folder("House", name="Dreamy")
      create_folder("Zouk", name="Amber", parent_relative_path="Chill")
      create_folder("Both", name="Pulse", parent_relative_path="Energy")
    """
    folder_name = _validate_folder_name(name)
    libraries = expand_library_mode(library_name)
    created: list[dict[str, str]] = []
    primary: dict[str, Any] | None = None

    for lib in libraries:
        root = LIBRARIES[lib].resolve()
        if parent_relative_path.strip():
            parent = resolve_destination(lib, parent_relative_path, create=True)
        else:
            parent = root

        if not parent.is_dir():
            raise FileNotFoundError(f"Parent folder does not exist: {parent}")

        new_dir = (parent / folder_name).resolve()
        try:
            new_dir.relative_to(root)
        except ValueError as exc:
            raise ValueError("New folder would escape library root") from exc

        if new_dir.exists():
            if not new_dir.is_dir():
                raise FileExistsError(f"A non-folder already exists at: {new_dir}")
            # For Both, skip libraries that already have the folder.
            if library_name.strip().lower() != "both":
                raise FileExistsError(f"Folder already exists: {new_dir}")
        else:
            new_dir.mkdir(parents=False, exist_ok=False)

        rel = new_dir.relative_to(root).as_posix()
        entry = {
            "library": lib,
            "name": folder_name,
            "relative_path": rel,
            "absolute_path": str(new_dir),
            "parent_relative_path": parent_relative_path.strip().strip("/"),
        }
        created.append(entry)
        if primary is None:
            primary = entry

    assert primary is not None
    primary = dict(primary)
    primary["created_in"] = created
    primary["library_mode"] = library_name
    return primary


def flatten_folder_paths(nodes: Iterable[dict[str, Any]], prefix: str = "") -> list[str]:
    """Utility for tests / search: all relative paths in a tree."""
    paths: list[str] = []
    for node in nodes:
        paths.append(node["relative_path"])
        paths.extend(flatten_folder_paths(node.get("children") or []))
    return paths
