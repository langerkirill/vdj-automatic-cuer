"""Incrementally add a newly cued track to the ML training set and retrain."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from .dataset import (
    DEFAULT_LABELS,
    drop_track_rows,
    labeled_rows_for_track,
    train_from_labels_file,
    upsert_track_rows,
)
from .labels import has_training_cue_points, is_training_source_path, is_trainable_track
from .match import assess_autocue_match
from .model import DEFAULT_ARTIFACT

log = logging.getLogger(__name__)

_INGEST_LOCK = threading.RLock()


def _resolve_summary(path: str | Path, summary: Any, database_path: Path | None) -> Any:
    if summary is not None:
        return summary
    try:
        from .dataset import ensure_ui_on_path

        ensure_ui_on_path()
        from sorter.relocate import summarize_cues

        return summarize_cues(path, database_path)
    except Exception:
        return None


def ingest_cued_track(
    path: str | Path,
    summary: Any = None,
    *,
    labels_path: Path | None = None,
    model_path: Path | None = None,
    database_path: Path | None = None,
    retrain: bool = True,
    seed: int = 0,
) -> dict[str, Any]:
    """Append/replace bar labels for one cued track and optionally retrain.

    Skips libraries, Mixes, No Cues Found, and low-quality folders. Skips
    empty/failed AutoCue results (no cue or loop POIs). Never raises to the
    caller — AutoCue and Music Sorter must keep working if ingest fails.
    """
    track_id = str(Path(path))
    dest = Path(labels_path) if labels_path else DEFAULT_LABELS
    artifact = Path(model_path) if model_path else DEFAULT_ARTIFACT
    result: dict[str, Any] = {
        "ok": False,
        "path": track_id,
        "reason": "",
        "rows": 0,
        "retrained": False,
        "labels": str(dest),
        "artifact": str(artifact),
    }
    try:
        if not is_training_source_path(track_id):
            result["reason"] = "not_training_source"
            return result
        resolved = _resolve_summary(track_id, summary, database_path)
        if not has_training_cue_points(resolved):
            with _INGEST_LOCK:
                dropped = drop_track_rows(dest, track_id)
            result["reason"] = "no_accepted_cues"
            result["dropped"] = dropped
            if dropped and retrain and dest.is_file():
                try:
                    metrics = train_from_labels_file(dest, artifact, seed=seed)
                    result["retrained"] = True
                    result["metrics"] = metrics
                except ValueError:
                    pass
            return result
        if not is_trainable_track(track_id, resolved):
            result["reason"] = "not_trainable"
            return result
        match = assess_autocue_match(track_id, resolved)
        result["autocue_match"] = match
        if match.get("matches"):
            with _INGEST_LOCK:
                dropped = drop_track_rows(dest, track_id)
            result["reason"] = "autocue_matches"
            result["dropped"] = dropped
            if dropped and retrain and dest.is_file():
                try:
                    metrics = train_from_labels_file(dest, artifact, seed=seed)
                    result["retrained"] = True
                    result["metrics"] = metrics
                except ValueError:
                    pass
            return result
        rows = labeled_rows_for_track(track_id, resolved)
        if not rows:
            result["reason"] = "no_label_rows"
            return result
        with _INGEST_LOCK:
            upsert_track_rows(dest, track_id, rows)
            result["rows"] = len(rows)
            result["ok"] = True
            result["reason"] = "ingested"
            if retrain:
                metrics = train_from_labels_file(dest, artifact, seed=seed)
                result["retrained"] = True
                result["metrics"] = metrics
                result["artifact"] = str(metrics.get("artifact") or artifact)
        return result
    except Exception as exc:
        log.exception("ML ingest failed for %s", track_id)
        result["reason"] = f"error:{exc}"
        return result


def drop_cued_track(
    path: str | Path,
    *,
    labels_path: Path | None = None,
    model_path: Path | None = None,
    retrain: bool = True,
    seed: int = 0,
) -> dict[str, Any]:
    """Remove a track from the label file (skip-folder promote, last cue deleted)."""
    track_id = str(Path(path))
    dest = Path(labels_path) if labels_path else DEFAULT_LABELS
    artifact = Path(model_path) if model_path else DEFAULT_ARTIFACT
    result: dict[str, Any] = {
        "ok": True,
        "path": track_id,
        "dropped": 0,
        "retrained": False,
        "labels": str(dest),
    }
    try:
        with _INGEST_LOCK:
            dropped = drop_track_rows(dest, track_id)
            result["dropped"] = dropped
            if dropped and retrain and dest.is_file():
                try:
                    metrics = train_from_labels_file(dest, artifact, seed=seed)
                    result["retrained"] = True
                    result["metrics"] = metrics
                except ValueError:
                    pass
        return result
    except Exception as exc:
        log.exception("ML drop failed for %s", track_id)
        result["ok"] = False
        result["reason"] = f"error:{exc}"
        return result


def schedule_ingest_cued_track(
    path: str | Path,
    summary: Any = None,
    **kwargs: Any,
) -> None:
    """Fire-and-forget ingest so Music Sorter / AutoCue stay responsive."""
    track_id = str(path)
    thread = threading.Thread(
        target=ingest_cued_track,
        args=(track_id, summary),
        kwargs=kwargs,
        name=f"ml-ingest-{Path(track_id).name}",
        daemon=True,
    )
    thread.start()


def schedule_drop_track(path: str | Path, **kwargs: Any) -> None:
    track_id = str(path)
    thread = threading.Thread(
        target=drop_cued_track,
        args=(track_id,),
        kwargs=kwargs,
        name=f"ml-drop-{Path(track_id).name}",
        daemon=True,
    )
    thread.start()
