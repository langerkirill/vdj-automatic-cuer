#!/usr/bin/env python3
"""Local Music Sorter — sort cued tracks + review Add Cues before Ready for Sort."""

from __future__ import annotations

import mimetypes
import subprocess
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from sorter.config import (
    ADD_CUES,
    CUE_STAGES,
    CUES_ROOT,
    LIBRARIES,
    READY_FOR_SORT,
    VDJ_DATABASE,
)
from sorter.library import (
    create_folder,
    find_cues_sorted_matches,
    find_library_matches,
    list_add_cues_tracks,
    list_libraries,
    list_library_tree,
    list_ready_tracks,
)
from sorter.recommend import get_recommender
from sorter.relocate import (
    assess_cue_readiness,
    delete_library_placement,
    demote_ready_to_add_cues,
    is_virtualdj_running,
    promote_add_cues_track,
    remove_from_ready_for_sort,
    sort_track,
    summarize_cues,
)
from sorter.action_log import (
    HISTORICAL_SORTS_2026_07_28,
    append_action,
    log_path,
    read_actions,
    seed_historical_sorts,
)
from sorter.audio_meta import probe_audio_meta
from sorter.autocue_retry import (
    get_batch,
    get_job,
    list_batches,
    list_jobs,
    start_batch_retry_cues,
    start_retry_cues,
)
from sorter.bpm_edit import halve_track_bpm
from sorter.cue_edit import delete_cue_point
from sorter.grid_preflight import assess_grid_for_autocue, preflight_from_cues
from sorter.notes_edit import set_track_comment
from sorter.undo import undo_action
from sorter.waveform import build_waveform

app = FastAPI(title="Music Sorter", version="0.2.0")
STATIC_DIR = Path(__file__).resolve().parent / "static"


@app.on_event("startup")
def _seed_action_log() -> None:
    """Ensure historical sorts from first session are in the durable log."""
    try:
        seed_historical_sorts(HISTORICAL_SORTS_2026_07_28)
    except OSError:
        pass


class SortRequest(BaseModel):
    path: str
    library: str
    relative_folder: str
    dry_run: bool = False
    allow_vdj_running: bool = False


class CreateFolderRequest(BaseModel):
    library: str
    name: str
    parent_relative_path: str = ""


class RecommendRequest(BaseModel):
    path: str
    preferred_library: Optional[str] = None
    force: bool = False


class PromoteRequest(BaseModel):
    path: str
    destination_stage: str = "ready_for_sort"
    dry_run: bool = False
    allow_vdj_running: bool = False
    require_cued: Optional[bool] = None


class RemoveReadyRequest(BaseModel):
    path: str
    dry_run: bool = False
    to_trash: bool = True


class DeletePlacementRequest(BaseModel):
    """Delete a House/Zouk/Cues Sorted copy + its VDJ Song (cues/loops)."""

    path: str  # full path of the library/archive placement
    dry_run: bool = False
    to_trash: bool = True
    allow_vdj_running: bool = False


class DemoteReadyRequest(BaseModel):
    """Kick a Ready for Sort track back to Add Cues."""

    path: str
    dry_run: bool = False
    allow_vdj_running: bool = False
    subfolder: str = "Back from Ready"


class RetryCuesRequest(BaseModel):
    path: str
    dry_run: bool = False
    allow_vdj_running: bool = False
    require_grid: bool = True
    deep_grid_check: bool = True
    # all/both | cues | loops — mirrors AutoCue --cues-only / --loops-only
    write_scope: str = "all"


class BatchRetryCuesRequest(BaseModel):
    """Batch AutoCue. Prefer paths, or filter=not_cued to take current Add Cues queue."""

    paths: list[str] = []
    filter: Optional[str] = None  # "not_cued" | None
    dry_run: bool = False
    allow_vdj_running: bool = False
    require_grid: bool = True
    deep_grid_check: bool = False
    write_scope: str = "all"


class UndoRequest(BaseModel):
    action_id: str
    dry_run: bool = False
    allow_vdj_running: bool = False


