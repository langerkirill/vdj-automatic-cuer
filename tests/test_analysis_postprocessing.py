import unittest
from unittest.mock import patch

import automatic_music_cuer_gemini as cuer_module


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


if __name__ == "__main__":
    unittest.main()
