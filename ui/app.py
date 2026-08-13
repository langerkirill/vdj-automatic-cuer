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
    CUES_SORTED,
    LIBRARIES,
    MIXES_ROOT,
    READY_FOR_SORT,
    VDJ_DATABASE,
)
from sorter.library import (
    build_audio_basename_index,
    create_folder,
    find_cues_sorted_matches,
    find_library_matches,
    add_cues_tracks_by_crate,
    list_add_cues_tracks,
    list_libraries,
    list_library_tree,
    list_ready_tracks,
)
from sorter.recommend import get_recommender
from sorter.autocue_path import ensure_autocue_on_path
from sorter.relocate import (
    assess_cue_readiness,
    delete_add_cues_track,
    delete_library_placement,
    demote_ready_to_add_cues,
    is_virtualdj_running,
    promote_add_cues_track,
    remove_from_ready_for_sort,
    sort_track,
    summarize_cues,
    summarize_cues_for_paths,
)

ensure_autocue_on_path()
# Load GEMINI_API_KEY from Desktop/src .env paths at UI boot (not only CWD).
try:
    from vdj_cuer.common import load_gemini_api_key  # noqa: E402

    load_gemini_api_key()
except Exception:
    pass
from vdj_database_safety import (  # noqa: E402
    ensure_healthy_vdj_database,
    quick_database_fingerprint,
    snapshot_last_good_database,
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
    max_concurrent_jobs,
    start_batch_retry_cues,
    start_retry_cues,
)
from sorter.bpm_edit import halve_track_bpm
from sorter.cue_edit import (
    delete_cue_point,
    scale_loop_point,
    set_poi_color,
    set_poi_position,
)
from sorter.grid_batch import (
    get_grid_fix_batch,
    list_grid_fix_batches,
    start_batch_grid_fix,
)
from sorter.grid_edit import set_beatgrid_anchor
from sorter.grid_preflight import assess_grid_for_autocue, preflight_from_cues
from sorter.notes_edit import set_track_comment
from sorter.poi_rename import set_poi_name
from sorter.undo import undo_action
from sorter.waveform import build_waveform
from sorter.practice_sets import (
    all_tracks_across_mixes,
    get_practice_set_detail,
    list_practice_mixes,
)
from sorter.practice_analyze import get_analyze_job, start_analyze_job
from sorter.transitions_db import (
    ensure_database,
    list_best_practice_scores,
    lookup_options,
    rebuild_database,
    update_practice_score,
)

app = FastAPI(title="Music Sorter", version="0.2.0")
STATIC_DIR = Path(__file__).resolve().parent / "static"


@app.on_event("startup")
def _seed_action_log() -> None:
    """Ensure historical sorts from first session are in the durable log."""
    try:
        seed_historical_sorts(HISTORICAL_SORTS_2026_07_28)
    except OSError:
        pass
    try:
        from sorter.vdj_sideview_watch import start_sideview_recs_watch

        start_sideview_recs_watch()
    except Exception:
        pass


class SortDestination(BaseModel):
    """One House/Zouk folder target (relative path under that library root)."""

    library: str  # House | Zouk | Both (Both expands to both libs at this folder)
    relative_folder: str


class SortRequest(BaseModel):
    path: str
    # Legacy single-target fields (still supported).
    library: str = "Zouk"
    relative_folder: str = ""
    # Preferred: one or more destinations (House and/or Zouk, any folders).
    destinations: Optional[list[SortDestination]] = None
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
    allow_vdj_running: bool = False
    create_backup: bool = True
    remove_from_database: bool = True


class DeleteAddCuesRequest(BaseModel):
    """Trash/delete an Add Cues track + remove its VDJ Song (cues/loops)."""

    path: str
    dry_run: bool = False
    to_trash: bool = True
    allow_vdj_running: bool = False


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
    filter: Optional[str] = None  # not_cued | pajamathon_not_cued | pajamathon_needs_loops
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


class ScaleLoopRequest(BaseModel):
    """Halve or double a loop's Size (beats) in VirtualDJ."""

    path: str
    pos: float
    factor: float  # 0.5 = half, 2.0 = double
    num: Optional[str] = None
    name: Optional[str] = None
    slot: Optional[str] = None
    dry_run: bool = False
    allow_vdj_running: bool = False


