#!/usr/bin/env python3
"""
Automatic Music Cueing System for VirtualDJ
Uses Google Gemini Pro to analyze music files and generate intelligent
cues and loops
"""

import os
import json
import math
import re
import shutil
import subprocess
import struct
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
import argparse
from typing import Dict, List, Optional, Tuple, Type
from dotenv import load_dotenv
import html
from pydantic import BaseModel
from google import genai
from google.genai import types
import asyncio
import time


DEFAULT_GEMINI_MODEL = "gemini-3.1-pro-preview"
DEFAULT_UPLOAD_RETRIES = 5
DEFAULT_ANALYSIS_RETRIES = 3
VDJ_STEM_NAMES = ("vocal", "hihat", "bass", "instruments", "kick")
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
BEATGRID_PHASE_CONSENSUS_MIN_SOURCES = 2
BEATGRID_PHASE_CONSENSUS_MIN_RATIO = 2.0
BEATGRID_PHASE_CONSENSUS_MIN_GAIN = 0.04

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
    stem_activity: Optional[StemActivity] = None


class LoopSegment(BaseModel):
    """Represents a loop segment for DJing"""

    start: float
    length_beats: int
    elements: List[str]
    loop_name: str
    color: str
    stem_activity: Optional[StemActivity] = None


class MusicAnalysis(BaseModel):
    """Complete music analysis response from Gemini"""

    measure_changes: List[MeasureChange]
    loop_segments: List[LoopSegment]


class BatchMusicAnalysis(BaseModel):
    """Complete batch music analysis response from Gemini"""

    analyses: List[MusicAnalysis]


