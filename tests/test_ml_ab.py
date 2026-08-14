"""A/B helpers: union / intersect / blend of AutoCue stem-plan vs ML."""

from __future__ import annotations

import unittest

from vdj_cuer.ml.ab import combine_times, f1
from vdj_cuer.ml.eval_metrics import precision_of, recall_within


class CombineTimesTests(unittest.TestCase):
    def test_union_keeps_both_and_dedupes_near_hits(self) -> None:
        out = combine_times(
            [0.0, 8.0],
            [0.2, 16.0],
            window=1.0,
            how="union",
        )
        self.assertEqual(out, [0.0, 8.0, 16.0])

    def test_intersect_keeps_only_agreement(self) -> None:
        out = combine_times(
            [0.0, 8.0, 24.0],
            [0.3, 16.0],
            window=1.0,
            how="intersect",
        )
        self.assertEqual(out, [0.0])

    def test_blend_prefers_ml_order_then_fills_from_stem(self) -> None:
        out = combine_times(
            ml_times=[10.0, 20.0, 30.0, 40.0, 50.0],
            stem_times=[4.0, 20.2],
            window=1.0,
            how="blend",
            max_cues=6,
            min_gap=6.0,
        )
        self.assertIn(10.0, out)
        self.assertIn(20.0, out)
        self.assertIn(4.0, out)
        self.assertLessEqual(len(out), 6)


class F1Tests(unittest.TestCase):
    def test_f1_balances_precision_and_recall(self) -> None:
        human = [0.0, 8.0, 16.0]
        pred = [0.0, 8.0, 40.0]
        rec = recall_within(human, pred, window=0.5)
        prec = precision_of(pred, human, window=0.5)
        self.assertAlmostEqual(rec, 2 / 3)
        self.assertAlmostEqual(prec, 2 / 3)
        self.assertAlmostEqual(f1(prec, rec), 2 / 3)


if __name__ == "__main__":
    unittest.main()
