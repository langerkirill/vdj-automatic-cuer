"""GeminiAnalysisMixin for AutomaticMusicCuer."""

from .analysis_cache import (
    analysis_is_usable,
    analyze_with_cache,
    load_cached_analysis,
    save_cached_analysis,
)
from .common import *
from .gemini_call import generate_json, generate_json_async
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
                timestamp, actual_bpm, beatgrid_offset, grid_beats=16
            )
        for loop in analysis_data.get("loop_segments", []):
            timestamp = float(loop.get("start", 0.0))
            loop["model_timestamp"] = timestamp
            loop["start"] = self._quantize_grid_time(
                timestamp, actual_bpm, beatgrid_offset, grid_beats=16
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
        """Keep grid times at VDJ Pos precision (6 decimals), not 0.01s."""
        if "measure_changes" in analysis_data:
            for cue in analysis_data["measure_changes"]:
                if "timestamp" in cue:
                    cue["timestamp"] = round(float(cue["timestamp"]), 6)

        if "loop_segments" in analysis_data:
            for loop in analysis_data["loop_segments"]:
                if "start" in loop:
                    loop["start"] = round(float(loop["start"]), 6)

        return analysis_data

    @staticmethod
    def _model_supports_thinking_level(model: str) -> bool:
        name = (model or "").casefold()
        return "gemini-3" in name and "flash" not in name

    @staticmethod
    def _is_unsupported_thinking_error(error: Exception) -> bool:
        return "thinking level is not supported" in str(error).lower()

    def _can_skip_gemini_naming(self, error: Exception) -> bool:
        """True when Gemini naming failed in a way stem+ML can still recover."""
        return (
            isinstance(error, json.JSONDecodeError)
            or self._is_empty_response_error(error)
            or self._is_capacity_error(error)
            or self._is_retryable_error(error)
        )

    @staticmethod
    def _empty_response_detail(response: object) -> str:
        """Explain why Gemini returned no text (block, finish_reason, …)."""
        if response is None:
            return ""
        parts: List[str] = []
        feedback = getattr(response, "prompt_feedback", None)
        block = getattr(feedback, "block_reason", None)
        if block:
            parts.append(f"block_reason={block}")
        candidates = getattr(response, "candidates", None)
        first = None
        if isinstance(candidates, (list, tuple)) and candidates:
            first = candidates[0]
        if first is not None:
            finish = getattr(first, "finish_reason", None)
            if finish:
                parts.append(f"finish_reason={finish}")
        return f" ({', '.join(parts)})" if parts else ""

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
        payload, used = generate_json(
            self.client,
            contents,
            schema,
            models=self._model_candidates(),
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        )
        if used != self.model_name:
            print(f"↪️  Switched AutoCue model to {used}")
        self.model_name = used
        return payload

    async def _generate_json_content_async(
        self,
        contents: List[object],
        schema: Type[BaseModel],
        timeout_seconds: int,
        max_retries: int = DEFAULT_ANALYSIS_RETRIES,
    ) -> Dict:
        """Call Gemini without an executor thread so cancellation stays prompt."""
        payload, used = await generate_json_async(
            self.client,
            contents,
            schema,
            models=self._model_candidates(),
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        )
        if used != self.model_name:
            print(f"↪️  Switched AutoCue model to {used}")
        self.model_name = used
        return payload

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
            beatgrid_offset = float(self.get_beatgrid_offset(audio_file_path) or 0.0)
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
        naming_error: Optional[Exception] = None
        try:
            analysis_data = self._generate_json_content(
                contents=[prompt, audio_file]
                + [uploaded for _, uploaded in stem_uploads],
                schema=MusicAnalysis,
                timeout_seconds=180,
            )
        except Exception as exc:
            if not self._can_skip_gemini_naming(exc):
                raise
            naming_error = exc
            print(
                f"⚠️  Gemini naming skipped ({exc}); "
                "using stem + ML times"
            )
            analysis_data = {
                "measure_changes": [],
                "loop_segments": [],
                "gemini_naming_skipped": True,
            }
        finalized = self._finalize_music_analysis(
            analysis_data, stem_files, bpm, audio_file_path
        )
        if naming_error is not None and not analysis_is_usable(finalized):
            raise naming_error
        return finalized

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
        naming_error: Optional[Exception] = None
        try:
            analysis_data = await self._generate_json_content_async(
                contents=[prompt, audio_file]
                + [uploaded for _, uploaded in stem_uploads],
                schema=MusicAnalysis,
                timeout_seconds=180,
            )
        except Exception as exc:
            if not self._can_skip_gemini_naming(exc):
                raise
            naming_error = exc
            print(
                f"⚠️  Gemini naming skipped ({exc}); "
                "using stem + ML times"
            )
            analysis_data = {
                "measure_changes": [],
                "loop_segments": [],
                "gemini_naming_skipped": True,
            }
        finalized = self._finalize_music_analysis(
            analysis_data, stem_files, bpm, audio_file_path
        )
        if naming_error is not None and not analysis_is_usable(finalized):
            raise naming_error
        return finalized

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
        """Build a concise evidence-first prompt for Gemini."""
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
- timestamp is a yellow phrase [1]: every 16 beats (4 bars) from Cue 1 /
  Scan Phase. Never beat 2/3/4, never a bar 1 that is not that phrase [1]
- role is one of intro, entry, groove, build, drop, breakdown, vocal, outro
- confidence is 0.0-1.0 and must reflect audible certainty
- elements contains every clearly audible component from drums, vocals, bass,
  piano, synth, strings, and guitar
- cue_name describes only what is supported at that timestamp
- never use "&" in cue_name or loop_name — write "and" instead
  (e.g. "Bass and Snaps In", not "Bass & Snaps In")
- A cue must be safe to jump to. At the exact press (the 1) the vocal
  stem is either silent or already rolling. Do not place a cue where a
  vocal *enters* on that 1 — jumping in would catch the word. Groove
  and instrumental cues fail the same onset test.

For each loop:
- start must be a yellow phrase [1] (every 16 beats from Cue 1). Never beat
  2/3/4
- start must be the beginning of the section you are looping (e.g. a Drop
  loop starts at the Drop cue, not at a Build cue two phrases earlier)
- Prefer an early melodic intro loop when the first 8-16 beats are a stable
  instrument-only phrase (no drums/vocals) that wraps cleanly — these are
  high-value DJ loops even if the rest of the track is sparse
- Do not start a loop mid-line / mid-bar. The start is a phrase [1].
- Vocals already rolling on that 1 are allowed when the phrase wraps
  cleanly. Do not reject a chorus or vocal-mix loop because the singer
  is on.
- loop_name must match that section and the stem-supported components (use
  Melodic for instrument-only intro phrases)
- the component makeup stays stable for the whole loop
- the wrap from loop end back to start must stay continuous (same level and
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
- Use Groove, Vocal Mix, Synth Intro, Beat Entry, Build, Drop, Chorus, or
  Verse when a useful part does not satisfy the stronger Drop/Breakdown claim.
  Never name a cue "Section" or "Rhythm Section".

Colors are deterministic categories:
- blue: instruments/bass, no drums, no vocals
- green: instruments/bass plus drums, no vocals
- purple: drums only
- yellow: drums plus vocals, with or without instruments/bass
- orange: vocals with no drums, with or without instruments/bass

Use the full mix to locate structure. Use isolated stems only as supporting
evidence. Do not infer from the filename. Place timestamps on phrase 1s
(every 4 bars from Cue 1 / Scan Phase). Do not round them off that grid.
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

        def _run(_path: str):
            return self._analyze_audio_with_gemini_uncached(
                audio_file_path, uploaded_file
            )

        return analyze_with_cache(
            _run,
            audio_file_path,
            model=getattr(self, "model_name", None),
        )

    def _analyze_audio_with_gemini_uncached(
        self, audio_file_path: str, uploaded_file=None
    ) -> Dict:
        song_length = self.get_song_length(audio_file_path) or 300
        bpm = self.get_song_bpm_from_database(audio_file_path)
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

        cached = load_cached_analysis(
            audio_file_path, model=getattr(self, "model_name", None)
        )
        if cached is not None:
            print("📦 Reusing cached AutoCue analysis (no Gemini upload)")
            return cached

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
            if analysis_data:
                save_cached_analysis(
                    audio_file_path,
                    analysis_data,
                    model=getattr(self, "model_name", None),
                )
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