class AutomaticMusicCuer:
    """A class to automatically cue music files for VirtualDJ."""

    @staticmethod
    def sanitize_xml_content(text: str) -> str:
        """Sanitize text content for safe XML inclusion"""
        if not text:
            return ""

        # Remove or replace problematic characters
        # Keep only printable ASCII and common Unicode characters
        sanitized = "".join(
            char for char in text if ord(char) >= 32 or char in "\t\n\r"
        )

        # HTML escape for XML safety
        sanitized = html.escape(sanitized, quote=False)

        # Remove any null bytes or other control characters
        sanitized = (
            sanitized.replace("\x00", "").replace("\x01", "").replace("\x02", "")
        )

        return sanitized.strip()

    def __init__(
        self,
        gemini_api_key: Optional[str] = None,
        vdj_database_path: Optional[str] = None,
        model_name: Optional[str] = None,
    ):
        """Initialize the automatic music cuer with Gemini Pro API"""
        # Load API key from .env file if not provided
        if gemini_api_key is None:
            load_dotenv()
            gemini_api_key = os.getenv("GEMINI_API_KEY")
            if not gemini_api_key:
                raise ValueError("GEMINI_API_KEY not found in environment or .env file")

        self.gemini_api_key = gemini_api_key
        self.model_name = model_name or os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
        self.client = genai.Client(api_key=gemini_api_key)
        self._beatgrid_alignment_cache: Dict[Tuple[str, float], BeatgridAlignment] = {}

        # Default VDJ database path
        if vdj_database_path is None:
            self.vdj_database_path = os.path.expanduser(
                "~/Library/Application Support/VirtualDJ/database.xml"
            )
        else:
            self.vdj_database_path = vdj_database_path

        # Color mappings for VDJ cues
        # (CORRECTED - based on actual VDJ database analysis)
        self.color_mappings = {
            "blue": "4278190335",  # Blue - melodic only (0xff0000ff) - FIXED
            "green": "4278255360",  # Green - melodic+drums (0xff00ff00)
            "purple": "4288020735",  # Purple - drums only (0xff9600ff)
            "yellow": "4294967040",  # Yellow - full mix (0xffffff00)
            "orange": "4294934272",  # Orange - vocal only (0xffff7f00)
        }

        print(f"🎵 Automatic Music Cuer initialized with Gemini model: {self.model_name}")
        print(f"📁 VDJ Database: {self.vdj_database_path}")

    def backup_database(self) -> str:
        """Create a timestamped backup of the VDJ database"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = f"{self.vdj_database_path}.backup.{timestamp}"
        shutil.copy2(self.vdj_database_path, backup_path)
        print(f"✅ Database backed up to: {backup_path}")
        return backup_path

    @staticmethod
    def is_virtualdj_running() -> bool:
        """Return True when a VirtualDJ process appears to be active."""
        try:
            result = subprocess.run(
                ["pgrep", "-fl", "VirtualDJ|virtualdj"],
                capture_output=True,
                text=True,
                check=False,
            )
        except Exception:
            return False

        if result.returncode != 0:
            return False

        return any("virtualdj" in line.lower() for line in result.stdout.splitlines())

    @staticmethod
    def _is_retryable_error(error: Exception, terms=RETRYABLE_API_ERROR_TERMS) -> bool:
        """Return True for temporary network/server failures worth retrying."""
        error_text = str(error).lower()
        return any(term in error_text for term in terms)

    def _upload_audio_file(self, audio_file_path: str):
        """Upload an audio file once using the current Gemini client."""
        upload_path = audio_file_path
        temp_upload_path = None

        try:
            os.path.basename(audio_file_path).encode("ascii")
        except UnicodeEncodeError:
            suffix = os.path.splitext(audio_file_path)[1] or ".audio"
            with tempfile.NamedTemporaryFile(
                prefix="vdj_upload_", suffix=suffix, delete=False
            ) as temp_file:
                temp_upload_path = temp_file.name
            shutil.copy2(audio_file_path, temp_upload_path)
            upload_path = temp_upload_path

        try:
            return self.client.files.upload(file=upload_path)
        finally:
            if temp_upload_path and os.path.exists(temp_upload_path):
                os.remove(temp_upload_path)

    def _upload_audio_file_with_retry(
        self, audio_file_path: str, max_retries: int = DEFAULT_UPLOAD_RETRIES
    ):
        """Upload a single audio file with retry handling."""
        audio_file = None
        for upload_retry in range(max_retries):
            try:
                audio_file = self._upload_audio_file(audio_file_path)
                print("✅ Upload complete")
                return audio_file
            except Exception as upload_e:
                if self._is_retryable_error(upload_e, NETWORK_ERROR_TERMS) and (
                    upload_retry < max_retries - 1
                ):
                    wait_time = min((upload_retry + 1) * 2, 30)
                    print(
                        f"⚠️  Upload failed (attempt "
                        f"{upload_retry + 1}/{max_retries}): {upload_e}"
                    )
                    print(f"🔄 Retrying upload in {wait_time} seconds...")
                    time.sleep(wait_time)
                    continue
                raise

        return audio_file

    @staticmethod
    def _find_vdj_stems_file(audio_file_path: str) -> Optional[str]:
        """Return the adjacent VDJ stems file path when it exists."""
        stems_path = f"{audio_file_path}.vdjstems"
        return stems_path if os.path.exists(stems_path) else None

    @staticmethod
    def _probe_vdj_stem_streams(vdj_stems_path: str) -> List[Tuple[str, int]]:
        """Read named audio streams from a VDJ stems Matroska file."""
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_streams",
                "-select_streams",
                "a",
                vdj_stems_path,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        probe_data = json.loads(result.stdout)
        streams = []

        for stream in probe_data.get("streams", []):
            title = stream.get("tags", {}).get("title", "").lower()
            index = stream.get("index")
            if title in VDJ_STEM_NAMES and index is not None:
                streams.append((title, index))

        return streams

    def _extract_vdj_stems(
        self, vdj_stems_path: str, output_dir: str
    ) -> List[Tuple[str, str]]:
        """Extract VDJ stem streams into small AAC files for model upload."""
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            print("⚠️  ffmpeg/ffprobe not found; skipping VDJ stem upload")
            return []

        stem_streams = self._probe_vdj_stem_streams(vdj_stems_path)
        extracted_files = []

        for stem_name, stream_index in stem_streams:
            output_path = os.path.join(output_dir, f"{stem_name}.m4a")
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    vdj_stems_path,
                    "-map",
                    f"0:{stream_index}",
                    "-c:a",
                    "copy",
                    output_path,
                ],
                check=True,
            )
            extracted_files.append((stem_name, output_path))

        return extracted_files

    def _prepare_vdj_stems_with_retry(
        self, audio_file_path: str
    ) -> Tuple[List[Tuple[str, object]], List[Tuple[str, str]], Optional[str]]:
        """Extract and upload adjacent VDJ stem files when available."""
        vdj_stems_path = self._find_vdj_stems_file(audio_file_path)
        if not vdj_stems_path:
            return [], [], None

        print(f"🧬 Found VDJ stems: {os.path.basename(vdj_stems_path)}")
        temp_dir = tempfile.mkdtemp(prefix="vdj-stems-")

        try:
            extracted_stems = self._extract_vdj_stems(vdj_stems_path, temp_dir)
            uploaded_stems = []

            for stem_name, stem_path in extracted_stems:
                file_size = os.path.getsize(stem_path) / (1024 * 1024)
                print(f"📤 Uploading {stem_name} stem ({file_size:.1f} MB)...")
                uploaded_stems.append(
                    (stem_name, self._upload_audio_file_with_retry(stem_path))
                )

            if uploaded_stems:
                print(f"✅ Uploaded {len(uploaded_stems)} VDJ stem files")
            return uploaded_stems, extracted_stems, temp_dir
        except Exception as e:
            print(f"⚠️  Could not use VDJ stems for {os.path.basename(audio_file_path)}: {e}")
            shutil.rmtree(temp_dir, ignore_errors=True)
            return [], [], None

    def _upload_vdj_stems_with_retry(self, audio_file_path: str) -> List[Tuple[str, object]]:
        """Upload adjacent VDJ stem files, then clean local temporary files."""
        stem_uploads, _, temp_dir = self._prepare_vdj_stems_with_retry(audio_file_path)
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
        return stem_uploads

    @staticmethod
    def _stem_upload_prompt(stem_uploads: List[Tuple[str, object]]) -> str:
        """Describe uploaded stem files and their order for Gemini."""
        if not stem_uploads:
            return (
                "Only the original full mix is uploaded. Infer elements from the "
                "full mix, then follow the strict label/color rules."
            )

        stem_lines = [
            f"- Uploaded file {index + 2}: isolated {stem_name} stem"
            for index, (stem_name, _) in enumerate(stem_uploads)
        ]
        return "\n".join(
            [
                "Uploaded audio files:",
                "- Uploaded file 1: original full mix",
                *stem_lines,
                "",
                "Use the isolated stems as evidence for element presence. For every",
                "cue and loop, set stem_activity for vocal, hihat, bass, instruments,",
                "and kick to one of: none, low, medium, high.",
            ]
        )

    @staticmethod
    def _volume_to_activity(mean_volume_db: Optional[float]) -> str:
        """Map ffmpeg volumedetect mean volume to a coarse activity level."""
        if mean_volume_db is None:
            return "none"
        if mean_volume_db > -25:
            return "high"
        if mean_volume_db > -38:
            return "medium"
        if mean_volume_db > -50:
            return "low"
        return "none"

    @staticmethod
    def _measure_mean_volume(
        audio_file_path: str, timestamp: float, duration_seconds: float = 4.0
    ) -> Optional[float]:
        """Measure mean volume around a timestamp using ffmpeg volumedetect."""
        start = max(float(timestamp) - (duration_seconds / 2), 0.0)
        result = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostats",
                "-ss",
                f"{start:.3f}",
                "-t",
                f"{duration_seconds:.3f}",
                "-i",
                audio_file_path,
                "-af",
                "volumedetect",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        match = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?) dB", result.stderr)
        return float(match.group(1)) if match else None

    def _measure_stem_activity(
        self, stem_files: List[Tuple[str, str]], timestamp: float
    ) -> Dict:
        """Measure activity for every extracted stem near a timestamp."""
        activity = {}
        for stem_name, stem_path in stem_files:
            mean_volume = self._measure_mean_volume(stem_path, timestamp)
            activity[stem_name] = self._volume_to_activity(mean_volume)
        return activity

    def _apply_measured_stem_activity(
        self, analysis_data: Dict, stem_files: List[Tuple[str, str]]
    ) -> Dict:
        """Replace model-reported stem activity with measured stem activity."""
        if not stem_files:
            return analysis_data

        for cue_data in analysis_data.get("measure_changes", []):
            timestamp = cue_data.get("timestamp")
            if timestamp is not None:
                cue_data["stem_activity"] = self._measure_stem_activity(
                    stem_files, float(timestamp)
                )

        for loop_data in analysis_data.get("loop_segments", []):
            timestamp = loop_data.get("start")
            if timestamp is not None:
                loop_data["stem_activity"] = self._measure_stem_activity(
                    stem_files, float(timestamp)
                )

        return analysis_data

    @staticmethod
    def _parse_json_response(response_text: str) -> Dict:
        """Parse Gemini JSON while normalizing overly precise decimal output."""
        cleaned_text = re.sub(
            r"(\d+\.\d{10,})",
            lambda m: f"{float(m.group(1)):.2f}",
            response_text,
        )
        return json.loads(cleaned_text)

    @staticmethod
    def _round_analysis_timestamps(analysis_data: Dict) -> Dict:
        """Normalize cue and loop timestamps to two decimal places."""
        if "measure_changes" in analysis_data:
            for cue in analysis_data["measure_changes"]:
                if "timestamp" in cue:
                    cue["timestamp"] = round(float(cue["timestamp"]), 2)

        if "loop_segments" in analysis_data:
            for loop in analysis_data["loop_segments"]:
                if "start" in loop:
                    loop["start"] = round(float(loop["start"]), 2)

        return analysis_data

    def _generate_json_content(
        self,
        contents: List[object],
        schema: Type[BaseModel],
        timeout_seconds: int,
        max_retries: int = DEFAULT_ANALYSIS_RETRIES,
    ) -> Dict:
        """Call Gemini with structured JSON output and retry temporary failures."""
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_json_schema=schema.model_json_schema(),
            http_options=types.HttpOptions(timeout=timeout_seconds * 1000),
        )

        for analysis_retry in range(max_retries):
            try:
                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=contents,
                    config=config,
                )
                if not response or not response.text:
                    raise ValueError("Empty response from Gemini")
                return self._parse_json_response(response.text)
            except Exception as analysis_e:
                if self._is_retryable_error(analysis_e) and (
                    analysis_retry < max_retries - 1
                ):
                    wait_time = min((analysis_retry + 1) * 3, 30)
                    print(
                        f"⚠️  Analysis failed (attempt "
                        f"{analysis_retry + 1}/{max_retries}): {analysis_e}"
                    )
                    print(f"🔄 Retrying analysis in {wait_time} seconds...")
                    time.sleep(wait_time)
                    continue

                print(f"⚠️  Gemini API error: {analysis_e}")
                raise

        raise RuntimeError("Failed to get analysis response after retries")

    def _generate_music_analysis(
        self,
        prompt: str,
        audio_file,
        stem_uploads: Optional[List[Tuple[str, object]]] = None,
        stem_files: Optional[List[Tuple[str, str]]] = None,
    ) -> Dict:
        """Generate and normalize structured analysis for one uploaded file."""
        stem_uploads = stem_uploads or []
        stem_files = stem_files or []
        analysis_data = self._generate_json_content(
            contents=[prompt, audio_file] + [uploaded for _, uploaded in stem_uploads],
            schema=MusicAnalysis,
            timeout_seconds=180,
        )
        analysis_data = self._apply_measured_stem_activity(analysis_data, stem_files)
        return self._normalize_analysis_data(analysis_data)

    def analyze_audio_with_gemini(self, audio_file_path: str, uploaded_file=None) -> Dict:
        """Send audio file to Gemini Pro for musical analysis"""
        print(f"🔍 Analyzing {os.path.basename(audio_file_path)} with Gemini...")

        # Get song length for validation
        song_length = self.get_song_length(audio_file_path) or 300  # fallback to 5 min

        try:
            # Upload only when the caller has not already uploaded this file.
            audio_file = uploaded_file
            if audio_file is None:
                print(
                    f"📤 Uploading audio file "
                    f"({os.path.getsize(audio_file_path) / 1024 / 1024:.1f} MB)..."
                )
                audio_file = self._upload_audio_file_with_retry(audio_file_path)
            else:
                print(f"📎 Reusing uploaded file for {os.path.basename(audio_file_path)}")

            stem_uploads, stem_files, stem_temp_dir = self._prepare_vdj_stems_with_retry(
                audio_file_path
            )
            try:
                stem_prompt = self._stem_upload_prompt(stem_uploads)

                prompt = f"""
                You are analyzing a DJ track for precise cue point placement.
                Listen to the ENTIRE audio file carefully.

                Song Information:
                - Length: {song_length:.1f} seconds
                - BPM: {self.get_song_bpm_from_database(audio_file_path) or 'Unknown'}
                - File: {os.path.basename(audio_file_path)}

                {stem_prompt}

                CRITICAL TIMING INSTRUCTIONS:
                1. Listen to the actual audio - do NOT make assumptions based on filename
                2. Pay attention to when elements ACTUALLY start/stop, not when you think
                   they should
                3. For vocals, listen for actual singing voices, not just background sounds
                4. For drums, identify when the kick/snare pattern begins, not just
                   percussion
                5. Be very conservative - only mark transitions where you clearly hear
                   changes

                Find 5-6 significant musical changes where elements ACTUALLY change:
                - Real intro (before main elements start)
                - When drums ACTUALLY enter (not just percussion)
                - When vocals ACTUALLY start singing (not just vocal sounds)
                - Breakdown sections (where elements drop out)
                - Drops/build-ups (energy changes)

                Find 3 loop sections for DJing (16-32 beats long).
                IMPORTANT: Try to find ALL THREE types:
                1. DRUM LOOP (highest priority): A section with ONLY drums/percussion,
                   no melody, no vocals - perfect for DJ transitions
                2. VOCAL LOOP: A section with prominent vocals (with or without other
                   elements) - great for crowd engagement
                3. MELODIC LOOP: A section with melody (synth/piano/guitar) but NO drums
                   and NO vocals - for smooth transitions

                Search the ENTIRE track to find these three distinct loop types.
                DJs need variety!

                Element Detection:
                - drums: Kick/snare patterns, not just hi-hats
                - vocals: Actual singing/rapping, not just vocal effects
                - bass: Prominent bassline
                - synth/piano: Melodic elements
                - Include every clearly audible element. If bass, synth, vocals, pads,
                  or effects are audible during a drum section, it is NOT drums-only.

                Strict Label Rules:
                - Only use "Melodic" or "Melody" in a name when there is a clear
                  foreground melody and NO audible drums or vocals.
                - Bass alone, pads, texture, atmosphere, or filtered chord wash are
                  NOT enough to call a section melodic. Name those by the actual
                  element instead, like "Bass Break" or "Synth Break".
                - Only use "Drum", "Drums", or "Percussion" in a name when drums are
                  isolated and no bass, synth, melody, vocal, pad, or tonal element is
                  audible.
                - If a section has drums plus other elements, use neutral names like
                  "Rhythm Section", "Groove", "Build", "Drop", or "Outro".
                - If you are uncertain whether other elements are present, include
                  those elements and avoid "drums-only" or "melodic-only" names/colors.

                Color Rules (be strict):
                - blue: Only melody, NO drums, NO vocals
                - green: Melody + drums, NO vocals
                - yellow: Full mix (drums + melody + vocals)
                - purple: Only drums/percussion
                - orange: Melody + vocals, NO drums

                RESPONSE FORMAT REQUIREMENTS:
                - All timestamps must be rounded to 2 decimal places (e.g., 45.67)
                - Each cue must have: timestamp, elements (array), cue_name (string),
                  color (string), stem_activity (object)
                - Each loop must have: start, length_beats, elements (array),
                  loop_name (string), color (string), stem_activity (object)
                - stem_activity must include vocal, hihat, bass, instruments, and kick,
                  each set to one of: none, low, medium, high
                - Use descriptive names like "Intro", "Drums In", "Vocal Drop",
                  "Build Up", "Breakdown"
                - NEVER use extremely long decimal numbers

                IMPORTANT: If you're not 100% sure about timing, be conservative and
                don't add that cue.

                LOOP REQUIREMENTS:
                - You MUST search for all 3 loop types (drum, vocal, melodic)
                - Even if a track is mostly instrumental, find the best vocal section
                  you can
                - Even if a track has constant drums, find a drum-only break somewhere
                - Prioritize quality over quantity - find the BEST example of each loop
                  type
                """

                print("🤖 Analyzing audio with Gemini...")
                analysis_data = self._generate_music_analysis(
                    prompt, audio_file, stem_uploads, stem_files
                )
            finally:
                if stem_temp_dir:
                    shutil.rmtree(stem_temp_dir, ignore_errors=True)

            print(
                f"✅ Analysis complete: "
                f"{len(analysis_data.get('measure_changes', []))} cues, "
                f"{len(analysis_data.get('loop_segments', []))} loops"
            )

            print("\n🔍 DEBUG - Structured output timestamps:")
            for i, cue in enumerate(analysis_data.get("measure_changes", []), 1):
                print(
                    f"  Cue {i}: {cue.get('cue_name', 'unnamed')} at "
                    f"{cue.get('timestamp', 0)}s - "
                    f"{cue.get('elements', [])} - Color: "
                    f"{cue.get('color', 'none')}"
                )
            for i, loop in enumerate(analysis_data.get("loop_segments", []), 1):
                print(
                    f"  Loop {i}: {loop.get('loop_name', 'unnamed')} at "
                    f"{loop.get('start', 0)}s "
                    f"({loop.get('length_beats', 0)} beats) - Color: "
                    f"{loop.get('color', 'none')}"
                )
            print()

            return analysis_data

        except json.JSONDecodeError as e:
            print(f"❌ Failed to parse structured JSON response: {e}")
            return None
        except Exception as e:
            import traceback

            print(f"❌ Error analyzing audio with Gemini: {e}")
            print("🔍 Full traceback:")
            traceback.print_exc()
            return None

    def get_song_bpm_from_database(self, file_path: str) -> Optional[float]:
        """Extract BPM from VDJ database for timing validation"""
        try:
            root = self.parse_vdj_database()
            if root is None:
                return None

            for song in root.findall("Song"):
                if song.get("FilePath") == file_path:
                    # Try Scan element first (more accurate)
                    scan = song.find("Scan")
                    if scan is not None:
                        bpm_str = scan.get("Bpm", "0")
                        vdj_bpm = float(bpm_str)
                        # VDJ stores BPM as fractional value, convert to actual
                        # Formula: actual_bpm = 60 / vdj_bpm (approximately)
                        if vdj_bpm > 0:
                            actual_bpm = 60.0 / vdj_bpm
                            # Sanity check - if BPM seems wrong, try different
                            if actual_bpm < 60 or actual_bpm > 200:
                                # Try alternative: maybe it's already in BPM
                                if vdj_bpm > 60 and vdj_bpm < 200:
                                    actual_bpm = vdj_bpm
                                    print(
                                        f"🎵 VDJ BPM: {vdj_bpm:.6f} (direct) → "
                                        f"Actual BPM: {actual_bpm:.1f}"
                                    )
                                else:
                                    # Try another common conversion
                                    actual_bpm = vdj_bpm * 120
                                    if actual_bpm > 200:
                                        actual_bpm = 120  # fallback
                                    print(
                                        f"🎵 VDJ BPM: {vdj_bpm:.6f} (alt "
                                        f"conversion) → Actual BPM: "
                                        f"{actual_bpm:.1f}"
                                    )
                            else:
                                print(
                                    f"🎵 VDJ BPM: {vdj_bpm:.6f} → Actual BPM: "
                                    f"{actual_bpm:.1f}"
                                )
                            return actual_bpm

                    # Fallback to Tags element
                    tags = song.find("Tags")
                    if tags is not None:
                        bpm_str = tags.get("Bpm", "0")
                        vdj_bpm = float(bpm_str)
                        if vdj_bpm > 0:
                            actual_bpm = 60.0 / vdj_bpm
                            print(
                                f"🎵 VDJ BPM (Tags): {vdj_bpm:.6f} → "
                                f"Actual BPM: {actual_bpm:.1f}"
                            )
                            return actual_bpm
            return None
        except ET.ParseError as e:
            print(f"⚠️  VDJ database XML is corrupted: {e}")
            print("⚠️  Using fallback BPM estimation")
            return None
        except Exception as e:
            print(f"⚠️  Could not get BPM from database: {e}")
            return None

    def validate_color_assignment(self, elements: List[str], gemini_color: str) -> str:
        """Validate and correct color assignment based on elements"""
        # Separate drums from light percussion
        has_drums = "drums" in elements
        has_light_percussion = "percussion" in elements and not has_drums
        has_vocals = "vocals" in elements
        has_melody = any(
            elem in elements for elem in ["piano", "synth", "strings", "guitar", "bass"]
        )

        # Strict color rules based on your feedback
        if has_vocals and has_drums:
            return "yellow"  # Full mix (vocals + drums = yellow, NOT green)
        elif has_drums and not has_vocals:
            # Check if it's drums/percussion focused (for purple)
            non_drum_elements = [
                e for e in elements if e not in ["drums", "percussion"]
            ]
            if not non_drum_elements:
                return "purple"  # Drums/percussion only
            else:
                # Drums with melody = green
                return "green"  # Melodic + drums
        elif has_vocals and not has_drums:
            return "orange"  # Melodic + vocals only
        elif not has_vocals and not has_drums:
            # No drums, no vocals - check what we have
            if has_melody:
                return "blue"  # Melodic only (including light percussion)
            elif has_light_percussion and len(elements) == 1:
                # Only use purple if percussion is the ONLY element
                return "purple"  # Percussion dominant
            else:
                return "blue"  # Default to blue for melodic content
        else:
            # Fallback to Gemini's suggestion
            return gemini_color

    @staticmethod
    def _has_drums(elements: List[str]) -> bool:
        return any(elem in elements for elem in ["drums", "percussion"])

    @staticmethod
    def _has_vocals(elements: List[str]) -> bool:
        return "vocals" in elements

    @staticmethod
    def _melodic_elements(elements: List[str]) -> List[str]:
        return [
            elem
            for elem in elements
            if elem in ["piano", "synth", "strings", "guitar", "bass"]
        ]

    @staticmethod
    def _activity_is_active(activity: Optional[str]) -> bool:
        if activity is None:
            return False
        if isinstance(activity, (int, float)):
            return activity >= 0.35
        return str(activity).strip().lower() in {"medium", "high", "active", "present"}

    @staticmethod
    def _stem_activity_dict(item_data: Dict) -> Dict:
        activity = item_data.get("stem_activity") or {}
        if isinstance(activity, BaseModel):
            return activity.model_dump()
        return activity if isinstance(activity, dict) else {}

    @staticmethod
    def _normalize_elements(elements: List[str]) -> List[str]:
        """Map raw model/stem names onto the app's supported element vocabulary."""
        aliases = {
            "vocal": "vocals",
            "voice": "vocals",
            "voices": "vocals",
            "kick": "drums",
            "hihat": "drums",
            "hi-hat": "drums",
            "hi_hat": "drums",
            "percussion": "drums",
            "instrument": "synth",
            "instruments": "synth",
            "melody": "synth",
            "melodic": "synth",
        }
        supported = {"drums", "vocals", "bass", "piano", "synth", "strings", "guitar"}
        normalized = []

        for element in elements:
            normalized_element = aliases.get(str(element).lower(), str(element).lower())
            if normalized_element in supported and normalized_element not in normalized:
                normalized.append(normalized_element)

        return normalized

    def _apply_stem_activity_to_elements(
        self, elements: List[str], stem_activity: Dict
    ) -> List[str]:
        """Use stem activity to correct the model's element list."""
        elements = self._normalize_elements(elements)
        if not stem_activity:
            return elements

        corrected = list(elements)

        def ensure_element(element: str):
            if element not in corrected:
                corrected.append(element)

        def remove_elements(elements_to_remove: List[str]):
            corrected[:] = [
                element for element in corrected if element not in elements_to_remove
            ]

        drums_active = self._activity_is_active(
            stem_activity.get("kick")
        ) or self._activity_is_active(stem_activity.get("hihat"))
        vocal_active = self._activity_is_active(stem_activity.get("vocal"))
        bass_active = self._activity_is_active(stem_activity.get("bass"))
        instruments_active = self._activity_is_active(
            stem_activity.get("instruments")
        )

        if drums_active:
            ensure_element("drums")
        else:
            remove_elements(["drums", "percussion"])

        if vocal_active:
            ensure_element("vocals")
        else:
            remove_elements(["vocals"])

        if bass_active:
            ensure_element("bass")
        else:
            remove_elements(["bass"])

        if instruments_active:
            if not any(
                element in corrected for element in ["piano", "synth", "strings", "guitar"]
            ):
                ensure_element("synth")
        else:
            remove_elements(["piano", "synth", "strings", "guitar"])

        return corrected

    def _is_drum_only(self, elements: List[str]) -> bool:
        return bool(elements) and self._has_drums(elements) and all(
            elem in ["drums", "percussion"] for elem in elements
        )

    def _is_melody_only(self, elements: List[str]) -> bool:
        melodic_elements = self._melodic_elements(elements)
        return bool(melodic_elements) and not self._has_drums(
            elements
        ) and not self._has_vocals(elements)

    def _element_label(self, elements: List[str]) -> str:
        """Create a neutral label that matches the returned elements."""
        has_drums = self._has_drums(elements)
        has_vocals = self._has_vocals(elements)
        melodic_elements = self._melodic_elements(elements)

        if has_vocals and has_drums:
            return "Vocal Mix"
        if has_vocals:
            return "Vocal Break"
        if has_drums and not self._is_drum_only(elements):
            return "Rhythm Section"
        if self._is_drum_only(elements):
            return "Drums"
        if melodic_elements:
            return " ".join(elem.capitalize() for elem in melodic_elements[:2])
        return "Section"

    @staticmethod
    def _preserved_position_prefix(name: str) -> str:
        lower_name = name.lower()
        for prefix in ["intro", "outro", "breakdown", "build", "drop"]:
            if prefix in lower_name:
                return prefix.capitalize()
        return ""

    def _replacement_name(
        self, original_name: str, elements: List[str], is_loop: bool
    ) -> str:
        base_label = self._element_label(elements)
        prefix = self._preserved_position_prefix(original_name)

        if prefix and not base_label.lower().startswith(prefix.lower()):
            base_label = f"{prefix} {base_label}"

        return base_label

    def _name_conflicts_with_elements(self, name: str, elements: List[str]) -> bool:
        """Detect misleading model labels from the model's own element list."""
        lower_name = name.lower()
        mentions_melody = "melodic" in lower_name or "melody" in lower_name
        mentions_drums = "drum" in lower_name or "percussion" in lower_name
        mentions_vocals = "vocal" in lower_name or "acapella" in lower_name
        mentions_instrumental = "instrumental" in lower_name
        mentions_bass = "bass" in lower_name
        mentions_synth = "synth" in lower_name
        mentions_piano = "piano" in lower_name
        mentions_guitar = "guitar" in lower_name
        mentions_strings = "string" in lower_name

        if mentions_melody:
            # Generic melody labels are too often hallucinated; prefer instruments.
            return True
        if mentions_instrumental and self._has_vocals(elements):
            return True
        if mentions_drums and not self._is_drum_only(elements):
            return True
        if mentions_vocals and not self._has_vocals(elements):
            return True
        if mentions_bass and "bass" not in elements:
            return True
        if mentions_synth and "synth" not in elements:
            return True
        if mentions_piano and "piano" not in elements:
            return True
        if mentions_guitar and "guitar" not in elements:
            return True
        if mentions_strings and "strings" not in elements:
            return True
        return False

    def _normalize_analysis_data(self, analysis_data: Dict) -> Dict:
        """Normalize timestamps, colors, and misleading Gemini labels."""
        analysis_data = self._round_analysis_timestamps(analysis_data)

        for cue_data in analysis_data.get("measure_changes", []):
            stem_activity = self._stem_activity_dict(cue_data)
            elements = self._apply_stem_activity_to_elements(
                cue_data.get("elements", []), stem_activity
            )
            cue_data["elements"] = elements
            color = cue_data.get("color", "green")
            cue_data["color"] = self.validate_color_assignment(elements, color)

            cue_name = cue_data.get("cue_name", "")
            if self._name_conflicts_with_elements(cue_name, elements):
                cue_data["cue_name"] = self._replacement_name(
                    cue_name, elements, is_loop=False
                )

        for loop_data in analysis_data.get("loop_segments", []):
            stem_activity = self._stem_activity_dict(loop_data)
            elements = self._apply_stem_activity_to_elements(
                loop_data.get("elements", []), stem_activity
            )
            loop_data["elements"] = elements
            color = loop_data.get("color", "green")
            loop_data["color"] = self.validate_color_assignment(elements, color)

            loop_name = loop_data.get("loop_name", "")
            if self._name_conflicts_with_elements(loop_name, elements):
                loop_data["loop_name"] = self._replacement_name(
                    loop_name, elements, is_loop=True
                )

        return analysis_data

    def create_cue_name(self, elements: List[str], measure: int) -> str:
        """Generate descriptive cue name based on detected elements"""
        # Sort elements for consistent naming
        sorted_elements = sorted(elements)

        # Create descriptive combinations
        if "vocals" in elements:
            # Combine vocal with other prominent elements
            other_elements = [e for e in sorted_elements if e != "vocals"]
            if "synth" in other_elements:
                return "vocalsynth"
            elif "piano" in other_elements:
                return "vocalpiano"
            elif "drums" in other_elements:
                return "vocaldrums"
            elif "guitar" in other_elements:
                return "vocalguitar"
            elif "strings" in other_elements:
                return "vocalstrings"
            elif "bass" in other_elements:
                return "vocalbass"
            else:
                return "vocals"
        elif "piano" in elements and "synth" in elements:
            return "pianosynth"
        elif "drums" in elements and "bass" in elements:
            return "drumsBass"
        elif "piano" in elements:
            return "piano"
        elif "synth" in elements:
            return "synth"
        elif "strings" in elements:
            return "strings"
        elif "guitar" in elements:
            return "guitar"
        elif "drums" in elements:
            return "drums"
        elif "bass" in elements:
            return "bass"
        else:
            # Use combination of first two elements or measure number
            if len(sorted_elements) >= 2:
                return f"{sorted_elements[0]}{sorted_elements[1]}"
            elif len(sorted_elements) == 1:
                return sorted_elements[0]
            else:
                return f"mix{measure}"

    def create_loop_name(self, elements: List[str]) -> str:
        """Generate loop name with 'l' suffix"""
        base_name = self.create_cue_name(elements, 0)
        if base_name.startswith("mix"):
            return "loopl"
        return f"{base_name}l"

    def preprocess_xml_for_parsing(self, xml_content: str) -> str:
        """Clean up XML content for Python's ElementTree parser"""
        import re

        # Remove any null bytes or control characters
        # (except tab, newline, carriage return)
        xml_content = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", xml_content)

        # Fix any duplicate closing tags by removing extras
        # This pattern looks for duplicate closing tags like </Song>\n</Song>
        xml_content = re.sub(r"(</[^>]+>)\s*\1+", r"\1", xml_content)

        # Remove any duplicate root closing tags
        xml_content = re.sub(
            r"(</VirtualDJ_Database>)\s*</VirtualDJ_Database>",
            r"\1",
            xml_content,
        )

        # Remove any stray content after the root closing tag
        if "</VirtualDJ_Database>" in xml_content:
            xml_content = (
                xml_content.split("</VirtualDJ_Database>")[0] + "</VirtualDJ_Database>"
            )

        return xml_content

    def parse_vdj_database(self):
        """Parse VDJ database with preprocessing for compatibility"""
        try:
            # Read the raw XML content
            with open(self.vdj_database_path, "r", encoding="utf-8") as f:
                xml_content = f.read()

            # Preprocess for Python parser compatibility
            cleaned_xml = self.preprocess_xml_for_parsing(xml_content)

            # Parse the cleaned XML
            root = ET.fromstring(cleaned_xml)
            return root
        except Exception as e:
            print(f"⚠️  Could not parse VDJ database: {e}")
            return None

    def get_beatgrid_offset(self, file_path: str) -> float:
        """Get beatgrid offset (where '1' beat starts) from VDJ database"""
        try:
            root = self.parse_vdj_database()
            if root is None:
                return 0.0

            for song in root.findall("Song"):
                if song.get("FilePath") == file_path:
                    for poi in song.findall("Poi"):
                        if poi.get("Type") == "beatgrid":
                            return float(poi.get("Pos", "0"))
            return 0.0  # Default if no beatgrid found
        except Exception as e:
            print(f"⚠️  Could not get beatgrid offset: {e}")
            return 0.0

    @staticmethod
    def _actual_bpm(bpm: float) -> Optional[float]:
        """Normalize VDJ fractional BPM or direct BPM into actual BPM."""
        if bpm <= 0:
            return None
        actual_bpm = 60.0 / bpm if bpm < 5 else bpm
        if actual_bpm < 60 or actual_bpm > 200:
            return None
        return actual_bpm

    @staticmethod
    def _choose_best_downbeat_phase(
        current_offset: float, beat_duration: float, phase_scores: Dict[int, float]
    ) -> BeatgridAlignment:
        """Pick a stronger whole-beat downbeat phase when confidence is high."""
        current_score = phase_scores.get(0, 0.0)
        best_score = max(phase_scores.values() or [0.0])
        confidence_ratio = best_score / max(current_score, 0.001)

        if best_score < 0.02:
            return BeatgridAlignment(
                offset=current_offset,
                confidence_ratio=confidence_ratio,
                phase_scores=phase_scores,
            )

        if current_score and (
            confidence_ratio < 1.75 or (best_score - current_score) < 0.04
        ):
            return BeatgridAlignment(
                offset=current_offset,
                confidence_ratio=confidence_ratio,
                phase_scores=phase_scores,
            )

        near_best_tolerance = max(0.01, best_score * 0.05)
        near_best_phases = [
            phase
            for phase, score in phase_scores.items()
            if best_score - score <= near_best_tolerance
        ]

        if 0 in near_best_phases:
            shift_beats = 0
        else:
            non_zero_phases = sorted(phase for phase in near_best_phases if phase != 0)
            shift_beats = non_zero_phases[0] if non_zero_phases else 0

        return BeatgridAlignment(
            offset=current_offset + (shift_beats * beat_duration),
            shift_beats=shift_beats,
            corrected=shift_beats != 0,
            confidence_ratio=confidence_ratio,
            phase_scores=phase_scores,
        )

    @staticmethod
    def _choose_best_beat_offset(
        current_offset: float,
        beat_duration: float,
        current_score: float,
        best_offset: float,
        best_score: float,
        source: str,
    ) -> BeatgridAlignment:
        """Pick a fine beat-grid offset only with strong kick-stem evidence."""
        shift_seconds = best_offset - current_offset
        confidence_ratio = best_score / max(current_score, 0.001)

        if source != "kick stem":
            return BeatgridAlignment(
                offset=current_offset,
                confidence_ratio=confidence_ratio,
                fine_shift_seconds=0.0,
                beat_score=current_score,
                best_beat_score=best_score,
                source=source,
            )

        if abs(shift_seconds) < BEATGRID_FINE_ALIGNMENT_MIN_SHIFT_SECONDS:
            return BeatgridAlignment(
                offset=current_offset,
                confidence_ratio=confidence_ratio,
                fine_shift_seconds=0.0,
                beat_score=current_score,
                best_beat_score=best_score,
                source=source,
            )

        if (
            best_score < BEATGRID_FINE_ALIGNMENT_MIN_SCORE
            or (best_score - current_score) < BEATGRID_FINE_ALIGNMENT_MIN_GAIN
            or confidence_ratio < BEATGRID_FINE_ALIGNMENT_MIN_RATIO
        ):
            return BeatgridAlignment(
                offset=current_offset,
                confidence_ratio=confidence_ratio,
                fine_shift_seconds=0.0,
                beat_score=current_score,
                best_beat_score=best_score,
                source=source,
            )

        # Only correct within half a beat. Larger shifts are bar-phase decisions.
        if abs(shift_seconds) > (beat_duration * 0.5):
            return BeatgridAlignment(
                offset=current_offset,
                confidence_ratio=confidence_ratio,
                fine_shift_seconds=0.0,
                beat_score=current_score,
                best_beat_score=best_score,
                source=source,
            )

        return BeatgridAlignment(
            offset=best_offset,
            corrected=True,
            confidence_ratio=confidence_ratio,
            fine_shift_seconds=shift_seconds,
            beat_score=current_score,
            best_beat_score=best_score,
            source=source,
        )

    @staticmethod
    def _choose_consensus_downbeat_phase(
        current_offset: float,
        beat_duration: float,
        source_phase_scores: List[Tuple[str, Dict[int, float]]],
    ) -> BeatgridAlignment:
        """Use multi-source agreement to correct a weak downbeat phase."""
        aggregate_scores = {phase: 0.0 for phase in range(4)}
        best_phase_counts = {phase: 0 for phase in range(4)}
        used_sources = 0

        for _, phase_scores in source_phase_scores:
            if not phase_scores:
                continue
            best_phase = max(phase_scores, key=phase_scores.get)
            best_score = phase_scores[best_phase]
            if best_score < BEATGRID_PHASE_SOURCE_MIN_SCORE:
                continue

            used_sources += 1
            best_phase_counts[best_phase] += 1
            for phase, score in phase_scores.items():
                aggregate_scores[phase] += score

        if used_sources < BEATGRID_PHASE_CONSENSUS_MIN_SOURCES:
            return BeatgridAlignment(
                offset=current_offset,
                phase_scores=aggregate_scores,
                source="multi-source consensus",
            )

        top_phase = max(aggregate_scores, key=aggregate_scores.get)
        current_score = aggregate_scores.get(0, 0.0)
        top_score = aggregate_scores[top_phase]
        confidence_ratio = top_score / max(current_score, 0.001)

        if top_phase == 0:
            return BeatgridAlignment(
                offset=current_offset,
                confidence_ratio=confidence_ratio,
                phase_scores=aggregate_scores,
                source="multi-source consensus",
            )

        if best_phase_counts[top_phase] < BEATGRID_PHASE_CONSENSUS_MIN_SOURCES:
            return BeatgridAlignment(
                offset=current_offset,
                confidence_ratio=confidence_ratio,
                phase_scores=aggregate_scores,
                source="multi-source consensus",
            )

        if (
            confidence_ratio < BEATGRID_PHASE_CONSENSUS_MIN_RATIO
            or (top_score - current_score) < BEATGRID_PHASE_CONSENSUS_MIN_GAIN
        ):
            return BeatgridAlignment(
                offset=current_offset,
                confidence_ratio=confidence_ratio,
                phase_scores=aggregate_scores,
                source="multi-source consensus",
            )

        return BeatgridAlignment(
            offset=current_offset + (top_phase * beat_duration),
            shift_beats=top_phase,
            corrected=True,
            confidence_ratio=confidence_ratio,
            phase_scores=aggregate_scores,
            source="multi-source consensus",
        )

    def _beatgrid_audio_sources(
        self, audio_file_path: str
    ) -> List[Tuple[str, str, Optional[str]]]:
        """Return candidate audio sources for beatgrid verification."""
        sources = []
        stems_path = self._find_vdj_stems_file(audio_file_path)
        if stems_path and shutil.which("ffmpeg") and shutil.which("ffprobe"):
            try:
                stream_map = {
                    stem_name: stream_index
                    for stem_name, stream_index in self._probe_vdj_stem_streams(
                        stems_path
                    )
                }
                for stem_name in ("kick", "hihat", "bass", "instruments", "vocal"):
                    if stem_name in stream_map:
                        sources.append(
                            (
                                f"{stem_name} stem",
                                stems_path,
                                f"0:{stream_map[stem_name]}",
                            )
                        )
            except Exception as e:
                print(f"⚠️  Could not inspect VDJ stems for beatgrid: {e}")

        sources.append(("mix", audio_file_path, None))
        return sources

    def _beatgrid_audio_source(
        self, audio_file_path: str
    ) -> Tuple[str, Optional[str], str]:
        """Prefer the VDJ kick stem for beatgrid verification when available."""
        for source_name, source_path, stream_map in self._beatgrid_audio_sources(
            audio_file_path
        ):
            if source_name == "kick stem":
                return source_path, stream_map, source_name

        return audio_file_path, None, "mix"

    def _extract_onset_envelope(
        self, audio_file_path: str, stream_map: Optional[str]
    ) -> Tuple[List[float], float]:
        """Extract a compact positive-difference energy envelope via ffmpeg."""
        if not shutil.which("ffmpeg"):
            return [], BEATGRID_ALIGNMENT_HOP_SECONDS

        sample_rate = BEATGRID_ALIGNMENT_SAMPLE_RATE
        frame_samples = max(
            1, int(sample_rate * BEATGRID_ALIGNMENT_FRAME_SECONDS)
        )
        hop_samples = max(1, int(sample_rate * BEATGRID_ALIGNMENT_HOP_SECONDS))
        hop_seconds = hop_samples / sample_rate

        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            "0",
            "-t",
            str(BEATGRID_ALIGNMENT_DURATION_SECONDS),
            "-i",
            audio_file_path,
        ]
        if stream_map:
            command.extend(["-map", stream_map])
        command.extend(
            [
                "-ac",
                "1",
                "-ar",
                str(sample_rate),
                "-f",
                "s16le",
                "-",
            ]
        )

        result = subprocess.run(command, capture_output=True, check=True)
        if not result.stdout:
            return [], hop_seconds

        sample_count = len(result.stdout) // 2
        samples = struct.unpack(f"<{sample_count}h", result.stdout)
        if len(samples) < frame_samples:
            return [], hop_seconds

        energies = []
        for start in range(0, len(samples) - frame_samples, hop_samples):
            frame = samples[start : start + frame_samples]
            square_sum = sum(sample * sample for sample in frame)
            rms = math.sqrt(square_sum / frame_samples) / 32768.0
            energies.append(rms)

        if not energies:
            return [], hop_seconds

        onsets = [0.0]
        for index in range(1, len(energies)):
            onsets.append(max(0.0, energies[index] - energies[index - 1]))

        return onsets, hop_seconds

    @staticmethod
    def _score_downbeat_phase(
        onsets: List[float],
        hop_seconds: float,
        offset: float,
        measure_duration: float,
    ) -> float:
        """Score how much onset energy appears near each bar downbeat."""
        if not onsets or hop_seconds <= 0 or measure_duration <= 0:
            return 0.0

        radius = max(1, int(0.06 / hop_seconds))
        end_time = len(onsets) * hop_seconds
        score = 0.0
        count = 0
        timestamp = offset

        while timestamp < 0.5:
            timestamp += measure_duration

        while timestamp < end_time:
            center = int(round(timestamp / hop_seconds))
            lo = max(0, center - radius)
            hi = min(len(onsets), center + radius + 1)
            if hi > lo:
                score += max(onsets[lo:hi])
                count += 1
            timestamp += measure_duration

        return score / count if count else 0.0

    @staticmethod
    def _score_beat_grid(
        onsets: List[float],
        hop_seconds: float,
        offset: float,
        beat_duration: float,
    ) -> float:
        """Score onset energy near every beat at a fixed tempo."""
        if not onsets or hop_seconds <= 0 or beat_duration <= 0:
            return 0.0

        radius = max(1, int(0.06 / hop_seconds))
        end_time = len(onsets) * hop_seconds
        score = 0.0
        count = 0
        timestamp = offset

        while timestamp < 0.5:
            timestamp += beat_duration

        while timestamp < end_time:
            center = int(round(timestamp / hop_seconds))
            lo = max(0, center - radius)
            hi = min(len(onsets), center + radius + 1)
            if hi > lo:
                score += max(onsets[lo:hi])
                count += 1
            timestamp += beat_duration

        return score / count if count else 0.0

    def _find_best_fine_beat_offset(
        self,
        onsets: List[float],
        hop_seconds: float,
        current_offset: float,
        beat_duration: float,
        source: str,
    ) -> BeatgridAlignment:
        """Search within half a beat for a better fixed-tempo beat offset."""
        current_score = self._score_beat_grid(
            onsets, hop_seconds, current_offset, beat_duration
        )
        best_score = current_score
        best_offset = current_offset

        max_shift = beat_duration * 0.5
        steps = int(max_shift / BEATGRID_FINE_ALIGNMENT_STEP_SECONDS)
        for step in range(-steps, steps + 1):
            shift = step * BEATGRID_FINE_ALIGNMENT_STEP_SECONDS
            candidate_offset = current_offset + shift
            candidate_score = self._score_beat_grid(
                onsets, hop_seconds, candidate_offset, beat_duration
            )
            if candidate_score > best_score:
                best_score = candidate_score
                best_offset = candidate_offset

        return self._choose_best_beat_offset(
            current_offset=current_offset,
            beat_duration=beat_duration,
            current_score=current_score,
            best_offset=best_offset,
            best_score=best_score,
            source=source,
        )

    def _verify_beatgrid_alignment(
        self, audio_file_path: str, bpm: float
    ) -> BeatgridAlignment:
        """Validate VDJ beatgrid downbeat phase against audio transients."""
        actual_bpm = self._actual_bpm(bpm)
        current_offset = self.get_beatgrid_offset(audio_file_path)
        if actual_bpm is None:
            return BeatgridAlignment(offset=current_offset)

        cache_key = (audio_file_path, round(actual_bpm, 3))
        if cache_key in self._beatgrid_alignment_cache:
            return self._beatgrid_alignment_cache[cache_key]

        beat_duration = 60.0 / actual_bpm
        measure_duration = beat_duration * 4

        try:
            audio_sources = self._beatgrid_audio_sources(audio_file_path)
            source_path, stream_map, source_name = self._beatgrid_audio_source(
                audio_file_path
            )
            onsets, hop_seconds = self._extract_onset_envelope(source_path, stream_map)
            fine_alignment = self._find_best_fine_beat_offset(
                onsets,
                hop_seconds,
                current_offset,
                beat_duration,
                source_name,
            )
            base_offset = fine_alignment.offset
            phase_scores = {
                phase: self._score_downbeat_phase(
                    onsets,
                    hop_seconds,
                    base_offset + (phase * beat_duration),
                    measure_duration,
                )
                for phase in range(4)
            }
            alignment = self._choose_best_downbeat_phase(
                base_offset, beat_duration, phase_scores
            )
            alignment.source = source_name
            alignment.fine_shift_seconds = fine_alignment.fine_shift_seconds
            alignment.beat_score = fine_alignment.beat_score
            alignment.best_beat_score = fine_alignment.best_beat_score
            alignment.confidence_ratio = max(
                alignment.confidence_ratio, fine_alignment.confidence_ratio
            )
            alignment.corrected = alignment.corrected or fine_alignment.corrected
            primary_evidence = max(
                max(phase_scores.values() or [0.0]),
                fine_alignment.best_beat_score,
            )

            if (
                not alignment.corrected
                and primary_evidence < BEATGRID_PHASE_SOURCE_MIN_SCORE
            ):
                source_phase_scores = []
                for candidate_name, candidate_path, candidate_stream in audio_sources:
                    if (
                        candidate_name == source_name
                        and candidate_path == source_path
                        and candidate_stream == stream_map
                    ):
                        candidate_onsets = onsets
                        candidate_hop_seconds = hop_seconds
                    else:
                        candidate_onsets, candidate_hop_seconds = (
                            self._extract_onset_envelope(
                                candidate_path, candidate_stream
                            )
                        )

                    candidate_scores = {
                        phase: self._score_downbeat_phase(
                            candidate_onsets,
                            candidate_hop_seconds,
                            current_offset + (phase * beat_duration),
                            measure_duration,
                        )
                        for phase in range(4)
                    }
                    source_phase_scores.append((candidate_name, candidate_scores))

                consensus_alignment = self._choose_consensus_downbeat_phase(
                    current_offset, beat_duration, source_phase_scores
                )
                if consensus_alignment.corrected:
                    alignment = consensus_alignment
                    alignment.beat_score = fine_alignment.beat_score
                    alignment.best_beat_score = fine_alignment.best_beat_score

            if alignment.corrected:
                corrections = []
                if abs(alignment.fine_shift_seconds) > 0:
                    corrections.append(
                        f"fine {alignment.fine_shift_seconds:+.3f}s"
                    )
                if alignment.shift_beats:
                    corrections.append(f"phase +{alignment.shift_beats} beat")
                correction_text = ", ".join(corrections) or "verified"
                print(
                    f"🎚️  Beatgrid correction: "
                    f"{current_offset:.6f}s → {alignment.offset:.6f}s "
                    f"({correction_text}, {alignment.source}, "
                    f"confidence {alignment.confidence_ratio:.1f}x)"
                )
            else:
                print(
                    f"🎚️  Beatgrid downbeat looks usable at "
                    f"{current_offset:.6f}s ({alignment.source})"
                )
        except Exception as e:
            print(f"⚠️  Beatgrid verification failed; using VDJ grid: {e}")
            alignment = BeatgridAlignment(offset=current_offset)

        self._beatgrid_alignment_cache[cache_key] = alignment
        return alignment

    def _get_verified_beatgrid_offset(self, file_path: str, bpm: float) -> float:
        """Return a verified beatgrid offset for timing quantization."""
        return self._verify_beatgrid_alignment(file_path, bpm).offset

    def _apply_verified_beatgrid_to_song(
        self, song_element, audio_file_path: str, bpm: float
    ) -> None:
        """Persist a confident beatgrid correction into the VDJ song XML."""
        alignment = self._verify_beatgrid_alignment(audio_file_path, bpm)
        if not alignment.corrected:
            return

        beatgrid_poi = None
        for poi in song_element.findall("Poi"):
            if poi.get("Type") == "beatgrid":
                beatgrid_poi = poi
                break

        if beatgrid_poi is None:
            beatgrid_poi = ET.Element("Poi")
            beatgrid_poi.set("Type", "beatgrid")
            song_element.append(beatgrid_poi)

        beatgrid_poi.set("Pos", f"{alignment.offset:.6f}")
        print(f"✅ Updated VDJ beatgrid '1' to {alignment.offset:.6f}s")

    def validate_timing_hybrid(
        self, gemini_timestamp: float, bpm: float, file_path: str
    ) -> float:
        """Hybrid timing validation: use Gemini's timestamp if reasonable,
        otherwise align to nearest '1' beat"""
        # Get beatgrid info
        beatgrid_offset = self._get_verified_beatgrid_offset(file_path, bpm)

        actual_bpm = self._actual_bpm(bpm)
        if actual_bpm is None:
            print(
                f"🎯 Invalid BPM {bpm}, using Gemini timestamp as-is: "
                f"{gemini_timestamp:.1f}s"
            )
            return gemini_timestamp

        beat_duration = 60.0 / actual_bpm  # seconds per beat
        measure_duration = beat_duration * 4  # 4 beats per measure

        # Find possible "1" beats around Gemini's timestamp
        measures_from_beatgrid = (gemini_timestamp - beatgrid_offset) / measure_duration

        # Check both floor and ceiling to find the closest "1" beat
        measure_before = int(measures_from_beatgrid)
        measure_after = measure_before + 1

        beat_one_before = beatgrid_offset + (measure_before * measure_duration)
        beat_one_after = beatgrid_offset + (measure_after * measure_duration)

        # Calculate distances to both potential "1" beats
        distance_to_before = abs(gemini_timestamp - beat_one_before)
        distance_to_after = abs(gemini_timestamp - beat_one_after)

        # Choose the closer "1" beat
        if distance_to_before <= distance_to_after:
            nearest_beat_one = beat_one_before
            distance_to_beat_one = distance_to_before
        else:
            nearest_beat_one = beat_one_after
            distance_to_beat_one = distance_to_after

        # If Gemini's timestamp is within 1.5 seconds of a "1" beat,
        # use the "1" beat. This ensures alignment to the corrected beatgrid
        if distance_to_beat_one <= 1.5:  # Within 1.5 seconds tolerance
            print(
                f"🎯 Aligned: {gemini_timestamp:.1f}s → "
                f"{nearest_beat_one:.1f}s "
                f"(distance: {distance_to_beat_one:.1f}s)"
            )
            return nearest_beat_one
        else:
            print(
                f"🎯 Keeping Gemini timing: {gemini_timestamp:.1f}s "
                f"(distance to nearest '1': {distance_to_beat_one:.1f}s)"
            )
            return gemini_timestamp

    def get_song_length(self, file_path: str) -> Optional[float]:
        """Get song length from VDJ database"""
        try:
            root = self.parse_vdj_database()
            if root is None:
                return None

            for song in root.findall("Song"):
                if song.get("FilePath") == file_path:
                    infos = song.find("Infos")
                    if infos is not None:
                        length_str = infos.get("SongLength", "0")
                        return float(length_str)
            return None
        except Exception as e:
            print(f"⚠️  Could not get song length: {e}")
            return None

    async def upload_file_with_retry(
        self, audio_file_path: str, max_retries: int = 5
    ) -> Optional[object]:
        """Upload a single file with exponential backoff retry logic"""
        file_size = os.path.getsize(audio_file_path) / (1024 * 1024)  # MB
        print(
            f"📤 Uploading {os.path.basename(audio_file_path)} "
            f"({file_size:.1f} MB)..."
        )

        for retry in range(max_retries):
            try:
                uploaded_file = await asyncio.get_running_loop().run_in_executor(
                    None, self._upload_audio_file, audio_file_path
                )
                print(f"✅ {os.path.basename(audio_file_path)} upload complete")
                return uploaded_file
            except Exception as e:
                if self._is_retryable_error(e, NETWORK_ERROR_TERMS) and (
                    retry < max_retries - 1
                ):
                    wait_time = min(
                        (retry + 1) ** 2, 30
                    )  # Exponential backoff: 1s, 4s, 9s...
                    print(
                        f"⚠️  {os.path.basename(audio_file_path)} upload "
                        f"failed (attempt {retry + 1}/{max_retries}): {e}"
                    )
                    print(f"🔄 Retrying in {wait_time} seconds...")
                    await asyncio.sleep(wait_time)
                else:
                    print(
                        f"❌ Failed to upload {os.path.basename(audio_file_path)} "
                        f"after {max_retries} attempts: {e}"
                    )
                    return None

        return None

    async def process_audio_batch_async(
        self, audio_file_paths: List[str], dry_run: bool = False
    ) -> List[bool]:
        """Process multiple audio files concurrently using asyncio"""
        print(f"\n🎶 Processing batch of {len(audio_file_paths)} songs concurrently:")
        for path in audio_file_paths:
            print(f"   - {os.path.basename(path)}")

        results = []
        valid_files = []

        # First, validate all files exist in VDJ database
        for audio_file_path in audio_file_paths:
            if self._validate_file_in_database(audio_file_path):
                valid_files.append(audio_file_path)
                results.append(True)  # Placeholder, will be updated
            else:
                results.append(False)

        if not valid_files:
            print("❌ No valid files found in VDJ database")
            return results

        print(f"✅ {len(valid_files)} files validated in VDJ database")

        try:
            # Upload all files concurrently
            print(f"📤 Uploading {len(valid_files)} audio files concurrently...")
            upload_tasks = [
                self.upload_file_with_retry(file_path) for file_path in valid_files
            ]
            uploaded_results = await asyncio.gather(
                *upload_tasks, return_exceptions=True
            )

            # Filter successful uploads
            uploaded_files = []
            successful_uploads = 0
            for i, (file_path, result) in enumerate(zip(valid_files, uploaded_results)):
                if isinstance(result, Exception):
                    print(
                        f"❌ Failed to upload "
                        f"{os.path.basename(file_path)}: {result}"
                    )
                elif result is not None:
                    uploaded_files.append((file_path, result))
                    successful_uploads += 1
                else:
                    print(f"❌ Upload failed for {os.path.basename(file_path)}")

            if not uploaded_files:
                print("❌ No files uploaded successfully")
                return [False] * len(audio_file_paths)

            print(
                f"✅ Successfully uploaded "
                f"{successful_uploads}/{len(valid_files)} files"
            )

            if dry_run:
                # For dry run, analyze each song individually
                print(
                    f"🤖 Analyzing {len(uploaded_files)} songs with Gemini "
                    f"(concurrent individual calls)..."
                )

                # Create concurrent analysis tasks
                analysis_tasks = []
                for audio_file_path, uploaded_file in uploaded_files:
                    task = asyncio.get_running_loop().run_in_executor(
                        None,
                        self.analyze_audio_with_gemini,
                        audio_file_path,
                        uploaded_file,
                    )
                    analysis_tasks.append(task)

                # Run all analyses concurrently
                analysis_results = await asyncio.gather(
                    *analysis_tasks, return_exceptions=True
                )

                # Process each song's results (dry run)
                batch_success = []
                for i, (audio_file_path, _) in enumerate(uploaded_files):
                    if (
                        i < len(analysis_results)
                        and not isinstance(analysis_results[i], Exception)
                        and analysis_results[i]
                    ):
                        song_analysis = analysis_results[i]
                        success = self._apply_cues_to_database(
                            audio_file_path, song_analysis, dry_run=True
                        )
                        batch_success.append(success)
                    else:
                        if isinstance(analysis_results[i], Exception):
                            print(
                                f"❌ Analysis failed for "
                                f"{os.path.basename(audio_file_path)}: "
                                f"{analysis_results[i]}"
                            )
                        else:
                            print(
                                f"❌ No analysis result for "
                                f"{os.path.basename(audio_file_path)}"
                            )
                        batch_success.append(False)

                # Update results for valid files
                valid_idx = 0
                for i, success in enumerate(results):
                    if success:  # This was a valid file
                        if valid_idx < len(batch_success):
                            results[i] = batch_success[valid_idx]
                        else:
                            results[i] = False
                        valid_idx += 1

                return results

            # For actual processing, analyze each song individually
            print(
                f"🤖 Analyzing {len(uploaded_files)} songs with Gemini "
                f"(concurrent individual calls)..."
            )

            # Create concurrent analysis tasks
            analysis_tasks = []
            for audio_file_path, uploaded_file in uploaded_files:
                task = asyncio.get_running_loop().run_in_executor(
                    None,
                    self.analyze_audio_with_gemini,
                    audio_file_path,
                    uploaded_file,
                )
                analysis_tasks.append(task)

            # Run all analyses concurrently
            analysis_results = await asyncio.gather(
                *analysis_tasks, return_exceptions=True
            )

            # Filter successful analyses
            valid_analyses = []
            valid_file_paths = []
            for i, (audio_file_path, _) in enumerate(uploaded_files):
                if (
                    i < len(analysis_results)
                    and not isinstance(analysis_results[i], Exception)
                    and analysis_results[i]
                ):
                    valid_analyses.append(analysis_results[i])
                    valid_file_paths.append(audio_file_path)
                else:
                    if isinstance(analysis_results[i], Exception):
                        print(
                            f"❌ Analysis failed for "
                            f"{os.path.basename(audio_file_path)}: "
                            f"{analysis_results[i]}"
                        )
                    else:
                        print(
                            f"❌ No analysis result for "
                            f"{os.path.basename(audio_file_path)}"
                        )

            if not valid_analyses:
                print("❌ Failed to analyze any songs")
                return [False] * len(audio_file_paths)

            # Load the VDJ database once for the entire batch
            print("📂 Loading VDJ database for batch processing...")
            root = self.parse_vdj_database()
            if root is None:
                print("❌ Could not parse VDJ database for batch modification")
                return [False] * len(audio_file_paths)

            # Process each song's results and modify the XML tree
            batch_success = []
            songs_processed = 0

            # Process valid analyses
            for audio_file_path, song_analysis in zip(valid_file_paths, valid_analyses):
                success = self._apply_cues_to_batch_database(
                    root, audio_file_path, song_analysis
                )
                batch_success.append(success)
                if success:
                    songs_processed += 1

            # Add failures for songs that couldn't be analyzed
            failed_songs = len(uploaded_files) - len(valid_analyses)
            batch_success.extend([False] * failed_songs)

            # Save the database once after processing all songs
            if songs_processed > 0:
                try:
                    print(
                        f"💾 Saving database with changes for "
                        f"{songs_processed} songs..."
                    )
                    xml_str = ET.tostring(root, encoding="unicode")

                    # Ensure CRLF line endings for VDJ compatibility
                    if "\r\n" not in xml_str and "\n" in xml_str:
                        xml_str = xml_str.replace("\n", "\r\n")

                    # Validate XML is well-formed
                    try:
                        ET.fromstring(xml_str)
                    except ET.ParseError as e:
                        raise ValueError(f"Generated XML is malformed: {e}")

                    # Atomic write
                    temp_path = f"{self.vdj_database_path}.tmp"
                    with open(temp_path, "w", encoding="utf-8", newline="") as f:
                        f.write(xml_str)

                    # Verify before replacing
                    try:
                        ET.parse(temp_path)
                        shutil.move(temp_path, self.vdj_database_path)
                        print("✅ Batch database update completed successfully")
                    except ET.ParseError as e:
                        os.remove(temp_path)
                        raise ValueError(f"Generated XML file failed verification: {e}")

                except Exception as e:
                    print(f"❌ Error saving database after batch processing: {e}")
                    # Set all successes to False since database save failed
                    batch_success = [False] * len(batch_success)

            # Update results for valid files
            valid_idx = 0
            for i, success in enumerate(results):
                if success:  # This was a valid file
                    if valid_idx < len(batch_success):
                        results[i] = batch_success[valid_idx]
                    else:
                        results[i] = False
                    valid_idx += 1

            successful_count = sum(batch_success)
            print(
                f"🎯 Async batch complete: {successful_count}/"
                f"{len(uploaded_files)} songs processed successfully"
            )
            return results

        except Exception as e:
            print(f"❌ Error processing async batch: {e}")
            import traceback

            traceback.print_exc()
            return [False] * len(audio_file_paths)

    def process_audio_batch(
        self, audio_file_paths: List[str], dry_run: bool = False
    ) -> List[bool]:
        """Process multiple audio files in a single API call for efficiency"""
        print(f"\n🎶 Processing batch of {len(audio_file_paths)} songs:")
        for path in audio_file_paths:
            print(f"   - {os.path.basename(path)}")

        results = []
        valid_files = []

        # First, validate all files exist in VDJ database
        for audio_file_path in audio_file_paths:
            if self._validate_file_in_database(audio_file_path):
                valid_files.append(audio_file_path)
                results.append(True)  # Placeholder, will be updated
            else:
                results.append(False)

        if not valid_files:
            print("❌ No valid files found in VDJ database")
            return results

        print(f"✅ {len(valid_files)} files validated in VDJ database")

        if dry_run:
            # For dry run, just analyze and show what would be done
            try:
                print(f"📤 Uploading {len(valid_files)} audio files for dry run...")
                uploaded_files = []
                total_size = 0

                for audio_file_path in valid_files:
                    file_size = os.path.getsize(audio_file_path) / (1024 * 1024)  # MB
                    total_size += file_size
                    print(
                        f"📤 Uploading {os.path.basename(audio_file_path)} "
                        f"({file_size:.1f} MB)..."
                    )

                    uploaded_file = self._upload_audio_file_with_retry(audio_file_path)
                    uploaded_files.append((audio_file_path, uploaded_file))

                print(f"✅ Upload complete ({total_size:.1f} MB total)")

                # Analyze all files in one API call
                print(f"🤖 Analyzing batch of {len(valid_files)} songs with Gemini...")
                analysis_results = self._analyze_audio_batch(uploaded_files)

                if not analysis_results:
                    print("❌ Failed to analyze audio batch")
                    return [False] * len(audio_file_paths)

                # Process each song's results (dry run)
                batch_success = []
                for i, (audio_file_path, _) in enumerate(uploaded_files):
                    if i < len(analysis_results):
                        song_analysis = analysis_results[i]
                        success = self._apply_cues_to_database(
                            audio_file_path, song_analysis, dry_run=True
                        )
                        batch_success.append(success)
                    else:
                        batch_success.append(False)

                # Update results for valid files
                valid_idx = 0
                for i, success in enumerate(results):
                    if success:  # This was a valid file
                        results[i] = batch_success[valid_idx]
                        valid_idx += 1

                return results

            except Exception as e:
                print(f"❌ Error processing batch (dry run): {e}")
                return [False] * len(audio_file_paths)

        # For actual processing, we need to modify the database
        try:
            print(f"📤 Uploading {len(valid_files)} audio files...")
            uploaded_files = []
            total_size = 0

            for audio_file_path in valid_files:
                file_size = os.path.getsize(audio_file_path) / (1024 * 1024)  # MB
                total_size += file_size
                print(
                    f"📤 Uploading {os.path.basename(audio_file_path)} "
                    f"({file_size:.1f} MB)..."
                )

                uploaded_file = self._upload_audio_file_with_retry(audio_file_path)
                uploaded_files.append((audio_file_path, uploaded_file))

            print(f"✅ Upload complete ({total_size:.1f} MB total)")

            # Analyze all files in one API call
            print(f"🤖 Analyzing batch of {len(valid_files)} songs with Gemini...")
            analysis_results = self._analyze_audio_batch(uploaded_files)

            if not analysis_results:
                print("❌ Failed to analyze audio batch")
                return [False] * len(audio_file_paths)

            # Load the VDJ database once for the entire batch
            print("📂 Loading VDJ database for batch processing...")
            root = self.parse_vdj_database()
            if root is None:
                print("❌ Could not parse VDJ database for batch modification")
                return [False] * len(audio_file_paths)

            # Process each song's results and modify the XML tree
            batch_success = []
            songs_processed = 0

            for i, (audio_file_path, _) in enumerate(uploaded_files):
                if i < len(analysis_results):
                    song_analysis = analysis_results[i]
                    success = self._apply_cues_to_batch_database(
                        root, audio_file_path, song_analysis
                    )
                    batch_success.append(success)
                    if success:
                        songs_processed += 1
                else:
                    batch_success.append(False)

            # Save the database once after processing all songs
            if songs_processed > 0:
                try:
                    print(
                        f"💾 Saving database with changes for "
                        f"{songs_processed} songs..."
                    )
                    xml_str = ET.tostring(root, encoding="unicode")

                    # Ensure CRLF line endings for VDJ compatibility
                    if "\r\n" not in xml_str and "\n" in xml_str:
                        xml_str = xml_str.replace("\n", "\r\n")

                    # Validate XML is well-formed
                    try:
                        ET.fromstring(xml_str)
                    except ET.ParseError as e:
                        raise ValueError(f"Generated XML is malformed: {e}")

                    # Atomic write
                    temp_path = f"{self.vdj_database_path}.tmp"
                    with open(temp_path, "w", encoding="utf-8", newline="") as f:
                        f.write(xml_str)

                    # Verify before replacing
                    try:
                        ET.parse(temp_path)
                        shutil.move(temp_path, self.vdj_database_path)
                        print("✅ Batch database update completed successfully")
                    except ET.ParseError as e:
                        os.remove(temp_path)
                        raise ValueError(f"Generated XML file failed verification: {e}")

                except Exception as e:
                    print(f"❌ Error saving database after batch processing: {e}")
                    # Set all successes to False since database save failed
                    batch_success = [False] * len(batch_success)

            # Update results for valid files
            valid_idx = 0
            for i, success in enumerate(results):
                if success:  # This was a valid file
                    results[i] = batch_success[valid_idx]
                    valid_idx += 1

            successful_count = sum(batch_success)
            print(
                f"🎯 Batch complete: {successful_count}/"
                f"{len(valid_files)} songs processed successfully"
            )
            return results

        except Exception as e:
            print(f"❌ Error processing batch: {e}")
            import traceback

            traceback.print_exc()
            return [False] * len(audio_file_paths)

    def _validate_file_in_database(self, audio_file_path: str) -> bool:
        """Check if a single file exists in VDJ database"""
        try:

            root = self.parse_vdj_database()
            if root is None:
                return False

            import unicodedata

            normalized_target = unicodedata.normalize("NFC", audio_file_path)

            for song in root.findall("Song"):
                db_path = song.get("FilePath", "")
                normalized_db_path = unicodedata.normalize("NFC", db_path)

                if normalized_db_path == normalized_target:
                    return True

            print(
                f"❌ File not found in VDJ database: "
                f"{os.path.basename(audio_file_path)}"
            )
            return False

        except Exception as e:
            print(f"❌ Error validating file: {e}")
            import traceback

            traceback.print_exc()
            return False

    def _analyze_audio_batch(self, uploaded_files: List[tuple]) -> List[Dict]:
        """Analyze multiple audio files in one API call"""
        try:
            # Create batch prompt for structured output
            file_info = []
            for i, (file_path, _) in enumerate(uploaded_files):
                song_length = self.get_song_length(file_path) or 300
                bpm = self.get_song_bpm_from_database(file_path) or "Unknown"
                file_info.append(
                    f"File {i+1}: {os.path.basename(file_path)} - "
                    f"Length: {song_length:.1f}s - BPM: {bpm}"
                )

            prompt = f"""
            You are analyzing {len(uploaded_files)} DJ tracks for precise cue point
            placement. Listen to ALL audio files carefully.

            Files to analyze:
            {chr(10).join(file_info)}

            CRITICAL TIMING INSTRUCTIONS:
            1. Listen to the actual audio - do NOT make assumptions based on filename
            2. Pay attention to when elements ACTUALLY start/stop, not when you think
               they should
            3. For vocals, listen for actual singing voices, not just background sounds
            4. For drums, identify when the kick/snare pattern begins, not just
               percussion
            5. Be very conservative - only mark transitions where you clearly hear
               changes

            For EACH file, find 5-6 significant musical changes where elements
            ACTUALLY change:
            - Real intro (before main elements start)
            - When drums ACTUALLY enter (not just percussion)
            - When vocals ACTUALLY start singing (not just vocal sounds)
            - Breakdown sections (where elements drop out)
            - Drops/build-ups (energy changes)

            For EACH file, find 3 loop sections for DJing (16-32 beats long).
            IMPORTANT: Try to find ALL THREE types:
            1. DRUM LOOP: A section with ONLY drums/percussion, no melody, no vocals -
               perfect for DJ transitions
            2. VOCAL LOOP: A section with prominent vocals (with or without other
               elements) - great for crowd engagement
            3. MELODIC LOOP: A section with melody (synth/piano/guitar) but NO drums
               and NO vocals - for smooth transitions

            Element Detection:
            - drums: Kick/snare patterns, not just hi-hats
            - vocals: Actual singing/rapping, not just vocal effects
            - bass: Prominent bassline
            - synth/piano: Melodic elements
            - Include every clearly audible element. If bass, synth, vocals, pads,
              or effects are audible during a drum section, it is NOT drums-only.

            Strict Label Rules:
            - Only use "Melodic" or "Melody" in a name when there is a clear
              foreground melody and NO audible drums or vocals.
            - Bass alone, pads, texture, atmosphere, or filtered chord wash are NOT
              enough to call a section melodic. Name those by the actual element
              instead, like "Bass Break" or "Synth Break".
            - Only use "Drum", "Drums", or "Percussion" in a name when drums are
              isolated and no bass, synth, melody, vocal, pad, or tonal element is
              audible.
            - If a section has drums plus other elements, use neutral names like
              "Rhythm Section", "Groove", "Build", "Drop", or "Outro".
            - If you are uncertain whether other elements are present, include those
              elements and avoid "drums-only" or "melodic-only" names/colors.

            Color Rules (be strict):
            - blue: Only melody, NO drums, NO vocals
            - green: Melody + drums, NO vocals
            - yellow: Full mix (drums + melody + vocals)
            - purple: Only drums/percussion
            - orange: Melody + vocals, NO drums

            RESPONSE FORMAT REQUIREMENTS:
            - All timestamps must be rounded to 2 decimal places (e.g., 45.67)
            - Each cue must have: timestamp, elements (array), cue_name (string),
              color (string)
            - Each loop must have: start, length_beats, elements (array),
              loop_name (string), color (string)
            - Use descriptive names like "Intro", "Drums In", "Vocal Drop",
              "Build Up", "Breakdown"
            - NEVER use extremely long decimal numbers

Analyze each file independently and return complete analysis for all
{len(uploaded_files)} files.
"""

            # Parse structured JSON response
            try:
                batch_data = self._generate_json_content(
                    contents=[prompt] + [uploaded_file for _, uploaded_file in uploaded_files],
                    schema=BatchMusicAnalysis,
                    timeout_seconds=300,
                )

                # Extract analyses from the structured response
                if "analyses" in batch_data:
                    analyses_list = batch_data["analyses"]
                else:
                    # Fallback if the response structure is different
                    analyses_list = batch_data if isinstance(batch_data, list) else []

                analyses_list = [
                    self._normalize_analysis_data(analysis_data)
                    for analysis_data in analyses_list
                ]

                print(
                    f"✅ Successfully analyzed {len(analyses_list)} " f"songs in batch"
                )
                return analyses_list

            except json.JSONDecodeError as e:
                print(f"❌ Failed to parse batch JSON response: {e}")
                return []

            except Exception as e:
                print(f"❌ Error in batch analysis: {e}")
                import traceback

                traceback.print_exc()
                return []

        except Exception as e:
            print(f"❌ Error in _analyze_audio_batch: {e}")
            import traceback

            traceback.print_exc()
            return []

    def _apply_cues_to_database(
        self, audio_file_path: str, analysis_data: Dict, dry_run: bool = False
    ) -> bool:
        """Apply analysis results to VDJ database for a single song"""
        try:
            print(f"\n🎶 Applying cues: {os.path.basename(audio_file_path)}")

            if dry_run:
                print("🔍 DRY RUN - Would create:")
                # Show what would be created
                cues = analysis_data.get("measure_changes", [])
                loops = analysis_data.get("loop_segments", [])
                working_bpm = self.get_song_bpm_from_database(audio_file_path) or 120
                alignment = self._verify_beatgrid_alignment(
                    audio_file_path, working_bpm
                )
                if alignment.corrected:
                    print(
                        f"  Would update beatgrid '1': "
                        f"{self.get_beatgrid_offset(audio_file_path):.6f}s → "
                        f"{alignment.offset:.6f}s"
                    )

                for i, cue_data in enumerate(cues[:6], 1):
                    cue_name = cue_data.get("cue_name", f"cue{i}")
                    timestamp = cue_data.get("timestamp", 0)
                    aligned_time = self.validate_timing_hybrid(
                        timestamp, working_bpm, audio_file_path
                    )
                    color = cue_data.get("color", "green")
                    elements = cue_data.get("elements", [])
                    print(
                        f"  Cue {i}: '{cue_name}' at {aligned_time:.1f}s | "
                        f"Color: {color.capitalize()} | Elements: {elements}"
                    )

                for i, loop_data in enumerate(loops[:3], 1):
                    loop_name = loop_data.get("loop_name", f"loop{i}l")
                    start = loop_data.get("start", 0)
                    aligned_start = self.validate_timing_hybrid(
                        start, working_bpm, audio_file_path
                    )
                    beats = loop_data.get("length_beats", 16)
                    color = loop_data.get("color", "green")
                    elements = loop_data.get("elements", [])
                    print(
                        f"  Loop {i}: '{loop_name}' at {aligned_start:.1f}s "
                        f"({beats} beats) | Color: {color.capitalize()} | "
                        f"Elements: {elements}"
                    )

                return True

            # Load and modify VDJ database
            root = self.parse_vdj_database()
            if root is None:
                print("❌ Could not parse VDJ database for modification")
                return False

            # Find the song in database
            song_element = None
            import unicodedata

            normalized_target = unicodedata.normalize("NFC", audio_file_path)

            for song in root.findall("Song"):
                db_path = song.get("FilePath", "")
                normalized_db_path = unicodedata.normalize("NFC", db_path)

                if normalized_db_path == normalized_target:
                    song_element = song
                    break

            if song_element is None:
                print(f"❌ Song not found in VDJ database: {audio_file_path}")
                return False

            # Remove existing manual cues and loops
            pois_to_remove = []
            for poi in song_element.findall("Poi"):
                if poi.get("Type") in ["cue", "loop"] and poi.get("Num", "0") != "0":
                    pois_to_remove.append(poi)

            for poi in pois_to_remove:
                song_element.remove(poi)

            # Get song info for validation
            song_length = self.get_song_length(audio_file_path)
            database_bpm = self.get_song_bpm_from_database(audio_file_path)
            working_bpm = database_bpm or 120
            self._apply_verified_beatgrid_to_song(
                song_element, audio_file_path, working_bpm
            )

            # Process cues
            all_pois = []
            cue_count = 0

            for cue_data in analysis_data.get("measure_changes", [])[:6]:
                # Validate timestamp
                gemini_time = cue_data.get("timestamp", 0)
                aligned_time = self.validate_timing_hybrid(
                    gemini_time, working_bpm, audio_file_path
                )

                # Skip cues beyond song length
                if song_length and aligned_time >= song_length:
                    continue

                cue_count += 1
                elements = cue_data.get("elements", [])
                if not elements:
                    continue

                # Validate color assignment
                gemini_color = cue_data.get("color", "green")
                validated_color = self.validate_color_assignment(elements, gemini_color)
                color = self.color_mappings.get(
                    validated_color, self.color_mappings["green"]
                )

                # Get cue name
                cue_name = cue_data.get("cue_name") or self.create_cue_name(
                    elements, cue_count
                )
                cue_name = self.sanitize_xml_content(cue_name)

                # Create cue POI
                cue_poi = ET.Element("Poi")
                cue_poi.set("Name", cue_name)
                cue_poi.set("Pos", f"{aligned_time:.6f}")
                cue_poi.set("Num", str(cue_count))
                cue_poi.set("Color", color)
                cue_poi.set("Type", "cue")

                all_pois.append((aligned_time, cue_poi))

            # Process loops
            loop_count = 0
            used_loop_types = set()

            loops = analysis_data.get("loop_segments", [])

            # Sort loops by priority (drum-only first, then vocal, then melodic)
            def loop_priority(loop_data):
                elements = loop_data.get("elements", [])
                has_drums = any(elem in elements for elem in ["drums", "percussion"])
                has_vocals = "vocals" in elements
                has_melody = any(
                    elem in elements for elem in ["piano", "synth", "strings", "guitar"]
                )

                if has_drums and not has_vocals and len(elements) <= 2:
                    return 0  # Drum-only (highest priority)
                elif has_vocals:
                    return 1  # Vocal sections
                elif has_melody and not has_drums and not has_vocals:
                    return 2  # Melodic-only
                else:
                    return 3  # Other

            loops.sort(key=loop_priority)

            for loop_data in loops[:3]:  # Max 3 loops
                # Validate timestamp
                gemini_time = loop_data.get("start", 0)
                aligned_time = self.validate_timing_hybrid(
                    gemini_time, working_bpm, audio_file_path
                )

                # Skip loops too close to song end
                if song_length and aligned_time >= (song_length - 10):
                    continue

                elements = loop_data.get("elements", [])
                if not elements:
                    continue

                # Get loop name
                loop_name = loop_data.get("loop_name") or self.create_loop_name(
                    elements
                )
                if not loop_name.endswith("l"):
                    loop_name = f"{loop_name}l"
                loop_name = self.sanitize_xml_content(loop_name)

                # Skip duplicate loop types
                if loop_name in used_loop_types:
                    continue

                loop_count += 1
                used_loop_types.add(loop_name)

                # Validate color assignment
                gemini_color = loop_data.get("color", "green")
                validated_color = self.validate_color_assignment(elements, gemini_color)
                color = self.color_mappings.get(
                    validated_color, self.color_mappings["green"]
                )

                # Create loop POI
                loop_poi = ET.Element("Poi")
                loop_poi.set("Name", loop_name)
                loop_poi.set("Pos", f"{aligned_time:.6f}")
                loop_poi.set("Num", "-1")
                loop_poi.set("Color", color)
                loop_poi.set("Type", "loop")
                loop_poi.set("Size", str(float(loop_data.get("length_beats", 16))))
                loop_poi.set("Slot", str(loop_count))

                all_pois.append((aligned_time, loop_poi))

            # Sort all POIs by timestamp and add to song
            all_pois.sort(key=lambda x: x[0])

            # Reassign loop slots in chronological order
            loop_slot_counter = 1
            for _, poi_element in all_pois:
                if poi_element.get("Type") == "loop":
                    poi_element.set("Slot", str(loop_slot_counter))
                    loop_slot_counter += 1
                song_element.append(poi_element)

            # Add comment with colors
            existing_comment = song_element.find("Comment")
            if existing_comment is not None:
                song_element.remove(existing_comment)

            # Generate comment from used colors
            used_colors = set()
            for _, poi_element in all_pois:
                color_value = poi_element.get("Color")
                for color_name, value in self.color_mappings.items():
                    if value == color_value:
                        used_colors.add(color_name)
                        break

            full_comment = " ".join(sorted(used_colors))
            full_comment = self.sanitize_xml_content(full_comment)
            comment_element = ET.Element("Comment")
            comment_element.text = full_comment
            song_element.append(comment_element)

            print(
                f"✅ Applied {cue_count} cues and {loop_count} loops to "
                f"{os.path.basename(audio_file_path)}"
            )
            return True

        except Exception as e:
            print(f"❌ Error applying cues to {audio_file_path}: {e}")
            import traceback

            traceback.print_exc()
            return False

    def _apply_cues_to_batch_database(
        self, root, audio_file_path: str, analysis_data: Dict
    ) -> bool:
        """Apply analysis results to XML tree for batch processing"""
        try:
            print(f"🎶 Applying cues: {os.path.basename(audio_file_path)}")

            # Find the song in database
            song_element = None
            import unicodedata

            normalized_target = unicodedata.normalize("NFC", audio_file_path)

            for song in root.findall("Song"):
                db_path = song.get("FilePath", "")
                normalized_db_path = unicodedata.normalize("NFC", db_path)

                if normalized_db_path == normalized_target:
                    song_element = song
                    break

            if song_element is None:
                print(f"❌ Song not found in VDJ database: {audio_file_path}")
                return False

            # Remove existing manual cues and loops
            pois_to_remove = []
            for poi in song_element.findall("Poi"):
                if poi.get("Type") in ["cue", "loop"] and poi.get("Num", "0") != "0":
                    pois_to_remove.append(poi)

            for poi in pois_to_remove:
                song_element.remove(poi)

            # Get song info for validation
            song_length = self.get_song_length(audio_file_path)
            database_bpm = self.get_song_bpm_from_database(audio_file_path)
            working_bpm = database_bpm or 120
            self._apply_verified_beatgrid_to_song(
                song_element, audio_file_path, working_bpm
            )

            # Process cues
            all_pois = []
            cue_count = 0

            for cue_data in analysis_data.get("measure_changes", [])[:6]:
                # Validate timestamp
                gemini_time = cue_data.get("timestamp", 0)
                aligned_time = self.validate_timing_hybrid(
                    gemini_time, working_bpm, audio_file_path
                )

                # Skip cues beyond song length
                if song_length and aligned_time >= song_length:
                    continue

                cue_count += 1
                elements = cue_data.get("elements", [])
                if not elements:
                    continue

                # Validate color assignment
                gemini_color = cue_data.get("color", "green")
                validated_color = self.validate_color_assignment(elements, gemini_color)
                color = self.color_mappings.get(
                    validated_color, self.color_mappings["green"]
                )

                # Get cue name
                cue_name = cue_data.get("cue_name") or self.create_cue_name(
                    elements, cue_count
                )
                cue_name = self.sanitize_xml_content(cue_name)

                # Create cue POI
                cue_poi = ET.Element("Poi")
                cue_poi.set("Name", cue_name)
                cue_poi.set("Pos", f"{aligned_time:.6f}")
                cue_poi.set("Num", str(cue_count))
                cue_poi.set("Color", color)
                cue_poi.set("Type", "cue")

                all_pois.append((aligned_time, cue_poi))

            # Process loops
            loop_count = 0
            used_loop_types = set()

            loops = analysis_data.get("loop_segments", [])

            # Sort loops by priority (drum-only first, then vocal, then melodic)
            def loop_priority(loop_data):
                elements = loop_data.get("elements", [])
                has_drums = any(elem in elements for elem in ["drums", "percussion"])
                has_vocals = "vocals" in elements
                has_melody = any(
                    elem in elements for elem in ["piano", "synth", "strings", "guitar"]
                )

                if has_drums and not has_vocals and len(elements) <= 2:
                    return 0  # Drum-only (highest priority)
                elif has_vocals:
                    return 1  # Vocal sections
                elif has_melody and not has_drums and not has_vocals:
                    return 2  # Melodic-only
                else:
                    return 3  # Other

            loops.sort(key=loop_priority)

            for loop_data in loops[:3]:  # Max 3 loops
                # Validate timestamp
                gemini_time = loop_data.get("start", 0)
                aligned_time = self.validate_timing_hybrid(
                    gemini_time, working_bpm, audio_file_path
                )

                # Skip loops too close to song end
                if song_length and aligned_time >= (song_length - 10):
                    continue

                elements = loop_data.get("elements", [])
                if not elements:
                    continue

                # Get loop name
                loop_name = loop_data.get("loop_name") or self.create_loop_name(
                    elements
                )
                if not loop_name.endswith("l"):
                    loop_name = f"{loop_name}l"
                loop_name = self.sanitize_xml_content(loop_name)

                # Skip duplicate loop types
                if loop_name in used_loop_types:
                    continue

                loop_count += 1
                used_loop_types.add(loop_name)

                # Validate color assignment
                gemini_color = loop_data.get("color", "green")
                validated_color = self.validate_color_assignment(elements, gemini_color)
                color = self.color_mappings.get(
                    validated_color, self.color_mappings["green"]
                )

                # Create loop POI
                loop_poi = ET.Element("Poi")
                loop_poi.set("Name", loop_name)
                loop_poi.set("Pos", f"{aligned_time:.6f}")
                loop_poi.set("Num", "-1")
                loop_poi.set("Color", color)
                loop_poi.set("Type", "loop")
                loop_poi.set("Size", str(float(loop_data.get("length_beats", 16))))
                loop_poi.set("Slot", str(loop_count))

                all_pois.append((aligned_time, loop_poi))

            # Sort all POIs by timestamp and add to song
            all_pois.sort(key=lambda x: x[0])

            # Reassign loop slots in chronological order
            loop_slot_counter = 1
            for _, poi_element in all_pois:
                if poi_element.get("Type") == "loop":
                    poi_element.set("Slot", str(loop_slot_counter))
                    loop_slot_counter += 1
                song_element.append(poi_element)

            # Add comment with colors
            existing_comment = song_element.find("Comment")
            if existing_comment is not None:
                song_element.remove(existing_comment)

            # Generate comment from used colors
            used_colors = set()
            for _, poi_element in all_pois:
                color_value = poi_element.get("Color")
                for color_name, value in self.color_mappings.items():
                    if value == color_value:
                        used_colors.add(color_name)
                        break

            full_comment = " ".join(sorted(used_colors))
            full_comment = self.sanitize_xml_content(full_comment)
            comment_element = ET.Element("Comment")
            comment_element.text = full_comment
            song_element.append(comment_element)

            print(
                f"✅ Applied {cue_count} cues and {loop_count} loops to "
                f"{os.path.basename(audio_file_path)} (in memory)"
            )
            return True

        except Exception as e:
            print(f"❌ Error applying cues to {audio_file_path}: {e}")
            import traceback

            traceback.print_exc()
            return False

    def process_audio_file(self, audio_file_path: str, dry_run: bool = False) -> bool:
        """Process a single audio file and add cues/loops to VDJ database"""
        print(f"\n🎶 Processing: {os.path.basename(audio_file_path)}")

        # First check if song exists in VDJ database (fail fast)
        try:
            print(f"🔍 Checking VDJ database for: {audio_file_path}")
            root = self.parse_vdj_database()
            if root is None:
                print("❌ Could not parse VDJ database")
                return False

            song_found = False
            songs_checked = 0

            # Normalize the target path for comparison (handle Unicode issues)
            import unicodedata

            normalized_target = unicodedata.normalize("NFC", audio_file_path)

            for song in root.findall("Song"):
                songs_checked += 1
                db_path = song.get("FilePath", "")
                normalized_db_path = unicodedata.normalize("NFC", db_path)

                if normalized_db_path == normalized_target:
                    song_found = True
                    print(
                        f"✅ Song found in database after checking "
                        f"{songs_checked} songs"
                    )
                    break

            if not song_found:
                print(
                    f"❌ Song not found in VDJ database after checking "
                    f"{songs_checked} songs"
                )
                print("💡 Make sure the song has been analyzed in VirtualDJ first")
                return False

        except ET.ParseError as e:
            print(f"⚠️  VDJ database XML parsing issue: {e}")
            # Continue anyway - the later database update might handle it
        except Exception as e:
            print(f"⚠️  Could not check VDJ database: {e}")
            # Continue anyway

        # Get Gemini analysis
        analysis = self.analyze_audio_with_gemini(audio_file_path)
        if not analysis:
            print(f"❌ Skipping {audio_file_path} - analysis failed")
            return False

        # Get song length for validation
        song_length = self.get_song_length(audio_file_path)

        # Get BPM from database for validation
        database_bpm = self.get_song_bpm_from_database(audio_file_path)
        analysis_bpm = analysis.get("song_structure", {}).get(
            "bpm", database_bpm or 120
        )
        working_bpm = database_bpm or analysis_bpm

        # Convert VDJ BPM fraction to actual BPM for display
        display_bpm = working_bpm
        if working_bpm and working_bpm < 5:  # If it looks like a VDJ fraction
            display_bpm = 60.0 / working_bpm

        print(
            f"📊 BPM: {display_bpm:.1f} | "
            f"Cues: {len(analysis.get('measure_changes', []))} | "
            f"Loops: {len(analysis.get('loop_segments', []))}"
        )

        if dry_run:
            print("🔍 DRY RUN - Would create:")
            for i, cue_data in enumerate(analysis.get("measure_changes", [])[:6], 1):
                # Use hybrid approach: prefer Gemini's timestamp if it's close
                # to a "1" beat
                gemini_time = cue_data.get("timestamp", 0)
                aligned_time = self.validate_timing_hybrid(
                    gemini_time, working_bpm, audio_file_path
                )
                # Use Gemini's suggested cue name if available, otherwise fallback
                cue_name = cue_data.get("cue_name") or self.create_cue_name(
                    cue_data.get("elements", []), cue_data.get("measure", i)
                )
                # Use Gemini's color assignment
                gemini_color = cue_data.get("color", "green")
                color_name = gemini_color.capitalize()

                print(
                    f"  Cue {i}: '{cue_name}' at {aligned_time:.1f}s | "
                    f"Color: {color_name} | "
                    f"Elements: {cue_data.get('elements', [])}"
                )

            # Show loops with same logic as actual processing
            loops = analysis.get("loop_segments", [])

            def loop_priority(loop_data):
                elements = loop_data.get("elements", [])
                element_count = len(elements)
                has_drums = any(elem in elements for elem in ["drums", "percussion"])
                has_vocals = "vocals" in elements
                has_melody = any(
                    elem in elements for elem in ["piano", "synth", "strings", "guitar"]
                )

                # Priority 1: Drum-only sections (purple loops)
                if has_drums and not has_vocals and element_count <= 2:
                    return 0
                # Priority 2: Vocal sections (great for mixing)
                elif has_vocals:
                    return 1
                # Priority 3: Melodic sections without drums (blue loops)
                elif has_melody and not has_drums and not has_vocals:
                    return 2
                # Priority 4: Other minimal sections (good for transitions)
                elif element_count <= 2:
                    return 3
                # Lower priority: fuller arrangements
                else:
                    return 4

            loops.sort(key=loop_priority)

            # Collect loops that would be selected
            selected_loops = []
            loop_count = 0
            used_loop_types = set()
            for loop_data in loops:
                if loop_count >= 3:
                    break

                # Use Gemini's suggested loop name if available, otherwise fallback
                loop_name = loop_data.get("loop_name") or self.create_loop_name(
                    loop_data.get("elements", [])
                )

                # Ensure loop name ends with 'l' suffix
                if not loop_name.endswith("l"):
                    loop_name = f"{loop_name}l"

                if loop_name in used_loop_types:
                    continue

                loop_count += 1
                used_loop_types.add(loop_name)

                # Use hybrid approach: prefer Gemini's timestamp if it's close
                # to a "1" beat
                gemini_time = loop_data.get("start", 0)
                aligned_time = self.validate_timing_hybrid(
                    gemini_time, working_bpm, audio_file_path
                )
                # Use Gemini's color assignment
                gemini_color = loop_data.get("color", "green")
                color_name = gemini_color.capitalize()

                selected_loops.append(
                    {
                        "name": loop_name,
                        "time": aligned_time,
                        "beats": loop_data.get("length_beats", 16),
                        "color": color_name,
                        "elements": loop_data.get("elements", []),
                    }
                )

            # Sort selected loops by timestamp and display in chronological order
            selected_loops.sort(key=lambda x: x["time"])
            for i, loop_info in enumerate(selected_loops, 1):
                print(
                    f"  Loop {i}: '{loop_info['name']}' at "
                    f"{loop_info['time']:.1f}s ({loop_info['beats']} beats) | "
                    f"Color: {loop_info['color']} | "
                    f"Elements: {loop_info['elements']}"
                )

            # Show the comment that would be generated from actually used colors
            used_colors = set()
            for cue_data in analysis.get("measure_changes", [])[:6]:
                gemini_color = cue_data.get("color", "green")
                used_colors.add(gemini_color)

            # Add colors from selected loops
            for loop_info in selected_loops:
                # Need to map back to original loop data to get color
                for loop_data in loops:
                    loop_name = loop_data.get("loop_name") or self.create_loop_name(
                        loop_data.get("elements", [])
                    )
                    if not loop_name.endswith("l"):
                        loop_name = f"{loop_name}l"
                    if loop_name == loop_info["name"]:
                        gemini_color = loop_data.get("color", "green")
                        used_colors.add(gemini_color)
                        break

            full_comment = " ".join(sorted(used_colors))
            print(f"\n  Comment: '{full_comment}'")
            return True

        # Load and modify VDJ database
        try:
            root = self.parse_vdj_database()
            if root is None:
                print("❌ Could not parse VDJ database for modification")
                return False

            # Find the song in database (with Unicode normalization)
            song_element = None
            normalized_target = unicodedata.normalize("NFC", audio_file_path)

            for song in root.findall("Song"):
                db_path = song.get("FilePath", "")
                normalized_db_path = unicodedata.normalize("NFC", db_path)

                if normalized_db_path == normalized_target:
                    song_element = song
                    break

            if song_element is None:
                print(f"❌ Song not found in VDJ database: {audio_file_path}")
                return False

            # Remove existing manual cues and loops (safe removal)
            pois_to_remove = []
            for poi in song_element.findall("Poi"):
                if poi.get("Type") in ["cue", "loop"] and poi.get("Num", "0") != "0":
                    pois_to_remove.append(poi)

            for poi in pois_to_remove:
                song_element.remove(poi)

            print(f"🧹 Removed {len(pois_to_remove)} existing cues/loops")
            self._apply_verified_beatgrid_to_song(
                song_element, audio_file_path, working_bpm
            )

            # Prepare all cues and loops with timing alignment
            all_pois = []

            # Process cues
            cue_count = 0
            for cue_data in analysis.get("measure_changes", [])[:6]:  # Max 6 cues
                # Use hybrid approach: prefer Gemini's timestamp if it's close
                # to a "1" beat
                gemini_time = cue_data.get("timestamp", 0)
                aligned_time = self.validate_timing_hybrid(
                    gemini_time, working_bpm, audio_file_path
                )

                # Skip cues that are beyond song length
                if song_length and aligned_time >= song_length:
                    print(
                        f"⚠️  Skipping cue at {aligned_time:.1f}s - beyond "
                        f"song length ({song_length:.1f}s)"
                    )
                    continue

                cue_count += 1
                # Validate and correct color assignment
                gemini_color = cue_data.get("color", "green")
                elements = cue_data.get("elements", [])  # Handle missing elements
                if not elements:
                    print(
                        "⚠️  Warning: Cue has no elements detected, "
                        f"skipping: {cue_data}"
                    )
                    continue

                validated_color = self.validate_color_assignment(elements, gemini_color)
                if validated_color != gemini_color:
                    reason = ""
                    if gemini_color == "purple" and validated_color == "blue":
                        reason = " (melodic elements prominent)"
                    print(
                        f"  🎨 Color corrected: {gemini_color} → "
                        f"{validated_color} for "
                        f"{cue_data.get('cue_name', 'cue')}{reason}"
                    )
                color = self.color_mappings.get(
                    validated_color, self.color_mappings["green"]
                )
                # Use Gemini's suggested cue name if available, otherwise fallback
                cue_name = cue_data.get("cue_name") or self.create_cue_name(
                    cue_data.get("elements", []),
                    cue_data.get("measure", cue_count),
                )

                # Sanitize cue name for XML safety
                cue_name = self.sanitize_xml_content(cue_name)

                cue_poi = ET.Element("Poi")
                cue_poi.set("Name", cue_name)
                cue_poi.set("Pos", f"{aligned_time:.6f}")
                cue_poi.set("Num", str(cue_count))
                cue_poi.set("Color", color)
                cue_poi.set("Type", "cue")

                all_pois.append((aligned_time, cue_poi))

            # Process loops (prioritize different types, ensure at least one drum loop)
            loop_count = 0
            used_loop_types = set()

            # Sort loops to prioritize breakdown/minimal sections and drum-only
            loops = analysis.get("loop_segments", [])

            def loop_priority(loop_data):
                elements = loop_data.get("elements", [])
                element_count = len(elements)
                has_drums = any(elem in elements for elem in ["drums", "percussion"])
                has_vocals = "vocals" in elements
                has_melody = any(
                    elem in elements for elem in ["piano", "synth", "strings", "guitar"]
                )

                # Priority 1: Drum-only sections (purple loops)
                if has_drums and not has_vocals and element_count <= 2:
                    return 0
                # Priority 2: Vocal sections (great for mixing)
                elif has_vocals:
                    return 1
                # Priority 3: Melodic sections without drums (blue loops)
                elif has_melody and not has_drums and not has_vocals:
                    return 2
                # Priority 4: Other minimal sections (good for transitions)
                elif element_count <= 2:
                    return 3
                # Lower priority: fuller arrangements
                else:
                    return 4

            loops.sort(key=loop_priority)

            for loop_data in loops:
                if loop_count >= 3:  # Max 3 loops
                    break

                # Use Gemini's suggested loop name if available, otherwise fallback
                loop_name = loop_data.get("loop_name") or self.create_loop_name(
                    loop_data.get("elements", [])
                )

                # Ensure loop name ends with 'l' suffix
                if not loop_name.endswith("l"):
                    loop_name = f"{loop_name}l"

                # Sanitize loop name for XML safety
                loop_name = self.sanitize_xml_content(loop_name)

                # Skip if we already have this type of loop
                if loop_name in used_loop_types:
                    continue

                loop_count += 1
                used_loop_types.add(loop_name)

                # Use hybrid approach: prefer Gemini's timestamp if it's close
                # to a "1" beat
                gemini_time = loop_data.get("start", 0)
                aligned_time = self.validate_timing_hybrid(
                    gemini_time, working_bpm, audio_file_path
                )

                # Skip loops that are beyond song length (leave some buffer)
                if song_length and aligned_time >= (song_length - 10):
                    print(
                        f"⚠️  Skipping loop at {aligned_time:.1f}s - too "
                        f"close to song end ({song_length:.1f}s)"
                    )
                    continue

                # Validate and correct color assignment
                gemini_color = loop_data.get("color", "green")
                elements = loop_data.get("elements", [])  # Handle missing elements
                if not elements:
                    print(
                        "⚠️  Warning: Loop has no elements detected, "
                        f"skipping: {loop_data}"
                    )
                    continue

                validated_color = self.validate_color_assignment(elements, gemini_color)
                if validated_color != gemini_color:
                    print(
                        f"  🎨 Color corrected: {gemini_color} → "
                        f"{validated_color} for {loop_name}"
                    )
                color = self.color_mappings.get(
                    validated_color, self.color_mappings["green"]
                )

                loop_poi = ET.Element("Poi")
                loop_poi.set("Name", loop_name)
                loop_poi.set("Pos", f"{aligned_time:.6f}")
                loop_poi.set("Num", "-1")
                loop_poi.set("Color", color)
                loop_poi.set("Type", "loop")
                loop_poi.set("Size", str(float(loop_data.get("length_beats", 16))))
                # Store loop_count for now, will reassign slots after sorting
                loop_poi.set("Slot", str(loop_count))

                all_pois.append((aligned_time, loop_poi))

            # Sort all POIs by timestamp and add to song element
            all_pois.sort(key=lambda x: x[0])

            # Reassign loop slots in chronological order
            loop_slot_counter = 1
            for _, poi_element in all_pois:
                if poi_element.get("Type") == "loop":
                    poi_element.set("Slot", str(loop_slot_counter))
                    loop_slot_counter += 1
                song_element.append(poi_element)

            # Add/update comment with colors
            existing_comment = song_element.find("Comment")
            if existing_comment is not None:
                song_element.remove(existing_comment)

            # Generate comment from actually used colors only
            used_colors = set()

            # Get colors from all POIs that were actually added
            for _, poi_element in all_pois:
                # Extract color from the POI element
                color_value = poi_element.get("Color")
                # Map color value back to color name
                for color_name, value in self.color_mappings.items():
                    if value == color_value:
                        used_colors.add(color_name)
                        break

            full_comment = " ".join(sorted(used_colors))
            full_comment = self.sanitize_xml_content(full_comment)
            comment_element = ET.Element("Comment")
            comment_element.text = full_comment
            song_element.append(comment_element)

            # Save database using safe method
            # (VDJ expects no XML declaration and CRLF line endings)
            try:
                xml_str = ET.tostring(root, encoding="unicode")

                # Ensure CRLF line endings for VDJ compatibility
                if "\r\n" not in xml_str and "\n" in xml_str:
                    xml_str = xml_str.replace("\n", "\r\n")

                # Validate XML is well-formed before writing
                try:
                    ET.fromstring(xml_str)
                except ET.ParseError as e:
                    raise ValueError(f"Generated XML is malformed: {e}")

                # Write to database with proper encoding (atomic write)
                temp_path = f"{self.vdj_database_path}.tmp"
                with open(temp_path, "w", encoding="utf-8", newline="") as f:
                    f.write(xml_str)

                # Verify before replacing
                try:
                    ET.parse(temp_path)
                    # If parsing succeeds, replace the original file
                    shutil.move(temp_path, self.vdj_database_path)
                    print("✅ Database written and verified successfully")
                except ET.ParseError as e:
                    # If parsing fails, remove temp file and raise error
                    os.remove(temp_path)
                    raise ValueError(f"Generated XML file failed verification: {e}")

            except Exception as e:
                print(f"❌ Error saving database: {e}")
                print("💾 Database backup is available if needed")
                raise

            # Show color summary
            print("\n🎨 Color Summary:")
            color_summary = []
            cue_num = 1
            loop_num = 1

            for _, poi_element in sorted(all_pois, key=lambda x: x[0]):
                poi_type = poi_element.get("Type")
                poi_name = poi_element.get("Name", "unnamed")
                color_value = poi_element.get("Color")

                # Map color value back to name
                color_name = "unknown"
                for name, value in self.color_mappings.items():
                    if value == color_value:
                        color_name = name
                        break

                if poi_type == "cue":
                    color_summary.append(f"  Cue {cue_num}: {poi_name} - {color_name}")
                    cue_num += 1
                elif poi_type == "loop":
                    color_summary.append(
                        f"  Loop {loop_num}: {poi_name} - {color_name}"
                    )
                    loop_num += 1

            for line in color_summary:
                print(line)

            print(
                f"\n✅ Added {cue_count} cues and {loop_count} loops to "
                f"{os.path.basename(audio_file_path)}"
            )
            print("💡 Tip: Press Cmd+Option+R in VirtualDJ to refresh the database")
            return True

        except Exception as e:
            import traceback

            print(f"❌ Error updating VDJ database: {e}")
            print("🔍 Full traceback:")
            traceback.print_exc()
            return False


