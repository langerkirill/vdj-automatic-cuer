"""Final acceptance policy for model-generated cues and loops."""

from __future__ import annotations

import math
from typing import Dict, Iterable, Optional


MIN_CUE_CONFIDENCE = 0.70
MIN_LOOP_CONFIDENCE = 0.75
ALLOWED_LOOP_BEATS = frozenset({4, 8, 16, 32})


def _confidence(item: Dict) -> float:
    try:
        return min(1.0, max(0.0, float(item.get("confidence", 0.0))))
    except (TypeError, ValueError):
        return 0.0


def _valid_time(value) -> Optional[float]:
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return None
    return timestamp if math.isfinite(timestamp) and timestamp >= 0.0 else None


def _has_supported_components(item: Dict) -> bool:
    return bool(item.get("elements"))


def _downbeat_index(timestamp: float, bpm: Optional[float], offset: float) -> int:
    if not bpm or bpm <= 0:
        return round(timestamp * 2.0)
    bar_duration = (60.0 / bpm) * 4.0
    return math.floor(((timestamp - offset) / bar_duration) + 0.5)


def _best_cue_per_downbeat(
    cues: Iterable[Dict], bpm: Optional[float], offset: float
) -> list[Dict]:
    selected: Dict[int, Dict] = {}
    for cue in cues:
        timestamp = float(cue["timestamp"])
        downbeat = _downbeat_index(timestamp, bpm, offset)
        current = selected.get(downbeat)
        if current is None or _confidence(cue) > _confidence(current):
            selected[downbeat] = cue
    return sorted(selected.values(), key=lambda cue: float(cue["timestamp"]))


def apply_precision_gate(
    analysis_data: Dict,
    bpm: Optional[float] = None,
    beatgrid_offset: float = 0.0,
) -> Dict:
    """Reject weak assertions before they can become VirtualDJ POIs."""
    rejected = {
        "low_confidence_cues": 0,
        "invalid_cues": 0,
        "duplicate_cues": 0,
        "low_confidence_loops": 0,
        "invalid_loops": 0,
    }

    accepted_cues = []
    for cue in analysis_data.get("measure_changes", []):
        timestamp = _valid_time(cue.get("timestamp"))
        if timestamp is None or not _has_supported_components(cue):
            rejected["invalid_cues"] += 1
            continue
        if _confidence(cue) < MIN_CUE_CONFIDENCE:
            rejected["low_confidence_cues"] += 1
            continue
        cue["timestamp"] = timestamp
        accepted_cues.append(cue)

    unique_cues = _best_cue_per_downbeat(accepted_cues, bpm, beatgrid_offset)
    rejected["duplicate_cues"] = len(accepted_cues) - len(unique_cues)

    accepted_loops = []
    for loop in analysis_data.get("loop_segments", []):
        start = _valid_time(loop.get("start"))
        try:
            length_beats = int(loop.get("length_beats", 0))
        except (TypeError, ValueError):
            length_beats = 0
        if (
            start is None
            or not _has_supported_components(loop)
            or length_beats not in ALLOWED_LOOP_BEATS
        ):
            rejected["invalid_loops"] += 1
            continue
        if _confidence(loop) < MIN_LOOP_CONFIDENCE:
            rejected["low_confidence_loops"] += 1
            continue
        loop["start"] = start
        loop["length_beats"] = length_beats
        accepted_loops.append(loop)

    analysis_data["measure_changes"] = unique_cues[:6]
    analysis_data["loop_segments"] = sorted(
        accepted_loops, key=lambda loop: float(loop["start"])
    )[:3]
    analysis_data["precision_gate"] = {
        "accepted_cues": len(analysis_data["measure_changes"]),
        "accepted_loops": len(analysis_data["loop_segments"]),
        "rejected": rejected,
    }
    return analysis_data
