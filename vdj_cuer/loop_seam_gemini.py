"""Perceptual loop-seam check: end of loop + start of loop through Gemini.

A good DJ loop wraps so the last few seconds into the first few seconds sound
continuous. We build that splice (end, then start) — default last 3s + first 3s —
and ask Gemini whether a careful listener would hear an easily perceptible
discontinuity at the midpoint.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from typing import Optional, Tuple

from pydantic import BaseModel, Field


# Half-clip duration on each side of the wrap (end then start).
# 3s + 3s gives Gemini enough hearing room around the splice.
LOOP_SEAM_CLIP_SECONDS = 3.0
# Below this half-duration the splice is too short for a fair listen.
LOOP_SEAM_MIN_HALF_SECONDS = 0.5
LOOP_SEAM_GEMINI_TIMEOUT_SECONDS = 60
# Cap how many discovery candidates get a Gemini listen (API cost).
LOOP_SEAM_GEMINI_MAX_CHECKS = 8
# When a wrap fails, try alternate starts/lengths this many times (incl. first).
LOOP_SEAM_MAX_ATTEMPTS = 3


class LoopSeamJudgment(BaseModel):
    """Structured Gemini verdict on a wrap-around splice."""

    seamless: bool = Field(
        description=(
            "True only when a careful listener would not easily notice a jump, "
            "click, level change, key change, or section change at the midpoint."
        )
    )
    reason: str = Field(
        default="",
        description="Short explanation of why the wrap is or is not seamless.",
    )


def seam_half_seconds(
    loop_duration: float, preferred: float = LOOP_SEAM_CLIP_SECONDS
) -> float:
    """Seconds of end and start audio to use for the wrap clip."""
    if loop_duration <= 0:
        return 0.0
    return min(float(preferred), float(loop_duration) / 2.0)


def build_loop_wrap_clip(
    audio_path: str,
    loop_start: float,
    loop_duration: float,
    *,
    seam_seconds: float = LOOP_SEAM_CLIP_SECONDS,
    output_path: Optional[str] = None,
) -> Tuple[str, float]:
    """Build a mono clip: [last seam of loop] + [first seam of loop].

    Layout (time →):
        0 ........ half ........ 2*half
        |--- end ---|--- start ---|
                      ^
                      splice under test (wrap point)

    Returns (path_to_audio, half_seconds). Caller owns deletion of the file
    when ``output_path`` was not supplied.
    """
    if not audio_path or not os.path.isfile(audio_path):
        raise FileNotFoundError(f"Audio not found: {audio_path}")
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is required to build loop wrap clips")

    half = seam_half_seconds(loop_duration, seam_seconds)
    if half < LOOP_SEAM_MIN_HALF_SECONDS:
        raise ValueError(
            f"Loop too short for seam clip ({loop_duration:.3f}s); "
            f"need at least {2 * LOOP_SEAM_MIN_HALF_SECONDS:.2f}s"
        )

    loop_start = float(loop_start)
    loop_duration = float(loop_duration)
    tail_start = loop_start + loop_duration - half
    head_start = loop_start

    owns_output = output_path is None
    if output_path is None:
        fd, output_path = tempfile.mkstemp(prefix="vdj_loop_seam_", suffix=".m4a")
        os.close(fd)

    # Single-pass extract + concat so end plays before start (wrap transition).
    filter_complex = (
        f"[0:a]atrim=start={tail_start:.6f}:duration={half:.6f},"
        f"asetpts=PTS-STARTPTS,aformat=sample_fmts=fltp:channel_layouts=mono[tail];"
        f"[0:a]atrim=start={head_start:.6f}:duration={half:.6f},"
        f"asetpts=PTS-STARTPTS,aformat=sample_fmts=fltp:channel_layouts=mono[head];"
        f"[tail][head]concat=n=2:v=0:a=1[out]"
    )
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        audio_path,
        "-filter_complex",
        filter_complex,
        "-map",
        "[out]",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        output_path,
    ]
    try:
        subprocess.run(command, check=True, capture_output=True)
    except Exception:
        if owns_output and output_path and os.path.exists(output_path):
            os.remove(output_path)
        raise

    if not os.path.isfile(output_path) or os.path.getsize(output_path) < 64:
        if owns_output and output_path and os.path.exists(output_path):
            os.remove(output_path)
        raise RuntimeError("ffmpeg produced an empty loop wrap clip")

    return output_path, half


def loop_seam_prompt(half_seconds: float) -> str:
    """Instruction text for the perceptual wrap listen."""
    mid = half_seconds
    total = half_seconds * 2.0
    return f"""You are judging a DJ loop wrap-around for VirtualDJ.

The audio is a {total:.2f}s clip built from ONE proposed loop region:
  1. The LAST {half_seconds:.2f}s of the loop (plays first)
  2. The FIRST {half_seconds:.2f}s of the loop (plays second)

The splice (wrap point) is exactly at {mid:.2f}s — middle of the clip.
Listen through the lead-in before the splice and the continuation after so you
have hearing room, not just a tiny crossfade.

A GOOD loop has no easily perceptible difference across that splice: continuous
groove, level, texture, harmony, and phrasing so a DJ could leave the loop
running forever without a click, jump, key change, sudden energy drop/rise, or
obvious section change at the midpoint.

Set seamless=true ONLY when a careful listener would not easily notice a
discontinuity at {mid:.2f}s.
Set seamless=false if the wrap is rough, even slightly, or if you are unsure.
Keep reason short (one sentence).
"""
