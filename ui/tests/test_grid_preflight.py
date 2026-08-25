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

    def test_stem_epipe_deep_verify_retries_mix_only(self):
        """Stem decode EPIPE must not leave can_autocue with a live stem map."""
        calls = []

        def fake_deep(audio_path, bpm, mix_only=False):
            calls.append(bool(mix_only))
            if not mix_only:
                return {"verified": False, "error": "[Errno 32] Broken pipe"}
            return {
                "verified": True,
                "offset": 0.12,
                "corrected": False,
                "shift_beats": 0,
                "fine_shift_seconds": 0.0,
                "confidence_ratio": 1.6,
                "source": "mix",
                "beat_score": 0.08,
                "best_beat_score": 0.08,
                "error": None,
                "stems_skipped": True,
            }

        with tempfile.NamedTemporaryFile(suffix=".m4a") as handle:
            path = Path(handle.name)
            path.write_bytes(b"x")
            cues = _cues(bpm=92.0, has_beatgrid=True, beatgrid_pos=0.12, scan_phase=0.12)
            with patch.object(gp, "_deep_verify_alignment", side_effect=fake_deep):
                result = gp.assess_grid_for_autocue(path, deep=True, cues=cues)

        self.assertEqual(calls, [False, True])
        self.assertTrue(result["can_autocue"])
        self.assertTrue(result["stems_skipped"])
        self.assertTrue(any("stem" in warning.lower() for warning in result["warnings"]))

    def test_stem_epipe_without_mix_success_does_not_keep_stem_map(self):
        def always_epipe(audio_path, bpm, mix_only=False):
            return {"verified": False, "error": "[Errno 32] Broken pipe"}

        with tempfile.NamedTemporaryFile(suffix=".m4a") as handle:
            path = Path(handle.name)
            path.write_bytes(b"x")
            cues = _cues(bpm=92.0, has_beatgrid=True, beatgrid_pos=0.12)
            with patch.object(gp, "_deep_verify_alignment", side_effect=always_epipe):
                result = gp.assess_grid_for_autocue(path, deep=True, cues=cues)

        # Structural grid is fine, but AutoCue must not keep the broken stem map.
        if result["can_autocue"]:
            self.assertTrue(result["stems_skipped"])
        self.assertTrue(result.get("stems_skipped"))


if __name__ == "__main__":
    unittest.main()

