"""Run vdj-automatic-cuer re-cue on a single track or a batch (background jobs)."""

from __future__ import annotations

import io
import threading
import traceback
import uuid
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv

from .autocue_path import ensure_autocue_on_path
from .config import CUES_ROOT, LIBRARIES, VDJ_DATABASE
from .grid_preflight import assess_grid_for_autocue
from .relocate import is_virtualdj_running, summarize_cues


# Matches vdj_cuer.common WRITE_SCOPE_* values.
WRITE_SCOPE_ALL = "all"
WRITE_SCOPE_CUES = "cues"
WRITE_SCOPE_LOOPS = "loops"
VALID_WRITE_SCOPES = frozenset({WRITE_SCOPE_ALL, WRITE_SCOPE_CUES, WRITE_SCOPE_LOOPS})


def normalize_write_scope(scope: str | None) -> str:
    """Map UI/API aliases to AutoCue write_scope values."""
    raw = (scope or WRITE_SCOPE_ALL).strip().lower()
    aliases = {
        "all": WRITE_SCOPE_ALL,
        "both": WRITE_SCOPE_ALL,
        "cues": WRITE_SCOPE_CUES,
        "cue": WRITE_SCOPE_CUES,
        "cues_only": WRITE_SCOPE_CUES,
        "cues-only": WRITE_SCOPE_CUES,
        "loops": WRITE_SCOPE_LOOPS,
        "loop": WRITE_SCOPE_LOOPS,
        "loops_only": WRITE_SCOPE_LOOPS,
        "loops-only": WRITE_SCOPE_LOOPS,
    }
    value = aliases.get(raw)
    if value is None:
        raise ValueError(
            f"Invalid write_scope {scope!r}; use all/both, cues, or loops"
        )
    return value


def write_scope_label(scope: str) -> str:
    if scope == WRITE_SCOPE_CUES:
        return "cues only"
    if scope == WRITE_SCOPE_LOOPS:
        return "loops only"
    return "cues + loops"


@dataclass
class RetryJob:
    id: str
    path: str
    name: str
    status: str  # queued | running | ok | error | skipped
    dry_run: bool = False
    created_at: str = ""
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    message: str = ""
    log_tail: str = ""
    cue_count_before: int = 0
    cue_count_after: Optional[int] = None
    loop_count_after: Optional[int] = None
    preflight: Optional[dict[str, Any]] = None
    batch_id: Optional[str] = None
    write_scope: str = WRITE_SCOPE_ALL

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BatchJob:
    id: str
    status: str  # queued | running | ok | error
    created_at: str = ""
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    message: str = ""
    total: int = 0
    queued: int = 0
    skipped: int = 0
    done: int = 0
    failed: int = 0
    item_job_ids: list[str] = field(default_factory=list)
    skip_reasons: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        # Attach live child job snapshots.
        with _lock:
            payload["items"] = [
                _jobs[jid].to_dict() for jid in self.item_job_ids if jid in _jobs
            ]
        return payload


_jobs: dict[str, RetryJob] = {}
_batches: dict[str, BatchJob] = {}
_lock = threading.Lock()
# Serialize only the heavy AutoCue process (Gemini + database.xml write).
# Multiple jobs may be started; extras wait here with status "queued".
_active_lock = threading.Lock()
# Max concurrent AutoCue processes. >1 speeds Gemini, but database.xml writes
# inside process_audio_file are not multi-writer-safe — keep at 1 unless write
# path is split. Analysis wait is still non-blocking for job creation/UI.
_MAX_CONCURRENT = 1
_active_sem = threading.Semaphore(_MAX_CONCURRENT)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def get_job(job_id: str) -> Optional[RetryJob]:
    with _lock:
        job = _jobs.get(job_id)
        return job


def get_batch(batch_id: str) -> Optional[BatchJob]:
    with _lock:
        return _batches.get(batch_id)


