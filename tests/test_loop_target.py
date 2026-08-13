"""AutoCue must land 2-3 loops per song when stems can prove them."""

import unittest
from unittest.mock import patch

from vdj_cuer.common import TARGET_MAX_LOOPS, TARGET_MIN_LOOPS
from vdj_cuer.core import AutomaticMusicCuer
from vdj_cuer.precision_gate import apply_precision_gate
from vdj_cuer.stem_evidence import StemProfile


def _loop(start, *, beats=16, confidence=0.9, name="Groove Loop"):
    return {
        "start": start,
        "length_beats": beats,
        "elements": ["drums", "bass"],
        "loop_name": name,
        "confidence": confidence,
        "role": "loop",
        "color": "green",
    }


class LoopTargetTests(unittest.TestCase):
    def test_target_is_two_to_three_loops(self):
        self.assertEqual(TARGET_MIN_LOOPS, 2)
        self.assertEqual(TARGET_MAX_LOOPS, 3)

    def test_precision_prompt_requires_two_to_three_loops(self):
        prompt = AutomaticMusicCuer._build_precision_prompt(
            "/tmp/song.flac",
            180.0,
            120.0,
            "isolated stems available",
        )
        self.assertIn("2-3", prompt)
        self.assertIn("at least 2", prompt.lower())
        self.assertNotIn("0-3 high-confidence loops", prompt)
        self.assertNotIn("zero loops is correct", prompt.lower())

    def test_precision_gate_accepts_mid_confidence_loops(self):
        analysis = {
            "measure_changes": [],
            "loop_segments": [
                _loop(8.0, confidence=0.64),
                _loop(40.0, confidence=0.80),
                _loop(80.0, confidence=0.50),
            ],
        }
        result = apply_precision_gate(analysis, bpm=120.0)
        starts = [item["start"] for item in result["loop_segments"]]
        self.assertEqual(starts, [8.0, 40.0])
        self.assertEqual(result["precision_gate"]["rejected"]["low_confidence_loops"], 1)

    def test_ensure_minimum_loops_fills_from_stem_scan(self):
        cuer = AutomaticMusicCuer.__new__(AutomaticMusicCuer)
        existing = [_loop(32.0, name="Body Loop")]
        discovered = [
            _loop(0.0, beats=8, name="Melodic Loop"),
            _loop(64.0, name="Drop Loop"),
        ]
        analysis = {"measure_changes": [], "loop_segments": list(existing)}
        with patch.object(
            cuer, "_discover_stem_validated_loops", return_value=discovered
        ):
            out = cuer._ensure_minimum_loops(
                analysis,
                profiles={"kick": object()},
                beat_duration=0.5,
                song_length=180.0,
                audio_file_path="/tmp/song.flac",
            )
        names = {loop["loop_name"] for loop in out["loop_segments"]}
        self.assertGreaterEqual(len(out["loop_segments"]), 2)
        self.assertIn("Body Loop", names)
        self.assertTrue(names & {"Melodic Loop", "Drop Loop"})

    def test_apply_stem_activity_keeps_two_when_scan_supplies_them(self):
        cuer = AutomaticMusicCuer.__new__(AutomaticMusicCuer)
        profiles = {
            "kick": StemProfile.from_frames([0.5] * 40, frame_seconds=0.25),
            "instruments": StemProfile.from_frames([0.5] * 40, frame_seconds=0.25),
            "vocal": StemProfile.from_frames([0.001] * 40, frame_seconds=0.25),
        }
        analysis = {
            "measure_changes": [],
            "loop_segments": [_loop(32.0, name="Only One")],
        }
        extra = [
            _loop(0.0, beats=8, name="Intro Loop"),
            _loop(64.0, name="Late Loop"),
        ]
        cuer._track_audio_cache = type(
            "Cache",
            (),
            {"get_or_load_stem_profiles": staticmethod(lambda files: profiles)},
        )()
        with patch(
            "vdj_cuer.stems.loop_is_stable", return_value=True
        ), patch(
            "vdj_cuer.stems.loop_seam_is_clean", return_value=True
        ), patch(
            "vdj_cuer.stems.measure_stem_evidence",
            return_value=type(
                "E",
                (),
                {
                    "activity": {"kick": "medium"},
                    "scores": {"kick": 0.5},
                    "elements": ["drums", "bass"],
                    "uncertain_elements": [],
                    "confidence": 0.7,
                },
            )(),
        ), patch.object(
            cuer, "_validate_loop_candidate", side_effect=lambda **kwargs: {
                "start": kwargs["start"],
                "length_beats": kwargs["length_beats"],
                "elements": ["drums", "bass"],
                "loop_name": kwargs["loop_name"],
                "color": "green",
                "role": "loop",
                "confidence": 0.85,
            }
        ), patch.object(
            cuer, "_discover_stem_validated_loops", return_value=extra
        ), patch.object(
            cuer, "_loop_discovery_song_length", return_value=120.0
        ), patch(
            "vdj_cuer.stems._stem_gate_confidence", return_value=0.85
        ):
            result = cuer._apply_measured_stem_activity(
                analysis,
                stem_files=[("kick", "/tmp/kick.m4a")],
                bpm=120.0,
            )
        self.assertGreaterEqual(len(result["loop_segments"]), TARGET_MIN_LOOPS)
        self.assertLessEqual(len(result["loop_segments"]), TARGET_MAX_LOOPS)


if __name__ == "__main__":
    unittest.main()