class GridPreflightRequest(BaseModel):
    path: str
    deep: bool = True


class DeleteCueRequest(BaseModel):
    path: str
    kind: str  # "cue" | "loop"
    pos: float
    num: Optional[str] = None
    name: Optional[str] = None
    slot: Optional[str] = None  # VDJ loop Slot — helps when Num is always -1
    dry_run: bool = False
    allow_vdj_running: bool = False


class NotesRequest(BaseModel):
    path: str
    comment: str = ""
    dry_run: bool = False
    allow_vdj_running: bool = True  # live typing often happens with VDJ open
    create_backup: bool = False


class HalveBpmRequest(BaseModel):
    path: str
    dry_run: bool = False
    allow_vdj_running: bool = False
    # True = restore double-time (×2) if you halved by mistake
    double_instead: bool = False


def _placement_with_cue_status(hit: dict[str, Any]) -> dict[str, Any]:
    """Attach VDJ cue status for a library / Cues Sorted path."""
    cues = summarize_cues(hit["path"])
    return {
        **hit,
        "is_cued": cues.is_cued,
        "in_database": cues.in_database,
        "cue_count": cues.cue_count,
        "loop_count": cues.loop_count,
        "has_beatgrid": cues.has_beatgrid,
        "bpm": cues.bpm,
        "cue_status": (
            "cued"
            if cues.is_cued
            else ("in_database_uncued" if cues.in_database else "missing_from_database")
        ),
    }


def _enrich_track(
    track_dict: dict[str, Any],
    *,
    review: bool = False,
    include_placements: bool = True,
) -> dict[str, Any]:
    cues = summarize_cues(track_dict["path"])
    track_dict["cues"] = cues.to_dict()
    track_dict["is_cued"] = cues.is_cued
    track_dict["status"] = (
        "cued"
        if cues.is_cued
        else ("in_database_uncued" if cues.in_database else "missing_from_database")
    )
    if include_placements:
        # Expensive (library rglob + VDJ lookups) — skip for Add Cues list loads.
        cues_sorted = [
            _placement_with_cue_status(h)
            for h in find_cues_sorted_matches(track_dict["name"])
        ]
        library_hits = [
            _placement_with_cue_status(h)
            for h in find_library_matches(track_dict["name"])
        ]
        track_dict["placements"] = {
            "in_cues_sorted": len(cues_sorted) > 0,
            "cues_sorted": cues_sorted,
            "in_library": len(library_hits) > 0,
            "library": library_hits,
            "already_sorted": len(cues_sorted) > 0 or len(library_hits) > 0,
            "any_library_cued": any(h.get("is_cued") for h in library_hits),
            "any_archive_cued": any(h.get("is_cued") for h in cues_sorted),
        }
    else:
        track_dict["placements"] = {
            "in_cues_sorted": False,
            "cues_sorted": [],
            "in_library": False,
            "library": [],
            "already_sorted": False,
            "any_library_cued": False,
            "any_archive_cued": False,
        }
    if review:
        track_dict["readiness"] = assess_cue_readiness(cues)
        # Fast structural grid preflight for list badges (no ffmpeg).
        track_dict["grid"] = preflight_from_cues(cues, track_dict["path"])
    return track_dict


def _assert_under_cues(path: Path) -> Path:
    audio = path.expanduser().resolve()
    root = CUES_ROOT.resolve()
    try:
        audio.relative_to(root)
    except ValueError as exc:
        raise HTTPException(
            status_code=403, detail="Audio must be under the Cues folder"
        ) from exc
    return audio


