"""Stem cue planner: snap to the 1 and merge Gemini names onto stem times."""

import unittest

from vdj_cuer.common import is_on_downbeat, quantize_to_downbeat
from vdj_cuer.stem_cue_plan import merge_gemini_onto_stem_cues, snap_to_downbeat
from vdj_cuer.stem_evidence import StemProfile


class StemCuePlanTests(unittest.TestCase):
    def test_snap_and_on_one(self):
        bpm = 84.0
        phase = 22.846775
        poi = 23.562858
        self.assertFalse(is_on_downbeat(poi, bpm, phase))
        snapped = snap_to_downbeat(poi, bpm, phase)
        self.assertTrue(is_on_downbeat(snapped, bpm, phase))
        self.assertAlmostEqual(
            quantize_to_downbeat(poi, bpm, phase), snapped, places=5
        )

    def test_merge_keeps_stem_time(self):
        stem = [{"timestamp": 8.0, "cue_name": "Melody", "elements": ["synth"]}]
        gemini = [{"timestamp": 9.7, "cue_name": "Intro", "role": "intro"}]
        merged = merge_gemini_onto_stem_cues(stem, gemini, bpm=120.0)
        self.assertEqual(merged[0]["timestamp"], 8.0)
        self.assertEqual(merged[0]["cue_name"], "Intro")


if __name__ == "__main__":
    unittest.main()
