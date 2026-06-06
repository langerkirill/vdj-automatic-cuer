"""GeminiAnalysisMixin for AutomaticMusicCuer."""

from .common import *


class GeminiAnalysisMixin:
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
                - Use "Drop" only when the timestamp marks an actual energy rise or
                  fuller section entering. If the point is musically useful but the
                  energy is already steady, rename it to "Rhythm Section", "Groove",
                  or "Vocal Mix"; if the true drop is elsewhere, move the cue.
                - Use "Breakdown" only when elements clearly drop out at that exact
                  timestamp. If the label is wrong but the cue is useful, rename it;
                  if the actual breakdown is elsewhere, move the cue.
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
