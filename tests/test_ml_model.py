"""Tiny synthetic booster must separate a one-feature cue signal."""

from __future__ import annotations

import unittest

from vdj_cuer.ml.features import FEATURE_NAMES
from vdj_cuer.ml.model import (
    CueBarModel,
    matrix_from_rows,
    split_track_ids,
    train_cue_bar_model,
)


def _row(track: str, vocal_dprev: float, is_cue: int) -> dict:
    base = {name: 0.0 for name in FEATURE_NAMES}
    base["vocal_dprev"] = vocal_dprev
    base["track_id"] = track
    base["is_cue"] = is_cue
    base["is_loop_start"] = 0
    return base


class SplitTrackIdsTests(unittest.TestCase):
    def test_no_track_leaks_across_splits(self) -> None:
        ids = [f"t{i}" for i in range(20)]
        split = split_track_ids(ids, seed=1)
        train, val, test = set(split.train), set(split.val), set(split.test)
        self.assertEqual(train & val, set())
        self.assertEqual(train & test, set())
        self.assertEqual(val & test, set())
        self.assertEqual(train | val | test, set(ids))


class TrainCueBarModelTests(unittest.TestCase):
    def test_learns_vocal_delta_rule(self) -> None:
        rows = []
        for i in range(12):
            tid = f"song-{i}"
            rows.append(_row(tid, 0.05, 0))
            rows.append(_row(tid, 0.06, 0))
            rows.append(_row(tid, 0.80, 1))
            rows.append(_row(tid, 0.04, 0))
        model = train_cue_bar_model(rows, seed=0)
        self.assertIsInstance(model, CueBarModel)
        X, _y_cue, _y_loop = matrix_from_rows(rows)
        proba = model.predict_cue_proba(X)
        pos = [p for p, r in zip(proba, rows) if r["is_cue"] == 1]
        neg = [p for p, r in zip(proba, rows) if r["is_cue"] == 0]
        self.assertGreater(min(pos), max(neg) - 0.05)
        self.assertGreater(sum(pos) / len(pos), sum(neg) / len(neg))
        self.assertTrue(model.cue_rerank is None or hasattr(model.cue_rerank, "predict_proba"))
        self.assertIsNotNone(model.miss_clf)
        self.assertTrue(hasattr(model.miss_clf, "predict_proba"))

    def test_train_fits_regularized_rerank_on_hard_negatives(self) -> None:
        import inspect

        from vdj_cuer.ml.model import train_cue_bar_model

        source = inspect.getsource(train_cue_bar_model)
        self.assertIn("_hard_negative_indices", source)
        self.assertIn("cue_rerank", source)
        self.assertIn("_fit_head", source)
        self.assertIn("offset_clf", source)
        self.assertIn("seq_clf", source)
        self.assertIn("column_stack", source)
        self.assertIn("tree_clf", source)
        self.assertIn("fuse_hgb_tree_scores", source)
        self.assertIn("miss_clf", source)
        self.assertIn("fuse_residual_misses", source)

    def test_fuse_lifts_tree_top_misses_only(self) -> None:
        import numpy as np

        from vdj_cuer.ml.model import fuse_hgb_tree_scores

        hgb = np.array([0.90, 0.80, 0.70, 0.60, 0.55, 0.54, 0.20, 0.10])
        tree = np.array([0.10, 0.12, 0.11, 0.09, 0.08, 0.07, 0.95, 0.05])
        fused = fuse_hgb_tree_scores(hgb, tree)
        self.assertGreaterEqual(fused[6], 0.50)
        self.assertAlmostEqual(fused[0], 0.90)
        self.assertAlmostEqual(fused[5], 0.54)

    def test_fuse_residual_lifts_only_buried_miss_top(self) -> None:
        """Still-loud / rank-13+ bars sit at HGB rank 8+; residual may pick them."""
        import numpy as np

        from vdj_cuer.ml.model import fuse_residual_misses

        hgb = np.array([0.92, 0.88, 0.80, 0.70, 0.62, 0.58, 0.40, 0.22, 0.18, 0.12])
        miss = np.array([0.10, 0.11, 0.09, 0.08, 0.07, 0.06, 0.05, 0.04, 0.91, 0.03])
        fused = fuse_residual_misses(hgb, miss, rank_from=hgb)
        self.assertGreaterEqual(fused[8], 0.55)
        self.assertAlmostEqual(fused[0], 0.92)
        self.assertAlmostEqual(fused[6], 0.40)
        self.assertAlmostEqual(fused[9], 0.12)

    def test_fuse_residual_skips_low_confidence_buried_bar(self) -> None:
        import numpy as np

        from vdj_cuer.ml.model import fuse_residual_misses

        hgb = np.array([0.92, 0.88, 0.80, 0.70, 0.62, 0.58, 0.40, 0.22, 0.18, 0.12])
        miss = np.array([0.10, 0.11, 0.09, 0.08, 0.07, 0.06, 0.05, 0.04, 0.03, 0.02])
        fused = fuse_residual_misses(hgb, miss, rank_from=hgb)
        self.assertAlmostEqual(fused[8], 0.18)
        self.assertAlmostEqual(fused[9], 0.12)

    def test_predict_uses_wide_seq_residual(self) -> None:
        import inspect

        from vdj_cuer.ml.model import CueBarModel

        source = inspect.getsource(CueBarModel.predict_cue_proba)
        self.assertIn("seq_clf", source)
        self.assertIn("0.45", source)
        self.assertIn("0.55", source)
        self.assertIn("column_stack", source)
        self.assertIn("miss_clf", source)
        self.assertIn("fuse_residual_misses", source)

    def test_sequence_view_is_narrow_not_full_matrix(self) -> None:
        import numpy as np

        from vdj_cuer.ml.features import MODEL_FEATURE_NAMES, SEQUENCE_MODEL_FEATURES
        from vdj_cuer.ml.model import _sequence_view

        wide = np.ones((5, len(MODEL_FEATURE_NAMES)))
        unary = np.linspace(0.1, 0.5, 5)
        view = _sequence_view(wide, unary)
        self.assertGreater(view.shape[1], 5)
        self.assertLess(view.shape[1], 20)
        from vdj_cuer.ml.model import _fit_seq_head

        source = __import__("inspect").getsource(_fit_seq_head)
        self.assertIn("l2_regularization", source)
        self.assertIn("max_depth", source)

    def test_offset_head_lifts_held_kick_drops_over_onset_false_peaks(self) -> None:
        rows = []
        for i in range(24):
            tid = f"drop-{i}"
            rows.append(_row(tid, 0.70, 0))
            rows.append(
                {
                    **_row(tid, 0.04, 1),
                    "kick_dprev": -0.45,
                    "kick_dnext": -0.02,
                    "kick_energy": 0.08,
                    "kick_prev": 0.53,
                    "kick_next": 0.06,
                    "mix_dprev": -0.04,
                    "mix_dnext": -0.01,
                }
            )
            rows.append(_row(tid, 0.08, 0))
            rows.append(
                {
                    **_row(tid, 0.10, 0),
                    "kick_dprev": 0.40,
                    "kick_energy": 0.6,
                    "mix_dprev": 0.30,
                }
            )
        model = train_cue_bar_model(rows, seed=1)
        self.assertIsNotNone(model.offset_clf)
        X, y_cue, _y_loop = matrix_from_rows(rows)
        proba = model.predict_cue_proba(X)
        pos = [p for p, label in zip(proba, y_cue) if label == 1]
        neg = [p for p, label in zip(proba, y_cue) if label == 0]
        self.assertGreater(sum(pos) / len(pos), sum(neg) / len(neg))


if __name__ == "__main__":
    unittest.main()
