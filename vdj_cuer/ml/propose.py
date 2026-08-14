"""Pick 2–6 well-spaced, clean-entry bars from classifier scores."""

from __future__ import annotations

import os
from typing import Any, Iterable, Optional

from vdj_cuer.stem_cue_plan import MAX_STEM_CUES, MIN_SPACING_BEATS, _default_color, _default_name

from .ab import combine_times
from .eval_metrics import bar_window_seconds


def propose_cues(
    rows: Iterable[dict[str, Any]],
    *,
    bpm: float,
    max_cues: int = MAX_STEM_CUES,
    min_spacing_beats: float = MIN_SPACING_BEATS,
    require_clean: bool = True,
) -> list[dict[str, Any]]:
    bar_candidates = [dict(row) for row in rows if row]
    if not bar_candidates or not bpm or bpm <= 0:
        return []
    min_gap = (60.0 / float(bpm)) * float(min_spacing_beats)
    ranked = sorted(
        bar_candidates,
        key=lambda row: (-float(row.get("score") or 0.0), float(row.get("timestamp") or 0.0)),
    )
    selected: list[dict[str, Any]] = []
    for row in ranked:
        if require_clean and float(row.get("clean_entry") or 0.0) < 0.5:
            continue
        ts = float(row.get("timestamp") or 0.0)
        if any(abs(ts - float(keep["timestamp"])) < min_gap - 1e-9 for keep in selected):
            continue
        item = dict(row)
        elements = list(item.get("elements") or [])
        item.setdefault("cue_name", _default_name(elements))
        item.setdefault("color", _default_color(elements))
        item.setdefault("role", "section")
        item.setdefault("assertion_source", "ml_cue_plan")
        item.setdefault("confidence", max(0.72, min(0.98, float(item.get("score") or 0.7))))
        selected.append(item)
        if len(selected) >= int(max_cues):
            break
    selected.sort(key=lambda row: float(row["timestamp"]))
    return selected


def ml_cues_enabled() -> bool:
    raw = (os.environ.get("AUTOCUE_DISABLE_ML") or "").strip().lower()
    return raw not in {"1", "true", "yes", "on"}


def blend_cue_plans(
    ml_cues: Iterable[dict[str, Any]],
    stem_cues: Iterable[dict[str, Any]],
    *,
    bpm: float,
    max_cues: int = MAX_STEM_CUES,
    min_spacing_beats: float = MIN_SPACING_BEATS,
) -> list[dict[str, Any]]:
    """ML first, then stem-plan extras that are at least one bar away."""
    ml_list = [dict(row) for row in ml_cues if row]
    stem_list = [dict(row) for row in stem_cues if row]
    if not ml_list:
        return list(stem_list)[: int(max_cues)]
    if not stem_list:
        return list(ml_list)[: int(max_cues)]
    if not bpm or bpm <= 0:
        return list(ml_list)[: int(max_cues)]

    window = bar_window_seconds(bpm, 1.0)
    min_gap = (60.0 / float(bpm)) * float(min_spacing_beats)
    ml_times = [float(row["timestamp"]) for row in ml_list]
    stem_times = [float(row["timestamp"]) for row in stem_list]
    merged = combine_times(
        ml_times,
        stem_times,
        window=window,
        how="blend",
        max_cues=max_cues,
        min_gap=min_gap,
    )

    def _lookup(t: float) -> dict[str, Any]:
        best = None
        best_dist = None
        for row in ml_list + stem_list:
            dist = abs(float(row["timestamp"]) - t)
            if best_dist is None or dist < best_dist:
                best = row
                best_dist = dist
        item = dict(best or {"timestamp": t})
        item["timestamp"] = float(t)
        item["assertion_source"] = "hybrid_cue_plan"
        return item

    return [_lookup(t) for t in merged]


def propose_ml_cues(
    profiles: dict,
    *,
    bpm: float,
    offset: float,
    duration: float,
    audio_path: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Score every VDJ 1 with the trained booster. Empty if disabled or no artifact."""
    if not ml_cues_enabled() or not bpm or bpm <= 0 or duration <= 0:
        return []
    if not audio_path or not os.path.isfile(audio_path):
        return []
    try:
        import numpy as np

        from .features import feature_matrix, rows_for_track
        from .model import load_cue_bar_model
    except Exception:
        return []
    model = load_cue_bar_model()
    if model is None:
        return []
    try:
        rows = rows_for_track(
            profiles,
            duration=duration,
            bpm=bpm,
            offset=offset,
            audio_path=audio_path,
        )
        if not rows:
            return []
        X = np.asarray(feature_matrix(rows), dtype=float)
        scores = model.predict_cue_proba(X)
        scored = []
        for row, score in zip(rows, scores):
            item = dict(row)
            item["score"] = float(score)
            scored.append(item)
        return propose_cues(scored, bpm=bpm)
    except Exception:
        return []