def expand_audio_files(paths):
    """Expand directories and file patterns into audio files"""
    import glob

    audio_extensions = {
        ".mp3",
        ".flac",
        ".wav",
        ".m4a",
        ".aac",
        ".ogg",
        ".opus",
        ".mpeg",
    }
    audio_files = []

    for path in paths:
        if os.path.isfile(path):
            # Single file
            if any(path.lower().endswith(ext) for ext in audio_extensions):
                audio_files.append(path)
            else:
                print(f"⚠️  Skipping non-audio file: {path}")
        elif os.path.isdir(path):
            # Directory - find all audio files recursively
            print(f"📁 Scanning directory: {path}")
            found_files = []
            for root, _, files in os.walk(path):
                for file in files:
                    if any(file.lower().endswith(ext) for ext in audio_extensions):
                        full_path = os.path.join(root, file)
                        found_files.append(full_path)

            found_files.sort()  # Sort for consistent processing order
            audio_files.extend(found_files)
            print(f"📁 Found {len(found_files)} audio files in {path}")
        else:
            # Try glob pattern
            matches = glob.glob(path)
            if matches:
                for match in matches:
                    if os.path.isfile(match) and any(
                        match.lower().endswith(ext) for ext in audio_extensions
                    ):
                        audio_files.append(match)
            else:
                print(f"❌ Path not found: {path}")

    return audio_files


