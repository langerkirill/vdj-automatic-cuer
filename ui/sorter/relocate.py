"""
Move cued audio into a destination folder and retarget VirtualDJ FilePath.

XML handling is delegated entirely to vdj-automatic-cuer's vdj_database_safety:
CRLF preservation, surgical Song rewrite, integrity checks.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from .autocue_path import ensure_autocue_on_path
from .config import (
    ADD_CUES,
    AUDIO_EXTENSIONS,
    CUE_STAGES,
    CUES_ROOT,
    CUES_SORTED,
    LIBRARIES,
    READY_FOR_SORT,
    SETS_ROOT,
    VDJ_DATABASE,
)
from .db_lock import vdj_db_write
from .cue_readiness import assess_cue_readiness, vdj_bpm_to_actual
from .musical_key import key_to_camelot, song_key_from_element
from .library import (
    expand_library_mode,
    find_cues_sorted_matches,
    find_library_matches,
    find_set_matches,
    is_pajamathon_event,
    is_pajamathon_set_audio,
    resolve_destination,
)

ensure_autocue_on_path()

from vdj_database_safety import (  # noqa: E402
    MANUAL_CUE_TYPES,
    _FILEPATH_RE,
    _POI_LINE_RE,
    _SONG_OPEN_RE,
    _find_song_span,
    _is_manual_cue_or_loop_poi,
    _lightweight_content_stats,
    _lightweight_rewrite_stats,
    _unescape_xml_attr,
    atomic_replace_database_parts,
    clone_song_entry_to_path,
    directory_sort_label,
    iter_manual_poi_tags,
    load_song_element,
    normalize_database_path,
    normalize_user2_dest,
    patch_song_infos_and_user2,
    read_vdj_database_text,
    relocate_song_filepath_in_database,
    rewrite_song_xml_in_database,
)

# VirtualDJ ARGB color ints → display names (same palette as AutoCue).
VDJ_COLOR_NAMES: dict[str, str] = {
    "4278190335": "blue",
    "4278255360": "green",
    "4288020735": "purple",
    "4294967040": "yellow",
    "4294934272": "orange",
}


@dataclass
class CuePoint:
    """One jumpable VDJ marker (cue or loop)."""

    kind: str  # "cue" | "loop"
    name: str
    pos: float
    num: str
    color: str
    color_name: str
    size: Optional[str] = None  # loop length in beats
    slot: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CueSummary:
    cue_count: int
    loop_count: int
    has_beatgrid: bool
    title: str
    author: str
    in_database: bool
    song_length: Optional[float] = None
    beatgrid_pos: Optional[float] = None
    scan_phase: Optional[float] = None
    bpm: Optional[float] = None
    comment: str = ""  # VirtualDJ <Comment> notes field
    key: str = ""
    camelot: str = ""
    user_color: str = ""
    points: list[CuePoint] = field(default_factory=list)

    @property
    def is_cued(self) -> bool:
        """True when the track has at least one real manual cue point."""
        return self.in_database and self.cue_count > 0

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["is_cued"] = self.is_cued
        payload["points"] = [p.to_dict() if isinstance(p, CuePoint) else p for p in self.points]
        return payload


@dataclass
class SortResult:
    source_path: str
    dest_path: str
    stems_moved: bool
    database_updated: bool
    database_backup: Optional[str]
    cues: CueSummary
    dry_run: bool
    cues_sorted_path: Optional[str] = None
    cues_sorted_copied: bool = False
    cues_sorted_db_cloned: bool = False
    cues_sorted_already_present: bool = False
    library_mode: str = ""
    library_dests: list[dict[str, str]] = field(default_factory=list)
    sets_cues_copied: int = 0
    sets_cues_skipped: int = 0
    sets_paths: list[str] = field(default_factory=list)
    lane: str = ""
    lane_color: str = ""
    dest_reused: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["cues"] = self.cues.to_dict()
        return payload


def is_virtualdj_running() -> bool:
    """
    True only when the VirtualDJ *app* appears to be running.

    Avoid false positives from:
    - Docker/Apple ``virtualization`` helpers
    - Shells/agents whose argv merely *mentions* VirtualDJ (scripts, paths)
    """
    try:
        result = subprocess.run(
            ["ps", "-ax", "-o", "comm=,command="],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return False

    for raw in result.stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        # ps: first token is comm, rest is full command
        parts = line.split(None, 1)
        comm = parts[0].lower().replace(" ", "")
        cmd = (parts[1] if len(parts) > 1 else "").lower()

        # Process name is the VirtualDJ binary.
        if comm in {
            "virtualdj",
            "virtualdj8",
            "virtualdj2021",
            "virtualdj2022",
            "virtualdj2023",
            "virtualdj2024",
            "virtualdj2025",
            "virtualdj2026",
        }:
            return True

        # macOS app bundle executable (not a random shell that mentions the path).
        if "virtualdj.app/contents/macos/" in cmd:
            # Skip long shell wrappers that only reference the path in a script body.
            if comm in {"zsh", "bash", "sh", "fish", "csh", "tcsh", "python", "python3"}:
                continue
            return True

    return False


def _normalize_path(path: str | Path) -> str:
    return normalize_database_path(str(Path(path).expanduser().resolve()))


def _optional_float(value: Optional[str]) -> Optional[float]:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _format_point_name(poi_type: str, name: str, num: str) -> str:
    cleaned = (name or "").strip()
    if cleaned:
        return cleaned
    if poi_type == "loop":
        return f"Loop {num}" if num not in {"", "-1"} else "Loop"
    if num and num != "0":
        return f"Cue {num}"
    return "Cue"


def _empty_cue_summary() -> CueSummary:
    return CueSummary(
        cue_count=0,
        loop_count=0,
        has_beatgrid=False,
        title="",
        author="",
        in_database=False,
        song_length=None,
        beatgrid_pos=None,
        scan_phase=None,
        bpm=None,
        comment="",
        key="",
        camelot="",
        user_color="",
        points=[],
    )


def _path_lookup_keys(audio_path: str | Path) -> list[str]:
    raw = str(audio_path)
    keys: list[str] = []
    seen: set[str] = set()
    candidates = [raw, normalize_database_path(raw)]
    try:
        candidates.append(_normalize_path(raw))
    except OSError:
        pass
    try:
        candidates.append(normalize_database_path(unicodedata.normalize("NFC", raw)))
        candidates.append(normalize_database_path(unicodedata.normalize("NFD", raw)))
    except Exception:
        pass
    for item in candidates:
        if item and item not in seen:
            seen.add(item)
            keys.append(item)
    return keys


def _song_xml_from_open(content: str, open_match: Any) -> Optional[str]:
    start = open_match.start()
    depth = 1
    pos = open_match.end()
    while depth > 0:
        next_open = content.find("<Song", pos)
        next_close = content.find("</Song>", pos)
        if next_close < 0:
            return None
        if next_open >= 0 and next_open < next_close:
            depth += 1
            pos = next_open + 5
        else:
            depth -= 1
            if depth == 0:
                return content[start : next_close + len("</Song>")]
            pos = next_close + len("</Song>")
    return None


def _cue_summary_from_song_element(song: ET.Element) -> CueSummary:
    tags = song.find("Tags")
    infos = song.find("Infos")
    scan = song.find("Scan")
    comment_el = song.find("Comment")
    title = tags.get("Title", "") if tags is not None else ""
    author = tags.get("Author", "") if tags is not None else ""
    comment = (comment_el.text or "").strip() if comment_el is not None else ""
    user_color = (infos.get("UserColor") if infos is not None else None) or ""
    song_length = _optional_float(infos.get("SongLength") if infos is not None else None)
    scan_phase = _optional_float(scan.get("Phase") if scan is not None else None)
    bpm = vdj_bpm_to_actual(
        _optional_float(scan.get("Bpm") if scan is not None else None)
    )
    if bpm is None:
        bpm = vdj_bpm_to_actual(
            _optional_float(tags.get("Bpm") if tags is not None else None)
        )

    cue_count = 0
    loop_count = 0
    has_beatgrid = False
    beatgrid_pos: Optional[float] = None
    points: list[CuePoint] = []

    for poi in song.findall("Poi"):
        poi_type = (poi.get("Type") or "").lower()
        if poi_type == "beatgrid":
            has_beatgrid = True
            beatgrid_pos = _optional_float(poi.get("Pos"))
            continue

        if poi_type == "cue" and poi.get("Num", "0") != "0":
            cue_count += 1
        elif poi_type == "loop":
            loop_count += 1
        else:
            continue

        color = poi.get("Color", "") or ""
        num = poi.get("Num", "0") or "0"
        pos = _optional_float(poi.get("Pos")) or 0.0
        points.append(
            CuePoint(
                kind="loop" if poi_type == "loop" else "cue",
                name=_format_point_name(poi_type, poi.get("Name", "") or "", num),
                pos=pos,
                num=num,
                color=color,
                color_name=VDJ_COLOR_NAMES.get(color, "unknown"),
                size=poi.get("Size"),
                slot=poi.get("Slot"),
            )
        )

    points.sort(key=lambda p: (p.pos, 0 if p.kind == "cue" else 1, p.num))
    key = song_key_from_element(song)

    return CueSummary(
        cue_count=cue_count,
        loop_count=loop_count,
        has_beatgrid=has_beatgrid,
        title=title,
        author=author,
        in_database=True,
        song_length=song_length,
        beatgrid_pos=beatgrid_pos,
        scan_phase=scan_phase,
        bpm=bpm,
        comment=comment,
        key=key,
        camelot=key_to_camelot(key) or "",
        user_color=str(user_color),
        points=points,
    )


def summarize_cues(
    audio_path: str | Path,
    database_path: Path | None = None,
) -> CueSummary:
    db = database_path or VDJ_DATABASE
    if not db.is_file():
        return _empty_cue_summary()
    try:
        song = load_song_element(db, _normalize_path(audio_path))
    except (KeyError, OSError):
        try:
            song = load_song_element(db, normalize_database_path(str(audio_path)))
        except (KeyError, OSError):
            return _empty_cue_summary()
    return _cue_summary_from_song_element(song)


def summarize_cues_for_paths(
    paths: Iterable[str | Path],
    database_path: Path | None = None,
) -> dict[str, CueSummary]:
    """One database.xml read + one Song scan for many tracks.

    Add Cues used to call summarize_cues per file, which re-read a ~35MB
    VirtualDJ database for every row and froze the UI after AutoCue.
    """
    wanted: dict[str, str] = {}
    originals: list[str] = []
    for raw in paths:
        key = str(raw)
        if not key:
            continue
        originals.append(key)
        for variant in _path_lookup_keys(key):
            wanted.setdefault(variant, key)

    out: dict[str, CueSummary] = {path: _empty_cue_summary() for path in originals}
    if not wanted:
        return out
    db = database_path or VDJ_DATABASE
    if not db.is_file():
        return out
    try:
        content = read_vdj_database_text(db)
    except OSError:
        return out

    remaining = set(out)
    for match in _SONG_OPEN_RE.finditer(content):
        if not remaining:
            break
        attrs = match.group("attrs")
        path_match = _FILEPATH_RE.search(attrs)
        if path_match is None:
            continue
        song_path = normalize_database_path(_unescape_xml_attr(path_match.group(1)))
        orig = wanted.get(song_path)
        if orig is None or orig not in remaining:
            continue
        xml = _song_xml_from_open(content, match)
        if not xml:
            continue
        try:
            song = ET.fromstring(xml)
        except ET.ParseError:
            continue
        out[orig] = _cue_summary_from_song_element(song)
        remaining.discard(orig)
    return out


def _find_db_path_variant(
    audio_path: Path,
    database_path: Path,
) -> Optional[str]:
    """Return the FilePath string as stored in the DB, if present."""
    candidates = [
        _normalize_path(audio_path),
        normalize_database_path(str(audio_path)),
        normalize_database_path(unicodedata.normalize("NFC", str(audio_path))),
        normalize_database_path(unicodedata.normalize("NFD", str(audio_path))),
    ]
    content = read_vdj_database_text(database_path)
    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if _find_song_span(content, candidate) is not None:
            return candidate
    return None


def backup_database(database_path: Path | None = None) -> str:
    db = database_path or VDJ_DATABASE
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{db}.backup.{timestamp}.music-sorter"
    shutil.copy2(db, backup_path)
    return backup_path


def _move_audio_and_retarget_db(
    source: Path,
    dest: Path,
    *,
    db: Path,
    cues: CueSummary,
    dry_run: bool,
    allow_vdj_running: bool,
    create_backup: bool,
    require_cued: bool,
    reuse_existing: bool = False,
) -> SortResult:
    if require_cued and not cues.is_cued:
        raise PermissionError(
            "Track is not cued in VirtualDJ (no manual cue points)."
        )

    if dest.exists() and not reuse_existing:
        raise FileExistsError(f"Destination already has a file named {dest.name}")

    if dest.exists() and reuse_existing:
        return _reuse_existing_dest_and_drop_source(
            source,
            dest,
            db=db,
            cues=cues,
            dry_run=dry_run,
            allow_vdj_running=allow_vdj_running,
            create_backup=create_backup,
        )

    stems_source = Path(f"{source}.vdjstems")
    stems_dest = Path(f"{dest}.vdjstems")
    stems_exists = stems_source.is_file()

    if dry_run:
        return SortResult(
            source_path=str(source),
            dest_path=str(dest),
            stems_moved=stems_exists,
            database_updated=cues.in_database,
            database_backup=None,
            cues=cues,
            dry_run=True,
        )

    if is_virtualdj_running() and not allow_vdj_running:
        raise RuntimeError(
            "VirtualDJ is running. Close it before moving so it cannot overwrite "
            "database.xml on exit. Pass allow_vdj_running=true only if you know "
            "what you are doing."
        )

    backup: Optional[str] = None
    db_path_in_db = _find_db_path_variant(source, db) if db.is_file() else None

    if db_path_in_db and create_backup:
        backup = backup_database(db)

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(dest))
    stems_moved = False
    if stems_exists:
        if stems_dest.exists():
            stems_dest.unlink()
        shutil.move(str(stems_source), str(stems_dest))
        stems_moved = True

    database_updated = False
    if db_path_in_db:
        try:
            with vdj_db_write():
                relocate_song_filepath_in_database(
                    db,
                    db_path_in_db,
                    _normalize_path(dest),
                    validate=True,
                )
            database_updated = True
        except Exception:
            try:
                if dest.is_file() and not source.exists():
                    shutil.move(str(dest), str(source))
                if stems_moved and stems_dest.is_file() and not stems_source.exists():
                    shutil.move(str(stems_dest), str(stems_source))
            except OSError:
                pass
            raise

    return SortResult(
        source_path=str(source),
        dest_path=str(dest),
        stems_moved=stems_moved,
        database_updated=database_updated,
        database_backup=backup,
        cues=cues,
        dry_run=False,
    )



def _reuse_existing_dest_and_drop_source(
    source: Path,
    dest: Path,
    *,
    db: Path,
    cues: CueSummary,
    dry_run: bool,
    allow_vdj_running: bool,
    create_backup: bool,
) -> SortResult:
    """Dest filename already exists: use that file, retarget FilePath, drop source."""
    if dry_run:
        return SortResult(
            source_path=str(source),
            dest_path=str(dest),
            stems_moved=False,
            database_updated=cues.in_database,
            database_backup=None,
            cues=cues,
            dry_run=True,
            dest_reused=True,
        )
    if is_virtualdj_running() and not allow_vdj_running:
        raise RuntimeError(
            "VirtualDJ is running. Close it before sorting so it cannot overwrite "
            "database.xml on exit. Pass allow_vdj_running=true only if you know "
            "what you are doing."
        )

    backup: Optional[str] = None
    db_path_in_db = _find_db_path_variant(source, db) if db.is_file() else None
    dest_already_in_db = bool(db.is_file() and _find_db_path_variant(dest, db))
    database_updated = False
    if db_path_in_db and not dest_already_in_db:
        if create_backup:
            backup = backup_database(db)
        with vdj_db_write():
            relocate_song_filepath_in_database(
                db,
                db_path_in_db,
                _normalize_path(dest),
                validate=True,
            )
        database_updated = True
    elif dest_already_in_db or cues.in_database:
        database_updated = True

    same_inode = False
    try:
        same_inode = source.exists() and dest.exists() and source.stat().st_ino == dest.stat().st_ino
    except OSError:
        same_inode = False
    if source.exists() and source.resolve() != dest.resolve():
        stems_source = Path(f"{source}.vdjstems")
        if same_inode:
            source.unlink(missing_ok=True)
        else:
            source.unlink(missing_ok=True)
        if stems_source.is_file() and not Path(f"{dest}.vdjstems").is_file():
            try:
                shutil.move(str(stems_source), str(Path(f"{dest}.vdjstems")))
            except OSError:
                pass
        elif stems_source.is_file():
            stems_source.unlink(missing_ok=True)

    return SortResult(
        source_path=str(source),
        dest_path=str(dest),
        stems_moved=False,
        database_updated=database_updated,
        database_backup=backup,
        cues=cues,
        dry_run=False,
        dest_reused=True,
    )


def cues_sorted_destination(relative_folder: str, filename: str) -> Path:
    """
    Mirror library relative path under Cues Sorted.

    Example: relative_folder='Chill/Mystical', file='track.flac'
      → .../Cues/Cues Sorted/Chill/Mystical/track.flac
    """
    rel = relative_folder.strip().strip("/")
    if not rel or ".." in Path(rel).parts:
        raise ValueError(f"Invalid Cues Sorted relative folder: {relative_folder!r}")
    dest = (CUES_SORTED / rel / filename).resolve()
    try:
        dest.relative_to(CUES_SORTED.resolve())
    except ValueError as exc:
        raise ValueError("Cues Sorted destination escapes Cues Sorted root") from exc
    return dest


def _copy_file_and_stems(source: Path, dest: Path, *, reuse_existing: bool = False) -> bool:
    """Copy audio (+ .vdjstems if present). Returns whether stems were copied."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        if reuse_existing:
            return False
        raise FileExistsError(f"Destination already has {dest.name} at {dest}")
    shutil.copy2(str(source), str(dest))
    stems_source = Path(f"{source}.vdjstems")
    stems_dest = Path(f"{dest}.vdjstems")
    if stems_source.is_file():
        if stems_dest.exists():
            stems_dest.unlink()
        shutil.copy2(str(stems_source), str(stems_dest))
        return True
    return False


