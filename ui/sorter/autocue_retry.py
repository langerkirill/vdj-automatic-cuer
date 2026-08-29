"""Run vdj-automatic-cuer re-cue on a single track or a batch (background jobs)."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .autocue_path import ensure_autocue_on_path
from .config import CUES_ROOT, DJ_NOTES_ROOT, LIBRARIES, SETS_ROOT, VDJ_DATABASE
from .action_log import DEFAULT_LOG_PATH
from .db_lock import get_db_write_lock
from .grid_preflight import assess_grid_for_autocue
from .ml_training import schedule_training_update
from .relocate import is_virtualdj_running, summarize_cues, summarize_cues_for_paths


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


ANALYSIS_EMPTY_ATTEMPTS = 3


def analyze_audio_until_data(
    analyze,
    audio_path: str,
    *,
    attempts: int = ANALYSIS_EMPTY_ATTEMPTS,
    sleep_fn=time.sleep,
    on_retry=None,
):
    """
    Call analyze(audio_path) until it returns data or attempts are exhausted.

    Gemini sometimes returns empty/invalid JSON after inner API retries.
    A second full pass (fresh upload) often succeeds — Turn me On did.
    """
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


def autocue_fail_message(
    log_text: str,
    *,
    analysis_empty: bool = False,
    warn_msg: str = "",
) -> str:
    """Prefer the last real AutoCue error over the generic beatgrid sentence."""
    for line in reversed((log_text or "").splitlines()):
        text = line.strip()
        if not text:
            continue
        if text.startswith("❌"):
            return text.lstrip("❌ ").strip()
        if "VirtualDJ is running" in text:
            return text
        if "Error applying cues" in text or "Error analyzing audio" in text:
            return text
    if analysis_empty:
        return "AutoCue analysis returned no data (Gemini error or invalid JSON)."
    if warn_msg:
        return warn_msg
    return "AutoCue failed while writing cues (not a missing-beatgrid check)."


def maybe_ingest_after_autocue(
    audio_path: str | Path,
    after: Any,
    *,
    dry_run: bool,
    ok: bool,
) -> None:
    if not ok or dry_run or after is None:
        return
    if not (getattr(after, "cue_count", 0) or getattr(after, "loop_count", 0)):
        return
    try:
        from vdj_cuer.ml.match import save_written_autocue_points
        save_written_autocue_points(audio_path, after)
    except Exception:
        pass
    schedule_training_update(audio_path, after)


RETRY_HISTORY_ACTIONS = frozenset({"retry_cues", "retry_cues_complete"})
_retry_history_cache: dict[str, tuple[int, int, dict[str, dict[str, Any]]]] = {}


def _retry_path_keys(path: str | Path) -> list[str]:
    raw = str(path or "").strip()
    if not raw:
        return []
    keys = [raw]
    try:
        keys.append(str(Path(raw).expanduser()))
    except Exception:
        pass
    try:
        keys.append(str(Path(raw).expanduser().resolve()))
    except Exception:
        pass
    # Unique, keep order
    seen: set[str] = set()
    out: list[str] = []
    for key in keys:
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return out


def _scope_from_action(row: dict[str, Any]) -> Optional[str]:
    details = row.get("details") or {}
    raw = details.get("write_scope") or details.get("writeScope")
    if not raw:
        return None
    try:
        return normalize_write_scope(str(raw))
    except ValueError:
        return None


def _kind_from_tried(*, tried_cues: bool, tried_loops: bool) -> Optional[str]:
    if tried_cues and tried_loops:
        return "both"
    if tried_cues:
        return "cues"
    if tried_loops:
        return "loops"
    return None


def summarize_retry_history(
    *,
    log_file: Path | None = None,
) -> dict[str, dict[str, Any]]:
    """
    Map audio path → AutoCue retry buckets from the durable action log.

    kind is exclusive: cues | loops | both
    (both = one all/both run, or separate cues + loops runs).
    """
    path = Path(log_file) if log_file else DEFAULT_LOG_PATH
    cache_key = str(path)
    mtime = 0
    size = 0
    if path.is_file():
        stat = path.stat()
        mtime = int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1e9)))
        size = int(stat.st_size)
    hit = _retry_history_cache.get(cache_key)
    if hit and hit[0] == mtime and hit[1] == size:
        return hit[2]

    acc: dict[str, dict[str, Any]] = {}
    if path.is_file():
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("action") not in RETRY_HISTORY_ACTIONS:
                    continue
                source = row.get("source_path")
                if not source:
                    continue
                scope = _scope_from_action(row)
                if scope is None:
                    continue
                keys = _retry_path_keys(source)
                if not keys:
                    continue
                primary = keys[-1]
                entry = acc.get(primary)
                if entry is None:
                    entry = {
                        "path": primary,
                        "tried_cues": False,
                        "tried_loops": False,
                        "scopes": [],
                        "last_ts": None,
                    }
                    acc[primary] = entry
                scopes = set(entry["scopes"])
                scopes.add(scope)
                if scope in {WRITE_SCOPE_CUES, WRITE_SCOPE_ALL}:
                    entry["tried_cues"] = True
                if scope in {WRITE_SCOPE_LOOPS, WRITE_SCOPE_ALL}:
                    entry["tried_loops"] = True
                entry["scopes"] = sorted(scopes)
                ts = row.get("ts")
                if ts:
                    entry["last_ts"] = ts
                for key in keys:
                    acc[key] = entry

    for entry in acc.values():
        kind = _kind_from_tried(
            tried_cues=bool(entry["tried_cues"]),
            tried_loops=bool(entry["tried_loops"]),
        )
        entry["kind"] = kind
        entry["tried_both"] = kind == "both"

    _retry_history_cache[cache_key] = (mtime, size, acc)
    return acc


def retry_history_for_path(
    path: str | Path,
    history: dict[str, dict[str, Any]] | None = None,
) -> Optional[dict[str, Any]]:
    hist = history if history is not None else summarize_retry_history()
    for key in _retry_path_keys(path):
        hit = hist.get(key)
        if hit:
            return hit
    return None


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
    model_name: Optional[str] = None
    worker_pid: Optional[int] = None

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
        with _lock:
            live = [_jobs[jid] for jid in self.item_job_ids if jid in _jobs]
        active = [job.to_dict() for job in live if job.status in {"queued", "running"}]
        payload["items"] = active
        payload["active_count"] = len(active)
        return payload


_jobs: dict[str, RetryJob] = {}
_batches: dict[str, BatchJob] = {}
# Re-entrant: list_batches / to_dict may snapshot while a caller already holds it.
# A plain Lock here deadlocked GET /api/retry-cues once a batch existed (to_dict
# re-entered _lock while list_batches held it), which then froze _update_job and
# blocked database writes after Gemini returned.
_lock = threading.RLock()
# Shared with cue edit / sort / notes so concurrent RMW never clobber database.xml.
_db_write_lock = get_db_write_lock()
# How many AutoCue analyses may run in parallel (upload + Gemini).
# Default 1 so the sorter UI stays responsive; override with
# MUSIC_SORTER_AUTOCUE_CONCURRENCY. Hard cap 3 — 5× 100MB stems froze 8787.
# database.xml applies in the AutoCue child go through vdj_database_exclusive_lock.
def _parse_max_concurrent() -> int:
    raw = (os.environ.get("MUSIC_SORTER_AUTOCUE_CONCURRENCY") or "1").strip()
    try:
        n = int(raw)
    except ValueError:
        n = 1
    return max(1, min(n, 3))


_MAX_CONCURRENT = _parse_max_concurrent()
_active_sem = threading.Semaphore(_MAX_CONCURRENT)

# Survives 8787 restart / UI refresh. Running jobs resume their worker.
JOB_STORE_PATH = DJ_NOTES_ROOT / "autocue_jobs.json"
_ACTIVE_JOB_STATUSES = frozenset({"queued", "running", "starting"})


def persist_jobs(store_path: Path | None = None) -> None:
    """Write the in-memory AutoCue job store to disk."""
    store = Path(store_path or JOB_STORE_PATH)
    with _lock:
        payload = {
            "version": 1,
            "saved_at": _now(),
            "jobs": [job.to_dict() for job in _jobs.values()],
            "batches": [asdict(batch) for batch in _batches.values()],
        }
    try:
        store.parent.mkdir(parents=True, exist_ok=True)
        tmp = store.with_suffix(store.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        tmp.replace(store)
    except OSError as exc:
        print(f"⚠️  AutoCue job persist failed: {exc}")


def _job_from_dict(raw: dict[str, Any]) -> Optional[RetryJob]:
    if not isinstance(raw, dict) or not raw.get("id"):
        return None
    known = {f.name for f in fields(RetryJob)}
    return RetryJob(**{key: raw[key] for key in known if key in raw})


def _batch_from_dict(raw: dict[str, Any]) -> Optional[BatchJob]:
    if not isinstance(raw, dict) or not raw.get("id"):
        return None
    known = {f.name for f in fields(BatchJob)}
    data = {key: raw[key] for key in known if key in raw}
    return BatchJob(**data)


def restore_jobs(
    store_path: Path | None = None,
    *,
    resume: bool = True,
) -> int:
    """Load persisted jobs. Re-spawn workers for queued/running. Returns resume count."""
    store = Path(store_path or JOB_STORE_PATH)
    if not store.is_file():
        return 0
    try:
        data = json.loads(store.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    if not isinstance(data, dict):
        return 0
    loaded = 0
    with _lock:
        for raw in data.get("jobs") or []:
            job = _job_from_dict(raw) if isinstance(raw, dict) else None
            if job is None:
                continue
            _jobs[job.id] = job
            loaded += 1
        for raw in data.get("batches") or []:
            batch = _batch_from_dict(raw) if isinstance(raw, dict) else None
            if batch is None:
                continue
            _batches[batch.id] = batch
    if loaded:
        persist_jobs(store)
    if not resume:
        return 0
    return resume_active_jobs()


def resume_active_jobs() -> int:
    """Start workers for persisted queued/running jobs. Does not create new job ids."""
    with _lock:
        ids = [
            job.id
            for job in _jobs.values()
            if job.status in _ACTIVE_JOB_STATUSES
        ]
    resumed = 0
    for job_id in ids:
        job = get_job(job_id)
        if job is None:
            continue
        if not Path(job.path).expanduser().is_file():
            _update_job(
                job_id,
                status="error",
                finished_at=_now(),
                worker_pid=None,
                message="Resumed job: audio file missing",
            )
            continue
        live_pid = getattr(job, "worker_pid", None)
        if isinstance(live_pid, int) and live_pid > 0 and _pid_is_alive(live_pid):
            _update_job(
                job_id,
                status="running",
                message=f"Reattached to AutoCue process {live_pid} after restart…",
            )
            threading.Thread(
                target=_reap_orphaned_worker,
                args=(job_id, live_pid),
                name=f"autocue-reap-{job_id}",
                daemon=True,
            ).start()
            resumed += 1
            continue
        _update_job(
            job_id,
            status="queued",
            started_at=None,
            finished_at=None,
            worker_pid=None,
            message="Resumed after restart — waiting for AutoCue slot…",
        )
        threading.Thread(
            target=_run_job,
            args=(job_id, job.dry_run, getattr(job, "model_name", None)),
            name=f"autocue-retry-{job_id}",
            daemon=True,
        ).start()
        resumed += 1
    return resumed


def _pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    except Exception:
        return False
    return True


def _reap_orphaned_worker(job_id: str, pid: int) -> None:
    """Wait for a child that outlived 8787 instead of starting a second AutoCue."""
    _active_sem.acquire()
    try:
        while _pid_is_alive(pid):
            time.sleep(1.5)
        job = get_job(job_id)
        if job is None:
            return
        after = summarize_cues(job.path)
        cues = int(getattr(after, "cue_count", 0) or 0)
        loops = int(getattr(after, "loop_count", 0) or 0)
        if cues or loops:
            _update_job(
                job_id,
                status="ok",
                finished_at=_now(),
                worker_pid=None,
                cue_count_after=cues,
                loop_count_after=loops,
                message=(
                    f"AutoCue process {pid} finished after restart · "
                    f"{cues} cues, {loops} loops"
                ),
            )
            maybe_ingest_after_autocue(job.path, after, dry_run=job.dry_run, ok=True)
            return
        _update_job(
            job_id,
            status="error",
            finished_at=_now(),
            worker_pid=None,
            message=f"AutoCue process {pid} exited; cues not confirmed",
        )
    finally:
        _active_sem.release()



def max_concurrent_jobs() -> int:
    return _MAX_CONCURRENT


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
        selected = batches[:limit]
    return [batch.to_dict() for batch in selected]


def _assert_allowed_path(path: Path) -> Path:
    audio = path.expanduser().resolve()
    if not audio.is_file():
        raise FileNotFoundError(f"Audio not found: {audio}")

    allowed_roots = [
        CUES_ROOT.resolve(),
        SETS_ROOT.resolve(),
        *[p.resolve() for p in LIBRARIES.values()],
    ]
    for root in allowed_roots:
        try:
            audio.relative_to(root)
            return audio
        except ValueError:
            continue
    raise ValueError(
        "Retry cues is only allowed for files under Cues/, Sets/, or House/Zouk"
    )


STEMS_REQUIRED_MESSAGE = (
    "Blocked: analyze stems in VirtualDJ first "
    "(needs adjacent .vdjstems beside the audio)"
)



def apply_preflight_stem_failover(cuer, preflight):
    """Honor preflight mix-only failover so AutoCue does not reuse a broken stem map."""
    if preflight and preflight.get("stems_skipped"):
        cuer._beatgrid_mix_only = True
        print("⚠️  Preflight skipped VDJ stems; AutoCue using mix only")
        return True
    return False


def adjacent_vdj_stems(audio: str | Path) -> Optional[Path]:
    """Sidecar VirtualDJ writes next to the audio file, or None."""
    stems = Path(f"{audio}.vdjstems")
    return stems if stems.is_file() else None


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
    cues_before: Optional[Any] = None,
    require_stems: bool = True,
) -> RetryJob:
    scope = normalize_write_scope(write_scope)
    audio = _assert_allowed_path(Path(source_path))
    before = cues_before if cues_before is not None else summarize_cues(audio)

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
    stems = adjacent_vdj_stems(audio)
    if isinstance(preflight, dict):
        preflight = {**preflight, "has_stems": stems is not None}
    if require_stems and stems is None:
        job = RetryJob(
            id=uuid.uuid4().hex[:12],
            path=str(audio),
            name=audio.name,
            status="skipped",
            dry_run=dry_run,
            created_at=_now(),
            finished_at=_now(),
            cue_count_before=before.cue_count,
            message=STEMS_REQUIRED_MESSAGE,
            preflight=preflight,
            batch_id=batch_id,
            write_scope=scope,
        )
        with _lock:
            _jobs[job.id] = job
        persist_jobs()
        return job
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
        persist_jobs()
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
        model_name=model_name,
    )
    with _lock:
        _jobs[job.id] = job
    persist_jobs()

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
    model_name: Optional[str] = None,
) -> BatchJob:
    """
    Queue AutoCue for many tracks. Up to MUSIC_SORTER_AUTOCUE_CONCURRENCY
    analyses run in parallel; database writes are serialized.

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
    persist_jobs()

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
            model_name,
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
    model_name: Optional[str] = None,
) -> None:
    scope = normalize_write_scope(write_scope)
    start_errors = 0
    _update_batch(
        batch_id,
        status="running",
        started_at=_now(),
        message=(
            f"Running batch ({len(paths)} tracks, {write_scope_label(scope)}, "
            f"up to {_MAX_CONCURRENT} concurrent)…"
        ),
    )
    summaries = summarize_cues_for_paths(paths)
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
                cues_before=summaries.get(path),
                model_name=model_name,
            )
        except Exception as exc:
            start_errors += 1
            with _lock:
                batch = _batches.get(batch_id)
                if batch:
                    batch.skip_reasons.append({"path": path, "reason": str(exc)})
            continue

        with _lock:
            batch = _batches.get(batch_id)
            if batch:
                batch.item_job_ids.append(job.id)
                if job.status == "skipped":
                    batch.skip_reasons.append(
                        {
                            "path": job.path,
                            "reason": job.message,
                            "job_id": job.id,
                        }
                    )
                else:
                    batch.queued += 1

        # Do not wait here — start jobs; _active_sem limits parallel analysis.

    # Wait until every non-skipped child finishes (up to N run in parallel).
    while True:
        with _lock:
            batch = _batches.get(batch_id)
            if not batch:
                return
            ids = list(batch.item_job_ids)
        done = 0
        failed = 0
        skipped = 0
        still_active = 0
        for jid in ids:
            live = get_job(jid)
            if live is None:
                continue
            if live.status == "ok":
                done += 1
            elif live.status == "error":
                failed += 1
            elif live.status == "skipped":
                skipped += 1
            elif live.status in {"queued", "running"}:
                still_active += 1
        failed_total = failed + start_errors
        with _lock:
            batch = _batches.get(batch_id)
            if batch:
                batch.done = done
                batch.failed = failed_total
                batch.skipped = skipped
                batch.message = (
                    f"{done} ok · {failed_total} failed · {skipped} skipped / "
                    f"{batch.total}"
                    + (f" · {still_active} running" if still_active else "")
                )
        if still_active == 0:
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
    persist_jobs()


