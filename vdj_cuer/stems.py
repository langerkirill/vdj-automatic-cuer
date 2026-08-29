"""StemMixin for AutomaticMusicCuer."""

from .common import *
from .precision_gate import MIN_LOOP_CONFIDENCE
from .loop_seam_gemini import (
    LOOP_SEAM_GEMINI_MAX_CHECKS,
    LOOP_SEAM_GEMINI_TIMEOUT_SECONDS,
    LOOP_SEAM_MAX_ATTEMPTS,
    LOOP_SEAM_MIN_HALF_SECONDS,
    LoopSeamJudgment,
    build_loop_wrap_clip,
    loop_seam_prompt,
    seam_half_seconds,
)
from .stem_evidence import (
    ACTIVITY_RANK,
    is_clean_phrase_entry,
    load_stem_profiles,
    loop_is_stable,
    loop_seam_is_clean,
    measure_stem_evidence,
)


def _max_loop_beats_for_tempo(beat_duration: float) -> int:
    """Longest allowed loop length in beats for this tempo."""
    if beat_duration <= 0:
        return 16
    for beats in (32, 16, 8, 4):
        if beats * beat_duration <= MAX_LOOP_DURATION_SECONDS:
            return beats
    return 8


def _cap_loop_length_beats(length_beats: int, beat_duration: float) -> int:
    """Shorten over-long loops (32 beats at 75 BPM is ~26s — not DJ-usable)."""
    try:
        beats = int(length_beats)
    except (TypeError, ValueError):
        beats = 16
    if beats not in LOOP_BEAT_CHOICES:
        beats = 16
    max_beats = _max_loop_beats_for_tempo(beat_duration)
    while beats > max_beats:
        beats = max(beats // 2, MIN_USEFUL_LOOP_BEATS)
    return beats


def _stem_gate_confidence(evidence) -> float:
    """
    Map asserted stem activity levels into precision-gate confidence.

    Assertable components are already medium+; convert that into scores that
    pass the 0.70/0.75 gates without trusting optimistic model confidence.
    """
    if not evidence.elements:
        return 0.0

    rank_to_confidence = {0: 0.0, 1: 0.45, 2: 0.78, 3: 0.92}
    ranks: List[int] = []
    activity = evidence.activity or {}

    if "drums" in evidence.elements:
        ranks.append(
            max(
                ACTIVITY_RANK.get(activity.get("kick", "none"), 0),
                ACTIVITY_RANK.get(activity.get("hihat", "none"), 0),
            )
        )
    if "vocals" in evidence.elements:
        ranks.append(ACTIVITY_RANK.get(activity.get("vocal", "none"), 0))
    if "bass" in evidence.elements:
        ranks.append(ACTIVITY_RANK.get(activity.get("bass", "none"), 0))
    if any(
        element in evidence.elements
        for element in ("piano", "synth", "strings", "guitar")
    ):
        ranks.append(ACTIVITY_RANK.get(activity.get("instruments", "none"), 0))

    if not ranks:
        return 0.0
    return min(rank_to_confidence.get(rank, 0.0) for rank in ranks)


class StemMixin:
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
            if getattr(self, "client", None) is None:
                print(
                    f"🧬 Local stems only ({len(extracted_stems)}) — "
                    "Grok 4.6 does not accept audio uploads"
                )
                return [], extracted_stems, temp_dir
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

    async def _prepare_vdj_stems_async(
        self, audio_file_path: str
    ) -> Tuple[List[Tuple[str, object]], List[Tuple[str, str]], Optional[str]]:
        """Extract stems locally and upload them with cancellable async requests."""
        vdj_stems_path = self._find_vdj_stems_file(audio_file_path)
        if not vdj_stems_path:
            return [], [], None

        print(f"🧬 Found VDJ stems: {os.path.basename(vdj_stems_path)}")
        temp_dir = tempfile.mkdtemp(prefix="vdj-stems-")
        uploaded_stems = []

        try:
            # Keep subprocess work on the main thread. A terminal interrupt can then
            # stop it immediately instead of waiting for an executor thread to exit.
            extracted_stems = self._extract_vdj_stems(vdj_stems_path, temp_dir)
            for stem_name, stem_path in extracted_stems:
                file_size = os.path.getsize(stem_path) / (1024 * 1024)
                print(f"📤 Uploading {stem_name} stem ({file_size:.1f} MB)...")
                uploaded = await self.upload_file_with_retry(stem_path)
                if uploaded is not None:
                    uploaded_stems.append((stem_name, uploaded))

            if uploaded_stems:
                print(f"✅ Uploaded {len(uploaded_stems)} VDJ stem files")
            return uploaded_stems, extracted_stems, temp_dir
        except BaseException:
            await asyncio.shield(
                self._delete_uploaded_files_async(
                    [uploaded for _, uploaded in uploaded_stems]
                )
            )
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise

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

    def _gemini_loop_seam_available(self) -> bool:
        """True when we can call Gemini for a perceptual wrap listen."""
        return bool(getattr(self, "client", None)) and callable(
            getattr(self, "_generate_json_content", None)
        )

    @staticmethod
    def _loop_retry_placements(
        start: float,
        length_beats: int,
        beat_duration: float,
        *,
        max_attempts: int = LOOP_SEAM_MAX_ATTEMPTS,
    ) -> List[Tuple[float, int]]:
        """Retry shorter lengths on the same yellow [1]. Never ±1 beat.

        Off-1 nudges wrote loops next to the phrase grid, then the writer
        either hard-failed or snapped them to a different 1.
        """
        if beat_duration <= 0:
            return [(float(start), int(length_beats))]

        base_start = float(start)
        base_beats = int(length_beats)
        # offset_beats, optional length override (None = keep base)
        plan: List[Tuple[int, Optional[int]]] = [
            (0, None),
            (0, max(MIN_USEFUL_LOOP_BEATS, base_beats // 2)),
        ]
        placements: List[Tuple[float, int]] = []
        seen: set[Tuple[int, int]] = set()
        for offset_beats, beats_override in plan:
            beats = int(beats_override if beats_override is not None else base_beats)
            if beats not in LOOP_BEAT_CHOICES:
                continue
            candidate_start = base_start + (offset_beats * beat_duration)
            if candidate_start < 0:
                continue
            # Quantize key to ms so float noise does not explode the set.
            key = (int(round(candidate_start * 1000)), beats)
            if key in seen:
                continue
            seen.add(key)
            placements.append((candidate_start, beats))
            if len(placements) >= max(max_attempts * 3, max_attempts):
                break
        return placements or [(base_start, base_beats)]

    def _evaluate_loop_seam_with_gemini(
        self,
        audio_file_path: Optional[str],
        loop_start: float,
        loop_duration: float,
        *,
        loop_name: str = "loop",
        attempt: int = 1,
        max_attempts: int = LOOP_SEAM_MAX_ATTEMPTS,
    ) -> bool:
        """Listen to end→start splice via Gemini (last ~3s + first ~3s).

        Returns True when seamless, or when the check cannot run (no audio /
        client / clip too short) so stem gates stay authoritative.
        Returns False when Gemini hears a clear wrap problem.
        """
        if not audio_file_path or not os.path.isfile(audio_file_path):
            return True
        if not self._gemini_loop_seam_available():
            return True

        half = seam_half_seconds(loop_duration)
        if half < LOOP_SEAM_MIN_HALF_SECONDS:
            return True

        clip_path: Optional[str] = None
        uploaded = None
        try:
            clip_path, half = build_loop_wrap_clip(
                audio_file_path,
                float(loop_start),
                float(loop_duration),
            )
            uploaded = self._upload_audio_file_with_retry(clip_path)
            judgment = self._generate_json_content(
                contents=[loop_seam_prompt(half), uploaded],
                schema=LoopSeamJudgment,
                timeout_seconds=LOOP_SEAM_GEMINI_TIMEOUT_SECONDS,
                max_retries=2,
            )
            seamless = bool(judgment.get("seamless"))
            reason = str(judgment.get("reason") or "").strip()
            attempt_label = f" attempt {attempt}/{max_attempts}"
            if seamless:
                print(
                    f"  🎧 Gemini wrap OK for '{loop_name}'{attempt_label} "
                    f"({half:.1f}s+{half:.1f}s end→start)"
                    + (f": {reason}" if reason else "")
                )
            else:
                print(
                    f"  🧹 Wrap not seamless for '{loop_name}'{attempt_label}"
                    + (f" — {reason}" if reason else "")
                )
            return seamless
        except Exception as error:
            # Infrastructure failure: keep stem-validated loop rather than
            # dropping every candidate when Gemini is briefly unavailable.
            print(
                f"  ⚠️  Gemini wrap check skipped for '{loop_name}': {error}"
            )
            return True
        finally:
            if uploaded is not None:
                self._delete_uploaded_files([uploaded])
            if clip_path and os.path.exists(clip_path):
                try:
                    os.remove(clip_path)
                except OSError:
                    pass

    def _validate_loop_candidate(
        self,
        *,
        profiles: Dict,
        start: float,
        length_beats: int,
        beat_duration: float,
        model_elements: List,
        loop_name: str,
        audio_file_path: Optional[str],
        require_gemini_seam: bool = True,
    ) -> Optional[Dict]:
        """Run stem gates + a Gemini wrap listen on the same phrase [1].

        Shorter lengths on that 1 are allowed. Never nudge off the 1.
        Gemini wrap is advisory: a stem-clean loop is kept.
        """
        if beat_duration <= 0:
            return None

        base_beats = _cap_loop_length_beats(int(length_beats), beat_duration)
        placements = self._loop_retry_placements(
            float(start), base_beats, beat_duration, max_attempts=LOOP_SEAM_MAX_ATTEMPTS
        )
        gemini_attempts = 0
        last_stem_fail = "no placement"

        for candidate_start, candidate_beats in placements:
            if gemini_attempts >= LOOP_SEAM_MAX_ATTEMPTS:
                break
            candidate_beats = _cap_loop_length_beats(candidate_beats, beat_duration)
            duration_seconds = max(4.0, float(candidate_beats) * beat_duration)
            evidence = measure_stem_evidence(
                profiles,
                timestamp=float(candidate_start),
                duration_seconds=duration_seconds,
                model_elements=model_elements,
                centered=True,
                strict_drums=False,
            )
            if not evidence.elements:
                last_stem_fail = "no components"
                continue
            if not loop_is_stable(
                profiles,
                start=float(candidate_start),
                duration_seconds=duration_seconds,
                model_elements=evidence.elements,
            ):
                last_stem_fail = "unstable"
                continue
            if not loop_seam_is_clean(
                profiles,
                start=float(candidate_start),
                duration_seconds=duration_seconds,
                elements=evidence.elements,
            ):
                last_stem_fail = "stem seam"
                continue

            if require_gemini_seam and audio_file_path:
                gemini_attempts += 1
                gemini_ok = bool(
                    self._evaluate_loop_seam_with_gemini(
                        audio_file_path,
                        float(candidate_start),
                        float(duration_seconds),
                        loop_name=loop_name,
                        attempt=gemini_attempts,
                        max_attempts=LOOP_SEAM_MAX_ATTEMPTS,
                    )
                )
                if not gemini_ok:
                    # Stem stability + seam already passed. Do not drop-all
                    # loops just because Gemini is down or disagrees.
                    print(
                        f"  ⚠️  Gemini wrap failed for '{loop_name}' — "
                        "keeping stem-clean loop"
                    )
            else:
                gemini_ok = False

            confidence = _stem_gate_confidence(evidence)
            if confidence < MIN_LOOP_CONFIDENCE:
                last_stem_fail = "low confidence"
                continue

            color = self.validate_color_assignment(list(evidence.elements), "green")
            result = {
                "start": round(float(candidate_start), 6),
                "length_beats": candidate_beats,
                "elements": list(evidence.elements),
                "loop_name": loop_name,
                "color": color,
                "role": "loop",
                "confidence": confidence,
                "stem_activity": evidence.activity,
                "stem_scores": evidence.scores,
                "uncertain_elements": evidence.uncertain_elements,
                "assertion_confidence": evidence.confidence,
                "model_confidence": 0.0,
            }
            if gemini_ok:
                result["gemini_seam"] = True
            if abs(candidate_start - float(start)) > 1e-6 or candidate_beats != base_beats:
                result["seam_retry"] = {
                    "original_start": round(float(start), 6),
                    "original_beats": base_beats,
                    "gemini_attempts": gemini_attempts,
                }
            return result

        if gemini_attempts >= LOOP_SEAM_MAX_ATTEMPTS:
            print(
                f"  🧹 Dropping loop '{loop_name}' after "
                f"{LOOP_SEAM_MAX_ATTEMPTS} wrap attempts ({last_stem_fail})"
            )
        return None

    def _apply_measured_stem_activity(
        self,
        analysis_data: Dict,
        stem_files: List[Tuple[str, str]],
        bpm: Optional[float] = None,
        audio_file_path: Optional[str] = None,
    ) -> Dict:
        """Replace model assertions with calibrated post-boundary stem evidence."""
        if not stem_files:
            # Practice: never write loops without stem-backed stability + seam checks.
            # Cues can still land from the full mix; loops need wrap-around proof.
            dropped = len(analysis_data.get("loop_segments", []))
            if dropped:
                print(
                    f"  🧹 Dropping {dropped} loop(s): no adjacent .vdjstems to "
                    "validate component stability and loop seam continuity"
                )
                analysis_data["loop_segments"] = []
            return analysis_data

        print("🔬 Calibrating VDJ stems for component verification...")
        scope = getattr(self, "write_scope", WRITE_SCOPE_ALL)
        # Partial retries: skip the side we will not rewrite (kept from DB later).
        if scope == WRITE_SCOPE_LOOPS:
            analysis_data["measure_changes"] = []
            print("  🔒 Loops-only: skipping cue stem gates (existing cues kept)")
        if scope == WRITE_SCOPE_CUES:
            analysis_data["loop_segments"] = []
            print("  🔒 Cues-only: skipping loop stem gates (existing loops kept)")

        cache = getattr(self, "_track_audio_cache", None)
        if cache is not None:
            profiles = cache.get_or_load_stem_profiles(list(stem_files))
        else:
            profiles = load_stem_profiles(stem_files)
        actual_bpm = self._actual_bpm(bpm) or 120.0
        beat_duration = 60.0 / actual_bpm
        cue_window = 4.0

        if scope != WRITE_SCOPE_LOOPS:
            from .ml.propose import blend_cue_plans, propose_ml_cues
            from .stem_cue_plan import merge_gemini_onto_stem_cues, plan_stem_cues
            from .stem_evidence import StemProfile

            origin = 0.0
            if audio_file_path:
                origin = float(self.get_beatgrid_offset(audio_file_path) or 0.0)
            song_len = self._loop_discovery_song_length(analysis_data, profiles)
            planned = plan_stem_cues(
                profiles,
                bpm=actual_bpm,
                offset=origin,
                duration=song_len,
            )
            ml_profiles = dict(profiles)
            if audio_file_path:
                try:
                    if cache is not None:
                        ml_profiles["mix"] = cache.get_or_load_mix_profile(
                            audio_file_path
                        )
                    else:
                        ml_profiles["mix"] = StemProfile.decode(audio_file_path)
                except Exception:
                    pass
            ml_planned = propose_ml_cues(
                ml_profiles,
                bpm=actual_bpm,
                offset=origin,
                duration=song_len,
                audio_path=audio_file_path,
            )
            hybrid = blend_cue_plans(ml_planned, planned, bpm=actual_bpm)
            if hybrid:
                named = merge_gemini_onto_stem_cues(
                    hybrid,
                    list(analysis_data.get("measure_changes") or []),
                    bpm=actual_bpm,
                )
                print(
                    f"  🎯 Hybrid cue plan: {len(named)} on the 1 "
                    f"(ML {len(ml_planned)} · stem {len(planned)}) "
                    f"@ {origin:.3f}s"
                )
                analysis_data["measure_changes"] = named
            elif planned:
                named = merge_gemini_onto_stem_cues(
                    planned,
                    list(analysis_data.get("measure_changes") or []),
                    bpm=actual_bpm,
                )
                print(
                    f"  🎯 Stem cue plan: {len(named)} change-point(s) "
                    f"locked to the 1 @ {origin:.3f}s"
                )
                analysis_data["measure_changes"] = named

        kept_cues = []
        for cue_data in analysis_data.get("measure_changes", []):
            timestamp = cue_data.get("timestamp")
            if timestamp is not None:
                evidence = measure_stem_evidence(
                    profiles,
                    timestamp=float(timestamp),
                    duration_seconds=cue_window,
                    model_elements=cue_data.get("elements", []),
                    centered=True,
                    strict_drums=False,
                )
                cue_data["stem_activity"] = evidence.activity
                cue_data["stem_scores"] = evidence.scores
                cue_data["elements"] = evidence.elements
                cue_data["uncertain_elements"] = evidence.uncertain_elements
                cue_data["assertion_confidence"] = evidence.confidence
                cue_data["assertion_source"] = "calibrated_vdj_stems"
                cue_data["model_confidence"] = float(
                    cue_data.get("confidence", 0.0) or 0.0
                )
                # Gate on measured stem activity, not optimistic model scores.
                cue_data["confidence"] = _stem_gate_confidence(evidence)
                cue_data["color"] = self.validate_color_assignment(
                    list(evidence.elements),
                    cue_data.get("color") or "green",
                    evidence.activity,
                )
                if not is_clean_phrase_entry(
                    profiles,
                    timestamp=float(timestamp),
                    elements=evidence.elements,
                ):
                    cue_data["jumpable"] = False
                kept_cues.append(cue_data)
            else:
                kept_cues.append(cue_data)
        analysis_data["measure_changes"] = kept_cues
        if scope != WRITE_SCOPE_LOOPS:
            phrase_cues = self._discover_phrase_entry_cues(
                profiles,
                beat_duration=beat_duration,
                song_length=self._loop_discovery_song_length(
                    analysis_data, profiles
                ),
                existing_cues=kept_cues,
            )
            if phrase_cues:
                print(
                    f"  🔁 Added {len(phrase_cues)} clean phrase-entry cue(s) "
                    "(safe to cue-jump)"
                )
                analysis_data["measure_changes"] = sorted(
                    list(kept_cues) + phrase_cues,
                    key=lambda cue: float(cue.get("timestamp", 0.0)),
                )[:6]

        stable_loops = []
        for loop_data in analysis_data.get("loop_segments", []):
            timestamp = loop_data.get("start")
            if timestamp is None:
                continue
            requested_beats = int(loop_data.get("length_beats", 16) or 16)
            capped_beats = _cap_loop_length_beats(requested_beats, beat_duration)
            if capped_beats < requested_beats:
                print(
                    f"  ✂️  Shortened loop '{loop_data.get('loop_name', 'loop')}' "
                    f"from {requested_beats} to {capped_beats} beats "
                    f"({capped_beats * beat_duration:.1f}s max usable length)"
                )
            accepted = self._validate_loop_candidate(
                profiles=profiles,
                start=float(timestamp),
                length_beats=capped_beats,
                beat_duration=beat_duration,
                model_elements=list(loop_data.get("elements") or []),
                loop_name=str(loop_data.get("loop_name") or "loop"),
                audio_file_path=audio_file_path,
                require_gemini_seam=True,
            )
            if accepted is None:
                continue
            accepted["assertion_source"] = "calibrated_vdj_stems"
            accepted["model_confidence"] = float(
                loop_data.get("confidence", 0.0) or 0.0
            )
            # Preserve model name when still valid; color/elements from stems.
            if loop_data.get("loop_name"):
                accepted["loop_name"] = loop_data["loop_name"]
            stable_loops.append(accepted)

        # Always stem-scan for seamless loops (especially intro melodic 8-counts).
        # Merge with model loops so a good intro is not lost when Gemini
        # returns zero loops or only mid-track candidates.
        if scope != WRITE_SCOPE_CUES:
            discovered = self._discover_stem_validated_loops(
                profiles,
                beat_duration=beat_duration,
                song_length=self._loop_discovery_song_length(
                    analysis_data, profiles
                ),
                transition_times=[
                    float(cue.get("timestamp"))
                    for cue in analysis_data.get("measure_changes", [])
                    if cue.get("timestamp") is not None
                ],
                audio_file_path=audio_file_path,
            )
            if discovered:
                before = len(stable_loops)
                stable_loops = self._merge_loop_candidates(
                    stable_loops, discovered, beat_duration=beat_duration
                )
                added = len(stable_loops) - before
                if before == 0 and stable_loops:
                    print(
                        f"  🔁 Stem scan found {len(stable_loops)} seamless "
                        "loop(s) (model had none that passed gates)"
                    )
                elif added > 0:
                    print(
                        f"  🔁 Stem scan added {added} seamless loop(s) "
                        f"({len(stable_loops)} total after merge)"
                    )

            analysis_data["loop_segments"] = stable_loops[:TARGET_MAX_LOOPS]
            analysis_data = self._ensure_minimum_loops(
                analysis_data,
                profiles=profiles,
                beat_duration=beat_duration,
                song_length=self._loop_discovery_song_length(
                    analysis_data, profiles
                ),
                audio_file_path=audio_file_path,
            )
            kept = len(analysis_data.get("loop_segments") or [])
            if kept < TARGET_MIN_LOOPS:
                print(
                    f"  ⚠️  Only {kept} loop(s) after fill "
                    f"(target {TARGET_MIN_LOOPS}–{TARGET_MAX_LOOPS})"
                )
        else:
            analysis_data["loop_segments"] = []
        return analysis_data

    @staticmethod
    def _merge_loop_candidates(
        primary: List[Dict],
        secondary: List[Dict],
        beat_duration: float,
        max_loops: int = TARGET_MAX_LOOPS,
    ) -> List[Dict]:
        """Combine model + stem-scan loops, de-duping nearby starts."""
        min_spacing = beat_duration * 12.0
        merged: List[Dict] = []

        def consider(item: Dict) -> None:
            start = float(item.get("start", 0.0))
            if any(
                abs(start - float(existing.get("start", 0.0))) < min_spacing
                for existing in merged
            ):
                return
            merged.append(item)

        # Prefer already-validated model loops, then fill gaps from stem scan
        # (scan is sorted to surface intro melodic phrases first).
        for item in primary:
            consider(item)
        for item in secondary:
            if len(merged) >= max_loops:
                break
            consider(item)
        return merged[:max_loops]

    def _ensure_minimum_loops(
        self,
        analysis_data: Dict,
        *,
        profiles: Dict,
        beat_duration: float,
        song_length: float,
        audio_file_path: Optional[str] = None,
    ) -> Dict:
        """If Gemini kept fewer than 2 loops, fill from a wider stem scan."""
        loops = list(analysis_data.get("loop_segments") or [])
        if len(loops) >= TARGET_MIN_LOOPS:
            return analysis_data
        if not profiles or beat_duration <= 0:
            return analysis_data

        print(
            f"  🔁 Only {len(loops)} loop(s) kept — scanning for "
            f"{TARGET_MIN_LOOPS}–{TARGET_MAX_LOOPS}…"
        )
        transition_times = [
            float(cue.get("timestamp"))
            for cue in analysis_data.get("measure_changes", [])
            if cue.get("timestamp") is not None
        ]
        discovered = self._discover_stem_validated_loops(
            profiles,
            beat_duration=beat_duration,
            song_length=song_length,
            transition_times=transition_times,
            max_loops=TARGET_MAX_LOOPS,
            audio_file_path=audio_file_path,
            gemini_check_budget=max(LOOP_SEAM_GEMINI_MAX_CHECKS * 2, 16),
            require_gemini_seam=True,
        )
        merged = self._merge_loop_candidates(
            loops, discovered, beat_duration=beat_duration, max_loops=TARGET_MAX_LOOPS
        )
        if len(merged) < TARGET_MIN_LOOPS:
            stem_only = self._discover_stem_validated_loops(
                profiles,
                beat_duration=beat_duration,
                song_length=song_length,
                transition_times=transition_times,
                max_loops=TARGET_MAX_LOOPS,
                audio_file_path=None,
                require_gemini_seam=False,
            )
            merged = self._merge_loop_candidates(
                merged,
                stem_only,
                beat_duration=beat_duration,
                max_loops=TARGET_MAX_LOOPS,
            )
        if len(merged) > len(loops):
            print(
                f"  🔁 Loop fill now {len(merged)} "
                f"(was {len(loops)}; target {TARGET_MIN_LOOPS}–{TARGET_MAX_LOOPS})"
            )
        analysis_data["loop_segments"] = merged
        return analysis_data

    @staticmethod
    def _loop_discovery_song_length(
        analysis_data: Dict, profiles: Dict
    ) -> float:
        """Best-effort duration for loop discovery from stems, then cues."""
        if profiles:
            any_profile = next(iter(profiles.values()))
            profile_length = len(any_profile.frames) * any_profile.frame_seconds
            if profile_length >= 30.0:
                return profile_length
        cue_times = [
            float(cue.get("timestamp", 0.0))
            for cue in analysis_data.get("measure_changes", [])
            if cue.get("timestamp") is not None
        ]
        if cue_times:
            return max(cue_times) + 45.0
        return 180.0

    def _loop_discovery_label(self, elements: List[str]) -> str:
        """DJ-friendly loop names; melody-only intros become Melodic."""
        if self._is_melody_only(elements):
            return "Melodic"
        if self._is_drum_only(elements):
            return "Drums"
        return self._element_label(elements)

    def _discover_phrase_entry_cues(
        self,
        profiles: Dict,
        beat_duration: float,
        song_length: float,
        existing_cues: List[Dict],
        max_cues: int = 3,
    ) -> List[Dict]:
        """Find vocal phrase attacks that are safe to cue-jump to.

        Used after mid-phrase model cues are rejected so tracks like Need it Bad
        still get a real drop/verse hit without pre-chorus word lead-ins.
        """
        if beat_duration <= 0 or song_length <= 0 or not profiles:
            return []
        if "vocal" not in profiles:
            return []

        phrase_duration = beat_duration * float(PHRASE_BEATS)
        existing_times = sorted(
            float(cue.get("timestamp"))
            for cue in existing_cues
            if cue.get("timestamp") is not None
        )
        min_spacing = beat_duration * float(PHRASE_BEATS)
        candidates: List[Dict] = []
        origin = existing_times[0] if existing_times else 0.0
        start = origin
        while start + phrase_duration < song_length - 2.0:
            if any(abs(start - existing) < min_spacing for existing in existing_times):
                start += phrase_duration
                continue
            evidence = measure_stem_evidence(
                profiles,
                timestamp=start,
                duration_seconds=4.0,
                model_elements=["drums", "vocals", "bass", "synth"],
                centered=True,
                strict_drums=False,
            )
            if "vocals" not in evidence.elements:
                start += phrase_duration
                continue
            if not is_clean_phrase_entry(
                profiles, timestamp=start, elements=evidence.elements
            ):
                start += phrase_duration
                continue
            confidence = _stem_gate_confidence(evidence)
            if confidence < 0.70:
                start += phrase_duration
                continue
            label = self._element_label(list(evidence.elements))
            color = self.validate_color_assignment(evidence.elements, "yellow")
            candidates.append(
                {
                    "timestamp": round(start, 6),
                    "elements": list(evidence.elements),
                    "cue_name": label,
                    "color": color,
                    "role": "vocal" if "vocals" in evidence.elements else "section",
                    "confidence": confidence,
                    "stem_activity": evidence.activity,
                    "stem_scores": evidence.scores,
                    "uncertain_elements": evidence.uncertain_elements,
                    "assertion_confidence": evidence.confidence,
                    "assertion_source": "phrase_entry_scan",
                    "model_confidence": 0.0,
                }
            )
            start += phrase_duration

        # Prefer earlier strong entries (intro/verse/drop) over late continuous choruses.
        candidates.sort(key=lambda cue: float(cue["timestamp"]))
        selected: List[Dict] = []
        for candidate in candidates:
            timestamp = float(candidate["timestamp"])
            if any(
                abs(timestamp - float(item["timestamp"])) < min_spacing
                for item in selected
            ):
                continue
            selected.append(candidate)
            if len(selected) >= max_cues:
                break
        return selected

    def _discover_stem_validated_loops(
        self,
        profiles: Dict,
        beat_duration: float,
        song_length: float,
        transition_times: Optional[List[float]] = None,
        max_loops: int = TARGET_MAX_LOOPS,
        audio_file_path: Optional[str] = None,
        gemini_check_budget: Optional[int] = None,
        require_gemini_seam: bool = True,
    ) -> List[Dict]:
        """Scan bar grid for loops that pass stability + seam gates.

        Always available as a supplement to Gemini. Strongly prefers early
        sparse phrases (intro melodic 8-counts like Sasha Keable - heal
        something) while still finding later drum/vocal loops. Skips mid-bar
        starts and any region that would cross a known cue boundary.

        After stem gates, candidates get Gemini end→start wrap listens.
        Keep scanning until we have TARGET_MIN_LOOPS when possible.
        """
        if beat_duration <= 0 or song_length <= 0 or not profiles:
            return []

        transitions = sorted(
            timestamp
            for timestamp in (transition_times or [])
            if timestamp is not None
        )
        boundary_safety = beat_duration
        intro_horizon = max(beat_duration * 32.0, song_length * 0.12)

        def fits_without_section_change(start: float, duration_seconds: float) -> bool:
            loop_end = start + duration_seconds
            later = [
                timestamp
                for timestamp in transitions
                if timestamp > start + (beat_duration * 0.5)
            ]
            if not later:
                return True
            return loop_end <= min(later) - boundary_safety

        # Phrase [1]s only — never beat 2/3/4 or in-between bar 1s.
        step_duration = beat_duration * float(PHRASE_BEATS)
        candidates: List[Dict] = []
        max_beats = _max_loop_beats_for_tempo(beat_duration)
        origin = float(transitions[0]) if transitions else 0.0
        # Prefer shorter lengths first; skip 32-beat on slow tracks (too long).
        length_order = tuple(b for b in (8, 16, 32) if b <= max_beats)
        for beats in length_order:
            duration_seconds = beats * beat_duration
            if duration_seconds >= song_length - 4.0:
                continue
            start = origin
            while start + duration_seconds < song_length - 2.0:
                if not fits_without_section_change(start, duration_seconds):
                    start += step_duration
                    continue
                evidence = measure_stem_evidence(
                    profiles,
                    timestamp=start + (duration_seconds / 2.0),
                    duration_seconds=min(2.0, duration_seconds / 3.0),
                    model_elements=["drums", "vocals", "bass", "synth"],
                    centered=True,
                    strict_drums=False,
                )
                if not evidence.elements:
                    start += step_duration
                    continue
                if not loop_is_stable(
                    profiles,
                    start=start,
                    duration_seconds=duration_seconds,
                    model_elements=evidence.elements,
                ):
                    start += step_duration
                    continue
                if not loop_seam_is_clean(
                    profiles,
                    start=start,
                    duration_seconds=duration_seconds,
                    elements=evidence.elements,
                ):
                    start += step_duration
                    continue

                confidence = _stem_gate_confidence(evidence)
                if confidence < MIN_LOOP_CONFIDENCE:
                    start += step_duration
                    continue

                label = self._loop_discovery_label(list(evidence.elements))
                color = self.validate_color_assignment(
                    evidence.elements, "green"
                )
                # Prefer downbeats in ranking without requiring them.
                beat_index = int(round(start / beat_duration)) % 4
                candidates.append(
                    {
                        "start": round(start, 6),
                        "length_beats": beats,
                        "elements": list(evidence.elements),
                        "loop_name": f"{label} Loop",
                        "color": color,
                        "role": "loop",
                        "confidence": confidence,
                        "stem_activity": evidence.activity,
                        "stem_scores": evidence.scores,
                        "uncertain_elements": evidence.uncertain_elements,
                        "assertion_confidence": evidence.confidence,
                        "assertion_source": "stem_scan_loop",
                        "model_confidence": 0.0,
                        "on_downbeat": beat_index == 0,
                    }
                )
                start += step_duration

        # Rank: sparse components, early intro, downbeat preferred, 8-beat
        # melodic, then classic 16, then later full arrangements.
        beat_preference = {8: 0, 16: 1, 32: 2}

        def sort_key(item: Dict):
            elements = list(item.get("elements", []))
            start = float(item.get("start", 0.0))
            beats = int(item.get("length_beats", 16))
            melodic_intro = (
                start <= intro_horizon
                and self._is_melody_only(elements)
                and beats <= 16
            )
            return (
                0 if melodic_intro else 1,
                len(elements),
                0 if start <= intro_horizon else 1,
                0 if item.get("on_downbeat") else 1,
                beat_preference.get(beats, 9),
                start,
            )

        candidates.sort(key=sort_key)
        selected: List[Dict] = []
        min_spacing = beat_duration * 12.0
        # Cap how many ranked candidates enter the wrap-retry path (API cost).
        check_budget = (
            LOOP_SEAM_GEMINI_MAX_CHECKS
            if gemini_check_budget is None
            else int(gemini_check_budget)
        )
        candidates_tried = 0
        for candidate in candidates:
            start = float(candidate["start"])
            if any(abs(start - float(item["start"])) < min_spacing for item in selected):
                continue
            if audio_file_path:
                if candidates_tried >= check_budget:
                    break
                candidates_tried += 1
                # Up to 3 wrap attempts (original + beat nudges) per candidate.
                accepted = self._validate_loop_candidate(
                    profiles=profiles,
                    start=start,
                    length_beats=int(candidate["length_beats"]),
                    beat_duration=beat_duration,
                    model_elements=list(candidate.get("elements") or []),
                    loop_name=str(candidate.get("loop_name") or "loop"),
                    audio_file_path=audio_file_path,
                    require_gemini_seam=require_gemini_seam,
                )
                if accepted is None:
                    continue
                accepted["assertion_source"] = "stem_scan_loop"
                if candidate.get("loop_name"):
                    accepted["loop_name"] = candidate["loop_name"]
                selected.append(accepted)
            else:
                selected.append(candidate)
            if len(selected) >= max_loops:
                break
        return selected
