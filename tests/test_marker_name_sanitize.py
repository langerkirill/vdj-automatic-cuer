"""Cue/loop names must never contain ampersands or double-entity encoding."""

from __future__ import annotations

import unittest

from automatic_music_cuer_gemini import AutomaticMusicCuer
from vdj_database_safety import format_vdj_poi_line


class MarkerNameSanitizeTests(unittest.TestCase):
    def test_ampersand_becomes_and(self):
        self.assertEqual(
            AutomaticMusicCuer.sanitize_marker_name("Bass & Snaps In"),
            "Bass and Snaps In",
        )

    def test_double_entity_decoded_then_and(self):
        self.assertEqual(
            AutomaticMusicCuer.sanitize_marker_name("Bass &amp;amp; Snaps In"),
            "Bass and Snaps In",
        )

    def test_poi_line_does_not_contain_amp_entity(self):
        name = AutomaticMusicCuer.sanitize_marker_name("Bass & Snaps In")
        line = format_vdj_poi_line(
            pos=10.0,
            poi_type="cue",
            num="2",
            color="4294934272",
            name=name,
            newline="\n",
        )
        self.assertIn('Name="Bass and Snaps In"', line)
        self.assertNotIn("&amp;", line)
        self.assertNotIn("&", line.split("Name=")[1].split()[0])

    def test_normalize_analysis_strips_ampersand_from_gemini_names(self):
        from unittest.mock import patch

        with patch("builtins.print"):
            cuer = AutomaticMusicCuer(
                gemini_api_key="test-key",
                vdj_database_path="/tmp/database.xml",
            )
        analysis = {
            "measure_changes": [
                {
                    "timestamp": 10.0,
                    "elements": ["drums", "bass", "synth"],
                    "cue_name": "Bass & Snaps In",
                    "color": "orange",
                    "confidence": 0.9,
                    "assertion_source": "calibrated_vdj_stems",
                }
            ],
            "loop_segments": [
                {
                    "start": 20.0,
                    "length_beats": 16,
                    "elements": ["vocals", "synth"],
                    "loop_name": "Vocal & Synth Loop",
                    "color": "yellow",
                    "confidence": 0.9,
                    "assertion_source": "calibrated_vdj_stems",
                }
            ],
        }
        fixed = cuer._normalize_analysis_data(analysis)
        self.assertEqual(fixed["measure_changes"][0]["cue_name"], "Bass and Snaps In")
        self.assertNotIn("&", fixed["measure_changes"][0]["cue_name"])
        self.assertEqual(
            fixed["loop_segments"][0]["loop_name"], "Vocal and Synth Loop"
        )
        self.assertNotIn("&", fixed["loop_segments"][0]["loop_name"])


if __name__ == "__main__":
    unittest.main()
