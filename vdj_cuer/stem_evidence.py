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
# VDJ's "vocal" stem on instrumentals is melody bleed. Relative scores then
# look medium/high because they are calibrated to that stem's own peak.
# Memories vocal/instruments peak ≈ 0.21; NDULE/Essence/Sozinho ≥ 0.60.
VOCAL_STEM_TO_INSTRUMENTS = 0.40
# Even on a vocal track, a window is not a singer if vocal energy is a
# tiny fraction of kick+instruments (intro / breakdown).
VOCAL_WINDOW_TO_MIX = 0.12


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

    def window_peak(self, start: float, duration_seconds: float) -> float:
        window = self._window(start, duration_seconds)
        return max(window) if window else 0.0

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


def vocal_stem_is_usable(profiles: Dict[str, StemProfile]) -> bool:
    """False when the VDJ vocal stem is too quiet to be a singer (bleed)."""
    vocal = profiles.get("vocal")
    if vocal is None or vocal.reference_peak < GLOBAL_SILENCE_PEAK:
        return False
    instruments = profiles.get("instruments")
    if instruments is None or instruments.reference_peak < GLOBAL_SILENCE_PEAK:
        return True
    return vocal.reference_peak >= VOCAL_STEM_TO_INSTRUMENTS * instruments.reference_peak


