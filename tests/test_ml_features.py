"""Bar-1 feature rows from stem/mix envelopes."""

from __future__ import annotations

import math
import unittest

from vdj_cuer.ml.features import (
    FEATURE_NAMES,
    apply_track_relative_features,
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
        self.assertIn("mix_is_peak", FEATURE_NAMES)
        self.assertIn("early_intro", FEATURE_NAMES)
        self.assertIn("mix_offset", FEATURE_NAMES)
        self.assertIn("kick_offset", FEATURE_NAMES)
        from vdj_cuer.ml.features import MODEL_FEATURE_NAMES

        self.assertNotIn("pos_frac", MODEL_FEATURE_NAMES)
        self.assertNotIn("early_intro", MODEL_FEATURE_NAMES)
        self.assertNotIn("late_outro", MODEL_FEATURE_NAMES)
        self.assertIn("mix_energy", MODEL_FEATURE_NAMES)
        self.assertIn("mix_local8", MODEL_FEATURE_NAMES)
        self.assertIn("mix_z", MODEL_FEATURE_NAMES)
        self.assertIn("mix_offset", MODEL_FEATURE_NAMES)
        self.assertIn("kick_offset", MODEL_FEATURE_NAMES)
        self.assertIn("stem_sig_changed_8", MODEL_FEATURE_NAMES)
        self.assertIn("texture_change", MODEL_FEATURE_NAMES)
        self.assertIn("energy_drop_held", MODEL_FEATURE_NAMES)
        self.assertIn("next8_mix_delta", MODEL_FEATURE_NAMES)
        self.assertIn("next16_mix_delta", MODEL_FEATURE_NAMES)
        self.assertIn("pre_decline", MODEL_FEATURE_NAMES)
        self.assertIn("mix_vs_prev8_max", MODEL_FEATURE_NAMES)
        self.assertIn("kick_share", MODEL_FEATURE_NAMES)
        self.assertIn("vocal_share", MODEL_FEATURE_NAMES)
        self.assertIn("kick_share_fwd8", MODEL_FEATURE_NAMES)
        self.assertIn("still_loud_kick_drop", MODEL_FEATURE_NAMES)
        self.assertIn("still_loud_vocal_drop", MODEL_FEATURE_NAMES)
        self.assertIn("phrase_pre_decline", MODEL_FEATURE_NAMES)
        self.assertIn("after_peak", MODEL_FEATURE_NAMES)

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
        self.assertIn(onset["mix_is_peak"], (0.0, 1.0))
        self.assertIn(onset["phrase8_zero"], (0.0, 1.0))

    def test_track_relative_features_mark_the_loudest_local_bar(self) -> None:
        from vdj_cuer.ml.features import apply_track_relative_features

        rows = [
            {"timestamp": 0.0, "mix_energy": 0.1},
            {"timestamp": 2.0, "mix_energy": 0.9},
            {"timestamp": 4.0, "mix_energy": 0.2},
            {"timestamp": 6.0, "mix_energy": 0.15},
        ]
        out = apply_track_relative_features(rows)
        self.assertEqual(out[1]["mix_local8"], 1.0)
        self.assertGreater(out[1]["mix_z"], out[0]["mix_z"])
        self.assertGreater(out[1]["mix_rank_frac"], out[0]["mix_rank_frac"])

    def test_derived_offsets_require_a_held_drop(self) -> None:
        from vdj_cuer.ml.features import apply_derived_features

        held = apply_derived_features(
            {"mix_energy": 0.3, "mix_prev": 0.6, "mix_next": 0.28, "mix_dprev": -0.3, "mix_dnext": -0.02}
        )
        bounce = apply_derived_features(
            {"mix_energy": 0.3, "mix_prev": 0.6, "mix_next": 0.7, "mix_dprev": -0.3, "mix_dnext": 0.4}
        )
        kick = apply_derived_features(
            {"kick_energy": 0.1, "kick_prev": 0.5, "kick_next": 0.08, "kick_dprev": -0.4, "kick_dnext": -0.02}
        )
        self.assertEqual(held["mix_offset"], 1.0)
        self.assertEqual(bounce["mix_offset"], 0.0)
        self.assertEqual(kick["kick_offset"], 1.0)

    def test_track_relative_marks_stem_signature_and_held_energy_drop(self) -> None:
        from vdj_cuer.ml.features import apply_track_relative_features

        rows = []
        for i in range(12):
            kick = 0.8 if i < 8 else 0.05
            mix = 0.85 if i < 8 else 0.25
            rows.append(
                {
                    "timestamp": float(i * 2.0),
                    "mix_energy": mix,
                    "mix_dprev": -0.6 if i == 8 else (0.02 if i < 8 else -0.01),
                    "kick_energy": kick,
                    "vocal_energy": 0.1,
                    "bass_energy": 0.4 if i < 8 else 0.05,
                    "instruments_energy": 0.3,
                    "hihat_energy": 0.2,
                    "chroma_change_8": 0.05 if i != 8 else 0.4,
                }
            )
        out = apply_track_relative_features(rows)
        self.assertEqual(out[8]["stem_sig_changed_8"], 1.0)
        self.assertEqual(out[3]["stem_sig_changed_8"], 0.0)
        self.assertEqual(out[8]["texture_change"], 1.0)
        self.assertEqual(out[8]["energy_drop_held"], 1.0)
        self.assertEqual(out[2]["energy_drop_held"], 0.0)
        self.assertGreater(out[2]["mix_vs_peak"], out[8]["mix_vs_peak"])
        self.assertEqual(out[8]["chroma_local8"], 1.0)

    def test_feature_matrix_does_not_leak_stem_sig_across_tracks(self) -> None:
        from vdj_cuer.ml.features import feature_matrix, MODEL_FEATURE_NAMES

        def bar(track: str, i: int, kick: float) -> dict:
            return {
                "track_id": track,
                "timestamp": float(i * 2.0),
                "mix_energy": 0.5,
                "kick_energy": kick,
                "vocal_energy": 0.1,
                "bass_energy": 0.2,
                "instruments_energy": 0.2,
                "hihat_energy": 0.1,
            }

        # Track A is all-kick. B continues later in time with kick off. Concatenated
        # globally, B[0] is 8+ bars after an A bar so a leak would flip sig_changed_8.
        rows = [bar("A", i, 0.8) for i in range(10)]
        rows.extend(
            {**bar("B", i, 0.05), "timestamp": 20.0 + i * 2.0} for i in range(4)
        )
        matrix = feature_matrix(rows)
        sig_i = list(MODEL_FEATURE_NAMES).index("stem_sig_changed_8")
        leaked = apply_track_relative_features(rows)
        self.assertEqual(leaked[10]["stem_sig_changed_8"], 1.0)
        self.assertEqual(matrix[10][sig_i], 0.0)

    def test_lookahead_marks_still_loud_bar_before_energy_falls(self) -> None:
        """Still-loud outros sit on a hot bar whose next 8–16 bars decay."""
        rows = []
        for i in range(20):
            mix = 0.85 if i < 12 else 0.18
            kick = 0.7 if i < 12 else 0.08
            rows.append(
                {
                    "timestamp": float(i * 2.0),
                    "mix_energy": mix,
                    "kick_energy": kick,
                    "vocal_energy": 0.2,
                    "chroma_change_8": 0.1,
                }
            )
        out = apply_track_relative_features(rows)
        self.assertLess(out[11]["next8_mix_delta"], -0.3)
        self.assertLess(out[11]["next16_mix_delta"], -0.3)
        self.assertEqual(out[11]["pre_decline"], 1.0)
        self.assertEqual(out[4]["pre_decline"], 0.0)
        self.assertGreater(out[4]["next8_mix_delta"], out[11]["next8_mix_delta"])
        self.assertGreater(out[11]["mix_vs_peak"], 0.9)

    def test_still_loud_kick_drop_when_mix_holds_and_kick_falls(self) -> None:
        """Still-loud outros often drop kick while the mix stays hot."""
        rows = []
        for i in range(10):
            rows.append(
                {
                    "timestamp": float(i * 2.0),
                    "mix_energy": 0.85,
                    "kick_energy": 0.70 if i < 8 else 0.08,
                    "kick_dprev": -0.62 if i == 8 else 0.01,
                    "vocal_energy": 0.4 if i < 8 else 0.05,
                    "vocal_dprev": -0.35 if i == 8 else 0.0,
                    "bass_energy": 0.5,
                    "instruments_energy": 0.4,
                    "phrase8": 0.0 if i == 8 else 3.0,
                    "phrase16": 0.0 if i == 8 else 7.0,
                    "spec_flux": 0.01,
                }
            )
        out = apply_track_relative_features(rows)
        self.assertEqual(out[8]["still_loud_kick_drop"], 1.0)
        self.assertEqual(out[3]["still_loud_kick_drop"], 0.0)
        self.assertEqual(out[8]["still_loud_vocal_drop"], 1.0)
        self.assertEqual(out[8]["phrase_pre_decline"], 1.0)
        self.assertLess(out[8]["kick_share"], out[3]["kick_share"])
        self.assertLess(out[7]["kick_share_fwd8"], -0.3)

    def test_after_peak_marks_bars_below_the_recent_mix_max(self) -> None:
        """Rank-13+ humans sit after a louder 8-bar window, not on mix onsets."""
        rows = []
        for i in range(16):
            mix = 0.90 if i < 8 else 0.40
            rows.append(
                {
                    "timestamp": float(i * 2.0),
                    "mix_energy": mix,
                    "kick_energy": 0.5 if i < 8 else 0.2,
                    "vocal_energy": 0.2,
                }
            )
        out = apply_track_relative_features(rows)
        self.assertEqual(out[10]["after_peak"], 1.0)
        self.assertEqual(out[3]["after_peak"], 0.0)
        self.assertLess(out[10]["mix_vs_prev8_max"], -0.08)

    def test_section_entry_is_after_peak_phrase_with_stem_change(self) -> None:
        """Rank-13+ after-peak humans are phrase starts whose stem signature flipped."""
        rows = []
        for i in range(16):
            kick = 0.80 if i < 8 else 0.08
            mix = 0.90 if i < 8 else 0.40
            rows.append(
                {
                    "timestamp": float(i * 2.0),
                    "mix_energy": mix,
                    "kick_energy": kick,
                    "vocal_energy": 0.15,
                    "bass_energy": 0.50 if i < 8 else 0.10,
                    "instruments_energy": 0.30,
                    "hihat_energy": 0.20,
                    "phrase8": 0.0 if i == 10 else 3.0,
                    "phrase16": 0.0 if i == 10 else 7.0,
                    "mix_dprev": -0.50 if i == 8 else 0.01,
                    "spec_flux": 0.02,
                }
            )
        out = apply_track_relative_features(rows)
        self.assertEqual(out[10]["after_peak"], 1.0)
        self.assertEqual(out[10]["phrase8_zero"], 1.0)
        self.assertEqual(out[10]["section_entry"], 1.0)
        self.assertEqual(out[3]["section_entry"], 0.0)
        peak_idx = next(i for i, row in enumerate(out) if row["mix_local8"] >= 1.0)
        self.assertLessEqual(out[peak_idx]["bars_since_local_peak"], 1e-9)
        self.assertGreater(out[10]["bars_since_local_peak"], out[peak_idx]["bars_since_local_peak"])


if __name__ == "__main__":
    unittest.main()