def _update_batch(batch_id: str, **fields: Any) -> None:
    with _lock:
        batch = _batches.get(batch_id)
        if not batch:
            return
        for key, value in fields.items():
            setattr(batch, key, value)
    persist_jobs()


def _update_job(job_id: str, persist: bool = True, **fields: Any) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return
        for key, value in fields.items():
            setattr(job, key, value)
    if persist:
        persist_jobs()


def autocue_job_argv(
    *,
    audio: str,
    result_path: str,
    database: str,
    write_scope: str,
    dry_run: bool = False,
    model_name: Optional[str] = None,
    stems_skipped: bool = False,
    grid_confirmed: bool = False,
) -> list[str]:
    cmd = [
        sys.executable,
        "-m",
        "vdj_cuer.autocue_job",
        "--audio",
        audio,
        "--database",
        database,
        "--write-scope",
        write_scope,
        "--result",
        result_path,
    ]
    if dry_run:
        cmd.append("--dry-run")
    if model_name:
        cmd.extend(["--model", model_name])
    if stems_skipped:
        cmd.append("--stems-skipped")
    if grid_confirmed:
        cmd.append("--grid-confirmed")
    return cmd


def autocue_subprocess_env() -> dict[str, str]:
    root = str(ensure_autocue_on_path())
    try:
        from compute_thread_limits import env_with_compute_thread_limits

        env = env_with_compute_thread_limits(os.environ)
    except Exception:
        env = os.environ.copy()
        env.setdefault("OMP_NUM_THREADS", "1")
        env["PYTHONUNBUFFERED"] = "1"
    pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = root if not pythonpath else f"{root}{os.pathsep}{pythonpath}"
    return env


