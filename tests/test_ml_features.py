"""Bar-1 feature rows from stem/mix envelopes."""

from __future__ import annotations

import math
import unittest

from vdj_cuer.ml.features import (
    FEATURE_NAMES,
    bar_feature_row,
    iter_bar_times,
)
from vdj_cuer.stem_evidence import StemProfile


def _profile(values: list[float], frame_seconds: float = 0.25) -> StemProfile:
    return StemProfile.from_frames(values, frame_seconds=frame_seconds)


class IterBarTimesTests(unittest.TestCase):
    def test_bars_land_on_the_grid(self) -> None:
        times = iter_bar_times(duration=8.0, bpm=120.0, offset=0.0)
        self.assertEqual(times[0], 0.0)
        self.assertAlmostEqual(times[1] - times[0], 2.0)
        self.assertLess(times[-1], 8.0)


class BarFeatureRowTests(unittest.TestCase):
    def test_feature_names_are_stable(self) -> None:
        self.assertIn("pos_frac", FEATURE_NAMES)
        self.assertIn("mix_energy", FEATURE_NAMES)
        self.assertIn("vocal_energy", FEATURE_NAMES)
        self.assertIn("clean_entry", FEATURE_NAMES)
        self.assertIn("signature_changed", FEATURE_NAMES)
        self.assertIn("spec_bass", FEATURE_NAMES)
        self.assertIn("spec_centroid", FEATURE_NAMES)
        self.assertIn("spec_bass_share", FEATURE_NAMES)
        self.assertIn("spec_flux", FEATURE_NAMES)
        self.assertIn("chroma_change_1", FEATURE_NAMES)
        self.assertIn("phrase8", FEATURE_NAMES)

    def test_missing_stems_are_nan(self) -> None:
        mix = _profile([0.2] * 40)
        row = bar_feature_row(
            {"mix": mix},
            t=2.0,
            duration=10.0,
            bpm=120.0,
            offset=0.0,
            bar_index=1,
        )
        self.assertTrue(math.isnan(row["vocal_energy"]))
        self.assertTrue(math.isnan(row["kick_energy"]))
        self.assertTrue(math.isnan(row["spec_bass"]))
        self.assertAlmostEqual(row["mix_energy"], 0.2, places=2)
        self.assertEqual(set(row) - {"timestamp"}, set(FEATURE_NAMES) | {"timestamp"} - {"timestamp"})
        self.assertTrue(set(FEATURE_NAMES).issubset(row))

    def test_vocal_onset_flips_delta(self) -> None:
        # 120 BPM → 2s bars. Quiet then loud vocal from t=4s.
        frames = [0.01] * 16 + [0.8] * 16
        vocal = _profile(frames)
        kick = _profile([0.4] * 32)
        mix = _profile([0.2] * 16 + [0.6] * 16)
        quiet = bar_feature_row(
            {"mix": mix, "vocal": vocal, "kick": kick},
            t=2.0,
            duration=8.0,
            bpm=120.0,
            offset=0.0,
            bar_index=1,
        )
        onset = bar_feature_row(
            {"mix": mix, "vocal": vocal, "kick": kick},
            t=4.0,
            duration=8.0,
            bpm=120.0,
            offset=0.0,
            bar_index=2,
        )
        self.assertGreater(onset["vocal_energy"], quiet["vocal_energy"])
        self.assertGreater(onset["vocal_dprev"], 0.2)
        self.assertTrue(onset["signature_changed"] in (0.0, 1.0))


if __name__ == "__main__":
    unittest.main()
