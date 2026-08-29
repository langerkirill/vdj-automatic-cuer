"""
Batch BPM + beatgrid fixer.

Goal: put the musical 1 on beat 1 of the bar. The absolute second can be any
bar-equivalent of that 1 (offset ≡ 0 mod 4 beats). Learned from the
Pajamathon hand-edits in tests/fixtures/pajamathon_grid_edits.json.
"""

from __future__ import annotations

import json
import math
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .bpm_edit import halve_track_bpm
from .config import ADD_CUES, CUES_ROOT, LIBRARIES, assert_existing_audio
from .grid_edit import set_beatgrid_anchor
from .relocate import is_virtualdj_running, summarize_cues, summarize_cues_for_paths
from .autocue_path import ensure_autocue_on_path

ensure_autocue_on_path()
from vdj_cuer.common import existing_downbeat_is_trusted  # noqa: E402


NEVER_HALVE_BELOW = 110.0
# 143.999 (Dusk/Dawn) is VDJ double-time for ~72.
ALWAYS_HALVE_LOW = 138.0
ALWAYS_HALVE_HIGH = 168.0
MID_AC_HALVE_RATIO = 1.25
MID_KICK_HALVE_RATIO = 1.22
MID_MIX_HALVE_RATIO = 1.85
PHASE_WIN_RATIO = 1.35
MIN_PHASE_SCORE = 0.015
DOWNBEAT_BEAT_TOL = 0.30
FINE_ALIGN_STEP = 0.01
FINE_ALIGN_MIN_RATIO = 1.35
FINE_ALIGN_MAX_BEATS = 0.28
PHASE_WRITE_RATIO = 1.20
# Auto-align searches ±2 beats in ¼-beat steps. A ½-beat slip is invisible
# to the 0–3 phase voter and to fine-align (capped at 0.28 beats).
SUBBEAT_STEP_BEATS = 0.25
SUBBEAT_SEARCH_BEATS = 2.0
EARLY_ONES_START = 0.4
EARLY_ONES_END = 20.0
SKEPTICAL_WIN_RATIO = 1.28
SKEPTICAL_WIN_DELTA = 0.015
HALFBEAT_WIN_RATIO = 1.8
HALFBEAT_ABS_MIN = 0.03

SOURCE_RANK = {
    "same_path_phase": 0,
    "same_path_backup": 1,
    "zouk_original": 2,
}


@dataclass(frozen=True)
class FixtureCase:
    name: str
    source: str
    before_bpm: float
    after_bpm: float
    before_anchor: float
    after_anchor: float
    delta_beats: float
    expected_halve: bool
    expected_phase: int


