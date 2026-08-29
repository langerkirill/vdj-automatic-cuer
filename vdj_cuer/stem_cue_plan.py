"""Stem-first cue planning: change points from isolated VDJ stem envelopes.

Stems decide *when*. The VDJ grid decides the exact downbeat. Gemini may name
a planned time; it does not invent timestamps.
"""

from __future__ import annotations

import math
from typing import Dict, Iterable, List, Optional, Sequence

from .common import PHRASE_BEATS, quantize_to_phrase_one
from .stem_evidence import (
    StemProfile,
    is_clean_phrase_entry,
    measure_stem_evidence,
)

MAX_STEM_CUES = 6
MIN_STEM_CUES = 2
MIN_SPACING_BEATS = float(PHRASE_BEATS)
HOLD_BARS = 1


def snap_to_downbeat(time_sec: float, bpm: float, offset: float = 0.0) -> float:
    """Upcoming yellow [1] (4 bars). Never the previous phrase 1."""
    return quantize_to_phrase_one(time_sec, bpm, offset)


def _signature(elements: Iterable[str]) -> frozenset[str]:
    values = {str(item).strip().lower() for item in elements}
    kept = set()
    if "drums" in values:
        kept.add("drums")
    if "vocals" in values:
        kept.add("vocals")
    if "bass" in values:
        kept.add("bass")
    if values.intersection({"piano", "synth", "strings", "guitar"}):
        kept.add("instruments")
    return frozenset(kept)


def _default_name(elements: Sequence[str]) -> str:
    items = [str(item) for item in elements]
    if "vocals" in items and "drums" in items:
        return "Vocal Mix"
    if "vocals" in items:
        return "Vocal Break"
    if "drums" in items and "bass" in items:
        return "Groove"
    if "drums" in items:
        return "Drums"
    if "bass" in items or any(
        name in items for name in ("piano", "synth", "strings", "guitar")
    ):
        return "Melody"
    return "Beat Entry"


def _default_color(elements: Sequence[str]) -> str:
    items = set(elements)
    has_drums = "drums" in items
    has_vocals = "vocals" in items
    if has_vocals and has_drums:
        return "yellow"
    if has_vocals:
        return "orange"
    if has_drums and (
        "bass" in items
        or items.intersection({"piano", "synth", "strings", "guitar"})
    ):
        return "green"
    if has_drums:
        return "purple"
    return "blue"


def plan_stem_cues(
    profiles: Dict[str, StemProfile],
    *,
    bpm: float,
    offset: float,
    duration: float,
    max_cues: int = MAX_STEM_CUES,
) -> List[dict]:
    """Return 2–6 grid-snapped cues from measured stem change-points."""
    if not profiles or not bpm or bpm <= 0 or duration <= 0:
        return []
    beat = 60.0 / float(bpm)
    phrase = beat * float(PHRASE_BEATS)
    if phrase <= 0:
        return []
    origin = max(0.0, float(offset))
    t = origin

    rows: List[dict] = []
    prev_sig: Optional[frozenset[str]] = None
    while t + 0.25 < duration:
        evidence = measure_stem_evidence(
            profiles,
            timestamp=t,
            duration_seconds=min(phrase, 4.0),
            model_elements=["drums", "vocals", "bass", "synth"],
            centered=True,
            strict_drums=False,
        )
        sig = _signature(evidence.elements)
        first_signature = prev_sig is None and bool(sig)
        changed = first_signature or (
            prev_sig is not None and sig != prev_sig and bool(sig)
        )
        hold_end = t + phrase * HOLD_BARS
        held = True
        if changed and not first_signature:
            later = measure_stem_evidence(
                profiles,
                timestamp=min(hold_end, duration - 0.1),
                duration_seconds=min(phrase, 4.0),
                model_elements=["drums", "vocals", "bass", "synth"],
                centered=True,
                strict_drums=False,
            )
            held = _signature(later.elements) == sig
        clean = is_clean_phrase_entry(
            profiles, timestamp=t, elements=evidence.elements
        )
        if first_signature or (changed and held and clean):
            turned_on = sig - (prev_sig or frozenset())
            score = float(len(turned_on) + len(sig.symmetric_difference(prev_sig or set())))
            score += min(1.0, float(evidence.confidence or 0.0))
            rows.append(
                {
                    "timestamp": round(quantize_to_phrase_one(t, bpm, origin), 6),
                    "elements": list(evidence.elements),
                    "cue_name": _default_name(evidence.elements),
                    "color": _default_color(evidence.elements),
                    "role": "entry",
                    "confidence": max(0.72, min(0.98, 0.7 + score / 8.0)),
                    "assertion_source": "stem_cue_plan",
                    "stem_score": score,
                    "stem_activity": evidence.activity,
                    "stem_scores": evidence.scores,
                }
            )
        prev_sig = sig if sig else prev_sig
        t += phrase

    rows.sort(key=lambda item: (-float(item.get("stem_score", 0.0)), float(item["timestamp"])))
    selected: List[dict] = []
    min_gap = (60.0 / float(bpm)) * MIN_SPACING_BEATS
    # Prefer one early cue, then fill by score/time.
    early = [row for row in rows if float(row["timestamp"]) <= duration * 0.22]
    pool = early + [row for row in rows if row not in early]
    for row in pool:
        ts = float(row["timestamp"])
        if any(abs(ts - float(keep["timestamp"])) < min_gap for keep in selected):
            continue
        selected.append(row)
        if len(selected) >= max_cues:
            break
    selected.sort(key=lambda item: float(item["timestamp"]))
    return selected[:max_cues]


def merge_gemini_onto_stem_cues(
    stem_cues: List[dict],
    gemini_cues: List[dict],
    *,
    bpm: float,
    bar_slack: float = 1.0,
) -> List[dict]:
    """Keep stem times. Attach Gemini names when they sit within ±1 bar."""
    if not stem_cues:
        return []
    if not bpm or bpm <= 0:
        return list(stem_cues)
    window = (60.0 / float(bpm)) * 4.0 * max(0.5, bar_slack)
    merged: List[dict] = []
    for stem in stem_cues:
        item = dict(stem)
        st = float(stem["timestamp"])
        best = None
        best_dist = window
        for gem in gemini_cues:
            try:
                gt = float(gem.get("timestamp"))
            except (TypeError, ValueError):
                continue
            dist = abs(gt - st)
            if dist <= best_dist:
                best = gem
                best_dist = dist
        if best is not None:
            name = str(best.get("cue_name") or "").strip()
            if name:
                item["cue_name"] = name
            if best.get("role"):
                item["role"] = best.get("role")
            item["model_timestamp"] = float(best.get("timestamp"))
        merged.append(item)
    return merged
