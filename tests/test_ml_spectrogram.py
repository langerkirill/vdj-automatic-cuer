"""Full-song spectrogram must see frequency balance, not just loudness."""

from __future__ import annotations

import math
import unittest

import numpy as np

from vdj_cuer.ml.spectrogram import (
    SPEC_SR,
    SongSpectrogram,
    band_features_at,
)


def _sine(hz: float, seconds: float, sr: int = SPEC_SR) -> np.ndarray:
    n = int(seconds * sr)
    t = np.arange(n, dtype=np.float64) / sr
    return (0.4 * np.sin(2 * np.pi * hz * t)).astype(np.float32)


class SongSpectrogramTests(unittest.TestCase):
    def test_bass_tone_has_more_bass_than_highs(self) -> None:
        spec = SongSpectrogram.from_samples(_sine(110, 2.0), SPEC_SR)
        row = band_features_at(spec, t=0.4, width=0.8)
        self.assertGreater(row["spec_bass"], row["spec_highmid"])
        self.assertGreater(row["spec_bass_share"], 0.35)
        self.assertLess(row["spec_centroid"], 400.0)

    def test_treble_tone_has_more_highs_than_bass(self) -> None:
        spec = SongSpectrogram.from_samples(_sine(3000, 2.0), SPEC_SR)
        row = band_features_at(spec, t=0.4, width=0.8)
        self.assertGreater(row["spec_highmid"], row["spec_bass"])
        self.assertGreater(row["spec_high_share"], 0.35)
        self.assertGreater(row["spec_centroid"], 1500.0)

    def test_section_change_moves_balance(self) -> None:
        audio = np.concatenate([_sine(110, 2.0), _sine(3000, 2.0)])
        spec = SongSpectrogram.from_samples(audio, SPEC_SR)
        low = band_features_at(spec, t=0.4, width=0.8)
        high = band_features_at(spec, t=2.4, width=0.8)
        self.assertGreater(high["spec_centroid"], low["spec_centroid"] + 800)
        self.assertGreater(high["spec_high_share"], low["spec_high_share"])

    def test_missing_spec_is_nan(self) -> None:
        row = band_features_at(None, t=0.0, width=1.0)
        self.assertTrue(math.isnan(row["spec_bass"]))
        self.assertTrue(math.isnan(row["spec_centroid"]))


if __name__ == "__main__":
    unittest.main()
