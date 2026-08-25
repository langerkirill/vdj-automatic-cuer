"""Map VirtualDJ cue/loop POIs onto bar-1 labels."""

from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Iterable

from .features import iter_bar_times

TRAIN_DIR_MARKERS = (
    "/cues sorted/",
    "/ready for sort/",
    "/add cues/",
)
EXCLUDE_DIR_MARKERS = (
    "/no cues found/",
    "/ac low quality/",
    "/low quality skip/",
    "/mixes/",
)


def _norm_path(path: str) -> str:
    raw = (path or "").replace("\\", "/")
    return f"/{raw.lower().strip('/')}/"


def is_training_source_path(path: str) -> bool:
    """Cue-pipeline folders that may contribute labels.

    Cues Sorted, Ready For Sort, and Add Cues are eligible. Libraries,
    Mixes, No Cues Found, and low-quality skip folders are never sources.
    Callers must still require real cue/loop POIs via
    ``has_training_cue_points`` / ``is_trainable_track``.
    """
    folded = _norm_path(path)
    if any(marker in folded for marker in EXCLUDE_DIR_MARKERS):
        return False
    return any(marker in folded for marker in TRAIN_DIR_MARKERS)


def _summary_field(summary: Any, name: str, default: Any = None) -> Any:
    if summary is None:
        return default
    if isinstance(summary, dict):
        return summary.get(name, default)
    return getattr(summary, name, default)


def has_training_cue_points(summary: Any) -> bool:
    """True when summarize_cues found real cue/loop POIs and a usable BPM.

    Empty or failed AutoCue results (no points, missing BPM, not in VDJ)
    must not enter the training set.
    """
    if summary is None:
        return False
    in_database = _summary_field(summary, "in_database", True)
    if in_database is False:
        return False
    try:
        bpm = float(_summary_field(summary, "bpm") or 0.0)
    except (TypeError, ValueError):
        return False
    if bpm <= 0:
        return False
    points = list(_summary_field(summary, "points") or [])
    try:
        cue_count = int(_summary_field(summary, "cue_count") or 0)
        loop_count = int(_summary_field(summary, "loop_count") or 0)
    except (TypeError, ValueError):
        cue_count = 0
        loop_count = 0
    if points:
        return True
    return cue_count > 0 or loop_count > 0


def is_trainable_track(path: str, summary: Any) -> bool:
    """Path is a cue-pipeline source and the track has accepted cue points."""
    return is_training_source_path(path) and has_training_cue_points(summary)


def _point_kind(point: dict[str, Any]) -> str:
    raw = str(point.get("kind") or point.get("type") or "").strip().lower()
    return "loop" if raw == "loop" else "cue"


def _point_pos(point: dict[str, Any]) -> float | None:
    try:
        value = float(point.get("pos", point.get("timestamp", point.get("start"))))
    except (TypeError, ValueError):
        return None
    if value < 0:
        return None
    return value


def label_bars(
    points: Iterable[dict[str, Any]],
    *,
    duration: float,
    bpm: float,
    offset: float,
    slack_beats: float = 0.5,
) -> list[dict[str, Any]]:
    """One row per bar-1 with is_cue / is_loop_start in {0, 1}."""
    times = iter_bar_times(duration=duration, bpm=bpm, offset=offset)
    beat = (60.0 / float(bpm)) if bpm and bpm > 0 else 0.5
    slack = beat * float(slack_beats)
    cues: list[float] = []
    loops: list[float] = []
    for point in points or []:
        pos = _point_pos(point)
        if pos is None:
            continue
        if _point_kind(point) == "loop":
            loops.append(pos)
        else:
            cues.append(pos)

    def hits(anchor: float, stamps: list[float]) -> bool:
        return any(abs(anchor - stamp) <= slack for stamp in stamps)

    rows: list[dict[str, Any]] = []
    for t in times:
        rows.append(
            {
                "timestamp": t,
                "is_cue": 1 if hits(t, cues) else 0,
                "is_loop_start": 1 if hits(t, loops) else 0,
            }
        )
    return rows


def attach_labels(
    feature_rows: list[dict[str, Any]],
    label_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_time = {round(float(row["timestamp"]), 4): row for row in label_rows}
    out: list[dict[str, Any]] = []
    for row in feature_rows:
        key = round(float(row["timestamp"]), 4)
        labels = by_time.get(key) or {"is_cue": 0, "is_loop_start": 0}
        merged = dict(row)
        merged["is_cue"] = int(labels["is_cue"])
        merged["is_loop_start"] = int(labels["is_loop_start"])
        out.append(merged)
    return out


def path_leaf(path: str) -> str:
    posix = PurePosixPath(path.replace("\\", "/"))
    name = posix.name or PureWindowsPath(path).name
    return name
