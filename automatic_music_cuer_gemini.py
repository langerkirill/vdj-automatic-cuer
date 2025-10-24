#!/usr/bin/env python3
"""
Automatic Music Cueing System for VirtualDJ
Uses Google Gemini Pro 2.0 to analyze music files and generate intelligent
cues and loops
"""

import os
import json
import shutil
import xml.etree.ElementTree as ET
from datetime import datetime
import argparse
import google.generativeai as genai
from typing import Dict, List, Optional
from dotenv import load_dotenv
import html
from pydantic import BaseModel
import asyncio
import time


class MeasureChange(BaseModel):
    """Represents a significant musical change point for cues"""

    timestamp: float
    elements: List[str]
    cue_name: str
    color: str


class LoopSegment(BaseModel):
    """Represents a loop segment for DJing"""

    start: float
    length_beats: int
    elements: List[str]
    loop_name: str
    color: str


class MusicAnalysis(BaseModel):
    """Complete music analysis response from Gemini"""

    measure_changes: List[MeasureChange]
    loop_segments: List[LoopSegment]


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

    def __init__(self, gemini_api_key: str = None, vdj_database_path: str = None):
        """Initialize the automatic music cuer with Gemini Pro API"""
        # Load API key from .env file if not provided
        if gemini_api_key is None:
            load_dotenv()
            gemini_api_key = os.getenv("GEMINI_API_KEY")
            if not gemini_api_key:
                raise ValueError("GEMINI_API_KEY not found in environment or .env file")

        self.gemini_api_key = gemini_api_key
        genai.configure(api_key=gemini_api_key)
        self.model = genai.GenerativeModel("gemini-2.5-pro")

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

        print("🎵 Automatic Music Cuer initialized with Gemini")
        print(f"📁 VDJ Database: {self.vdj_database_path}")

    def backup_database(self) -> str:
        """Create a timestamped backup of the VDJ database"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = f"{self.vdj_database_path}.backup.{timestamp}"
        shutil.copy2(self.vdj_database_path, backup_path)
        print(f"✅ Database backed up to: {backup_path}")
        return backup_path

    def analyze_audio_with_gemini(self, audio_file_path: str) -> Dict:
        """Send audio file to Gemini Pro for musical analysis"""
        print(f"🔍 Analyzing {os.path.basename(audio_file_path)} with Gemini...")

        # Get song length for validation
        song_length = self.get_song_length(audio_file_path) or 300  # fallback to 5 min

        # Retry logic for temporary failures
        max_retries = 3
        retry_count = 0

        while retry_count < max_retries:
            try:
                # Upload audio file to Gemini with retry logic
                print(
                    f"📤 Uploading audio file "
                    f"({os.path.getsize(audio_file_path) / 1024 / 1024:.1f} MB)..."
                )

                # Retry upload up to 5 times for network issues
                audio_file = None
                upload_retries = 5
                for upload_retry in range(upload_retries):
                    try:
                        audio_file = genai.upload_file(audio_file_path)
                        print("✅ Upload complete")
                        break
                    except Exception as upload_e:
                        error_str = str(upload_e).lower()
                        is_network_error = any(
                            term in error_str
                            for term in [
                                "ssl",
                                "connection",
                                "network",
                                "broken pipe",
                                "timeout",
                                "reset",
                            ]
                        )

                        if is_network_error and upload_retry < upload_retries - 1:
                            wait_time = (upload_retry + 1) * 2  # 2s, 4s, 6s, 8s delays
                            print(
                                f"⚠️  Upload failed (attempt "
                                f"{upload_retry + 1}/{upload_retries}): {upload_e}"
                            )
                            print(f"🔄 Retrying upload in {wait_time} seconds...")
                            time.sleep(wait_time)
                        else:
                            raise upload_e

                if not audio_file:
                    raise Exception(
                        f"Failed to upload {audio_file_path} "
                        f"after {upload_retries} attempts"
                    )

                # Create prompt for JSON response with more specific instructions
                prompt = f"""
                You are analyzing a DJ track for precise cue point placement.
                Listen to the ENTIRE audio file carefully.

                Song Information:
                - Length: {song_length:.1f} seconds
                - BPM: {self.get_song_bpm_from_database(audio_file_path) or 'Unknown'}
                - File: {os.path.basename(audio_file_path)}
                
                CRITICAL TIMING INSTRUCTIONS:
                1. Listen to the actual audio - do NOT make assumptions based on
                   filename
                2. Pay attention to when elements ACTUALLY start/stop, not when you
                   think they should
                3. For vocals, listen for actual singing voices, not just
                   background sounds
                4. For drums, identify when the kick/snare pattern begins, not just
                   percussion
                5. Be very conservative - only mark transitions where you clearly
                   hear changes
                
                Find 5-6 significant musical changes where elements ACTUALLY change:
                - Real intro (before main elements start)
                - When drums ACTUALLY enter (not just percussion)
                - When vocals ACTUALLY start singing (not just vocal sounds)
                - Breakdown sections (where elements drop out)
                - Drops/build-ups (energy changes)
                
                Find 3 loop sections for DJing (16-32 beats long).
                IMPORTANT: Try to find ALL THREE types:
                1. DRUM LOOP (highest priority): A section with ONLY
                   drums/percussion, no melody, no vocals - perfect for DJ
                   transitions
                2. VOCAL LOOP: A section with prominent vocals (with or without
                   other elements) - great for crowd engagement
                3. MELODIC LOOP: A section with melody (synth/piano/guitar) but NO
                   drums and NO vocals - for smooth transitions
                
                Search the ENTIRE track to find these three distinct loop types.
                DJs need variety!
                
                Element Detection:
                - drums: Kick/snare patterns, not just hi-hats
                - vocals: Actual singing/rapping, not just vocal effects
                - bass: Prominent bassline
                - synth/piano: Melodic elements
                
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
                
                IMPORTANT: If you're not 100% sure about timing, be conservative and
                don't add that cue.
                
                LOOP REQUIREMENTS:
                - You MUST search for all 3 loop types (drum, vocal, melodic)
                - Even if a track is mostly instrumental, find the best vocal
                  section you can
                - Even if a track has constant drums, find a drum-only break
                  somewhere
                - Prioritize quality over quantity - find the BEST example of each
                  loop type
                """

                # Generate content with structured output and retry logic
                print("🤖 Analyzing audio with Gemini...")
                response = None
                analysis_retries = 3
                for analysis_retry in range(analysis_retries):
                    try:
                        response = self.model.generate_content(
                            contents=[prompt, audio_file],
                            generation_config=genai.GenerationConfig(
                                response_mime_type="application/json",
                                response_schema=MusicAnalysis,
                                temperature=0.1,  # Low temp for consistency
                            ),
                            request_options={
                                "timeout": 180
                            },  # 3 minute timeout for slow connections
                        )
                        break
                    except Exception as analysis_e:
                        error_str = str(analysis_e).lower()
                        is_retryable_error = any(
                            term in error_str
                            for term in [
                                "ssl",
                                "connection",
                                "network",
                                "broken pipe",
                                "timeout",
                                "reset",
                                "internal error",
                            ]
                        )

                        if is_retryable_error and analysis_retry < analysis_retries - 1:
                            wait_time = (analysis_retry + 1) * 3  # 3s, 6s delays
                            print(
                                f"⚠️  Analysis failed (attempt "
                                f"{analysis_retry + 1}/{analysis_retries}): "
                                f"{analysis_e}"
                            )
                            print(f"🔄 Retrying analysis in {wait_time} seconds...")
                            time.sleep(wait_time)
                        else:
                            print(f"⚠️  Gemini API error: {analysis_e}")
                            raise analysis_e

                if not response:
                    raise Exception("Failed to get analysis response after retries")

                # Parse structured JSON response
                try:
                    # With structured output, we should get clean JSON directly
                    raw_text = response.text

                    # Clean up any malformed numbers (extremely long decimals)
                    import re

                    # Replace extremely long decimal numbers with reasonable precision
                    cleaned_text = re.sub(
                        r"(\d+\.\d{10,})",
                        lambda m: f"{float(m.group(1)):.2f}",
                        raw_text,
                    )

                    analysis_data = json.loads(cleaned_text)

                    # Round all timestamps to 2 decimal places
                    if "measure_changes" in analysis_data:
                        for cue in analysis_data["measure_changes"]:
                            if "timestamp" in cue:
                                cue["timestamp"] = round(float(cue["timestamp"]), 2)

                    if "loop_segments" in analysis_data:
                        for loop in analysis_data["loop_segments"]:
                            if "start" in loop:
                                loop["start"] = round(float(loop["start"]), 2)

                    print(
                        f"✅ Analysis complete: "
                        f"{len(analysis_data.get('measure_changes', []))} cues, "
                        f"{len(analysis_data.get('loop_segments', []))} loops"
                    )

                    # Debug: Show raw Gemini response
                    print("\n🔍 DEBUG - Raw Gemini response:")
                    print(f"  Response text: {response.text[:500]}...")

                    print("\n🔍 DEBUG - Structured output timestamps:")
                    for i, cue in enumerate(
                        analysis_data.get("measure_changes", []), 1
                    ):
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
                    print(f"Raw response: {response.text}")
                    return None

            except Exception as e:
                retry_count += 1
                error_str = str(e).lower()
                # Check for retryable errors
                # (server errors, SSL issues, timeouts)
                is_retryable = any(
                    term in error_str
                    for term in [
                        "500",
                        "502",
                        "503",
                        "504",
                        "internal error",
                        "ssl",
                        "timeout",
                        "connection",
                        "network",
                    ]
                )

                if is_retryable:
                    if retry_count < max_retries:
                        print(
                            f"⚠️  Temporary error (attempt "
                            f"{retry_count}/{max_retries}): {e}"
                        )
                        print(f"🔄 Retrying in {retry_count * 3} seconds...")
                        time.sleep(retry_count * 3)  # Increasing delay
                        continue
                    else:
                        print(
                            f"❌ Max retries ({max_retries}) reached. "
                            f"Gemini API unavailable."
                        )
                        return None
                else:
                    import traceback

                    print(f"❌ Error analyzing audio with Gemini: {e}")
                    print("🔍 Full traceback:")
                    traceback.print_exc()
                    return None

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

    def validate_timing_hybrid(
        self, gemini_timestamp: float, bpm: float, file_path: str
    ) -> float:
        """Hybrid timing validation: use Gemini's timestamp if reasonable,
        otherwise align to nearest '1' beat"""
        # Get beatgrid info
        beatgrid_offset = self.get_beatgrid_offset(file_path)

        if bpm <= 0 or bpm > 200 or bpm < 60:
            print(
                f"🎯 Invalid BPM {bpm}, using Gemini timestamp as-is: "
                f"{gemini_timestamp:.1f}s"
            )
            return gemini_timestamp

        # Convert VDJ fractional BPM to actual BPM if needed
        actual_bpm = bpm
        if bpm < 5:  # VDJ fractional format
            actual_bpm = 60.0 / bpm

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
                # Use a custom retry wrapper for genai.upload_file
                uploaded_file = await asyncio.get_event_loop().run_in_executor(
                    None, genai.upload_file, audio_file_path
                )
                print(f"✅ {os.path.basename(audio_file_path)} upload complete")
                return uploaded_file
            except Exception as e:
                error_str = str(e).lower()
                is_network_error = any(
                    term in error_str
                    for term in [
                        "ssl",
                        "connection",
                        "network",
                        "broken pipe",
                        "timeout",
                        "reset",
                        "errno 32",
                    ]
                )

                if is_network_error and retry < max_retries - 1:
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
                for audio_file_path, _ in uploaded_files:
                    task = asyncio.get_event_loop().run_in_executor(
                        None, self.analyze_audio_with_gemini, audio_file_path
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
            for audio_file_path, _ in uploaded_files:
                task = asyncio.get_event_loop().run_in_executor(
                    None, self.analyze_audio_with_gemini, audio_file_path
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

                    # Retry upload up to 5 times for network issues
                    uploaded_file = None
                    max_retries = 5
                    for retry in range(max_retries):
                        try:
                            uploaded_file = genai.upload_file(audio_file_path)
                            break
                        except Exception as e:
                            error_str = str(e).lower()
                            is_network_error = any(
                                term in error_str
                                for term in [
                                    "ssl",
                                    "connection",
                                    "network",
                                    "broken pipe",
                                    "timeout",
                                    "reset",
                                ]
                            )

                            if is_network_error and retry < max_retries - 1:
                                wait_time = (retry + 1) * 2  # 2s, 4s, 6s, 8s delays
                                print(
                                    f"⚠️  Upload failed (attempt "
                                    f"{retry + 1}/{max_retries}): {e}"
                                )
                                print(f"🔄 Retrying in {wait_time} seconds...")
                                time.sleep(wait_time)
                            else:
                                raise e

                    if uploaded_file:
                        uploaded_files.append((audio_file_path, uploaded_file))
                    else:
                        raise Exception(
                            f"Failed to upload {audio_file_path} "
                            f"after {max_retries} attempts"
                        )

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

                # Retry upload up to 5 times for network issues
                uploaded_file = None
                max_retries = 5
                for retry in range(max_retries):
                    try:
                        uploaded_file = genai.upload_file(audio_file_path)
                        break
                    except Exception as e:
                        error_str = str(e).lower()
                        is_network_error = any(
                            term in error_str
                            for term in [
                                "ssl",
                                "connection",
                                "network",
                                "broken pipe",
                                "timeout",
                                "reset",
                            ]
                        )

                        if is_network_error and retry < max_retries - 1:
                            wait_time = (retry + 1) * 2  # 2s, 4s, 6s, 8s delays
                            print(
                                f"⚠️  Upload failed (attempt "
                                f"{retry + 1}/{max_retries}): {e}"
                            )
                            print(f"🔄 Retrying in {wait_time} seconds...")
                            time.sleep(wait_time)
                        else:
                            raise e

                if uploaded_file:
                    uploaded_files.append((audio_file_path, uploaded_file))
                else:
                    raise Exception(
                        f"Failed to upload {audio_file_path} "
                        f"after {max_retries} attempts"
                    )

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

            # Use structured output for better JSON parsing
            class BatchMusicAnalysis(BaseModel):
                """Complete batch music analysis response from Gemini"""

                analyses: List[MusicAnalysis]

            # Send to Gemini with structured output
            response = self.model.generate_content(
                contents=[uf[1] for uf in uploaded_files],
                generation_config=genai.GenerationConfig(
                    response_mime_type="application/json",
                    response_schema=BatchMusicAnalysis,
                    temperature=0.1,
                ),
                request_options={"timeout": 300},  # 5 minute timeout for batch
            )

            if not response or not response.text:
                print("❌ Empty response from Gemini")
                return []

            # Parse structured JSON response
            try:
                raw_text = response.text

                # Clean up any malformed numbers
                import re

                cleaned_text = re.sub(
                    r"(\d+\.\d{10,})",
                    lambda m: f"{float(m.group(1)):.2f}",
                    raw_text,
                )

                batch_data = json.loads(cleaned_text)

                # Extract analyses from the structured response
                if "analyses" in batch_data:
                    analyses_list = batch_data["analyses"]
                else:
                    # Fallback if the response structure is different
                    analyses_list = batch_data if isinstance(batch_data, list) else []

                # Round all timestamps to 2 decimal places
                for analysis_data in analyses_list:
                    if "measure_changes" in analysis_data:
                        for cue in analysis_data["measure_changes"]:
                            if "timestamp" in cue:
                                cue["timestamp"] = round(float(cue["timestamp"]), 2)

                    if "loop_segments" in analysis_data:
                        for loop in analysis_data["loop_segments"]:
                            if "start" in loop:
                                loop["start"] = round(float(loop["start"]), 2)

                print(
                    f"✅ Successfully analyzed {len(analyses_list)} " f"songs in batch"
                )
                return analyses_list

            except json.JSONDecodeError as e:
                print(f"❌ Failed to parse batch JSON response: {e}")
                print(f"Raw response: {response.text[:500]}...")
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

                for i, cue_data in enumerate(cues[:6], 1):
                    cue_name = cue_data.get("cue_name", f"cue{i}")
                    timestamp = cue_data.get("timestamp", 0)
                    color = cue_data.get("color", "green")
                    elements = cue_data.get("elements", [])
                    print(
                        f"  Cue {i}: '{cue_name}' at {timestamp:.1f}s | "
                        f"Color: {color.capitalize()} | Elements: {elements}"
                    )

                for i, loop_data in enumerate(loops[:3], 1):
                    loop_name = loop_data.get("loop_name", f"loop{i}l")
                    start = loop_data.get("start", 0)
                    beats = loop_data.get("length_beats", 16)
                    color = loop_data.get("color", "green")
                    elements = loop_data.get("elements", [])
                    print(
                        f"  Loop {i}: '{loop_name}' at {start:.1f}s "
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
    cuer = AutomaticMusicCuer(args.api_key, args.database)

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
