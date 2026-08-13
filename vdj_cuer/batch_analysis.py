"""BatchAnalysisMixin for AutomaticMusicCuer."""

from .common import *
from .precision_gate import apply_precision_gate


class BatchAnalysisMixin:
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

            For EACH file, find 4-6 high-confidence musical changes where elements
            ACTUALLY change. Return fewer when the audio does not support more:
            - Real intro (before main elements start)
            - When drums ACTUALLY enter (not just percussion)
            - When vocals ACTUALLY start singing (not just vocal sounds)
            - Breakdown sections (where elements drop out)
            - Drops/build-ups (energy changes)

            For EACH file, find 2-3 loop sections for DJing (8, 16, or 32 beats).
            At least 2 loops are required on any track longer than 45 seconds
            (early repeating phrase + later groove/drop). A third is preferred.
            Prefer 16-beat loops; use 8 for tight repeating phrases, especially
            instrument-only intros in the first bars (melodic intro loops).
            Prefer loop starts on beat 1, but other beats are OK if the wrap is
            cleaner. If a 16-beat wrap is messy, try 8 beats.
            A loop must wrap cleanly: level and texture at the end must match the
            start. Do not invent a vocal/drum-only label — pick real phrases.
            Candidate types are:
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
            - Use "Drop" only when the timestamp marks an actual energy rise or
              fuller section entering. If the point is musically useful but the
              energy is already steady, rename it to "Rhythm Section", "Groove",
              or "Vocal Mix"; if the true drop is elsewhere, move the cue.
            - Use "Breakdown" only when elements clearly drop out at that exact
              timestamp. If the label is wrong but the cue is useful, rename it;
              if the actual breakdown is elsewhere, move the cue.
            - If you are uncertain whether other elements are present, include those
              elements and avoid "drums-only" or "melodic-only" names/colors.

            Color Rules (be strict):
            - blue: Only melody, NO drums, NO vocals
            - green: Melody + drums, NO vocals
            - yellow: Drums + vocals, with or without melody
            - purple: Only drums/percussion
            - orange: Vocals with NO drums, with or without melody

            RESPONSE FORMAT REQUIREMENTS:
            - All timestamps must be rounded to 2 decimal places (e.g., 45.67)
            - Each cue must have: timestamp, elements (array), cue_name (string),
              color (string), role (string), confidence (0.0-1.0)
            - Each loop must have: start, length_beats, elements (array),
              loop_name (string), color (string), role (string),
              confidence (0.0-1.0)
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

                validated_analyses = []
                for index, analysis_data in enumerate(analyses_list):
                    if index >= len(uploaded_files):
                        break
                    audio_file_path = uploaded_files[index][0]
                    bpm = self.get_song_bpm_from_database(audio_file_path)
                    actual_bpm = self._actual_bpm(bpm)
                    beatgrid_offset = 0.0
                    if actual_bpm:
                        beatgrid_offset = self._get_verified_beatgrid_offset(
                            audio_file_path, bpm
                        )
                    analysis_data = self._align_analysis_candidates(
                        analysis_data, actual_bpm, beatgrid_offset
                    )
                    analysis_data = self._normalize_analysis_data(analysis_data)
                    analysis_data = self._validate_structural_assertions(
                        analysis_data, audio_file_path
                    )
                    validated_analyses.append(
                        apply_precision_gate(
                            analysis_data, actual_bpm, beatgrid_offset
                        )
                    )
                analyses_list = validated_analyses

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
