"""BeatgridAlignmentMixin for AutomaticMusicCuer."""

from .common import *


class BeatgridAlignmentMixin:
    @staticmethod
    def _quantize_grid_time(
        timestamp: float,
        actual_bpm: float,
        beatgrid_offset: float,
        grid_beats: int,
    ) -> float:
        """Return the nearest nonnegative point on a beat or bar grid."""
        beat_duration = 60.0 / actual_bpm
        grid_beats = max(1, int(grid_beats))
        grid_duration = beat_duration * grid_beats
        grid_steps = (timestamp - beatgrid_offset) / grid_duration
        nearest_step = math.floor(grid_steps + 0.5)
        first_nonnegative_step = math.ceil(-beatgrid_offset / grid_duration)
        nearest_step = max(nearest_step, first_nonnegative_step)
        return beatgrid_offset + (nearest_step * grid_duration)

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
        self, audio_file_path: str, bpm: float, *, mix_only: bool = False
    ) -> BeatgridAlignment:
        """Validate VDJ beatgrid downbeat phase against audio transients."""
        actual_bpm = self._actual_bpm(bpm)
        current_offset = self.get_beatgrid_offset(audio_file_path)
        if actual_bpm is None:
            return BeatgridAlignment(offset=current_offset)

        if mix_only:
            self._beatgrid_mix_only = True

        cache_key = (audio_file_path, round(actual_bpm, 3))
        if cache_key in self._beatgrid_alignment_cache:
            return self._beatgrid_alignment_cache[cache_key]

        beat_duration = 60.0 / actual_bpm
        measure_duration = beat_duration * 4
        stems_skipped = bool(getattr(self, "_beatgrid_mix_only", False))

        try:
            audio_sources = self._beatgrid_audio_sources(audio_file_path)
            source_path, stream_map, source_name = self._beatgrid_audio_source(
                audio_file_path
            )
            try:
                onsets, hop_seconds = self._extract_onset_envelope(
                    source_path, stream_map
                )
            except StemDecodeError as stem_exc:
                print(
                    f"⚠️  Skipping VDJ stems ({source_name} decode failed: "
                    f"{stem_exc}); using mix only"
                )
                self._beatgrid_mix_only = True
                stems_skipped = True
                audio_sources = [("mix", audio_file_path, None)]
                source_path, stream_map, source_name = audio_file_path, None, "mix"
                onsets, hop_seconds = self._extract_onset_envelope(
                    source_path, None
                )
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

            # Always try multi-source consensus when the primary stem/mix did not
            # confidently correct the grid. Primary kick can look "strong" while
            # still being phase-ambiguous (near-tie) or simply wrong for a track.
            if not alignment.corrected and len(audio_sources) >= 2:
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
                        try:
                            candidate_onsets, candidate_hop_seconds = (
                                self._extract_onset_envelope(
                                    candidate_path, candidate_stream
                                )
                            )
                        except StemDecodeError as stem_exc:
                            print(
                                f"⚠️  Skipping {candidate_name} "
                                f"(stem decode failed: {stem_exc})"
                            )
                            self._beatgrid_mix_only = True
                            stems_skipped = True
                            continue

                    # Score from the fine-aligned base when available so phase
                    # candidates share the same sub-beat origin.
                    phase_base = base_offset
                    candidate_scores = {
                        phase: self._score_downbeat_phase(
                            candidate_onsets,
                            candidate_hop_seconds,
                            phase_base + (phase * beat_duration),
                            measure_duration,
                        )
                        for phase in range(4)
                    }
                    source_phase_scores.append((candidate_name, candidate_scores))

                consensus_alignment = self._choose_consensus_downbeat_phase(
                    base_offset, beat_duration, source_phase_scores
                )
                if consensus_alignment.corrected:
                    alignment = consensus_alignment
                    alignment.fine_shift_seconds = fine_alignment.fine_shift_seconds
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
            alignment.stems_skipped = stems_skipped
        except Exception as e:
            print(f"⚠️  Beatgrid verification failed; using VDJ grid: {e}")
            alignment = BeatgridAlignment(
                offset=current_offset, stems_skipped=stems_skipped
            )

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
        self,
        gemini_timestamp: float,
        bpm: float,
        file_path: str,
        grid_beats: int = 4,
    ) -> float:
        """Quantize a model timestamp to a verified downbeat or beat grid."""
        # Get beatgrid info
        # Snap to the grid already in VirtualDJ. Alignment is a separate UI action.
        beatgrid_offset = float(self.get_beatgrid_offset(file_path) or 0.0)

        actual_bpm = self._actual_bpm(bpm)
        if actual_bpm is None:
            print(f"🚫 Invalid BPM {bpm} — cannot snap to the 1")
            return None

        grid_beats = max(1, int(grid_beats))
        aligned_time = self._quantize_grid_time(
            gemini_timestamp,
            actual_bpm,
            beatgrid_offset,
            grid_beats,
        )
        distance = abs(gemini_timestamp - aligned_time)
        target = "downbeat" if grid_beats == 4 else "beat"
        print(
            f"🎯 Aligned to {target}: {gemini_timestamp:.1f}s → "
            f"{aligned_time:.1f}s (distance: {distance:.1f}s)"
        )
        return aligned_time

    def get_song_length(self, file_path: str) -> Optional[float]:
        """Get song length from VDJ database"""
        try:
            metadata = self._get_song_metadata(file_path)
            return metadata.song_length if metadata is not None else None
        except Exception as e:
            print(f"⚠️  Could not get song length: {e}")
            return None