def _remove_audio_and_stems(path: Path) -> None:
    """Best-effort delete of an audio file and its .vdjstems sidecar."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
    stems = Path(f"{path}.vdjstems")
    try:
        stems.unlink(missing_ok=True)
    except OSError:
        pass


def _normalize_sort_destinations(
    *,
    library_name: str,
    relative_folder: str,
    destinations: list[dict[str, str]] | None,
) -> list[tuple[str, str]]:
    """
    Build ordered unique (library, relative_folder) pairs.

    Prefer explicit ``destinations`` (multi-folder / multi-library picks).
    Fall back to legacy library_name + single relative_folder (House|Zouk|Both).
    """
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def _add(lib: str, rel: str) -> None:
        cleaned_rel = (rel or "").strip().strip("/")
        if not cleaned_rel:
            raise ValueError("Each destination needs a relative_folder")
        # Expand Both → House + Zouk for that folder path.
        for name in expand_library_mode(lib):
            key = (name, cleaned_rel)
            if key in seen:
                continue
            seen.add(key)
            pairs.append(key)

    if destinations:
        if not destinations:
            raise ValueError("destinations list is empty")
        for raw in destinations:
            lib = str(raw.get("library") or "").strip()
            rel = str(raw.get("relative_folder") or raw.get("path") or "").strip()
            if not lib:
                raise ValueError("Each destination needs a library (House or Zouk)")
            _add(lib, rel)
    else:
        if not relative_folder or not str(relative_folder).strip():
            raise ValueError("relative_folder is required when destinations is omitted")
        _add(library_name, relative_folder)

    if not pairs:
        raise ValueError("No sort destinations resolved")

    # Prefer Zouk as primary when present (main cued archive), then House,
    # then preserve remaining user order.
    def _rank(item: tuple[str, str]) -> tuple[int, int]:
        lib, _rel = item
        lib_rank = 0 if lib == "Zouk" else 1 if lib == "House" else 2
        return (lib_rank, pairs.index(item))

    return sorted(pairs, key=_rank)


def _build_library_destinations(
    library_mode: str,
    relative_folder: str,
    filename: str,
    *,
    create_missing: bool,
    destinations: list[dict[str, str]] | None = None,
) -> list[tuple[str, Path, str]]:
    """
    Resolve concrete file destinations.

    Returns list of (library_name, absolute_file_path, relative_folder).
    """
    pairs = _normalize_sort_destinations(
        library_name=library_mode,
        relative_folder=relative_folder,
        destinations=destinations,
    )
    dests: list[tuple[str, Path, str]] = []
    for lib, rel in pairs:
        dest_dir = resolve_destination(lib, rel, create=create_missing)
        if not dest_dir.is_dir():
            raise FileNotFoundError(
                f"Destination folder does not exist in {lib}: {rel}"
            )
        dests.append((lib, (dest_dir / filename).resolve(), rel))
    return dests


def sort_track(
    source_path: str | Path,
    *,
    library_name: str,
    relative_folder: str = "",
    destinations: list[dict[str, str]] | None = None,
    database_path: Path | None = None,
    ready_root: Path | None = None,
    dry_run: bool = False,
    allow_vdj_running: bool = False,
    create_backup: bool = True,
    also_cues_sorted: bool = True,
    lane: str | None = None,
) -> SortResult:
    """
    Move a Ready-for-Sort track into one or more House/Zouk folders and retarget VDJ cues.

    Destinations:
      - ``destinations=[{library, relative_folder}, ...]`` — multi-folder / multi-library
      - or legacy ``library_name`` ("House"|"Zouk"|"Both") + single ``relative_folder``

    Primary destination (first after Zouk-preferring sort) receives the move + VDJ
    FilePath retarget. Additional destinations get a file copy + VDJ Song clone.
    Cues Sorted archives under the primary relative folder.
    """
    db = Path(database_path) if database_path else VDJ_DATABASE
    source = Path(source_path).expanduser().resolve()
    ready = (ready_root or READY_FOR_SORT).resolve()
    library_mode = (library_name or "").strip() or "Both"

    if not source.is_file():
        raise FileNotFoundError(f"Source file not found: {source}")
    if source.suffix.lower() not in AUDIO_EXTENSIONS:
        raise ValueError(f"Not an audio file: {source.name}")

    source_kind = None
    for kind, root in (
        ("ready", ready),
        ("add_cues", ADD_CUES.resolve()),
        ("sets", SETS_ROOT.resolve()),
    ):
        try:
            source.relative_to(root)
            source_kind = kind
            break
        except ValueError:
            continue
    if source_kind is None:
        raise ValueError(
            f"Source must live under Add Cues, Ready for Sort, or Sets, got {source}"
        )
    keep_source = source_kind == "sets"

    # Create missing folders when multi-targeting or Both (House may lack Zouk nests).
    create_missing = (
        bool(destinations and len(destinations) > 1)
        or library_mode.lower() == "both"
        or (bool(destinations) and len({d.get("library") for d in destinations}) > 1)
    )
    library_dests = _build_library_destinations(
        library_mode,
        relative_folder,
        source.name,
        create_missing=create_missing,
        destinations=destinations,
    )
    primary_lib, primary_dest, primary_rel = library_dests[0]
    secondary = library_dests[1:]

    cues = summarize_cues(source, db)

    cues_sorted_dest: Optional[Path] = None
    cues_sorted_already = False
    if also_cues_sorted:
        cues_sorted_dest = cues_sorted_destination(primary_rel, source.name)
        cues_sorted_already = cues_sorted_dest.exists()

    library_dest_payload = [
        {
            "library": lib,
            "path": str(path),
            "relative_folder": rel,
        }
        for lib, path, rel in library_dests
    ]
    # Label mode for logs: multi-dest vs single legacy.
    if destinations and len(library_dests) > 1:
        library_mode = "multi"
    elif destinations and len(library_dests) == 1:
        library_mode = library_dests[0][0]
    else:
        library_mode = library_mode or primary_lib

    if dry_run:
        dry_set = _copy_cues_to_set_matches(
            source,
            database_path=db,
            allow_vdj_running=True,
            create_backup=False,
            dry_run=True,
        )
        return SortResult(
            source_path=str(source),
            dest_path=str(primary_dest),
            stems_moved=Path(f"{source}.vdjstems").is_file(),
            database_updated=cues.in_database,
            database_backup=None,
            cues=cues,
            dry_run=True,
            cues_sorted_path=str(cues_sorted_dest) if cues_sorted_dest else None,
            cues_sorted_copied=bool(cues_sorted_dest) and not cues_sorted_already,
            cues_sorted_already_present=cues_sorted_already,
            library_mode=library_mode,
            library_dests=library_dest_payload,
            sets_cues_copied=int(dry_set.get("copied") or 0),
            sets_cues_skipped=int(dry_set.get("skipped") or 0),
            sets_paths=list(dry_set.get("paths") or []),
        )

    # Dest filename already present: use that file (still Cues Sorted + color + FilePath).

    # Transactional multi-dest:
    # 1) Copy secondaries + Cues Sorted while source is still under Ready
    # 2) Move primary + retarget VDJ
    # 3) Clone Song entries for secondaries/archive
    # On any failure before step 2 completes, remove partial copies so Ready stays intact.
    secondary_copied: list[Path] = []
    cues_sorted_created = False
    try:
        for lib, sec_dest, sec_rel in secondary:
            try:
                if not sec_dest.exists():
                    _copy_file_and_stems(source, sec_dest, reuse_existing=True)
                    secondary_copied.append(sec_dest)
            except Exception as exc:
                raise RuntimeError(
                    f"Failed to copy into {lib}/{sec_rel} at {sec_dest} "
                    f"(source still at Ready): {exc}"
                ) from exc

        if also_cues_sorted and cues_sorted_dest is not None and not cues_sorted_dest.exists():
            try:
                _copy_file_and_stems(source, cues_sorted_dest, reuse_existing=True)
                cues_sorted_created = True
            except Exception as exc:
                raise RuntimeError(
                    f"Failed to copy into Cues Sorted at {cues_sorted_dest} "
                    f"(source still at Ready): {exc}"
                ) from exc

        # Push cues onto matching Sets/Pajamathon copies while the Ready file
        # still owns the Song entry. Failures here must not abort the sort.
        try:
            set_push = _copy_cues_to_set_matches(
                source,
                database_path=db,
                allow_vdj_running=allow_vdj_running,
                create_backup=False,
            )
        except Exception:
            set_push = {"copied": 0, "skipped": 0, "failed": 1, "paths": []}

        if keep_source:
            if not primary_dest.exists():
                _copy_file_and_stems(source, primary_dest, reuse_existing=True)
            result = SortResult(
                source_path=str(source),
                dest_path=str(primary_dest),
                stems_moved=Path(f"{source}.vdjstems").is_file(),
                database_updated=cues.in_database,
                database_backup=None,
                cues=cues,
                dry_run=False,
            )
            if cues.in_database:
                with vdj_db_write():
                    clone_song_entry_to_path(
                        db,
                        _normalize_path(source),
                        _normalize_path(primary_dest),
                        validate=True,
                        skip_if_exists=True,
                    )
                result.database_updated = True
        else:
            result = _move_audio_and_retarget_db(
                source,
                primary_dest,
                db=db,
                cues=cues,
                dry_run=False,
                allow_vdj_running=allow_vdj_running,
                create_backup=create_backup,
                require_cued=True,
                reuse_existing=True,
            )
    except Exception:
        for copied in secondary_copied:
            _remove_audio_and_stems(copied)
        if cues_sorted_created and cues_sorted_dest is not None:
            _remove_audio_and_stems(cues_sorted_dest)
        raise

    result.library_mode = library_mode
    result.library_dests = library_dest_payload
    result.sets_cues_copied = int(set_push.get("copied") or 0)
    result.sets_cues_skipped = int(set_push.get("skipped") or 0)
    result.sets_paths = list(set_push.get("paths") or [])

    # Clone VDJ Song for secondary library destinations (hard fail if source Song missing).
    for lib, sec_dest, sec_rel in secondary:
        if result.database_updated or cues.in_database:
            try:
                with vdj_db_write():
                    clone_song_entry_to_path(
                        db,
                        _normalize_path(primary_dest),
                        _normalize_path(sec_dest),
                        validate=True,
                        skip_if_exists=True,
                    )
            except KeyError as exc:
                raise RuntimeError(
                    f"Sorted into {primary_lib} at {primary_dest} and copied to "
                    f"{lib}/{sec_rel}, but failed to clone VirtualDJ cues for "
                    f"{sec_dest}: song entry missing for primary path"
                ) from exc

    dest_paths: list[Path] = [primary_dest, *[sec_dest for _lib, sec_dest, _rel in secondary]]
    if keep_source:
        dest_paths.append(source)

    if not also_cues_sorted or cues_sorted_dest is None:
        return _paint_sort_destinations(result, db, dest_paths, cues, lane=lane)

    result.cues_sorted_path = str(cues_sorted_dest)
    result.cues_sorted_already_present = (
        cues_sorted_dest.exists() and not cues_sorted_created
    )
    result.cues_sorted_copied = cues_sorted_created

    # Clone VDJ entry so the Cues Sorted copy keeps the same cues.
    if result.database_updated or cues.in_database:
        try:
            with vdj_db_write():
                clone_info = clone_song_entry_to_path(
                    db,
                    _normalize_path(primary_dest),
                    _normalize_path(cues_sorted_dest),
                    validate=True,
                    skip_if_exists=True,
                )
            result.cues_sorted_db_cloned = bool(
                clone_info.get("cloned") or clone_info.get("already_present")
            )
            if clone_info.get("already_present"):
                result.cues_sorted_already_present = True
        except KeyError as exc:
            raise RuntimeError(
                f"Sorted into library at {primary_dest} and copied to Cues Sorted, "
                f"but failed to clone VirtualDJ cues for {cues_sorted_dest}: "
                f"song entry missing for primary path"
            ) from exc

    dest_paths.append(cues_sorted_dest)
    return _paint_sort_destinations(result, db, dest_paths, cues, lane=lane)


def _paint_sort_destinations(
    result: SortResult,
    db: Path,
    dest_paths: list[Path],
    cues: CueSummary,
    lane: str | None = None,
) -> SortResult:
    """Paint the confirmed lane (or a mapped dest). Never write white."""
    if not (result.database_updated or cues.in_database):
        return result
    try:
        from song_lane_color import apply_lane_color_after_move

        painted = apply_lane_color_after_move(db, dest_paths, lane=lane)
        result.lane = str(painted.get("lane") or "")
        result.lane_color = str(painted.get("color") or "")
    except Exception:
        result.lane = ""
        result.lane_color = ""
    return result


def _trash_or_unlink(path: Path, *, to_trash: bool) -> None:
    """
    Move path to macOS Trash when to_trash=True; permanent unlink otherwise.

    When to_trash=True, Finder/osascript failure raises — never hard-unlinks.
    """
    if not path.exists():
        return
    if to_trash:
        # Escape backslashes and quotes for AppleScript string.
        posix = str(path).replace("\\", "\\\\").replace('"', '\\"')
        script = (
            'tell application "Finder"\n'
            f'  delete (POSIX file "{posix}" as alias)\n'
            "end tell"
        )
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return
        err = (result.stderr or result.stdout or "").strip() or f"exit {result.returncode}"
        raise RuntimeError(
            f"Could not move to Trash (file left in place): {path.name}. "
            f"Finder/osascript error: {err}"
        )
    path.unlink(missing_ok=True)


def _drop_path(path: Path, *, to_trash: bool) -> dict[str, Any]:
    """
    Remove one directory name.

    If the inode is shared (House/Zouk/inbox/set hard-link), unlink this
    name only. Finder Trash of a hard-link can take the other copies.
    """
    kept = 0
    try:
        if path.is_file():
            kept = max(0, path.stat().st_nlink - 1)
    except OSError:
        kept = 0
    if kept > 0:
        path.unlink(missing_ok=True)
        return {"unlink_only": True, "kept_hardlinks": kept, "to_trash": False}
    _trash_or_unlink(path, to_trash=to_trash)
    return {"unlink_only": False, "kept_hardlinks": 0, "to_trash": to_trash}


def _allowed_placement_roots() -> list[Path]:
    """House / Zouk / Cues Sorted / Sets (not Ready / Add Cues)."""
    roots = [p.resolve() for p in LIBRARIES.values()]
    roots.append(CUES_SORTED.resolve())
    try:
        roots.append(SETS_ROOT.resolve())
    except OSError:
        pass
    return roots


def _allowed_copy_cue_dest_roots() -> list[Path]:
    """Library/archive plus event crates (Sets/Pajamathon)."""
    roots = _allowed_placement_roots()
    try:
        roots.append(SETS_ROOT.resolve())
    except OSError:
        pass
    return roots


def _assert_under_roots(path: Path, roots: list[Path], detail: str) -> Path:
    audio = path.expanduser().resolve()
    for root in roots:
        try:
            audio.relative_to(root)
            return audio
        except ValueError:
            continue
    raise ValueError(detail)


def _assert_under_placement_roots(path: Path) -> Path:
    return _assert_under_roots(
        path,
        _allowed_placement_roots(),
        "This path must be under House/, Zouk/, Cues Sorted/, or Sets/",
    )


def _assert_under_copy_cue_dests(path: Path) -> Path:
    return _assert_under_roots(
        path,
        _allowed_copy_cue_dest_roots(),
        "Copy cues destination must be under House/, Zouk/, Cues Sorted/, or Sets/",
    )


def _assert_under_queue_roots(path: Path) -> Path:
    """Ready for Sort or Add Cues — the queues that own newly written cues."""
    audio = path.expanduser().resolve()
    roots = [
        READY_FOR_SORT.resolve(),
        ADD_CUES.resolve(),
        CUES_SORTED.resolve(),
        SETS_ROOT.resolve(),
        *[p.resolve() for p in LIBRARIES.values()],
    ]
    for root in roots:
        try:
            audio.relative_to(root)
            return audio
        except ValueError:
            continue
    raise ValueError(
        "Copy cues source must live under Ready, Add Cues, House, Zouk, "
        "Cues Sorted, or Sets"
    )


def _placement_label(path: Path) -> tuple[str, str]:
    try:
        sets_root = SETS_ROOT.resolve()
        rel = path.relative_to(sets_root).as_posix()
        event = Path(rel).parts[0] if Path(rel).parts else "Sets"
        return event, rel
    except ValueError:
        pass
    for root in _allowed_placement_roots():
        try:
            return root.name, path.relative_to(root).as_posix()
        except ValueError:
            continue
    return "unknown", path.name


def _song_span_for_path(
    content: str, audio: Path, raw_path: str | Path
) -> tuple[str, int, int]:
    candidates = [
        normalize_database_path(str(audio)),
        normalize_database_path(str(Path(raw_path))),
        _normalize_path(audio),
        normalize_database_path(unicodedata.normalize("NFC", str(audio))),
        normalize_database_path(unicodedata.normalize("NFD", str(audio))),
    ]
    seen: set[str] = set()
    for cand in candidates:
        if cand in seen:
            continue
        seen.add(cand)
        span = _find_song_span(content, cand)
        if span is not None:
            return cand, span[0], span[1]
    raise KeyError(f"Song not found in database: {audio}")


def _replace_manual_pois_in_song_xml(dest_xml: str, poi_tags: list[str]) -> str:
    """Swap dest cue/loop POIs for source tags. Keep FilePath, Tags, Scan, Comment."""
    cleaned = _POI_LINE_RE.sub(
        lambda match: "" if _is_manual_cue_or_loop_poi(match.group(0)) else match.group(0),
        dest_xml,
    )
    close_idx = cleaned.rfind("</Song>")
    if close_idx < 0:
        raise ValueError("Destination Song XML is missing </Song>")
    newline = "\r\n" if "\r\n" in dest_xml else "\n"
    body = cleaned[:close_idx].rstrip(" \t")
    if not body.endswith("\n"):
        body += newline
    insertion = "".join(poi_tags)
    if insertion and not insertion.endswith(("\n", "\r")):
        insertion += newline
    return body + insertion + cleaned[close_idx:]


def remove_song_entry_from_database(
    audio_path: str | Path,
    *,
    database_path: Path | None = None,
    create_backup: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Remove the entire VirtualDJ <Song> block for this file path.

    Adjusts integrity stats so a single intentional song delete is allowed
    (default VDJ safety rejects any song_count drop).
    """
    audio = Path(audio_path).expanduser().resolve()
    db = Path(database_path) if database_path else VDJ_DATABASE
    if not db.is_file():
        raise FileNotFoundError(f"VDJ database not found: {db}")

    content = read_vdj_database_text(db)
    candidates = [
        normalize_database_path(str(audio)),
        normalize_database_path(str(Path(audio_path))),
    ]
    span = None
    path_in_db: Optional[str] = None
    for cand in candidates:
        span = _find_song_span(content, cand)
        if span is not None:
            path_in_db = cand
            break
    if span is None or path_in_db is None:
        return {
            "removed_from_db": False,
            "path_in_db": None,
            "database_backup": None,
            "dry_run": dry_run,
            "reason": "not_in_database",
        }

    start, end = span
    # Swallow trailing newline so we don't leave a blank line gap.
    if end < len(content) and content.startswith("\r\n", end):
        end += 2
    elif end < len(content) and content[end] == "\n":
        end += 1

    removed_chunk = content[start:end]
    removed_cues = (
        removed_chunk.count('Type="cue"')
        + removed_chunk.count("Type='cue'")
        + removed_chunk.count('Type="loop"')
        + removed_chunk.count("Type='loop'")
    )

    if dry_run:
        return {
            "removed_from_db": True,
            "path_in_db": path_in_db,
            "cue_loop_pois_removed": removed_cues,
            "database_backup": None,
            "dry_run": True,
        }

    backup: Optional[str] = None
    if create_backup:
        backup = backup_database(db)

    original_stats = _lightweight_rewrite_stats(content, db)
    # Pre-adjust expected stats so validate_database_replacement allows this.
    removed_bytes = len(removed_chunk.encode("utf-8"))
    adjusted_stats = {
        "size_bytes": max(0, int(original_stats["size_bytes"]) - removed_bytes),
        "song_count": max(0, int(original_stats["song_count"]) - 1),
        "cue_loop_count": max(0, int(original_stats["cue_loop_count"]) - removed_cues),
    }

    # Size check uses 75% floor on large DBs — single song is tiny; keep size
    # close enough by using the true original size and only tightening song/cues.
    # validate: song_count must not *drop below* adjusted; candidate will match.
    # For size: candidate ≈ original - removed; if original >= 1MB and we only
    # remove one song, size stays well above 75%.
    with vdj_db_write():
        atomic_replace_database_parts(
            db,
            (content[:start], content[end:]),
            original_stats=adjusted_stats,
            stats_fn=_lightweight_content_stats,
        )

    return {
        "removed_from_db": True,
        "path_in_db": path_in_db,
        "cue_loop_pois_removed": removed_cues,
        "database_backup": backup,
        "dry_run": False,
    }


