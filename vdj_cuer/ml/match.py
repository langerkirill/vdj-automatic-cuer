"""Compare on-screen VDJ POIs to an AutoCue proposal (cache or last write)."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from vdj_cuer.analysis_cache import DEFAULT_CACHE_DIR, load_cached_analysis
from .eval_metrics import bar_window_seconds

EXACT_SECONDS = 0.08
WRITTEN_PREFIX = "written-"


def _item_kind(item: Any) -> str:
    if isinstance(item, dict):
        raw = str(item.get("kind") or item.get("type") or "").strip().lower()
        if raw:
            return "loop" if raw == "loop" else "cue"
        if "start" in item and "pos" not in item and "timestamp" not in item:
            return "loop"
        return "cue"
    raw = str(getattr(item, "kind", "") or getattr(item, "type", "") or "").lower()
    return "loop" if raw == "loop" else "cue"


def _item_pos(item: Any) -> float | None:
    if isinstance(item, (int, float)):
        value = float(item)
        return value if value >= 0 else None
    keys = ("pos", "timestamp", "start")
    if isinstance(item, dict):
        for key in keys:
            if key in item and item[key] is not None:
                try:
                    value = float(item[key])
                except (TypeError, ValueError):
                    continue
                return value if value >= 0 else None
        return None
    for key in keys:
        raw = getattr(item, key, None)
        if raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        return value if value >= 0 else None
    return None


def _times_for_kind(items: Iterable[Any] | None, kind: str) -> list[float]:
    values = list(items or [])
    if values and all(isinstance(item, (int, float)) for item in values):
        return point_times(values)
    return point_times(values, kind=kind)


def point_times(items: Iterable[Any] | None, *, kind: str | None = None) -> list[float]:
    times: list[float] = []
    for item in items or []:
        if kind and _item_kind(item) != kind:
            continue
        pos = _item_pos(item)
        if pos is None:
            continue
        times.append(pos)
    times.sort()
    return times


def proposal_times_from_analysis(analysis: Any) -> tuple[list[float], list[float]]:
    if not isinstance(analysis, dict):
        return [], []
    cues = point_times(analysis.get("measure_changes") or [])
    loops = point_times(analysis.get("loop_segments") or [])
    return cues, loops


def _pair_hits(
    actual: list[float], proposed: list[float], *, window: float
) -> tuple[int, float]:
    """Greedy nearest pairing. Returns (paired count, max paired |delta|)."""
    unused = list(proposed)
    hits = 0
    max_delta = 0.0
    for stamp in actual:
        best_i = None
        best_delta = None
        for index, pred in enumerate(unused):
            delta = abs(stamp - pred)
            if delta > window:
                continue
            if best_delta is None or delta < best_delta:
                best_i = index
                best_delta = delta
        if best_i is None or best_delta is None:
            continue
        unused.pop(best_i)
        hits += 1
        max_delta = max(max_delta, best_delta)
    return hits, max_delta


def compare_cue_sets(
    actual_cues: Iterable[Any],
    proposed_cues: Iterable[Any],
    *,
    actual_loops: Iterable[Any] | None = None,
    proposed_loops: Iterable[Any] | None = None,
    bpm: float | None = None,
) -> dict[str, Any]:
    """Compare on-screen POIs to an AutoCue proposal.

    ``match`` — same counts, every point within 80ms.
    ``near`` — same counts, every point within one bar.
    ``mismatch`` — extra/missing points or a pair outside the bar window.
    """
    act_cues = _times_for_kind(actual_cues, "cue")
    prop_cues = _times_for_kind(proposed_cues, "cue")
    act_loops = _times_for_kind(actual_loops, "loop")
    prop_loops = _times_for_kind(proposed_loops, "loop")

    has_actual = bool(act_cues or act_loops)
    has_proposed = bool(prop_cues or prop_loops)
    if not has_actual:
        return _result(
            matches=False,
            status="not_cued",
            reason="Not cued yet",
            act_cues=act_cues,
            prop_cues=prop_cues,
            act_loops=act_loops,
            prop_loops=prop_loops,
        )
    if not has_proposed:
        return _result(
            matches=False,
            status="no_proposal",
            reason="No AutoCue plan yet",
            act_cues=act_cues,
            prop_cues=prop_cues,
            act_loops=act_loops,
            prop_loops=prop_loops,
        )

    window = bar_window_seconds(float(bpm or 0.0), 1.0)
    cue_hits, cue_delta = _pair_hits(act_cues, prop_cues, window=window)
    loop_hits, loop_delta = _pair_hits(act_loops, prop_loops, window=window)
    max_delta = max(cue_delta, loop_delta)
    counts_equal = len(act_cues) == len(prop_cues) and len(act_loops) == len(prop_loops)
    all_paired = cue_hits == len(act_cues) and loop_hits == len(act_loops)

    if counts_equal and all_paired and max_delta <= EXACT_SECONDS:
        status = "match"
        reason = "On-screen cues match AutoCue"
    elif counts_equal and all_paired:
        status = "near"
        reason = f"On-screen cues within 1 bar of AutoCue ({max_delta:.2f}s)"
    elif not counts_equal:
        status = "mismatch"
        reason = (
            f"Count differs · cues {len(act_cues)}/{len(prop_cues)} · "
            f"loops {len(act_loops)}/{len(prop_loops)}"
        )
    else:
        status = "mismatch"
        reason = "A cue or loop is outside AutoCue's bar window"
    return _result(
        matches=status in {"match", "near"},
        status=status,
        reason=reason,
        act_cues=act_cues,
        prop_cues=prop_cues,
        act_loops=act_loops,
        prop_loops=prop_loops,
        cue_hits=cue_hits,
        loop_hits=loop_hits,
        max_delta=max_delta,
    )


def _result(
    *,
    matches: bool,
    status: str,
    reason: str,
    act_cues: list[float],
    prop_cues: list[float],
    act_loops: list[float],
    prop_loops: list[float],
    cue_hits: int = 0,
    loop_hits: int = 0,
    max_delta: float = 0.0,
) -> dict[str, Any]:
    return {
        "matches": matches,
        "status": status,
        "reason": reason,
        "autocue_matches": matches,
        "cue_hits": cue_hits,
        "loop_hits": loop_hits,
        "max_delta": max_delta,
        "actual_cues": len(act_cues),
        "proposed_cues": len(prop_cues),
        "actual_loops": len(act_loops),
        "proposed_loops": len(prop_loops),
    }


def _summary_points(summary: Any) -> list[Any]:
    if summary is None:
        return []
    if isinstance(summary, dict):
        return list(summary.get("points") or [])
    return list(getattr(summary, "points", None) or [])


def _summary_bpm(summary: Any) -> float | None:
    raw = summary.get("bpm") if isinstance(summary, dict) else getattr(summary, "bpm", None)
    try:
        bpm = float(raw)
    except (TypeError, ValueError):
        return None
    return bpm if bpm > 0 else None


def written_snapshot_id(path: str | Path) -> Optional[str]:
    audio = Path(path)
    try:
        size = int(audio.stat().st_size)
    except OSError:
        return None
    key = f"{audio.name.lower()}:{size}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def written_snapshot_path(path: str | Path, cache_dir: Path | None = None) -> Optional[Path]:
    digest = written_snapshot_id(path)
    if not digest:
        return None
    root = Path(cache_dir) if cache_dir else DEFAULT_CACHE_DIR
    return root / f"{WRITTEN_PREFIX}{digest}.json"


def save_written_autocue_points(
    path: str | Path,
    summary: Any,
    *,
    cache_dir: Path | None = None,
) -> Optional[Path]:
    """Remember the POIs AutoCue just wrote so later list loads can compare."""
    dest = written_snapshot_path(path, cache_dir)
    if dest is None:
        return None
    points = _summary_points(summary)
    payload = {
        "path": str(path),
        "name": Path(path).name,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "cues": point_times(points, kind="cue"),
        "loops": point_times(points, kind="loop"),
        "bpm": _summary_bpm(summary),
    }
    if not payload["cues"] and not payload["loops"]:
        return None
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(dest)
    except OSError:
        return None
    return dest


def load_written_autocue_points(
    path: str | Path,
    *,
    cache_dir: Path | None = None,
) -> Optional[dict[str, Any]]:
    dest = written_snapshot_path(path, cache_dir)
    if dest is None or not dest.is_file():
        return None
    try:
        payload = json.loads(dest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if not (payload.get("cues") or payload.get("loops")):
        return None
    return payload


def load_autocue_proposal(
    path: str | Path,
    *,
    cache_dir: Path | None = None,
) -> tuple[list[float], list[float]]:
    written = load_written_autocue_points(path, cache_dir=cache_dir)
    if written:
        return list(written.get("cues") or []), list(written.get("loops") or [])
    analysis = load_cached_analysis(path, cache_dir=cache_dir)
    return proposal_times_from_analysis(analysis)


def assess_autocue_match(
    path: str | Path,
    summary: Any = None,
    *,
    cache_dir: Path | None = None,
    proposed_cues: Iterable[Any] | None = None,
    proposed_loops: Iterable[Any] | None = None,
) -> dict[str, Any]:
    """List-load compare: on-screen VDJ POIs vs last AutoCue plan."""
    points = _summary_points(summary)
    actual_cues = point_times(points, kind="cue")
    actual_loops = point_times(points, kind="loop")
    if proposed_cues is None and proposed_loops is None:
        prop_cues, prop_loops = load_autocue_proposal(path, cache_dir=cache_dir)
    else:
        prop_cues, prop_loops = point_times(proposed_cues or []), point_times(
            proposed_loops or []
        )
    return compare_cue_sets(
        actual_cues,
        prop_cues,
        actual_loops=actual_loops,
        proposed_loops=prop_loops,
        bpm=_summary_bpm(summary),
    )
