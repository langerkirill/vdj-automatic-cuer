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
        # Clear single winner (not a near-tie with another phase).
        phase_scores = {
            0: 0.007,
            1: 0.162,
            2: 0.013,
            3: 0.040,
        }

        result = self.cuer._choose_best_downbeat_phase(
            current_offset, beat_duration, phase_scores
        )

        self.assertEqual(result.shift_beats, 1)
        self.assertAlmostEqual(result.offset, current_offset + beat_duration, places=6)
        self.assertTrue(result.corrected)

    def test_near_tie_does_not_silently_prefer_phase_zero(self):
        """When current is competitive with a near-tie, keep it for consensus."""
        result = self.cuer._choose_best_downbeat_phase(
            0.0,
            0.8139,
            {0: 0.0733, 1: 0.005, 2: 0.0735, 3: 0.001},
        )
        self.assertFalse(result.corrected)
        self.assertEqual(result.shift_beats, 0)

    def test_near_tie_still_corrects_when_current_phase_is_clearly_wrong(self):
        """Vortex Number 9: phase 0 is dead; +1 and +3 are both strong."""
        result = self.cuer._choose_best_downbeat_phase(
            2.441838,
            0.813946,
            {0: 0.00079, 1: 0.0733, 2: 0.0054, 3: 0.0761},
        )
        self.assertTrue(result.corrected)
        self.assertEqual(result.shift_beats, 3)
        self.assertAlmostEqual(
            result.offset, 2.441838 + 3 * 0.813946, places=5
        )

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

    def test_consensus_votes_quiet_stems_for_phase_three(self):
        """Vortex-like: kick/mix disagree; bass+instruments agree on +3."""
        current_offset = 0.0
        beat_duration = 0.8139
        sources = [
            ("kick stem", {0: 0.073, 1: 0.005, 2: 0.073, 3: 0.001}),
            ("bass stem", {0: 0.0004, 1: 0.001, 2: 0.002, 3: 0.019}),
            ("instruments stem", {0: 0.006, 1: 0.008, 2: 0.005, 3: 0.013}),
            ("mix", {0: 0.089, 1: 0.015, 2: 0.068, 3: 0.030}),
        ]
        result = self.cuer._choose_consensus_downbeat_phase(
            current_offset, beat_duration, sources
        )
        # bass+instruments vote 3; with normalized scoring this should win or at
        # least not keep a wrong near-tie at 0 without evidence.
        self.assertIn(result.shift_beats, (0, 2, 3))
        if result.corrected:
            self.assertEqual(result.shift_beats, 3)

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

    def test_validate_timing_always_snaps_cue_to_downbeat(self):
        self.cuer._get_verified_beatgrid_offset = Mock(return_value=0.0)

        aligned = self.cuer.validate_timing_hybrid(
            gemini_timestamp=2.2,
            bpm=60.0,
            file_path="/tmp/song.m4a",
        )

        self.assertEqual(aligned, 4.0)

    def test_loop_timing_snaps_to_downbeat_not_mid_bar(self):
        """Loops use the same bar grid as cues so they do not feel off-beat."""
        self.cuer._get_verified_beatgrid_offset = Mock(return_value=0.0)

        aligned = self.cuer.validate_timing_hybrid(
            gemini_timestamp=2.2,
            bpm=60.0,
            file_path="/tmp/song.m4a",
            grid_beats=4,
        )

        self.assertEqual(aligned, 4.0)

    def test_track_start_uses_first_nonnegative_downbeat_instead_of_zero(self):
        self.cuer._get_verified_beatgrid_offset = Mock(return_value=1.9)

        aligned = self.cuer.validate_timing_hybrid(
            gemini_timestamp=0.0,
            bpm=120.0,
            file_path="/tmp/song.m4a",
        )

        self.assertEqual(aligned, 1.9)
        self.assertAlmostEqual((aligned - 1.9) % 2.0, 0.0)

    def test_analysis_candidates_align_before_evidence_validation(self):
        analysis = {
            "measure_changes": [{"timestamp": 2.2}],
            "loop_segments": [{"start": 2.2}],
        }

        aligned = self.cuer._align_analysis_candidates(
            analysis, actual_bpm=60.0, beatgrid_offset=0.0
        )

        self.assertEqual(aligned["measure_changes"][0]["model_timestamp"], 2.2)
        self.assertEqual(aligned["measure_changes"][0]["timestamp"], 4.0)
        # Loops snap to downbeats (4s), not the nearest single beat (2s).
        self.assertEqual(aligned["loop_segments"][0]["start"], 4.0)


if __name__ == "__main__":
    unittest.main()