def list_jobs(limit: int = 20) -> list[dict[str, Any]]:
    with _lock:
        jobs = sorted(_jobs.values(), key=lambda j: j.created_at, reverse=True)
        return [j.to_dict() for j in jobs[:limit]]


def list_batches(limit: int = 10) -> list[dict[str, Any]]:
    with _lock:
        batches = sorted(_batches.values(), key=lambda b: b.created_at, reverse=True)
        return [b.to_dict() for b in batches[:limit]]


def _assert_allowed_path(path: Path) -> Path:
    audio = path.expanduser().resolve()
    if not audio.is_file():
        raise FileNotFoundError(f"Audio not found: {audio}")

    allowed_roots = [CUES_ROOT.resolve(), *[p.resolve() for p in LIBRARIES.values()]]
    for root in allowed_roots:
        try:
            audio.relative_to(root)
            return audio
        except ValueError:
            continue
    raise ValueError(
        "Retry cues is only allowed for files under Cues/ or House/Zouk libraries"
    )


def _has_active_job_for_path(audio_path: str) -> bool:
    target = str(Path(audio_path).expanduser().resolve())
    with _lock:
        for job in _jobs.values():
            if job.status not in {"queued", "running"}:
                continue
            try:
                if str(Path(job.path).expanduser().resolve()) == target:
                    return True
            except Exception:
                if job.path == audio_path or job.path == target:
                    return True
    return False


def start_retry_cues(
    source_path: str | Path,
    *,
    dry_run: bool = False,
    allow_vdj_running: bool = False,
    model_name: Optional[str] = None,
    require_grid: bool = True,
    deep_grid_check: bool = True,
    batch_id: Optional[str] = None,
    write_scope: str = WRITE_SCOPE_ALL,
) -> RetryJob:
    scope = normalize_write_scope(write_scope)
    audio = _assert_allowed_path(Path(source_path))
    before = summarize_cues(audio)

    if _has_active_job_for_path(str(audio)):
        raise RuntimeError(
            f"AutoCue is already running for {audio.name}. Wait for it to finish."
        )

    if is_virtualdj_running() and not dry_run and not allow_vdj_running:
        raise RuntimeError(
            "VirtualDJ is running. Close it before re-cueing, or pass "
            "allow_vdj_running=true (not recommended)."
        )

    preflight = assess_grid_for_autocue(
        audio, deep=deep_grid_check and require_grid, cues=before
    )
    if require_grid and not preflight.get("can_autocue"):
        job = RetryJob(
            id=uuid.uuid4().hex[:12],
            path=str(audio),
            name=audio.name,
            status="skipped",
            dry_run=dry_run,
            created_at=_now(),
            finished_at=_now(),
            cue_count_before=before.cue_count,
            message=preflight.get("label")
            or "Blocked: fix beatgrid in VirtualDJ first",
            preflight=preflight,
            batch_id=batch_id,
            write_scope=scope,
        )
        with _lock:
            _jobs[job.id] = job
        return job

    scope_note = write_scope_label(scope)
    job = RetryJob(
        id=uuid.uuid4().hex[:12],
        path=str(audio),
        name=audio.name,
        status="queued",
        dry_run=dry_run,
        created_at=_now(),
        cue_count_before=before.cue_count,
        message=(
            f"Queued for AutoCue ({scope_note})…"
            + (
                f" · grid: {preflight.get('label')}"
                if preflight.get("needs_align")
                else ""
            )
        ),
        preflight=preflight,
        batch_id=batch_id,
        write_scope=scope,
    )
    with _lock:
        _jobs[job.id] = job

    thread = threading.Thread(
        target=_run_job,
        args=(job.id, dry_run, model_name),
        name=f"autocue-retry-{job.id}",
        daemon=True,
    )
    thread.start()
    return job


