"""Gemini-powered transition quality analysis for practice mixes.

For each transition:
  1. Intelligently crop a blend sample from the mix (outgoing → incoming)
  2. Send only that clip to Gemini (not the full mix)
  3. Score quality 1–10
  4. If notes/history suggest stronger destinations, recommend a better option + why
  5. Persist scores so reopening a mix never re-runs finished transitions
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from vdj_cuer.gemini_call import generate_json

from .llm import models_to_try, resolve_sorter_model
from .practice_sets import get_practice_set_detail
from .transitions_db import lookup_options, normalize_key, save_practice_score

DEFAULT_MODEL = resolve_sorter_model()
MODEL_FALLBACKS = models_to_try(DEFAULT_MODEL)

# Default blend window (refined per-transition by gap length)
PRE_ROLL_DEFAULT = 28.0
POST_ROLL_DEFAULT = 22.0
PRE_ROLL_MIN = 16.0
PRE_ROLL_MAX = 40.0
POST_ROLL_MIN = 14.0
POST_ROLL_MAX = 32.0

_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()


def _load_api_key() -> str:
    ui_root = Path(__file__).resolve().parents[1]
    repo_root = Path(__file__).resolve().parents[2]
    load_dotenv(ui_root / ".env")
    load_dotenv(repo_root / ".env")
    load_dotenv(Path.home() / "Desktop" / "vdj-automatic-cuer" / ".env")
    load_dotenv()
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise RuntimeError(
            "GEMINI_API_KEY not found. Set it in vdj-automatic-cuer/.env (or ui/.env)."
        )
    return key


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class BetterOptionSchema(BaseModel):
    """A historically stronger destination than what was actually mixed."""

    track: str = Field(description="Recommended alternate track label")
    reason: str = Field(description="Why this would likely beat the actual choice")
    source: str = Field(
        default="history",
        description="note | history | note+history",
    )
    confidence: float = Field(
        ge=0.0, le=1.0, description="How confident you are this is better"
    )


class TransitionScoreSchema(BaseModel):
    overall: float = Field(ge=1, le=10, description="Overall transition quality 1-10")
    smoothness: float = Field(ge=1, le=10)
    creativity: float = Field(ge=1, le=10)
    flow: float = Field(ge=1, le=10)
    energy_match: float = Field(ge=1, le=10)
    comments: str = Field(description="2-4 sentences of specific feedback on the audio")
    save_for_set: bool = Field(
        description="True if strong enough to keep/practice for a real set"
    )
    strengths: list[str] = Field(default_factory=list)
    improvements: list[str] = Field(default_factory=list)
    better_option: Optional[BetterOptionSchema] = Field(
        default=None,
        description=(
            "If history/notes include a stronger destination than the actual "
            "incoming track, recommend it. Null if the actual choice is best "
            "or no credible alternatives exist."
        ),
    )


def clip_window_for_transition(
    *,
    at_sec: float,
    prev_at_sec: Optional[float],
    next_at_sec: Optional[float],
    mix_duration: Optional[float],
) -> tuple[float, float, float]:
    """
    Return (start_sec, duration_sec, pre_roll) for a smart blend sample.

    Prefers enough outgoing context without bleeding into the prior transition,
    and enough incoming without swallowing the next blend.
    """
    at = float(at_sec)
    # Outgoing window: up to half the gap from previous cue, capped
    if prev_at_sec is not None and at > float(prev_at_sec):
        gap_out = at - float(prev_at_sec)
        pre = min(PRE_ROLL_MAX, max(PRE_ROLL_MIN, gap_out * 0.45))
    else:
        pre = PRE_ROLL_DEFAULT

    # Incoming window: up to half gap until next cue
    if next_at_sec is not None and float(next_at_sec) > at:
        gap_in = float(next_at_sec) - at
        post = min(POST_ROLL_MAX, max(POST_ROLL_MIN, gap_in * 0.4))
    else:
        post = POST_ROLL_DEFAULT

    start = max(0.0, at - pre)
    end = at + post
    if mix_duration is not None and mix_duration > 0:
        end = min(end, float(mix_duration))
    duration = max(8.0, end - start)
    # Recompute pre actually used (for prompt text)
    actual_pre = at - start
    return start, duration, actual_pre


def extract_transition_clip(
    mix_path: Path,
    at_sec: float,
    *,
    pre: float = PRE_ROLL_DEFAULT,
    post: float = POST_ROLL_DEFAULT,
    start_sec: Optional[float] = None,
    duration_sec: Optional[float] = None,
    out_dir: Optional[Path] = None,
) -> Path:
    """Export a short mp3 of just the blend for Gemini upload."""
    if start_sec is not None and duration_sec is not None:
        start = max(0.0, float(start_sec))
        duration = max(5.0, float(duration_sec))
    else:
        start = max(0.0, float(at_sec) - pre)
        duration = pre + post
    out_dir = out_dir or Path(tempfile.gettempdir())
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"tx_{uuid.uuid4().hex[:10]}.mp3"
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-y",
        "-ss",
        f"{start:.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(mix_path),
        "-vn",
        "-ac",
        "2",
        "-ar",
        "44100",
        "-b:a",
        "160k",
        str(out),
    ]
    proc = subprocess.run(cmd, check=False, capture_output=True)
    if proc.returncode != 0 or not out.is_file() or out.stat().st_size < 1000:
        err = (proc.stderr or b"").decode("utf-8", "replace")[-400:]
        raise RuntimeError(
            f"Failed to extract transition clip from {mix_path.name}: {err}"
        )
    return out


def _parse_json_response(text: str) -> dict[str, Any]:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return json.loads(cleaned)


def _wait_file_active(client: genai.Client, uploaded: Any, timeout: int = 90) -> Any:
    for _ in range(timeout):
        state = getattr(getattr(uploaded, "state", None), "name", None) or str(
            getattr(uploaded, "state", "")
        )
        if not state or state in {"ACTIVE", "FileState.ACTIVE", "STATE_ACTIVE"}:
            return uploaded
        if "FAILED" in state.upper():
            raise RuntimeError(f"Gemini file processing failed: {state}")
        time.sleep(1)
        if getattr(uploaded, "name", None):
            uploaded = client.files.get(name=uploaded.name)
    return uploaded


def _format_alternatives(
    alternatives: list[dict[str, Any]],
    actual_to: str,
) -> tuple[str, list[dict[str, Any]]]:
    """Build prompt block + filtered list excluding the actual destination."""
    actual_key = normalize_key(actual_to)
    filtered: list[dict[str, Any]] = []
    lines: list[str] = []
    for a in alternatives or []:
        label = (a.get("to_label") or "").strip()
        if not label:
            continue
        if normalize_key(label) == actual_key:
            continue  # skip actual choice
        # Also skip very low-signal history one-offs unless notes
        src = a.get("source") or ""
        count = int(a.get("count") or 0)
        if "history" in src and "note" not in src and count < 1:
            continue
        filtered.append(a)
        bit = f"- {label}"
        if count:
            bit += f"  [history ×{count}]"
        if a.get("source"):
            bit += f"  (source: {a['source']})"
        if a.get("vibe"):
            bit += f"  vibe={a['vibe']}"
        if a.get("note"):
            bit += f"  notes: {a['note']}"
        lines.append(bit)
    # Prefer highest-scoring / most-played first (already ranked by lookup_options)
    block = "\n".join(lines[:10]) if lines else "(no alternate history or notes)"
    return block, filtered[:10]


def score_transition_clip(
    client: genai.Client,
    clip_path: Path,
    *,
    from_track: str,
    to_track: str,
    at_sec: float,
    clip_pre: float,
    clip_duration: float,
    alternatives: list[dict[str, Any]],
    model_name: str,
) -> dict[str, Any]:
    alt_block, alt_list = _format_alternatives(alternatives, to_track)
    alt_names = ", ".join(
        (a.get("to_label") or "") for a in alt_list[:8] if a.get("to_label")
    ) or "none"

    prompt = f"""You are an expert club / zouk DJ coach reviewing ONE live mix transition.

