"""Stem cue planner: snap to the 1 and merge Gemini names onto stem times."""

import unittest

from vdj_cuer.common import is_on_downbeat, is_on_phrase_one, quantize_to_phrase_one
from vdj_cuer.stem_cue_plan import (
    merge_gemini_onto_stem_cues,
    plan_stem_cues,
    snap_to_downbeat,
)
from vdj_cuer.stem_evidence import StemProfile


class StemCuePlanTests(unittest.TestCase):
    def test_snap_and_on_one(self):
        bpm = 84.0
        phase = 22.846775
        poi = 23.562858
        self.assertFalse(is_on_downbeat(poi, bpm, phase))
        snapped = snap_to_downbeat(poi, bpm, phase)
        self.assertTrue(is_on_downbeat(snapped, bpm, phase))
        self.assertTrue(is_on_phrase_one(snapped, bpm, phase))
        self.assertAlmostEqual(
            quantize_to_phrase_one(poi, bpm, phase), snapped, places=5
        )

    def test_merge_keeps_stem_time(self):
        stem = [{"timestamp": 8.0, "cue_name": "Melody", "elements": ["synth"]}]
        gemini = [{"timestamp": 9.7, "cue_name": "Intro", "role": "intro"}]
        merged = merge_gemini_onto_stem_cues(stem, gemini, bpm=120.0)
        self.assertEqual(merged[0]["timestamp"], 8.0)
        self.assertEqual(merged[0]["cue_name"], "Intro")

    def test_plan_includes_first_bar_intro_when_stems_are_already_on(self) -> None:
        """prev_sig is None on bar 0, so 'changed' never fired and intros vanished."""
        n = 80
        kick = [0.8] * n
        vocal = [0.05] * 16 + [0.7] * (n - 16)
        instruments = [0.6] * n
        profiles = {
            "kick": StemProfile.from_frames(kick, frame_seconds=0.25),
            "vocal": StemProfile.from_frames(vocal, frame_seconds=0.25),
            "instruments": StemProfile.from_frames(instruments, frame_seconds=0.25),
            "bass": StemProfile.from_frames([0.5] * n, frame_seconds=0.25),
        }
        planned = plan_stem_cues(
            profiles, bpm=120.0, offset=0.0, duration=20.0, max_cues=6
        )
        self.assertGreaterEqual(len(planned), 1)
        times = [float(row["timestamp"]) for row in planned]
        self.assertLessEqual(min(times), 0.05)
        for row in planned:
            self.assertTrue(
                is_on_phrase_one(float(row["timestamp"]), 120.0, 0.0),
                row["timestamp"],
            )


if __name__ == "__main__":
    unittest.main()

