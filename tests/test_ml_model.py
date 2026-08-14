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


if __name__ == "__main__":
    unittest.main()
