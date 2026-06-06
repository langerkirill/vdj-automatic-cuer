import unittest

import cue_visual_audit as audit


class VisualCueAuditTests(unittest.TestCase):
    def test_infers_yellow_for_vocals_drums_and_instruments(self):
        elements = audit.infer_elements_from_activity(
            {"vocal": 0.8, "kick": 0.7, "hihat": 0.5, "bass": 0.4, "instruments": 0.6}
        )

        self.assertEqual(elements, {"vocals", "drums", "bass", "synth"})
        self.assertEqual(audit.expected_color(elements), "yellow")

    def test_flags_drum_name_when_other_elements_are_active(self):
        issue = audit.name_element_issue(
            "Outro Drums", {"drums", "bass", "synth"}, timestamp=180, song_length=200
        )

        self.assertIsNotNone(issue)
        self.assertIn("drums-only", issue)

    def test_flags_outro_name_too_early(self):
        issue = audit.name_element_issue(
            "Outro", {"vocals", "synth"}, timestamp=80, song_length=200
        )

        self.assertIsNotNone(issue)
        self.assertIn("outro", issue.lower())

    def test_flags_drop_without_energy_rise(self):
        issue = audit.energy_shape_issue("Main Drop", before_energy=0.8, after_energy=0.85)

        self.assertIsNotNone(issue)
        self.assertIn("rise", issue)

    def test_no_stems_does_not_claim_missing_vocals_or_drums(self):
        track = audit.Track(
            path="/tmp/example.flac",
            title="Example",
            artist="Artist",
            length=120,
            pois=[
                audit.Poi(
                    name="Vocal Drop",
                    pos=30,
                    poi_type="cue",
                    color_value="4294967040",
                    color_name="yellow",
                )
            ],
        )
        analysis = audit.AudioAnalysis(
            duration=120,
            bin_seconds=1,
            mix=[0.3] * 120,
            stems={},
        )

        observations, issues = audit.inspect_track(track, analysis)

        self.assertEqual(observations[0].elements, "no-stems")
        self.assertEqual(observations[0].expected_color, "unknown")
        self.assertNotIn("vocal stem", " ".join(issue.issue for issue in issues))


if __name__ == "__main__":
    unittest.main()