def start_batch_retry_cues(
    paths: list[str],
    *,
    dry_run: bool = False,
    allow_vdj_running: bool = False,
    require_grid: bool = True,
    deep_grid_check: bool = False,
    write_scope: str = WRITE_SCOPE_ALL,
) -> BatchJob:
    """
    Queue AutoCue for many tracks. Jobs run one-at-a-time via _active_lock.

    deep_grid_check defaults False for batch (too slow per file); structural
    preflight still blocks tracks without BPM/grid.
    """
    if not paths:
        raise ValueError("No paths provided for batch AutoCue")

    scope = normalize_write_scope(write_scope)

    if is_virtualdj_running() and not dry_run and not allow_vdj_running:
        raise RuntimeError(
            "VirtualDJ is running. Close it before batch AutoCue, or pass "
            "allow_vdj_running=true (not recommended)."
        )

    batch = BatchJob(
        id=uuid.uuid4().hex[:12],
        status="queued",
        created_at=_now(),
        total=len(paths),
        message=f"Queued {len(paths)} tracks ({write_scope_label(scope)})…",
    )
    with _lock:
        _batches[batch.id] = batch

    thread = threading.Thread(
        target=_run_batch,
        args=(
            batch.id,
            list(paths),
            dry_run,
            allow_vdj_running,
            require_grid,
            deep_grid_check,
            scope,
        ),
        name=f"autocue-batch-{batch.id}",
        daemon=True,
    )
    thread.start()
    return batch


def _run_batch(
    batch_id: str,
    paths: list[str],
    dry_run: bool,
    allow_vdj_running: bool,
    require_grid: bool,
    deep_grid_check: bool,
    write_scope: str = WRITE_SCOPE_ALL,
) -> None:
    scope = normalize_write_scope(write_scope)
    _update_batch(
        batch_id,
        status="running",
        started_at=_now(),
        message=f"Running batch ({len(paths)} tracks, {write_scope_label(scope)})…",
    )
    for path in paths:
        try:
            job = start_retry_cues(
                path,
                dry_run=dry_run,
                allow_vdj_running=allow_vdj_running,
                require_grid=require_grid,
                deep_grid_check=deep_grid_check,
                batch_id=batch_id,
                write_scope=scope,
            )
        except Exception as exc:
            with _lock:
                batch = _batches.get(batch_id)
                if batch:
                    batch.failed += 1
                    batch.skip_reasons.append({"path": path, "reason": str(exc)})
            continue

        with _lock:
            batch = _batches.get(batch_id)
            if batch:
                batch.item_job_ids.append(job.id)
                if job.status == "skipped":
                    batch.skipped += 1
                    batch.skip_reasons.append(
                        {
                            "path": job.path,
                            "reason": job.message,
                            "job_id": job.id,
                        }
                    )
                else:
                    batch.queued += 1

        # Wait for this job to finish before starting the next (serialize).
        if job.status == "queued" or job.status == "running":
            while True:
                live = get_job(job.id)
                if live is None or live.status in {"ok", "error", "skipped"}:
                    with _lock:
                        batch = _batches.get(batch_id)
                        if batch and live:
                            if live.status == "ok":
                                batch.done += 1
                            elif live.status == "error":
                                batch.failed += 1
                            batch.message = (
                                f"{batch.done} ok · {batch.failed} failed · "
                                f"{batch.skipped} skipped / {batch.total}"
                            )
                    break
                threading.Event().wait(1.5)

    with _lock:
        batch = _batches.get(batch_id)
        if not batch:
            return
        batch.finished_at = _now()
        if batch.failed and not batch.done:
            batch.status = "error"
        else:
            batch.status = "ok"
        batch.message = (
            f"Batch done · {batch.done} ok · {batch.failed} failed · "
            f"{batch.skipped} skipped / {batch.total}"
        )


def _update_batch(batch_id: str, **fields: Any) -> None:
    with _lock:
        batch = _batches.get(batch_id)
        if not batch:
            return
        for key, value in fields.items():
            setattr(batch, key, value)


def _update_job(job_id: str, **fields: Any) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return
        for key, value in fields.items():
            setattr(job, key, value)


