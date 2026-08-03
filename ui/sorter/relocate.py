"""
Move cued audio into a destination folder and retarget VirtualDJ FilePath.

XML handling is delegated entirely to vdj-automatic-cuer's vdj_database_safety:
CRLF preservation, surgical Song rewrite, integrity checks.
"""

from __future__ import annotations

import shutil
import subprocess
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .autocue_path import ensure_autocue_on_path
from .config import (
    ADD_CUES,
    AUDIO_EXTENSIONS,
    CUE_STAGES,
    CUES_ROOT,
    CUES_SORTED,
    LIBRARIES,
    READY_FOR_SORT,
    VDJ_DATABASE,
)
from .library import expand_library_mode, resolve_destination

ensure_autocue_on_path()

from vdj_database_safety import (  # noqa: E402
    MANUAL_CUE_TYPES,
    _find_song_span,
    _lightweight_content_stats,
    _lightweight_rewrite_stats,
    atomic_replace_database_parts,
    clone_song_entry_to_path,
    load_song_element,
    normalize_database_path,
    read_vdj_database_text,
    relocate_song_filepath_in_database,
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


def vdj_bpm_to_actual(vdj_bpm: Optional[float]) -> Optional[float]:
    """
    Convert VirtualDJ Scan/Tags Bpm values to musical BPM.

    VDJ usually stores beat duration in seconds (e.g. 0.5 → 120 BPM).
    Values already in a musical range (60–200) are returned as-is.
    """
    if vdj_bpm is None or vdj_bpm <= 0:
        return None
    if 60.0 <= vdj_bpm <= 220.0:
        return float(vdj_bpm)
    actual = 60.0 / vdj_bpm
    if 40.0 <= actual <= 240.0:
        return actual
    # Odd encodings: try *120 heuristic used elsewhere in AutoCue
    alt = vdj_bpm * 120.0
    if 40.0 <= alt <= 240.0:
        return alt
    return None


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

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["cues"] = self.cues.to_dict()
        return payload


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


def summarize_cues(
    audio_path: str | Path,
    database_path: Path | None = None,
) -> CueSummary:
    db = database_path or VDJ_DATABASE
    path = _normalize_path(audio_path)
    empty = CueSummary(
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
        points=[],
    )
    if not db.is_file():
        return empty

    try:
        song = load_song_element(db, path)
    except KeyError:
        # Try non-resolved path variants (database may store non-resolved form)
        try:
            song = load_song_element(db, normalize_database_path(str(audio_path)))
        except KeyError:
            return empty

    tags = song.find("Tags")
    infos = song.find("Infos")
    scan = song.find("Scan")
    comment_el = song.find("Comment")
    title = tags.get("Title", "") if tags is not None else ""
    author = tags.get("Author", "") if tags is not None else ""
    comment = (comment_el.text or "").strip() if comment_el is not None else ""
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
            # Skip automix / Num=0 hotcues / unknown types from the jump list.
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
        points=points,
    )


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


def assess_cue_readiness(cues: CueSummary) -> dict[str, Any]:
    """
    Heuristic for whether a track looks ready to leave Add Cues → Ready for Sort.
    """
    checks = {
        "in_database": cues.in_database,
        "has_beatgrid": cues.has_beatgrid,
        "has_cues": cues.cue_count > 0,
        "multiple_cues": cues.cue_count >= 2,
        "has_loops": cues.loop_count > 0,
    }
    if not cues.in_database:
        status = "missing"
        label = "Missing from VDJ"
        ready = False
    elif cues.cue_count <= 0:
        status = "not_cued"
        label = "Not cued yet"
        ready = False
    elif cues.has_beatgrid and cues.cue_count >= 2:
        status = "ready"
        label = "Looks ready"
        ready = True
    elif cues.cue_count >= 1:
        status = "partial"
        label = "Partially cued — review"
        ready = False
    else:
        status = "not_cued"
        label = "Not cued yet"
        ready = False

    return {
        "status": status,
        "label": label,
        "ready": ready,
        "checks": checks,
        "summary": (
            f"{cues.cue_count} cues · {cues.loop_count} loops"
            + (" · beatgrid" if cues.has_beatgrid else " · no beatgrid")
        ),
    }


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
) -> SortResult:
    if require_cued and not cues.is_cued:
        raise PermissionError(
            "Track is not cued in VirtualDJ (no manual cue points)."
        )

    if dest.exists():
        raise FileExistsError(f"Destination already has a file named {dest.name}")

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