def _vocal_competes_in_window(
    measurements: Dict[str, ActivityMeasurement],
) -> bool:
    vocal = measurements.get("vocal")
    if vocal is None:
        return False
    mix = 0.0
    for name in ("instruments", "kick"):
        item = measurements.get(name)
        if item is not None:
            mix = max(mix, item.local_peak)
    if mix <= 0.0:
        return True
    return vocal.local_peak >= VOCAL_WINDOW_TO_MIX * mix


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
    vocal_level = activity.get("vocal", "none")
    if not vocal_stem_is_usable(profiles):
        vocal_level = "none"
    elif _is_assertable(vocal_level) and not _vocal_competes_in_window(measurements):
        vocal_level = "none"
    if vocal_level != activity.get("vocal"):
        activity = {**activity, "vocal": vocal_level}

    component_levels = {
        "drums": drum_level,
        "vocals": vocal_level,
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


def _groove_signature(elements: Iterable[str]) -> frozenset[str]:
    """Arrangement fingerprint that ignores vocal phrasing.

    A singer resting for a bar is not a section change. Kick/bass/instruments
    appearing or disappearing is.
    """
    signature = set(_broad_signature(elements))
    groove = signature - {"vocals"}
    return frozenset(groove if groove else signature)


def loop_is_stable(
    profiles: Dict[str, StemProfile],
    start: float,
    duration_seconds: float,
    model_elements: Iterable[str],
) -> bool:
    """Require the same groove in each third of the loop.

    Short 1–2s slices flip drums/vocals around the medium threshold on sparse
    R&B and chill grooves, which zeroed out loops on Make You Feel / Swimmers.
    """
    if duration_seconds <= 0:
        return False
    third = duration_seconds / 3.0
    half = third / 2.0
    sample_centers = (start + half, start + third + half, start + 2.0 * third + half)
    signatures = [
        _groove_signature(
            measure_stem_evidence(
                profiles,
                timestamp=sample_center,
                duration_seconds=third,
                model_elements=model_elements,
                centered=True,
                strict_drums=False,
            ).elements
        )
        for sample_center in sample_centers
    ]
    return bool(signatures[0]) and signatures[0] == signatures[1] == signatures[2]


# Phrase-entry thresholds (Need it Bad chorus: loops/cues sitting on pre-chorus
# words so cue-jumps and loops start mid-line).
PHRASE_ENTRY_LOOKBACK_SECONDS = 2.0
PHRASE_ENTRY_STRONG_PRE = 0.25
PHRASE_ENTRY_LEAD_IN_PRE = 0.12
PHRASE_ENTRY_LEAD_IN_POST = 0.35
PHRASE_ENTRY_CONTINUOUS_PRE = 0.18

# Cue-press window: the instant a DJ jumps to the marker. A vocal already
# sounding here (held note, mid-syllable, ad-lib) makes the cue unusable
# even when the 2s phrase average looks fine (Kaysha/Jacira — Havana).
CUE_PRESS_PRE_SECONDS = 0.20
CUE_PRESS_WINDOW_SECONDS = 0.10
CUE_PRESS_PRE_MAX = 0.14
CUE_PRESS_BUSY = 0.20
CUE_PRESS_BUSY_PRE = 0.08
CUE_PRESS_ABS_FLOOR = 0.02


def _vocal_press_levels(
    profiles: Dict[str, StemProfile],
    timestamp: float,
) -> Optional[tuple[float, float, float, float]]:
    """Return (pre_abs, press_abs, pre, press) or None if no usable vocal stem."""
    vocal = profiles.get("vocal")
    if vocal is None or vocal.reference_peak < GLOBAL_SILENCE_PEAK:
        return None
    reference = max(vocal.reference_peak, 1e-6)
    t = max(0.0, float(timestamp))
    pre_start = max(0.0, t - CUE_PRESS_PRE_SECONDS)
    pre_abs = max(
        vocal.window_average(pre_start, CUE_PRESS_PRE_SECONDS),
        vocal.window_peak(pre_start, CUE_PRESS_PRE_SECONDS),
    )
    press_abs = max(
        vocal.window_average(t, CUE_PRESS_WINDOW_SECONDS),
        vocal.window_peak(t, CUE_PRESS_WINDOW_SECONDS),
    )
    return pre_abs, press_abs, pre_abs / reference, press_abs / reference


def vocal_onset_on_downbeat(
    profiles: Dict[str, StemProfile],
    timestamp: float,
    *,
    beat_seconds: float = 0.5,
) -> bool:
    return is_vocal_onset_on_press(profiles, timestamp)


def is_vocal_onset_on_press(
    profiles: Dict[str, StemProfile],
    timestamp: float,
) -> bool:
    """True when a vocal *enters* on or into the 1 — jump would catch the word."""
    vocal = profiles.get("vocal")
    levels = _vocal_press_levels(profiles, timestamp)
    if vocal is None or levels is None:
        return False
    pre_abs, press_abs, pre, press = levels
    if pre_abs < CUE_PRESS_ABS_FLOOR and press_abs < CUE_PRESS_ABS_FLOOR:
        return False
    # Attack on the downbeat.
    if (
        pre < CUE_PRESS_PRE_MAX
        and press >= CUE_PRESS_BUSY
        and press_abs >= CUE_PRESS_ABS_FLOOR
    ):
        return True
    # Lead-in burst: quiet, then singing in the last ~200ms before the 1.
    if pre >= CUE_PRESS_PRE_MAX and pre_abs >= CUE_PRESS_ABS_FLOOR:
        t = max(0.0, float(timestamp))
        earlier_start = max(0.0, t - 0.70)
        earlier_abs = max(
            vocal.window_average(earlier_start, 0.25),
            vocal.window_peak(earlier_start, 0.25),
        )
        if earlier_abs < max(CUE_PRESS_ABS_FLOOR, pre_abs * 0.25):
            return True
    return False


def is_jump_safe_cue_press(
    profiles: Dict[str, StemProfile],
    timestamp: float,
) -> bool:
    """True when the vocal stem is silent at the press — safe pad jump."""
    levels = _vocal_press_levels(profiles, timestamp)
    if levels is None:
        return True
    pre_abs, press_abs, _pre, _press = levels
    return pre_abs < CUE_PRESS_ABS_FLOOR and press_abs < CUE_PRESS_ABS_FLOOR


def is_clean_cue_press(
    profiles: Dict[str, StemProfile],
    timestamp: float,
) -> bool:
    """True when a cue on the 1 is safe to jump to.

    Jump-safe: vocal silent at the press. Vocal onset or already-singing
    is not jump-safe (write as info POI instead).
    """
    return is_jump_safe_cue_press(profiles, timestamp)


def is_clean_phrase_entry(
    profiles: Dict[str, StemProfile],
    timestamp: float,
    elements: Optional[Iterable[str]] = None,
    lookback_seconds: float = PHRASE_ENTRY_LOOKBACK_SECONDS,
) -> bool:
    """True when a marker is safe to cue-jump to / loop from.

    Reject vocal onset on the 1. Already-rolling vocal is allowed.
    """
    del elements, lookback_seconds
    return is_clean_cue_press(profiles, timestamp)


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

    # Sparse/syncopated kick envelopes fail cosine even on a repeating 8-count.
    # If bass, instruments, or vocal can prove the wrap, do not let kick veto.
    non_kick = tuple(name for name in stem_names if name != "kick")
    if non_kick:
        stem_names = non_kick

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
