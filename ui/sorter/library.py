"""Discover Ready-for-Sort tracks and nested House/Zouk destination folders."""

from __future__ import annotations

import re
import time
import unicodedata
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
    SETS_ROOT,
    ZOUK_VIBE_FOLDERS,
)

# Set crate indexes: "407. Title"
_SET_INDEX_PREFIX_RE = re.compile(r"^\d{1,4}\.\s+")
# One library/crate prefix after that: "01 - Title", "01. Title",
# "14 - Title" (space before the dash), or zero-padded "01 Title".
# Do not eat "50 Cent" / "99 Problems".
_CRATE_PREFIX_RE = re.compile(r"^(?:\d{1,3}\s*[.\-]\s*|0\d\s+)")
_EXPLICIT_RE = re.compile(r"\s*\(\s*explicit\s*\)", re.I)
# Promote collisions are "_1" / "_12", not years like "_2024".
_COLLISION_SUFFIX_RE = re.compile(r"_\d{1,2}$")
_PARENTHETICAL_RE = re.compile(r"\([^)]*\)")
_VERSION_NUMBER_RE = re.compile(r"\bversion\s+(\d+)\b", re.I)
_PAJAMATHON_EVENT_PREFIX = "pajamathon"
_PLACEMENT_INDEX_CACHE: dict[str, Any] = {
    "at": 0.0,
    "placement": None,
    "sets": None,
}


def invalidate_placement_indexes() -> None:
    """Drop the House/Zouk/Sets basename cache after files move or vanish."""
    _PLACEMENT_INDEX_CACHE["at"] = 0.0
    _PLACEMENT_INDEX_CACHE["placement"] = None
    _PLACEMENT_INDEX_CACHE["sets"] = None


def cached_placement_indexes() -> tuple[
    dict[str, list[dict[str, str]]],
    dict[str, list[dict[str, str]]],
]:
    now = time.monotonic()
    cached = _PLACEMENT_INDEX_CACHE
    if cached["placement"] is not None and now - float(cached["at"]) < 120:
        return cached["placement"], cached["sets"]
    placement = build_audio_basename_index([*LIBRARIES.values(), CUES_SORTED])
    sets = build_set_match_index(SETS_ROOT)
    cached["at"] = now
    cached["placement"] = placement
    cached["sets"] = sets
    return placement, sets


@dataclass(frozen=True)
class TrackInfo:
    path: str
    name: str
    stems_path: Optional[str]
    size_bytes: int
    relative_path: str = ""
    group: str = ""
    section: str = ""

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


def build_audio_basename_index(
    roots: Iterable[Path],
    *,
    max_per_name: int = 8,
) -> dict[str, list[dict[str, str]]]:
    """
    One-pass index of audio under library / archive roots.

    Used by Add Cues and Sort so N tracks don't each rglob the tree.
    Keys are exact basenames and normalized title keys (track numbers stripped).
    """
    index: dict[str, list[dict[str, str]]] = {}
    for root in roots:
        root = Path(root)
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not _is_audio_file(path):
                continue
            if path.name.endswith(".vdjstems"):
                continue
            try:
                rel_parts = path.relative_to(root).parts
            except ValueError:
                continue
            if any(_should_skip_dir(part) for part in rel_parts[:-1]):
                continue
            rel = path.relative_to(root).as_posix()
            hit = {
                "path": str(path),
                "relative_path": rel,
                "root": str(root),
                "root_name": root.name,
            }
            for key in (path.name, normalize_placement_key(path.name)):
                if not key:
                    continue
                bucket = index.setdefault(key, [])
                if any(existing["path"] == hit["path"] for existing in bucket):
                    continue
                if len(bucket) >= max_per_name:
                    continue
                bucket.append(hit)
    return index


def find_matches_from_index(
    filename: str,
    index: dict[str, list[dict[str, str]]],
    *,
    root_names: Optional[set[str]] = None,
    max_hits: int = 8,
    fuzzy: bool = True,
) -> list[dict[str, str]]:
    """Filter a prebuilt basename index for one filename (optionally by root)."""
    keys = [filename]
    if fuzzy:
        norm = normalize_placement_key(filename)
        if norm and norm not in keys:
            keys.append(norm)
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for key in keys:
        for hit in index.get(key) or []:
            path = hit.get("path") or ""
            if not path or path in seen:
                continue
            if not Path(path).is_file():
                continue
            if root_names is not None and hit.get("root_name") not in root_names:
                continue
            seen.add(path)
            out.append(hit)
            if len(out) >= max_hits:
                return out
    return out


def _stem_after_prefixes(filename: str) -> tuple[str, str]:
    path = Path(filename)
    stem = unicodedata.normalize("NFC", path.stem)
    ext = path.suffix.lower()
    while True:
        nxt = _SET_INDEX_PREFIX_RE.sub("", stem, count=1)
        if nxt == stem:
            break
        stem = nxt
    stem = _CRATE_PREFIX_RE.sub("", stem, count=1)
    stem = _EXPLICIT_RE.sub("", stem)
    stem = _COLLISION_SUFFIX_RE.sub("", stem)
    return stem, ext