def _run_job(job_id: str, dry_run: bool, model_name: Optional[str]) -> None:
    """
    Run one AutoCue job in an isolated subprocess.

    The UI process only waits on Popen (GIL released). Stem FFT / sklearn
    stay in the child. ``_active_sem`` still limits how many children run.
    Waiting jobs stay status=queued with a clear message.
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
        if adjacent_vdj_stems(audio_path) is None:
            _update_job(
                job_id,
                status="skipped",
                finished_at=_now(),
                message=STEMS_REQUIRED_MESSAGE,
            )
            return

        _update_job(
            job_id,
            status="running",
            started_at=_now(),
            message=(
                f"Running AutoCue (upload + analysis) · {write_scope_label(scope)}…"
            ),
        )

        try:
            # Isolated child: stem FFT / sklearn / OpenMP must not enter 8787.
            has_stems = Path(f"{audio_path}.vdjstems").is_file()
            preflight = getattr(job, "preflight", None) or {}
            label = str(preflight.get("label") or "")
            result_file: Optional[Path] = None
            proc: Optional[subprocess.Popen[str]] = None
            analysis: Any = None
            ok = False
            warn_msg = ""
            payload: dict[str, Any] = {}
            try:
                fd, result_name = tempfile.mkstemp(
                    prefix=f"autocue-{job_id}-", suffix=".json"
                )
                os.close(fd)
                result_file = Path(result_name)
                argv = autocue_job_argv(
                    audio=audio_path,
                    result_path=str(result_file),
                    database=str(VDJ_DATABASE),
                    write_scope=scope,
                    dry_run=dry_run,
                    model_name=model_name or getattr(job, "model_name", None),
                    stems_skipped=bool(preflight.get("stems_skipped")),
                    grid_confirmed=label.startswith("Grid manually confirmed"),
                )
                _update_job(
                    job_id,
                    message=(
                        f"Analyzing {Path(audio_path).name} "
                        f"({write_scope_label(scope)}; isolated process)…"
                    ),
                    log_tail=(
                        f"write_scope: {scope}\n"
                        f"concurrency: {_MAX_CONCURRENT}\n"
                    ),
                )
                proc = subprocess.Popen(
                    argv,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    env=autocue_subprocess_env(),
                    cwd=str(ensure_autocue_on_path()),
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                )
                _update_job(job_id, worker_pid=proc.pid)
                last_persist = 0.0
                assert proc.stdout is not None
                for line in proc.stdout:
                    log_buf.write(line)
                    stripped = line.strip()
                    now = time.monotonic()
                    persist = (now - last_persist) >= 2.0
                    fields: dict[str, Any] = {
                        "log_tail": log_buf.getvalue()[-4000:]
                    }
                    if stripped.startswith(
                        ("❌", "⚠️", "📋", "🎚️", "Writing ")
                    ):
                        fields["message"] = stripped[:240]
                    _update_job(job_id, persist=persist, **fields)
                    if persist:
                        last_persist = now
                proc.wait()
                if result_file.is_file():
                    try:
                        loaded = json.loads(
                            result_file.read_text(encoding="utf-8")
                        )
                        if isinstance(loaded, dict):
                            payload = loaded
                    except (OSError, json.JSONDecodeError):
                        payload = {}
                warn_msg = str(payload.get("warn") or "")
                analysis_empty = bool(
                    payload.get("analysis_empty", not payload)
                )
                analysis = None if analysis_empty else payload
                ok = bool(payload.get("ok"))
                if payload.get("error") and not ok:
                    log_buf.write(f"\n{payload.get('error')}\n")
            finally:
                if proc is not None and proc.poll() is None:
                    proc.kill()
                    try:
                        proc.wait(timeout=8)
                    except Exception:
                        pass
                if result_file is not None:
                    try:
                        result_file.unlink(missing_ok=True)
                    except OSError:
                        pass

            log_text = log_buf.getvalue()
            tail = log_text[-4000:] if log_text else ""

            try:
                after = summarize_cues(audio_path)
            except Exception as summarize_exc:
                log_buf.write(
                    f"\n⚠️  Could not re-read cues after write: {summarize_exc}\n"
                )
                tail = log_buf.getvalue()[-4000:]
                after = type(
                    "CueSummaryLite",
                    (),
                    {
                        "cue_count": int(payload.get("analysis_cues") or 0),
                        "loop_count": int(payload.get("analysis_loops") or 0),
                    },
                )()
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

            fail_message = autocue_fail_message(
                tail,
                analysis_empty=not analysis if "analysis" in locals() else True,
                warn_msg=warn_msg or "",
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
                    worker_pid=None,
                    message=msg,
                    log_tail=tail,
                    cue_count_after=after.cue_count,
                    loop_count_after=after.loop_count,
                )
                if not dry_run:
                    try:
                        from .set_approval import revoke_set_approval

                        revoke_set_approval(audio_path)
                    except Exception:
                        pass
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
                maybe_ingest_after_autocue(
                    audio_path, after, dry_run=dry_run, ok=True
                )
            else:
                _update_job(
                    job_id,
                    status="error",
                    finished_at=_now(),
                    worker_pid=None,
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
                worker_pid=None,
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
