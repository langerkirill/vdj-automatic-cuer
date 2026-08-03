import unittest
from unittest.mock import patch

from vdj_cuer.stem_evidence import StemEvidence, StemProfile
from vdj_cuer.stems import _stem_gate_confidence


class StemGateConfidenceTests(unittest.TestCase):
    def test_medium_asserted_components_pass_precision_gate_threshold(self):
        evidence = StemEvidence(
            activity={
                "kick": "medium",
                "hihat": "none",
                "bass": "medium",
                "instruments": "none",
                "vocal": "none",
            },
            scores={
                "kick": 0.25,
                "hihat": 0.0,
                "bass": 0.22,
                "instruments": 0.0,
                "vocal": 0.0,
            },
            elements=["drums", "bass"],
            uncertain_elements=[],
            confidence=0.22,
        )
        self.assertGreaterEqual(_stem_gate_confidence(evidence), 0.75)

    def test_empty_elements_have_zero_confidence(self):
        evidence = StemEvidence(
            activity={},
            scores={},
            elements=[],
            uncertain_elements=["drums"],
            confidence=0.0,
        )
        self.assertEqual(_stem_gate_confidence(evidence), 0.0)

    def test_apply_measured_stem_activity_overwrites_model_confidence(self):
        import automatic_music_cuer_gemini as cuer_module

        with patch("builtins.print"):
            cuer = cuer_module.AutomaticMusicCuer(
                gemini_api_key="test-key",
                vdj_database_path="/tmp/database.xml",
            )

        frames = tuple([0.0] * 100 + [0.5] * 100 + [0.0] * 100)
        profile = StemProfile.from_frames(frames, frame_seconds=0.25)
        silent = StemProfile.from_frames(tuple([0.0] * 300), 0.25)
        with patch.object(
            cuer._track_audio_cache,
            "get_or_load_stem_profiles",
            return_value={
                "kick": profile,
                "hihat": silent,
                "bass": profile,
                "instruments": silent,
                "vocal": silent,
            },
        ):
            analysis = {
                "measure_changes": [
                    {
                        "timestamp": 30.0,
                        "elements": ["drums"],
                        "cue_name": "Drop",
                        "color": "purple",
                        "confidence": 0.99,
                        "role": "drop",
                    }
                ],
                "loop_segments": [],
            }
            updated = cuer._apply_measured_stem_activity(
                analysis, [("kick", "/tmp/kick.m4a")], bpm=120
            )

        cue = updated["measure_changes"][0]
        self.assertEqual(cue["assertion_source"], "calibrated_vdj_stems")
        self.assertEqual(cue["model_confidence"], 0.99)
        self.assertLessEqual(cue["confidence"], 0.99)
        self.assertGreaterEqual(cue["confidence"], 0.70)


if __name__ == "__main__":
    unittest.main()
