"""Train and load the bar-1 HistGradientBoosting cue / loop heads."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

import numpy as np
from sklearn.ensemble import ExtraTreesClassifier, HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score

from .features import (
    MODEL_FEATURE_NAMES,
    SEQUENCE_MODEL_FEATURES,
    apply_derived_features,
    apply_track_relative_by_track,
    feature_matrix,
)

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
    feature_names: tuple[str, ...] = MODEL_FEATURE_NAMES
    schema: int = SCHEMA
    cue_rerank: HistGradientBoostingClassifier | None = None
    offset_clf: HistGradientBoostingClassifier | None = None
    seq_clf: HistGradientBoostingClassifier | None = None
    mid_clf: HistGradientBoostingClassifier | None = None
    outro_clf: HistGradientBoostingClassifier | None = None
    tree_clf: ExtraTreesClassifier | None = None
    tree_fill: np.ndarray | None = None
    miss_clf: HistGradientBoostingClassifier | None = None

    def predict_unary(self, X: np.ndarray) -> np.ndarray:
        stage1 = _positive_proba(self.cue_clf, X)
        if self.cue_rerank is None:
            return stage1
        stage2 = _positive_proba(self.cue_rerank, X)
        return 0.35 * stage1 + 0.65 * stage2

    def predict_cue_proba(self, X: np.ndarray) -> np.ndarray:
        unary = self.predict_unary(X)
        # 0.593-class wide seq residual — mid/outro specialists collapsed AUC.
        if self.seq_clf is not None:
            seq = _positive_proba(self.seq_clf, np.column_stack([X, unary]))
            combined = 0.45 * unary + 0.55 * seq
        else:
            combined = unary
        # Residual miss-ranker (new family): stack on [X, unary] with
        # buried-cue weights, then lift its top buried bar. ExtraTrees
        # 0.15 blend cut F1 and is not applied here.
        if self.miss_clf is not None:
            miss = _positive_proba(self.miss_clf, np.column_stack([X, unary]))
            stacked = 0.60 * combined + 0.40 * miss
            combined = fuse_residual_misses(stacked, miss, rank_from=combined)
        if self.offset_clf is None:
            return combined
        offset = _positive_proba(self.offset_clf, X)
        mask = _offset_feature_mask(X)
        lifted = 0.40 * combined + 0.60 * offset
        return np.where(mask, np.maximum(combined, lifted), combined)

    def predict_loop_proba(self, X: np.ndarray) -> np.ndarray:
        return _positive_proba(self.loop_clf, X)


def _positive_proba(clf: HistGradientBoostingClassifier, X: np.ndarray) -> np.ndarray:
    proba = clf.predict_proba(X)
    classes = list(clf.classes_)
    if 1 in classes:
        return proba[:, classes.index(1)]
    return np.zeros(len(X), dtype=float)


def _tree_positive_proba(clf: ExtraTreesClassifier, X: np.ndarray) -> np.ndarray:
    proba = clf.predict_proba(X)
    classes = list(clf.classes_)
    if 1 in classes:
        return proba[:, classes.index(1)]
    return np.zeros(len(X), dtype=float)


def _impute_finite(X: np.ndarray, fill: np.ndarray) -> np.ndarray:
    data = np.asarray(X, dtype=float).copy()
    if data.ndim != 2 or data.size == 0:
        return data
    width = min(data.shape[1], len(fill))
    for column in range(width):
        missing = ~np.isfinite(data[:, column])
        if np.any(missing):
            data[missing, column] = float(fill[column])
    return data


def fuse_residual_misses(
    base: np.ndarray,
    miss: np.ndarray,
    *,
    top_k: int = 1,
    floor: float = 0.55,
    min_rank: int = 8,
    min_miss: float = 0.50,
    rank_from: np.ndarray | None = None,
) -> np.ndarray:
    """Lift a confident residual top buried bar into the after-core 2–6 map."""
    scores = np.asarray(base, dtype=float).reshape(-1)
    residual = np.asarray(miss, dtype=float).reshape(-1)
    if scores.size == 0 or residual.size != scores.size:
        return scores
    ranking = np.asarray(
        rank_from if rank_from is not None else scores, dtype=float
    ).reshape(-1)
    if ranking.size != scores.size:
        ranking = scores
    ranks = np.empty(len(ranking), dtype=int)
    ranks[np.argsort(-ranking)] = np.arange(len(ranking))
    buried = np.flatnonzero(
        (ranks >= int(min_rank)) & (residual >= float(min_miss))
    )
    if buried.size == 0:
        return scores.copy()
    order = buried[np.argsort(-residual[buried])]
    out = scores.copy()
    keep = min(int(top_k), len(order))
    for index in order[:keep]:
        out[index] = max(out[index], float(floor))
    return out


def fuse_hgb_tree_scores(
    hgb: np.ndarray,
    tree: np.ndarray,
    *,
    top_k: int = 6,
    floor: float = 0.50,
) -> np.ndarray:
    """Lift bars ExtraTrees ranks in its top-k that HGB left out of its top-6."""
    hgb_scores = np.asarray(hgb, dtype=float).reshape(-1)
    tree_scores = np.asarray(tree, dtype=float).reshape(-1)
    if hgb_scores.size == 0:
        return hgb_scores
    if tree_scores.size != hgb_scores.size:
        return hgb_scores
    hgb_rank = np.empty(len(hgb_scores), dtype=int)
    hgb_rank[np.argsort(-hgb_scores)] = np.arange(len(hgb_scores))
    tree_rank = np.empty(len(tree_scores), dtype=int)
    tree_rank[np.argsort(-tree_scores)] = np.arange(len(tree_scores))
    out = hgb_scores.copy()
    keep = min(int(top_k), len(out))
    lift = tree_rank < keep
    missed = hgb_rank >= 6
    out[lift & missed] = np.maximum(out[lift & missed], float(floor))
    return out


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


def _fit_head(
    X: np.ndarray,
    y: np.ndarray,
    *,
    seed: int,
    sample_weight: np.ndarray | None = None,
    class_weight: str | None = "auto",
) -> HistGradientBoostingClassifier:
    if class_weight == "auto":
        weight_mode: str | None = None if sample_weight is not None else "balanced"
    else:
        weight_mode = class_weight
    clf = HistGradientBoostingClassifier(
        max_depth=6,
        max_iter=220,
        learning_rate=0.06,
        min_samples_leaf=12,
        l2_regularization=0.05,
        random_state=seed,
        class_weight=weight_mode,
    )
    train_x = np.asarray(X, dtype=float)
    # Tiny / wide synthetic sets: all-NaN extra columns break HGB binning.
    if len(y) < 80 or train_x.shape[1] > max(8, len(y) // 2):
        train_x = np.nan_to_num(train_x, nan=0.0)
        clf.set_params(min_samples_leaf=2, max_iter=40, max_depth=3, max_bins=8)
    clf.fit(train_x, y, sample_weight=sample_weight)
    return clf


def _hard_negative_indices(
    rows: Sequence[dict[str, Any]],
    proba: np.ndarray,
    y_cue: np.ndarray,
    *,
    top_k: int = 20,
) -> np.ndarray:
    """Per-track top-k + every true cue — the bars propose_cues actually ranks."""
    from collections import defaultdict

    by_track: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        by_track[str(row.get("track_id") or index)].append(index)
    keep: set[int] = set()
    for indexes in by_track.values():
        ranked = sorted(indexes, key=lambda i: -float(proba[i]))
        keep.update(ranked[: max(8, min(top_k, len(ranked)))])
        keep.update(i for i in indexes if int(y_cue[i]) == 1)
    return np.asarray(sorted(keep), dtype=int)


_OFFSET_FEATURE_KEYS = (
    "kick_offset",
    "texture_change",
    "energy_drop_held",
)


def _offset_feature_mask(X: np.ndarray) -> np.ndarray:
    return _feature_flag_mask(X, _OFFSET_FEATURE_KEYS)


def _feature_flag_mask(X: np.ndarray, keys: tuple[str, ...]) -> np.ndarray:
    names = list(MODEL_FEATURE_NAMES)
    cols = [names.index(key) for key in keys if key in names]
    if not cols:
        return np.zeros(len(X), dtype=bool)
    block = np.asarray(X[:, cols], dtype=float)
    return np.any(np.nan_to_num(block, nan=0.0) >= 1.0, axis=1)


def _mid_band(unary: np.ndarray) -> np.ndarray:
    scores = np.asarray(unary, dtype=float)
    return (scores >= 0.28) & (scores <= 0.62)


_OUTRO_REGION_KEYS = (
    "pre_decline",
    "still_loud_kick_drop",
    "still_loud_vocal_drop",
    "phrase_pre_decline",
)


def _outro_region_mask(X: np.ndarray) -> np.ndarray:
    mask = _feature_flag_mask(X, _OUTRO_REGION_KEYS)
    names = list(MODEL_FEATURE_NAMES)
    if "mix_vs_peak" in names:
        peak = np.nan_to_num(X[:, names.index("mix_vs_peak")], nan=0.0)
        mask = np.logical_or(mask, peak >= 0.70)
    return mask


def _is_offset_like(row: dict[str, Any]) -> bool:
    return any(float(row.get(key) or 0.0) >= 1.0 for key in _OFFSET_FEATURE_KEYS)


def _sequence_view(X: np.ndarray, unary: np.ndarray) -> np.ndarray:
    """Narrow phrase/stem lookahead + unary — not the full bar matrix."""
    names = list(MODEL_FEATURE_NAMES)
    cols = [names.index(key) for key in SEQUENCE_MODEL_FEATURES if key in names]
    if not cols:
        return np.column_stack([np.asarray(unary, dtype=float).reshape(-1, 1)])
    return np.column_stack([np.asarray(X, dtype=float)[:, cols], unary])


def _fit_seq_head(
    X: np.ndarray,
    y: np.ndarray,
    *,
    seed: int,
    sample_weight: np.ndarray | None = None,
) -> HistGradientBoostingClassifier:
    clf = HistGradientBoostingClassifier(
        max_depth=5,
        max_iter=140,
        learning_rate=0.05,
        min_samples_leaf=16,
        l2_regularization=0.2,
        random_state=seed,
        class_weight=None if sample_weight is not None else "balanced",
    )
    train_x = np.asarray(X, dtype=float)
    if len(y) < 80 or train_x.shape[1] > max(8, len(y) // 2):
        train_x = np.nan_to_num(train_x, nan=0.0)
        clf.set_params(min_samples_leaf=2, max_iter=40, max_depth=2, max_bins=8)
    clf.fit(train_x, y, sample_weight=sample_weight)
    return clf


def train_cue_bar_model(
    rows: Sequence[dict[str, Any]],
    *,
    seed: int = 0,
    use_offset_head: bool = True,
    offset_positive_weight: float = 3.0,
) -> CueBarModel:
    enriched = apply_track_relative_by_track(
        [apply_derived_features(dict(row)) for row in rows]
    )
    X, y_cue, y_loop = matrix_from_rows(enriched)
    if X.size == 0:
        raise ValueError("No training rows")
    offset_like = np.asarray([_is_offset_like(row) for row in enriched], dtype=bool)
    # Exact-bar head for ranking, then a regularized rerank on the hard top-k.
    cue_clf = _fit_head(X, y_cue, seed=seed)
    stage1 = _positive_proba(cue_clf, X)
    hard = _hard_negative_indices(enriched, stage1, y_cue)
    cue_rerank = None
    if len(hard) >= 20 and int(y_cue[hard].sum()) > 0:
        # Upweight true cues and the top-8 false positives that steal the 2–6 map.
        weights = np.ones(len(hard), dtype=float)
        from collections import defaultdict

        by_track: dict[str, list[int]] = defaultdict(list)
        for index, row in enumerate(enriched):
            by_track[str(row.get("track_id") or index)].append(index)
        steal: set[int] = set()
        for indexes in by_track.values():
            ranked = sorted(indexes, key=lambda i: -float(stage1[i]))
            steal.update(i for i in ranked[:8] if int(y_cue[i]) == 0)
        for offset, source_index in enumerate(hard.tolist()):
            if int(y_cue[source_index]) == 1:
                # Offset / texture positives are rare vs intros; lift without 8×.
                weights[offset] = (
                    float(offset_positive_weight)
                    if bool(offset_like[source_index])
                    else 3.0
                )
            elif source_index in steal:
                weights[offset] = 2.5
        cue_rerank = _fit_head(
            X[hard], y_cue[hard], seed=seed + 2, sample_weight=weights
        )
    offset_clf = None
    offset_idx = np.flatnonzero(offset_like)
    if (
        use_offset_head
        and len(offset_idx) >= 20
        and int(y_cue[offset_idx].sum()) > 0
    ):
        offset_weights = np.ones(len(offset_idx), dtype=float)
        for pos, source_index in enumerate(offset_idx.tolist()):
            if int(y_cue[source_index]) == 1:
                offset_weights[pos] = float(offset_positive_weight)
        offset_clf = _fit_head(
            X[offset_idx],
            y_cue[offset_idx],
            seed=seed + 3,
            sample_weight=offset_weights,
        )
    # ExtraTrees family + fuse_hgb_tree_scores(hgb, tree) in predict_cue_proba.
    tree_clf = None
    tree_fill = None
    if len(X) >= 40 and int(y_cue.sum()) > 0:
        with np.errstate(all="ignore"):
            tree_fill = np.nanmedian(np.asarray(X, dtype=float), axis=0)
        tree_fill = np.where(np.isfinite(tree_fill), tree_fill, 0.0)
        imputed = _impute_finite(X, tree_fill)
        try:
            tree = ExtraTreesClassifier(
                n_estimators=180 if len(y_cue) >= 80 else 40,
                max_depth=12 if len(y_cue) >= 80 else 4,
                min_samples_leaf=4 if len(y_cue) >= 80 else 1,
                class_weight="balanced",
                random_state=seed + 7,
                n_jobs=1,
            )
            tree.fit(imputed, y_cue)
            tree_clf = tree
        except ValueError:
            tree_clf = None
            tree_fill = None
    seq_clf = None
    miss_clf = None
    unary = stage1
    if cue_rerank is not None:
        unary = 0.35 * stage1 + 0.65 * _positive_proba(cue_rerank, X)
    if len(X) >= 40 and int(y_cue.sum()) > 0:
        seq_x = np.column_stack([X, unary])
        try:
            seq_clf = _fit_head(seq_x, y_cue, seed=seed + 4)
        except ValueError:
            seq_clf = None
        # Residual family + fuse_residual_misses(stacked, miss) in predict_cue_proba.
        # Rank with the same 0.45/0.55 combined scores predict uses.
        if seq_clf is not None:
            seq_scores = _positive_proba(seq_clf, np.column_stack([X, unary]))
            rank_scores = 0.45 * unary + 0.55 * seq_scores
        else:
            rank_scores = unary
        miss_weights = np.ones(len(y_cue), dtype=float)
        from collections import defaultdict

        by_track_miss: dict[str, list[int]] = defaultdict(list)
        for index, row in enumerate(enriched):
            by_track_miss[str(row.get("track_id") or index)].append(index)
        for indexes in by_track_miss.values():
            ranked = sorted(indexes, key=lambda i: -float(rank_scores[i]))
            rank_of = {source: rank for rank, source in enumerate(ranked)}
            for source_index in indexes:
                if int(y_cue[source_index]) != 1:
                    continue
                miss_weights[source_index] = (
                    4.0 if rank_of[source_index] >= 8 else 1.0
                )
        try:
            miss_clf = _fit_head(
                np.column_stack([X, unary]),
                y_cue,
                seed=seed + 8,
                sample_weight=miss_weights,
                class_weight="balanced",
            )
        except ValueError:
            miss_clf = None
    if int(y_loop.sum()) == 0:
        loop_clf = _fit_head(X, y_cue, seed=seed + 1)
    else:
        loop_clf = _fit_head(X, y_loop, seed=seed + 1)
    return CueBarModel(
        cue_clf=cue_clf,
        loop_clf=loop_clf,
        cue_rerank=cue_rerank,
        offset_clf=offset_clf,
        seq_clf=seq_clf,
        tree_clf=tree_clf,
        tree_fill=tree_fill,
        miss_clf=miss_clf,
    )


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
        "cue_rerank": model.cue_rerank,
        "offset_clf": model.offset_clf,
        "seq_clf": model.seq_clf,
        "mid_clf": model.mid_clf,
        "outro_clf": model.outro_clf,
        "tree_clf": model.tree_clf,
        "tree_fill": model.tree_fill,
        "miss_clf": model.miss_clf,
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
    names = tuple(payload.get("feature_names") or MODEL_FEATURE_NAMES)
    if names != MODEL_FEATURE_NAMES:
        return None
    return CueBarModel(
        cue_clf=payload["cue_clf"],
        loop_clf=payload["loop_clf"],
        feature_names=names,
        cue_rerank=payload.get("cue_rerank"),
        offset_clf=payload.get("offset_clf"),
        seq_clf=payload.get("seq_clf"),
        mid_clf=payload.get("mid_clf"),
        outro_clf=payload.get("outro_clf"),
        tree_clf=payload.get("tree_clf"),
        tree_fill=payload.get("tree_fill"),
        miss_clf=payload.get("miss_clf"),
    )


def auc_or_none(y_true: np.ndarray, y_score: np.ndarray) -> Optional[float]:
    if len(set(int(v) for v in y_true.tolist())) < 2:
        return None
    return float(roc_auc_score(y_true, y_score))
