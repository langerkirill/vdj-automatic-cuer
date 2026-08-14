"""Train and load the bar-1 HistGradientBoosting cue / loop heads."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score

from .features import FEATURE_NAMES, feature_matrix

SCHEMA = 1
DEFAULT_ARTIFACT = (
    Path(__file__).resolve().parent / "artifacts" / "cue_bar_clf.joblib"
)


@dataclass(frozen=True)
class TrackSplit:
    train: list[str]
    val: list[str]
    test: list[str]


@dataclass
class CueBarModel:
    cue_clf: HistGradientBoostingClassifier
    loop_clf: HistGradientBoostingClassifier
    feature_names: tuple[str, ...] = FEATURE_NAMES
    schema: int = SCHEMA

    def predict_cue_proba(self, X: np.ndarray) -> np.ndarray:
        return _positive_proba(self.cue_clf, X)

    def predict_loop_proba(self, X: np.ndarray) -> np.ndarray:
        return _positive_proba(self.loop_clf, X)


def _positive_proba(clf: HistGradientBoostingClassifier, X: np.ndarray) -> np.ndarray:
    proba = clf.predict_proba(X)
    classes = list(clf.classes_)
    if 1 in classes:
        return proba[:, classes.index(1)]
    return np.zeros(len(X), dtype=float)


def matrix_from_rows(
    rows: Sequence[dict[str, Any]],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    X = np.asarray(feature_matrix(rows), dtype=float)
    y_cue = np.asarray([int(row.get("is_cue") or 0) for row in rows], dtype=int)
    y_loop = np.asarray([int(row.get("is_loop_start") or 0) for row in rows], dtype=int)
    return X, y_cue, y_loop


def split_track_ids(
    track_ids: Iterable[str],
    *,
    seed: int = 0,
    train: float = 0.8,
    val: float = 0.1,
) -> TrackSplit:
    ids = sorted({str(tid) for tid in track_ids if tid})
    rng = np.random.default_rng(seed)
    order = np.array(ids, dtype=object)
    rng.shuffle(order)
    n = len(order)
    n_train = max(1, int(round(n * train))) if n else 0
    n_val = int(round(n * val)) if n > 2 else 0
    if n_train + n_val >= n and n:
        n_train = max(1, n - max(1, n_val) - (1 if n > n_val + 1 else 0))
    train_ids = [str(x) for x in order[:n_train]]
    val_ids = [str(x) for x in order[n_train : n_train + n_val]]
    test_ids = [str(x) for x in order[n_train + n_val :]]
    if n and not test_ids and val_ids:
        test_ids = [val_ids.pop()]
    return TrackSplit(train=train_ids, val=val_ids, test=test_ids)


def _fit_head(X: np.ndarray, y: np.ndarray, *, seed: int) -> HistGradientBoostingClassifier:
    clf = HistGradientBoostingClassifier(
        max_depth=4,
        max_iter=80,
        learning_rate=0.08,
        min_samples_leaf=8,
        l2_regularization=0.1,
        random_state=seed,
        class_weight="balanced",
    )
    # Tiny synthetic sets may have a class with < min_samples_leaf.
    if len(y) < 40:
        clf.set_params(min_samples_leaf=2, max_iter=40, max_depth=3)
    clf.fit(X, y)
    return clf


def train_cue_bar_model(rows: Sequence[dict[str, Any]], *, seed: int = 0) -> CueBarModel:
    X, y_cue, y_loop = matrix_from_rows(rows)
    if X.size == 0:
        raise ValueError("No training rows")
    cue_clf = _fit_head(X, y_cue, seed=seed)
    # Loop head: if no positives, still fit a dummy on zeros+one flip-safe path.
    if int(y_loop.sum()) == 0:
        loop_clf = _fit_head(X, y_cue, seed=seed + 1)
    else:
        loop_clf = _fit_head(X, y_loop, seed=seed + 1)
    return CueBarModel(cue_clf=cue_clf, loop_clf=loop_clf)


def save_cue_bar_model(
    model: CueBarModel,
    path: Path | None = None,
    *,
    metrics: Optional[dict[str, Any]] = None,
) -> Path:
    import joblib

    dest = Path(path) if path else DEFAULT_ARTIFACT
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": SCHEMA,
        "feature_names": list(model.feature_names),
        "cue_clf": model.cue_clf,
        "loop_clf": model.loop_clf,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "metrics": metrics or {},
    }
    joblib.dump(payload, dest)
    sidecar = dest.with_suffix(".json")
    sidecar.write_text(
        json.dumps(
            {
                "schema": SCHEMA,
                "feature_names": list(model.feature_names),
                "saved_at": payload["saved_at"],
                "metrics": metrics or {},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return dest


def load_cue_bar_model(path: Path | None = None) -> Optional[CueBarModel]:
    import joblib

    dest = Path(path) if path else DEFAULT_ARTIFACT
    if not dest.is_file():
        return None
    payload = joblib.load(dest)
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        return None
    names = tuple(payload.get("feature_names") or FEATURE_NAMES)
    if names != FEATURE_NAMES:
        return None
    return CueBarModel(
        cue_clf=payload["cue_clf"],
        loop_clf=payload["loop_clf"],
        feature_names=names,
    )


def auc_or_none(y_true: np.ndarray, y_score: np.ndarray) -> Optional[float]:
    if len(set(int(v) for v in y_true.tolist())) < 2:
        return None
    return float(roc_auc_score(y_true, y_score))
