"""Calibrated audio evidence for conservative cue component assertions."""

from __future__ import annotations

import array
import math
import subprocess
import sys
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence


FRAME_SECONDS = 0.25
SAMPLE_RATE = 800
PROFILE_BINS = 1200
POST_BOUNDARY_OFFSET_SECONDS = 0.25
GLOBAL_SILENCE_PEAK = 0.004
ACTIVITY_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3}
SPECIFIC_INSTRUMENTS = ("piano", "synth", "strings", "guitar")


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = round((len(ordered) - 1) * percentile)
    return ordered[min(len(ordered) - 1, max(0, index))]


@dataclass(frozen=True)
class ActivityMeasurement:
    level: str
    score: float
    persistence: float
    local_peak: float


@dataclass(frozen=True)
class StemEvidence:
    activity: Dict[str, str]
    scores: Dict[str, float]
    elements: List[str]
    uncertain_elements: List[str]
    confidence: float


@dataclass(frozen=True)
class StemProfile:
    """A low-rate peak envelope calibrated against one whole stem."""

    frames: tuple[float, ...]
    frame_seconds: float
    reference_peak: float

    @classmethod
    def from_frames(
        cls, frames: Iterable[float], frame_seconds: float = FRAME_SECONDS
    ) -> "StemProfile":
        values = tuple(max(0.0, float(value)) for value in frames)
        return cls(
            frames=values,
            frame_seconds=frame_seconds,
            reference_peak=_percentile(values, 0.95),
        )

    @classmethod
    def decode(cls, audio_path: str) -> "StemProfile":
        """Decode an audio file once into a compact peak envelope."""
        result = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                audio_path,
                "-ac",
                "1",
                "-ar",
                str(SAMPLE_RATE),
                "-f",
                "s16le",
                "-",
            ],
            capture_output=True,
            check=True,
        )
        samples = array.array("h")
        samples.frombytes(result.stdout)
        if sys.byteorder != "little":
            samples.byteswap()

        peaks = []
        for bin_index in range(PROFILE_BINS):
            start = int(bin_index * len(samples) / PROFILE_BINS)
            end = int((bin_index + 1) * len(samples) / PROFILE_BINS)
            frame = samples[start:end]
            if not frame:
                peaks.append(0.0)
                continue
            peaks.append(max(abs(sample) for sample in frame) / 32768.0)
        duration = len(samples) / SAMPLE_RATE
        return cls.from_frames(peaks, frame_seconds=duration / PROFILE_BINS)

    def _window(self, start: float, duration_seconds: float) -> tuple[float, ...]:
        first = max(0, int(start / self.frame_seconds))
        last = min(
            len(self.frames),
            max(first + 1, int((start + duration_seconds) / self.frame_seconds)),
        )
        return self.frames[first:last]

    def window_average(self, start: float, duration_seconds: float) -> float:
        window = self._window(start, duration_seconds)
        return sum(window) / len(window) if window else 0.0

    def measure(
        self,
        timestamp: float,
        duration_seconds: float = 4.0,
        post_boundary_offset: float = POST_BOUNDARY_OFFSET_SECONDS,
    ) -> ActivityMeasurement:
        """Measure persistent activity after a section boundary."""
        if self.reference_peak < GLOBAL_SILENCE_PEAK:
            return ActivityMeasurement("none", 0.0, 0.0, 0.0)

        window = self._window(
            max(0.0, timestamp + post_boundary_offset),
            max(self.frame_seconds, duration_seconds),
        )
        if not window:
            return ActivityMeasurement("none", 0.0, 0.0, 0.0)

        local_peak = sum(window) / len(window)
        score = min(1.0, local_peak / self.reference_peak)
        persistence_floor = self.reference_peak * 0.08
        persistence = sum(value >= persistence_floor for value in window) / len(window)

        if score >= 0.55 and persistence >= 0.75:
            level = "high"
        elif score >= 0.20 and persistence >= 0.35:
            level = "medium"
        elif score >= 0.03:
            level = "low"
        else:
            level = "none"
        return ActivityMeasurement(level, score, persistence, local_peak)

    def measure_centered(
        self, timestamp: float, duration_seconds: float = 4.0
    ) -> ActivityMeasurement:
        """Measure the same calibrated cue window used by the visual audit."""
        if self.reference_peak < GLOBAL_SILENCE_PEAK:
            return ActivityMeasurement("none", 0.0, 0.0, 0.0)
        window = self._window(
            max(0.0, timestamp - (duration_seconds / 2.0)),
            duration_seconds,
        )
        if not window:
            return ActivityMeasurement("none", 0.0, 0.0, 0.0)
        local_peak = sum(window) / len(window)
        score = min(1.0, local_peak / self.reference_peak)
        if score >= 0.55:
            level = "high"
        elif score >= 0.18:
            level = "medium"
        elif score >= 0.08:
            level = "low"
        else:
            level = "none"
        return ActivityMeasurement(level, score, 1.0, local_peak)