@app.get("/api/health")
def health() -> dict[str, Any]:
    stage_counts = {}
    for key, path in CUE_STAGES.items():
        if not path.is_dir():
            stage_counts[key] = 0
            continue
        if key == "add_cues":
            stage_counts[key] = len(list_add_cues_tracks(path))
        else:
            stage_counts[key] = len(list_ready_tracks(path)) if key == "ready_for_sort" else sum(
                1
                for p in path.iterdir()
                if p.is_file()
                and p.suffix.lower()
                in {".mp3", ".flac", ".m4a", ".wav", ".aiff", ".aif", ".ogg", ".opus"}
            )

    return {
        "ok": True,
        "ready_for_sort": str(READY_FOR_SORT),
        "add_cues": str(ADD_CUES),
        "cues_root": str(CUES_ROOT),
        "ready_exists": READY_FOR_SORT.is_dir(),
        "vdj_database": str(VDJ_DATABASE),
        "vdj_database_exists": VDJ_DATABASE.is_file(),
        "virtualdj_running": is_virtualdj_running(),
        "libraries": list_libraries(),
        "stage_counts": stage_counts,
    }


@app.get("/api/tracks")
def get_tracks(mode: str = Query("sort")) -> dict[str, Any]:
    """
    mode=sort → Ready for Sort (flat)
    mode=add_cues → Add Cues recursive review queue
    """
    if mode == "add_cues":
        raw = list_add_cues_tracks()
        # Skip placement scans — review UI doesn't show them and they dominate latency.
        tracks = [
            _enrich_track(t.to_dict(), review=True, include_placements=False)
            for t in raw
        ]
        ready_n = sum(1 for t in tracks if t.get("readiness", {}).get("ready"))
        partial_n = sum(
            1 for t in tracks if t.get("readiness", {}).get("status") == "partial"
        )
        not_cued_n = sum(
            1
            for t in tracks
            if t.get("readiness", {}).get("status") in {"not_cued", "missing"}
        )
        return {
            "mode": "add_cues",
            "source": str(ADD_CUES),
            "tracks": tracks,
            "counts": {
                "total": len(tracks),
                "ready": ready_n,
                "partial": partial_n,
                "not_cued": not_cued_n,
                "cued": sum(1 for t in tracks if t["is_cued"]),
                "uncued": sum(1 for t in tracks if not t["is_cued"]),
            },
        }

    tracks = [_enrich_track(t.to_dict()) for t in list_ready_tracks()]
    cued = sum(1 for t in tracks if t["is_cued"])
    return {
        "mode": "sort",
        "source": str(READY_FOR_SORT),
        "tracks": tracks,
        "counts": {
            "total": len(tracks),
            "cued": cued,
            "uncued": len(tracks) - cued,
        },
    }


@app.get("/api/libraries")
def get_libraries() -> dict[str, Any]:
    return {"libraries": list_libraries()}


@app.get("/api/folders/{library_name}")
def get_folders(library_name: str, max_depth: int = Query(4, ge=1, le=6)) -> dict[str, Any]:
    try:
        return list_library_tree(library_name, max_depth=max_depth)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/folders")
def post_create_folder(body: CreateFolderRequest) -> dict[str, Any]:
    try:
        created = create_folder(
            body.library,
            name=body.name,
            parent_relative_path=body.parent_relative_path,
        )
        append_action(
            "create_folder",
            name=body.name,
            dest_path=created.get("absolute_path"),
            details={
                "library": body.library,
                "relative_path": created.get("relative_path"),
                "parent_relative_path": body.parent_relative_path,
            },
        )
        return {"ok": True, "folder": created}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/actions")
def get_actions(
    limit: int = Query(100, ge=1, le=1000),
    action: Optional[str] = Query(None),
) -> dict[str, Any]:
    """Newest-first durable action log (sorts, promotes, removes, retries)."""
    rows = read_actions(limit=limit, action=action)
    # Mark which sort/promote rows already have an undo entry.
    undone_ids = {
        (r.get("details") or {}).get("original_id")
        for r in read_actions(limit=2000, action="undo")
        if (r.get("details") or {}).get("original_id")
    }
    for row in rows:
        if row.get("action") in {"sort", "promote"} and row.get("success", True):
            row["undoable"] = row.get("id") not in undone_ids
            row["undone"] = row.get("id") in undone_ids
        else:
            row["undoable"] = False
            row["undone"] = False
    return {
        "ok": True,
        "log_path": str(log_path()),
        "count": len(rows),
        "actions": rows,
    }


