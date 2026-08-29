"""BeatgridSourceMixin for AutomaticMusicCuer."""

from .common import *


class BeatgridSourceMixin:
    def get_beatgrid_offset(self, file_path: str) -> float:
        """Get beatgrid offset (where '1' beat starts) from VDJ database.

        Honor this FilePath's Scan Phase / beatgrid. Never borrow a sibling
        library 1 — a user-set first-beat is the source of truth. AutoCue
        does not rewrite Scan.
        """
        try:
            metadata = self._get_song_metadata(file_path)
            if metadata is None:
                return 0.0
            return float(metadata.beatgrid_offset or 0.0)
        except Exception as e:
            print(f"⚠️  Could not get beatgrid offset: {e}")
            return 0.0

    @staticmethod
    def _is_staging_library_path(file_path: str) -> bool:
        lowered = file_path.replace("\\", "/").lower()
        return "/add cues/" in lowered or "/sets/" in lowered

    @staticmethod
    def _is_borrowable_library_path(file_path: str) -> bool:
        lowered = file_path.replace("\\", "/").lower()
        if "/add cues/" in lowered or "/sets/" in lowered:
            return False
        if "/downloads/" in lowered:
            return False
        return True

    def _sibling_library_beatgrid_offset(self, file_path: str, metadata) -> Optional[float]:
        """Disabled: never restore a sibling or previous first-beat."""
        return None
        """Use a library sibling's first-1 when this Add Cues/Sets grid is off."""
        if not self._is_staging_library_path(file_path):
            return None
        current = float(metadata.beatgrid_offset or 0.0)
        beat = float(metadata.scan_bpm or 0.0)
        if not 0.25 <= beat <= 2.0:
            beat = 0.5
        index = self._get_metadata_index()
        self_path = self._normalize_database_path(file_path)
        best = None
        for other_path, other in index.items():
            if other_path == self_path:
                continue
            if not self._is_borrowable_library_path(other_path):
                continue
            same_sig = bool(metadata.audio_sig) and metadata.audio_sig == getattr(
                other, "audio_sig", None
            )
            same_size = (
                not metadata.audio_sig
                and metadata.file_size
                and metadata.file_size == getattr(other, "file_size", None)
                and Path(other_path).name.lower() == Path(self_path).name.lower()
            )
            if not (same_sig or same_size):
                continue
            other_offset = float(other.beatgrid_offset or 0.0)
            if abs(other_offset - current) <= beat + 1e-6:
                continue
            # Prefer the first matching library copy; they share AudioSig.
            if best is None or abs(other_offset - current) > abs(best - current):
                best = other_offset
                best_path = other_path
        if best is None:
            return None
        print(
            f"🎯 Using sibling library 1 at {best:.3f}s "
            f"(this file {current:.3f}s is >1 beat off; {best_path})"
        )
        return best

    @staticmethod
    def _actual_bpm(bpm: float) -> Optional[float]:
        """Normalize VDJ fractional BPM or direct BPM into actual BPM."""
        if bpm <= 0:
            return None
        actual_bpm = 60.0 / bpm if bpm < 5 else bpm
        if actual_bpm < 50 or actual_bpm > 200:
            return None
        return actual_bpm

    @staticmethod
    def _choose_best_downbeat_phase(
        current_offset: float, beat_duration: float, phase_scores: Dict[int, float]
    ) -> BeatgridAlignment:
        """Pick a stronger whole-beat downbeat phase when confidence is high.

        If the current VDJ 1 already matches stem downbeats, keep it — that is
        how we honor a manual alignment.
        """
        if existing_downbeat_is_trusted(phase_scores):
            current_score = phase_scores.get(0, 0.0)
            best_score = max(phase_scores.values()) if phase_scores else 0.0
            return BeatgridAlignment(
                offset=current_offset,
                confidence_ratio=best_score / max(current_score, 0.001),
                phase_scores=phase_scores,
            )
        current_score = phase_scores.get(0, 0.0)
        best_phase = max(phase_scores, key=phase_scores.get) if phase_scores else 0
        best_score = phase_scores.get(best_phase, 0.0)
        confidence_ratio = best_score / max(current_score, 0.001)
        ordered = sorted(phase_scores.values(), reverse=True)
        second_best = ordered[1] if len(ordered) > 1 else 0.0
        near_tie = best_score <= max(second_best * BEATGRID_PHASE_NEAR_TIE_RATIO, second_best + 0.01)

        if best_score < 0.02:
            return BeatgridAlignment(
                offset=current_offset,
                confidence_ratio=confidence_ratio,
                phase_scores=phase_scores,
            )

        # Near-ties among *strong* alternatives: keep current only when the
        # current phase is itself competitive. Vortex Number 9: phase 0 is ~0
        # while +1 and +3 are both strong — still correct to the best phase.
        if near_tie and best_phase != 0:
            current_is_competitive = (
                current_score >= second_best * BEATGRID_PHASE_NEAR_TIE_RATIO
                or current_score >= best_score * 0.5
            )
            if current_is_competitive:
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

        shift_beats = 0 if best_phase == 0 else best_phase
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
    def _source_phase_is_usable(phase_scores: Dict[int, float]) -> bool:
        """Accept a stem/mix vote when absolute or relative evidence is clear."""
        if not phase_scores:
            return False
        ordered = sorted(phase_scores.values(), reverse=True)
        best = ordered[0]
        second = ordered[1] if len(ordered) > 1 else 0.0
        if best >= BEATGRID_PHASE_SOURCE_MIN_SCORE:
            return True
        return (
            best >= BEATGRID_PHASE_SOURCE_RELATIVE_MIN_SCORE
            and best >= second * BEATGRID_PHASE_SOURCE_RELATIVE_RATIO
        )

    @staticmethod
    def _choose_consensus_downbeat_phase(
        current_offset: float,
        beat_duration: float,
        source_phase_scores: List[Tuple[str, Dict[int, float]]],
    ) -> BeatgridAlignment:
        """Use multi-source agreement to correct a weak or ambiguous downbeat phase."""
        aggregate_scores = {phase: 0.0 for phase in range(4)}
        best_phase_counts = {phase: 0 for phase in range(4)}
        used_sources = 0
        used_names: List[str] = []

        for source_name, phase_scores in source_phase_scores:
            if not phase_scores or not BeatgridSourceMixin._source_phase_is_usable(
                phase_scores
            ):
                continue
            best_phase = max(phase_scores, key=phase_scores.get)
            used_sources += 1
            used_names.append(source_name)
            best_phase_counts[best_phase] += 1
            # Normalize per source so a loud kick cannot drown quieter stems.
            total = sum(phase_scores.values()) or 1.0
            for phase, score in phase_scores.items():
                aggregate_scores[phase] += score / total

        if used_sources < BEATGRID_PHASE_CONSENSUS_MIN_SOURCES:
            return BeatgridAlignment(
                offset=current_offset,
                phase_scores=aggregate_scores,
                source="multi-source consensus",
            )

        # Prefer majority vote; break ties with normalized aggregate energy.
        top_vote = max(best_phase_counts.values())
        vote_leaders = [
            phase for phase, count in best_phase_counts.items() if count == top_vote
        ]
        if len(vote_leaders) == 1:
            top_phase = vote_leaders[0]
        else:
            top_phase = max(vote_leaders, key=lambda phase: aggregate_scores[phase])

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
            and (top_score - current_score) < BEATGRID_PHASE_CONSENSUS_MIN_GAIN
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
        self, audio_file_path: str, *, mix_only: bool = False
    ) -> List[Tuple[str, str, Optional[str]]]:
        """Return candidate audio sources for beatgrid verification."""
        if mix_only or getattr(self, "_beatgrid_mix_only", False):
            return [("mix", audio_file_path, None)]

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
        cache = getattr(self, "_track_audio_cache", None)
        cache_key = (audio_file_path, stream_map)

        def load() -> Tuple[List[float], float]:
            try:
                return self._decode_onset_envelope(audio_file_path, stream_map)
            except StemDecodeError:
                raise
            except Exception as exc:
                if stream_map and is_stem_decode_error(exc):
                    raise StemDecodeError(
                        f"ffmpeg stem decode failed ({stream_map} in "
                        f"{audio_file_path}): {exc}"
                    ) from exc
                raise

        if cache is not None:
            return cache.get_or_load_onset(cache_key, load)
        return load()

    def _decode_onset_envelope(
        self, audio_file_path: str, stream_map: Optional[str]
    ) -> Tuple[List[float], float]:
        """Decode onset envelope once (used through the track audio cache)."""
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
            "-nostdin",
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

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                check=True,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        except (BrokenPipeError, OSError, subprocess.CalledProcessError) as exc:
            if stream_map:
                self._beatgrid_mix_only = True
                print(
                    f"⚠️  Stem decode failed ({stream_map}); "
                    "dropping stem map, will retry mix only"
                )
                raise StemDecodeError(
                    f"ffmpeg stem decode failed ({stream_map} in "
                    f"{audio_file_path}): {exc}"
                ) from exc
            raise
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


def run_with_mix_only_stem_failover(cuer, work):
    """Run work(); on stem EPIPE / ffmpeg decode failure, retry mix-only.

    Decode may already have dropped the stem map (``_beatgrid_mix_only``)
    before raising. Still retry the job once so AutoCue can finish on mix.
    """
    try:
        return work()
    except Exception as exc:
        if not is_stem_decode_error(exc):
            raise
        if getattr(cuer, "_stem_mix_only_retry_done", False):
            raise
        print(
            "⚠️  Stem decode failed; retrying AutoCue without stems (mix only)"
        )
        cuer._beatgrid_mix_only = True
        cuer._stem_mix_only_retry_done = True
        cache = getattr(cuer, "_beatgrid_alignment_cache", None)
        if cache is not None:
            cache.clear()
        return work()
