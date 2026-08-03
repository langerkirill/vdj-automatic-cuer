"""Shared models and constants for cue visual audits."""


import argparse
import html
import json
import math
import os
import re
import struct
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


DEFAULT_DATABASE = (
    Path.home() / "Library" / "Application Support" / "VirtualDJ" / "database.xml"
)
CUE_COLOR_VALUES = {
    "4278190335": "blue",
    "4278255360": "green",
    "4288020735": "purple",
    "4294967040": "yellow",
    "4294934272": "orange",
}
COLOR_HEX = {
    "blue": "#1d4ed8",
    "green": "#16a34a",
    "purple": "#9333ea",
    "yellow": "#facc15",
    "orange": "#f97316",
    "unknown": "#9ca3af",
}
STEM_HEX = {
    "drums": "#ef4444",
    "vocal": "#22c55e",
    "bass": "#06b6d4",
    "instruments": "#3b82f6",
    "mix": "#cbd5e1",
}
STEM_NAMES = ("vocal", "hihat", "bass", "instruments", "kick")


@dataclass
class Poi:
    name: str
    pos: float
    poi_type: str
    color_value: str
    color_name: str
    size: str = ""
    slot: str = ""


@dataclass
class Track:
    path: str
    title: str
    artist: str
    length: float
    pois: list[Poi]
    beatgrid: Optional[float] = None
    scan_phase: Optional[float] = None
    scan_bpm: Optional[float] = None


@dataclass
class CueIssue:
    track: str
    cue: str
    timestamp: float
    severity: str
    issue: str
    cue_color: str
    expected_color: str
    elements: str


@dataclass
class CueObservation:
    track: str
    cue: str
    timestamp: float
    cue_type: str
    cue_color: str
    expected_color: str
    elements: str
    before_energy: float
    after_energy: float
    issues: list[str] = field(default_factory=list)


@dataclass
class AudioAnalysis:
    duration: float
    bin_seconds: float
    mix: list[float]
    stems: dict[str, list[float]] = field(default_factory=dict)

