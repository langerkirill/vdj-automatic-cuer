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
    "mix_is_peak",
    "kick_is_peak",
    "phrase8_zero",
    "phrase16_zero",
    "early_intro",
    "late_outro",
    "mix_offset",
    "kick_offset",
    "vocal_offset",
    "vocal_onset",
)

# pos_frac / bar_index leak "cues are early" on train (946 intro vs 181 outro
# positives) and bury mid/late phrase starts on holdout. Keep them on the row
# for propose(); do not feed them to the classifier.
POSITION_LEAK_FEATURES = frozenset(
    {"pos_frac", "bar_index", "early_intro", "late_outro"}
)
TRACK_RELATIVE_FEATURES: tuple[str, ...] = (
    "mix_z",
    "mix_rank_frac",
    "mix_local8",
    "kick_z",
    "vocal_z",
    "mix_vs_peak",
    "stem_sig_changed_4",
    "stem_sig_changed_8",
    "chroma_local8",
    "texture_change",
    "energy_drop_held",
    "next8_mix_delta",
    "next16_mix_delta",
    "prev8_mix_delta",
    "kick_fwd8_delta",
    "vocal_fwd8_delta",
    "mix_vs_prev8_max",
    "pre_decline",
    "chroma_fwd8",
    "kick_share",
    "vocal_share",
    "kick_share_fwd8",
    "still_loud_kick_drop",
    "still_loud_vocal_drop",
    "phrase_pre_decline",
    "after_peak",
)
SEQUENCE_MODEL_FEATURES: tuple[str, ...] = (
    "next8_mix_delta",
    "next16_mix_delta",
    "prev8_mix_delta",
    "kick_fwd8_delta",
    "vocal_fwd8_delta",
    "bass_fwd8_delta",
    "instruments_fwd8_delta",
    "mix_vs_prev8_max",
    "pre_decline",
    "chroma_fwd8",
    "spec_flux_fwd8",
    "still_loud_kick_drop",
    "still_loud_vocal_drop",
    "phrase_pre_decline",
)
STEM_ON_KEYS = ("kick", "vocal", "bass", "instruments", "hihat")
MODEL_FEATURE_NAMES: tuple[str, ...] = tuple(
    name for name in FEATURE_NAMES if name not in POSITION_LEAK_FEATURES
) + TRACK_RELATIVE_FEATURES


