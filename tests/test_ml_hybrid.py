"""Hybrid AutoCue: stem-plan times plus ML times, then existing gates still apply."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from vdj_cuer.ml.propose import blend_cue_plans, ml_cues_enabled


class BlendCuePlansTests(unittest.TestCase):
    def test_keeps_ml_and_fills_stem_extras(self) -> None:
        ml = [
            {"timestamp": 8.0, "cue_name": "ML A", "assertion_source": "ml_cue_plan"},
            {"timestamp": 24.0, "cue_name": "ML B", "assertion_source": "ml_cue_plan"},
        ]
        stem = [
            {"timestamp": 8.2, "cue_name": "Stem near A", "assertion_source": "stem_cue_plan"},
            {"timestamp": 40.0, "cue_name": "Stem extra", "assertion_source": "stem_cue_plan"},
        ]
        out = blend_cue_plans(ml, stem, bpm=120.0, max_cues=6)
        times = [round(float(c["timestamp"]), 1) for c in out]
        self.assertIn(8.0, times)
        self.assertIn(24.0, times)
        self.assertIn(40.0, times)
        near = next(c for c in out if abs(float(c["timestamp"]) - 8.0) < 0.05)
        self.assertEqual(near["cue_name"], "ML A")

    def test_stem_only_when_ml_empty(self) -> None:
        stem = [{"timestamp": 4.0, "cue_name": "Intro", "assertion_source": "stem_cue_plan"}]
        out = blend_cue_plans([], stem, bpm=120.0)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["cue_name"], "Intro")

    def test_ml_only_when_stem_empty(self) -> None:
        ml = [{"timestamp": 4.0, "cue_name": "ML", "assertion_source": "ml_cue_plan"}]
        out = blend_cue_plans(ml, [], bpm=120.0)
        self.assertEqual(out[0]["cue_name"], "ML")


class MlEnabledTests(unittest.TestCase):
    def test_can_disable_with_env(self) -> None:
        with patch.dict(os.environ, {"AUTOCUE_DISABLE_ML": "1"}):
            self.assertFalse(ml_cues_enabled())
        with patch.dict(os.environ, {"AUTOCUE_DISABLE_ML": ""}):
            self.assertTrue(ml_cues_enabled())


if __name__ == "__main__":
    unittest.main()