@dataclass
class GridFixPlan:
    path: str
    name: str
    bpm_before: float
    bpm_after: float
    halve: bool
    anchor_before: float
    anchor_after: float
    shift_beats: int
    confidence: float
    action: str
    reason: str
    phase_scores: dict[int, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GridFixBatch:
    id: str
    status: str  # queued | running | ok | error
    created_at: str = ""
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    message: str = ""
    total: int = 0
    done: int = 0
    failed: int = 0
    skipped: int = 0
    halved: int = 0
    aligned: int = 0
    apply: bool = False
    items: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_batches: dict[str, GridFixBatch] = {}
_lock = threading.Lock()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def beat_period(bpm: float) -> float:
    if bpm <= 0:
        raise ValueError(f"Invalid BPM: {bpm}")
    return 60.0 / float(bpm)


def bar_phase_shift_beats(delta_beats: float) -> int:
    """Whole-beat move folded into the bar (0–3)."""
    return int(round(float(delta_beats))) % 4


def anchors_share_downbeat(
    left: float,
    right: float,
    bpm: float,
    *,
    beat_tol: float = DOWNBEAT_BEAT_TOL,
) -> bool:
    """True when both times are the same beat-of-bar (mod 4 beats)."""
    if bpm <= 0:
        return False
    wrapped = ((float(left) - float(right)) / beat_period(bpm)) % 4.0
    if wrapped < 0:
        wrapped += 4.0
    dist = min(wrapped, 4.0 - wrapped)
    return dist <= beat_tol


def decide_halve(
    bpm: float,
    score_full: float,
    score_half: float,
    *,
    ac_ratio: float | None = None,
    kick_ratio: float | None = None,
    kick_half: float | None = None,
    always_halve_band: bool = True,
) -> bool:
    """
    Halve VDJ double-time when the musical tempo is half.

    - Never below ~110 (already in the zouk / R&B pocket).
    - Always in 142–168 for Zouk/Pajamathon (VDJ's usual 72–84 double-time).
      House keeps that band evidence-gated so a real ~150 stays 150.
    - 110–141: half only with period-doubling evidence (onset autocorr or
      kick stem) and enough absolute energy. Mix-only scores lie.
    """
    tempo = float(bpm)
    if tempo < NEVER_HALVE_BELOW:
        return False
    in_double_band = ALWAYS_HALVE_LOW <= tempo <= ALWAYS_HALVE_HIGH
    if always_halve_band and in_double_band:
        return True
    half = float(score_half)
    if ac_ratio is not None and half >= 0.03:
        ac = float(ac_ratio)
        if ac >= MID_AC_HALVE_RATIO:
            return True
        # Tighter bands from later Pajamathon hand-halves (Luna 124, MOTHICA 130)
        # that do not trip Rejuvenate (130, ac 0.84) or Light (122, ac 0.94).
        if 118.0 <= tempo <= 128.0 and ac >= 1.12:
            return True
        if 129.0 <= tempo <= 133.0 and ac >= 1.05:
            return True
    if (
        kick_ratio is not None
        and float(kick_ratio) >= MID_KICK_HALVE_RATIO
        and float(kick_half or 0.0) >= 0.04
    ):
        return True
    if half <= 0:
        return False
    return half >= max(float(score_full) * MID_MIX_HALVE_RATIO, 0.08)


def _always_halve_double_time_band(path: str) -> bool:
    """Aggressive 142–168 half is for Zouk / Pajamathon, not House club tempo."""
    if not path:
        return True
    lowered = path.replace("\\", "/").lower()
    return "/house/" not in lowered


def choose_bar_phase(phase_scores: dict[int, float]) -> tuple[int, float]:
    """Pick beat-of-bar 0–3. Stay on 0 unless another phase is clearly stronger."""
    if not phase_scores:
        return 0, 1.0
    best = max(phase_scores, key=lambda phase: phase_scores[phase])
    best_score = float(phase_scores[best])
    current = float(phase_scores.get(0, 0.0))
    confidence = best_score / max(current, 1e-3)
    if best_score < MIN_PHASE_SCORE:
        return 0, confidence
    if best != 0 and best_score >= current * PHASE_WIN_RATIO:
        return int(best), confidence
    return 0, confidence


def propose_downbeat_anchor(
    anchor: float,
    bpm: float,
    phase_scores: dict[int, float],
) -> float:
    """Shift `anchor` by 0–3 beats so it sits on the winning 1 (mod 4)."""
    phase, _ = choose_bar_phase(phase_scores)
    return _nonnegative_same_phase(float(anchor) + phase * beat_period(bpm), bpm)


def _nonnegative_same_phase(anchor: float, bpm: float) -> float:
    period = beat_period(bpm)
    bar = 4.0 * period
    if anchor >= 0:
        return anchor
    if anchor >= -0.05:
        return 0.0
    steps = math.ceil(-anchor / bar)
    return anchor + steps * bar


def load_fixture_cases(path: str | Path) -> list[FixtureCase]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    best: dict[str, tuple[int, dict[str, Any]]] = {}
    for row in raw:
        name = str(row["name"])
        rank = SOURCE_RANK.get(str(row.get("source") or ""), 9)
        prev = best.get(name)
        if prev is None or rank < prev[0]:
            best[name] = (rank, row)

    cases: list[FixtureCase] = []
    for _rank, row in best.values():
        before_bpm = float(row["before_bpm"])
        after_bpm = float(row["after_bpm"])
        before_anchor = float(row["before_anchor"])
        after_anchor = float(row["after_anchor"])
        delta = float(row.get("delta_beats") or 0.0)
        if abs(delta) < 1e-9 and after_bpm > 0:
            delta = (after_anchor - before_anchor) / beat_period(after_bpm)
        cases.append(
            FixtureCase(
                name=str(row["name"]),
                source=str(row.get("source") or ""),
                before_bpm=before_bpm,
                after_bpm=after_bpm,
                before_anchor=before_anchor,
                after_anchor=after_anchor,
                delta_beats=delta,
                expected_halve=(
                    after_bpm <= before_bpm * 0.65 and after_bpm >= 55.0
                ),
                expected_phase=bar_phase_shift_beats(delta),
            )
        )
    return sorted(cases, key=lambda case: case.name.lower())


def resolve_pajamathon_audio(
    name: str, *, paj_dir: Path | None = None
) -> Optional[Path]:
    root = paj_dir if paj_dir is not None else ADD_CUES / "Pajamathon"
    direct = root / name
    if direct.is_file():
        return direct
    if not root.is_dir():
        return None
    target = name.lower()
    for child in root.iterdir():
        if child.is_file() and child.name.lower() == target:
            return child
    return None


def _score_beat_grid(
    onsets: list[float], hop: float, offset: float, beat_duration: float
) -> float:
    from vdj_cuer.beatgrid_alignment import BeatgridAlignmentMixin

    return float(
        BeatgridAlignmentMixin._score_beat_grid(
            onsets, hop, offset, beat_duration
        )
    )


def _score_downbeat_phase(
    onsets: list[float], hop: float, offset: float, measure_duration: float
) -> float:
    from vdj_cuer.beatgrid_alignment import BeatgridAlignmentMixin

    return float(
        BeatgridAlignmentMixin._score_downbeat_phase(
            onsets, hop, offset, measure_duration
        )
    )


def extract_onsets(audio_path: str | Path) -> tuple[list[float], float]:
    """Mix onset envelope (same decoder AutoCue uses for grid checks)."""
    from vdj_cuer.beatgrid_sources import BeatgridSourceMixin

    helper = BeatgridSourceMixin()
    onsets, hop = helper._decode_onset_envelope(str(audio_path), None)
    return list(onsets), float(hop)


def extract_kick_onsets(audio_path: str | Path) -> Optional[tuple[list[float], float]]:
    """Kick-stem envelope when VirtualDJ wrote an adjacent .vdjstems file."""
    from vdj_cuer.beatgrid_sources import BeatgridSourceMixin
    from vdj_cuer.stems import StemMixin

    stems = f"{audio_path}.vdjstems"
    if not Path(stems).is_file():
        return None
    try:
        streams = StemMixin._probe_vdj_stem_streams(stems)
    except Exception:
        return None
    index = {name: idx for name, idx in streams}
    if "kick" not in index:
        return None
    helper = BeatgridSourceMixin()
    onsets, hop = helper._decode_onset_envelope(stems, f"0:{index['kick']}")
    if not onsets:
        return None
    return list(onsets), float(hop)


def onset_autocorr_ratio(
    onsets: list[float], hop: float, bpm: float
) -> float:
    """onset-autocorr(2T) / onset-autocorr(T). >1 means a stronger half-tempo."""
    if bpm <= 0 or hop <= 0 or len(onsets) < 8:
        return 0.0
    period = beat_period(bpm)

    def at_lag(seconds: float) -> float:
        lag = max(1, int(round(seconds / hop)))
        if lag >= len(onsets):
            return 0.0
        total = 0.0
        count = 0
        for index in range(0, len(onsets) - lag):
            total += onsets[index] * onsets[index + lag]
            count += 1
        return total / count if count else 0.0

    full = at_lag(period)
    half = at_lag(period * 2.0)
    return half / max(full, 1e-12)


def _mask_onsets(
    onsets: list[float], hop: float, start: float = 20.0, end: float = 80.0
) -> list[float]:
    return [
        value if start <= index * hop <= end else 0.0
        for index, value in enumerate(onsets)
    ]


def _phase_scores(
    onsets: list[float], hop: float, origin: float, beat: float
) -> dict[int, float]:
    measure = beat * 4.0
    return {
        phase: _score_downbeat_phase(onsets, hop, origin + phase * beat, measure)
        for phase in range(4)
    }


def _earliest_downbeat(
    onsets: list[float], hop: float, time: float, bpm: float
) -> float:
    """Walk the same bar-phase toward the start while the 1 still has energy."""
    if not onsets or bpm <= 0:
        return time
    bar = 4.0 * beat_period(bpm)
    best = time
    reference = _score_downbeat_phase(onsets, hop, time, bar)
    cursor = time
    while cursor - bar >= 0.35:
        cursor -= bar
        score = _score_downbeat_phase(onsets, hop, cursor, bar)
        if reference <= 0:
            if score > 0:
                best = cursor
                reference = score
            continue
        if score >= reference * 0.55:
            best = cursor
        elif cursor < 10.0 and score < reference * 0.35:
            break
    return best


def _usable_phase_vote(scores: dict[int, float]) -> Optional[int]:
    if not scores:
        return None
    ordered = sorted(scores, key=lambda phase: scores[phase], reverse=True)
    best = ordered[0]
    second = ordered[1] if len(ordered) > 1 else best
    if scores[best] < MIN_PHASE_SCORE:
        return None
    if scores[best] < scores[second] * PHASE_WIN_RATIO:
        return None
    return int(best)


def _fine_align_offset(
    onsets: list[float], hop: float, offset: float, beat_duration: float
) -> float:
    """Nudge within a fraction of a beat only when the grid score jumps."""
    if beat_duration <= 0 or not onsets:
        return offset
    current = _score_beat_grid(onsets, hop, offset, beat_duration)
    best_offset = offset
    best_score = current
    max_shift = beat_duration * FINE_ALIGN_MAX_BEATS
    steps = int(max_shift / FINE_ALIGN_STEP)
    for step in range(-steps, steps + 1):
        candidate = offset + step * FINE_ALIGN_STEP
        score = _score_beat_grid(onsets, hop, candidate, beat_duration)
        if score > best_score:
            best_score = score
            best_offset = candidate
    if current <= 0:
        return best_offset if best_score >= MIN_PHASE_SCORE else offset
    if best_score >= current * FINE_ALIGN_MIN_RATIO:
        return best_offset
    return offset


def _score_ones_in_range(
    onsets: list[float],
    hop: float,
    offset: float,
    bpm: float,
    *,
    start: float = EARLY_ONES_START,
    end: float = EARLY_ONES_END,
) -> float:
    """Downbeat energy only inside [start, end] — intro 1s, not late-song fills."""
    if not onsets or hop <= 0 or bpm <= 0:
        return 0.0
    measure = beat_period(bpm) * 4.0
    radius = max(1, int(0.06 / hop))
    song_end = len(onsets) * hop
    hi_limit = min(float(end), song_end)
    lo_limit = max(0.0, float(start))
    score = 0.0
    count = 0
    timestamp = float(offset)
    if measure <= 0:
        return 0.0
    while timestamp < lo_limit:
        timestamp += measure
    while timestamp < hi_limit:
        center = int(round(timestamp / hop))
        lo = max(0, center - radius)
        hi = min(len(onsets), center + radius + 1)
        if hi > lo:
            score += max(onsets[lo:hi])
            count += 1
        timestamp += measure
    return score / count if count else 0.0


def _wrap_nonnegative(time: float, bpm: float) -> float:
    if time >= 0:
        return time
    bar = beat_period(bpm) * 4.0
    if bar <= 0:
        return 0.0
    while time < 0:
        time += bar
    return time


def _best_subbeat_offset(
    onsets: list[float],
    hop: float,
    origin: float,
    bpm: float,
    *,
    win_ratio: float = HALFBEAT_WIN_RATIO,
    win_delta: float = SKEPTICAL_WIN_DELTA,
) -> tuple[float, float, float]:
    """
    Search ±2 beats in ¼-beat steps using early-song bar-1 energy.

    Returns (best_offset, best_score, current_score).
    """
    current = _score_ones_in_range(onsets, hop, origin, bpm)
    beat = beat_period(bpm)
    # A ½-beat slip is the usual "every cue is off" case. Prefer it over a
    # louder beat-3 / offbeat that can win a wide ¼-beat search.
    half_best = origin
    half_score = current
    for step in (-0.5, 0.5):
        candidate = _wrap_nonnegative(origin + step * beat, bpm)
        score = _score_ones_in_range(onsets, hop, candidate, bpm)
        if score > half_score:
            half_score = score
            half_best = candidate
    if (
        half_best != origin
        and half_score >= HALFBEAT_ABS_MIN
        and half_score >= max(current * 2.0, current + 0.02)
    ):
        return half_best, half_score, current

    best_offset = origin
    best_score = current
    steps = int(round(SUBBEAT_SEARCH_BEATS / SUBBEAT_STEP_BEATS))
    for step in range(-steps, steps + 1):
        if step == 0:
            continue
        candidate = _wrap_nonnegative(origin + step * SUBBEAT_STEP_BEATS * beat, bpm)
        score = _score_ones_in_range(onsets, hop, candidate, bpm)
        if score > best_score:
            best_score = score
            best_offset = candidate
    if current <= 1e-9 and best_score >= MIN_PHASE_SCORE:
        return best_offset, best_score, current
    if (
        best_score >= max(current * win_ratio, MIN_PHASE_SCORE)
        and best_score >= current + win_delta
    ):
        return best_offset, best_score, current
    return origin, current, current


def plan_grid_bpm_fix(
    audio_path: str | Path | None = None,
    *,
    bpm: float,
    anchor: float,
    onsets: list[float] | None = None,
    hop_seconds: float | None = None,
    name: str = "",
    skeptical: bool = False,
) -> GridFixPlan:
    """
    Decide half-vs-keep and which beat of the bar is the 1.

    Pass a precomputed onset envelope to stay offline (tests). Otherwise the
    audio file is decoded the same way AutoCue verifies grids.
    """
    path_s = str(audio_path) if audio_path is not None else ""
    label = name or (Path(path_s).name if path_s else "")
    tempo = float(bpm)
    origin = float(anchor)

    if onsets is None:
        if audio_path is None:
            raise ValueError("plan_grid_bpm_fix needs audio_path or onsets")
        onsets, hop_seconds = extract_onsets(audio_path)

    hop = float(hop_seconds or 0.01)
    score_full = (
        _score_beat_grid(onsets, hop, origin, beat_period(tempo)) if onsets else 0.0
    )
    score_half = (
        _score_beat_grid(onsets, hop, origin, beat_period(tempo / 2.0))
        if onsets and tempo > 1
        else 0.0
    )
    ac_ratio = onset_autocorr_ratio(onsets, hop, tempo) if onsets else None
    kick = None
    kick_ratio = None
    kick_half_score = None
    if audio_path is not None:
        kick = extract_kick_onsets(audio_path)
        if kick:
            kick_onsets, kick_hop = kick
            kick_full = _score_beat_grid(
                kick_onsets, kick_hop, origin, beat_period(tempo)
            )
            kick_half_score = _score_beat_grid(
                kick_onsets, kick_hop, origin, beat_period(tempo / 2.0)
            )
            kick_ratio = kick_half_score / max(kick_full, 1e-6)
    always_band = _always_halve_double_time_band(path_s)
    halve = decide_halve(
        tempo,
        score_full,
        score_half,
        ac_ratio=ac_ratio,
        kick_ratio=kick_ratio,
        kick_half=kick_half_score,
        always_halve_band=always_band,
    )
    bpm_after = tempo / 2.0 if halve else tempo
    beat = beat_period(bpm_after)

    search_src = kick if kick else ((onsets, hop) if onsets else None)
    subbeat_reason = ""
    if search_src and not halve:
        win_ratio = SKEPTICAL_WIN_RATIO if skeptical else HALFBEAT_WIN_RATIO
        best_off, best_sc, cur_sc = _best_subbeat_offset(
            search_src[0],
            search_src[1],
            origin,
            bpm_after,
            win_ratio=win_ratio,
        )
        if abs(best_off - origin) > 0.02:
            delta_beats = (best_off - origin) / beat
            # Fold into (-2, 2] so the reason is readable.
            while delta_beats > 2:
                delta_beats -= 4
            while delta_beats <= -2:
                delta_beats += 4
            origin = best_off
            subbeat_reason = (
                f"1 is {delta_beats:+.2f} beat "
                f"(early {'kick' if kick else 'mix'} {cur_sc:.3f}→{best_sc:.3f})"
            )

    # If the stored 1 already matches kick/mix downbeats, do not fine-shift or
    # flip phase — that is a hand-aligned grid. Half-beat slips still win via
    # subbeat_reason (Auto-align / skeptical included).
    origin_phase_scores: dict[int, float] = {}
    if kick:
        origin_phase_scores = _phase_scores(kick[0], kick[1], origin, beat)
    elif onsets:
        origin_phase_scores = _phase_scores(onsets, hop, origin, beat)
    if (
        not subbeat_reason
        and existing_downbeat_is_trusted(origin_phase_scores)
        and not halve
    ):
        candidate = _nonnegative_same_phase(origin, bpm_after)
        if onsets:
            walked = _earliest_downbeat(onsets, hop, candidate, bpm_after)
            if anchors_share_downbeat(origin, walked, bpm_after, beat_tol=0.15):
                candidate = walked
        return GridFixPlan(
            path=path_s,
            name=label,
            bpm_before=tempo,
            bpm_after=bpm_after,
            halve=False,
            anchor_before=float(anchor),
            anchor_after=candidate,
            shift_beats=0,
            confidence=1.0,
            action="skip",
            reason=f"keep existing 1 @ {float(anchor):.3f}s (stems agree)",
            phase_scores=origin_phase_scores,
        )

    fine_src = kick if kick else ((onsets, hop) if onsets else None)
    fine = (
        _fine_align_offset(fine_src[0], fine_src[1], origin, beat)
        if fine_src
        else origin
    )

    votes: list[tuple[str, int, float]] = []
    mix_scores = {phase: 0.0 for phase in range(4)}
    if onsets:
        mix_scores = _phase_scores(onsets, hop, fine, beat)
        mix_vote = _usable_phase_vote(mix_scores)
        if mix_vote is None:
            body = _mask_onsets(onsets, hop)
            if any(body):
                body_scores = _phase_scores(body, hop, fine, beat)
                body_vote = _usable_phase_vote(body_scores)
                if body_vote is not None:
                    votes.append(("body", body_vote, body_scores[body_vote]))
        else:
            votes.append(("mix", mix_vote, mix_scores[mix_vote]))
    if kick:
        kick_scores = _phase_scores(kick[0], kick[1], fine, beat)
        kick_phase = _usable_phase_vote(kick_scores)
        if kick_phase is not None:
            votes.append(("kick", kick_phase, kick_scores[kick_phase]))

    kick_vote = next((vote for name, vote, _score in votes if name == "kick"), None)
    if subbeat_reason:
        # A ½-beat (or other sub-beat) 1 already won the early-kick test.
        # Whole-beat voting would slide it onto the 3 / the &.
        phase = 0
        confidence = 1.0
    elif halve:
        # Keep VDJ's time origin after a half unless the kick is very sure.
        phase = 0
        confidence = 1.0
        if kick is not None and kick_vote not in (None, 0):
            kick_scores = _phase_scores(kick[0], kick[1], fine, beat)
            kick_best = kick_scores.get(kick_vote, 0.0)
            kick_cur = kick_scores.get(0, 0.0)
            if kick_best >= max(kick_cur * 1.4, MIN_PHASE_SCORE):
                phase = kick_vote
                confidence = kick_best / max(kick_cur, 1e-3)
    elif votes:
        counts: dict[int, int] = {}
        for _name, vote, _score in votes:
            counts[vote] = counts.get(vote, 0) + 1
        top = max(counts.values())
        leaders = [item for item, count in counts.items() if count == top]
        phase = (
            leaders[0]
            if len(leaders) == 1
            else max(votes, key=lambda item: item[2])[1]
        )
        confidence = float(top)
        if phase != 0 and top < 2 and confidence < PHASE_WRITE_RATIO:
            # A single weak source is not enough to move the 1.
            strongest = max(votes, key=lambda item: item[2])
            if strongest[2] < MIN_PHASE_SCORE * 4:
                phase = 0
    else:
        phase, confidence = choose_bar_phase(mix_scores)

    phase_scores = mix_scores
    candidate = _nonnegative_same_phase(fine + phase * beat, bpm_after)
    if onsets:
        candidate = _earliest_downbeat(onsets, hop, candidate, bpm_after)
    anchor_after = candidate
    stored = float(anchor)

    moved = not anchors_share_downbeat(
        stored, anchor_after, bpm_after, beat_tol=0.15
    )
    if halve and moved:
        action = "halve_and_align"
    elif halve:
        action = "halve"
    elif moved:
        action = "align"
    else:
        action = "skip"

    reason_parts = []
    if halve:
        extra = []
        if ac_ratio is not None:
            extra.append(f"ac {ac_ratio:.2f}")
        if kick_ratio is not None:
            extra.append(f"kick {kick_ratio:.2f}")
        reason_parts.append(
            f"halve {tempo:.1f}→{bpm_after:.1f} "
            f"(grid {score_full:.3f}/{score_half:.3f}"
            + (f", {', '.join(extra)}" if extra else "")
            + ")"
        )
    elif action == "skip":
        reason_parts.append(f"existing 1 still best @ {stored:.3f}s")
    else:
        reason_parts.append(f"keep {tempo:.1f} BPM")
    if subbeat_reason:
        reason_parts.append(subbeat_reason)
    if phase:
        reason_parts.append(f"1 is +{phase} beat(s)")
    if abs(fine - origin) > 0.02:
        reason_parts.append(f"fine {fine - origin:+.3f}s")
    reason = "; ".join(reason_parts)

    return GridFixPlan(
        path=path_s,
        name=label,
        bpm_before=tempo,
        bpm_after=bpm_after,
        halve=halve,
        anchor_before=stored,
        anchor_after=anchor_after,
        shift_beats=phase,
        confidence=confidence,
        action=action,
        reason=reason,
        phase_scores=phase_scores,
    )


def apply_grid_fix_plan(
    plan: GridFixPlan,
    *,
    dry_run: bool = False,
    allow_vdj_running: bool = False,
    create_backup: bool = True,
) -> dict[str, Any]:
    if plan.action == "skip":
        return {"ok": True, "action": "skip", "plan": plan.to_dict()}

    if is_virtualdj_running() and not dry_run and not allow_vdj_running:
        raise RuntimeError(
            "VirtualDJ is running. Close it before writing BPM/beatgrid."
        )

    if plan.halve and not dry_run and plan.path:
        live = summarize_cues(plan.path)
        live_bpm = getattr(live, "bpm", None)
        if live_bpm and abs(float(live_bpm) - float(plan.bpm_before)) > 1.0:
            return {
                "ok": True,
                "action": "skipped_stale",
                "plan": plan.to_dict(),
                "reason": (
                    f"BPM is {live_bpm:.1f} now, plan was {plan.bpm_before:.1f} "
                    "— refusing another half"
                ),
            }

    result: dict[str, Any] = {
        "ok": True,
        "action": plan.action,
        "plan": plan.to_dict(),
    }
    backup_next = create_backup
    if plan.halve:
        result["bpm"] = halve_track_bpm(
            plan.path,
            dry_run=dry_run,
            allow_vdj_running=allow_vdj_running,
            create_backup=backup_next,
        )
        backup_next = False
    if plan.action in {"align", "halve_and_align"}:
        try:
            result["grid"] = set_beatgrid_anchor(
                plan.path,
                anchor_seconds=plan.anchor_after,
                dry_run=dry_run,
                allow_vdj_running=allow_vdj_running,
                create_backup=backup_next,
            )
        except Exception as exc:
            result["ok"] = False
            result["action"] = "partial" if plan.halve else "error"
            result["error"] = str(exc)
    return result


def _assert_allowed(path: Path) -> Path:
    return assert_existing_audio(path)


def _grid_anchor_from_cues(cues: Any) -> Optional[float]:
    if getattr(cues, "scan_phase", None) is not None:
        return float(cues.scan_phase)
    if getattr(cues, "beatgrid_pos", None) is not None:
        return float(cues.beatgrid_pos)
    return None


def plan_for_track(
    audio_path: str | Path,
    *,
    cues: Any = None,
    skeptical: bool = False,
) -> GridFixPlan:
    audio = _assert_allowed(Path(audio_path))
    if cues is None:
        cues = summarize_cues(audio)
    if not getattr(cues, "in_database", False):
        return GridFixPlan(
            path=str(audio),
            name=audio.name,
            bpm_before=0.0,
            bpm_after=0.0,
            halve=False,
            anchor_before=0.0,
            anchor_after=0.0,
            shift_beats=0,
            confidence=0.0,
            action="skip",
            reason="Not in VirtualDJ database",
        )
    bpm = getattr(cues, "bpm", None)
    anchor = _grid_anchor_from_cues(cues)
    if not bpm or anchor is None:
        return GridFixPlan(
            path=str(audio),
            name=audio.name,
            bpm_before=float(bpm or 0.0),
            bpm_after=float(bpm or 0.0),
            halve=False,
            anchor_before=float(anchor or 0.0),
            anchor_after=float(anchor or 0.0),
            shift_beats=0,
            confidence=0.0,
            action="skip",
            reason="No usable BPM or beatgrid",
        )
    return plan_grid_bpm_fix(
        audio, bpm=float(bpm), anchor=float(anchor), skeptical=skeptical
    )


def attempt_grid_align(
    source_path: str | Path,
    *,
    apply: bool = False,
    dry_run: bool = False,
    allow_vdj_running: bool = False,
    create_backup: bool = True,
) -> dict[str, Any]:
    """
    Run the same stem/onset 1-finder used by batch grid-fix on one track.

    apply=False returns the plan only (UI preview). apply=True writes BPM/grid.
    """
    audio = _assert_allowed(Path(source_path))
    plan = plan_for_track(audio, skeptical=True)
    payload: dict[str, Any] = {
        "ok": True,
        "path": str(audio),
        "name": audio.name,
        "plan": plan.to_dict(),
        "applied": False,
        "apply_result": None,
    }
    if not apply or plan.action == "skip":
        return payload
    applied = apply_grid_fix_plan(
        plan,
        dry_run=dry_run,
        allow_vdj_running=allow_vdj_running,
        create_backup=create_backup,
    )
    payload["applied"] = bool(applied.get("ok")) and applied.get("action") not in {
        "skip",
        "skipped_stale",
    }
    payload["apply_result"] = applied
    if applied.get("grid", {}).get("cues"):
        payload["cues"] = applied["grid"]["cues"]
    return payload


def get_grid_fix_batch(batch_id: str) -> Optional[GridFixBatch]:
    with _lock:
        return _batches.get(batch_id)


def list_grid_fix_batches(limit: int = 10) -> list[dict[str, Any]]:
    with _lock:
        batches = sorted(_batches.values(), key=lambda item: item.created_at, reverse=True)
        return [item.to_dict() for item in batches[:limit]]


def start_batch_grid_fix(
    paths: list[str],
    *,
    apply: bool = True,
    dry_run: bool = False,
    allow_vdj_running: bool = False,
    wait: bool = False,
) -> GridFixBatch:
    if not paths:
        raise ValueError("No paths provided for grid fix")

    if apply and not dry_run and is_virtualdj_running() and not allow_vdj_running:
        raise RuntimeError(
            "VirtualDJ is running. Close it before writing BPM/beatgrid."
        )

    if apply and not dry_run:
        with _lock:
            busy = [
                item
                for item in _batches.values()
                if item.apply and item.status in {"queued", "running"}
            ]
        if busy:
            raise RuntimeError(
                "A grid-fix batch is already running. Wait for it to finish "
                "before starting another (a second run can half BPM twice)."
            )

    batch = GridFixBatch(
        id=uuid.uuid4().hex[:12],
        status="queued",
        created_at=_now(),
        total=len(paths),
        apply=apply and not dry_run,
        message=f"Queued {len(paths)} tracks for grid/BPM fix…",
    )
    with _lock:
        _batches[batch.id] = batch

    thread = threading.Thread(
        target=_run_batch,
        args=(batch.id, list(paths), apply, dry_run, allow_vdj_running),
        name=f"grid-fix-{batch.id}",
        daemon=True,
    )
    thread.start()
    if wait:
        thread.join()
    return get_grid_fix_batch(batch.id) or batch


def _update_batch(batch_id: str, **changes: Any) -> None:
    with _lock:
        batch = _batches.get(batch_id)
        if batch is None:
            return
        for key, value in changes.items():
            setattr(batch, key, value)


def _run_batch(
    batch_id: str,
    paths: list[str],
    apply: bool,
    dry_run: bool,
    allow_vdj_running: bool,
) -> None:
    _update_batch(
        batch_id,
        status="running",
        started_at=_now(),
        message=f"Analyzing grids ({len(paths)} tracks)…",
    )
    backup_next = True
    done = 0
    failed = 0
    skipped = 0
    halved = 0
    aligned = 0
    items: list[dict[str, Any]] = []
    cue_index = summarize_cues_for_paths(paths)

    for index, raw_path in enumerate(paths, start=1):
        _update_batch(
            batch_id,
            message=f"Grid fix {index}/{len(paths)}…",
            done=done,
            failed=failed,
            skipped=skipped,
            halved=halved,
            aligned=aligned,
            items=list(items),
        )
        try:
            plan = plan_for_track(raw_path, cues=cue_index.get(raw_path))
            applied: dict[str, Any] = {"ok": True, "action": plan.action}
            if apply and plan.action != "skip":
                applied = apply_grid_fix_plan(
                    plan,
                    dry_run=dry_run,
                    allow_vdj_running=allow_vdj_running,
                    create_backup=backup_next,
                )
                backup_next = False
            applied_action = str(applied.get("action") or plan.action)
            if plan.action == "skip" or applied_action == "skipped_stale":
                skipped += 1
            elif applied_action == "partial":
                done += 1
                failed += 1
                if plan.halve:
                    halved += 1
            else:
                done += 1
                if plan.halve:
                    halved += 1
                if plan.action in {"align", "halve_and_align"}:
                    aligned += 1
            items.append(
                {
                    "path": plan.path,
                    "name": plan.name,
                    "action": plan.action,
                    "reason": plan.reason,
                    "halve": plan.halve,
                    "bpm_before": plan.bpm_before,
                    "bpm_after": plan.bpm_after,
                    "anchor_before": plan.anchor_before,
                    "anchor_after": plan.anchor_after,
                    "shift_beats": plan.shift_beats,
                    "applied": applied.get("ok", False),
                }
            )
        except Exception as exc:
            failed += 1
            items.append(
                {
                    "path": raw_path,
                    "name": Path(raw_path).name,
                    "action": "error",
                    "reason": str(exc),
                    "applied": False,
                }
            )

    status = "error" if failed and not done else "ok"
    summary = (
        f"Grid fix done · {done} changed · {halved} halved · "
        f"{aligned} aligned · {skipped} already ok"
    )
    if failed:
        summary += f" · {failed} failed"
    _update_batch(
        batch_id,
        status=status,
        finished_at=_now(),
        message=summary,
        done=done,
        failed=failed,
        skipped=skipped,
        halved=halved,
        aligned=aligned,
        items=items,
    )
