"""Snap VDJ points onto bar-1s. Never train on Add Cues."""

from __future__ import annotations

import unittest

from vdj_cuer.ml.labels import (
    is_training_source_path,
    label_bars,
)


class TrainingPathTests(unittest.TestCase):
    def test_cues_sorted_is_allowed_ready_is_not(self) -> None:
        self.assertTrue(
            is_training_source_path(
                "/Users/x/Music/DJ/Music/Cues/Cues Sorted/Pop/song.flac"
            )
        )
        self.assertFalse(
            is_training_source_path(
                "/Users/x/Music/DJ/Music/Cues/Ready For Sort/01 - Amaria - Moon.flac"
            )
        )

    def test_add_cues_is_excluded(self) -> None:
        self.assertFalse(
            is_training_source_path(
                "/Users/x/Music/DJ/Music/Cues/Add Cues/Pajamathon/track.m4a"
            )
        )
        self.assertFalse(
            is_training_source_path("/Users/x/Music/DJ/Music/Zouk/Pop/track.flac")
        )


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
