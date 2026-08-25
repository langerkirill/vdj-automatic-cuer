"""CLI: export labels from cued pipeline folders, train, evaluate, ingest."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

from .ab import combine_times, f1
from .dataset import DEFAULT_LABELS, export_training_labels, load_jsonl as _load_jsonl
from .eval_metrics import bar_window_seconds, score_track
from .features import feature_matrix
from .ingest import ingest_cued_track
from .model import (
    DEFAULT_ARTIFACT,
    auc_or_none,
    load_cue_bar_model,
    matrix_from_rows,
    save_cue_bar_model,
    split_track_ids,
    train_cue_bar_model,
)
from .propose import propose_cues


def cmd_export(args: argparse.Namespace) -> int:
    dest = Path(args.out)
    stats = export_training_labels(dest, progress=lambda msg: print(msg, flush=True))
    print(
        f"Wrote {stats['written']} bar rows → {dest} "
        f"(skipped {stats['skipped']} tracks, scanned {stats['tracks']})"
    )
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    result = ingest_cued_track(
        args.path,
        labels_path=Path(args.out) if args.out else None,
        model_path=Path(args.model) if args.model else None,
        retrain=not args.no_retrain,
        seed=int(args.seed),
    )
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("ok") or result.get("reason") in {
        "not_training_source",
        "no_accepted_cues",
        "no_label_rows",
        "not_trainable",
    } else 1


def _rows_by_track(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("track_id") or "")].append(row)
    return grouped


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _eval_split(
    grouped: dict[str, list[dict[str, Any]]],
    track_ids: list[str],
    *,
    model,
) -> dict[str, float]:
    cue_recalls: list[float] = []
    cue_precs: list[float] = []
    loop_recalls: list[float] = []
    for tid in track_ids:
        rows = grouped.get(tid) or []
        if not rows:
            continue
        X = __import__("numpy").asarray(feature_matrix(rows), dtype=float)
        cue_scores = model.predict_cue_proba(X)
        loop_scores = model.predict_loop_proba(X)
        scored = []
        loop_scored = []
        for row, cs, ls in zip(rows, cue_scores, loop_scores):
            item = dict(row)
            item["score"] = float(cs)
            scored.append(item)
            loop_item = dict(row)
            loop_item["score"] = float(ls)
            loop_scored.append(loop_item)
        bpm = float(rows[0].get("bpm") or 120.0)
        cues = propose_cues(scored, bpm=bpm)
        loops = propose_cues(loop_scored, bpm=bpm, max_cues=3)
        cue_m = score_track(rows, [float(r["timestamp"]) for r in cues], bpm=bpm)
        loop_m = score_track(
            rows,
            [float(r["timestamp"]) for r in loops],
            bpm=bpm,
            label_key="is_loop_start",
        )
        cue_recalls.append(cue_m["recall_1bar"])
        cue_precs.append(cue_m["precision_top"])
        loop_recalls.append(loop_m["recall_1bar"])
    return {
        "tracks": float(len(track_ids)),
        "cue_recall_1bar": _mean(cue_recalls),
        "cue_precision_top6": _mean(cue_precs),
        "loop_recall_1bar": _mean(loop_recalls),
    }


def cmd_train(args: argparse.Namespace) -> int:
    src = Path(args.labels)
    if not src.is_file():
        print(f"No label file at {src}. Run export first.", file=sys.stderr)
        return 1
    rows = _load_jsonl(src)
    grouped = _rows_by_track(rows)
    split = split_track_ids(grouped.keys(), seed=int(args.seed))
    train_rows = [row for tid in split.train for row in grouped[tid]]
    model = train_cue_bar_model(train_rows, seed=int(args.seed))
    metrics = {
        "train": _eval_split(grouped, split.train, model=model),
        "val": _eval_split(grouped, split.val, model=model),
        "test": _eval_split(grouped, split.test, model=model),
        "n_rows": len(rows),
        "n_tracks": len(grouped),
    }
    X_test, y_cue, y_loop = matrix_from_rows(
        [row for tid in split.test for row in grouped[tid]]
    )
    if len(X_test):
        metrics["test"]["cue_auc"] = auc_or_none(y_cue, model.predict_cue_proba(X_test))
        metrics["test"]["loop_auc"] = auc_or_none(y_loop, model.predict_loop_proba(X_test))
    dest = save_cue_bar_model(model, Path(args.out), metrics=metrics)
    print(json.dumps(metrics, indent=2))
    print(f"Saved model → {dest}")
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    src = Path(args.labels)
    model = load_cue_bar_model(Path(args.model) if args.model else None)
    if model is None:
        print("No model artifact. Run train first.", file=sys.stderr)
        return 1
    rows = _load_jsonl(src)
    grouped = _rows_by_track(rows)
    split = split_track_ids(grouped.keys(), seed=int(args.seed))
    metrics = {
        "val": _eval_split(grouped, split.val, model=model),
        "test": _eval_split(grouped, split.test, model=model),
    }
    if args.compare_stem_plan:
        metrics["stem_plan_test"] = _eval_stem_plan(split.test, grouped)
    print(json.dumps(metrics, indent=2))
    return 0


def _eval_stem_plan(
    track_ids: list[str], grouped: dict[str, list[dict[str, Any]]]
) -> dict[str, float]:
    from vdj_cuer.stem_cue_plan import plan_stem_cues
    from vdj_cuer.stem_evidence import StemProfile, load_stem_profiles
    from vdj_cuer.stems import StemMixin

    class _StemIO(StemMixin):
        pass

    helper = _StemIO()
    recalls: list[float] = []
    precs: list[float] = []
    used = 0
    for tid in track_ids:
        rows = grouped.get(tid) or []
        if not rows:
            continue
        stems_path = Path(f"{tid}.vdjstems")
        if not stems_path.is_file():
            continue
        bpm = float(rows[0].get("bpm") or 0.0)
        duration = float(rows[0].get("duration") or 0.0)
        offset = float(rows[0].get("offset") or 0.0)
        if bpm <= 0 or duration <= 0:
            continue
        try:
            with tempfile.TemporaryDirectory(prefix="ml-eval-stems-") as tmp:
                extracted = helper._extract_vdj_stems(str(stems_path), tmp)
                if not extracted:
                    continue
                profiles = load_stem_profiles(extracted)
            planned = plan_stem_cues(
                profiles, bpm=bpm, offset=offset, duration=duration
            )
        except Exception:
            continue
        used += 1
        metrics = score_track(
            rows, [float(p["timestamp"]) for p in planned], bpm=bpm
        )
        recalls.append(metrics["recall_1bar"])
        precs.append(metrics["precision_top"])
    return {
        "tracks_with_stems": float(used),
        "cue_recall_1bar": _mean(recalls),
        "cue_precision_top6": _mean(precs),
    }


def _ml_cue_times(rows: list[dict[str, Any]], model, *, bpm: float) -> list[float]:
    import numpy as np

    X = np.asarray(feature_matrix(rows), dtype=float)
    scores = model.predict_cue_proba(X)
    scored = []
    for row, score in zip(rows, scores):
        item = dict(row)
        item["score"] = float(score)
        scored.append(item)
    return [float(r["timestamp"]) for r in propose_cues(scored, bpm=bpm)]


def _stem_plan_times(tid: str, rows: list[dict[str, Any]]) -> list[float]:
    from vdj_cuer.stem_cue_plan import plan_stem_cues
    from vdj_cuer.stem_evidence import load_stem_profiles
    from vdj_cuer.stems import StemMixin

    class _StemIO(StemMixin):
        pass

    stems_path = Path(f"{tid}.vdjstems")
    if not stems_path.is_file():
        return []
    bpm = float(rows[0].get("bpm") or 0.0)
    duration = float(rows[0].get("duration") or 0.0)
    offset = float(rows[0].get("offset") or 0.0)
    if bpm <= 0 or duration <= 0:
        return []
    try:
        with tempfile.TemporaryDirectory(prefix="ml-ab-stems-") as tmp:
            extracted = _StemIO()._extract_vdj_stems(str(stems_path), tmp)
            if not extracted:
                return []
            profiles = load_stem_profiles(extracted)
        planned = plan_stem_cues(
            profiles, bpm=bpm, offset=offset, duration=duration
        )
    except Exception:
        return []
    return [float(p["timestamp"]) for p in planned]


def _gemini_cache_times(tid: str) -> list[float]:
    try:
        from vdj_cuer.analysis_cache import load_cached_analysis
    except Exception:
        return []
    analysis = load_cached_analysis(tid)
    if not analysis:
        return []
    times: list[float] = []
    for cue in analysis.get("measure_changes") or []:
        try:
            times.append(float(cue.get("timestamp")))
        except (TypeError, ValueError):
            continue
    return times


def _pack_scores(per_track: list[dict[str, float]]) -> dict[str, float]:
    if not per_track:
        return {
            "tracks": 0.0,
            "cue_recall_1bar": 0.0,
            "cue_precision_top6": 0.0,
            "f1": 0.0,
        }
    rec = _mean([row["recall_1bar"] for row in per_track])
    prec = _mean([row["precision_top"] for row in per_track])
    return {
        "tracks": float(len(per_track)),
        "cue_recall_1bar": rec,
        "cue_precision_top6": prec,
        "f1": f1(prec, rec),
        "n_human": _mean([row["n_human"] for row in per_track]),
        "n_pred": _mean([row["n_pred"] for row in per_track]),
    }


def cmd_ab(args: argparse.Namespace) -> int:
    """Score stem-plan vs ML vs combos on the labeled holdout (no DB writes)."""
    model = load_cue_bar_model(Path(args.model) if args.model else None)
    if model is None:
        print("No model artifact. Run train first.", file=sys.stderr)
        return 1
    rows = _load_jsonl(Path(args.labels))
    grouped = _rows_by_track(rows)
    split = split_track_ids(grouped.keys(), seed=int(args.seed))
    methods = ("ml", "stem_plan", "gemini_cache", "union", "intersect", "blend")
    buckets = {
        "all_test": {name: [] for name in methods},
        "stemmed_test": {name: [] for name in methods},
    }

    for tid in split.test:
        track_rows = grouped.get(tid) or []
        if not track_rows:
            continue
        bpm = float(track_rows[0].get("bpm") or 0.0)
        if bpm <= 0:
            continue
        window = bar_window_seconds(bpm, 1.0)
        min_gap = (60.0 / bpm) * 12.0
        ml_times = _ml_cue_times(track_rows, model, bpm=bpm)
        stem_times = _stem_plan_times(tid, track_rows)
        gem_times = _gemini_cache_times(tid)
        proposals = {
            "ml": ml_times,
            "stem_plan": stem_times,
            "gemini_cache": gem_times,
            "union": combine_times(
                ml_times, stem_times, window=window, how="union", min_gap=min_gap
            ),
            "intersect": combine_times(
                ml_times, stem_times, window=window, how="intersect", min_gap=min_gap
            ),
            "blend": combine_times(
                ml_times, stem_times, window=window, how="blend", min_gap=min_gap
            ),
        }
        stemmed = Path(f"{tid}.vdjstems").is_file()
        for name, times in proposals.items():
            scored = score_track(track_rows, times, bpm=bpm)
            buckets["all_test"][name].append(scored)
            if stemmed:
                buckets["stemmed_test"][name].append(scored)

    report = {
        slice_name: {name: _pack_scores(vals) for name, vals in methods_map.items()}
        for slice_name, methods_map in buckets.items()
    }
    print(json.dumps(report, indent=2))
    for slice_name, methods_map in report.items():
        ranked = sorted(
            methods_map.items(),
            key=lambda item: (-item[1]["f1"], -item[1]["cue_recall_1bar"]),
        )
        if not ranked or ranked[0][1]["tracks"] <= 0:
            continue
        winner, stats = ranked[0]
        print(
            f"{slice_name} winner: {winner}  "
            f"F1={stats['f1']:.3f}  recall={stats['cue_recall_1bar']:.3f}  "
            f"precision={stats['cue_precision_top6']:.3f}  "
            f"n={int(stats['tracks'])}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vdj_cuer.ml")
    sub = parser.add_subparsers(dest="cmd", required=True)

    export_p = sub.add_parser(
        "export",
        help="Write bar labels from Cues Sorted, Ready For Sort, and cued Add Cues",
    )
    export_p.add_argument("--out", default=str(DEFAULT_LABELS))
    export_p.set_defaults(func=cmd_export)

    ingest_p = sub.add_parser(
        "ingest",
        help="Incrementally add one cued track to the label set and retrain",
    )
    ingest_p.add_argument("path", help="Audio path under Add Cues, Ready For Sort, or Cues Sorted")
    ingest_p.add_argument("--out", default=str(DEFAULT_LABELS), help="bars.jsonl path")
    ingest_p.add_argument("--model", default=str(DEFAULT_ARTIFACT))
    ingest_p.add_argument("--seed", type=int, default=0)
    ingest_p.add_argument("--no-retrain", action="store_true")
    ingest_p.set_defaults(func=cmd_ingest)

    train_p = sub.add_parser("train", help="Train cue/loop heads and write artifact")
    train_p.add_argument("--labels", default=str(DEFAULT_LABELS))
    train_p.add_argument("--out", default=str(DEFAULT_ARTIFACT))
    train_p.add_argument("--seed", type=int, default=0)
    train_p.set_defaults(func=cmd_train)

    eval_p = sub.add_parser("eval", help="Score a saved model on the holdout split")
    eval_p.add_argument("--labels", default=str(DEFAULT_LABELS))
    eval_p.add_argument("--model", default=str(DEFAULT_ARTIFACT))
    eval_p.add_argument("--seed", type=int, default=0)
    eval_p.add_argument("--compare-stem-plan", action="store_true")
    eval_p.set_defaults(func=cmd_eval)

    ab_p = sub.add_parser(
        "ab",
        help="A/B AutoCue stem-plan vs ML vs combo on the Cues Sorted holdout",
    )
    ab_p.add_argument("--labels", default=str(DEFAULT_LABELS))
    ab_p.add_argument("--model", default=str(DEFAULT_ARTIFACT))
    ab_p.add_argument("--seed", type=int, default=0)
    ab_p.set_defaults(func=cmd_ab)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
