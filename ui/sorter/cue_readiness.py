"""Add Cues → Ready rules. Pure policy — no database.xml or file I/O."""

from __future__ import annotations

from typing import Any, Protocol


READY_MIN_CUES = 2
READY_MIN_LOOPS = 2


class CueCounts(Protocol):
    """Minimal cue summary for readiness (CueSummary satisfies this)."""

    in_database: bool
    has_beatgrid: bool
    cue_count: int
    loop_count: int


def vdj_bpm_to_actual(vdj_bpm: float | None) -> float | None:
    """
    Convert VirtualDJ Scan/Tags Bpm values to musical BPM.

    VDJ usually stores beat duration in seconds (e.g. 0.5 → 120 BPM).
    Values already in a musical range (50–220) are returned as-is.
    """
    if vdj_bpm is None or vdj_bpm <= 0:
        return None
    if 50.0 <= vdj_bpm <= 220.0:
        return float(vdj_bpm)
    actual = 60.0 / vdj_bpm
    if 40.0 <= actual <= 240.0:
        return actual
    alt = vdj_bpm * 120.0
    if 40.0 <= alt <= 240.0:
        return alt
    return None


def assess_cue_readiness(cues: CueCounts) -> dict[str, Any]:
    """
    Whether a track looks ready to leave Add Cues → Ready for Sort.
    Ready requires a beatgrid, at least 2 cues, and at least 2 loops.
    """
    checks = {
        "in_database": cues.in_database,
        "has_beatgrid": cues.has_beatgrid,
        "has_cues": cues.cue_count > 0,
        "multiple_cues": cues.cue_count >= READY_MIN_CUES,
        "has_loops": cues.loop_count > 0,
        "multiple_loops": cues.loop_count >= READY_MIN_LOOPS,
    }
    if not cues.in_database:
        status = "missing"
        label = "Missing from VDJ"
        ready = False
    elif cues.cue_count <= 0:
        status = "not_cued"
        label = "Not cued yet"
        ready = False
    elif (
        cues.has_beatgrid
        and cues.cue_count >= READY_MIN_CUES
        and cues.loop_count >= READY_MIN_LOOPS
    ):
        status = "ready"
        label = "Looks ready"
        ready = True
    elif cues.cue_count >= READY_MIN_CUES and cues.loop_count < READY_MIN_LOOPS:
        status = "partial"
        label = "Cued — needs 2 loops"
        ready = False
    elif cues.cue_count >= 1:
        status = "partial"
        label = "Partially cued — review"
        ready = False
    else:
        status = "not_cued"
        label = "Not cued yet"
        ready = False

    return {
        "status": status,
        "label": label,
        "ready": ready,
        "checks": checks,
        "summary": (
            f"{cues.cue_count} cues · {cues.loop_count} loops"
            + (" · beatgrid" if cues.has_beatgrid else " · no beatgrid")
        ),
    }
