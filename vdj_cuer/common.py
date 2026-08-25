"""Shared imports, constants, and response models for vdj_cuer."""

import os
import sys
import json
import math
import re
import errno
import shutil
import subprocess
import struct
import tempfile
import threading
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
import argparse
from typing import Dict, List, Literal, Optional, Tuple, Type
from dotenv import load_dotenv
from pathlib import Path
import html
from pydantic import BaseModel, Field
from google import genai
from google.genai import types
import asyncio
import time

from vdj_database_safety import (
    database_integrity_stats,
    validate_database_replacement,
)


def load_gemini_api_key() -> str:
    """
    Resolve GEMINI_API_KEY from env or known .env locations.

    load_dotenv() alone only searches the process CWD, which breaks when the
    Music Sorter UI is launched from home/src without a local .env while the
    key lives under Desktop/vdj-automatic-cuer/.env.
    """
    if os.getenv("GEMINI_API_KEY"):
        return os.environ["GEMINI_API_KEY"]

    repo_root = Path(__file__).resolve().parents[1]
    candidates = [
        repo_root / ".env",
        repo_root / "ui" / ".env",
        Path.home() / "Desktop" / "vdj-automatic-cuer" / ".env",
        Path.home() / "Desktop" / "vdj-automatic-cuer" / "ui" / ".env",
        Path.home() / "src" / "vdj-automatic-cuer" / ".env",
        Path.cwd() / ".env",
    ]
    for path in candidates:
        if path.is_file():
            load_dotenv(path, override=False)
    load_dotenv(override=False)

    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not key:
        searched = ", ".join(str(path) for path in candidates)
        raise ValueError(
            "GEMINI_API_KEY not found in environment or .env file. "
            f"Looked for: {searched}"
        )
    return key


DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"
# 3.5 Flash-Lite 503s / returns empty under load. 2.5 Flash 404s for new keys.
GEMINI_PRO_FALLBACKS = (
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
    "gemini-2.5-pro",
)
_DEAD_GEMINI_MODELS = {
    "gemini-2.5-flash",
    "gemini-2.0-flash",
}


def resolve_gemini_model(explicit: str | None = None) -> str:
    """Pick AutoCue model: call arg, AUTOCUE_GEMINI_MODEL, GEMINI_MODEL, default.

    Ignore leftover grok-* and retired 2.5 Flash ids.
    """
    for candidate in (
        explicit,
        os.getenv("AUTOCUE_GEMINI_MODEL"),
        os.getenv("GEMINI_MODEL"),
        DEFAULT_GEMINI_MODEL,
    ):
        name = (candidate or "").strip()
        if not name or name.lower().startswith("grok"):
            continue
        if name.casefold() in _DEAD_GEMINI_MODELS:
            continue
        return name
    return DEFAULT_GEMINI_MODEL
DEFAULT_UPLOAD_RETRIES = 5
DEFAULT_ANALYSIS_RETRIES = 3
# What to rewrite in database.xml: both, cues only (keep loops), or loops only (keep cues).
WRITE_SCOPE_ALL = "all"
WRITE_SCOPE_CUES = "cues"
WRITE_SCOPE_LOOPS = "loops"
WRITE_SCOPES = (WRITE_SCOPE_ALL, WRITE_SCOPE_CUES, WRITE_SCOPE_LOOPS)
VDJ_STEM_NAMES = ("vocal", "hihat", "bass", "instruments", "kick")
LOOP_BEAT_CHOICES = (32, 16, 8, 4)
MIN_USEFUL_LOOP_BEATS = 4
TARGET_MIN_LOOPS = 2
TARGET_MAX_LOOPS = 3
# Wall-clock cap so 32-beat loops on slow tracks (e.g. Valley Of The Winds @ 75
# BPM ≈ 25.6s) are shortened to a DJ-usable length.
MAX_LOOP_DURATION_SECONDS = 14.0
BEATGRID_ALIGNMENT_DURATION_SECONDS = 90
BEATGRID_ALIGNMENT_SAMPLE_RATE = 8000
BEATGRID_ALIGNMENT_FRAME_SECONDS = 0.04
BEATGRID_ALIGNMENT_HOP_SECONDS = 0.01
BEATGRID_FINE_ALIGNMENT_STEP_SECONDS = 0.01
BEATGRID_FINE_ALIGNMENT_MIN_SCORE = 0.05
BEATGRID_FINE_ALIGNMENT_MIN_GAIN = 0.04
BEATGRID_FINE_ALIGNMENT_MIN_RATIO = 2.5
BEATGRID_FINE_ALIGNMENT_MIN_SHIFT_SECONDS = 0.08
BEATGRID_PHASE_SOURCE_MIN_SCORE = 0.02
# Absolute floor for multi-source votes; relative dominance within a stem can
# still qualify a quieter source (important when kick is ambiguous).
BEATGRID_PHASE_SOURCE_RELATIVE_MIN_SCORE = 0.008
# Keep the current VDJ 1 when it already scores close to the stem-best phase.
# Manual alignments must not be flipped for a slightly louder +2.
EXISTING_DOWNBEAT_KEEP_RATIO = 0.72
EXISTING_DOWNBEAT_MIN_SCORE = 0.02


