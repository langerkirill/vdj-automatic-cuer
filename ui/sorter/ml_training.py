"""Fire-and-forget ML training updates after a track is successfully cued."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .autocue_path import ensure_autocue_on_path

ensure_autocue_on_path()

log = logging.getLogger(__name__)


def schedule_training_update(path: str | Path, summary: Any = None) -> None:
    """Ingest bar labels for a newly cued / edited track. Never raises."""
    try:
        from vdj_cuer.ml.ingest import schedule_ingest_cued_track

        schedule_ingest_cued_track(path, summary)
    except Exception:
        log.debug("ML training ingest not scheduled for %s", path, exc_info=True)


def schedule_training_drop(path: str | Path) -> None:
    """Drop a track that left the training corpus (skip folders, emptied cues)."""
    try:
        from vdj_cuer.ml.ingest import schedule_drop_track

        schedule_drop_track(path)
    except Exception:
        log.debug("ML training drop not scheduled for %s", path, exc_info=True)