def _compact_stem_key(stem: str, ext: str) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", stem.lower()).strip()
    return f"{text}{ext}" if text else ""


def normalize_placement_key(filename: str) -> str:
    """Comparable stem+ext: strip track numbers, (Explicit), punctuation."""
    stem, ext = _stem_after_prefixes(filename)
    return _compact_stem_key(stem, ext)


def placement_match_keys(filename: str) -> list[str]:
    """
    Lookup keys for the same recording under slightly different filenames.

    Includes the exact name, the normalized title, a parenthetical-stripped
    title (remix vs featured-artist tags), and Version N collapsed to N.
    """
    keys: list[str] = []
    seen: set[str] = set()

    def add(key: str) -> None:
        if key and key not in seen:
            seen.add(key)
            keys.append(key)

    add(filename)
    stem, ext = _stem_after_prefixes(filename)
    add(_compact_stem_key(stem, ext))
    add(_compact_stem_key(_VERSION_NUMBER_RE.sub(r"\1", stem), ext))
    stripped = _PARENTHETICAL_RE.sub(" ", stem)
    add(_compact_stem_key(stripped, ext))
    add(_compact_stem_key(_VERSION_NUMBER_RE.sub(r"\1", stripped), ext))
    return keys


def is_must_play_folder_path(path: str | Path) -> bool:
    """True when any path part is the Must Play folder."""
    text = str(path or "").replace("\\", "/")
    return any(part.lower() == "must play" for part in text.split("/") if part)


def is_pajamathon_event(name: str) -> bool:
    return (name or "").strip().lower().startswith(_PAJAMATHON_EVENT_PREFIX)


def is_pajamathon_set_audio(
    path: str | Path,
    *,
    sets_root: Path | None = None,
) -> bool:
    """True when the file lives in Sets/Pajamathon* (the event crate)."""
    audio = Path(path).expanduser().resolve()
    root = Path(sets_root or SETS_ROOT).expanduser().resolve()
    try:
        rel = audio.relative_to(root)
    except ValueError:
        return False
    if not rel.parts:
        return False
    return is_pajamathon_event(rel.parts[0])


def _set_hit(path: Path, sets_root: Path) -> dict[str, str]:
    rel = path.relative_to(sets_root).as_posix()
    event = path.relative_to(sets_root).parts[0]
    return {
        "path": str(path),
        "relative_path": rel,
        "root": str(sets_root),
        "root_name": event,
        "event": event,
    }


def build_set_match_index(
    sets_root: Path | None = None,
    *,
    max_per_key: int = 8,
) -> dict[str, list[dict[str, str]]]:
    """Index Sets/ audio by exact basename and normalized title key."""
    root = Path(sets_root or SETS_ROOT)
    index: dict[str, list[dict[str, str]]] = {}
    if not root.is_dir():
        return index
    for path in root.rglob("*"):
        if not _is_audio_file(path):
            continue
        try:
            rel_parts = path.relative_to(root).parts
        except ValueError:
            continue
        if not rel_parts or not is_pajamathon_event(rel_parts[0]):
            continue
        if any(_should_skip_dir(part) for part in rel_parts[:-1]):
            continue
        hit = _set_hit(path, root)
        for key in placement_match_keys(path.name):
            if not key:
                continue
            bucket = index.setdefault(key, [])
            if any(existing["path"] == hit["path"] for existing in bucket):
                continue
            if len(bucket) >= max_per_key:
                continue
            bucket.append(hit)
    return index


def find_set_matches(
    filename: str,
    *,
    index: Optional[dict[str, list[dict[str, str]]]] = None,
    sets_root: Path | None = None,
    max_hits: int = 8,
) -> list[dict[str, str]]:
    """Locate the same song under Sets/ (exact name, then fuzzy title)."""
    lookup = index if index is not None else build_set_match_index(sets_root)
    hits: list[dict[str, str]] = []
    seen: set[str] = set()
    for key in placement_match_keys(filename):
        if not key:
            continue
        for hit in lookup.get(key) or []:
            path = hit.get("path") or ""
            if not path or path in seen:
                continue
            if not Path(path).is_file():
                continue
            seen.add(path)
            hits.append(hit)
            if len(hits) >= max_hits:
                return hits
    return hits


def find_cues_sorted_matches(
    filename: str,
    *,
    index: Optional[dict[str, list[dict[str, str]]]] = None,
) -> list[dict[str, str]]:
    lookup = (
        index
        if index is not None
        else build_audio_basename_index([CUES_SORTED])
    )
    return find_matches_from_index(
        filename, lookup, root_names={CUES_SORTED.name}, fuzzy=True
    )


def find_library_matches(
    filename: str,
    *,
    index: Optional[dict[str, list[dict[str, str]]]] = None,
) -> list[dict[str, str]]:
    lookup = (
        index
        if index is not None
        else build_audio_basename_index(list(LIBRARIES.values()))
    )
    return find_matches_from_index(
        filename, lookup, root_names=set(LIBRARIES.keys()), fuzzy=True
    )


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


