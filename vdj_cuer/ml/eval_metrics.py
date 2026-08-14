"""DJ-facing holdout metrics: recall @ 1 bar and precision of top-6."""

from __future__ import annotations

from typing import Any, Iterable, Sequence


def _times(rows: Iterable[dict[str, Any]], key: str) -> list[float]:
    out: list[float] = []
    for row in rows:
        if int(row.get(key) or 0) != 1:
            continue
        try:
            out.append(float(row["timestamp"]))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def recall_within(
    human: Sequence[float],
    predicted: Sequence[float],
    *,
    window: float,
) -> float:
    if not human:
        return 1.0
    hits = 0
    for stamp in human:
        if any(abs(stamp - pred) <= window for pred in predicted):
            hits += 1
    return hits / len(human)


def precision_of(
    predicted: Sequence[float],
    human: Sequence[float],
    *,
    window: float,
) -> float:
    if not predicted:
        return 0.0
    hits = sum(
        1 for pred in predicted if any(abs(pred - stamp) <= window for stamp in human)
    )
    return hits / len(predicted)


def bar_window_seconds(bpm: float, bars: float = 1.0) -> float:
    if not bpm or bpm <= 0:
        return 2.0
    return (60.0 / float(bpm)) * 4.0 * float(bars)


def score_track(
    labeled_rows: Sequence[dict[str, Any]],
    predicted_times: Sequence[float],
    *,
    bpm: float,
    label_key: str = "is_cue",
) -> dict[str, float]:
    human = _times(labeled_rows, label_key)
    window = bar_window_seconds(bpm, 1.0)
    return {
        "recall_1bar": recall_within(human, predicted_times, window=window),
        "precision_top": precision_of(predicted_times, human, window=window),
        "n_human": float(len(human)),
        "n_pred": float(len(predicted_times)),
    }
