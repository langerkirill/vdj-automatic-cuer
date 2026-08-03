"""Shared imports, constants, and response models for vdj_cuer."""

import os
import sys
import json
import math
import re
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


DEFAULT_GEMINI_MODEL = "gemini-3.1-pro-preview"
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
