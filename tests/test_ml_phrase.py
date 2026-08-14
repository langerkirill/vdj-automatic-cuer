"""Phrase-scale features around each VDJ 1: novelty, harmony, phrase index."""

from __future__ import annotations

import math
import unittest

import numpy as np

from vdj_cuer.ml.spectrogram import SPEC_SR, SongSpectrogram
from vdj_cuer.ml.phrase import PHRASE_FEATURE_NAMES, phrase_features_at


def _sine(hz: float, seconds: float, sr: int = SPEC_SR) -> np.ndarray:
    n = int(seconds * sr)
    t = np.arange(n, dtype=np.float64) / sr
    return (0.4 * np.sin(2 * np.pi * hz * t)).astype(np.float32)


class PhraseFeatureTests(unittest.TestCase):
    def test_names_cover_novelty_timbre_harmony_metric(self) -> None:
        joined = " ".join(PHRASE_FEATURE_NAMES)
        self.assertIn("flux", joined)
        self.assertIn("chroma", joined)
        self.assertIn("phrase8", joined)
        self.assertIn("phrase16", joined)

    def test_phrase_index_wraps(self) -> None:
        spec = SongSpectrogram.from_samples(_sine(220, 2.0), SPEC_SR)
        row = phrase_features_at(spec, t=0.0, width=0.5, bar_index=17)
        self.assertEqual(row["phrase8"], 1.0)
        self.assertEqual(row["phrase16"], 1.0)

    def test_section_change_raises_flux_and_chroma_change(self) -> None:
        audio = np.concatenate([_sine(110, 2.0), _sine(196, 2.0)])
        spec = SongSpectrogram.from_samples(audio, SPEC_SR)
        stable = phrase_features_at(spec, t=0.4, width=0.6, bar_index=0, prev_t=-0.2)
        change = phrase_features_at(spec, t=2.0, width=0.6, bar_index=1, prev_t=1.4)
        self.assertGreater(change["spec_flux"], stable["spec_flux"])
        self.assertGreater(change["chroma_change_1"], 0.05)

    def test_missing_spec_is_nan_except_phrase_index(self) -> None:
        row = phrase_features_at(None, t=0.0, width=1.0, bar_index=4)
        self.assertEqual(row["phrase8"], 4.0)
        self.assertTrue(math.isnan(row["spec_flux"]))
        self.assertTrue(math.isnan(row["chroma_change_1"]))


if __name__ == "__main__":
    unittest.main()
