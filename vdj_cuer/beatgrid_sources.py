"""BeatgridSourceMixin for AutomaticMusicCuer."""

from .common import *


class BeatgridSourceMixin:
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