class SetCueColorRequest(BaseModel):
    """Change Color on one cue or loop POI."""

    path: str
    kind: str  # cue | loop
    pos: float
    color: str  # blue | green | purple | yellow | orange (or raw VDJ int)
    num: Optional[str] = None
    name: Optional[str] = None
    slot: Optional[str] = None
    dry_run: bool = False
    allow_vdj_running: bool = False


class MovePoiRequest(BaseModel):
    """Move a cue or loop to a new start time (seconds)."""

    path: str
    kind: str  # cue | loop
    pos: float  # current position (to find the POI)
    new_pos: float  # new start time
    num: Optional[str] = None
    name: Optional[str] = None
    slot: Optional[str] = None
    dry_run: bool = False
    allow_vdj_running: bool = False


class RenamePoiRequest(BaseModel):
    """Rename a cue or loop Name attribute in VirtualDJ."""

    path: str
    kind: str  # cue | loop
    pos: float
    new_name: str
    num: Optional[str] = None
    name: Optional[str] = None  # current name (for matching)
    slot: Optional[str] = None
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


class SetBeatgridRequest(BaseModel):
    """Write a new downbeat time into VDJ Scan Phase + beatgrid POI."""

    path: str
    anchor_seconds: float
    dry_run: bool = False
    allow_vdj_running: bool = False


class GridFixBatchRequest(BaseModel):
    """Analyze (and optionally write) BPM half + bar-1 phase for many tracks."""

    paths: list[str] = []
    filter: Optional[str] = None  # pajamathon
    apply: bool = True
    dry_run: bool = False
    allow_vdj_running: bool = False


