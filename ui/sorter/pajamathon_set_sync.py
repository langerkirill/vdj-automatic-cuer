"""Sync Add Cues/Pajamathon deletions into Sets/Pajamathon only.

Library copies (Zouk/House/Cues Sorted) are never touched. Already-cued set
tracks that were never staged in Add Cues stay put.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional
from xml.sax.saxutils import unescape

from .config import (
    ADD_CUES,
    AUDIO_EXTENSIONS,
    CUE_STAGES,
    CUES_ROOT,
    CUES_SORTED,
    DJ_NOTES_ROOT,
    LIBRARIES,
    READY_FOR_SORT,
    SETS_ROOT,
    VDJ_DATABASE,
)
from .library import (
    build_set_match_index,
    find_set_matches,
    is_pajamathon_event,
    normalize_placement_key,
)
from .relocate import (
    _drop_path,
    is_virtualdj_running,
    remove_song_entry_from_database,
)
from .vdj_sideview_recs import VDJ_MYLISTS

SNAPSHOT_NAME = "pajamathon_add_cues_snapshot.json"
ADD_FOLDER_NAME = "Pajamathon"
def default_snapshot_path() -> Path:
    return DJ_NOTES_ROOT / SNAPSHOT_NAME


def deleted_add_cues_names(
    previous_names: Iterable[str],
    current_names: Iterable[str],
    extra_names: Iterable[str] | None = None,
) -> set[str]:
    current = set(current_names)
    extras = set(extra_names or [])
    return (set(previous_names) | extras) - current


def list_audio_filenames(folder: Path, *, recursive: bool = False) -> list[str]:
    if not folder.is_dir():
        return []
    iterator = folder.rglob("*") if recursive else folder.iterdir()
    names: list[str] = []
    for path in iterator:
        if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS:
            names.append(path.name)
    return names


def _snapshot_names(snapshot: dict[str, Any]) -> list[str]:
    files = snapshot.get("files") or []
    names: list[str] = []
    for item in files:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict) and item.get("name"):
            names.append(str(item["name"]))
    return names


def _load_snapshot(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_snapshot(path: Path, names: list[str], folder: Path) -> None:
    files = []
    for name in sorted(names, key=str.lower):
        audio = folder / name
        size = audio.stat().st_size if audio.is_file() else 0
        files.append(
            {
                "name": name,
                "key": normalize_placement_key(name),
                "size": size,
            }
        )
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "folder": str(folder),
        "files": files,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _audio_keys(folder: Path, *, recursive: bool = False) -> set[str]:
    return {normalize_placement_key(name) for name in list_audio_filenames(folder, recursive=recursive)}


def _pipeline_keys(
    *,
    add_cues_root: Path,
    ready_root: Path,
    cue_stage_roots: dict[str, Path] | None,
) -> set[str]:
    keys = _audio_keys(add_cues_root, recursive=True)
    keys.update(_audio_keys(ready_root))
    stages = cue_stage_roots if cue_stage_roots is not None else CUE_STAGES
    for stage_name, stage_path in stages.items():
        if stage_name == "add_cues":
            continue
        keys.update(_audio_keys(stage_path))
    return keys


def _library_keys(library_roots: Iterable[Path], cues_sorted_root: Path | None) -> set[str]:
    keys: set[str] = set()
    for root in library_roots:
        keys.update(_audio_keys(root, recursive=True))
    if cues_sorted_root is not None:
        keys.update(_audio_keys(cues_sorted_root, recursive=True))
    return keys


def _unique_set_match(
    filename: str,
    *,
    index: dict[str, list[dict[str, str]]],
) -> dict[str, str] | None:
    hits = [
        hit
        for hit in find_set_matches(filename, index=index)
        if is_pajamathon_event(str(hit.get("event") or hit.get("root_name") or ""))
    ]
    if len(hits) != 1:
        return None if not hits else {"ambiguous": "1", "paths": [h["path"] for h in hits]}
    return hits[0]


def _assert_pajamathon_set_file(path: Path, sets_root: Path) -> Path:
    audio = path.expanduser().resolve()
    root = sets_root.expanduser().resolve()
    try:
        rel = audio.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Refusing to delete outside Sets/: {audio}") from exc
    if not rel.parts or not is_pajamathon_event(rel.parts[0]):
        raise ValueError(f"Refusing to delete non-Pajamathon set file: {audio}")
    lowered = str(audio).lower()
    for banned in ("/zouk/", "/house/", "/cues sorted/", "/add cues/"):
        if banned in lowered:
            raise ValueError(f"Refusing to delete library/add-cues path: {audio}")
    return audio


def prune_m3u_paths(path: Path, removed_paths: set[str]) -> int:
    if not path.is_file() or not removed_paths:
        return 0
    lines = path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    removed = 0
    index = 0
    while index < len(lines):
        line = lines[index]
        nxt = lines[index + 1] if index + 1 < len(lines) else ""
        if line.startswith("#EXTINF") and nxt in removed_paths:
            removed += 1
            index += 2
            continue
        if line in removed_paths:
            removed += 1
            index += 1
            continue
        out.append(line)
        index += 1
    if removed:
        path.write_text("\n".join(out) + ("\n" if out else ""), encoding="utf-8")
    return removed


def prune_vdjfolder_paths(path: Path, removed_paths: set[str]) -> int:
    if not path.is_file() or not removed_paths:
        return 0
    text = path.read_text(encoding="utf-8")
    removed = 0

    def drop(match: re.Match[str]) -> str:
        nonlocal removed
        raw = unescape(match.group(1), {"apos": "'"})
        if raw in removed_paths:
            removed += 1
            return ""
        return match.group(0)

    updated = re.sub(
        r"[ \t]*<song\b[^>]*\bpath=\"([^\"]+)\"[^>]*/>[ \t]*\n?",
        drop,
        text,
    )
    if removed:
        path.write_text(updated, encoding="utf-8")
    return removed


def _default_playlist_paths(sets_root: Path, event_folder: str) -> list[Path]:
    slug = re.sub(r"[^a-z0-9]+", "-", event_folder.lower()).strip("-")
    return [
        DJ_NOTES_ROOT / "playlists" / f"{slug}.m3u",
        sets_root / event_folder / f"{event_folder}.m3u",
        CUES_ROOT / f"{event_folder}.vdjfolder",
        VDJ_MYLISTS / f"{event_folder}.vdjfolder",
    ]


def _event_folder_name(sets_root: Path) -> str:
    if not sets_root.is_dir():
        return "Pajamathon 2026"
    for child in sorted(sets_root.iterdir()):
        if child.is_dir() and is_pajamathon_event(child.name):
            return child.name
    return "Pajamathon 2026"


def sync_pajamathon_set_deletes(
    *,
    add_cues_root: Path | None = None,
    add_folder_name: str = ADD_FOLDER_NAME,
    sets_root: Path | None = None,
    snapshot_path: Path | None = None,
    staged_seed_path: Path | None = None,
    historical_delete_paths: list[Path] | None = None,
    extra_deleted: list[str] | None = None,
    ready_root: Path | None = None,
    cue_stage_roots: dict[str, Path] | None = None,
    library_roots: list[Path] | None = None,
    cues_sorted_root: Path | None = None,
    database_path: Path | None = None,
    playlist_paths: list[Path] | None = None,
    dry_run: bool = False,
    to_trash: bool = True,
) -> dict[str, Any]:
    add_root = (add_cues_root or ADD_CUES).expanduser()
    add_folder = add_root / add_folder_name
    sets = (sets_root or SETS_ROOT).expanduser()
    ready = (ready_root or READY_FOR_SORT).expanduser()
    snap_path = snapshot_path or default_snapshot_path()
    db = Path(database_path) if database_path else VDJ_DATABASE
    production = snapshot_path is None
    if library_roots is not None:
        lib_roots = library_roots
    elif production:
        lib_roots = list(LIBRARIES.values())
    else:
        lib_roots = []
    if cues_sorted_root is not None:
        archive = cues_sorted_root
    elif production:
        archive = CUES_SORTED
    else:
        archive = None

    current_names = list_audio_filenames(add_folder)
    snapshot = _load_snapshot(snap_path)
    extra = list(extra_deleted or [])
    # Missing snapshot: record current Add Cues only. Never replay staged/history
    # as deletes — that would trash successfully cued set tracks.
    previous_names = _snapshot_names(snapshot) if snapshot else list(current_names)

    candidates = sorted(
        deleted_add_cues_names(previous_names, current_names, extra),
        key=str.lower,
    )
    pipeline = _pipeline_keys(
        add_cues_root=add_root,
        ready_root=ready,
        cue_stage_roots=cue_stage_roots,
    )
    library = _library_keys(lib_roots, archive)
    explicit = {name for name in extra if name}
    index = build_set_match_index(sets)

    skipped_promoted: list[str] = []
    skipped_ambiguous: list[dict[str, Any]] = []
    skipped_missing: list[str] = []
    removed: list[dict[str, Any]] = []
    errors: list[str] = []

    for name in candidates:
        key = normalize_placement_key(name)
        if key in pipeline:
            skipped_promoted.append(name)
            continue
        # Snapshot-diff only: still in House/Zouk/Cues Sorted means it was
        # sorted, not deleted. Explicit Add Cues deletes still drop the set copy.
        if name not in explicit and key in library:
            skipped_promoted.append(name)
            continue
        match = _unique_set_match(name, index=index)
        if match is None:
            skipped_missing.append(name)
            continue
        if match.get("ambiguous"):
            skipped_ambiguous.append({"name": name, "paths": match.get("paths") or []})
            continue
        listed_path = Path(match["path"])
        try:
            set_path = _assert_pajamathon_set_file(listed_path, sets)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        stems = Path(f"{set_path}.vdjstems")
        files = [str(set_path)]
        if stems.is_file():
            files.append(str(stems))
        db_result: dict[str, Any] | None = None
        if not dry_run:
            try:
                for file_str in files:
                    _drop_path(Path(file_str), to_trash=to_trash)
                if db.is_file() and not is_virtualdj_running():
                    db_result = remove_song_entry_from_database(
                        listed_path,
                        database_path=db,
                        create_backup=False,
                        dry_run=False,
                    )
                    if not db_result.get("removed_from_db") and listed_path != set_path:
                        db_result = remove_song_entry_from_database(
                            set_path,
                            database_path=db,
                            create_backup=False,
                            dry_run=False,
                        )
            except Exception as exc:
                errors.append(f"{set_path.name}: {exc}")
                continue
        removed.append(
            {
                "name": name,
                "set_path": str(listed_path),
                "removed_files": files,
                "database": db_result,
            }
        )

    removed_paths = {item["set_path"] for item in removed}
    for item in removed:
        resolved = str(Path(item["set_path"]).expanduser())
        try:
            removed_paths.add(str(Path(item["set_path"]).resolve()))
        except OSError:
            removed_paths.add(resolved)
    playlist_hits = 0
    if removed_paths and not dry_run:
        event_folder = _event_folder_name(sets)
        lists = (
            playlist_paths
            if playlist_paths is not None
            else _default_playlist_paths(sets, event_folder)
        )
        for list_path in lists:
            if list_path.suffix.lower() == ".m3u":
                playlist_hits += prune_m3u_paths(list_path, removed_paths)
            else:
                playlist_hits += prune_vdjfolder_paths(list_path, removed_paths)

    if not dry_run:
        _write_snapshot(snap_path, current_names, add_folder)

    return {
        "ok": not errors,
        "dry_run": dry_run,
        "add_cues_count": len(current_names),
        "candidates": candidates,
        "removed": removed,
        "removed_count": len(removed),
        "skipped_promoted": skipped_promoted,
        "skipped_ambiguous": skipped_ambiguous,
        "skipped_missing": skipped_missing,
        "playlist_entries_removed": playlist_hits,
        "errors": errors,
        "snapshot": str(snap_path),
    }


def _is_queue_hit(hit: dict[str, Any]) -> bool:
    root = str(hit.get("root") or "")
    name = str(hit.get("root_name") or "")
    return name in {"Ready For Sort", "Add Cues"} or "/Cues/Ready For Sort" in root or "/Cues/Add Cues" in root


def _source_rank(hit: dict[str, Any], cues: Any) -> tuple[int, int, int]:
    """Prefer more cues/loops, then Cues Sorted / Zouk over other crates."""
    root = str(hit.get("root_name") or "")
    crate = 0 if root == "Cues Sorted" else 1 if root == "Zouk" else 2
    return (int(getattr(cues, "cue_count", 0) or 0), int(getattr(cues, "loop_count", 0) or 0), -crate)


def push_library_cues_to_pajamathon(
    *,
    sets_root: Path | None = None,
    library_roots: Optional[list[Path]] = None,
    database_path: Path | None = None,
    dry_run: bool = False,
    create_backup: bool = True,
) -> dict[str, Any]:
    """
    Clone cued House/Zouk/Cues Sorted/queue Song blocks onto matching
    Sets/Pajamathon files. Skips dests that already have at least as many cues.
    """
    from .library import build_audio_basename_index, find_matches_from_index
    from .playlist_assemble import clone_cues_for_set_paths
    from .relocate import backup_database, summarize_cues_for_paths

    if is_virtualdj_running() and not dry_run:
        raise RuntimeError(
            "VirtualDJ is running. Close it before pushing cues onto Pajamathon."
        )

    set_index = build_set_match_index(sets_root)
    set_hits: list[dict[str, str]] = []
    seen_dest: set[str] = set()
    for bucket in set_index.values():
        for hit in bucket:
            path = hit.get("path") or ""
            if not path or path in seen_dest:
                continue
            seen_dest.add(path)
            set_hits.append(hit)

    roots = library_roots or [
        *LIBRARIES.values(),
        CUES_SORTED,
        READY_FOR_SORT,
        ADD_CUES,
    ]
    lib_index = build_audio_basename_index(roots)

    dest_paths = [h["path"] for h in set_hits]
    cand_paths: list[str] = []
    dest_cands: dict[str, list[dict[str, str]]] = {}
    for hit in set_hits:
        dest = hit["path"]
        matches = find_matches_from_index(Path(dest).name, lib_index, fuzzy=True)
        dest_cands[dest] = matches
        cand_paths.extend(m["path"] for m in matches)

    cue_index = summarize_cues_for_paths([*dest_paths, *cand_paths], database_path)
    pairs: list[tuple[str, str]] = []
    planned: list[dict[str, Any]] = []
    skipped_already = 0
    skipped_no_source = 0
    for dest, matches in dest_cands.items():
        dest_cues = cue_index.get(dest)
        dest_n = int(getattr(dest_cues, "cue_count", 0) or 0)
        best: tuple[tuple[int, int, int], dict[str, str], Any] | None = None
        for match in matches:
            src_cues = cue_index.get(match["path"])
            if src_cues is None or not src_cues.is_cued:
                continue
            rank = _source_rank(match, src_cues)
            if best is None or rank > best[0]:
                best = (rank, match, src_cues)
        if best is None:
            skipped_no_source += 1
            continue
        _rank, match, src_cues = best
        src_n = int(src_cues.cue_count or 0)
        if dest_n >= src_n and dest_n > 0:
            skipped_already += 1
            continue
        src_path = str(Path(match["path"]).expanduser().resolve())
        dest_path = str(Path(dest).expanduser().resolve())
        pairs.append((src_path, dest_path))
        planned.append(
            {
                "source": src_path,
                "dest": dest_path,
                "source_cues": src_n,
                "source_loops": int(src_cues.loop_count or 0),
                "dest_cues": dest_n,
            }
        )

    backup = None
    cloned = {"cloned": 0, "already_present": 0, "missing": 0, "message": "dry-run"}
    if not dry_run and pairs:
        db = Path(database_path or VDJ_DATABASE)
        if create_backup:
            backup = backup_database(db)
        cloned = clone_cues_for_set_paths(pairs, database_path=db)

    return {
        "ok": True,
        "dry_run": dry_run,
        "set_files": len(set_hits),
        "planned": len(planned),
        "copied": int(cloned.get("cloned") or 0) if not dry_run else len(planned),
        "already_cued": skipped_already,
        "no_cued_source": skipped_no_source,
        "missing": int(cloned.get("missing") or 0),
        "database_backup": backup,
        "message": cloned.get("message") or "",
        "tracks": planned[:40],
    }


def push_cues_to_sibling_copies(
    *,
    dest_roots: Optional[list[Path]] = None,
    source_roots: Optional[list[Path]] = None,
    database_path: Path | None = None,
    dry_run: bool = False,
    create_backup: bool = True,
) -> dict[str, Any]:
    """
    For each song, clone the richest cued copy onto House/Zouk/Cues Sorted/Sets
    siblings that have fewer cues. Replays copy-cues / sort archives lost when
    VirtualDJ was open.
    """
    from collections import defaultdict

    from .library import (
        build_audio_basename_index,
        is_pajamathon_event,
        normalize_placement_key,
    )
    from .playlist_assemble import clone_cues_for_set_paths
    from .relocate import backup_database, summarize_cues_for_paths

    if is_virtualdj_running() and not dry_run:
        raise RuntimeError(
            "VirtualDJ is running. Close it before pushing cues onto library copies."
        )

    paj_root = None
    sets = Path(SETS_ROOT)
    if sets.is_dir():
        for child in sets.iterdir():
            if child.is_dir() and is_pajamathon_event(child.name):
                paj_root = child
                break

    dests = dest_roots or [
        *LIBRARIES.values(),
        CUES_SORTED,
        *([paj_root] if paj_root is not None else []),
    ]
    extras = source_roots or [READY_FOR_SORT, ADD_CUES]
    index = build_audio_basename_index([*dests, *extras])

    unique: dict[str, dict[str, str]] = {}
    for bucket in index.values():
        for hit in bucket:
            path = hit.get("path") or ""
            if path:
                unique[path] = hit

    dest_root_resolved = [Path(r).expanduser().resolve() for r in dests]

    def _is_dest(path: str) -> bool:
        try:
            resolved = Path(path).expanduser().resolve()
        except OSError:
            return False
        for root in dest_root_resolved:
            try:
                resolved.relative_to(root)
                return True
            except ValueError:
                continue
        return False

    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for hit in unique.values():
        key = normalize_placement_key(Path(hit["path"]).name)
        if key:
            groups[key].append(hit)

    all_paths = [h["path"] for h in unique.values()]
    cue_index = summarize_cues_for_paths(all_paths, database_path)

    pairs: list[tuple[str, str]] = []
    planned: list[dict[str, Any]] = []
    skipped_already = 0
    skipped_no_source = 0
    groups_touched = 0
    for hits in groups.values():
        dest_hits = [h for h in hits if _is_dest(h["path"])]
        if not dest_hits:
            continue
        best: tuple[tuple[int, int, int], dict[str, str], Any] | None = None
        for hit in hits:
            cues = cue_index.get(hit["path"])
            if cues is None or not cues.is_cued:
                continue
            rank = _source_rank(hit, cues)
            if best is None or rank > best[0]:
                best = (rank, hit, cues)
        if best is None:
            skipped_no_source += len(dest_hits)
            continue
        _rank, src_hit, src_cues = best
        src_n = int(src_cues.cue_count or 0)
        src_loops = int(src_cues.loop_count or 0)
        src_path = str(Path(src_hit["path"]).expanduser().resolve())
        group_planned = 0
        for dest_hit in dest_hits:
            dest_path = str(Path(dest_hit["path"]).expanduser().resolve())
            if dest_path == src_path:
                continue
            dest_cues = cue_index.get(dest_hit["path"])
            dest_n = int(getattr(dest_cues, "cue_count", 0) or 0)
            dest_loops = int(getattr(dest_cues, "loop_count", 0) or 0)
            if dest_n >= src_n and dest_loops >= src_loops and dest_n > 0:
                skipped_already += 1
                continue
            pairs.append((src_path, dest_path))
            planned.append(
                {
                    "source": src_path,
                    "dest": dest_path,
                    "source_cues": src_n,
                    "source_loops": src_loops,
                    "dest_cues": dest_n,
                    "dest_root": dest_hit.get("root_name"),
                }
            )
            group_planned += 1
        if group_planned:
            groups_touched += 1

    backup = None
    cloned = {"cloned": 0, "already_present": 0, "missing": 0, "message": "dry-run"}
    if not dry_run and pairs:
        db = Path(database_path or VDJ_DATABASE)
        if create_backup:
            backup = backup_database(db)
        cloned = clone_cues_for_set_paths(pairs, database_path=db)

    dest_folders: dict[str, int] = defaultdict(int)
    for item in planned:
        dest_folders[str(item.get("dest_root") or "other")] += 1

    return {
        "ok": True,
        "dry_run": dry_run,
        "groups_touched": groups_touched,
        "planned": len(planned),
        "copied": int(cloned.get("cloned") or 0) if not dry_run else len(planned),
        "already_cued": skipped_already,
        "no_cued_source": skipped_no_source,
        "missing": int(cloned.get("missing") or 0),
        "database_backup": backup,
        "message": cloned.get("message") or "",
        "by_root": dict(dest_folders),
        "tracks": planned[:50],
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Remove Sets/Pajamathon copies of songs deleted from Add Cues/Pajamathon."
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-trash", action="store_true", help="Unlink instead of Trash")
    parser.add_argument("--name", action="append", dest="names", default=[])
    args = parser.parse_args(argv)
    result = sync_pajamathon_set_deletes(
        extra_deleted=args.names,
        dry_run=args.dry_run,
        to_trash=not args.no_trash,
    )
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