def audio_inode_key(path: str | Path) -> tuple[int, int] | None:
    """Device + inode for hard-link identity, or None if the file is gone."""
    try:
        st = Path(path).expanduser().stat()
    except OSError:
        return None
    return (st.st_dev, st.st_ino)


def inode_keys_for_paths(paths: Iterable[str | Path]) -> set[tuple[int, int]]:
    keys: set[tuple[int, int]] = set()
    for path in paths:
        key = audio_inode_key(path)
        if key is not None:
            keys.add(key)
    return keys


def is_add_cues_vdj_ghost(
    path: str | Path,
    *,
    in_database: bool,
    set_inodes: set[tuple[int, int]],
) -> bool:
    """Add Cues leftover: no VDJ Song here, but the same bytes already live in Sets."""
    if in_database:
        return False
    key = audio_inode_key(path)
    return key is not None and key in set_inodes


def drop_add_cues_vdj_ghosts(
    tracks: list[TrackInfo],
    *,
    in_database_by_path: dict[str, bool],
    set_inodes: set[tuple[int, int]],
) -> list[TrackInfo]:
    """Drop Add Cues rows whose only Song is the Sets hard-link."""
    kept: list[TrackInfo] = []
    for track in tracks:
        if is_add_cues_vdj_ghost(
            track.path,
            in_database=bool(in_database_by_path.get(track.path, False)),
            set_inodes=set_inodes,
        ):
            continue
        kept.append(track)
    return kept


def add_cues_section(*, group: str = "", relative_path: str = "") -> str:
    """Split Add Cues into the Pajamathon crate vs the general inbox."""
    rel = (relative_path or "").replace("\\", "/").strip("/")
    top = rel.split("/", 1)[0] if rel else ""
    name = (group or top or "").strip().lower()
    if name.startswith("pajamathon"):
        return "pajamathon"
    return "inbox"


def list_pajamathon_set_tracks(sets_root: Path | None = None) -> list[TrackInfo]:
    """Audio in Sets/Pajamathon* — the event crate the Pajamathon tab should show."""
    root = Path(sets_root or SETS_ROOT)
    if not root.is_dir():
        return []
    tracks: list[TrackInfo] = []
    for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir() or not is_pajamathon_event(child.name):
            continue
        try:
            files = sorted(child.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            continue
        for path in files:
            if not _is_audio_file(path):
                continue
            stems = Path(f"{path}.vdjstems")
            tracks.append(
                TrackInfo(
                    path=str(path),
                    name=path.name,
                    stems_path=str(stems) if stems.is_file() else None,
                    size_bytes=path.stat().st_size,
                    relative_path=f"{child.name}/{path.name}",
                    group=child.name,
                    section="pajamathon",
                )
            )
    return tracks


def list_all_set_tracks(sets_root: Path | None = None) -> list[TrackInfo]:
    """Every audio file under Sets/, including Pajamathon subfolders like Must Play."""
    root = Path(sets_root or SETS_ROOT)
    if not root.is_dir():
        return []
    tracks: list[TrackInfo] = []
    for path in sorted(root.rglob("*"), key=lambda p: str(p).lower()):
        if not _is_audio_file(path):
            continue
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        if any(part.startswith(".") for part in rel.parts[:-1]):
            continue
        folder = "/".join(rel.parts[:-1]) or root.name
        stems = Path(f"{path}.vdjstems")
        tracks.append(
            TrackInfo(
                path=str(path),
                name=path.name,
                stems_path=str(stems) if stems.is_file() else None,
                size_bytes=path.stat().st_size,
                relative_path=rel.as_posix(),
                group=folder,
                section="set",
            )
        )
    return tracks


def merge_add_cues_and_pajamathon_set(
    add_tracks: list[TrackInfo],
    set_tracks: list[TrackInfo] | None = None,
) -> list[TrackInfo]:
    """Add Cues queue only. Sets/Pajamathon is the approved crate — not listed here."""
    del set_tracks
    return list(add_tracks)


def add_cues_tracks_by_crate(
    crate: str = "all",
    source_dir: Path | None = None,
) -> list[TrackInfo]:
    """Add Cues tracks for one crate: all, pajamathon, or inbox."""
    tracks = list_add_cues_tracks(source_dir)
    if crate == "pajamathon":
        return [t for t in tracks if t.section == "pajamathon"]
    if crate == "inbox":
        return [t for t in tracks if t.section != "pajamathon"]
    return list(tracks)


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
        section = add_cues_section(group=group, relative_path=rel)

        stems = Path(f"{path}.vdjstems")
        tracks.append(
            TrackInfo(
                path=str(path),
                name=path.name,
                stems_path=str(stems) if stems.is_file() else None,
                size_bytes=path.stat().st_size,
                relative_path=rel,
                group=group,
                section=section,
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
