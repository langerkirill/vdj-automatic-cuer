"""Beatgrid preflight classification (structural, no ffmpeg)."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sorter import grid_preflight as gp
from sorter.relocate import CueSummary


def _cues(**kwargs) -> CueSummary:
    base = dict(
        cue_count=0,
        loop_count=0,
        has_beatgrid=False,
        title="",
        author="",
        in_database=True,
        song_length=180.0,
        beatgrid_pos=None,
        scan_phase=None,
        bpm=120.0,
        points=[],
    )
    base.update(kwargs)
    return CueSummary(**base)


class GridPreflightTests(unittest.TestCase):
    def test_missing_from_db_blocks(self):
        result = gp.preflight_from_cues(_cues(in_database=False))
        self.assertFalse(result["can_autocue"])
        self.assertTrue(result["manual_required"])
        self.assertEqual(result["status"], "blocked")

    def test_no_bpm_blocks(self):
        result = gp.preflight_from_cues(_cues(bpm=None, has_beatgrid=True, beatgrid_pos=0.1))
        self.assertFalse(result["can_autocue"])
        self.assertEqual(result["status"], "blocked")

    def test_no_grid_anchor_blocks(self):
        result = gp.preflight_from_cues(_cues(bpm=128.0, has_beatgrid=False, scan_phase=None))
        self.assertFalse(result["can_autocue"])
        self.assertIn("beatgrid", result["issues"][0].lower())

    def test_phase_poi_mismatch_is_fixable(self):
        result = gp.preflight_from_cues(
            _cues(bpm=100.0, has_beatgrid=True, beatgrid_pos=0.0, scan_phase=0.5)
        )
        self.assertTrue(result["can_autocue"])
        self.assertTrue(result["needs_align"])
        self.assertEqual(result["status"], "fixable")

    def test_double_time_warns(self):
        result = gp.preflight_from_cues(
            _cues(bpm=140.0, has_beatgrid=True, beatgrid_pos=0.05)
        )
        self.assertTrue(result["can_autocue"])
        self.assertEqual(result["status"], "warn")
        self.assertTrue(any("double-time" in w.lower() for w in result["warnings"]))

    def test_slow_zouk_57_bpm_can_autocue(self):
        result = gp.preflight_from_cues(
            _cues(bpm=57.0, has_beatgrid=True, beatgrid_pos=0.1, scan_phase=0.1)
        )
        self.assertTrue(result["can_autocue"])
        self.assertNotEqual(result["status"], "blocked")
        self.assertNotEqual(result["label"], "No usable BPM")

    def test_ok_grid(self):
        result = gp.preflight_from_cues(
            _cues(bpm=92.0, has_beatgrid=True, beatgrid_pos=0.12, scan_phase=0.12)
        )
        self.assertTrue(result["can_autocue"])
        self.assertEqual(result["status"], "ok")

    def test_scan_phase_alone_is_enough_anchor(self):
        result = gp.preflight_from_cues(
            _cues(bpm=110.0, has_beatgrid=False, scan_phase=1.25)
        )
        self.assertTrue(result["can_autocue"])
        self.assertEqual(result["grid_anchor"], 1.25)

    def test_assess_missing_file(self):
        result = gp.assess_grid_for_autocue("/tmp/definitely-missing-music-sorter.flac")
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["can_autocue"])

    def test_assess_uses_cues_without_reloading(self):
        with tempfile.NamedTemporaryFile(suffix=".flac") as handle:
            path = Path(handle.name)
            path.write_bytes(b"x")
            cues = _cues(bpm=95.0, has_beatgrid=True, beatgrid_pos=0.2)
            with patch.object(gp, "summarize_cues") as mock_sum:
                result = gp.assess_grid_for_autocue(path, deep=False, cues=cues)
                mock_sum.assert_not_called()
            self.assertTrue(result["can_autocue"])

    def test_deep_corrected_blocks_autocue_until_confirm(self):
        with tempfile.NamedTemporaryFile(suffix=".flac") as handle:
            path = Path(handle.name)
            path.write_bytes(b"x")
            cues = _cues(bpm=95.0, has_beatgrid=True, beatgrid_pos=0.2, scan_phase=0.2)
            fake = {
                "verified": True,
                "corrected": True,
                "confidence_ratio": 2.0,
                "shift_beats": 2,
                "fine_shift_seconds": 0.05,
                "best_beat_score": 0.4,
                "offset": 0.6,
            }
            with patch.object(gp, "_deep_verify_alignment", return_value=fake):
                result = gp.assess_grid_for_autocue(path, deep=True, cues=cues)
        self.assertFalse(result["can_autocue"])
        self.assertTrue(result["needs_align"])
        self.assertTrue(result["manual_confirmable"])
        self.assertEqual(result["status"], "fixable")



if __name__ == "__main__":
    unittest.main()
