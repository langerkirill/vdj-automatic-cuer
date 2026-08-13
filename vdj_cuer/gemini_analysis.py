"""GeminiAnalysisMixin for AutomaticMusicCuer."""

from .common import *
from .precision_gate import apply_precision_gate


class GeminiAnalysisMixin:
    def _align_analysis_candidates(
        self,
        analysis_data: Dict,
        actual_bpm: Optional[float],
        beatgrid_offset: float,
    ) -> Dict:
        """Align candidates before measuring any audio evidence at their positions."""
        if not actual_bpm:
            return analysis_data

        for cue in analysis_data.get("measure_changes", []):
            timestamp = float(cue.get("timestamp", 0.0))
            cue["model_timestamp"] = timestamp
            cue["timestamp"] = self._quantize_grid_time(
                timestamp, actual_bpm, beatgrid_offset, grid_beats=4
            )
        for loop in analysis_data.get("loop_segments", []):
            timestamp = float(loop.get("start", 0.0))
            loop["model_timestamp"] = timestamp
            # Snap to a beat. Prefer the 1 when the model already aimed there,
            # but mid-bar beat starts are allowed when they wrap better.
            loop["start"] = self._quantize_grid_time(
                timestamp, actual_bpm, beatgrid_offset, grid_beats=1
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

    @staticmethod
    def _model_supports_thinking_level(model: str) -> bool:
        name = (model or "").casefold()
        return "gemini-3" in name

    @staticmethod
    def _is_unsupported_thinking_error(error: Exception) -> bool:
        return "thinking level is not supported" in str(error).lower()

    def _generate_json_config(
        self,
        schema: Type[BaseModel],
        timeout_seconds: int,
        *,
        thinking: bool,
    ) -> types.GenerateContentConfig:
        kwargs: Dict[str, object] = {
            "response_mime_type": "application/json",
            "response_json_schema": schema.model_json_schema(),
            "http_options": types.HttpOptions(timeout=timeout_seconds * 1000),
        }
        if thinking:
            kwargs["thinking_config"] = types.ThinkingConfig(thinking_level="high")
        return types.GenerateContentConfig(**kwargs)

    def _generate_json_content(
        self,
        contents: List[object],
        schema: Type[BaseModel],
        timeout_seconds: int,
        max_retries: int = DEFAULT_ANALYSIS_RETRIES,
    ) -> Dict:
        """Call Gemini with structured JSON output and retry temporary failures."""
        last_error: Optional[Exception] = None
        for model in self._model_candidates():
            use_thinking = self._model_supports_thinking_level(model)
            for analysis_retry in range(max_retries):
                try:
                    response = self.client.models.generate_content(
                        model=model,
                        contents=contents,
                        config=self._generate_json_config(
                            schema, timeout_seconds, thinking=use_thinking
                        ),
                    )
                    if not response or not response.text:
                        raise ValueError("Empty response from Gemini")
                    if model != self.model_name:
                        print(f"↪️  Switched AutoCue model to {model}")
                    self.model_name = model
                    return self._parse_json_response(response.text)
                except Exception as analysis_e:
                    last_error = analysis_e
                    if self._is_unsupported_thinking_error(analysis_e) and use_thinking:
                        print(f"⚠️  {model} does not support thinking_level; retrying without it")
                        use_thinking = False
                        continue
                    if self._is_daily_quota_error(analysis_e):
                        print(
                            f"⚠️  Daily quota on {model}; trying another Pro…"
                        )
                        break
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

        if last_error is not None:
            raise last_error
        raise RuntimeError("Failed to get analysis response after retries")

    async def _generate_json_content_async(
        self,
        contents: List[object],
        schema: Type[BaseModel],
        timeout_seconds: int,
        max_retries: int = DEFAULT_ANALYSIS_RETRIES,
    ) -> Dict:
        """Call Gemini without an executor thread so cancellation stays prompt."""
        last_error: Optional[Exception] = None
        for model in self._model_candidates():
            use_thinking = self._model_supports_thinking_level(model)
            for analysis_retry in range(max_retries):
                try:
                    response = await self.client.aio.models.generate_content(
                        model=model,
                        contents=contents,
                        config=self._generate_json_config(
                            schema, timeout_seconds, thinking=use_thinking
                        ),
                    )
                    if not response or not response.text:
                        raise ValueError("Empty response from Gemini")
                    if model != self.model_name:
                        print(f"↪️  Switched AutoCue model to {model}")
                    self.model_name = model
                    return self._parse_json_response(response.text)
                except asyncio.CancelledError:
                    raise
                except Exception as analysis_error:
                    last_error = analysis_error
                    if (
                        self._is_unsupported_thinking_error(analysis_error)
                        and use_thinking
                    ):
                        print(
                            f"⚠️  {model} does not support thinking_level; retrying without it"
                        )
                        use_thinking = False
                        continue
                    if self._is_daily_quota_error(analysis_error):
                        print(
                            f"⚠️  Daily quota on {model}; trying another Pro…"
                        )
                        break
                    if self._is_retryable_error(analysis_error) and (
                        analysis_retry < max_retries - 1
                    ):
                        wait_time = min((analysis_retry + 1) * 3, 30)
                        print(
                            f"⚠️  Analysis failed (attempt "
                            f"{analysis_retry + 1}/{max_retries}): {analysis_error}"
                        )
                        print(f"🔄 Retrying analysis in {wait_time} seconds...")
                        await asyncio.sleep(wait_time)
                        continue

                    print(f"⚠️  Gemini API error: {analysis_error}")
                    raise

        if last_error is not None:
            raise last_error
        raise RuntimeError("Failed to get analysis response after retries")

    def _finalize_music_analysis(
        self,
        analysis_data: Dict,
        stem_files: List[Tuple[str, str]],
        bpm: Optional[float],
        audio_file_path: Optional[str],
    ) -> Dict:
        actual_bpm = self._actual_bpm(bpm)
        beatgrid_offset = 0.0
        if audio_file_path and actual_bpm:
            beatgrid_offset = self._get_verified_beatgrid_offset(audio_file_path, bpm)
        analysis_data = self._align_analysis_candidates(
            analysis_data, actual_bpm, beatgrid_offset
        )
        analysis_data = self._apply_measured_stem_activity(
            analysis_data,
            stem_files,
            bpm=bpm,
            audio_file_path=audio_file_path,
        )
        analysis_data = self._normalize_analysis_data(analysis_data)
        analysis_data = self._validate_structural_assertions(
            analysis_data, audio_file_path
        )
        return apply_precision_gate(analysis_data, actual_bpm, beatgrid_offset)

    def _generate_music_analysis(
        self,
        prompt: str,
        audio_file,
        stem_uploads: Optional[List[Tuple[str, object]]] = None,
        stem_files: Optional[List[Tuple[str, str]]] = None,
        bpm: Optional[float] = None,
        audio_file_path: Optional[str] = None,
    ) -> Dict:
        """Generate and normalize structured analysis for one uploaded file."""
        stem_uploads = stem_uploads or []
        stem_files = stem_files or []
        analysis_data = self._generate_json_content(
            contents=[prompt, audio_file] + [uploaded for _, uploaded in stem_uploads],
            schema=MusicAnalysis,
            timeout_seconds=180,
        )
        return self._finalize_music_analysis(
            analysis_data, stem_files, bpm, audio_file_path
        )

    async def _generate_music_analysis_async(
        self,
        prompt: str,
        audio_file,
        stem_uploads: Optional[List[Tuple[str, object]]] = None,
        stem_files: Optional[List[Tuple[str, str]]] = None,
        bpm: Optional[float] = None,
        audio_file_path: Optional[str] = None,
    ) -> Dict:
        stem_uploads = stem_uploads or []
        stem_files = stem_files or []
        analysis_data = await self._generate_json_content_async(
            contents=[prompt, audio_file] + [uploaded for _, uploaded in stem_uploads],
            schema=MusicAnalysis,
            timeout_seconds=180,
        )
        return self._finalize_music_analysis(
            analysis_data, stem_files, bpm, audio_file_path
        )

    @staticmethod
    def _report_analysis(analysis_data: Dict) -> None:
        print(
            f"✅ Analysis complete: "
            f"{len(analysis_data.get('measure_changes', []))} cues, "
            f"{len(analysis_data.get('loop_segments', []))} loops"
        )
        gate = analysis_data.get("precision_gate", {})
        rejected = sum(gate.get("rejected", {}).values())
        if rejected:
            print(f"🛡️  Precision gate rejected {rejected} weak assertions")

        print("\n🔍 DEBUG - Structured output timestamps:")
        for i, cue in enumerate(analysis_data.get("measure_changes", []), 1):
            print(
                f"  Cue {i}: {cue.get('cue_name', 'unnamed')} at "
                f"{cue.get('timestamp', 0)}s - {cue.get('elements', [])} - "
                f"Color: {cue.get('color', 'none')}"
            )
        for i, loop in enumerate(analysis_data.get("loop_segments", []), 1):
            print(
                f"  Loop {i}: {loop.get('loop_name', 'unnamed')} at "
                f"{loop.get('start', 0)}s "
                f"({loop.get('length_beats', 0)} beats) - "
                f"Color: {loop.get('color', 'none')}"
            )
        print()

    @staticmethod
    def _build_precision_prompt(
        audio_file_path: str,
        song_length: float,
        bpm: Optional[float],
        stem_prompt: str,
    ) -> str:
        """Build a concise evidence-first prompt for Gemini 3.1 Pro."""
        return f"""
You are locating reliable DJ cue boundaries in one complete track.

Track: {os.path.basename(audio_file_path)}
Duration: {song_length:.1f} seconds
BPM: {bpm or 'unknown'}

{stem_prompt}

Return 4-6 high-confidence structural cues and 2-3 high-confidence loops.
You must return at least 2 loops on any track longer than 45 seconds
(intro/melodic phrase, plus a body/groove or drop). A third loop (breakdown
or outro) is preferred. Never invent a vocal, drum-only, or melodic-only
*label* to satisfy a quota — pick real repeating phrases that wrap.

For each cue:
- timestamp is the first beat of the bar where the new section begins
- role is one of intro, entry, groove, build, drop, breakdown, vocal, outro,
  or section
- confidence is 0.0-1.0 and must reflect audible certainty
- elements contains every clearly audible component from drums, vocals, bass,
  piano, synth, strings, and guitar
- cue_name describes only what is supported at that timestamp
- never use "&" in cue_name or loop_name — write "and" instead
  (e.g. "Bass and Snaps In", not "Bass & Snaps In")

For each loop:
- Prefer starting on beat 1 of a bar when it still wraps cleanly; starting on
  beat 2/3/4 is allowed when that yields a better seamless wrap
- start must be the beginning of the section you are looping (e.g. a Drop
  loop starts at the Drop cue, not at a Build cue two phrases earlier)
- Prefer an early melodic intro loop when the first 8-16 beats are a stable
  instrument-only phrase (no drums/vocals) that wraps cleanly — these are
  high-value DJ loops even if the rest of the track is sparse
- Never place a vocal cue or loop where a lyric line is already running
  (pre-chorus words into a chorus). Markers must be phrase attacks that are
  safe to cue-jump to; mid-line starts are invalid
- loop_name must match that section and the audible components (use Melodic
  for instrument-only intro phrases)
- the component makeup stays stable for the whole loop
- the wrap from loop end back to start must sound continuous (same level and
  texture); do not place loops on evolving solos, progressing chords, or
  vocal phrases that do not restart cleanly
- length_beats is 8 or 16 preferred; 32 only on fast tracks where 32 beats is
  still under ~14 seconds. Slow ambient tracks should use 8 (never a 25s loop)
- role is loop
- confidence is 0.0-1.0
- every loop must wrap continuously (same level and texture at the splice)
- if a 16-beat wrap is messy, try 8 beats at the same start
- returning 0 or 1 loop is a failure on a normal-length song; find two
  different repeating regions (early + later) instead of giving up

Strict assertions:
- Drums means a kick/snare rhythm, not incidental percussion.
- Vocals means singing or rapping, not a vocal-like effect.
- Melodic or Melody is allowed only for foreground melody with no drums/vocals.
- Drum/Drums/Percussion in a name is allowed only when no bass, instruments,
  melody, or vocals are audible.
- Drop requires a clear energy increase at the exact timestamp.
- Breakdown requires a clear reduction of elements at the exact timestamp.
- Use Groove, Rhythm Section, Vocal Mix, or Synth Section when a useful section
  does not satisfy the stronger Drop/Breakdown/Drums/Melodic claim.

Colors are deterministic categories:
- blue: instruments/bass, no drums, no vocals
- green: instruments/bass plus drums, no vocals
- purple: drums only
- yellow: drums plus vocals, with or without instruments/bass
- orange: vocals with no drums, with or without instruments/bass

Use the full mix to locate structure. Use isolated stems only as supporting
evidence. Do not infer from the filename. Round timestamps to 0.01 seconds.
""".strip()

    def analyze_audio_with_gemini(self, audio_file_path: str, uploaded_file=None) -> Dict:
        """Send audio file to Gemini Pro for musical analysis"""
        print(f"🔍 Analyzing {os.path.basename(audio_file_path)} with Gemini...")

        # Get song metadata before uploading anything.
        song_length = self.get_song_length(audio_file_path) or 300  # fallback to 5 min
        bpm = self.get_song_bpm_from_database(audio_file_path)
        if self._actual_bpm(bpm) is None:
            print("❌ A valid VirtualDJ BPM/beatgrid is required for precise cues")
            return None

        audio_file = uploaded_file
        owns_audio_file = uploaded_file is None
        stem_uploads = []
        stem_temp_dir = None
        try:
            # Upload only when the caller has not already uploaded this file.
            if audio_file is None:
                print(
                    f"📤 Uploading audio file "
                    f"({os.path.getsize(audio_file_path) / 1024 / 1024:.1f} MB)..."
                )
                audio_file = self._upload_audio_file_with_retry(audio_file_path)
            else:
                print(f"📎 Reusing uploaded file for {os.path.basename(audio_file_path)}")

            stem_uploads, stem_files, stem_temp_dir = (
                self._prepare_vdj_stems_with_retry(audio_file_path)
            )
            stem_prompt = self._stem_upload_prompt(stem_uploads)
            prompt = self._build_precision_prompt(
                audio_file_path, song_length, bpm, stem_prompt
            )

            print("🤖 Analyzing audio with Gemini...")
            analysis_data = self._generate_music_analysis(
                prompt,
                audio_file,
                stem_uploads,
                stem_files,
                bpm,
                audio_file_path,
            )
            self._report_analysis(analysis_data)

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
        finally:
            remote_files = [uploaded for _, uploaded in stem_uploads]
            if owns_audio_file and audio_file is not None:
                remote_files.append(audio_file)
            self._delete_uploaded_files(remote_files)
            if stem_temp_dir:
                shutil.rmtree(stem_temp_dir, ignore_errors=True)

    async def analyze_audio_with_gemini_async(
        self, audio_file_path: str, uploaded_file=None
    ) -> Dict:
        """Cancellable async analysis used by the CLI batch workflow."""
        print(f"🔍 Analyzing {os.path.basename(audio_file_path)} with Gemini...")
        song_length = self.get_song_length(audio_file_path) or 300
        bpm = self.get_song_bpm_from_database(audio_file_path)
        if self._actual_bpm(bpm) is None:
            print("❌ A valid VirtualDJ BPM/beatgrid is required for precise cues")
            return None

        audio_file = uploaded_file
        owns_audio_file = uploaded_file is None
        stem_uploads = []
        stem_temp_dir = None
        try:
            if audio_file is None:
                print(
                    f"📤 Uploading audio file "
                    f"({os.path.getsize(audio_file_path) / 1024 / 1024:.1f} MB)..."
                )
                audio_file = await self.upload_file_with_retry(audio_file_path)
            else:
                print(f"📎 Reusing uploaded file for {os.path.basename(audio_file_path)}")

            stem_uploads, stem_files, stem_temp_dir = (
                await self._prepare_vdj_stems_async(audio_file_path)
            )
            stem_prompt = self._stem_upload_prompt(stem_uploads)
            prompt = self._build_precision_prompt(
                audio_file_path, song_length, bpm, stem_prompt
            )
            print("🤖 Analyzing audio with Gemini...")
            analysis_data = await self._generate_music_analysis_async(
                prompt,
                audio_file,
                stem_uploads,
                stem_files,
                bpm,
                audio_file_path,
            )
            self._report_analysis(analysis_data)
            return analysis_data
        except asyncio.CancelledError:
            print(f"⏹️  Cancelled analysis for {os.path.basename(audio_file_path)}")
            raise
        except json.JSONDecodeError as error:
            print(f"❌ Failed to parse structured JSON response: {error}")
            return None
        except Exception as error:
            import traceback

            print(f"❌ Error analyzing audio with Gemini: {error}")
            print("🔍 Full traceback:")
            traceback.print_exc()
            return None
        finally:
            remote_files = [uploaded for _, uploaded in stem_uploads]
            if owns_audio_file and audio_file is not None:
                remote_files.append(audio_file)
            if remote_files:
                await asyncio.shield(self._delete_uploaded_files_async(remote_files))
            if stem_temp_dir:
                shutil.rmtree(stem_temp_dir, ignore_errors=True)
