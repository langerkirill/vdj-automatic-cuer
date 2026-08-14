"""Compare AutoCue stem-plan times, ML times, and combos against human cues."""

from __future__ import annotations

from typing import Iterable, Literal, Sequence

How = Literal["union", "intersect", "blend"]


def f1(precision: float, recall: float) -> float:
    if precision <= 0 and recall <= 0:
        return 0.0
    return 2.0 * precision * recall / (precision + recall)


def _near(stamp: float, others: Sequence[float], window: float) -> bool:
    return any(abs(stamp - other) <= window for other in others)


def combine_times(
    ml_times: Iterable[float],
    stem_times: Iterable[float],
    *,
    window: float,
    how: How,
    max_cues: int = 6,
    min_gap: float = 0.0,
) -> list[float]:
    """Merge two timestamp lists. Near-duplicates collapse to the first source."""
    ml = [float(t) for t in ml_times]
    stem = [float(t) for t in stem_times]
    if how == "intersect":
        picked = [t for t in ml if _near(t, stem, window)]
        return _spaced(picked, min_gap=min_gap, max_cues=max_cues)

    if how == "union":
        ordered = list(ml)
        for t in stem:
            if not _near(t, ordered, window):
                ordered.append(t)
        return _spaced(ordered, min_gap=min_gap, max_cues=max_cues)

    # blend: ML first (already ranked), then stem-only extras.
    ordered = list(ml)
    extras = [t for t in stem if not _near(t, ordered, window)]
    return _spaced(ordered + extras, min_gap=min_gap, max_cues=max_cues)


def _spaced(
    times: Sequence[float], *, min_gap: float, max_cues: int
) -> list[float]:
    selected: list[float] = []
    for t in times:
        if any(abs(t - keep) < min_gap - 1e-9 for keep in selected):
            continue
        selected.append(t)
        if len(selected) >= max_cues:
            break
    return sorted(selected)
