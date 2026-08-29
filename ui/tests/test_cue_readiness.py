"""Add Cues readiness lives in sorter.cue_readiness — not relocate I/O."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from sorter import cue_readiness
from sorter import relocate as relocate_mod


class CueReadinessHomeTests(unittest.TestCase):
    def test_ready_needs_grid_two_cues_two_loops(self) -> None:
        ready = SimpleNamespace(
            in_database=True, has_beatgrid=True, cue_count=3, loop_count=2
        )
        out = cue_readiness.assess_cue_readiness(ready)
        self.assertTrue(out["ready"])
        self.assertEqual(out["status"], "ready")

    def test_partial_when_loops_short(self) -> None:
        cues = SimpleNamespace(
            in_database=True, has_beatgrid=True, cue_count=3, loop_count=1
        )
        out = cue_readiness.assess_cue_readiness(cues)
        self.assertFalse(out["ready"])
        self.assertEqual(out["status"], "partial")
        self.assertIn("2 loops", out["label"].lower())

    def test_missing_and_not_cued(self) -> None:
        missing = SimpleNamespace(
            in_database=False, has_beatgrid=False, cue_count=0, loop_count=0
        )
        self.assertEqual(cue_readiness.assess_cue_readiness(missing)["status"], "missing")
        empty = SimpleNamespace(
            in_database=True, has_beatgrid=True, cue_count=0, loop_count=0
        )
        self.assertEqual(cue_readiness.assess_cue_readiness(empty)["status"], "not_cued")

    def test_relocate_reexports_the_same_function(self) -> None:
        self.assertIs(relocate_mod.assess_cue_readiness, cue_readiness.assess_cue_readiness)
        self.assertIs(relocate_mod.vdj_bpm_to_actual, cue_readiness.vdj_bpm_to_actual)

    def test_vdj_bpm_half_second_is_120(self) -> None:
        self.assertAlmostEqual(cue_readiness.vdj_bpm_to_actual(0.5) or 0, 120.0)
        self.assertAlmostEqual(cue_readiness.vdj_bpm_to_actual(128) or 0, 128.0)


if __name__ == "__main__":
    unittest.main()
