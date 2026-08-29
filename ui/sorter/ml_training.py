"""Fire-and-forget ML training updates after a track is successfully cued.

Ingest/retrain runs in a subprocess so sklearn/OpenMP never enter the 8787
UI process (that freeze-locked Set Overview after AutoCue).
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from .autocue_path import ensure_autocue_on_path

ensure_autocue_on_path()

log = logging.getLogger(__name__)
_INGEST_SLOT = threading.Semaphore(1)
_INGEST_TIMEOUT_SECONDS = 20 * 60


def ingest_cli_argv(path: str, *, drop: bool = False) -> list[str]:
    cmd = [sys.executable, "-m", "vdj_cuer.ml.ingest", "--path", str(path)]
    if drop:
        cmd.append("--drop")
    return cmd


def ingest_subprocess_env() -> dict[str, str]:
    root = str(ensure_autocue_on_path())
    try:
        from compute_thread_limits import env_with_compute_thread_limits

        env = env_with_compute_thread_limits(os.environ)
    except Exception:
        env = os.environ.copy()
        env.setdefault("OMP_NUM_THREADS", "1")
        env["PYTHONUNBUFFERED"] = "1"
    pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = root if not pythonpath else f"{root}{os.pathsep}{pythonpath}"
    return env


def _run_ingest_subprocess(path: str, *, drop: bool = False) -> None:
    _INGEST_SLOT.acquire()
    try:
        subprocess.run(
            ingest_cli_argv(path, drop=drop),
            cwd=str(ensure_autocue_on_path()),
            env=ingest_subprocess_env(),
            check=False,
            capture_output=True,
            text=True,
            timeout=_INGEST_TIMEOUT_SECONDS,
        )
    except Exception:
        log.debug("ML ingest subprocess failed for %s", path, exc_info=True)
    finally:
        _INGEST_SLOT.release()


def schedule_training_update(path: str | Path, summary: Any = None) -> None:
    """Queue isolated ingest so Music Sorter stays responsive. Never raises."""
    del summary  # child re-reads VDJ cues; avoid pickling UI objects
    track_id = str(path)
    thread = threading.Thread(
        target=_run_ingest_subprocess,
        args=(track_id,),
        name=f"ml-ingest-{Path(track_id).name}",
        daemon=True,
    )
    thread.start()


def schedule_training_drop(path: str | Path) -> None:
    """Drop a track from the training corpus in an isolated process."""
    track_id = str(path)
    thread = threading.Thread(
        target=_run_ingest_subprocess,
        args=(track_id,),
        kwargs={"drop": True},
        name=f"ml-drop-{Path(track_id).name}",
        daemon=True,
    )
    thread.start()