def load_stem_profiles(stem_files: Iterable[tuple[str, str]]) -> Dict[str, StemProfile]:
    """Decode each extracted VDJ stem once for all cue and loop checks."""
    return {stem_name: StemProfile.decode(path) for stem_name, path in stem_files}


def _strongest_level(*levels: str) -> str:
    return max(levels, key=lambda level: ACTIVITY_RANK.get(level, 0))


def _is_assertable(level: str) -> bool:
    return ACTIVITY_RANK.get(level, 0) >= ACTIVITY_RANK["medium"]


def measure_stem_evidence(
    profiles: Dict[str, StemProfile],
    timestamp: float,
    duration_seconds: float,
    model_elements: Iterable[str],
    centered: bool = False,
    strict_drums: bool = True,
) -> StemEvidence:
    """Return only component claims supported by persistent stem activity."""
    measurements = {}
    for stem_name, profile in profiles.items():
        if centered:
            measurements[stem_name] = profile.measure_centered(
                timestamp, duration_seconds
            )
        else:
            measurements[stem_name] = profile.measure(timestamp, duration_seconds)
    activity = {name: measurement.level for name, measurement in measurements.items()}
    scores = {name: round(measurement.score, 4) for name, measurement in measurements.items()}
    normalized_model = {str(element).strip().lower() for element in model_elements}

    kick_level = activity.get("kick", "none")
    hihat_level = activity.get("hihat", "none")
    if strict_drums:
        drum_level = kick_level if _is_assertable(kick_level) else "none"
        if drum_level == "none" and (
            kick_level == "low" or ACTIVITY_RANK.get(hihat_level, 0) > 0
        ):
            drum_level = "low"
    else:
        drum_level = _strongest_level(kick_level, hihat_level)
    component_levels = {
        "drums": drum_level,
        "vocals": activity.get("vocal", "none"),
        "bass": activity.get("bass", "none"),
        "instruments": activity.get("instruments", "none"),
    }

    elements: List[str] = []
    uncertain: List[str] = []
    if _is_assertable(drum_level):
        elements.append("drums")
    elif drum_level == "low":
        uncertain.append("drums")

    if _is_assertable(component_levels["vocals"]):
        elements.append("vocals")
    elif component_levels["vocals"] == "low":
        uncertain.append("vocals")

    if _is_assertable(component_levels["bass"]):
        elements.append("bass")
    elif component_levels["bass"] == "low":
        uncertain.append("bass")

    instrument_level = component_levels["instruments"]
    if _is_assertable(instrument_level):
        specifics = [name for name in SPECIFIC_INSTRUMENTS if name in normalized_model]
        elements.extend(specifics or ["synth"])
    elif instrument_level == "low":
        uncertain.append("instruments")

    asserted_scores = []
    if "drums" in elements:
        asserted_scores.append(max(scores.get("kick", 0.0), scores.get("hihat", 0.0)))
    for element, stem_name in (("vocals", "vocal"), ("bass", "bass")):
        if element in elements:
            asserted_scores.append(scores.get(stem_name, 0.0))
    if any(element in elements for element in SPECIFIC_INSTRUMENTS):
        asserted_scores.append(scores.get("instruments", 0.0))

    confidence = min(asserted_scores) if asserted_scores else 0.0
    return StemEvidence(activity, scores, elements, uncertain, round(confidence, 4))