AUDIO YOU HEAR
- This is a short SAMPLE of the mix only — not the full set.
- Length ≈ {clip_duration:.0f}s.
- Roughly the first {clip_pre:.0f}s is the OUTGOING track leading into the blend;
  the rest is the INCOMING track after the mix-in cue.
- Listen for phrasing, EQ/filter moves, volume automation, beat match, harmonic clash,
  energy handoff, and whether the next track feels inevitable.

ACTUAL TRANSITION
- Outgoing: {from_track}
- Incoming (what the DJ actually mixed into): {to_track}
- Mix-in cue time in the full recording: {at_sec:.1f}s

ALTERNATE OPTIONS FROM THIS DJ'S NOTES + PLAY HISTORY
(destinations they have used or written about from the outgoing track — NOT the actual choice)
{alt_block}

Candidate alternate names for better_option.track (prefer these exact labels when recommending):
{alt_names}

YOUR JOB
1) Score the ACTUAL audio transition you hear (1–10):
   - smoothness, creativity, flow, energy_match, overall
2) save_for_set = true only if overall ≥ 7.5 and no critical mistakes
3) strengths (≤3) and improvements (≤3) — specific to what you hear
4) comments: 2–4 sentences on the audio
5) better_option:
   - If the alternate list has a destination that would likely OUTPERFORM the actual
     incoming track for this moment (energy, vibe continuity, proven history count,
     note technique, harmonic/story sense), set better_option with:
       track, reason (why better than actual), source, confidence (0–1)
   - If the actual choice is best, or alternatives are weak/irrelevant, set better_option to null
   - Do NOT invent tracks that are not in the alternate list
   - Prefer high history counts and note-backed techniques when ranking alternatives