def existing_downbeat_is_trusted(phase_scores: Optional[Dict[int, float]]) -> bool:
    """True when the current grid (phase 0) already looks like the musical 1.

    Weak or missing evidence → trust the stored grid (often a hand alignment).
    Only return False when another phase clearly beats the current 1.
    """
    if not phase_scores:
        return True
    current = float(phase_scores.get(0, 0.0) or 0.0)
    best = max((float(score or 0.0) for score in phase_scores.values()), default=0.0)
    if best < EXISTING_DOWNBEAT_MIN_SCORE:
        return True
    return current >= best * EXISTING_DOWNBEAT_KEEP_RATIO


DOWNBEAT_HARD_FAIL_BEATS = 0.08


def quantize_to_downbeat(time_sec: float, bpm: float, offset: float = 0.0) -> float:
    """Nearest nonnegative bar-1 (beat 1)."""
    if not bpm or bpm <= 0 or not math.isfinite(float(time_sec)):
        return float(time_sec)
    bar = (60.0 / float(bpm)) * 4.0
    if bar <= 0:
        return max(0.0, float(time_sec))
    steps = (float(time_sec) - float(offset)) / bar
    nearest = math.floor(steps + 0.5)
    first = math.ceil(-float(offset) / bar)
    nearest = max(int(nearest), int(first))
    return float(offset) + nearest * bar


def is_on_downbeat(
    time_sec: float,
    bpm: float,
    offset: float = 0.0,
    *,
    tol_beats: float = DOWNBEAT_HARD_FAIL_BEATS,
) -> bool:
    """True when time is on beat 1 of the bar (the DJ jump point)."""
    if not bpm or bpm <= 0 or not math.isfinite(float(time_sec)):
        return False
    beat = 60.0 / float(bpm)
    bar = beat * 4.0
    if bar <= 0:
        return False
    pos = (float(time_sec) - float(offset)) % bar
    if pos < 0:
        pos += bar
    dist = min(pos, bar - pos)
    return dist <= beat * max(0.0, float(tol_beats))
BEATGRID_PHASE_SOURCE_RELATIVE_RATIO = 1.45
BEATGRID_PHASE_NEAR_TIE_RATIO = 1.15
BEATGRID_PHASE_CONSENSUS_MIN_SOURCES = 2
BEATGRID_PHASE_CONSENSUS_MIN_RATIO = 1.35
BEATGRID_PHASE_CONSENSUS_MIN_GAIN = 0.02

NETWORK_ERROR_TERMS = (
    "ssl",
    "connection",
    "network",
    "broken pipe",
    "timeout",
    "reset",
    "errno 32",
)

RETRYABLE_API_ERROR_TERMS = NETWORK_ERROR_TERMS + (
    "429",
    "500",
    "502",
    "503",
    "504",
    "internal error",
    "unavailable",
    "resource exhausted",
    "quota",
    "rate limit",
    "too many requests",
    "empty response",
    "high demand",
    "overloaded",
)


class StemDecodeError(RuntimeError):
    """ffmpeg could not decode a VDJ stem stream (EPIPE, bad map, etc.)."""


def is_stem_decode_error(error: BaseException) -> bool:
    """True for EPIPE / ffmpeg stem-decode failures that should fail over to mix."""
    if isinstance(error, StemDecodeError):
        return True
    if isinstance(error, BrokenPipeError):
        return True
    if isinstance(error, OSError) and getattr(error, "errno", None) == errno.EPIPE:
        return True
    if isinstance(error, subprocess.CalledProcessError):
        if error.returncode in {141, -13}:  # SIGPIPE
            return True
        raw = error.stderr or error.stdout or b""
        detail = (
            raw.decode(errors="replace")
            if isinstance(raw, (bytes, bytearray))
            else str(raw)
        )
        if "broken pipe" in detail.lower():
            return True
    text = str(error).lower()
    return any(
        term in text
        for term in ("broken pipe", "errno 32", "epipe", "stem decode")
    )


@dataclass
class BeatgridAlignment:
    """Verified beatgrid offset and confidence metadata."""

    offset: float
    shift_beats: int = 0
    corrected: bool = False
    confidence_ratio: float = 1.0
    phase_scores: Dict[int, float] = field(default_factory=dict)
    source: str = "database"
    fine_shift_seconds: float = 0.0
    beat_score: float = 0.0
    best_beat_score: float = 0.0
    stems_skipped: bool = False


class StemActivity(BaseModel):
    """Per-stem activity around a cue or loop."""

    vocal: Optional[str] = None
    hihat: Optional[str] = None
    bass: Optional[str] = None
    instruments: Optional[str] = None
    kick: Optional[str] = None


class MeasureChange(BaseModel):
    """Represents a significant musical change point for cues"""

    timestamp: float
    elements: List[str]
    cue_name: str
    color: str
    role: Literal[
        "intro",
        "entry",
        "groove",
        "build",
        "drop",
        "breakdown",
        "vocal",
        "outro",
        "section",
    ]
    confidence: float = Field(ge=0.0, le=1.0)
    stem_activity: Optional[StemActivity] = None


class LoopSegment(BaseModel):
    """Represents a loop segment for DJing"""

    start: float
    length_beats: int
    elements: List[str]
    loop_name: str
    color: str
    role: Literal["loop"]
    confidence: float = Field(ge=0.0, le=1.0)
    stem_activity: Optional[StemActivity] = None


class MusicAnalysis(BaseModel):
    """Complete music analysis response from Gemini"""

    measure_changes: List[MeasureChange]
    loop_segments: List[LoopSegment]


class BatchMusicAnalysis(BaseModel):
    """Complete batch music analysis response from Gemini"""

    analyses: List[MusicAnalysis]