def _broad_signature(elements: Iterable[str]) -> frozenset[str]:
    values = set(elements)
    signature = set()
    if "drums" in values:
        signature.add("drums")
    if "vocals" in values:
        signature.add("vocals")
    if "bass" in values:
        signature.add("bass")
    if values.intersection(SPECIFIC_INSTRUMENTS):
        signature.add("instruments")
    return frozenset(signature)


def loop_is_stable(
    profiles: Dict[str, StemProfile],
    start: float,
    duration_seconds: float,
    model_elements: Iterable[str],
) -> bool:
    """Require the same asserted components near the start, middle, and end."""
    if duration_seconds <= 0:
        return False
    sample_duration = min(2.0, max(0.75, duration_seconds / 4.0))
    latest_start = max(start, start + duration_seconds - sample_duration - 0.25)
    sample_starts = (start, start + duration_seconds / 2.0, latest_start)
    signatures = [
        _broad_signature(
            measure_stem_evidence(
                profiles,
                timestamp=sample_start,
                duration_seconds=sample_duration,
                model_elements=model_elements,
            ).elements
        )
        for sample_start in sample_starts
    ]
    return bool(signatures[0]) and signatures[0] == signatures[1] == signatures[2]


# Phrase-entry thresholds (Need it Bad chorus: loops/cues sitting on pre-chorus
# words so cue-jumps and loops start mid-line).
PHRASE_ENTRY_LOOKBACK_SECONDS = 2.0
PHRASE_ENTRY_STRONG_PRE = 0.25
PHRASE_ENTRY_LEAD_IN_PRE = 0.12
PHRASE_ENTRY_LEAD_IN_POST = 0.35
PHRASE_ENTRY_CONTINUOUS_PRE = 0.18


def is_clean_phrase_entry(
    profiles: Dict[str, StemProfile],
    timestamp: float,
    elements: Optional[Iterable[str]] = None,
    lookback_seconds: float = PHRASE_ENTRY_LOOKBACK_SECONDS,
) -> bool:
    """True when a marker is safe to cue-jump to / loop from.

    Vocal-forward markers must not start while a line is already running
    (pre-chorus words into a chorus). Instrumental markers always pass.
    """
    element_set = {
        str(element).strip().lower() for element in (elements or [])
    }
    # Only gate when vocals are part of the claim (chorus/verse/vocal mix).
    if elements is not None and "vocals" not in element_set:
        return True

    vocal = profiles.get("vocal")
    if vocal is None:
        return True

    lookback = max(0.5, float(lookback_seconds))
    pre = vocal.window_average(max(0.0, timestamp - lookback), lookback)
    post = vocal.window_average(max(0.0, timestamp), min(2.0, lookback + 0.5))
    reference = max(vocal.reference_peak, 1e-6)
    pre_n = pre / reference
    post_n = post / reference

    # Already singing hard before the hit.
    if pre_n >= PHRASE_ENTRY_STRONG_PRE:
        return False
    # Pre-chorus words leading into a loud vocal section.
    if (
        pre_n >= PHRASE_ENTRY_LEAD_IN_PRE
        and post_n >= PHRASE_ENTRY_LEAD_IN_POST
        and post_n < pre_n * 3.5
    ):
        return False
    # Continuous mid-line: pre and post both high with no clear phrase attack.
    if (
        pre_n >= PHRASE_ENTRY_CONTINUOUS_PRE
        and post_n >= PHRASE_ENTRY_CONTINUOUS_PRE
        and post_n <= pre_n * 1.6
    ):
        return False
    return True


# Loop wrap-around continuity thresholds. Component presence can stay "stable"
# while level/texture still jump hard enough to make a DJ loop unusable
# (Masego - Breathe chorus loops, Matthew Halsall evolving jazz sections).
LOOP_SEAM_MIN_COSINE = 0.72
LOOP_SEAM_MIN_MEAN_RATIO = 0.55
LOOP_SEAM_MAX_MEAN_RATIO = 1.85
LOOP_SEAM_ACTIVE_PEAK_FRACTION = 0.08


