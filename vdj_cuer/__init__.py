"""VirtualDJ automatic cue generation package."""

from .common import (
    BatchMusicAnalysis,
    BeatgridAlignment,
    DEFAULT_ANALYSIS_RETRIES,
    DEFAULT_GEMINI_MODEL,
    DEFAULT_UPLOAD_RETRIES,
    LoopSegment,
    MeasureChange,
    MusicAnalysis,
    StemActivity,
    WRITE_SCOPE_ALL,
    WRITE_SCOPE_CUES,
    WRITE_SCOPE_LOOPS,
    WRITE_SCOPES,
)
from .core import AutomaticMusicCuer

__all__ = [
    "AutomaticMusicCuer",
    "BatchMusicAnalysis",
    "BeatgridAlignment",
    "DEFAULT_ANALYSIS_RETRIES",
    "DEFAULT_GEMINI_MODEL",
    "DEFAULT_UPLOAD_RETRIES",
    "LoopSegment",
    "MeasureChange",
    "MusicAnalysis",
    "StemActivity",
    "WRITE_SCOPE_ALL",
    "WRITE_SCOPE_CUES",
    "WRITE_SCOPE_LOOPS",
    "WRITE_SCOPES",
]
