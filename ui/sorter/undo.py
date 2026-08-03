"""
Undo sort / promote actions using the durable action log.

Reverses file moves + VDJ FilePath retargets. Secondary library copies and
Cues Sorted archives created by a sort are removed when we created them.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Optional

from .action_log import append_action, read_actions
from .config import ADD_CUES, CUES_ROOT, LIBRARIES, READY_FOR_SORT, VDJ_DATABASE
from .relocate import (
    _move_audio_and_retarget_db,
    summarize_cues,
)


UNDOABLE = frozenset({"sort", "promote"})


def find_action(action_id: str) -> Optional[dict[str, Any]]:
    for row in read_actions(limit=5000):
        if row.get("id") == action_id:
            return row
    return None


def already_undone(action_id: str) -> Optional[dict[str, Any]]:
    for row in read_actions(limit=5000):
        if row.get("action") != "undo":
            continue
        if (row.get("details") or {}).get("original_id") == action_id:
            return row
    return None


def _assert_under_allowed(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    roots = [CUES_ROOT.resolve(), *[p.resolve() for p in LIBRARIES.values()]]
    for root in roots:
        try:
            resolved.relative_to(root)
            return resolved
        except ValueError:
            continue
    raise ValueError(f"Path is outside Cues/House/Zouk: {resolved}")


def _remove_copy(path: Path) -> bool:
    """Delete audio + stems if present. Returns True if audio was removed."""
    if not path.is_file():
        return False
    stems = Path(f"{path}.vdjstems")
    path.unlink(missing_ok=True)
    if stems.is_file():
        stems.unlink(missing_ok=True)
    return True


def undo_action(
    action_id: str,
    *,
    dry_run: bool = False,
    allow_vdj_running: bool = False,
    create_backup: bool = True,
) -> dict[str, Any]:
    """
    Undo a logged sort or promote by id.

    sort: library dest (+ optional House twin / Cues Sorted) → Ready for Sort
    promote: Ready for Sort → original Add Cues path
    """
    original = find_action(action_id)
    if original is None:
        raise KeyError(f"Action not found: {action_id}")
    if not original.get("success", True):
        raise ValueError("Cannot undo a failed action")
    action = original.get("action")
    if action not in UNDOABLE:
        raise ValueError(f"Action type {action!r} is not undoable (only sort/promote)")

    prior = already_undone(action_id)
    if prior:
        raise ValueError(f"Already undone at {prior.get('ts')} (undo id {prior.get('id')})")

    if action == "sort":
        result = _undo_sort(
            original,
            dry_run=dry_run,
            allow_vdj_running=allow_vdj_running,
            create_backup=create_backup,
        )
    else:
        result = _undo_promote(
            original,
            dry_run=dry_run,
            allow_vdj_running=allow_vdj_running,
            create_backup=create_backup,
        )

    if not dry_run:
        append_action(
            "undo",
            source_path=result.get("moved_from"),
            dest_path=result.get("moved_to"),
            name=original.get("name"),
            details={
                "original_id": action_id,
                "original_action": action,
                "original_ts": original.get("ts"),
                **{k: v for k, v in result.items() if k not in {"moved_from", "moved_to"}},
            },
        )
    return {"ok": True, "dry_run": dry_run, "original": original, "result": result}


def _undo_sort(
    original: dict[str, Any],
    *,
    dry_run: bool,
    allow_vdj_running: bool,
    create_backup: bool,
) -> dict[str, Any]:
    details = original.get("details") or {}
    dest = Path(original["dest_path"]).expanduser().resolve() if original.get("dest_path") else None
    source = (
        Path(original["source_path"]).expanduser().resolve()
        if original.get("source_path")
        else None
    )
    if not dest or not source:
        raise ValueError("Sort action is missing source_path or dest_path")

    dest = _assert_under_allowed(dest)
    source = _assert_under_allowed(source)

    # Source should be under Ready for Sort.
    try:
        source.relative_to(READY_FOR_SORT.resolve())
    except ValueError as exc:
        raise ValueError(
            f"Undo sort expects source under Ready for Sort, got {source}"
        ) from exc

    if not dest.is_file():
        raise FileNotFoundError(
            f"Sorted file is no longer at {dest} — cannot auto-undo"
        )
    if source.exists():
        raise FileExistsError(
            f"Ready for Sort already has {source.name} — move it aside before undo"
        )

    removed_copies: list[str] = []
    cues_sorted_path = details.get("cues_sorted_path")
    library_dests = details.get("library_dests") or []

    # Secondary library copies (e.g. House when Both) — not the primary dest.
    for entry in library_dests:
        p = Path(entry.get("path") or "")
        if not p or not p.is_file():
            continue
        if p.resolve() == dest:
            continue
        if dry_run:
            removed_copies.append(str(p))
        else:
            if _remove_copy(p.resolve()):
                removed_copies.append(str(p.resolve()))

    if cues_sorted_path and details.get("cues_sorted_copied"):
        cs = Path(cues_sorted_path)
        if cs.is_file():
            if dry_run:
                removed_copies.append(str(cs))
            else:
                if _remove_copy(cs.resolve()):
                    removed_copies.append(str(cs.resolve()))

    cues = summarize_cues(dest)
    move_result = _move_audio_and_retarget_db(
        dest,
        source,
        db=VDJ_DATABASE,
        cues=cues,
        dry_run=dry_run,
        allow_vdj_running=allow_vdj_running,
        create_backup=create_backup,
        require_cued=False,
    )

    return {
        "moved_from": str(dest),
        "moved_to": str(source),
        "stems_moved": move_result.stems_moved,
        "database_updated": move_result.database_updated,
        "database_backup": move_result.database_backup,
        "removed_copies": removed_copies,
        "action": "sort",
    }


def _undo_promote(
    original: dict[str, Any],
    *,
    dry_run: bool,
    allow_vdj_running: bool,
    create_backup: bool,
) -> dict[str, Any]:
    dest = Path(original["dest_path"]).expanduser().resolve() if original.get("dest_path") else None
    source = (
        Path(original["source_path"]).expanduser().resolve()
        if original.get("source_path")
        else None
    )
    if not dest or not source:
        raise ValueError("Promote action is missing source_path or dest_path")

    dest = _assert_under_allowed(dest)
    source = _assert_under_allowed(source)

    try:
        source.relative_to(ADD_CUES.resolve())
    except ValueError as exc:
        raise ValueError(
            f"Undo promote expects original source under Add Cues, got {source}"
        ) from exc

    if not dest.is_file():
        raise FileNotFoundError(
            f"Promoted file is no longer at {dest} — cannot auto-undo"
        )
    if source.exists():
        raise FileExistsError(
            f"Add Cues already has {source.name} at {source} — move it aside first"
        )

    # Ensure parent folder under Add Cues still exists.
    source.parent.mkdir(parents=True, exist_ok=True)

    cues = summarize_cues(dest)
    move_result = _move_audio_and_retarget_db(
        dest,
        source,
        db=VDJ_DATABASE,
        cues=cues,
        dry_run=dry_run,
        allow_vdj_running=allow_vdj_running,
        create_backup=create_backup,
        require_cued=False,
    )

    return {
        "moved_from": str(dest),
        "moved_to": str(source),
        "stems_moved": move_result.stems_moved,
        "database_updated": move_result.database_updated,
        "database_backup": move_result.database_backup,
        "removed_copies": [],
        "action": "promote",
    }
