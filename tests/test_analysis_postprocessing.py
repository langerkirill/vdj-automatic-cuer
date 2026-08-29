import unittest
from unittest.mock import patch

import automatic_music_cuer_gemini as cuer_module
from vdj_cuer.stem_evidence import StemProfile


class AnalysisPostprocessingTests(unittest.TestCase):
    def setUp(self):
        with patch("builtins.print"):
            self.cuer = cuer_module.AutomaticMusicCuer(
                gemini_api_key="test-key",
                vdj_database_path="/tmp/database.xml",
            )

    def test_relabels_melodic_cue_when_elements_are_not_pure_melody(self):
        analysis = {
            "measure_changes": [
                {
                    "timestamp": 163.4,
                    "elements": ["drums", "bass", "synth"],
                    "cue_name": "Melodic Section",
                    "color": "blue",
                }
            ],
            "loop_segments": [],
        }

        normalized = self.cuer._normalize_analysis_data(analysis)

        cue = normalized["measure_changes"][0]
        self.assertNotIn("melodic", cue["cue_name"].lower())
        self.assertNotIn("melody", cue["cue_name"].lower())
        self.assertEqual(cue["color"], "green")

    def test_drums_plus_audible_vocal_is_yellow_even_if_elements_missed_it(self):
        """NDULE Beat Entry: voice is in the mix so the marker cannot stay green."""
        color = self.cuer.validate_color_assignment(
            ["drums", "synth"],
            "green",
            stem_activity={"vocal": "medium", "kick": "medium", "instruments": "medium"},
        )
        self.assertEqual(color, "yellow")
        quiet = self.cuer.validate_color_assignment(
            ["drums", "synth"],
            "green",
            stem_activity={"vocal": "none", "kick": "high", "instruments": "medium"},
        )
        self.assertEqual(quiet, "green")
        bleed = self.cuer.validate_color_assignment(
            ["drums", "synth"],
            "green",
            stem_activity={"vocal": "low", "kick": "medium", "instruments": "medium"},
        )
        self.assertEqual(bleed, "green")

    def test_relabels_drum_loop_when_more_than_drums_are_present(self):
        analysis = {
            "measure_changes": [],
            "loop_segments": [
                {
                    "start": 224.68,
                    "length_beats": 32,
                    "elements": ["drums", "bass", "synth"],
                    "loop_name": "Outro Drum Loop",
                    "color": "purple",
                }
            ],
        }

        normalized = self.cuer._normalize_analysis_data(analysis)

        loop = normalized["loop_segments"][0]
        self.assertNotIn("drum", loop["loop_name"].lower())
        self.assertEqual(loop["color"], "green")

    def test_stem_activity_overrides_impossible_drum_only_result(self):
        analysis = {
            "measure_changes": [],
            "loop_segments": [
                {
                    "start": 224.68,
                    "length_beats": 32,
                    "elements": ["drums"],
                    "loop_name": "Outro Drum Loop",
                    "color": "purple",
                    "stem_activity": {
                        "kick": "high",
                        "hihat": "medium",
                        "bass": "medium",
                        "instruments": "medium",
                        "vocal": "none",
                    },
                }
            ],
        }

        normalized = self.cuer._normalize_analysis_data(analysis)

        loop = normalized["loop_segments"][0]
        self.assertIn("bass", loop["elements"])
        self.assertIn("synth", loop["elements"])
        self.assertNotIn("drum", loop["loop_name"].lower())
        self.assertEqual(loop["color"], "green")

    def test_raw_stem_names_are_normalized_to_supported_elements(self):
        analysis = {
            "measure_changes": [
                {
                    "timestamp": 20.43,
                    "elements": ["vocal", "kick", "hihat", "instruments"],
                    "cue_name": "Vocals & Kick In",
                    "color": "yellow",
                    "stem_activity": {
                        "kick": "high",
                        "hihat": "medium",
                        "bass": "none",
                        "instruments": "medium",
                        "vocal": "high",
                    },
                }
            ],
            "loop_segments": [],
        }

        normalized = self.cuer._normalize_analysis_data(analysis)

        cue = normalized["measure_changes"][0]
        self.assertEqual(cue["elements"], ["vocals", "drums", "synth"])
        self.assertEqual(cue["color"], "yellow")

    def test_relabels_instrumental_name_when_vocals_are_present(self):
        analysis = {
            "measure_changes": [],
            "loop_segments": [
                {
                    "start": 30.64,
                    "length_beats": 16,
                    "elements": ["drums", "synth", "vocals"],
                    "loop_name": "Instrumental Groove",
                    "color": "yellow",
                }
            ],
        }

        normalized = self.cuer._normalize_analysis_data(analysis)

        loop = normalized["loop_segments"][0]
        self.assertNotIn("instrumental", loop["loop_name"].lower())
        self.assertEqual(loop["color"], "yellow")

    def test_relabels_bass_name_when_bass_is_not_present(self):
        analysis = {
            "measure_changes": [
                {
                    "timestamp": 30.64,
                    "elements": ["drums", "synth", "vocals"],
                    "cue_name": "Bass & Synth In",
                    "color": "yellow",
                }
            ],
            "loop_segments": [],
        }

        normalized = self.cuer._normalize_analysis_data(analysis)

        cue = normalized["measure_changes"][0]
        self.assertNotIn("bass", cue["cue_name"].lower())
        self.assertEqual(cue["color"], "yellow")

    def test_shortens_loop_that_would_cross_next_section_change(self):
        analysis = {
            "measure_changes": [
                {"timestamp": 0.0, "elements": ["drums"], "cue_name": "Intro", "color": "purple"},
                {"timestamp": 34.0, "elements": ["drums", "bass"], "cue_name": "Bass In", "color": "green"},
            ],
            "loop_segments": [
                {
                    "start": 18.0,
                    "length_beats": 32,
                    "elements": ["drums"],
                    "loop_name": "Drum Loop",
                    "color": "purple",
                }
            ],
        }

        fixed = self.cuer._postprocess_loop_segments(
            analysis,
            bpm=120,
            song_length=120,
        )

        self.assertEqual(len(fixed["loop_segments"]), 1)
        self.assertEqual(fixed["loop_segments"][0]["length_beats"], 16)

    def test_deletes_loop_when_section_change_is_too_close(self):
        analysis = {
            "measure_changes": [
                {"timestamp": 0.0, "elements": ["drums"], "cue_name": "Intro", "color": "purple"},
                {"timestamp": 20.5, "elements": ["vocals"], "cue_name": "Vocal In", "color": "orange"},
            ],
            "loop_segments": [
                {
                    "start": 19.5,
                    "length_beats": 16,
                    "elements": ["drums"],
                    "loop_name": "Drum Loop",
                    "color": "purple",
                }
            ],
        }

        fixed = self.cuer._postprocess_loop_segments(
            analysis,
            bpm=120,
            song_length=120,
        )

        self.assertEqual(fixed["loop_segments"], [])

    def test_downgrades_drop_name_when_energy_does_not_rise(self):
        analysis = {
            "measure_changes": [
                {
                    "timestamp": 4.0,
                    "elements": ["drums", "synth", "vocals"],
                    "cue_name": "Main Drop",
                    "role": "drop",
                    "color": "yellow",
                }
            ],
            "loop_segments": [],
        }
        flat_profile = StemProfile.from_frames([0.5] * 32, frame_seconds=0.5)

        with patch(
            "vdj_cuer.analysis_postprocess.StemProfile.decode",
            return_value=flat_profile,
        ):
            validated = self.cuer._validate_structural_assertions(
                analysis, "/tmp/song.flac"
            )

        cue = validated["measure_changes"][0]
        self.assertEqual(cue["cue_name"], "Main Vocal Mix")
        self.assertEqual(cue["role"], "section")
        self.assertTrue(cue["assertion_downgraded"])

    def test_keeps_drop_name_when_energy_rises(self):
        analysis = {
            "measure_changes": [
                {
                    "timestamp": 4.0,
                    "elements": ["drums", "synth"],
                    "cue_name": "Drop",
                    "role": "drop",
                    "color": "green",
                }
            ],
            "loop_segments": [],
        }
        rising_profile = StemProfile.from_frames(
            [0.2] * 8 + [0.8] * 8,
            frame_seconds=0.5,
        )

        with patch(
            "vdj_cuer.analysis_postprocess.StemProfile.decode",
            return_value=rising_profile,
        ):
            validated = self.cuer._validate_structural_assertions(
                analysis, "/tmp/song.flac"
            )

        self.assertEqual(validated["measure_changes"][0]["cue_name"], "Drop")


if __name__ == "__main__":
    unittest.main()
