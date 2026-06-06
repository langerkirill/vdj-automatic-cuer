import unittest
from unittest.mock import Mock, patch

import automatic_music_cuer_gemini as cuer_module


class BeatgridAlignmentTests(unittest.TestCase):
    def setUp(self):
        with patch("builtins.print"):
            self.cuer = cuer_module.AutomaticMusicCuer(
                gemini_api_key="test-key",
                vdj_database_path="/tmp/database.xml",
            )

    def test_selects_stronger_downbeat_phase(self):
        beat_duration = 0.638299
        current_offset = 2.462313
        phase_scores = {
            0: 0.007,
            1: 0.162,
            2: 0.013,
            3: 0.167,
        }

        result = self.cuer._choose_best_downbeat_phase(
            current_offset, beat_duration, phase_scores
        )

        self.assertEqual(result.shift_beats, 1)
        self.assertAlmostEqual(result.offset, current_offset + beat_duration, places=6)
        self.assertTrue(result.corrected)

    def test_keeps_current_phase_when_not_confident(self):
        result = self.cuer._choose_best_downbeat_phase(
            2.462313,
            0.638299,
            {
                0: 0.100,
                1: 0.118,
                2: 0.094,
                3: 0.105,
            },
        )

        self.assertEqual(result.shift_beats, 0)
        self.assertAlmostEqual(result.offset, 2.462313, places=6)
        self.assertFalse(result.corrected)

    def test_selects_confident_fine_offset_from_kick_stem(self):
        result = self.cuer._choose_best_beat_offset(
            current_offset=18.822834,
            beat_duration=0.681814,
            current_score=0.0145,
            best_offset=18.552834,
            best_score=0.0754,
            source="kick stem",
        )

        self.assertTrue(result.corrected)
        self.assertAlmostEqual(result.offset, 18.552834, places=6)
        self.assertAlmostEqual(result.fine_shift_seconds, -0.27, places=3)

    def test_rejects_weak_fine_offset(self):
        result = self.cuer._choose_best_beat_offset(
            current_offset=3.868,
            beat_duration=0.476,
            current_score=0.1016,
            best_offset=3.788,
            best_score=0.1107,
            source="kick stem",
        )

        self.assertFalse(result.corrected)
        self.assertAlmostEqual(result.offset, 3.868, places=6)

    def test_rejects_mix_only_fine_offset(self):
        result = self.cuer._choose_best_beat_offset(
            current_offset=18.822834,
            beat_duration=0.681814,
            current_score=0.0145,
            best_offset=18.552834,
            best_score=0.0754,
            source="mix",
        )

        self.assertFalse(result.corrected)
        self.assertAlmostEqual(result.offset, 18.822834, places=6)

    def test_selects_consensus_downbeat_phase(self):
        current_offset = 4.241066
        beat_duration = 0.659184
        sources = [
            ("mix", {0: 0.015, 1: 0.019, 2: 0.036, 3: 0.013}),
            ("vocal stem", {0: 0.013, 1: 0.008, 2: 0.033, 3: 0.011}),
            ("instruments stem", {0: 0.006, 1: 0.022, 2: 0.038, 3: 0.008}),
        ]

        result = self.cuer._choose_consensus_downbeat_phase(
            current_offset, beat_duration, sources
        )

        self.assertTrue(result.corrected)
        self.assertEqual(result.shift_beats, 2)
        self.assertEqual(result.source, "multi-source consensus")
        self.assertAlmostEqual(
            result.offset, current_offset + (2 * beat_duration), places=6
        )

    def test_rejects_single_source_downbeat_phase(self):
        result = self.cuer._choose_consensus_downbeat_phase(
            4.241066,
            0.659184,
            [("mix", {0: 0.015, 1: 0.019, 2: 0.036, 3: 0.013})],
        )

        self.assertFalse(result.corrected)
        self.assertEqual(result.shift_beats, 0)

    def test_validate_timing_uses_verified_beatgrid_offset(self):
        self.cuer.get_beatgrid_offset = Mock(return_value=2.462313)
        self.cuer._get_verified_beatgrid_offset = Mock(return_value=3.100612)

        aligned = self.cuer.validate_timing_hybrid(
            gemini_timestamp=20.334685,
            bpm=94.0,
            file_path="/tmp/song.m4a",
        )

        self.assertAlmostEqual(aligned, 20.334685 + 0.638299, places=3)


if __name__ == "__main__":
    unittest.main()