def main():
    """Main function to run the music cuer."""
    parser = argparse.ArgumentParser(
        description="Automatic Music Cueing for VirtualDJ (Gemini)"
    )
    parser.add_argument(
        "paths", nargs="+", help="Audio files or directories to process"
    )
    parser.add_argument("--api-key", help="Gemini API key (optional if in .env file)")
    parser.add_argument(
        "--model",
        default=None,
        help=(
            f"Gemini model to use (default: GEMINI_MODEL or {DEFAULT_GEMINI_MODEL})"
        ),
    )
    parser.add_argument("--database", help="Path to VDJ database.xml")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without modifying database",
    )
    parser.add_argument(
        "--backup",
        action="store_true",
        default=True,
        help="Create database backup (default: True)",
    )
    parser.add_argument(
        "--recursive",
        "-r",
        action="store_true",
        default=True,
        help="Process directories recursively (default: True)",
    )
    parser.add_argument(
        "--batch-size",
        "-b",
        type=int,
        default=5,
        help="Number of songs to process in each batch (default: 5)",
    )
    parser.add_argument(
        "--batch-delay",
        type=int,
        default=0,
        help="Delay in seconds between batches (default: 0)",
    )
    parser.add_argument(
        "--max-songs",
        "-m",
        type=int,
        default=None,
        help="Maximum number of songs to process (default: all songs)",
    )

    args = parser.parse_args()

    # Expand directories and patterns into audio files
    audio_files = expand_audio_files(args.paths)

    if not audio_files:
        print("❌ No audio files found to process")
        return

    # Limit number of songs if max-songs is specified
    original_count = len(audio_files)
    if args.max_songs and args.max_songs < len(audio_files):
        audio_files = audio_files[: args.max_songs]
        print(
            f"🎯 Limited to first {args.max_songs} songs out of "
            f"{original_count} found"
        )

    # Split into batches
    total_files = len(audio_files)
    batch_size = args.batch_size
    num_batches = (total_files + batch_size - 1) // batch_size
    print(f"🎵 Processing {total_files} audio files")
    print(f"📦 Processing in {num_batches} batches of {batch_size} songs each")

    # Initialize cuer (will auto-load from .env if api_key not provided)
    cuer = AutomaticMusicCuer(args.api_key, args.database, args.model)

    if not args.dry_run and cuer.is_virtualdj_running():
        print("❌ VirtualDJ appears to be running.")
        print("   Close VirtualDJ before making database changes, then run again.")
        print("   Dry-runs are safe while VirtualDJ is open: add --dry-run.")
        return

    # Create backup if requested (only once at the beginning)
    if args.backup and not args.dry_run:
        cuer.backup_database()

    # Process files in batches using efficient batch processing
    success_count = 0

    for batch_num in range(num_batches):
        start_idx = batch_num * batch_size
        end_idx = min(start_idx + batch_size, total_files)
        batch_files = audio_files[start_idx:end_idx]

        print(
            f"\n🔄 Batch {batch_num + 1}/{num_batches} - "
            f"Processing {len(batch_files)} files"
        )
        print(f"📊 Overall Progress: {start_idx}/{total_files} files completed")

        # Check if all batch files exist
        valid_batch_files = []
        for audio_file in batch_files:
            if os.path.exists(audio_file):
                valid_batch_files.append(audio_file)
            else:
                print(f"❌ File not found: {audio_file}")

        if not valid_batch_files:
            print(f"❌ No valid files in batch {batch_num + 1}")
            continue

        try:
            # Use async batch processing for concurrent uploads and retries
            batch_results = asyncio.run(
                cuer.process_audio_batch_async(valid_batch_files, args.dry_run)
            )

            # Count successes
            batch_success = sum(batch_results)
            success_count += batch_success

            print(
                f"\n✅ Batch {batch_num + 1} complete: {batch_success}/"
                f"{len(valid_batch_files)} files processed successfully"
            )

        except KeyboardInterrupt:
            print("\n⏹️  Processing interrupted by user")
            print(f"📊 Processed {success_count} files before interruption")
            return
        except Exception as e:
            print(f"❌ Error processing batch {batch_num + 1}: {e}")
            import traceback

            traceback.print_exc()
            continue

        # Add delay between batches if specified
        if args.batch_delay > 0 and batch_num < num_batches - 1:
            print(f"⏳ Waiting {args.batch_delay} seconds before next batch...")
            time.sleep(args.batch_delay)

    print(
        f"\n🎯 All batches complete: {success_count}/{total_files} files "
        f"processed successfully"
    )

    if args.dry_run:
        print("🔍 This was a dry run - no changes were made to the database")
        print("💡 Remove --dry-run flag to apply changes")


if __name__ == "__main__":
    main()
