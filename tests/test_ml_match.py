"""Compare on-screen VDJ POIs to an AutoCue proposal."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from vdj_cuer.ml.match import (
    assess_autocue_match,
    compare_cue_sets,
    save_written_autocue_points,
)


class CompareCueSetsTests(unittest.TestCase):
    def test_exact_match(self) -> None:
        out = compare_cue_sets(
            [8.0, 32.0, 64.0],
            [8.0, 32.0, 64.0],
            actual_loops=[16.0, 48.0],
            proposed_loops=[16.0, 48.0],
            bpm=120.0,
        )
        self.assertTrue(out["matches"])
        self.assertTrue(out["autocue_matches"])
        self.assertEqual(out["status"], "match")

    def test_near_match_within_one_bar(self) -> None:
        # 120 BPM → 1 bar = 2.0s. 0.4s is near, not exact.
        out = compare_cue_sets(
            [8.4, 32.3],
            [8.0, 32.0],
            actual_loops=[16.2],
            proposed_loops=[16.0],
            bpm=120.0,
        )
        self.assertTrue(out["matches"])
        self.assertEqual(out["status"], "near")
        self.assertIn("1 bar", out["reason"])

    def test_mismatch_outside_window(self) -> None:
        out = compare_cue_sets(
            [8.0, 40.0],
            [8.0, 32.0],
            actual_loops=[],
            proposed_loops=[],
            bpm=120.0,
        )
        self.assertFalse(out["matches"])
        self.assertEqual(out["status"], "mismatch")

    def test_mismatch_when_counts_differ(self) -> None:
        out = compare_cue_sets(
            [8.0, 32.0, 64.0],
            [8.0, 32.0],
            bpm=120.0,
        )
        self.assertFalse(out["matches"])
        self.assertEqual(out["status"], "mismatch")
        self.assertIn("Count differs", out["reason"])

    def test_accepts_vdj_point_dicts(self) -> None:
        out = compare_cue_sets(
            [{"kind": "cue", "pos": 8.0}],
            [{"timestamp": 8.02, "cue_name": "Intro"}],
            actual_loops=[{"kind": "loop", "pos": 16.0}],
            proposed_loops=[{"start": 16.05, "loop_name": "A"}],
            bpm=128.0,
        )
        self.assertTrue(out["matches"])
        self.assertEqual(out["status"], "match")


class AssessAutocueMatchTests(unittest.TestCase):
    def test_written_snapshot_is_a_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "song.flac"
            audio.write_bytes(b"audio-bytes")
            summary = SimpleNamespace(
                bpm=120.0,
                points=[
                    SimpleNamespace(kind="cue", pos=8.0),
                    SimpleNamespace(kind="cue", pos=32.0),
                    SimpleNamespace(kind="loop", pos=16.0),
                ],
            )
            cache = Path(tmp) / "cache"
            saved = save_written_autocue_points(audio, summary, cache_dir=cache)
            self.assertIsNotNone(saved)
            hit = assess_autocue_match(audio, summary, cache_dir=cache)
            self.assertTrue(hit["matches"])
            self.assertEqual(hit["status"], "match")

            edited = SimpleNamespace(
                bpm=120.0,
                points=[
                    SimpleNamespace(kind="cue", pos=8.0),
                    SimpleNamespace(kind="cue", pos=48.0),
                    SimpleNamespace(kind="loop", pos=16.0),
                ],
            )
            miss = assess_autocue_match(audio, edited, cache_dir=cache)
            self.assertFalse(miss["matches"])
            self.assertEqual(miss["status"], "mismatch")

    def test_no_proposal_is_not_a_match(self) -> None:
        summary = SimpleNamespace(
            bpm=120.0,
            points=[SimpleNamespace(kind="cue", pos=8.0)],
        )
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "missing.flac"
            audio.write_bytes(b"x")
            out = assess_autocue_match(audio, summary, cache_dir=Path(tmp) / "empty")
        self.assertFalse(out["matches"])
        self.assertEqual(out["status"], "no_proposal")


if __name__ == "__main__":
    unittest.main()