Be honest. Average club transitions score 5–6. 9–10 is rare.
"""

    uploaded = client.files.upload(file=str(clip_path))
    uploaded = _wait_file_active(client, uploaded)

    data, used = generate_json(
        client,
        [prompt, uploaded],
        TransitionScoreSchema,
        models=models_to_try(model_name),
        timeout_seconds=180,
        thinking=False,
    )

    try:
        if getattr(uploaded, "name", None):
            client.files.delete(name=uploaded.name)
    except Exception:
        pass

    parsed = TransitionScoreSchema.model_validate(data)
    result = parsed.model_dump()
    result["model"] = used
    result["clip_start_sec"] = max(0.0, float(at_sec) - float(clip_pre))
    result["clip_duration_sec"] = float(clip_duration)
    result["clip_pre_sec"] = float(clip_pre)
    # Flatten better_option for DB/UI
    bo = result.get("better_option")
    if isinstance(bo, dict) and bo.get("track"):
        result["better_option_track"] = bo.get("track") or ""
        result["better_option_reason"] = bo.get("reason") or ""
        result["better_option_source"] = bo.get("source") or ""
        result["better_option_confidence"] = bo.get("confidence")
    else:
        result["better_option"] = None
        result["better_option_track"] = ""
        result["better_option_reason"] = ""
        result["better_option_source"] = ""
        result["better_option_confidence"] = None
    return result


def _set_job(job_id: str, **kwargs: Any) -> None:
    with _jobs_lock:
        job = _jobs.setdefault(job_id, {"id": job_id})
        job.update(kwargs)
        job["updated_at"] = _now_iso()


def get_analyze_job(job_id: str) -> Optional[dict[str, Any]]:
    with _jobs_lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None


def start_analyze_job(
    mix_path: str | Path,
    *,
    force: bool = False,
    max_transitions: Optional[int] = None,
) -> dict[str, Any]:
    """Start background Gemini analysis. Skips already-scored transitions unless force."""
    path = Path(mix_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Mix not found: {path}")

    detail = get_practice_set_detail(path, include_alternatives=True)
    transitions = detail.get("transitions") or []
    if max_transitions is not None:
        transitions = transitions[: max(0, int(max_transitions))]

    already = sum(
        1
        for tx in transitions
        if tx.get("score") and tx["score"].get("overall") is not None
    )
    pending = len(transitions) - already

    job_id = uuid.uuid4().hex[:12]
    _set_job(
        job_id,
        status="queued",
        mix_path=str(path),
        mix_name=path.name,
        total=len(transitions),
        already_scored=already,
        pending=pending if not force else len(transitions),
        done=0,
        current=None,
        results=[],
        error=None,
        force=force,
        started_at=_now_iso(),
    )

    def worker() -> None:
        try:
            # Fast path: everything already scored and not forcing
            if not force and pending == 0 and transitions:
                results = []
                for tx in transitions:
                    s = tx.get("score") or {}
                    results.append(
                        {
                            "transition_index": tx["index"],
                            "from_track": tx["from_track"],
                            "to_track": tx["to_track"],
                            "at_sec": tx["at_sec"],
                            "cached": True,
                            **{
                                k: s.get(k)
                                for k in (
                                    "overall",
                                    "smoothness",
                                    "creativity",
                                    "flow",
                                    "energy_match",
                                    "comments",
                                    "save_for_set",
                                    "model",
                                    "strengths",
                                    "improvements",
                                    "better_option_track",
                                    "better_option_reason",
                                    "better_option_source",
                                    "better_option_confidence",
                                )
                            },
                        }
                    )
                scored = [r for r in results if r.get("overall") is not None]
                scored_sorted = sorted(
                    scored, key=lambda r: float(r.get("overall") or 0), reverse=True
                )
                save = [r for r in scored_sorted if r.get("save_for_set")]
                _set_job(
                    job_id,
                    status="done",
                    done=len(transitions),
                    results=results,
                    summary={
                        "scored": len(scored),
                        "cached": len(scored),
                        "errors": 0,
                        "avg_overall": (
                            round(
                                sum(float(r["overall"]) for r in scored) / len(scored),
                                2,
                            )
                            if scored
                            else None
                        ),
                        "top": scored_sorted[:5],
                        "save_for_set": save,
                        "needs_work": list(
                            sorted(scored, key=lambda x: float(x.get("overall") or 0))
                        )[:5],
                        "better_options": [
                            r
                            for r in scored
                            if r.get("better_option_track")
                        ],
                    },
                    finished_at=_now_iso(),
                )
                return

            client = genai.Client(api_key=_load_api_key())
            model_name = DEFAULT_MODEL
            results: list[dict[str, Any]] = []
            tmp_dir = Path(tempfile.mkdtemp(prefix="practice_tx_"))
            mix_duration = detail.get("duration_sec")
            _set_job(job_id, status="running", temp_dir=str(tmp_dir))

            for i, tx in enumerate(transitions):
                # Neighbor cues for smart windowing
                prev_at = (
                    float(transitions[i - 1]["at_sec"])
                    if i > 0
                    else (
                        float(detail["tracks"][0]["pos_sec"])
                        if detail.get("tracks")
                        else None
                    )
                )
                # prev track start is better for outgoing context when i==0
                if i == 0 and detail.get("tracks"):
                    # use this track's start (outgoing started earlier)
                    # find track matching from name? use previous transition end = at of this - gap
                    pass
                if i > 0:
                    prev_at = float(transitions[i - 1]["at_sec"])
                else:
                    # outgoing may have started at previous track pos
                    tracks = detail.get("tracks") or []
                    prev_at = float(tracks[0]["pos_sec"]) if tracks else 0.0

                next_at = (
                    float(transitions[i + 1]["at_sec"])
                    if i + 1 < len(transitions)
                    else mix_duration
                )

                if not force and tx.get("score") and tx["score"].get("overall") is not None:
                    s = tx["score"]
                    results.append(
                        {
                            "transition_index": tx["index"],
                            "from_track": tx["from_track"],
                            "to_track": tx["to_track"],
                            "at_sec": tx["at_sec"],
                            "cached": True,
                            **{
                                k: s.get(k)
                                for k in (
                                    "overall",
                                    "smoothness",
                                    "creativity",
                                    "flow",
                                    "energy_match",
                                    "comments",
                                    "save_for_set",
                                    "model",
                                    "strengths",
                                    "improvements",
                                    "better_option_track",
                                    "better_option_reason",
                                    "better_option_source",
                                    "better_option_confidence",
                                )
                            },
                        }
                    )
                    _set_job(
                        job_id,
                        done=i + 1,
                        current=f"saved · {tx['from_track']} → {tx['to_track']}",
                        results=list(results),
                    )
                    continue

                # Fresh alternatives from DB at analyze time
                alts = tx.get("alternatives") or lookup_options(
                    tx["from_track"], limit=12
                )

                start, duration, pre = clip_window_for_transition(
                    at_sec=float(tx["at_sec"]),
                    prev_at_sec=prev_at,
                    next_at_sec=next_at,
                    mix_duration=mix_duration,
                )

                label = f"{tx['from_track']} → {tx['to_track']}"
                _set_job(
                    job_id,
                    current=f"listening · {label}",
                    done=i,
                    clip_window={"start": start, "duration": duration, "pre": pre},
                )
                clip: Optional[Path] = None
                try:
                    clip = extract_transition_clip(
                        path,
                        float(tx["at_sec"]),
                        start_sec=start,
                        duration_sec=duration,
                        out_dir=tmp_dir,
                    )
                    score = score_transition_clip(
                        client,
                        clip,
                        from_track=tx["from_track"],
                        to_track=tx["to_track"],
                        at_sec=float(tx["at_sec"]),
                        clip_pre=pre,
                        clip_duration=duration,
                        alternatives=alts,
                        model_name=model_name,
                    )
                    model_name = score.get("model") or model_name
                    record = {
                        "mix_path": str(path),
                        "from_track": tx["from_track"],
                        "to_track": tx["to_track"],
                        "transition_index": int(tx["index"]),
                        "at_sec": float(tx["at_sec"]),
                        "overall": score["overall"],
                        "smoothness": score["smoothness"],
                        "creativity": score["creativity"],
                        "flow": score["flow"],
                        "energy_match": score["energy_match"],
                        "comments": score.get("comments") or "",
                        "save_for_set": bool(score.get("save_for_set")),
                        "model": score.get("model") or model_name,
                        "analyzed_at": _now_iso(),
                        "strengths": score.get("strengths") or [],
                        "improvements": score.get("improvements") or [],
                        "better_option_track": score.get("better_option_track") or "",
                        "better_option_reason": score.get("better_option_reason") or "",
                        "better_option_source": score.get("better_option_source") or "",
                        "better_option_confidence": score.get(
                            "better_option_confidence"
                        ),
                        "clip_start_sec": score.get("clip_start_sec"),
                        "clip_duration_sec": score.get("clip_duration_sec"),
                    }
                    save_practice_score(record)
                    results.append({**record, "cached": False})
                except Exception as exc:
                    results.append(
                        {
                            "transition_index": tx["index"],
                            "from_track": tx["from_track"],
                            "to_track": tx["to_track"],
                            "at_sec": tx["at_sec"],
                            "error": str(exc),
                        }
                    )
                finally:
                    if clip and clip.is_file():
                        try:
                            clip.unlink()
                        except OSError:
                            pass
                _set_job(job_id, done=i + 1, results=list(results))

            scored = [r for r in results if r.get("overall") is not None]
            scored_sorted = sorted(
                scored, key=lambda r: float(r.get("overall") or 0), reverse=True
            )
            save = [r for r in scored_sorted if r.get("save_for_set")]
            _set_job(
                job_id,
                status="done",
                current=None,
                results=results,
                summary={
                    "scored": len(scored),
                    "cached": sum(1 for r in results if r.get("cached")),
                    "errors": sum(1 for r in results if r.get("error")),
                    "avg_overall": (
                        round(
                            sum(float(r["overall"]) for r in scored) / len(scored), 2
                        )
                        if scored
                        else None
                    ),
                    "top": scored_sorted[:5],
                    "save_for_set": save,
                    "needs_work": list(
                        sorted(scored, key=lambda x: float(x.get("overall") or 0))
                    )[:5],
                    "better_options": [
                        r for r in scored if r.get("better_option_track")
                    ],
                },
                finished_at=_now_iso(),
            )
            try:
                for p in tmp_dir.glob("*"):
                    p.unlink(missing_ok=True)
                tmp_dir.rmdir()
            except OSError:
                pass
        except Exception as exc:
            _set_job(job_id, status="error", error=str(exc), finished_at=_now_iso())

    t = threading.Thread(target=worker, name=f"practice-analyze-{job_id}", daemon=True)
    t.start()
    return get_analyze_job(job_id) or {"id": job_id, "status": "queued"}
