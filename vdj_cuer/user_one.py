"""Pin AutoCue markers to the user 1 already on disk (Scan Phase)."""

from __future__ import annotations

from dataclasses import is_dataclass, replace
from typing import Iterable, List, Optional, Sequence, TypeVar

T = TypeVar("T")

USER_ONE_EPS = 0.02  # 20 ms — same 1, not a previous bar


def _pos(item: object) -> float:
    if isinstance(item, dict):
        if "position" in item:
            return float(item["position"])
        if "timestamp" in item:
            return float(item["timestamp"])
        if "start" in item:
            return float(item["start"])
    return float(getattr(item, "position"))


def _at_user_one(item: object, offset: float, eps: float = USER_ONE_EPS) -> bool:
    return abs(_pos(item) - float(offset)) <= eps


def pin_markers_to_user_one(
    offset: float,
    items: Sequence[T],
    *,
    eps: float = USER_ONE_EPS,
) -> List[T]:
    """Keep markers on/after the disk 1. Sort so the 1 is first.

    Earlier grid 1s (Rodrigo 0.03 when Phase is 24.03) are a previous
    downbeat, not the user 1. Drop them. Do not invent a replacement here —
    the caller injects a cue on ``offset`` when the list has no 1.
    """
    offset = float(offset)
    kept = [item for item in items if _pos(item) >= offset - eps]
    kept.sort(key=lambda item: (0 if _at_user_one(item, offset, eps) else 1, _pos(item)))
    return kept


def has_marker_on_user_one(
    offset: float,
    items: Iterable[object],
    *,
    eps: float = USER_ONE_EPS,
) -> bool:
    return any(_at_user_one(item, offset, eps) for item in items)


def phrase_grid_offset(user_one: float, bpm: float) -> float:
    """8-count origin for mix points.

    Cue 1 stays on the disk 1. Yellow [1]s are phrase 1s: every 4 bars
    (16 beats) from that 1. Do not shift a bar later — that lands cues
    on in-between 4-beat 1s with no [1] box (Come back 82.697 vs 85.364).
    """
    return float(user_one)



def _with_loop_start(item: T, position: float, length_beats: float = 8.0) -> T:
    if isinstance(item, dict):
        new = dict(item)
        if "position" in new or "position" not in new and "start" not in new and "timestamp" not in new:
            new["position"] = position
        if "start" in new:
            new["start"] = position
        if "timestamp" in new:
            new["timestamp"] = position
        new["length_beats"] = int(length_beats)
        return new  # type: ignore[return-value]
    kwargs = {"position": position}
    if is_dataclass(item) and "length_beats" in getattr(item, "__dataclass_fields__", {}):
        kwargs["length_beats"] = float(length_beats)
    elif hasattr(item, "length_beats"):
        kwargs["length_beats"] = float(length_beats)
    return replace(item, **kwargs)  # type: ignore[misc]


def ensure_loops_on_user_one(
    offset: float,
    loops: Sequence[T],
    *,
    bpm: float,
    song_length: Optional[float] = None,
    eps: float = USER_ONE_EPS,
    max_loops: int = 2,
) -> List[T]:
    """Keep post-1 loops, and always park an 8-beat loop on the disk 1.

    Gemini often puts loops on the intro 1 (Rodrigo 0.03 / 12.03) while
    Scan Phase is later. Pin would drop them all. Slide a copy onto the
    user 1 and the next 32-beat phrase so they replay on-grid with no
    residual (exactly 8 beats).
    """
    offset = float(offset)
    kept = pin_markers_to_user_one(offset, loops, eps=eps)
    out: List[T] = [_with_loop_start(item, _pos(item), 8.0) for item in kept]
    if not bpm or bpm <= 0:
        return out[:max_loops]
    beat = 60.0 / float(bpm)
    second = offset + beat * 32.0
    slots = [offset]
    if song_length is None or second < float(song_length) - 10.0:
        slots.append(second)
    templates = list(loops)
    ti = 0
    for slot in slots:
        if any(abs(_pos(item) - slot) <= eps for item in out):
            continue
        if song_length is not None and slot >= float(song_length) - 10.0:
            continue
        if not templates:
            break
        src = templates[min(ti, len(templates) - 1)]
        ti += 1
        out.append(_with_loop_start(src, slot, 8.0))
    out.sort(key=lambda item: (0 if _at_user_one(item, offset, eps) else 1, _pos(item)))
    return out[:max_loops]
