"""Run one AutoCue analyze+apply in an isolated process.

Music Sorter 8787 must not import stem FFT / sklearn / OpenMP into the UI
process. In-process AutoCue held the GIL so /api/tracks appeared frozen.
"""

from __future__ import annotations

import argparse
import json
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Optional

from compute_thread_limits import apply_compute_thread_limits

ANALYSIS_EMPTY_ATTEMPTS = 3
WRITE_SCOPE_ALL = "all"
WRITE_SCOPE_CUES = "cues"
WRITE_SCOPE_LOOPS = "loops"

STEMS_REQUIRED_MESSAGE = (
    "Blocked: analyze stems in VirtualDJ first "
    "(needs adjacent .vdjstems beside the audio)"
)


def load_gemini_api_key() -> None:
    from vdj_cuer.common import load_gemini_api_key as _load

    _load()


def analyze_audio_until_data(
    analyze: Callable[[str], Any],
    audio_path: str,
    *,
    attempts: int = ANALYSIS_EMPTY_ATTEMPTS,
    sleep_fn=time.sleep,
    on_retry=None,
):
    """Call analyze(audio_path) until it returns data or attempts are exhausted."""
    last = None
    total = max(1, int(attempts))
    for attempt in range(1, total + 1):
        last = analyze(audio_path)
        if last:
            return last
        if attempt >= total:
            break
        if on_retry is not None:
            on_retry(attempt, total)
        sleep_fn(min(2 * attempt, 8))
    return last


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="vdj_cuer.autocue_job")
    parser.add_argument("--audio", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--database", default="")
    parser.add_argument("--write-scope", default=WRITE_SCOPE_ALL)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--model", default=None)
    parser.add_argument("--stems-skipped", action="store_true")
    parser.add_argument("--grid-confirmed", action="store_true")
    return parser.parse_args(argv)


def write_result(path: str | Path, payload: dict[str, Any]) -> None:
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(dest)


def _empty_result(**fields: Any) -> dict[str, Any]:
    payload = {
        "ok": False,
        "analysis_empty": True,
        "warn": "",
        "error": "",
        "analysis_cues": 0,
        "analysis_loops": 0,
    }
    payload.update(fields)
    return payload


def _build_cuer(
    *,
    database_path: str,
    model_name: Optional[str],
    write_scope: str,
    stems_skipped: bool,
    grid_confirmed: bool,
    preflight: Optional[dict[str, Any]] = None,
):
    from vdj_cuer import (
        AutomaticMusicCuer,
        WRITE_SCOPE_ALL as AC_ALL,
        WRITE_SCOPE_CUES as AC_CUES,
        WRITE_SCOPE_LOOPS as AC_LOOPS,
    )

    load_gemini_api_key()
    scope_map = {
        WRITE_SCOPE_ALL: AC_ALL,
        WRITE_SCOPE_CUES: AC_CUES,
        WRITE_SCOPE_LOOPS: AC_LOOPS,
    }
    cuer = AutomaticMusicCuer(
        gemini_api_key=None,
        vdj_database_path=database_path,
        model_name=model_name,
    )
    if stems_skipped:
        cuer._beatgrid_mix_only = True
        print("⚠️  Preflight skipped VDJ stems; AutoCue using mix only")
    cuer.post_cue_audit_enabled = False
    cuer.write_scope = scope_map.get(write_scope, AC_ALL)
    cuer.grid_preflight = preflight
    cuer.grid_confirmed = grid_confirmed
    return cuer


def run_one(
    audio_path: str,
    *,
    database_path: str = "",
    write_scope: str = WRITE_SCOPE_ALL,
    dry_run: bool = False,
    model_name: Optional[str] = None,
    stems_skipped: bool = False,
    grid_confirmed: bool = False,
    preflight: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Analyze one file and apply cues. Safe to run in a child process."""
    apply_compute_thread_limits()
    audio = Path(audio_path).expanduser()
    if not audio.is_file():
        return _empty_result(error=f"Audio not found: {audio}")
    db = database_path or str(
        Path.home() / "Library" / "Application Support" / "VirtualDJ" / "database.xml"
    )
    stems_path = Path(f"{audio}.vdjstems")
    has_stems = stems_path.is_file()
    if not has_stems:
        print(STEMS_REQUIRED_MESSAGE)
        return _empty_result(error=STEMS_REQUIRED_MESSAGE)

    scope = (write_scope or WRITE_SCOPE_ALL).strip().lower()
    if scope in {"both", "cues_only", "cues-only"}:
        scope = WRITE_SCOPE_ALL if scope == "both" else WRITE_SCOPE_CUES
    if scope in {"loops_only", "loops-only", "loop"}:
        scope = WRITE_SCOPE_LOOPS
    if scope in {"cue"}:
        scope = WRITE_SCOPE_CUES

    cuer = _build_cuer(
        database_path=db,
        model_name=model_name,
        write_scope=scope,
        stems_skipped=stems_skipped,
        grid_confirmed=grid_confirmed,
        preflight=preflight,
    )

    from vdj_cuer.analysis_cache import analyze_with_cache
    from vdj_cuer.beatgrid_sources import run_with_mix_only_stem_failover

    def _on_empty_retry(attempt: int, total: int) -> None:
        print(
            f"❌ Analysis returned no data "
            f"(attempt {attempt}/{total}) — retrying…"
        )

    def _analyze_and_apply():
        analysis_data = analyze_with_cache(
            lambda path: analyze_audio_until_data(
                cuer.analyze_audio_with_gemini,
                path,
                on_retry=_on_empty_retry,
            ),
            str(audio),
            model=getattr(cuer, "model_name", None),
        )
        applied = False
        note = ""
        if not analysis_data:
            print("❌ Analysis returned no data after retries")
            return analysis_data, applied, note
        song_length = cuer.get_song_length(str(audio))
        database_bpm = cuer.get_song_bpm_from_database(str(audio))
        analysis_bpm = analysis_data.get("song_structure", {}).get(
            "bpm", database_bpm or 120
        )
        working_bpm = database_bpm or analysis_bpm
        if hasattr(cuer, "_postprocess_loop_segments"):
            analysis_data = cuer._postprocess_loop_segments(
                analysis_data, working_bpm, song_length
            )
        loop_n = len(analysis_data.get("loop_segments") or [])
        cue_n = len(analysis_data.get("measure_changes") or [])
        print(
            f"📋 Scope={scope} · analysis cues={cue_n} "
            f"loops={loop_n} · stems={'yes' if has_stems else 'NO'}"
        )
        if scope in (WRITE_SCOPE_LOOPS, WRITE_SCOPE_ALL) and loop_n == 0:
            if not has_stems:
                note = (
                    "No loops written — AutoCue needs adjacent "
                    f"{audio.name}.vdjstems (stems) to "
                    "validate loop seams. Analyze stems in VirtualDJ first."
                )
            else:
                note = (
                    "No loops passed stem/seam validation "
                    "(Gemini/stem gates rejected all candidates)."
                )
            print(f"⚠️  {note}")
        print(f"Writing cues to VirtualDJ · {audio.name}…")
        if not dry_run:
            try:
                backup = cuer.backup_database()
                print(f"backup: {backup}")
            except Exception as backup_exc:
                print(f"⚠️  Backup warning: {backup_exc}")
        applied = bool(
            cuer._apply_cues_to_database(
                str(audio), analysis_data, dry_run=dry_run
            )
        )
        return analysis_data, applied, note

    analysis, ok, warn_msg = run_with_mix_only_stem_failover(cuer, _analyze_and_apply)
    cue_n = len((analysis or {}).get("measure_changes") or []) if analysis else 0
    loop_n = len((analysis or {}).get("loop_segments") or []) if analysis else 0
    if not analysis:
        return _empty_result(
            ok=False,
            analysis_empty=True,
            warn=warn_msg or "",
            error="AutoCue analysis returned no data (Gemini error or invalid JSON).",
        )
    return {
        "ok": bool(ok),
        "analysis_empty": False,
        "warn": warn_msg or "",
        "error": "" if ok else (warn_msg or "AutoCue failed while writing cues."),
        "analysis_cues": cue_n,
        "analysis_loops": loop_n,
    }


def main(argv: Optional[list[str]] = None) -> int:
    apply_compute_thread_limits()
    args = parse_args(argv)
    try:
        result = run_one(
            args.audio,
            database_path=args.database,
            write_scope=args.write_scope,
            dry_run=args.dry_run,
            model_name=args.model,
            stems_skipped=args.stems_skipped,
            grid_confirmed=args.grid_confirmed,
        )
    except Exception as exc:
        traceback.print_exc()
        result = _empty_result(error=str(exc))
    write_result(args.result, result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
