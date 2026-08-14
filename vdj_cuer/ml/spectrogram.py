"""Full-song magnitude spectrogram → per-bar frequency-balance features."""

from __future__ import annotations

import math
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
from scipy.signal import stft

SPEC_SR = 16_000
SPEC_N_FFT = 1024
SPEC_HOP = 512

# Musical-ish bands (Hz). Nyquist at 8 kHz.
FREQ_BANDS: tuple[tuple[str, float, float], ...] = (
    ("sub", 20.0, 60.0),
    ("bass", 60.0, 250.0),
    ("lowmid", 250.0, 500.0),
    ("mid", 500.0, 2_000.0),
    ("highmid", 2_000.0, 6_000.0),
    ("high", 6_000.0, 8_000.0),
)

SPEC_FEATURE_NAMES: tuple[str, ...] = (
    *sum(
        (
            (f"spec_{name}", f"spec_{name}_dprev")
            for name, _lo, _hi in FREQ_BANDS
        ),
        (),
    ),
    "spec_centroid",
    "spec_centroid_dprev",
    "spec_bass_share",
    "spec_high_share",
)


@dataclass(frozen=True)
class SongSpectrogram:
    """Power spectrogram for one mix: shape (freq_bins, frames)."""

    sr: int
    hop: int
    freqs: np.ndarray
    power: np.ndarray

    @classmethod
    def from_samples(cls, samples: np.ndarray, sr: int = SPEC_SR) -> "SongSpectrogram":
        wave = np.asarray(samples, dtype=np.float64).reshape(-1)
        if wave.size == 0:
            freqs = np.fft.rfftfreq(SPEC_N_FFT, d=1.0 / sr)
            return cls(sr=sr, hop=SPEC_HOP, freqs=freqs, power=np.zeros((freqs.size, 1)))
        peak = float(np.max(np.abs(wave))) or 1.0
        wave = wave / peak
        freqs, _times, zxx = stft(
            wave,
            fs=sr,
            nperseg=SPEC_N_FFT,
            noverlap=SPEC_N_FFT - SPEC_HOP,
            boundary=None,
            padded=False,
        )
        power = np.abs(zxx) ** 2
        if power.ndim == 1:
            power = power[:, np.newaxis]
        return cls(sr=int(sr), hop=SPEC_HOP, freqs=np.asarray(freqs), power=power)

    @classmethod
    def from_audio_path(cls, audio_path: str | Path) -> Optional["SongSpectrogram"]:
        path = Path(audio_path)
        if not path.is_file():
            return None
        try:
            result = subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    str(path),
                    "-ac",
                    "1",
                    "-ar",
                    str(SPEC_SR),
                    "-f",
                    "s16le",
                    "-",
                ],
                capture_output=True,
                check=True,
            )
        except (OSError, subprocess.CalledProcessError):
            return None
        samples = np.frombuffer(result.stdout, dtype="<i2").astype(np.float32) / 32768.0
        if samples.size == 0:
            return None
        return cls.from_samples(samples, SPEC_SR)

    def _frame_slice(self, start: float, width: float) -> tuple[int, int]:
        first = max(0, int(start * self.sr / self.hop))
        last = min(
            self.power.shape[1],
            max(first + 1, int(math.ceil((start + width) * self.sr / self.hop))),
        )
        return first, last

    def band_power(self, start: float, width: float, fmin: float, fmax: float) -> float:
        f0, f1 = self._frame_slice(start, width)
        chunk = self.power[:, f0:f1]
        if chunk.size == 0:
            return 0.0
        mask = (self.freqs >= fmin) & (self.freqs < fmax)
        if not np.any(mask):
            return 0.0
        return float(np.mean(chunk[mask, :]))

    def centroid(self, start: float, width: float) -> float:
        f0, f1 = self._frame_slice(start, width)
        chunk = self.power[:, f0:f1]
        if chunk.size == 0:
            return float("nan")
        weights = np.mean(chunk, axis=1)
        total = float(np.sum(weights))
        if total <= 1e-12:
            return float("nan")
        return float(np.sum(self.freqs * weights) / total)


def _nan_bands() -> dict[str, float]:
    return {name: float("nan") for name in SPEC_FEATURE_NAMES}


def band_features_at(
    spec: Optional[SongSpectrogram],
    *,
    t: float,
    width: float,
    prev_t: Optional[float] = None,
) -> dict[str, float]:
    """Frequency-balance features for [t, t+width), plus deltas vs previous bar."""
    if spec is None or width <= 0:
        return _nan_bands()
    now: dict[str, float] = {}
    for name, lo, hi in FREQ_BANDS:
        now[f"spec_{name}"] = spec.band_power(t, width, lo, hi)
    now["spec_centroid"] = spec.centroid(t, width)
    total = sum(now[f"spec_{name}"] for name, _lo, _hi in FREQ_BANDS) or 1e-12
    bass = now["spec_sub"] + now["spec_bass"]
    highs = now["spec_highmid"] + now["spec_high"]
    now["spec_bass_share"] = bass / total
    now["spec_high_share"] = highs / total

    if prev_t is None:
        for name, _lo, _hi in FREQ_BANDS:
            now[f"spec_{name}_dprev"] = float("nan")
        now["spec_centroid_dprev"] = float("nan")
        return {key: now[key] for key in SPEC_FEATURE_NAMES}

    prev = band_features_at(spec, t=prev_t, width=width, prev_t=None)
    for name, _lo, _hi in FREQ_BANDS:
        now[f"spec_{name}_dprev"] = now[f"spec_{name}"] - prev[f"spec_{name}"]
    now["spec_centroid_dprev"] = now["spec_centroid"] - prev["spec_centroid"]
    return {key: now[key] for key in SPEC_FEATURE_NAMES}