def delete_library_placement(
    placement_path: str | Path,
    *,
    database_path: Path | None = None,
    dry_run: bool = False,
    to_trash: bool = True,
    allow_vdj_running: bool = False,
    create_backup: bool = True,
) -> dict[str, Any]:
    """
    Delete a House/Zouk/Cues Sorted/Sets copy and remove its VirtualDJ Song.

    Used from the Already-in-library card. Does not touch the Ready / Add Cues
    source track. If the audio is already gone (Trash / leftover assemble
    path), still drop leftover stems and the VirtualDJ Song for that path.
    """
    source = _assert_under_placement_roots(Path(placement_path))
    if source.suffix.lower() not in AUDIO_EXTENSIONS:
        raise ValueError(f"Not an audio file: {source.name}")
    missing_file = not source.is_file()

    db = Path(database_path) if database_path else VDJ_DATABASE
    stems = Path(f"{source}.vdjstems")
    files_to_remove: list[str] = []
    if source.is_file():
        files_to_remove.append(str(source))
    if stems.is_file():
        files_to_remove.append(str(stems))

    vdj_open = is_virtualdj_running()
    if vdj_open and not dry_run and not allow_vdj_running and files_to_remove:
        raise RuntimeError(
            "VirtualDJ is running. Close it before deleting library placements, "
            "or pass allow_vdj_running=true (not recommended)."
        )

    cues_before = summarize_cues(source, db)
    if (
        vdj_open
        and not dry_run
        and not allow_vdj_running
        and not files_to_remove
    ):
        db_preview = remove_song_entry_from_database(
            source, database_path=db, create_backup=False, dry_run=True
        )
        if db_preview.get("removed_from_db"):
            raise RuntimeError(
                "VirtualDJ is running. Close it before deleting library placements, "
                "or pass allow_vdj_running=true (not recommended)."
            )
        root_name, relative_path = _placement_label(source)
        return {
            "ok": True,
            "dry_run": False,
            "missing_file": missing_file,
            "path": str(source),
            "name": source.name,
            "root_name": root_name,
            "relative_path": relative_path,
            "removed_files": [],
            "to_trash": to_trash,
            "had_cues": cues_before.cue_count,
            "had_loops": cues_before.loop_count,
            "in_database": cues_before.in_database,
            "database": {**db_preview, "dry_run": False},
            "database_backup": None,
        }

    root_name, relative_path = _placement_label(source)
    try:
        kept_hardlinks = (
            max(0, source.stat().st_nlink - 1) if source.is_file() else 0
        )
    except OSError:
        kept_hardlinks = 0
    unlink_only = kept_hardlinks > 0

    if dry_run:
        db_preview = remove_song_entry_from_database(
            source, database_path=db, create_backup=False, dry_run=True
        )
        return {
            "ok": True,
            "dry_run": True,
            "missing_file": missing_file,
            "path": str(source),
            "name": source.name,
            "root_name": root_name,
            "relative_path": relative_path,
            "removed_files": files_to_remove,
            "to_trash": False if unlink_only else to_trash,
            "had_cues": cues_before.cue_count,
            "had_loops": cues_before.loop_count,
            "in_database": cues_before.in_database,
            "database": db_preview,
            "kept_hardlinks": kept_hardlinks,
            "unlink_only": unlink_only,
        }

    # Drop this name first; only then remove the Song for this path.
    # Shared inodes are unlinked (never Finder-trashed).
    kept_hardlinks = 0
    unlink_only = False
    for path_str in files_to_remove:
        dropped = _drop_path(Path(path_str), to_trash=to_trash)
        if dropped["unlink_only"]:
            unlink_only = True
            kept_hardlinks = max(kept_hardlinks, int(dropped["kept_hardlinks"]))

    db_result = remove_song_entry_from_database(
        source,
        database_path=db,
        create_backup=create_backup,
        dry_run=False,
    )

    return {
        "ok": True,
        "dry_run": False,
        "missing_file": missing_file,
        "path": str(source),
        "name": source.name,
        "root_name": root_name,
        "relative_path": relative_path,
        "kept_hardlinks": kept_hardlinks,
        "unlink_only": unlink_only,
        "removed_files": files_to_remove,
        "to_trash": False if unlink_only else to_trash,
        "had_cues": cues_before.cue_count,
        "had_loops": cues_before.loop_count,
        "in_database": cues_before.in_database,
        "database": db_result,
        "database_backup": db_result.get("database_backup"),
    }


