"""
Pre-flight beatgrid checks before shipping a track to AutoCue.

AutoCue requires a valid VDJ BPM and a usable downbeat ("1"). Misaligned grids
produce cues that snap to the wrong bar. This module classifies:

  - can_autocue: structural preconditions met (in DB + usable BPM + grid anchor)
  - needs_align: evidence the grid "1" is off (fixable by AutoCue or VDJ)
  - manual_required: not safe/possible to AutoCue until fixed in VirtualDJ
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from .autocue_path import ensure_autocue_on_path
from .config import VDJ_DATABASE
from .relocate import CueSummary, summarize_cues

# AutoCue's _actual_bpm window is 50–200 musical BPM (slow zouk / half-time).
MIN_BPM = 50.0
MAX_BPM = 200.0
# Common double-time zone (VDJ often reports 140 when the music is ~70).
DOUBLE_TIME_LOW = 128.0
DOUBLE_TIME_HIGH = 155.0
# Phase vs beatgrid POI disagreement (seconds) — matches vdj_audit threshold.
PHASE_POI_TOLERANCE = 0.02
# Deep verify: require this confidence ratio to claim a clear misalignment.
ALIGN_CONFIDENCE = 1.5


def _bpm_ok(bpm: Optional[float]) -> bool:
    return bpm is not None and MIN_BPM <= bpm <= MAX_BPM


def _grid_anchor(cues: CueSummary) -> Optional[float]:
    """Best-known downbeat origin: Scan Phase (VDJ's live 1), else beatgrid POI."""
    if cues.scan_phase is not None:
        return float(cues.scan_phase)
    if cues.has_beatgrid and cues.beatgrid_pos is not None:
        return float(cues.beatgrid_pos)
    return None


def _base_from_cues(cues: CueSummary, path_str: str) -> dict[str, Any]:
    issues: list[str] = []
    warnings: list[str] = []
    can_autocue = True
    needs_align = False
    manual_required = False
    status = "ok"
    label = "Grid OK for AutoCue"

    if not cues.in_database:
        return {
            "path": path_str,
            "status": "blocked",
            "label": "Not in VDJ database",
            "can_autocue": False,
            "needs_align": False,
            "manual_required": True,
            "manual_confirmable": False,
            "issues": [
                "Track is missing from VirtualDJ database.xml — open it in VDJ "
                "so it can analyze BPM/beatgrid first."
            ],
            "warnings": [],
            "bpm": None,
            "has_beatgrid": False,
            "beatgrid_pos": None,
            "scan_phase": None,
            "grid_anchor": None,
        }

    bpm = cues.bpm
    if not _bpm_ok(bpm):
        can_autocue = False
        manual_required = True
        status = "blocked"
        label = "No usable BPM"
        issues.append(
            "VirtualDJ has no usable BPM (need ~50–200). Analyze the track in VDJ "
            "before AutoCue."
        )

    anchor = _grid_anchor(cues)
    if anchor is None and can_autocue:
        can_autocue = False
        manual_required = True
        status = "blocked"
        label = "No beatgrid / Phase"
        issues.append(
            "No beatgrid POI and no Scan Phase — AutoCue cannot lock a downbeat. "
            "In VirtualDJ: play the track, set the grid '1', then re-try."
        )

    if (
        cues.has_beatgrid
        and cues.beatgrid_pos is not None
        and cues.scan_phase is not None
        and abs(float(cues.beatgrid_pos) - float(cues.scan_phase)) > PHASE_POI_TOLERANCE
    ):
        needs_align = True
        if status == "ok":
            status = "fixable"
            label = "Phase ≠ beatgrid"
        warnings.append(
            f"Scan Phase ({cues.scan_phase:.3f}s) disagrees with beatgrid POI "
            f"({cues.beatgrid_pos:.3f}s) — VDJ may draw the wrong '1'. AutoCue can "
            "often correct this when it writes cues."
        )

    suggest_halve_bpm = False
    if bpm is not None and DOUBLE_TIME_LOW <= bpm <= DOUBLE_TIME_HIGH:
        if status == "ok":
            status = "warn"
            label = "Possible double-time BPM"
        suggest_halve_bpm = True
        warnings.append(
            f"VDJ BPM is {bpm:.0f} (common double-time zone). If the track is "
            f"really ~{bpm / 2:.0f}, use Halve BPM in the UI (or fix in VirtualDJ) "
            "before AutoCue — otherwise cues snap at the wrong period."
        )

    if status == "blocked":
        can_autocue = False

    return {
        "path": path_str,
        "status": status,
        "label": label,
        "can_autocue": can_autocue,
        "needs_align": needs_align,
        "manual_required": manual_required,
        # True only when deep onset fails but structural grid/BPM exist (see assess_grid).
        "manual_confirmable": False,
        "issues": issues,
        "warnings": warnings,
        "bpm": bpm,
        "suggest_halve_bpm": suggest_halve_bpm,
        "halved_bpm": (bpm / 2.0) if bpm and suggest_halve_bpm else None,
        "has_beatgrid": cues.has_beatgrid,
        "beatgrid_pos": cues.beatgrid_pos,
        "scan_phase": cues.scan_phase,
        "grid_anchor": anchor,
    }


def assess_grid_for_autocue(
    audio_path: str | Path,
    *,
    deep: bool = False,
    cues: CueSummary | None = None,
) -> dict[str, Any]:
    """
    Return a preflight assessment for AutoCue readiness.

    Fast path (deep=False): VDJ database structure only.
    Deep path (deep=True): also runs AutoCue onset-based downbeat verification
    (ffmpeg + kick/mix analysis) — slower; use for current track / pre-submit.
    """
    path = Path(audio_path).expanduser()
    path_str = str(path.resolve()) if path.exists() else str(path)

    if cues is None:
        if not path.is_file():
            return {
                "path": path_str,
                "status": "blocked",
                "label": "File missing",
                "can_autocue": False,
                "needs_align": False,
                "manual_required": True,
                "manual_confirmable": False,
                "issues": ["Audio file not found on disk"],
                "warnings": [],
                "bpm": None,
                "has_beatgrid": False,
                "beatgrid_pos": None,
                "scan_phase": None,
                "grid_anchor": None,
                "alignment": None,
                "deep": deep,
            }
        cues = summarize_cues(path)

    base = _base_from_cues(cues, path_str)
    base["alignment"] = None
    base["deep"] = deep

    if not (deep and base["can_autocue"] and base["bpm"] is not None and path.is_file()):
        return base

    alignment = _deep_verify_alignment(str(path.resolve()), float(base["bpm"]))
    base["alignment"] = alignment

    if alignment.get("error"):
        if base["status"] in {"ok", "warn"}:
            base["status"] = "warn"
            base["label"] = "Grid check incomplete"
        base["warnings"].append(
            f"Could not deeply verify beatgrid: {alignment['error']}. "
            "Structural checks still apply; listen to the VDJ grid before trusting AutoCue."
        )
        return base

    if not alignment.get("verified"):
        return base

    conf = float(alignment.get("confidence_ratio") or 0)
    corrected = bool(alignment.get("corrected"))
    if corrected and conf >= ALIGN_CONFIDENCE:
        base["needs_align"] = True
        if base["status"] != "blocked":
            base["status"] = "fixable"
            base["label"] = "Grid likely misaligned"
        base["warnings"].append(
            f"Onset analysis suggests the downbeat is off by "
            f"{alignment.get('shift_beats', 0)} beat(s) "
            f"({alignment.get('fine_shift_seconds', 0):+.3f}s fine), "
            f"confidence {conf:.1f}×. If the 1 already sounds right, leave it. "
            "AutoCue will not move the grid. Auto-align is only a preview."
        )
    elif corrected and conf < ALIGN_CONFIDENCE:
        base["needs_align"] = True
        if base["status"] not in {"blocked", "fixable"}:
            base["status"] = "warn"
            base["label"] = "Ambiguous grid"
        base["warnings"].append(
            "Weak evidence of grid misalignment — AutoCue will keep the "
            "current VDJ grid. Use Align grid if the '1' sounds wrong."
        )
    elif conf < 1.05 and float(alignment.get("best_beat_score") or 0) < 0.02:
        # Structural grid exists (BPM + anchor); deep onset just cannot verify.
        # User may confirm the VDJ grid manually and proceed with AutoCue.
        base["needs_align"] = True
        base["can_autocue"] = False
        base["manual_required"] = True
        base["manual_confirmable"] = True
        base["status"] = "blocked"
        base["label"] = "Cannot verify grid"
        base["issues"].append(
            "Onset energy is too weak to verify the beatgrid (sparse kick / "
            "ambient intro). Set the grid manually in VirtualDJ, then confirm "
            "it here or AutoCue with 'Grid is correct'."
        )
    else:
        if base["status"] == "ok":
            base["label"] = "Grid verified"
        base["warnings"].append(
            f"Downbeat looks usable at {alignment.get('offset', 0):.3f}s "
            f"({alignment.get('source', 'audio')})."
        )

    return base


def _deep_verify_alignment(audio_path: str, bpm: float) -> dict[str, Any]:
    """Run AutoCue's onset-based beatgrid verification."""
    try:
        ensure_autocue_on_path()
        from dotenv import load_dotenv

        ui_root = Path(__file__).resolve().parents[1]  # ui/
        repo_root = Path(__file__).resolve().parents[2]  # vdj-automatic-cuer/
        load_dotenv(ui_root / ".env", override=False)
        load_dotenv(repo_root / ".env", override=False)
        load_dotenv(
            Path.home() / "Desktop" / "vdj-automatic-cuer" / ".env", override=False
        )
        try:
            from vdj_cuer.common import load_gemini_api_key  # type: ignore

            load_gemini_api_key()
        except Exception:
            pass

        from vdj_cuer import AutomaticMusicCuer  # type: ignore

        cuer = AutomaticMusicCuer(
            gemini_api_key=None,
            vdj_database_path=str(VDJ_DATABASE),
        )
        result = cuer._verify_beatgrid_alignment(audio_path, bpm)
        try:
            cuer._release_track_resources(audio_path)
        except Exception:
            pass
        return {
            "verified": True,
            "offset": result.offset,
            "corrected": result.corrected,
            "shift_beats": result.shift_beats,
            "fine_shift_seconds": result.fine_shift_seconds,
            "confidence_ratio": result.confidence_ratio,
            "source": result.source,
            "beat_score": result.beat_score,
            "best_beat_score": result.best_beat_score,
            "error": None,
        }
    except Exception as exc:
        return {
            "verified": False,
            "error": str(exc),
        }


def preflight_from_cues(cues: CueSummary, path: str = "") -> dict[str, Any]:
    """List-row badge payload from an already-loaded CueSummary (fast)."""
    base = _base_from_cues(cues, path)
    base["alignment"] = None
    base["deep"] = False
    return base
