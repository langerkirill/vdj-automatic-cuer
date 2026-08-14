"""Map VirtualDJ cue/loop POIs onto bar-1 labels."""

from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Iterable

from .features import iter_bar_times

TRAIN_DIR_MARKERS = (
    "/cues sorted/",
)
EXCLUDE_DIR_MARKERS = (
    "/add cues/",
    "/no cues found/",
    "/ac low quality/",
    "/low quality skip/",
)


def _norm_path(path: str) -> str:
    raw = (path or "").replace("\\", "/")
    return f"/{raw.lower().strip('/')}/"


def is_training_source_path(path: str) -> bool:
    """Cues Sorted only. Never Add Cues, Ready, or libraries."""
    folded = _norm_path(path)
    if any(marker in folded for marker in EXCLUDE_DIR_MARKERS):
        return False
    return any(marker in folded for marker in TRAIN_DIR_MARKERS)


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