def _finite(value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return number if math.isfinite(number) else float("nan")


def apply_derived_features(row: dict) -> dict[str, float]:
    """Phrase-map extras computed from already-exported bar columns."""
    out = dict(row)
    mix = _finite(row.get("mix_energy"))
    mix_prev = _finite(row.get("mix_prev"))
    mix_next = _finite(row.get("mix_next"))
    kick = _finite(row.get("kick_energy"))
    kick_prev = _finite(row.get("kick_prev"))
    kick_next = _finite(row.get("kick_next"))
    phrase8 = _finite(row.get("phrase8"))
    phrase16 = _finite(row.get("phrase16"))
    pos = _finite(row.get("pos_frac"))
    out["mix_is_peak"] = (
        1.0
        if math.isfinite(mix)
        and (not math.isfinite(mix_prev) or mix + 1e-9 >= mix_prev)
        and (not math.isfinite(mix_next) or mix + 1e-9 >= mix_next)
        else 0.0
    )
    out["kick_is_peak"] = (
        1.0
        if math.isfinite(kick)
        and (not math.isfinite(kick_prev) or kick + 1e-9 >= kick_prev)
        and (not math.isfinite(kick_next) or kick + 1e-9 >= kick_next)
        else 0.0
    )
    out["phrase8_zero"] = 1.0 if phrase8 == 0.0 else 0.0
    out["phrase16_zero"] = 1.0 if phrase16 == 0.0 else 0.0
    out["early_intro"] = 1.0 if math.isfinite(pos) and pos <= 0.14 else 0.0
    out["late_outro"] = 1.0 if math.isfinite(pos) and pos >= 0.82 else 0.0
    mix_dprev = _finite(row.get("mix_dprev"))
    mix_dnext = _finite(row.get("mix_dnext"))
    kick_dprev = _finite(row.get("kick_dprev"))
    kick_dnext = _finite(row.get("kick_dnext"))
    vocal_dprev = _finite(row.get("vocal_dprev"))
    out["mix_offset"] = _held_drop(mix_dprev, mix_dnext)
    out["kick_offset"] = _held_drop(kick_dprev, kick_dnext)
    out["vocal_offset"] = (
        1.0 if math.isfinite(vocal_dprev) and vocal_dprev < -0.05 else 0.0
    )
    out["vocal_onset"] = (
        1.0 if math.isfinite(vocal_dprev) and vocal_dprev > 0.05 else 0.0
    )
    return out


def _held_drop(dprev: float, dnext: float, *, thresh: float = -0.02) -> float:
    if not math.isfinite(dprev) or dprev >= thresh:
        return 0.0
    if math.isfinite(dnext) and dnext > 0.01:
        return 0.0
    return 1.0


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
    return apply_derived_features(row)


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


def _z_and_median(values: list[float]) -> tuple[list[float], float]:
    finite = [value for value in values if math.isfinite(value)]
    mean = sum(finite) / len(finite) if finite else 0.0
    var = (
        sum((value - mean) ** 2 for value in finite) / len(finite) if finite else 0.0
    )
    std = math.sqrt(var) if var > 1e-12 else 1.0
    zscores = [
        (value - mean) / std if math.isfinite(value) else float("nan")
        for value in values
    ]
    median = sorted(finite)[len(finite) // 2] if finite else float("nan")
    return zscores, median


def _stem_on(energy: float, peak: float) -> int:
    if not math.isfinite(energy) or not math.isfinite(peak) or peak <= 1e-12:
        return 0
    return 1 if energy >= max(0.08, 0.45 * peak) else 0


def _local_peak(values: list[float], place: int, radius: int = 4) -> bool:
    if place < 0 or place >= len(values):
        return False
    current = values[place]
    if not math.isfinite(current):
        return False
    for step in range(1, radius + 1):
        for neighbor in (place - step, place + step):
            if neighbor < 0 or neighbor >= len(values):
                continue
            other = values[neighbor]
            if math.isfinite(other) and other > current + 1e-9:
                return False
    return True


def apply_track_relative_features(rows: list[dict]) -> list[dict]:
    """Phrase-local mix / stem identity — no song-position leak, per track."""
    if not rows:
        return []
    energies: list[float] = []
    kick_energies: list[float] = []
    vocal_energies: list[float] = []
    bass_energies: list[float] = []
    inst_energies: list[float] = []
    flux: list[float] = []
    chroma: list[float] = []
    for row in rows:
        energies.append(_finite(row.get("mix_energy")))
        kick_energies.append(_finite(row.get("kick_energy")))
        vocal_energies.append(_finite(row.get("vocal_energy")))
        bass_energies.append(_finite(row.get("bass_energy")))
        inst_energies.append(_finite(row.get("instruments_energy")))
        flux.append(_finite(row.get("spec_flux")))
        chroma_val = _finite(row.get("chroma_change_8"))
        if not math.isfinite(chroma_val):
            chroma_val = _finite(row.get("chroma_change_1"))
        chroma.append(chroma_val)
    finite = [value for value in energies if math.isfinite(value)]
    mix_z, _mix_med = _z_and_median(energies)
    kick_z, _kick_med = _z_and_median(kick_energies)
    vocal_z, _vocal_med = _z_and_median(vocal_energies)
    peak = max(finite) if finite else 0.0
    if peak <= 1e-12:
        peak = 1.0
    stem_peaks: dict[str, float] = {}
    for stem in STEM_ON_KEYS:
        vals = [_finite(row.get(f"{stem}_energy")) for row in rows]
        finite_stem = [value for value in vals if math.isfinite(value)]
        stem_peaks[stem] = max(finite_stem) if finite_stem else float("nan")
    order = sorted(
        range(len(rows)),
        key=lambda i: float(rows[i].get("timestamp") or 0.0),
    )
    bits: list[tuple[int, ...]] = []
    for index in order:
        row = rows[index]
        bits.append(
            tuple(
                _stem_on(_finite(row.get(f"{stem}_energy")), stem_peaks[stem])
                for stem in STEM_ON_KEYS
            )
        )
    vs_peak = [
        (energy / peak) if math.isfinite(energy) else float("nan") for energy in energies
    ]
    out: list[dict] = []
    for index, row in enumerate(rows):
        item = apply_derived_features(row)
        energy = energies[index]
        item["mix_z"] = mix_z[index]
        item["kick_z"] = kick_z[index]
        item["vocal_z"] = vocal_z[index]
        item["mix_vs_peak"] = vs_peak[index]
        if math.isfinite(energy) and finite:
            # Rank among this track only (caller must group by track_id).
            rank = 1 + sum(1 for value in finite if value < energy - 1e-12)
            item["mix_rank_frac"] = rank / len(finite)
        else:
            item["mix_rank_frac"] = float("nan")
        try:
            place = order.index(index)
        except ValueError:
            place = -1
        item["mix_local8"] = (
            1.0 if _local_peak([energies[i] for i in order], place) else 0.0
        )
        chroma_series = [chroma[i] for i in order]
        chroma_now = chroma[index]
        item["chroma_local8"] = (
            1.0
            if _local_peak(chroma_series, place)
            and math.isfinite(chroma_now)
            and chroma_now > 0.08
            else 0.0
        )
        sig_now = bits[place] if place >= 0 else tuple(0 for _ in STEM_ON_KEYS)
        sig4 = bits[place - 4] if place >= 4 else None
        sig8 = bits[place - 8] if place >= 8 else None
        item["stem_sig_changed_4"] = (
            1.0 if sig4 is not None and sig_now != sig4 else 0.0
        )
        item["stem_sig_changed_8"] = (
            1.0 if sig8 is not None and sig_now != sig8 else 0.0
        )
        mix_dprev = _finite(item.get("mix_dprev"))
        item["texture_change"] = (
            1.0
            if item["stem_sig_changed_8"] >= 1.0
            and (not math.isfinite(mix_dprev) or mix_dprev < 0.05)
            else 0.0
        )
        drop = 0.0
        if (
            place >= 8
            and math.isfinite(vs_peak[index])
            and vs_peak[index] < 0.62
        ):
            older = vs_peak[order[place - 8]]
            if math.isfinite(older) and older >= 0.75:
                drop = 1.0
        item["energy_drop_held"] = drop
        out.append(item)
    _apply_sequence_features(
        out,
        order=order,
        vs_peak=vs_peak,
        mix_energies=energies,
        kick_energies=kick_energies,
        vocal_energies=vocal_energies,
        bass_energies=bass_energies,
        inst_energies=inst_energies,
        flux=flux,
        chroma=chroma,
    )
    return out


def _ordered_window_mean(series: list[float], order: list[int], place: int, start: int, width: int) -> float:
    values: list[float] = []
    for step in range(start, start + width):
        neighbor = place + step
        if neighbor < 0 or neighbor >= len(order):
            continue
        value = series[order[neighbor]]
        if math.isfinite(value):
            values.append(value)
    if not values:
        return float("nan")
    return sum(values) / len(values)


def _apply_sequence_features(
    rows: list[dict],
    *,
    order: list[int],
    vs_peak: list[float],
    mix_energies: list[float],
    kick_energies: list[float],
    vocal_energies: list[float],
    bass_energies: list[float],
    inst_energies: list[float],
    flux: list[float],
    chroma: list[float],
) -> None:
    """Lookahead / lookback phrase structure — no clock-position leak."""
    for index, item in enumerate(rows):
        try:
            place = order.index(index)
        except ValueError:
            place = -1
        current_vs = vs_peak[index] if 0 <= index < len(vs_peak) else float("nan")
        next8 = _ordered_window_mean(vs_peak, order, place, 1, 8)
        next16 = _ordered_window_mean(vs_peak, order, place, 1, 16)
        prev8 = _ordered_window_mean(vs_peak, order, place, -8, 8)
        item["next8_mix_delta"] = (
            next8 - current_vs
            if math.isfinite(next8) and math.isfinite(current_vs)
            else float("nan")
        )
        item["next16_mix_delta"] = (
            next16 - current_vs
            if math.isfinite(next16) and math.isfinite(current_vs)
            else float("nan")
        )
        item["prev8_mix_delta"] = (
            current_vs - prev8
            if math.isfinite(prev8) and math.isfinite(current_vs)
            else float("nan")
        )
        kick_now = kick_energies[index] if 0 <= index < len(kick_energies) else float("nan")
        vocal_now = vocal_energies[index] if 0 <= index < len(vocal_energies) else float("nan")
        kick_fwd = _ordered_window_mean(kick_energies, order, place, 1, 8)
        vocal_fwd = _ordered_window_mean(vocal_energies, order, place, 1, 8)
        item["kick_fwd8_delta"] = (
            kick_fwd - kick_now
            if math.isfinite(kick_fwd) and math.isfinite(kick_now)
            else float("nan")
        )
        item["vocal_fwd8_delta"] = (
            vocal_fwd - vocal_now
            if math.isfinite(vocal_fwd) and math.isfinite(vocal_now)
            else float("nan")
        )
        bass_now = bass_energies[index] if 0 <= index < len(bass_energies) else float("nan")
        inst_now = inst_energies[index] if 0 <= index < len(inst_energies) else float("nan")
        bass_fwd = _ordered_window_mean(bass_energies, order, place, 1, 8)
        inst_fwd = _ordered_window_mean(inst_energies, order, place, 1, 8)
        item["bass_fwd8_delta"] = (
            bass_fwd - bass_now
            if math.isfinite(bass_fwd) and math.isfinite(bass_now)
            else float("nan")
        )
        item["instruments_fwd8_delta"] = (
            inst_fwd - inst_now
            if math.isfinite(inst_fwd) and math.isfinite(inst_now)
            else float("nan")
        )
        prev_max = float("nan")
        if place >= 1:
            prev_vals = [
                vs_peak[order[step]]
                for step in range(max(0, place - 8), place)
                if math.isfinite(vs_peak[order[step]])
            ]
            if prev_vals:
                prev_max = max(prev_vals)
        item["mix_vs_prev8_max"] = (
            current_vs - prev_max
            if math.isfinite(current_vs) and math.isfinite(prev_max)
            else float("nan")
        )
        item["pre_decline"] = (
            1.0
            if math.isfinite(current_vs)
            and current_vs >= 0.70
            and math.isfinite(item["next8_mix_delta"])
            and item["next8_mix_delta"] <= -0.15
            else 0.0
        )
        prev8_max_delta = item["mix_vs_prev8_max"]
        item["after_peak"] = (
            1.0
            if math.isfinite(prev8_max_delta) and prev8_max_delta <= -0.08
            else 0.0
        )
        next8_delta = item["next8_mix_delta"]
        item["section_hold"] = (
            1.0
            if item["after_peak"] >= 1.0
            and math.isfinite(next8_delta)
            and abs(next8_delta) < 0.08
            else 0.0
        )
        phrase_mark = (
            float(item.get("phrase8_zero") or 0.0) >= 1.0
            or float(item.get("phrase16_zero") or 0.0) >= 1.0
        )
        stem_flip = (
            float(item.get("stem_sig_changed_4") or 0.0) >= 1.0
            or float(item.get("texture_change") or 0.0) >= 1.0
            or float(item.get("signature_changed") or 0.0) >= 1.0
        )
        item["section_entry"] = (
            1.0 if item["after_peak"] >= 1.0 and phrase_mark and stem_flip else 0.0
        )
        chroma_fwd = float("nan")
        flux_fwd = float("nan")
        if 0 <= place + 8 < len(order):
            chroma_fwd = chroma[order[place + 8]]
            flux_fwd = flux[order[place + 8]]
        item["chroma_fwd8"] = chroma_fwd
        item["spec_flux_fwd8"] = flux_fwd
        kick_dprev = _finite(item.get("kick_dprev"))
        vocal_dprev = _finite(item.get("vocal_dprev"))
        item["still_loud_kick_drop"] = (
            1.0
            if math.isfinite(current_vs)
            and current_vs >= 0.70
            and math.isfinite(kick_dprev)
            and kick_dprev <= -0.10
            else 0.0
        )
        item["still_loud_vocal_drop"] = (
            1.0
            if math.isfinite(current_vs)
            and current_vs >= 0.70
            and math.isfinite(vocal_dprev)
            and vocal_dprev <= -0.08
            else 0.0
        )
        phrase_mark = (
            float(item.get("phrase8_zero") or 0.0) >= 1.0
            or float(item.get("phrase16_zero") or 0.0) >= 1.0
        )
        item["phrase_pre_decline"] = (
            1.0
            if phrase_mark
            and (
                item["pre_decline"] >= 1.0
                or item["still_loud_kick_drop"] >= 1.0
                or item["still_loud_vocal_drop"] >= 1.0
            )
            else 0.0
        )
        mix_now = mix_energies[index] if 0 <= index < len(mix_energies) else float("nan")
        item["kick_share"] = (
            kick_now / mix_now
            if math.isfinite(kick_now) and math.isfinite(mix_now) and mix_now > 1e-6
            else float("nan")
        )
        item["vocal_share"] = (
            vocal_now / mix_now
            if math.isfinite(vocal_now) and math.isfinite(mix_now) and mix_now > 1e-6
            else float("nan")
        )

    shares = [float(row.get("kick_share", float("nan"))) for row in rows]
    for index, item in enumerate(rows):
        try:
            place = order.index(index)
        except ValueError:
            place = -1
        share_now = shares[index] if 0 <= index < len(shares) else float("nan")
        share_fwd = _ordered_window_mean(shares, order, place, 1, 8)
        item["kick_share_fwd8"] = (
            share_fwd - share_now
            if math.isfinite(share_fwd) and math.isfinite(share_now)
            else float("nan")
        )

    last_peak = -1
    for place, index in enumerate(order):
        item = rows[index]
        if float(item.get("mix_local8") or 0.0) >= 1.0:
            last_peak = place
        if last_peak < 0:
            item["bars_since_local_peak"] = 1.0
        else:
            item["bars_since_local_peak"] = min(place - last_peak, 32) / 32.0


def feature_vector(row: dict) -> list[float]:
    filled = apply_derived_features(row)
    return [float(filled.get(name, float("nan"))) for name in MODEL_FEATURE_NAMES]


def apply_track_relative_by_track(rows: list[dict]) -> list[dict]:
    """Compute mix_z / stem-sig per track so train matches per-track eval."""
    if not rows:
        return []
    if not any(row.get("track_id") for row in rows):
        return apply_track_relative_features(rows)
    from collections import defaultdict

    grouped: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        grouped[str(row.get("track_id") or "")].append(index)
    out: list[dict | None] = [None] * len(rows)
    for indexes in grouped.values():
        rel = apply_track_relative_features([rows[i] for i in indexes])
        for index, item in zip(indexes, rel):
            out[index] = item
    if any(row is None for row in out):
        raise RuntimeError("track-relative feature alignment failed")
    return [row for row in out if row is not None]


def feature_matrix(rows: Iterable[dict]) -> list[list[float]]:
    materialized = [apply_derived_features(dict(row)) for row in rows]
    materialized = apply_track_relative_by_track(materialized)
    return [feature_vector(row) for row in materialized]