def _run_job(job_id: str, dry_run: bool, model_name: Optional[str]) -> None:
    """
    Run one AutoCue job.

    Multiple jobs may be started at once from the UI. Only
    ``process_audio_file`` is slot-limited (semaphore) so database.xml is not
    multi-written. Waiting jobs stay status=queued with a clear message.
    """
    log_buf = io.StringIO()
    job = get_job(job_id)
    if job is None:
        return

    _update_job(
        job_id,
        status="queued",
        message="Queued — waiting for AutoCue slot…",
    )

    _active_sem.acquire()
    try:
        job = get_job(job_id)
        if job is None:
            return
        audio_path = job.path
        scope = normalize_write_scope(getattr(job, "write_scope", WRITE_SCOPE_ALL))

        _update_job(
            job_id,
            status="running",
            started_at=_now(),
            message=(
                f"Running AutoCue (upload + analysis) · {write_scope_label(scope)}…"
            ),
        )

        try:
            autocue_root = ensure_autocue_on_path()
            load_dotenv(autocue_root / ".env")
            load_dotenv(Path(__file__).resolve().parents[1] / ".env")

            from vdj_cuer import (  # type: ignore
                AutomaticMusicCuer,
                WRITE_SCOPE_ALL as AC_ALL,
                WRITE_SCOPE_CUES as AC_CUES,
                WRITE_SCOPE_LOOPS as AC_LOOPS,
            )

            scope_map = {
                WRITE_SCOPE_ALL: AC_ALL,
                WRITE_SCOPE_CUES: AC_CUES,
                WRITE_SCOPE_LOOPS: AC_LOOPS,
            }

            cuer = AutomaticMusicCuer(
                gemini_api_key=None,  # load from env
                vdj_database_path=str(VDJ_DATABASE),
                model_name=model_name,
            )
            # Keep sorter UI snappy; audits are optional elsewhere.
            cuer.post_cue_audit_enabled = False
            cuer.write_scope = scope_map.get(scope, AC_ALL)

            if not dry_run:
                try:
                    backup = cuer.backup_database()
                    _update_job(
                        job_id,
                        message=(
                            f"Backup created · analyzing {Path(audio_path).name} "
                            f"({write_scope_label(scope)})…"
                        ),
                        log_tail=f"backup: {backup}\nwrite_scope: {scope}\n",
                    )
                except Exception as backup_exc:
                    _update_job(
                        job_id,
                        message=f"Backup warning: {backup_exc} · continuing…",
                    )

            # Prefer surgical analyze → apply path so write_scope (cues/loops/all)
            # is honored. process_audio_file always strips + rewrites both kinds.
            stems_path = Path(f"{audio_path}.vdjstems")
            has_stems = stems_path.is_file()
            with redirect_stdout(log_buf), redirect_stderr(log_buf):
                analysis = cuer.analyze_audio_with_gemini(audio_path)
                ok = False
                warn_msg = ""
                if not analysis:
                    print("❌ Analysis returned no data")
                else:
                    song_length = cuer.get_song_length(audio_path)
                    database_bpm = cuer.get_song_bpm_from_database(audio_path)
                    analysis_bpm = analysis.get("song_structure", {}).get(
                        "bpm", database_bpm or 120
                    )
                    working_bpm = database_bpm or analysis_bpm
                    if hasattr(cuer, "_postprocess_loop_segments"):
                        analysis = cuer._postprocess_loop_segments(
                            analysis, working_bpm, song_length
                        )
                    loop_n = len(analysis.get("loop_segments") or [])
                    cue_n = len(analysis.get("measure_changes") or [])
                    print(
                        f"📋 Scope={scope} · analysis cues={cue_n} "
                        f"loops={loop_n} · stems={'yes' if has_stems else 'NO'}"
                    )
                    if scope in (WRITE_SCOPE_LOOPS, WRITE_SCOPE_ALL) and loop_n == 0:
                        if not has_stems:
                            warn_msg = (
                                "No loops written — AutoCue needs adjacent "
                                f"{Path(audio_path).name}.vdjstems (stems) to "
                                "validate loop seams. Analyze stems in VirtualDJ first."
                            )
                            print(f"⚠️  {warn_msg}")
                        else:
                            warn_msg = (
                                "No loops passed stem/seam validation "
                                "(Gemini/stem gates rejected all candidates)."
                            )
                            print(f"⚠️  {warn_msg}")
                    # Surgical writer honors write_scope (keeps the other kind).
                    ok = bool(
                        cuer._apply_cues_to_database(
                            audio_path, analysis, dry_run=dry_run
                        )
                    )

            log_text = log_buf.getvalue()
            tail = log_text[-4000:] if log_text else ""

            after = summarize_cues(audio_path)
            try:
                from sorter.action_log import append_action
            except Exception:
                append_action = None  # type: ignore

            job = get_job(job_id)
            # warn_msg is set inside the analysis block; default if analysis crashed early.
            if "warn_msg" not in locals():
                warn_msg = ""
            if "has_stems" not in locals():
                has_stems = Path(f"{audio_path}.vdjstems").is_file()

            fail_message = (
                "AutoCue reported failure — check that the track is analyzed "
                "in VirtualDJ and has a beatgrid."
            )
            # Treat loops-only with zero loops as a failure so UI shows why.
            if (
                ok
                and scope == WRITE_SCOPE_LOOPS
                and (after.loop_count or 0) == 0
            ):
                ok = False
                fail_message = warn_msg or (
                    "No loops written. AutoCue needs adjacent .vdjstems "
                    "(stem analysis in VirtualDJ) to validate loop seams."
                )

            if ok:
                msg = (
                    f"AutoCue finished ({write_scope_label(scope)}) · "
                    f"{after.cue_count} cues, {after.loop_count} loops"
                    + (" (dry run)" if dry_run else "")
                )
                if warn_msg and scope == WRITE_SCOPE_ALL and (after.loop_count or 0) == 0:
                    msg += f" · note: {warn_msg}"
                _update_job(
                    job_id,
                    status="ok",
                    finished_at=_now(),
                    message=msg,
                    log_tail=tail,
                    cue_count_after=after.cue_count,
                    loop_count_after=after.loop_count,
                )
                if append_action and not dry_run:
                    append_action(
                        "retry_cues_complete",
                        source_path=audio_path,
                        name=Path(audio_path).name,
                        details={
                            "job_id": job_id,
                            "write_scope": scope,
                            "cue_count_before": job.cue_count_before if job else None,
                            "cue_count_after": after.cue_count,
                            "loop_count_after": after.loop_count,
                            "has_stems": has_stems,
                            "warn": warn_msg or None,
                        },
                    )
            else:
                _update_job(
                    job_id,
                    status="error",
                    finished_at=_now(),
                    message=fail_message,
                    log_tail=tail,
                    cue_count_after=after.cue_count,
                    loop_count_after=after.loop_count,
                )
                if append_action:
                    append_action(
                        "retry_cues_complete",
                        source_path=audio_path,
                        name=Path(audio_path).name,
                        success=False,
                        error=fail_message,
                        details={
                            "job_id": job_id,
                            "write_scope": scope,
                            "has_stems": has_stems,
                        },
                    )
        except Exception as exc:
            tb = traceback.format_exc()
            _update_job(
                job_id,
                status="error",
                finished_at=_now(),
                message=str(exc),
                log_tail=(log_buf.getvalue() + "\n" + tb)[-4000:],
            )
            try:
                from sorter.action_log import append_action

                job = get_job(job_id)
                append_action(
                    "retry_cues_complete",
                    source_path=job.path if job else None,
                    name=job.name if job else None,
                    success=False,
                    error=str(exc),
                    details={"job_id": job_id},
                )
            except Exception:
                pass
    finally:
        _active_sem.release()
