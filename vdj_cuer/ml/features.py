"""Bar-1 feature rows from mix / VDJ stem envelopes."""

from __future__ import annotations

import math
from typing import Dict, Iterable, Optional

from vdj_cuer.stem_cue_plan import _signature
from vdj_cuer.stem_evidence import (
    StemProfile,
    is_clean_phrase_entry,
    measure_stem_evidence,
)
from .phrase import PHRASE_FEATURE_NAMES, phrase_features_at
from .spectrogram import SPEC_FEATURE_NAMES, SongSpectrogram, band_features_at

STEM_KEYS = ("kick", "vocal", "bass", "instruments", "hihat")

FEATURE_NAMES: tuple[str, ...] = (
    "pos_frac",
    "bar_index",
    "bpm",
    "mix_energy",
    "mix_prev",
    "mix_next",
    "mix_dprev",
    "mix_dnext",
    *sum(
        (
            (
                f"{stem}_energy",
                f"{stem}_prev",
                f"{stem}_next",
                f"{stem}_dprev",
                f"{stem}_dnext",
            )
            for stem in STEM_KEYS
        ),
        (),
    ),
    "signature_changed",
    "hold_next_bar",
    "clean_entry",
    "vocal_lookback",
    "kick_flux",
    *SPEC_FEATURE_NAMES,
    *PHRASE_FEATURE_NAMES,
)


def bar_seconds(bpm: float) -> float:
    return (60.0 / float(bpm)) * 4.0 if bpm and bpm > 0 else 0.0


def iter_bar_times(
    *, duration: float, bpm: float, offset: float
) -> list[float]:
    bar = bar_seconds(bpm)
    if bar <= 0 or duration <= 0:
        return []
    t = float(offset)
    if t > 0:
        t -= math.ceil((t / bar) - 1e-12) * bar
    while t < -1e-9:
        t += bar
    if t < 0:
        t += bar
    times: list[float] = []
    while t + 0.25 < duration:
        times.append(round(t, 6))
        t += bar
    return times


def _energy(profile: Optional[StemProfile], start: float, width: float) -> float:
    if profile is None or width <= 0:
        return float("nan")
    return float(profile.window_average(max(0.0, start), width))


def _peak(profile: Optional[StemProfile], start: float, width: float) -> float:
    if profile is None or width <= 0:
        return float("nan")
    return float(profile.window_peak(max(0.0, start), width))


def _delta(now: float, other: float) -> float:
    if math.isnan(now) or math.isnan(other):
        return float("nan")
    return now - other


def bar_feature_row(
    profiles: Dict[str, StemProfile],
    *,
    t: float,
    duration: float,
    bpm: float,
    offset: float,
    bar_index: int,
    spectrogram: SongSpectrogram | None = None,
) -> dict[str, float]:
    """One feature dict for the bar-1 at time t. Missing stems/spec are NaN."""
    bar = bar_seconds(bpm) or 2.0
    mix = profiles.get("mix")
    prev_t = t - bar
    next_t = t + bar

    def pack(prefix: str, profile: Optional[StemProfile]) -> dict[str, float]:
        now = _energy(profile, t, bar)
        prev = _energy(profile, prev_t, bar) if prev_t >= -1e-6 else float("nan")
        nxt = _energy(profile, next_t, bar) if next_t + 0.05 < duration else float("nan")
        return {
            f"{prefix}_energy": now,
            f"{prefix}_prev": prev,
            f"{prefix}_next": nxt,
            f"{prefix}_dprev": _delta(now, prev),
            f"{prefix}_dnext": _delta(nxt, now),
        }

    row: dict[str, float] = {
        "timestamp": float(t),
        "pos_frac": float(t / duration) if duration > 0 else 0.0,
        "bar_index": float(bar_index),
        "bpm": float(bpm or 0.0),
    }
    row.update(pack("mix", mix))
    for stem in STEM_KEYS:
        row.update(pack(stem, profiles.get(stem)))

    stem_only = {k: v for k, v in profiles.items() if k != "mix"}
    evidence = (
        measure_stem_evidence(
            stem_only,
            timestamp=t,
            duration_seconds=min(bar, 4.0),
            model_elements=["drums", "vocals", "bass", "synth"],
            centered=True,
            strict_drums=False,
        )
        if stem_only
        else None
    )
    prev_ev = (
        measure_stem_evidence(
            stem_only,
            timestamp=prev_t,
            duration_seconds=min(bar, 4.0),
            model_elements=["drums", "vocals", "bass", "synth"],
            centered=True,
            strict_drums=False,
        )
        if stem_only and prev_t >= -1e-6
        else None
    )
    hold_ev = (
        measure_stem_evidence(
            stem_only,
            timestamp=min(t + bar, max(0.0, duration - 0.1)),
            duration_seconds=min(bar, 4.0),
            model_elements=["drums", "vocals", "bass", "synth"],
            centered=True,
            strict_drums=False,
        )
        if stem_only
        else None
    )
    sig = _signature(evidence.elements) if evidence else frozenset()
    prev_sig = _signature(prev_ev.elements) if prev_ev else frozenset()
    hold_sig = _signature(hold_ev.elements) if hold_ev else frozenset()
    changed = bool(stem_only and prev_ev is not None and sig != prev_sig and sig)
    held = (not changed) or (hold_sig == sig)
    clean = (
        is_clean_phrase_entry(
            stem_only, timestamp=t, elements=evidence.elements if evidence else []
        )
        if stem_only
        else True
    )
    vocal = profiles.get("vocal")
    kick = profiles.get("kick")
    beat = (60.0 / float(bpm)) if bpm and bpm > 0 else 0.5
    lookback = _energy(vocal, t - 1.0, 1.0) if vocal else float("nan")
    flux = _peak(kick, t - beat * 0.5, beat) if kick else float("nan")

    row["signature_changed"] = 1.0 if changed else 0.0
    row["hold_next_bar"] = 1.0 if held else 0.0
    row["clean_entry"] = 1.0 if clean else 0.0
    row["vocal_lookback"] = lookback
    row["kick_flux"] = flux
    prev_ok = prev_t if prev_t >= -1e-6 else None
    row.update(band_features_at(spectrogram, t=t, width=bar, prev_t=prev_ok))
    row.update(
        phrase_features_at(
            spectrogram,
            t=t,
            width=bar,
            bar_index=bar_index,
            prev_t=prev_ok,
        )
    )
    return row


def rows_for_track(
    profiles: Dict[str, StemProfile],
    *,
    duration: float,
    bpm: float,
    offset: float,
    audio_path: str | None = None,
    spectrogram: SongSpectrogram | None = None,
) -> list[dict[str, float]]:
    times = iter_bar_times(duration=duration, bpm=bpm, offset=offset)
    spec = spectrogram
    if spec is None and audio_path:
        spec = SongSpectrogram.from_audio_path(audio_path)
    return [
        bar_feature_row(
            profiles,
            t=t,
            duration=duration,
            bpm=bpm,
            offset=offset,
            bar_index=i,
            spectrogram=spec,
        )
        for i, t in enumerate(times)
    ]


def feature_vector(row: dict) -> list[float]:
    return [float(row.get(name, float("nan"))) for name in FEATURE_NAMES]


def feature_matrix(rows: Iterable[dict]) -> list[list[float]]:
    return [feature_vector(row) for row in rows]