_USER2_ATTR_RE = re.compile(r'\bUser2\s*=\s*"([^"]*)"', re.IGNORECASE)


def _user2_from_song_xml(song_xml: str) -> str:
    match = _USER2_ATTR_RE.search(song_xml)
    if match is None:
        return ""
    return _unescape_xml_attr(match.group(1))


def _fill_display_fields_from_source(
    dest_xml: str,
    source_xml: str,
    source_path: Path,
) -> str:
    """Copy Directory Sort (User2) and title UserColor onto a Sets song.

    Does not overwrite a dest that already has a valid library User2 or UserColor.
    """
    from song_lane_color import classify_path, color_for_lane, current_user_color

    dest_user2 = normalize_user2_dest(_user2_from_song_xml(dest_xml))
    src_user2 = normalize_user2_dest(_user2_from_song_xml(source_xml))
    if not src_user2:
        src_user2 = normalize_user2_dest(directory_sort_label(str(source_path)))
    user2 = dest_user2 or src_user2 or None

    dest_color = current_user_color(dest_xml)
    src_color = current_user_color(source_xml)
    if not src_color:
        lane = classify_path(str(source_path))
        src_color = color_for_lane(lane) if lane else None
    color = dest_color or src_color or None

    if user2 is None and color is None:
        return dest_xml
    return patch_song_infos_and_user2(dest_xml, user_color=color, user2=user2)