@app.post("/api/undo")
def post_undo(body: UndoRequest) -> dict[str, Any]:
    """Reverse a logged sort or promote (file + VDJ FilePath)."""
    try:
        result = undo_action(
            body.action_id,
            dry_run=body.dry_run,
            allow_vdj_running=body.allow_vdj_running,
        )
        return result
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        append_action(
            "undo",
            success=False,
            error=str(exc),
            details={"original_id": body.action_id},
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/grid-preflight")
def get_grid_preflight(
    path: str = Query(...),
    deep: bool = Query(True),
) -> dict[str, Any]:
    """Beatgrid readiness check before AutoCue (deep=onset verify)."""
    if not Path(path).is_file():
        raise HTTPException(status_code=404, detail="Track file not found")
    assessment = assess_grid_for_autocue(path, deep=deep)
    return {"ok": True, "preflight": assessment}


@app.post("/api/delete-cue")
def post_delete_cue(body: DeleteCueRequest) -> dict[str, Any]:
    """Delete one manual cue or loop from VirtualDJ for this track."""
    kind = (body.kind or "").strip().lower()
    if kind not in {"cue", "loop"}:
        raise HTTPException(status_code=400, detail="kind must be 'cue' or 'loop'")
    try:
        result = delete_cue_point(
            body.path,
            kind=kind,
            pos=body.pos,
            num=body.num,
            name=body.name,
            slot=body.slot,
            dry_run=body.dry_run,
            allow_vdj_running=body.allow_vdj_running,
        )
        if not body.dry_run:
            append_action(
                "delete_cue",
                source_path=body.path,
                name=Path(body.path).name,
                details={
                    "kind": kind,
                    "pos": body.pos,
                    "num": body.num,
                    "removed": result.get("removed"),
                    "cue_count_after": result.get("cue_count_after"),
                    "loop_count_after": result.get("loop_count_after"),
                    "database_backup": result.get("database_backup"),
                },
            )
        return {"ok": True, "result": result}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        append_action(
            "delete_cue",
            source_path=body.path,
            name=Path(body.path).name,
            success=False,
            error=str(exc),
            details={"kind": kind, "pos": body.pos},
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/notes")
def get_notes(path: str = Query(...)) -> dict[str, Any]:
    """Read VirtualDJ Comment notes for a track."""
    if not Path(path).is_file():
        raise HTTPException(status_code=404, detail="Track file not found")
    cues = summarize_cues(path)
    return {
        "ok": True,
        "path": path,
        "in_database": cues.in_database,
        "comment": cues.comment or "",
    }


@app.post("/api/notes")
def post_notes(body: NotesRequest) -> dict[str, Any]:
    """Live-update VirtualDJ <Comment> notes for a track."""
    try:
        result = set_track_comment(
            body.path,
            body.comment,
            dry_run=body.dry_run,
            allow_vdj_running=body.allow_vdj_running,
            create_backup=body.create_backup,
        )
        # Don't spam action log on every keystroke; only log non-empty changes
        # when not a no-op. (Optional: skip log entirely for live updates.)
        if not body.dry_run and not result.get("unchanged"):
            append_action(
                "update_notes",
                source_path=body.path,
                name=Path(body.path).name,
                details={
                    "comment_len": len(result.get("comment") or ""),
                    "vdj_running": is_virtualdj_running(),
                },
            )
        return {"ok": True, "result": result}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/halve-bpm")
def post_halve_bpm(body: HalveBpmRequest) -> dict[str, Any]:
    """
    Halve VDJ musical BPM for a track (double-time fix: 136 → 68).

    Rewrites Scan/Tags @Bpm in database.xml. Close VirtualDJ first.
    """
    try:
        result = halve_track_bpm(
            body.path,
            dry_run=body.dry_run,
            allow_vdj_running=body.allow_vdj_running,
            double_instead=body.double_instead,
        )
        if not body.dry_run:
            append_action(
                "double_bpm" if body.double_instead else "halve_bpm",
                source_path=body.path,
                name=Path(body.path).name,
                details={
                    "bpm_before": result.get("bpm_before"),
                    "bpm_after": result.get("bpm_after"),
                    "changes": result.get("changes"),
                    "database_backup": result.get("database_backup"),
                },
            )
        return {"ok": True, "result": result}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        append_action(
            "halve_bpm",
            source_path=body.path,
            name=Path(body.path).name,
            success=False,
            error=str(exc),
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/cues")
def get_cues(path: str = Query(...)) -> dict[str, Any]:
    if not Path(path).is_file():
        raise HTTPException(status_code=404, detail="Track file not found")
    cues = summarize_cues(path)
    payload = cues.to_dict()
    payload["readiness"] = assess_cue_readiness(cues)
    return payload


@app.post("/api/recommend")
def post_recommend(body: RecommendRequest) -> dict[str, Any]:
    path = Path(body.path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Track file not found")
    try:
        result = get_recommender().recommend(
            path,
            force=body.force,
            preferred_library=body.preferred_library,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    payload = result.to_dict()
    if result.error:
        return {"ok": False, "recommendation": payload}
    return {"ok": True, "recommendation": payload}


@app.post("/api/sort")
def post_sort(body: SortRequest) -> dict[str, Any]:
    try:
        result = sort_track(
            body.path,
            library_name=body.library,
            relative_folder=body.relative_folder,
            dry_run=body.dry_run,
            allow_vdj_running=body.allow_vdj_running,
        )
        payload = result.to_dict()
        if not body.dry_run:
            append_action(
                "sort",
                source_path=body.path,
                dest_path=result.dest_path,
                name=Path(body.path).name,
                details={
                    "library_mode": body.library,
                    "relative_folder": body.relative_folder,
                    "library_dests": payload.get("library_dests"),
                    "cues_sorted_path": payload.get("cues_sorted_path"),
                    "cues_sorted_copied": payload.get("cues_sorted_copied"),
                    "cues_sorted_db_cloned": payload.get("cues_sorted_db_cloned"),
                    "database_updated": payload.get("database_updated"),
                    "stems_moved": payload.get("stems_moved"),
                },
            )
        return {"ok": True, "result": payload}
    except PermissionError as exc:
        append_action(
            "sort",
            source_path=body.path,
            name=Path(body.path).name,
            success=False,
            error=str(exc),
            details={
                "library_mode": body.library,
                "relative_folder": body.relative_folder,
            },
        )
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileExistsError as exc:
        append_action(
            "sort",
            source_path=body.path,
            name=Path(body.path).name,
            success=False,
            error=str(exc),
            details={
                "library_mode": body.library,
                "relative_folder": body.relative_folder,
            },
        )
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        append_action(
            "sort",
            source_path=body.path,
            name=Path(body.path).name,
            success=False,
            error=str(exc),
            details={
                "library_mode": body.library,
                "relative_folder": body.relative_folder,
            },
        )
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        append_action(
            "sort",
            source_path=body.path,
            name=Path(body.path).name,
            success=False,
            error=str(exc),
            details={
                "library_mode": body.library,
                "relative_folder": body.relative_folder,
            },
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/remove-ready")
def post_remove_ready(body: RemoveReadyRequest) -> dict[str, Any]:
    """Remove from Ready for Sort only — no library or Cues Sorted placement."""
    try:
        result = remove_from_ready_for_sort(
            body.path,
            dry_run=body.dry_run,
            to_trash=body.to_trash,
        )
        if not body.dry_run:
            append_action(
                "remove_ready",
                source_path=body.path,
                name=result.get("name") or Path(body.path).name,
                details={"to_trash": body.to_trash, "removed": result.get("removed")},
            )
        return {"ok": True, "result": result}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        append_action(
            "remove_ready",
            source_path=body.path,
            name=Path(body.path).name,
            success=False,
            error=str(exc),
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/delete-placement")
def post_delete_placement(body: DeletePlacementRequest) -> dict[str, Any]:
    """
    Delete an Already-in-library placement (House/Zouk/Cues Sorted file)
    and remove its VirtualDJ Song entry (cues + loops for that path).
    """
    try:
        result = delete_library_placement(
            body.path,
            dry_run=body.dry_run,
            to_trash=body.to_trash,
            allow_vdj_running=body.allow_vdj_running,
        )
        if not body.dry_run:
            append_action(
                "delete_placement",
                source_path=body.path,
                name=result.get("name") or Path(body.path).name,
                details={
                    "root_name": result.get("root_name"),
                    "relative_path": result.get("relative_path"),
                    "to_trash": body.to_trash,
                    "removed_files": result.get("removed_files"),
                    "database": result.get("database"),
                    "had_cues": result.get("had_cues"),
                    "had_loops": result.get("had_loops"),
                    "database_backup": result.get("database_backup"),
                },
            )
        return {"ok": True, "result": result}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        append_action(
            "delete_placement",
            source_path=body.path,
            name=Path(body.path).name,
            success=False,
            error=str(exc),
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/retry-cues")
def post_retry_cues(body: RetryCuesRequest) -> dict[str, Any]:
    """
    Re-run vdj-automatic-cuer on a track with bad/missing cues.

    Returns a job id; poll GET /api/retry-cues/{job_id} until status is ok/error/skipped.
    Skipped = beatgrid preflight blocked AutoCue (fix grid in VDJ first).
    """
    try:
        job = start_retry_cues(
            body.path,
            dry_run=body.dry_run,
            allow_vdj_running=body.allow_vdj_running,
            require_grid=body.require_grid,
            deep_grid_check=body.deep_grid_check,
            write_scope=body.write_scope,
        )
        append_action(
            "retry_cues",
            source_path=body.path,
            name=Path(body.path).name,
            success=job.status != "skipped",
            error=job.message if job.status == "skipped" else None,
            details={
                "job_id": job.id,
                "dry_run": body.dry_run,
                "status": job.status,
                "write_scope": job.write_scope,
                "preflight": job.preflight,
            },
        )
        return {"ok": True, "job": job.to_dict()}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        append_action(
            "retry_cues",
            source_path=body.path,
            name=Path(body.path).name,
            success=False,
            error=str(exc),
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/retry-cues/batch")
def post_retry_cues_batch(body: BatchRetryCuesRequest) -> dict[str, Any]:
    """
    Queue AutoCue for many tracks (serialized).

    filter=not_cued → all Add Cues tracks with readiness not_cued/missing.
    """
    paths = list(body.paths or [])
    if body.filter == "not_cued":
        raw = list_add_cues_tracks()
        paths = []
        for t in raw:
            cues = summarize_cues(t.path)
            readiness = assess_cue_readiness(cues)
            if readiness.get("status") in {"not_cued", "missing"}:
                paths.append(t.path)
    if not paths:
        raise HTTPException(
            status_code=400,
            detail="No tracks to queue for AutoCue (pass paths or filter=not_cued)",
        )

    try:
        batch = start_batch_retry_cues(
            paths,
            dry_run=body.dry_run,
            allow_vdj_running=body.allow_vdj_running,
            require_grid=body.require_grid,
            deep_grid_check=body.deep_grid_check,
            write_scope=body.write_scope,
        )
        append_action(
            "retry_cues_batch",
            name=f"{len(paths)} tracks",
            details={
                "batch_id": batch.id,
                "total": len(paths),
                "filter": body.filter,
                "dry_run": body.dry_run,
                "write_scope": body.write_scope,
            },
        )
        return {"ok": True, "batch": batch.to_dict()}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/retry-cues")
def get_retry_cues_jobs() -> dict[str, Any]:
    return {"ok": True, "jobs": list_jobs(), "batches": list_batches()}


@app.get("/api/retry-cues/batch/{batch_id}")
def get_retry_cues_batch(batch_id: str) -> dict[str, Any]:
    batch = get_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    return {"ok": True, "batch": batch.to_dict()}


@app.get("/api/retry-cues/{job_id}")
def get_retry_cues_job(job_id: str) -> dict[str, Any]:
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"ok": True, "job": job.to_dict()}


@app.post("/api/promote")
def post_promote(body: PromoteRequest) -> dict[str, Any]:
    """Move an Add Cues track into Ready for Sort (or another cue stage)."""
    try:
        result = promote_add_cues_track(
            body.path,
            destination_stage=body.destination_stage,
            dry_run=body.dry_run,
            allow_vdj_running=body.allow_vdj_running,
            require_cued=body.require_cued,
        )
        payload = result.to_dict()
        if not body.dry_run:
            append_action(
                "promote",
                source_path=body.path,
                dest_path=result.dest_path,
                name=Path(body.path).name,
                details={
                    "destination_stage": body.destination_stage,
                    "database_updated": payload.get("database_updated"),
                    "stems_moved": payload.get("stems_moved"),
                },
            )
        return {"ok": True, "result": payload}
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PermissionError as exc:
        append_action(
            "promote",
            source_path=body.path,
            name=Path(body.path).name,
            success=False,
            error=str(exc),
            details={"destination_stage": body.destination_stage},
        )
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        append_action(
            "promote",
            source_path=body.path,
            name=Path(body.path).name,
            success=False,
            error=str(exc),
            details={"destination_stage": body.destination_stage},
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/demote-ready")
def post_demote_ready(body: DemoteReadyRequest) -> dict[str, Any]:
    """Send a Ready for Sort track back to Add Cues for re-review."""
    try:
        result = demote_ready_to_add_cues(
            body.path,
            dry_run=body.dry_run,
            allow_vdj_running=body.allow_vdj_running,
            subfolder=body.subfolder,
        )
        payload = result.to_dict()
        if not body.dry_run:
            append_action(
                "demote_ready",
                source_path=body.path,
                dest_path=result.dest_path,
                name=Path(body.path).name,
                details={
                    "subfolder": body.subfolder,
                    "database_updated": payload.get("database_updated"),
                    "stems_moved": payload.get("stems_moved"),
                },
            )
        return {"ok": True, "result": payload}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        append_action(
            "demote_ready",
            source_path=body.path,
            name=Path(body.path).name,
            success=False,
            error=str(exc),
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/audio")
def get_audio(path: str = Query(...)) -> FileResponse:
    audio = _assert_under_cues(Path(path))
    if not audio.is_file():
        raise HTTPException(status_code=404, detail="Audio file not found")
    mime, _ = mimetypes.guess_type(str(audio))
    return FileResponse(
        path=str(audio),
        media_type=mime or "application/octet-stream",
        filename=audio.name,
    )


@app.get("/api/waveform")
def get_waveform(
    path: str = Query(...),
    bins: int = Query(900, ge=64, le=2500),
) -> dict[str, Any]:
    """Peak envelope for the interactive cue waveform view."""
    audio = _assert_under_cues(Path(path))
    if not audio.is_file():
        raise HTTPException(status_code=404, detail="Audio file not found")
    try:
        return build_waveform(audio, bins=bins)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except subprocess.CalledProcessError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"ffmpeg/ffprobe failed: {exc.stderr.decode(errors='replace') if exc.stderr else exc}",
        ) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/meta")
def get_meta(path: str = Query(...)) -> dict[str, Any]:
    """Bitrate / codec metadata for the selected track."""
    audio = _assert_under_cues(Path(path))
    if not audio.is_file():
        raise HTTPException(status_code=404, detail="Audio file not found")
    try:
        return probe_audio_meta(audio)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except subprocess.CalledProcessError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"ffprobe failed: {exc.stderr if exc.stderr else exc}",
        ) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def main() -> None:
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=8787, reload=False)


if __name__ == "__main__":
    main()