def _envelope_cosine(left: Sequence[float], right: Sequence[float], bins: int = 32) -> float:
    """Compare coarse peak envelopes of two windows (1.0 = identical shape)."""
    if not left or not right or bins <= 0:
        return 0.0

    def envelope(frames: Sequence[float]) -> list[float]:
        values: list[float] = []
        length = len(frames)
        for index in range(bins):
            start = int(index * length / bins)
            end = max(start + 1, int((index + 1) * length / bins))
            chunk = frames[start:end]
            values.append(sum(chunk) / len(chunk))
        return values

    left_env = envelope(left)
    right_env = envelope(right)
    dot = sum(a * b for a, b in zip(left_env, right_env))
    left_norm = math.sqrt(sum(value * value for value in left_env))
    right_norm = math.sqrt(sum(value * value for value in right_env))
    if left_norm <= 1e-12 or right_norm <= 1e-12:
        return 0.0
    return dot / (left_norm * right_norm)


def _stems_for_loop_elements(elements: Iterable[str]) -> frozenset[str]:
    """Map asserted loop components onto the stems that must wrap cleanly.

    Residual bleed on non-asserted stems (e.g. faint kick under a vocal loop)
    must not veto an otherwise seamless candidate.
    """
    values = {str(element).strip().lower() for element in elements}
    stems: set[str] = set()
    if "drums" in values or "percussion" in values:
        stems.add("kick")
    if "vocals" in values:
        stems.add("vocal")
    if "bass" in values:
        stems.add("bass")
    if values.intersection(SPECIFIC_INSTRUMENTS) or "instruments" in values:
        stems.add("instruments")
    return frozenset(stems)


def loop_seam_is_clean(
    profiles: Dict[str, StemProfile],
    start: float,
    duration_seconds: float,
    elements: Optional[Iterable[str]] = None,
) -> bool:
    """Require head/tail continuity on asserted stems so the wrap does not jump.

    Stem-component stability alone is not enough: R&B choruses and evolving jazz
    can keep drums/bass/instruments "present" while level and texture change so
    much that looping sounds broken. Reject those before they become POIs.

    When ``elements`` is provided, only those component stems are checked. That
    avoids residual kick/bass bleed failing a deliberate vocal or melodic loop.
    """
    if duration_seconds <= 0 or not profiles:
        return False

    sample_duration = min(2.0, max(0.75, duration_seconds / 8.0))
    tail_start = max(start, start + duration_seconds - sample_duration)
    if elements is None:
        stem_names = tuple(profiles.keys())
    else:
        stem_names = tuple(
            name for name in _stems_for_loop_elements(elements) if name in profiles
        )
        if not stem_names:
            # Asserted components with no matching stem profiles cannot be proven.
            return False

    active_stems = 0
    for stem_name in stem_names:
        profile = profiles[stem_name]
        head = profile._window(start, sample_duration)
        tail = profile._window(tail_start, sample_duration)
        if not head or not tail:
            continue

        head_mean = sum(head) / len(head)
        tail_mean = sum(tail) / len(tail)
        activity_floor = max(
            GLOBAL_SILENCE_PEAK,
            profile.reference_peak * LOOP_SEAM_ACTIVE_PEAK_FRACTION,
        )
        if max(head_mean, tail_mean) < activity_floor:
            # Asserted component went silent on both ends → not loopable as claimed.
            if elements is not None:
                return False
            continue

        active_stems += 1
        mean_ratio = (tail_mean + 1e-9) / (head_mean + 1e-9)
        cosine = _envelope_cosine(head, tail)
        if (
            mean_ratio < LOOP_SEAM_MIN_MEAN_RATIO
            or mean_ratio > LOOP_SEAM_MAX_MEAN_RATIO
            or cosine < LOOP_SEAM_MIN_COSINE
        ):
            return False

    # No audible stem activity across the loop → not a useful DJ loop.
    return active_stems > 0


def energy_ratio(
    profile: StemProfile, timestamp: float, window_seconds: float = 4.0
) -> float:
    """Return post-transition energy divided by pre-transition energy."""
    before = profile.window_average(max(0.0, timestamp - window_seconds), window_seconds)
    after = profile.window_average(timestamp, window_seconds)
    if before <= 0.0001:
        return 10.0 if after > before else 1.0
    return after / before