def copy_display_fields_to_placement(
    source_path: str | Path,
    dest_path: str | Path,
    *,
    database_path: Path | None = None,
    dry_run: bool = False,
    allow_vdj_running: bool = False,
    create_backup: bool = False,
) -> dict[str, Any]:
    """Copy Directory Sort + title color from a Zouk/library original onto a Sets file."""
    source = _assert_under_queue_roots(Path(source_path))
    dest = _assert_under_copy_cue_dests(Path(dest_path))
    db = Path(database_path) if database_path else VDJ_DATABASE
    if is_virtualdj_running() and not dry_run and not allow_vdj_running:
        raise RuntimeError(
            "VirtualDJ is running. Close it before writing Directory Sort / color."
        )
    content = read_vdj_database_text(db)
    source_key, src_start, src_end = _song_span_for_path(content, source, source_path)
    dest_key, dest_start, dest_end = _song_span_for_path(content, dest, dest_path)
    new_dest = _fill_display_fields_from_source(
        content[dest_start:dest_end], content[src_start:src_end], source
    )
    payload = {
        "ok": True,
        "dry_run": dry_run,
        "source_path": str(source),
        "dest_path": str(dest),
        "user2": _user2_from_song_xml(new_dest),
        "user_color": None,
    }
    from song_lane_color import current_user_color

    payload["user_color"] = current_user_color(new_dest)
    if dry_run or new_dest == content[dest_start:dest_end]:
        payload["updated"] = False
        return payload
    backup = None
    if create_backup:
        backup = backup_database(db)
    with vdj_db_write():
        rewrite_song_xml_in_database(db, dest_key, new_dest, validate=False)
    payload["updated"] = True
    payload["database_backup"] = backup
    return payload


def copy_cues_to_placement(
    source_path: str | Path,
    dest_path: str | Path,
    *,
    overwrite: bool = False,
    database_path: Path | None = None,
    dry_run: bool = False,
    allow_vdj_running: bool = False,
    create_backup: bool = True,
) -> dict[str, Any]:
    """
    Copy VirtualDJ cue/loop markers from a Ready/Add Cues track onto an
    existing House/Zouk/Cues Sorted file. Audio files are not moved.

    If the destination has no Song entry, clone the source Song under the
    dest FilePath. If it already has a Song, replace only its manual cue/loop
    POIs and keep dest Tags/Scan/beatgrid/Comment/FilePath.
    """
    source = _assert_under_queue_roots(Path(source_path))
    dest = _assert_under_copy_cue_dests(Path(dest_path))
    if source == dest:
        raise ValueError("Source and destination are the same file")
    if not source.is_file():
        raise FileNotFoundError(f"Source file not found: {source}")
    if not dest.is_file():
        raise FileNotFoundError(f"Placement file not found: {dest}")
    if source.suffix.lower() not in AUDIO_EXTENSIONS:
        raise ValueError(f"Not an audio file: {source.name}")
    if dest.suffix.lower() not in AUDIO_EXTENSIONS:
        raise ValueError(f"Not an audio file: {dest.name}")

    db = Path(database_path) if database_path else VDJ_DATABASE
    if not db.is_file():
        raise FileNotFoundError(f"VDJ database not found: {db}")

    if is_virtualdj_running() and not dry_run and not allow_vdj_running:
        raise RuntimeError(
            "VirtualDJ is running. Close it before copying cues onto a library "
            "file, or pass allow_vdj_running=true (not recommended)."
        )

    source_cues = summarize_cues(source, db)
    if not source_cues.in_database:
        raise ValueError(f"Source track is not in the VirtualDJ database: {source}")
    if source_cues.cue_count < 1 and source_cues.loop_count < 1:
        raise ValueError("Source has no VirtualDJ cue points or loops to copy")

    dest_cues = summarize_cues(dest, db)
    if not dest_cues.in_database:
        dest_variant = _find_db_path_variant(dest, db)
        if dest_variant:
            dest_cues = summarize_cues(dest_variant, db)
    dest_has_markers = dest_cues.cue_count > 0 or dest_cues.loop_count > 0
    if dest_has_markers and not overwrite:
        raise ValueError(
            f"Destination already has {dest_cues.cue_count} cue(s)"
            + (f" and {dest_cues.loop_count} loop(s)" if dest_cues.loop_count else "")
            + ". Pass overwrite=true to replace them."
        )

    root_name, relative_path = _placement_label(dest)
    mode = "injected" if dest_cues.in_database else "cloned"
    payload: dict[str, Any] = {
        "ok": True,
        "dry_run": dry_run,
        "mode": mode,
        "source_path": str(source),
        "dest_path": str(dest),
        "name": dest.name,
        "root_name": root_name,
        "relative_path": relative_path,
        "copied_cues": source_cues.cue_count,
        "copied_loops": source_cues.loop_count,
        "overwrote": dest_has_markers,
        "dest_was_cued": dest_has_markers,
        "dest_had_cues": dest_cues.cue_count,
        "dest_had_loops": dest_cues.loop_count,
        "dest_in_database": dest_cues.in_database,
        "database_backup": None,
    }

    if dry_run:
        return payload

    backup: Optional[str] = None
    if create_backup:
        backup = backup_database(db)
        payload["database_backup"] = backup

    with vdj_db_write():
        content = read_vdj_database_text(db)
        source_key, src_start, src_end = _song_span_for_path(
            content, source, source_path
        )
        source_xml = content[src_start:src_end]
        poi_tags = list(iter_manual_poi_tags(source_xml))
        if not poi_tags:
            raise ValueError("Source has no VirtualDJ cue points to copy")

        if dest_cues.in_database:
            dest_key, dest_start, dest_end = _song_span_for_path(
                content, dest, dest_path
            )
            dest_xml = content[dest_start:dest_end]
            new_dest_xml = _replace_manual_pois_in_song_xml(dest_xml, poi_tags)
            new_dest_xml = _fill_display_fields_from_source(
                new_dest_xml, source_xml, source
            )
            rewrite_song_xml_in_database(
                db, dest_key, new_dest_xml, validate=True
            )
        else:
            clone_song_entry_to_path(
                db,
                source_key,
                _normalize_path(dest),
                validate=True,
                skip_if_exists=True,
            )

    after = summarize_cues(dest, db)
    payload["dest_cues"] = after.to_dict()
    payload["dest_is_cued"] = after.is_cued
    return payload


