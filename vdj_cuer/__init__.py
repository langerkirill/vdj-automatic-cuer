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
]
