"""Holdout metric helpers: documented recall / precision / F1 arithmetic."""

from __future__ import annotations

import unittest

from vdj_cuer.ml.ab import f1
from vdj_cuer.ml.eval_metrics import (
    bar_window_seconds,
    precision_of,
    recall_within,
    score_track,
)


class ScoreTrackArithmeticTests(unittest.TestCase):
    def test_score_track_matches_one_bar_window_math(self) -> None:
        bpm = 120.0
        window = bar_window_seconds(bpm, 1.0)
        self.assertAlmostEqual(window, 2.0)
        rows = [
            {"timestamp": 0.0, "is_cue": 1},
            {"timestamp": 2.0, "is_cue": 0},
            {"timestamp": 8.0, "is_cue": 1},
            {"timestamp": 16.0, "is_cue": 1},
        ]
        pred = [0.4, 8.1, 40.0]
        got = score_track(rows, pred, bpm=bpm)
        self.assertAlmostEqual(got["recall_1bar"], 2 / 3)
        self.assertAlmostEqual(got["precision_top"], 2 / 3)
        self.assertAlmostEqual(got["n_human"], 3.0)
        self.assertAlmostEqual(got["n_pred"], 3.0)
        self.assertAlmostEqual(
            f1(got["precision_top"], got["recall_1bar"]),
            f1(
                precision_of(pred, [0.0, 8.0, 16.0], window=window),
                recall_within([0.0, 8.0, 16.0], pred, window=window),
            ),
        )


if __name__ == "__main__":
    unittest.main()