def _copy_file_and_stems(source: Path, dest: Path) -> bool:
    """Copy audio (+ .vdjstems if present). Returns whether stems were copied."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        raise FileExistsError(f"Cues Sorted already has {dest.name} at {dest}")
    shutil.copy2(str(source), str(dest))
    stems_source = Path(f"{source}.vdjstems")
    stems_dest = Path(f"{dest}.vdjstems")
    if stems_source.is_file():
        if stems_dest.exists():
            stems_dest.unlink()
        shutil.copy2(str(stems_source), str(stems_dest))
        return True
    return False


def _build_library_destinations(
    library_mode: str,
    relative_folder: str,
    filename: str,
    *,
    create_missing: bool,
) -> list[tuple[str, Path]]:
    """
    Resolve destinations for House, Zouk, or Both.

    For Both, Zouk is ordered first so the primary VDJ FilePath prefers Zouk
    (main cued archive), then House receives a copy.
    """
    libraries = expand_library_mode(library_mode)
    if library_mode.strip().lower() == "both":
        libraries = sorted(libraries, key=lambda n: 0 if n == "Zouk" else 1)

    dests: list[tuple[str, Path]] = []
    for lib in libraries:
        dest_dir = resolve_destination(lib, relative_folder, create=create_missing)
        if not dest_dir.is_dir():
            raise FileNotFoundError(
                f"Destination folder does not exist in {lib}: {relative_folder}"
            )
        dests.append((lib, (dest_dir / filename).resolve()))
    return dests


def sort_track(
    source_path: str | Path,
    *,
    library_name: str,
    relative_folder: str,
    database_path: Path | None = None,
    ready_root: Path | None = None,
    dry_run: bool = False,
    allow_vdj_running: bool = False,
    create_backup: bool = True,
    also_cues_sorted: bool = True,
) -> SortResult:
    """
    Move a Ready-for-Sort track into House and/or Zouk and retarget VDJ cues.

    library_name: "House" | "Zouk" | "Both"
      - House / Zouk: single library destination
      - Both: Zouk (primary move + VDJ) and House (copy + VDJ clone)

    Also copies into Cues Sorted under the same relative folder path.
    """
    db = Path(database_path) if database_path else VDJ_DATABASE
    source = Path(source_path).expanduser().resolve()
    ready = (ready_root or READY_FOR_SORT).resolve()
    library_mode = library_name.strip()

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

    # Create missing folders when targeting Both so House can receive Zouk-only nests.
    create_missing = library_mode.lower() == "both"
    library_dests = _build_library_destinations(
        library_mode,
        relative_folder,
        source.name,
        create_missing=create_missing,
    )
    primary_lib, primary_dest = library_dests[0]
    secondary = library_dests[1:]

    cues = summarize_cues(source, db)

    cues_sorted_dest: Optional[Path] = None
    cues_sorted_already = False
    if also_cues_sorted:
        cues_sorted_dest = cues_sorted_destination(relative_folder, source.name)
        cues_sorted_already = cues_sorted_dest.exists()

    library_dest_payload = [
        {"library": lib, "path": str(path)} for lib, path in library_dests
    ]

    if dry_run:
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
        )

    # Fail early if any destination already has the file.
    for lib, path in library_dests:
        if path.exists():
            raise FileExistsError(f"{lib} already has a file named {source.name}: {path}")

    result = _move_audio_and_retarget_db(
        source,
        primary_dest,
        db=db,
        cues=cues,
        dry_run=False,
        allow_vdj_running=allow_vdj_running,
        create_backup=create_backup,
        require_cued=True,
    )
    result.library_mode = library_mode
    result.library_dests = library_dest_payload

    # Secondary libraries (House when Both) — copy + clone VDJ cues.
    for lib, sec_dest in secondary:
        try:
            _copy_file_and_stems(primary_dest, sec_dest)
        except Exception as exc:
            raise RuntimeError(
                f"Sorted into {primary_lib} at {primary_dest}, but failed to copy into "
                f"{lib} at {sec_dest}: {exc}"
            ) from exc
        if result.database_updated or cues.in_database:
            try:
                clone_song_entry_to_path(
                    db,
                    _normalize_path(primary_dest),
                    _normalize_path(sec_dest),
                    validate=True,
                    skip_if_exists=True,
                )
            except KeyError:
                pass

    if not also_cues_sorted or cues_sorted_dest is None:
        return result

    result.cues_sorted_path = str(cues_sorted_dest)
    result.cues_sorted_already_present = cues_sorted_dest.exists()

    # Copy archive file after primary move (source is gone; copy from primary dest).
    if not cues_sorted_dest.exists():
        try:
            _copy_file_and_stems(primary_dest, cues_sorted_dest)
            result.cues_sorted_copied = True
        except Exception as exc:
            raise RuntimeError(
                f"Sorted into library at {primary_dest}, but failed to copy into "
                f"Cues Sorted at {cues_sorted_dest}: {exc}"
            ) from exc
    else:
        result.cues_sorted_copied = False
        result.cues_sorted_already_present = True

    # Clone VDJ entry so the Cues Sorted copy keeps the same cues.
    if result.database_updated or cues.in_database:
        try:
            clone_info = clone_song_entry_to_path(
                db,
                _normalize_path(primary_dest),
                _normalize_path(cues_sorted_dest),
                validate=True,
                skip_if_exists=True,
            )
            result.cues_sorted_db_cloned = bool(clone_info.get("cloned"))
            if clone_info.get("already_present"):
                result.cues_sorted_already_present = True
        except KeyError:
            result.cues_sorted_db_cloned = False

    return result


def _trash_or_unlink(path: Path, *, to_trash: bool) -> None:
    """Move path to macOS Trash when possible; otherwise unlink."""
    if to_trash:
        script = (
            'tell application "Finder"\n'
            f'  delete (POSIX file "{path}" as alias)\n'
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
        # Fall through to permanent delete if AppleScript fails.
    path.unlink(missing_ok=True)


def _allowed_placement_roots() -> list[Path]:
    """House / Zouk libraries + Cues Sorted archive (not Ready / Add Cues)."""
    roots = [p.resolve() for p in LIBRARIES.values()]
    roots.append(CUES_SORTED.resolve())
    return roots


def _assert_under_placement_roots(path: Path) -> Path:
    audio = path.expanduser().resolve()
    for root in _allowed_placement_roots():
        try:
            audio.relative_to(root)
            return audio
        except ValueError:
            continue
    raise ValueError(
        "Delete placement is only allowed under House/, Zouk/, or Cues Sorted/"
    )


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
    Delete a House/Zouk/Cues Sorted copy and remove its VirtualDJ Song entry.

    Used from the Sort-mode "Already in library" card. Does not touch the
    Ready for Sort source track.
    """
    source = _assert_under_placement_roots(Path(placement_path))
    if not source.is_file():
        raise FileNotFoundError(f"Placement file not found: {source}")
    if source.suffix.lower() not in AUDIO_EXTENSIONS:
        raise ValueError(f"Not an audio file: {source.name}")

    db = Path(database_path) if database_path else VDJ_DATABASE
    if is_virtualdj_running() and not dry_run and not allow_vdj_running:
        raise RuntimeError(
            "VirtualDJ is running. Close it before deleting library placements, "
            "or pass allow_vdj_running=true (not recommended)."
        )

    stems = Path(f"{source}.vdjstems")
    files_to_remove = [str(source)]
    if stems.is_file():
        files_to_remove.append(str(stems))

    cues_before = summarize_cues(source, db)

    # Resolve which library/archive root this lives under (for UI labels).
    root_name = "unknown"
    relative_path = source.name
    for root in _allowed_placement_roots():
        try:
            relative_path = source.relative_to(root).as_posix()
            root_name = root.name
            break
        except ValueError:
            continue

    if dry_run:
        db_preview = remove_song_entry_from_database(
            source, database_path=db, create_backup=False, dry_run=True
        )
        return {
            "ok": True,
            "dry_run": True,
            "path": str(source),
            "name": source.name,
            "root_name": root_name,
            "relative_path": relative_path,
            "removed_files": files_to_remove,
            "to_trash": to_trash,
            "had_cues": cues_before.cue_count,
            "had_loops": cues_before.loop_count,
            "in_database": cues_before.in_database,
            "database": db_preview,
        }

    db_result = remove_song_entry_from_database(
        source,
        database_path=db,
        create_backup=create_backup,
        dry_run=False,
    )

    for path_str in files_to_remove:
        _trash_or_unlink(Path(path_str), to_trash=to_trash)

    return {
        "ok": True,
        "dry_run": False,
        "path": str(source),
        "name": source.name,
        "root_name": root_name,
        "relative_path": relative_path,
        "removed_files": files_to_remove,
        "to_trash": to_trash,
        "had_cues": cues_before.cue_count,
        "had_loops": cues_before.loop_count,
        "in_database": cues_before.in_database,
        "database": db_result,
        "database_backup": db_result.get("database_backup"),
    }


