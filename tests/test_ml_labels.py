"""Snap VDJ points onto bar-1s. Train on cued pipeline folders only."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from vdj_cuer.ml.labels import (
    has_training_cue_points,
    is_trainable_track,
    is_training_source_path,
    label_bars,
)


class TrainingPathTests(unittest.TestCase):
    def test_cues_sorted_and_ready_are_allowed(self) -> None:
        self.assertTrue(
            is_training_source_path(
                "/Users/x/Music/DJ/Music/Cues/Cues Sorted/Pop/song.flac"
            )
        )
        self.assertTrue(
            is_training_source_path(
                "/Users/x/Music/DJ/Music/Cues/Ready For Sort/01 - Amaria - Moon.flac"
            )
        )

    def test_add_cues_path_is_eligible_libraries_are_not(self) -> None:
        self.assertTrue(
            is_training_source_path(
                "/Users/x/Music/DJ/Music/Cues/Add Cues/Pajamathon/track.m4a"
            )
        )
        self.assertFalse(
            is_training_source_path("/Users/x/Music/DJ/Music/Zouk/Pop/track.flac")
        )
        self.assertFalse(
            is_training_source_path("/Users/x/Music/DJ/Music/House/Energy/track.flac")
        )
        self.assertFalse(is_training_source_path("/Users/x/Music/Mixes/practice.wav"))

    def test_skip_folders_are_excluded(self) -> None:
        self.assertFalse(
            is_training_source_path(
                "/Users/x/Music/DJ/Music/Cues/No Cues Found/miss.flac"
            )
        )
        self.assertFalse(
            is_training_source_path(
                "/Users/x/Music/DJ/Music/Cues/AC Low Quality/bad.flac"
            )
        )
        self.assertFalse(
            is_training_source_path(
                "/Users/x/Music/DJ/Music/Cues/Low Quality Skip/skip.flac"
            )
        )


class AcceptedCuePointTests(unittest.TestCase):
    def test_ready_summary_is_trainable(self) -> None:
        summary = SimpleNamespace(
            in_database=True,
            bpm=120.0,
            cue_count=3,
            loop_count=2,
            points=[{"kind": "cue", "pos": 8.0}],
        )
        path = "/Users/x/Music/DJ/Music/Cues/Ready For Sort/song.flac"
        self.assertTrue(has_training_cue_points(summary))
        self.assertTrue(is_trainable_track(path, summary))

    def test_cued_add_cues_is_trainable_uncued_is_not(self) -> None:
        path = "/Users/x/Music/DJ/Music/Cues/Add Cues/Pajamathon/track.m4a"
        cued = SimpleNamespace(
            in_database=True,
            bpm=124.0,
            cue_count=4,
            loop_count=0,
            points=[{"kind": "cue", "pos": 16.0}],
        )
        empty = SimpleNamespace(
            in_database=True,
            bpm=124.0,
            cue_count=0,
            loop_count=0,
            points=[],
        )
        self.assertTrue(is_trainable_track(path, cued))
        self.assertFalse(is_trainable_track(path, empty))
        self.assertFalse(has_training_cue_points(empty))

    def test_missing_bpm_or_database_is_rejected(self) -> None:
        path = "/Users/x/Music/DJ/Music/Cues/Cues Sorted/Pop/song.flac"
        no_bpm = SimpleNamespace(
            in_database=True, bpm=None, cue_count=2, loop_count=1, points=[{}]
        )
        missing = SimpleNamespace(
            in_database=False, bpm=120.0, cue_count=2, loop_count=1, points=[{}]
        )
        self.assertFalse(has_training_cue_points(no_bpm))
        self.assertFalse(is_trainable_track(path, missing))

    def test_loops_only_success_is_accepted(self) -> None:
        summary = SimpleNamespace(
            in_database=True,
            bpm=118.0,
            cue_count=0,
            loop_count=2,
            points=[{"kind": "loop", "pos": 32.0}],
        )
        self.assertTrue(has_training_cue_points(summary))


class LabelBarsTests(unittest.TestCase):
    def test_cue_snaps_to_its_bar_only(self) -> None:
        # 80 BPM, phase 0.47 — Skinny Remix style. Cue at 21.47 is a bar-1.
        bpm = 80.0
        offset = 0.47
        rows = label_bars(
            points=[
                {"kind": "cue", "pos": 21.47},
                {"kind": "loop", "pos": 0.47},
            ],
            duration=40.0,
            bpm=bpm,
            offset=offset,
        )
        cue_rows = [r for r in rows if r["is_cue"] == 1]
        loop_rows = [r for r in rows if r["is_loop_start"] == 1]
        self.assertEqual(len(cue_rows), 1)
        self.assertAlmostEqual(cue_rows[0]["timestamp"], 21.47, places=2)
        self.assertEqual(len(loop_rows), 1)
        self.assertAlmostEqual(loop_rows[0]["timestamp"], 0.47, places=2)
        neighbor = next(r for r in rows if abs(r["timestamp"] - 24.47) < 0.05)
        self.assertEqual(neighbor["is_cue"], 0)


if __name__ == "__main__":
    unittest.main()
