"""Beat-synchronous phrase context on a known VDJ 1 (no extra beat tracker)."""

from __future__ import annotations

import math
from typing import Optional

import numpy as np

from .spectrogram import FREQ_BANDS, SongSpectrogram

PHRASE_FEATURE_NAMES: tuple[str, ...] = (
    "phrase8",
    "phrase16",
    "spec_flux",
    "spec_flux_dprev",
    "chroma_change_1",
    "chroma_change_8",
    "energy_ctx8_mean",
    "energy_ctx8_delta",
    "centroid_ctx8_delta",
)


def _pitch_class_index(freq_hz: np.ndarray) -> np.ndarray:
    """Map Hz → pitch class 0–11 (C=0). Bins below 20 Hz are ignored (-1)."""
    safe = np.maximum(freq_hz, 1e-6)
    midi = 69.0 + 12.0 * np.log2(safe / 440.0)
    pcs = np.mod(np.rint(midi), 12).astype(int)
    pcs[freq_hz < 20.0] = -1
    return pcs


def _chroma(spec: SongSpectrogram, start: float, width: float) -> np.ndarray:
    first, last = spec._frame_slice(start, width)
    chunk = spec.power[:, first:last]
    chroma = np.zeros(12, dtype=np.float64)
    if chunk.size == 0:
        return chroma
    weights = np.mean(chunk, axis=1)
    pcs = _pitch_class_index(spec.freqs)
    for pc in range(12):
        mask = pcs == pc
        if np.any(mask):
            chroma[pc] = float(np.sum(weights[mask]))
    total = float(np.sum(chroma))
    if total > 1e-12:
        chroma /= total
    return chroma


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na <= 1e-12 or nb <= 1e-12:
        return 0.0
    return float(np.clip(np.dot(a, b) / (na * nb), -1.0, 1.0))


def _flux(spec: SongSpectrogram, start: float, width: float) -> float:
    """Mean positive spectral difference vs the previous window of the same length."""
    first, last = spec._frame_slice(start, width)
    prev_first, prev_last = spec._frame_slice(start - width, width)
    now = spec.power[:, first:last]
    prev = spec.power[:, prev_first:prev_last]
    if now.size == 0 or prev.size == 0:
        return 0.0
    now_m = np.mean(now, axis=1)
    prev_m = np.mean(prev, axis=1)
    diff = now_m - prev_m
    return float(np.mean(np.maximum(diff, 0.0)))


def _total_energy(spec: SongSpectrogram, start: float, width: float) -> float:
    return float(
        sum(spec.band_power(start, width, lo, hi) for _name, lo, hi in FREQ_BANDS)
    )


def phrase_features_at(
    spec: Optional[SongSpectrogram],
    *,
    t: float,
    width: float,
    bar_index: int,
    prev_t: Optional[float] = None,
    context_bars: int = 8,
) -> dict[str, float]:
    """Novelty / harmony / phrase-index features for the 1 at time t."""
    row = {
        "phrase8": float(int(bar_index) % 8),
        "phrase16": float(int(bar_index) % 16),
        "spec_flux": float("nan"),
        "spec_flux_dprev": float("nan"),
        "chroma_change_1": float("nan"),
        "chroma_change_8": float("nan"),
        "energy_ctx8_mean": float("nan"),
        "energy_ctx8_delta": float("nan"),
        "centroid_ctx8_delta": float("nan"),
    }
    if spec is None or width <= 0:
        return row

    flux = _flux(spec, t, width)
    row["spec_flux"] = flux
    if prev_t is not None:
        row["spec_flux_dprev"] = flux - _flux(spec, prev_t, width)

    chroma_now = _chroma(spec, t, width)
    if prev_t is not None:
        row["chroma_change_1"] = 1.0 - _cosine(chroma_now, _chroma(spec, prev_t, width))
    eight_back = t - (context_bars * width)
    if eight_back >= 0:
        row["chroma_change_8"] = 1.0 - _cosine(
            chroma_now, _chroma(spec, eight_back, width)
        )

    energies = []
    centroids = []
    for step in range(context_bars):
        start = t - step * width
        if start < -1e-6:
            break
        energies.append(_total_energy(spec, start, width))
        centroids.append(spec.centroid(start, width))
    if energies:
        finite_e = [e for e in energies if math.isfinite(e)]
        finite_c = [c for c in centroids if c == c]
        mean_e = float(np.mean(finite_e)) if finite_e else float("nan")
        mean_c = float(np.mean(finite_c)) if finite_c else float("nan")
        row["energy_ctx8_mean"] = mean_e
        now_e = energies[0] if energies else float("nan")
        now_c = centroids[0] if centroids else float("nan")
        row["energy_ctx8_delta"] = (
            now_e - mean_e if math.isfinite(now_e) and math.isfinite(mean_e) else float("nan")
        )
        row["centroid_ctx8_delta"] = (
            now_c - mean_c if math.isfinite(now_c) and math.isfinite(mean_c) else float("nan")
        )
    return row