def remove_from_ready_for_sort(
    source_path: str | Path,
    *,
    ready_root: Path | None = None,
    dry_run: bool = False,
    to_trash: bool = True,
) -> dict[str, Any]:
    """
    Remove a track from Ready for Sort only — no House/Zouk/Cues Sorted placement.

    Deletes (or moves to Trash) the audio file and .vdjstems sidecar.
    Does not rewrite VirtualDJ paths (there is no destination).
    """
    source = Path(source_path).expanduser().resolve()
    ready = (ready_root or READY_FOR_SORT).resolve()

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

    stems = Path(f"{source}.vdjstems")
    removed = [str(source)]
    if stems.is_file():
        removed.append(str(stems))

    if dry_run:
        return {
            "removed": removed,
            "to_trash": to_trash,
            "dry_run": True,
        }

    for path_str in removed:
        _trash_or_unlink(Path(path_str), to_trash=to_trash)

    return {
        "removed": removed,
        "to_trash": to_trash,
        "dry_run": False,
        "name": source.name,
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

    return _move_audio_and_retarget_db(
        source,
        dest,
        db=db,
        cues=cues,
        dry_run=dry_run,
        allow_vdj_running=allow_vdj_running,
        create_backup=create_backup,
        require_cued=require_cued,
    )


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