def copy_cues_to_placements(
    source_path: str | Path,
    dest_paths: list[str | Path],
    *,
    overwrite: bool = False,
    database_path: Path | None = None,
    dry_run: bool = False,
    allow_vdj_running: bool = False,
    create_backup: bool = True,
) -> dict[str, Any]:
    """
    Copy Ready/Add Cues markers onto every given library/archive/Sets copy.

    One database backup, then each dest is written independently. Destinations
    that already have cues/loops are skipped unless overwrite=True.
    """
    source = _assert_under_queue_roots(Path(source_path))
    if not dest_paths:
        raise ValueError("No destination copies given")

    seen: set[str] = set()
    dests: list[Path] = []
    for raw in dest_paths:
        dest = _assert_under_copy_cue_dests(Path(raw))
        key = str(dest)
        if key in seen:
            continue
        seen.add(key)
        dests.append(dest)
    if not dests:
        raise ValueError("No destination copies given")

    db = Path(database_path) if database_path else VDJ_DATABASE
    if is_virtualdj_running() and not dry_run and not allow_vdj_running:
        raise RuntimeError(
            "VirtualDJ is running. Close it before copying cues onto library "
            "files, or pass allow_vdj_running=true (not recommended)."
        )

    source_cues = summarize_cues(source, db)
    backup: Optional[str] = None
    if create_backup and not dry_run:
        backup = backup_database(db)

    results: list[dict[str, Any]] = []
    copied = 0
    skipped = 0
    failed = 0
    for dest in dests:
        try:
            item = copy_cues_to_placement(
                source,
                dest,
                overwrite=overwrite,
                database_path=db,
                dry_run=dry_run,
                allow_vdj_running=allow_vdj_running,
                create_backup=False,
            )
            copied += 1
            results.append(
                {
                    "ok": True,
                    "dest_path": str(dest),
                    "root_name": item.get("root_name"),
                    "relative_path": item.get("relative_path"),
                    "mode": item.get("mode"),
                    "overwrote": item.get("overwrote"),
                }
            )
        except ValueError as exc:
            message = str(exc)
            if "already has" in message.lower():
                skipped += 1
                status = "skipped"
            else:
                failed += 1
                status = "failed"
            results.append(
                {
                    "ok": False,
                    "dest_path": str(dest),
                    "error": message,
                    "status": status,
                }
            )
        except Exception as exc:
            failed += 1
            results.append(
                {
                    "ok": False,
                    "dest_path": str(dest),
                    "error": str(exc),
                    "status": "failed",
                }
            )

    return {
        "ok": failed == 0,
        "dry_run": dry_run,
        "source_path": str(source),
        "copied": copied,
        "skipped": skipped,
        "failed": failed,
        "results": results,
        "copied_cues": source_cues.cue_count,
        "copied_loops": source_cues.loop_count,
        "database_backup": backup,
        "overwrite": overwrite,
    }


_SET_INDEX_RE = re.compile(r"^(\d{1,4})\.\s")
# "01 - Title", "01. Title", "01) Title", or zero-padded "01 Title"
_LEADING_TRACK_NUM_RE = re.compile(r"^(?:\d+\s*[-.)]\s*|0\d\s+)")


def pajamathon_event_folder(sets_root: Path | None = None) -> Path:
    root = (sets_root or SETS_ROOT).expanduser().resolve()
    if root.is_dir():
        for child in sorted(root.iterdir()):
            if child.is_dir() and is_pajamathon_event(child.name):
                return child
    return root / "Pajamathon 2026"


def next_set_track_index(folder: Path) -> int:
    highest = 0
    if folder.is_dir():
        for path in folder.iterdir():
            if not path.is_file():
                continue
            match = _SET_INDEX_RE.match(path.name)
            if match:
                highest = max(highest, int(match.group(1)))
    return highest + 1


def _set_copy_basename(source: Path) -> str:
    stem = _LEADING_TRACK_NUM_RE.sub("", source.stem).strip() or source.stem
    return f"{stem}{source.suffix}"


MUST_PLAY_FOLDER = "Must Play"


def pajamathon_must_play_folder(sets_root: Path | None = None) -> Path:
    return pajamathon_event_folder(sets_root) / MUST_PLAY_FOLDER


def add_track_to_must_play(
    source_path: str | Path,
    *,
    sets_root: Path | None = None,
    database_path: Path | None = None,
    dry_run: bool = False,
    allow_vdj_running: bool = False,
    create_backup: bool = True,
) -> dict[str, Any]:
    """Move/copy a queue track into Sets/Pajamathon/Must Play."""
    source = _assert_under_queue_roots(Path(source_path))
    if not source.is_file():
        raise FileNotFoundError(f"Source file not found: {source}")
    if source.suffix.lower() not in AUDIO_EXTENSIONS:
        raise ValueError(f"Not an audio file: {source.name}")

    root = (sets_root or SETS_ROOT).expanduser().resolve()
    event = pajamathon_event_folder(root)
    folder = event / MUST_PLAY_FOLDER
    db = Path(database_path) if database_path else VDJ_DATABASE

    try:
        source.resolve().relative_to(folder.resolve())
        return {
            "ok": True,
            "already_exists": True,
            "moved": False,
            "dry_run": dry_run,
            "source_path": str(source),
            "dest_path": str(source),
            "event": event.name,
            "relative_path": f"{event.name}/{MUST_PLAY_FOLDER}/{source.name}",
        }
    except ValueError:
        pass

    for hit in find_set_matches(source.name, sets_root=root):
        rel = str(hit.get("relative_path") or hit.get("path") or "").lower()
        if "must play" in rel:
            return {
                "ok": True,
                "already_exists": True,
                "moved": False,
                "dry_run": dry_run,
                "source_path": str(source),
                "dest_path": str(hit.get("path") or ""),
                "event": event.name,
                "relative_path": str(hit.get("relative_path") or ""),
                "existing": hit,
            }

    in_event = False
    try:
        source.resolve().relative_to(event.resolve())
        in_event = True
    except ValueError:
        pass

    if in_event:
        dest = folder / source.name
        if dest.exists() and dest.resolve() != source.resolve():
            dest = folder / f"{source.stem} copy{source.suffix}"
    else:
        index = next_set_track_index(folder)
        dest = folder / f"{index:03d}. {_set_copy_basename(source)}"
        while dest.exists():
            index += 1
            dest = folder / f"{index:03d}. {_set_copy_basename(source)}"

    payload: dict[str, Any] = {
        "ok": True,
        "already_exists": False,
        "moved": in_event,
        "dry_run": dry_run,
        "source_path": str(source),
        "dest_path": str(dest),
        "event": event.name,
        "relative_path": f"{event.name}/{MUST_PLAY_FOLDER}/{dest.name}",
    }
    if dry_run:
        return payload

    folder.mkdir(parents=True, exist_ok=True)
    if in_event:
        cues = summarize_cues(source, db)
        moved = _move_audio_and_retarget_db(
            source,
            dest,
            db=db,
            cues=cues,
            dry_run=False,
            allow_vdj_running=allow_vdj_running,
            create_backup=create_backup,
            require_cued=False,
        )
        payload["dest_path"] = moved.dest_path
        payload["database_backup"] = moved.database_backup
        payload["copied_cues"] = cues.cue_count
        payload["copied_loops"] = cues.loop_count
        return payload

    source_cues = summarize_cues(source, db)
    _copy_file_and_stems(source, dest)
    payload["copied_cues"] = source_cues.cue_count
    payload["copied_loops"] = source_cues.loop_count
    if source_cues.cue_count > 0 or source_cues.loop_count > 0:
        copied = copy_cues_to_placement(
            source,
            dest,
            overwrite=False,
            database_path=db,
            dry_run=False,
            allow_vdj_running=allow_vdj_running,
            create_backup=create_backup,
        )
        payload["database_backup"] = copied.get("database_backup")
    return payload


def add_track_to_event_set(
    source_path: str | Path,
    *,
    event_name: str = "",
    sets_root: Path | None = None,
    database_path: Path | None = None,
    dry_run: bool = False,
    allow_vdj_running: bool = False,
    create_backup: bool = True,
) -> dict[str, Any]:
    """
    Copy a Ready/Add Cues track into Sets/Pajamathon (audio + stems + VDJ cues).

    Ready/Add Cues stays put. Used when a cued track is missing from the event crate.
    """
    source = _assert_under_queue_roots(Path(source_path))
    if not source.is_file():
        raise FileNotFoundError(f"Source file not found: {source}")
    if source.suffix.lower() not in AUDIO_EXTENSIONS:
        raise ValueError(f"Not an audio file: {source.name}")

    root = (sets_root or SETS_ROOT).expanduser().resolve()
    if event_name.strip():
        from .playlist_assemble import event_folder_name

        folder = root / event_folder_name(event_name)
    else:
        folder = pajamathon_event_folder(root)

    for hit in find_set_matches(source.name, sets_root=root):
        event = str(hit.get("event") or hit.get("root_name") or "")
        if is_pajamathon_event(event) or folder.name.lower() in event.lower():
            return {
                "ok": True,
                "already_exists": True,
                "dry_run": dry_run,
                "source_path": str(source),
                "dest_path": str(hit.get("path") or ""),
                "event": event,
                "relative_path": str(hit.get("relative_path") or ""),
                "existing": hit,
                "copied_cues": 0,
                "copied_loops": 0,
                "stems_copied": False,
                "database_backup": None,
            }

    index = next_set_track_index(folder)
    dest = folder / f"{index:03d}. {_set_copy_basename(source)}"
    while dest.exists():
        index += 1
        dest = folder / f"{index:03d}. {_set_copy_basename(source)}"

    source_cues = summarize_cues(source, database_path)
    payload: dict[str, Any] = {
        "ok": True,
        "dry_run": dry_run,
        "source_path": str(source),
        "dest_path": str(dest),
        "event": folder.name,
        "relative_path": f"{folder.name}/{dest.name}",
        "index": index,
        "copied_cues": 0,
        "copied_loops": 0,
        "stems_copied": Path(f"{source}.vdjstems").is_file(),
        "database_backup": None,
    }
    if dry_run:
        payload["copied_cues"] = source_cues.cue_count
        payload["copied_loops"] = source_cues.loop_count
        return payload

    _copy_file_and_stems(source, dest)
    if source_cues.cue_count > 0 or source_cues.loop_count > 0:
        copied = copy_cues_to_placement(
            source,
            dest,
            overwrite=False,
            database_path=database_path,
            dry_run=False,
            allow_vdj_running=allow_vdj_running,
            create_backup=create_backup,
        )
        payload["database_backup"] = copied.get("database_backup")
        payload["cue_mode"] = copied.get("mode")
        payload["copied_cues"] = int(copied.get("copied_cues") or 0)
        payload["copied_loops"] = int(copied.get("copied_loops") or 0)
    return payload