def _placement_with_cue_status(
    hit: dict[str, Any],
    cue_index: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Attach VDJ cue status for a library / Cues Sorted path."""
    cues = (cue_index or {}).get(hit["path"]) if cue_index is not None else None
    if cues is None:
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
    placement_index: Optional[dict[str, list[dict[str, str]]]] = None,
    cue_index: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    cues = (cue_index or {}).get(track_dict["path"]) if cue_index is not None else None
    if cues is None:
        cues = summarize_cues(track_dict["path"])
    track_dict["cues"] = cues.to_dict()
    track_dict["is_cued"] = cues.is_cued
    track_dict["status"] = (
        "cued"
        if cues.is_cued
        else ("in_database_uncued" if cues.in_database else "missing_from_database")
    )
    if include_placements:
        # Optional shared basename index avoids per-track library rglobs.
        cues_sorted = [
            _placement_with_cue_status(h, cue_index)
            for h in find_cues_sorted_matches(
                track_dict["name"], index=placement_index
            )
        ]
        library_hits = [
            _placement_with_cue_status(h, cue_index)
            for h in find_library_matches(
                track_dict["name"], index=placement_index
            )
        ]
        # Exclude the track's own Add Cues / Ready path if it ever collides.
        src = str(Path(track_dict["path"]).expanduser().resolve())
        library_hits = [
            h
            for h in library_hits
            if str(Path(h["path"]).expanduser().resolve()) != src
        ]
        cues_sorted = [
            h
            for h in cues_sorted
            if str(Path(h["path"]).expanduser().resolve()) != src
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
    allowed_roots = [CUES_ROOT.resolve(), MIXES_ROOT.resolve()]
    for root in allowed_roots:
        try:
            audio.relative_to(root)
            return audio
        except ValueError:
            continue
    raise HTTPException(
        status_code=403,
        detail="Audio must be under the Cues or Mixes folder",
    )


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

    db_health = ensure_healthy_vdj_database(VDJ_DATABASE)
    if db_health.get("ok") and not db_health.get("recovered"):
        try:
            snapshot_last_good_database(VDJ_DATABASE)
        except Exception:
            pass
    fp = db_health.get("fingerprint") or quick_database_fingerprint(VDJ_DATABASE)

    return {
        "ok": True,
        "ready_for_sort": str(READY_FOR_SORT),
        "add_cues": str(ADD_CUES),
        "cues_root": str(CUES_ROOT),
        "ready_exists": READY_FOR_SORT.is_dir(),
        "vdj_database": str(VDJ_DATABASE),
        "vdj_database_exists": VDJ_DATABASE.is_file(),
        "virtualdj_running": is_virtualdj_running(),
        "vdj_database_healthy": bool(fp.get("healthy")),
        "vdj_database_songs": fp.get("song_count"),
        "vdj_database_size": fp.get("size_bytes"),
        "vdj_database_recovered": bool(db_health.get("recovered")),
        "vdj_database_reason": fp.get("reason"),
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
        # One-pass library/archive index so Add Cues can flag tracks already
        # sorted (and whether those copies already have cues).
        placement_index = build_audio_basename_index(
            [*LIBRARIES.values(), CUES_SORTED]
        )
        cue_paths: list[str] = [t.path for t in raw]
        for t in raw:
            for hit in find_cues_sorted_matches(t.name, index=placement_index):
                cue_paths.append(hit["path"])
            for hit in find_library_matches(t.name, index=placement_index):
                cue_paths.append(hit["path"])
        cue_index = summarize_cues_for_paths(cue_paths)
        tracks = [
            _enrich_track(
                t.to_dict(),
                review=True,
                include_placements=True,
                placement_index=placement_index,
                cue_index=cue_index,
            )
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
        paj_tracks = [t for t in tracks if t.get("section") == "pajamathon"]
        paj_not_cued = sum(
            1
            for t in paj_tracks
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
                "pajamathon": len(paj_tracks),
                "pajamathon_not_cued": paj_not_cued,
                "inbox": len(tracks) - len(paj_tracks),
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


@app.post("/api/grid-fix/batch")
def post_grid_fix_batch(body: GridFixBatchRequest) -> dict[str, Any]:
    """Halve double-time BPM and snap the 1 (mod 4 beats) for many tracks."""
    paths = list(body.paths or [])
    if body.filter == "pajamathon" or not paths:
        if not paths:
            paths = [track.path for track in add_cues_tracks_by_crate("pajamathon")]
    if not paths:
        raise HTTPException(
            status_code=400,
            detail="No tracks to grid-fix (pass paths or filter=pajamathon)",
        )
    try:
        batch = start_batch_grid_fix(
            paths,
            apply=body.apply,
            dry_run=body.dry_run,
            allow_vdj_running=body.allow_vdj_running,
        )
        append_action(
            "grid_fix_batch",
            name=f"{len(paths)} tracks",
            details={
                "batch_id": batch.id,
                "total": len(paths),
                "filter": body.filter,
                "apply": body.apply,
                "dry_run": body.dry_run,
            },
        )
        return {"ok": True, "batch": batch.to_dict()}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/grid-fix/batch/{batch_id}")
def get_grid_fix_batch_route(batch_id: str) -> dict[str, Any]:
    batch = get_grid_fix_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Grid-fix batch not found")
    return {"ok": True, "batch": batch.to_dict()}


@app.get("/api/grid-fix")
def get_grid_fix_jobs() -> dict[str, Any]:
    return {"ok": True, "batches": list_grid_fix_batches()}


@app.post("/api/set-beatgrid")
def post_set_beatgrid(body: SetBeatgridRequest) -> dict[str, Any]:
    """Drag-align: write a new '1' (downbeat) into VirtualDJ for this track."""
    try:
        result = set_beatgrid_anchor(
            body.path,
            anchor_seconds=float(body.anchor_seconds),
            dry_run=body.dry_run,
            allow_vdj_running=body.allow_vdj_running,
        )
        if not body.dry_run:
            ch = result.get("changes") or {}
            append_action(
                "set_beatgrid",
                source_path=body.path,
                name=Path(body.path).name,
                details={
                    "anchor": result.get("anchor"),
                    "phase_before": ch.get("phase_before"),
                    "beatgrid_before": ch.get("beatgrid_before"),
                    "scan_phase": result.get("scan_phase"),
                    "beatgrid_pos": result.get("beatgrid_pos"),
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
            "set_beatgrid",
            source_path=body.path,
            name=Path(body.path).name,
            success=False,
            error=str(exc),
            details={"anchor": body.anchor_seconds},
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/scale-loop")
def post_scale_loop(body: ScaleLoopRequest) -> dict[str, Any]:
    """Halve (factor=0.5) or double (factor=2) a loop Size in VirtualDJ."""
    factor = float(body.factor)
    if abs(factor - 0.5) > 1e-9 and abs(factor - 2.0) > 1e-9:
        raise HTTPException(
            status_code=400, detail="factor must be 0.5 (half) or 2 (double)"
        )
    try:
        result = scale_loop_point(
            body.path,
            pos=body.pos,
            factor=factor,
            num=body.num,
            name=body.name,
            slot=body.slot,
            dry_run=body.dry_run,
            allow_vdj_running=body.allow_vdj_running,
        )
        if not body.dry_run:
            ch = result.get("change") or {}
            append_action(
                "scale_loop",
                source_path=body.path,
                name=Path(body.path).name,
                details={
                    "factor": factor,
                    "pos": body.pos,
                    "size_before": ch.get("size_before"),
                    "size_after": ch.get("size_after"),
                    "name": ch.get("name"),
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
            "scale_loop",
            source_path=body.path,
            name=Path(body.path).name,
            success=False,
            error=str(exc),
            details={"factor": body.factor, "pos": body.pos},
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/set-cue-color")
def post_set_cue_color(body: SetCueColorRequest) -> dict[str, Any]:
    """Change the Color of one cue or loop in VirtualDJ."""
    kind = (body.kind or "").strip().lower()
    if kind not in {"cue", "loop"}:
        raise HTTPException(status_code=400, detail="kind must be 'cue' or 'loop'")
    try:
        result = set_poi_color(
            body.path,
            kind=kind,
            pos=body.pos,
            color=body.color,
            num=body.num,
            name=body.name,
            slot=body.slot,
            dry_run=body.dry_run,
            allow_vdj_running=body.allow_vdj_running,
        )
        if not body.dry_run:
            ch = result.get("change") or {}
            append_action(
                "set_cue_color",
                source_path=body.path,
                name=Path(body.path).name,
                details={
                    "kind": kind,
                    "pos": body.pos,
                    "color_name": ch.get("color_name"),
                    "color_before": ch.get("color_before"),
                    "color_after": ch.get("color_after"),
                    "marker_name": ch.get("name"),
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
            "set_cue_color",
            source_path=body.path,
            name=Path(body.path).name,
            success=False,
            error=str(exc),
            details={"kind": kind, "pos": body.pos, "color": body.color},
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/move-poi")
def post_move_poi(body: MovePoiRequest) -> dict[str, Any]:
    """Move a cue or loop start time in VirtualDJ (drag-to-reposition)."""
    kind = (body.kind or "").strip().lower()
    if kind not in {"cue", "loop"}:
        raise HTTPException(status_code=400, detail="kind must be 'cue' or 'loop'")
    try:
        result = set_poi_position(
            body.path,
            kind=kind,
            pos=float(body.pos),
            new_pos=float(body.new_pos),
            num=body.num,
            name=body.name,
            slot=body.slot,
            dry_run=body.dry_run,
            allow_vdj_running=body.allow_vdj_running,
        )
        if not body.dry_run:
            ch = result.get("change") or {}
            append_action(
                "move_poi",
                source_path=body.path,
                name=Path(body.path).name,
                details={
                    "kind": kind,
                    "pos_before": ch.get("pos_before"),
                    "pos_after": ch.get("pos_after"),
                    "marker_name": ch.get("name"),
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
            "move_poi",
            source_path=body.path,
            name=Path(body.path).name,
            success=False,
            error=str(exc),
            details={
                "kind": kind,
                "pos": body.pos,
                "new_pos": body.new_pos,
            },
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/rename-poi")
def post_rename_poi(body: RenamePoiRequest) -> dict[str, Any]:
    """Rename one cue or loop Name in VirtualDJ for this track."""
    kind = (body.kind or "").strip().lower()
    if kind not in {"cue", "loop"}:
        raise HTTPException(status_code=400, detail="kind must be 'cue' or 'loop'")
    try:
        result = set_poi_name(
            body.path,
            kind=kind,
            pos=float(body.pos),
            new_name=body.new_name,
            num=body.num,
            name=body.name,
            slot=body.slot,
            dry_run=body.dry_run,
            allow_vdj_running=body.allow_vdj_running,
        )
        if not body.dry_run:
            ch = result.get("change") or {}
            append_action(
                "rename_poi",
                source_path=body.path,
                name=Path(body.path).name,
                details={
                    "kind": kind,
                    "pos": body.pos,
                    "name_before": ch.get("name_before"),
                    "name_after": ch.get("name_after"),
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
            "rename_poi",
            source_path=body.path,
            name=Path(body.path).name,
            success=False,
            error=str(exc),
            details={
                "kind": kind,
                "pos": body.pos,
                "new_name": body.new_name,
            },
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


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
        dest_payload = None
        if body.destinations:
            dest_payload = [
                {
                    "library": d.library,
                    "relative_folder": d.relative_folder,
                }
                for d in body.destinations
            ]
        result = sort_track(
            body.path,
            library_name=body.library,
            relative_folder=body.relative_folder,
            destinations=dest_payload,
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
                    "destinations": dest_payload
                    or [
                        {
                            "library": body.library,
                            "relative_folder": body.relative_folder,
                        }
                    ],
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
            allow_vdj_running=body.allow_vdj_running,
            create_backup=body.create_backup,
            remove_from_database=body.remove_from_database,
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


@app.post("/api/delete-add-cues")
def post_delete_add_cues(body: DeleteAddCuesRequest) -> dict[str, Any]:
    """
    Delete an Add Cues track entirely: audio + stems to Trash, and remove the
    VirtualDJ Song entry (cues + loops for that path).
    """
    try:
        result = delete_add_cues_track(
            body.path,
            dry_run=body.dry_run,
            to_trash=body.to_trash,
            allow_vdj_running=body.allow_vdj_running,
        )
        if not body.dry_run:
            append_action(
                "delete_add_cues",
                source_path=body.path,
                name=result.get("name") or Path(body.path).name,
                details={
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
            "delete_add_cues",
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
    Queue AutoCue for many tracks. Up to MUSIC_SORTER_AUTOCUE_CONCURRENCY
    songs analyze in parallel; database.xml writes stay one-at-a-time.

    filter=not_cued → all Add Cues tracks with readiness not_cued/missing.
    filter=pajamathon_not_cued → same, but only Add Cues/Pajamathon.
    filter=pajamathon_needs_loops → Pajamathon tracks with fewer than 2 loops.
    """
    paths = list(body.paths or [])
    if body.filter in {
        "not_cued",
        "pajamathon_not_cued",
        "needs_loops",
        "pajamathon_needs_loops",
    }:
        crate = "pajamathon" if "pajamathon" in body.filter else "all"
        raw = add_cues_tracks_by_crate(crate)
        summaries = summarize_cues_for_paths([t.path for t in raw])
        paths = []
        want_loops = body.filter in {"needs_loops", "pajamathon_needs_loops"}
        for t in raw:
            cues = summaries.get(t.path)
            if want_loops:
                loops = int(getattr(cues, "loop_count", 0) or 0) if cues else 0
                if loops < 2:
                    paths.append(t.path)
                continue
            readiness = assess_cue_readiness(cues) if cues is not None else {}
            if readiness.get("status") in {"not_cued", "missing"}:
                paths.append(t.path)
    if not paths:
        raise HTTPException(
            status_code=400,
            detail=(
                "No tracks to queue for AutoCue "
                "(pass paths or filter=not_cued / pajamathon_not_cued / "
                "pajamathon_needs_loops)"
            ),
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
    return {
        "ok": True,
        "jobs": list_jobs(),
        "batches": list_batches(),
        "max_concurrent": max_concurrent_jobs(),
    }


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


@app.get("/api/practice/sets")
def practice_sets() -> dict[str, Any]:
    """List practice mix recordings (Music/Mixes)."""
    try:
        db_stats = ensure_database()
    except Exception as exc:
        db_stats = {"error": str(exc)}
    mixes = list_practice_mixes()
    return {
        "mixes": mixes,
        "mixes_root": str(MIXES_ROOT),
        "transitions_db": db_stats,
    }


@app.get("/api/practice/set")
def practice_set_detail(path: str = Query(...)) -> dict[str, Any]:
    """Tracklist + transitions + alternate options for one practice mix."""
    try:
        ensure_database()
        return get_practice_set_detail(path, include_alternatives=True)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/practice/tracks")
def practice_all_tracks() -> dict[str, Any]:
    """Union of tracks played across recent practice mixes."""
    try:
        return all_tracks_across_mixes()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


class PracticeAnalyzeRequest(BaseModel):
    path: str
    force: bool = False
    max_transitions: Optional[int] = None


@app.post("/api/practice/analyze")
def practice_analyze(req: PracticeAnalyzeRequest) -> dict[str, Any]:
    """Start Gemini analysis of transitions in a practice mix (background job)."""
    try:
        ensure_database()
        job = start_analyze_job(
            req.path,
            force=req.force,
            max_transitions=req.max_transitions,
        )
        return {"job": job}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/practice/analyze/{job_id}")
def practice_analyze_status(job_id: str) -> dict[str, Any]:
    job = get_analyze_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Unknown analyze job")
    return {"job": job}


class PracticeScoreUpdateRequest(BaseModel):
    id: Optional[int] = None
    mix_path: Optional[str] = None
    transition_index: Optional[int] = None
    priority: Optional[int] = None
    save_for_set: Optional[bool] = None


@app.get("/api/practice/best")
def practice_best(
    prefix: str = Query("pj"),
    min_overall: float = Query(7.0),
    saved_only: bool = Query(False),
    min_priority: int = Query(0, ge=0, le=5),
) -> dict[str, Any]:
    """Cross-mix shortlist from Gemini rankings + user priority tiers."""
    try:
        ensure_database()
        items = list_best_practice_scores(
            prefix=prefix,
            min_overall=min_overall,
            saved_only=saved_only,
            min_priority=min_priority,
        )
        return {
            "items": items,
            "count": len(items),
            "prefix": prefix,
            "min_overall": min_overall,
            "saved_only": saved_only,
            "min_priority": min_priority,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/practice/score")
def practice_score_update(req: PracticeScoreUpdateRequest) -> dict[str, Any]:
    """Update manual priority (0–5) and/or save_for_set on a scored transition."""
    if req.priority is None and req.save_for_set is None:
        raise HTTPException(
            status_code=400, detail="Provide priority and/or save_for_set"
        )
    if req.priority is not None and not (0 <= int(req.priority) <= 5):
        raise HTTPException(status_code=400, detail="priority must be 0–5")
    if req.id is None and (not req.mix_path or req.transition_index is None):
        raise HTTPException(
            status_code=400, detail="Provide id or mix_path+transition_index"
        )
    try:
        ensure_database()
        row = update_practice_score(
            id=req.id,
            mix_path=req.mix_path,
            transition_index=req.transition_index,
            priority=req.priority,
            save_for_set=req.save_for_set,
        )
        return {"score": row}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/transitions/options")
def transition_options(
    from_track: str = Query(..., min_length=1),
    limit: int = Query(12, ge=1, le=40),
) -> dict[str, Any]:
    """Known alternate destinations for a track from notes + history."""
    try:
        ensure_database()
        opts = lookup_options(from_track, limit=limit)
        return {"from_track": from_track, "options": opts, "count": len(opts)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/transitions/rebuild")
def transitions_rebuild() -> dict[str, Any]:
    """Re-import transition notes + dj_transitions.csv into SQLite."""
    try:
        return rebuild_database()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/transitions/stats")
def transitions_stats() -> dict[str, Any]:
    try:
        return ensure_database()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ── Live transition recommendations (VDJ now-playing → energy buckets) ─────


class TransitionRecsRequest(BaseModel):
    path: Optional[str] = None  # override now-playing
    use_gemini: bool = True
    force_rescan: bool = False
    sync: bool = False  # if true, block until complete (tests / small libs)


@app.get("/api/recs/now-playing")
def recs_now_playing(
    fast: bool = Query(False),
    refresh: bool = Query(False),
) -> dict[str, Any]:
    """Most recently played track from VirtualDJ History (enriched with BPM/key)."""
    from sorter.vdj_now_playing import format_lastplay, get_now_playing

    try:
        np = get_now_playing(
            enrich=not fast,
            prefer_latest_file=True,
            force_rescan=refresh,
        )
        if np is None:
            return {"ok": True, "now_playing": None, "message": "No VDJ history plays found"}
        payload = np.to_dict()
        payload["lastplay_iso"] = format_lastplay(np.lastplay_unix)
        return {"ok": True, "now_playing": payload}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/recs/transitions")
def recs_transitions(body: TransitionRecsRequest) -> dict[str, Any]:
    """
    Recommend higher / same / lower energy next tracks.

    Filters cued library songs to ±BPM and Camelot-compatible keys, merges
    history/notes, then ranks with Gemini (or heuristics).
    """
    from sorter.transition_recs import get_job, recommend_transitions, start_recommend_job

    try:
        if body.sync:
            result = recommend_transitions(
                path=body.path,
                use_gemini=body.use_gemini,
                force_rescan=body.force_rescan,
            )
            return {"ok": True, "sync": True, "result": result}
        job = start_recommend_job(
            path=body.path,
            use_gemini=body.use_gemini,
            force_rescan=body.force_rescan,
        )
        return {"ok": True, "sync": False, "job": job.to_dict()}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/recs/transitions/{job_id}")
def recs_transitions_status(job_id: str) -> dict[str, Any]:
    from sorter.transition_recs import get_job

    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Unknown recommendation job")
    return {"job": job.to_dict()}


@app.get("/api/recs/sideview")
def recs_sideview_status() -> dict[str, Any]:
    """Where VDJ Sideview rec lists live, and whether shortcuts are pinned."""
    from sorter.vdj_sideview_recs import (
        BUCKET_FOLDERS,
        COMBINED_NAME,
        VDJ_MYLISTS,
        ensure_sideview_shortcuts,
    )

    names = [COMBINED_NAME, *BUCKET_FOLDERS.values()]
    files = {
        name: {
            "path": str(VDJ_MYLISTS / f"{name}.vdjfolder"),
            "exists": (VDJ_MYLISTS / f"{name}.vdjfolder").is_file(),
        }
        for name in names
    }
    paths = [VDJ_MYLISTS / f"{name}.vdjfolder" for name in names]
    shortcuts = ensure_sideview_shortcuts(paths)
    return {"ok": True, "mylists": str(VDJ_MYLISTS), "files": files, "shortcuts": shortcuts}


class AssembleRequest(BaseModel):
    event_name: str = "Pajamathon"
    brief: str = ""
    library: str = "Zouk"
    chunk_size: int = 16
    target: int = 400
    use_gemini: bool = True
    scan_all: bool = False
    lane_shares: Optional[dict[str, float]] = None
    min_fit: Optional[float] = None


class AssembleRebalanceRequest(BaseModel):
    event_name: str = "Pajamathon"
    target: int = 400
    lane_shares: Optional[dict[str, float]] = None
    min_fit: Optional[float] = None


class AssembleMixPrefsRequest(BaseModel):
    lane_shares: Optional[dict[str, float]] = None
    min_fit: Optional[float] = None


@app.get("/api/assemble/preview")
def assemble_preview(library: str = Query("Zouk")) -> dict[str, Any]:
    from sorter.playlist_assemble import preview_library

    try:
        return preview_library(library)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/assemble/start")
def assemble_start(body: AssembleRequest) -> dict[str, Any]:
    from sorter.playlist_assemble import start_assemble_job

    try:
        job = start_assemble_job(
            event_name=body.event_name,
            brief=body.brief or None,
            library=body.library,
            chunk_size=body.chunk_size,
            target=body.target,
            use_gemini=body.use_gemini,
            scan_all=body.scan_all,
            lane_shares=body.lane_shares,
            min_fit=body.min_fit,
        )
        return {"ok": True, "job": job.to_dict()}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/assemble/status/{job_id}")
def assemble_status(job_id: str) -> dict[str, Any]:
    from sorter.playlist_assemble import get_job

    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Unknown assemble job")
    return {"job": job.to_dict()}


@app.post("/api/assemble/stop/{job_id}")
def assemble_stop(job_id: str) -> dict[str, Any]:
    from sorter.playlist_assemble import cancel_job

    job = cancel_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Unknown assemble job")
    return {"ok": True, "job": job.to_dict()}


@app.get("/api/assemble/latest")
def assemble_latest() -> dict[str, Any]:
    from sorter.playlist_assemble import latest_job

    job = latest_job()
    return {"ok": True, "job": job.to_dict() if job else None}


@app.post("/api/assemble/rebalance")
def assemble_rebalance(body: AssembleRebalanceRequest) -> dict[str, Any]:
    from sorter.playlist_assemble import rebalance_latest_playlist

    try:
        return rebalance_latest_playlist(
            shares=body.lane_shares,
            target=body.target,
            event_name=body.event_name,
            min_fit=body.min_fit,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/assemble/mix-prefs")
def assemble_mix_prefs_get() -> dict[str, Any]:
    from sorter.playlist_assemble import load_mix_prefs

    prefs = load_mix_prefs()
    return {"ok": True, **prefs}


@app.post("/api/assemble/mix-prefs")
def assemble_mix_prefs_set(body: AssembleMixPrefsRequest) -> dict[str, Any]:
    from sorter.playlist_assemble import save_mix_prefs

    prefs = save_mix_prefs(body.lane_shares, body.min_fit)
    return {"ok": True, **prefs}


@app.post("/api/assemble/export")
def assemble_export() -> dict[str, Any]:
    from sorter.playlist_assemble import export_latest_playlist

    try:
        files = export_latest_playlist()
        return {"ok": True, "files": files}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
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