def _copy_cues_to_set_matches(
    source: Path,
    *,
    database_path: Path,
    allow_vdj_running: bool,
    create_backup: bool,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Clone Ready/Add Cues markers onto matching Sets/Pajamathon files."""
    copied = 0
    skipped = 0
    failed = 0
    paths: list[str] = []
    source_cues = summarize_cues(source, database_path)
    source_len = source_cues.song_length
    for hit in find_set_matches(source.name, sets_root=SETS_ROOT):
        event = str(hit.get("event") or hit.get("root_name") or "")
        if "pajamathon" not in event.lower():
            continue
        dest = Path(hit["path"])
        dest_cues = summarize_cues(dest, database_path)
        dest_len = dest_cues.song_length
        if (
            source_len
            and dest_len
            and abs(float(source_len) - float(dest_len)) > 2.0
        ):
            skipped += 1
            continue
        try:
            result = copy_cues_to_placement(
                source,
                dest,
                overwrite=False,
                database_path=database_path,
                dry_run=dry_run,
                allow_vdj_running=allow_vdj_running,
                create_backup=create_backup,
            )
        except (ValueError, FileNotFoundError, KeyError) as exc:
            if "already has" in str(exc).lower():
                skipped += 1
                paths.append(str(dest))
                continue
            failed += 1
            continue
        except Exception:
            failed += 1
            continue
        if result.get("ok"):
            copied += 1
            paths.append(str(dest))
    return {
        "copied": copied,
        "skipped": skipped,
        "failed": failed,
        "paths": paths,
    }


def best_cued_source_for_set_track(
    dest_path: str | Path,
    *,
    placement_index: Optional[dict[str, list[dict[str, str]]]] = None,
    database_path: Path | None = None,
) -> Optional[Path]:
    """Highest-cue Cues Sorted / House / Zouk copy of this Sets/Pajamathon file."""
    dest = Path(dest_path).expanduser()
    try:
        dest_resolved = str(dest.resolve())
    except OSError:
        dest_resolved = str(dest)
    names = [dest.name]
    stripped = _LEADING_TRACK_NUM_RE.sub("", dest.stem).strip()
    if stripped:
        names.append(f"{stripped}{dest.suffix}")
    seen: set[str] = set()
    candidates: list[Path] = []
    for name in names:
        hits = list(find_cues_sorted_matches(name, index=placement_index))
        hits.extend(find_library_matches(name, index=placement_index))
        for hit in hits:
            raw = str(hit.get("path") or "")
            if not raw:
                continue
            path = Path(raw).expanduser()
            try:
                key = str(path.resolve())
            except OSError:
                key = str(path)
            if key == dest_resolved or key in seen:
                continue
            seen.add(key)
            candidates.append(path)
    best: Optional[Path] = None
    best_n = 0
    for path in candidates:
        cues = summarize_cues(path, database_path)
        n = int(cues.cue_count or 0) + int(cues.loop_count or 0)
        if n > best_n:
            best_n = n
            best = path
    return best if best_n > 0 else None


def copy_cues_onto_uncued_set_track(
    dest_path: str | Path,
    *,
    overwrite: bool = False,
    database_path: Path | None = None,
    dry_run: bool = False,
    allow_vdj_running: bool = False,
    create_backup: bool = True,
    source_path: str | Path | None = None,
    placement_index: Optional[dict[str, list[dict[str, str]]]] = None,
) -> dict[str, Any]:
    """Clone cues from the library/Cues Sorted copy onto a Sets file that has none."""
    dest = _assert_under_copy_cue_dests(Path(dest_path))
    db = Path(database_path) if database_path else VDJ_DATABASE
    dest_cues = summarize_cues(dest, db)
    if (dest_cues.cue_count or dest_cues.loop_count) and not overwrite:
        return {
            "ok": True,
            "skipped": True,
            "reason": "already_cued",
            "dest_path": str(dest),
            "copied_cues": dest_cues.cue_count,
            "copied_loops": dest_cues.loop_count,
        }
    source = (
        Path(source_path).expanduser()
        if source_path
        else best_cued_source_for_set_track(
            dest, placement_index=placement_index, database_path=db
        )
    )
    if source is None:
        raise ValueError(f"No cued library/Cues Sorted source for {dest.name}")
    return copy_cues_to_placement(
        source,
        dest,
        overwrite=overwrite,
        database_path=db,
        dry_run=dry_run,
        allow_vdj_running=allow_vdj_running,
        create_backup=create_backup,
    )


def remove_from_ready_for_sort(
    source_path: str | Path,
    *,
    ready_root: Path | None = None,
    database_path: Path | None = None,
    dry_run: bool = False,
    to_trash: bool = True,
    allow_vdj_running: bool = False,
    create_backup: bool = True,
    remove_from_database: bool = True,
) -> dict[str, Any]:
    """
    Remove a track from Ready for Sort only — no House/Zouk/Cues Sorted placement.

    Deletes (or moves to Trash) the audio file and .vdjstems sidecar.
    Optionally removes the VirtualDJ Song entry so cues are not left orphaned.
    """
    source = Path(source_path).expanduser().resolve()
    ready = (ready_root or READY_FOR_SORT).resolve()
    db = Path(database_path) if database_path else VDJ_DATABASE

    if not source.is_file():
        raise FileNotFoundError(f"Source file not found: {source}")
    if source.suffix.lower() not in AUDIO_EXTENSIONS:
        raise ValueError(f"Not an audio file: {source.name}")

    try:
        source.relative_to(ready)
    except ValueError as exc:
        raise ValueError(
            f"Source must live under Ready for Sort ({ready}), got {source}"
        ) from exc

    if remove_from_database and is_virtualdj_running() and not dry_run and not allow_vdj_running:
        raise RuntimeError(
            "VirtualDJ is running. Close it before removing Ready tracks from the "
            "database, or pass allow_vdj_running=true (not recommended)."
        )

    stems = Path(f"{source}.vdjstems")
    removed = [str(source)]
    if stems.is_file():
        removed.append(str(stems))

    if dry_run:
        db_preview = None
        if remove_from_database:
            db_preview = remove_song_entry_from_database(
                source, database_path=db, create_backup=False, dry_run=True
            )
        return {
            "removed": removed,
            "to_trash": to_trash,
            "dry_run": True,
            "database": db_preview,
        }

    for path_str in removed:
        _trash_or_unlink(Path(path_str), to_trash=to_trash)

    db_result = None
    if remove_from_database:
        db_result = remove_song_entry_from_database(
            source,
            database_path=db,
            create_backup=create_backup,
            dry_run=False,
        )

    return {
        "removed": removed,
        "to_trash": to_trash,
        "dry_run": False,
        "name": source.name,
        "database": db_result,
        "database_backup": (db_result or {}).get("database_backup"),
    }


def delete_add_cues_track(
    source_path: str | Path,
    *,
    add_root: Path | None = None,
    sets_root: Path | None = None,
    database_path: Path | None = None,
    dry_run: bool = False,
    to_trash: bool = True,
    allow_vdj_running: bool = False,
    create_backup: bool = True,
) -> dict[str, Any]:
    """
    Permanently drop a cue-queue track: trash/delete audio + stems and
    remove its VirtualDJ <Song> entry (cues + loops for that path).

    Allowed roots: Add Cues (inbox) or Sets/Pajamathon* (event crate copies).
    House/Zouk library files are never deleted here.
    """
    source = Path(source_path).expanduser().resolve()
    root = (add_root or ADD_CUES).resolve()

    if not source.is_file():
        raise FileNotFoundError(f"Source file not found: {source}")
    if source.suffix.lower() not in AUDIO_EXTENSIONS:
        raise ValueError(f"Not an audio file: {source.name}")

    in_add_cues = True
    try:
        source.relative_to(root)
    except ValueError:
        in_add_cues = False
    set_copy = is_pajamathon_set_audio(source, sets_root=sets_root)
    if not in_add_cues and not set_copy:
        raise ValueError(
            "Can only delete Add Cues inbox files or Pajamathon set copies "
            f"(Add Cues: {root}), got {source}"
        )

    if is_virtualdj_running() and not dry_run and not allow_vdj_running:
        raise RuntimeError(
            "VirtualDJ is running. Close it before deleting Add Cues tracks, "
            "or pass allow_vdj_running=true (not recommended)."
        )

    db = Path(database_path) if database_path else VDJ_DATABASE
    stems = Path(f"{source}.vdjstems")
    files_to_remove = [str(source)]
    if stems.is_file():
        files_to_remove.append(str(stems))

    cues_before = summarize_cues(source, db)
    # Shared inode with House/Zouk/inbox: drop this name only. Finder Trash
    # on a hard-link can take the library copy with it.
    try:
        kept_hardlinks = max(0, source.stat().st_nlink - 1)
    except OSError:
        kept_hardlinks = 0
    unlink_only = kept_hardlinks > 0

    if dry_run:
        db_preview = remove_song_entry_from_database(
            source, database_path=db, create_backup=False, dry_run=True
        )
        return {
            "ok": True,
            "dry_run": True,
            "path": str(source),
            "name": source.name,
            "removed_files": files_to_remove,
            "to_trash": False if unlink_only else to_trash,
            "had_cues": cues_before.cue_count,
            "had_loops": cues_before.loop_count,
            "in_database": cues_before.in_database,
            "database": db_preview,
            "set_copy": set_copy,
            "kept_hardlinks": kept_hardlinks,
            "unlink_only": unlink_only,
        }

    # Remove this path first; only then drop the Song entry for this path.
    for path_str in files_to_remove:
        _drop_path(Path(path_str), to_trash=to_trash)

    db_result = remove_song_entry_from_database(
        source,
        database_path=db,
        create_backup=create_backup,
        dry_run=False,
    )

    return {
        "ok": True,
        "dry_run": False,
        "path": str(source),
        "name": source.name,
        "removed_files": files_to_remove,
        "to_trash": False if unlink_only else to_trash,
        "had_cues": cues_before.cue_count,
        "had_loops": cues_before.loop_count,
        "in_database": cues_before.in_database,
        "database": db_result,
        "database_backup": db_result.get("database_backup"),
        "set_copy": set_copy,
        "kept_hardlinks": kept_hardlinks,
        "unlink_only": unlink_only,
    }


def promote_add_cues_track(
    source_path: str | Path,
    *,
    destination_stage: str = "ready_for_sort",
    database_path: Path | None = None,
    dry_run: bool = False,
    allow_vdj_running: bool = False,
    create_backup: bool = True,
    require_cued: bool | None = None,
) -> SortResult:
    """
    Move a track from Add Cues into another cue-pipeline stage.

    Default destination is Ready for Sort. Also supports no_cues_found,
    ac_low_quality, and low_quality_skip.
    """
    if destination_stage not in CUE_STAGES:
        raise KeyError(f"Unknown cue stage: {destination_stage}")

    db = Path(database_path) if database_path else VDJ_DATABASE
    source = Path(source_path).expanduser().resolve()
    add_root = ADD_CUES.resolve()
    dest_root = CUE_STAGES[destination_stage].resolve()

    if not source.is_file():
        raise FileNotFoundError(f"Source file not found: {source}")
    if source.suffix.lower() not in AUDIO_EXTENSIONS:
        raise ValueError(f"Not an audio file: {source.name}")

    if is_pajamathon_set_audio(source):
        raise ValueError(
            "This file is already in the Pajamathon set. Cue it in place — "
            "Move to Ready for Sort is only for Add Cues inbox tracks."
        )

    try:
        source.relative_to(add_root)
    except ValueError as exc:
        raise ValueError(
            f"Source must live under Add Cues ({add_root}), got {source}"
        ) from exc

    # Flat destination stages (Ready for Sort, No Cues Found, …).
    dest = (dest_root / source.name).resolve()
    try:
        dest.relative_to(CUES_ROOT.resolve())
    except ValueError as exc:
        raise ValueError("Destination escapes Cues root") from exc

    cues = summarize_cues(source, db)
    if require_cued is None:
        require_cued = destination_stage == "ready_for_sort"

    result = _move_audio_and_retarget_db(
        source,
        dest,
        db=db,
        cues=cues,
        dry_run=dry_run,
        allow_vdj_running=allow_vdj_running,
        create_backup=create_backup,
        require_cued=require_cued,
    )
    if not dry_run:
        from .ml_training import schedule_training_drop, schedule_training_update
        if destination_stage == "ready_for_sort":
            schedule_training_update(result.dest_path, result.cues)
        elif destination_stage in {"no_cues_found", "ac_low_quality", "low_quality_skip"}:
            schedule_training_drop(source)
            schedule_training_drop(result.dest_path)
    return result


def demote_ready_to_add_cues(
    source_path: str | Path,
    *,
    database_path: Path | None = None,
    dry_run: bool = False,
    allow_vdj_running: bool = False,
    create_backup: bool = True,
    subfolder: str = "Back from Ready",
) -> SortResult:
    """
    Kick a Ready for Sort track back to Add Cues for re-review / re-cueing.

    Places the file under Add Cues/<subfolder>/ by default so returned tracks
    stay grouped. Cues/loops in VDJ are preserved (FilePath retarget only).
    """
    db = Path(database_path) if database_path else VDJ_DATABASE
    source = Path(source_path).expanduser().resolve()
    ready = READY_FOR_SORT.resolve()
    add_root = ADD_CUES.resolve()

    if not source.is_file():
        raise FileNotFoundError(f"Source file not found: {source}")
    if source.suffix.lower() not in AUDIO_EXTENSIONS:
        raise ValueError(f"Not an audio file: {source.name}")

    try:
        source.relative_to(ready)
    except ValueError as exc:
        raise ValueError(
            f"Source must live under Ready for Sort ({ready}), got {source}"
        ) from exc

    rel = (subfolder or "").strip().strip("/")
    if rel and (".." in Path(rel).parts or Path(rel).is_absolute()):
        raise ValueError(f"Invalid Add Cues subfolder: {subfolder!r}")

    dest_dir = (add_root / rel) if rel else add_root
    dest = (dest_dir / source.name).resolve()
    try:
        dest.relative_to(add_root)
    except ValueError as exc:
        raise ValueError("Destination escapes Add Cues root") from exc

    if not dry_run:
        dest_dir.mkdir(parents=True, exist_ok=True)

    cues = summarize_cues(source, db)
    return _move_audio_and_retarget_db(
        source,
        dest,
        db=db,
        cues=cues,
        dry_run=dry_run,
        allow_vdj_running=allow_vdj_running,
        create_backup=create_backup,
        require_cued=False,
    )


def remove_set_copy(
    source_path: str | Path,
    *,
    sets_root: Path | None = None,
    add_root: Path | None = None,
    database_path: Path | None = None,
    dry_run: bool = False,
    to_trash: bool = True,
    allow_vdj_running: bool = False,
    create_backup: bool = True,
) -> dict[str, Any]:
    """Delete the Sets copy only. Never touch Zouk / Cues Sorted / Add Cues."""
    del add_root  # siblings are never passed through this writer
    source = Path(source_path).expanduser().resolve()
    root = (sets_root or SETS_ROOT).expanduser().resolve()
    try:
        source.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            f"Remove only deletes a Sets/ copy (that path). Got {source}"
        ) from exc
    if not source.is_file():
        raise FileNotFoundError(f"Set file not found: {source}")
    if source.suffix.lower() not in AUDIO_EXTENSIONS:
        raise ValueError(f"Not an audio file: {source.name}")

    if is_virtualdj_running() and not dry_run and not allow_vdj_running:
        raise RuntimeError(
            "VirtualDJ is running. Close it before removing a set copy, "
            "or pass allow_vdj_running=true (not recommended)."
        )

    db = Path(database_path) if database_path else VDJ_DATABASE
    stems = Path(f"{source}.vdjstems")
    files_to_remove = [str(source)]
    if stems.is_file():
        files_to_remove.append(str(stems))
    cues_before = summarize_cues(source, db)

    if dry_run:
        kept = 0
        try:
            kept = max(0, source.stat().st_nlink - 1)
        except OSError:
            kept = 0
        return {
            "ok": True,
            "dry_run": True,
            "path": str(source),
            "name": source.name,
            "removed_files": files_to_remove,
            "to_trash": False if kept else to_trash,
            "had_cues": cues_before.cue_count,
            "had_loops": cues_before.loop_count,
            "in_database": cues_before.in_database,
            "kept_hardlinks": kept,
            "unlink_only": kept > 0,
        }

    kept_hardlinks = 0
    unlink_only = False
    for path_str in files_to_remove:
        dropped = _drop_path(Path(path_str), to_trash=to_trash)
        if dropped["unlink_only"]:
            unlink_only = True
            kept_hardlinks = max(kept_hardlinks, int(dropped["kept_hardlinks"]))

    db_result = remove_song_entry_from_database(
        source,
        database_path=db,
        create_backup=create_backup,
        dry_run=False,
    )
    return {
        "ok": True,
        "dry_run": False,
        "path": str(source),
        "name": source.name,
        "removed_files": files_to_remove,
        "to_trash": False if unlink_only else to_trash,
        "had_cues": cues_before.cue_count,
        "had_loops": cues_before.loop_count,
        "in_database": cues_before.in_database,
        "database": db_result,
        "database_backup": db_result.get("database_backup"),
        "kept_hardlinks": kept_hardlinks,
        "unlink_only": unlink_only,
    }


def send_set_copy_to_add_cues(
    source_path: str | Path,
    *,
    add_root: Path | None = None,
    sets_root: Path | None = None,
    database_path: Path | None = None,
    dry_run: bool = False,
    allow_vdj_running: bool = False,
    create_backup: bool = True,
) -> dict[str, Any]:
    """Relocate a Sets/Pajamathon copy to Add Cues/Pajamathon for new cues.

    Zouk / Cues Sorted siblings stay. If the inbox already has this name,
    drop the set name only (never overwrite the sibling).
    """
    source = Path(source_path).expanduser().resolve()
    if not is_pajamathon_set_audio(source, sets_root=sets_root):
        raise ValueError("Send-back is only for Sets/Pajamathon copies")
    if not source.is_file():
        raise FileNotFoundError(f"Set file not found: {source}")
    if source.suffix.lower() not in AUDIO_EXTENSIONS:
        raise ValueError(f"Not an audio file: {source.name}")

    inbox_root = (add_root or ADD_CUES).expanduser().resolve()
    dest_dir = inbox_root / "Pajamathon"
    dest = (dest_dir / source.name).resolve()
    try:
        dest.relative_to(inbox_root)
    except ValueError as exc:
        raise ValueError("Destination escapes Add Cues root") from exc

    db = Path(database_path) if database_path else VDJ_DATABASE
    stems = Path(f"{source}.vdjstems")

    if dest.exists():
        if dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "already_in_inbox": True,
                "source_path": str(source),
                "dest_path": str(dest),
                "name": source.name,
            }
        if is_virtualdj_running() and not allow_vdj_running:
            raise RuntimeError(
                "VirtualDJ is running. Close it before send-back, "
                "or pass allow_vdj_running=true (not recommended)."
            )
        dropped = _drop_path(source, to_trash=False)
        if stems.is_file():
            _drop_path(stems, to_trash=False)
        dest_cues = summarize_cues(dest, db)
        src_in_db = _find_db_path_variant(source, db) if db.is_file() else None
        database_updated = False
        if src_in_db:
            if dest_cues.in_database:
                remove_song_entry_from_database(
                    source,
                    database_path=db,
                    create_backup=create_backup,
                    dry_run=False,
                )
                database_updated = True
            else:
                with vdj_db_write():
                    relocate_song_filepath_in_database(
                        db,
                        src_in_db,
                        _normalize_path(dest),
                        validate=True,
                    )
                database_updated = True
        return {
            "ok": True,
            "dry_run": False,
            "already_in_inbox": True,
            "unlink_only": dropped.get("unlink_only"),
            "kept_hardlinks": dropped.get("kept_hardlinks"),
            "source_path": str(source),
            "dest_path": str(dest),
            "name": source.name,
            "database_updated": database_updated,
        }

    cues = summarize_cues(source, db)
    moved = _move_audio_and_retarget_db(
        source,
        dest,
        db=db,
        cues=cues,
        dry_run=dry_run,
        allow_vdj_running=allow_vdj_running,
        create_backup=create_backup,
        require_cued=False,
    )
    payload = moved.to_dict()
    payload["ok"] = True
    payload["already_in_inbox"] = False
    payload["name"] = source.name
    return payload

