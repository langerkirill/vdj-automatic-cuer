"""AnalysisPostprocessMixin for AutomaticMusicCuer."""

from .common import *
from .stem_evidence import StemProfile, energy_ratio


class AnalysisPostprocessMixin:
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
            if cue_data.get("assertion_source") == "calibrated_vdj_stems":
                elements = self._normalize_elements(cue_data.get("elements", []))
            else:
                elements = self._apply_stem_activity_to_elements(
                    cue_data.get("elements", []), stem_activity
                )
            cue_data["elements"] = elements
            color = cue_data.get("color", "green")
            cue_data["color"] = self.validate_color_assignment(elements, color)

            cue_name = cue_data.get("cue_name", "")
            if self._name_conflicts_with_elements(cue_name, elements):
                cue_name = self._replacement_name(
                    cue_name, elements, is_loop=False
                )
            # Never keep & / &amp; in cue labels (shows as &amp;amp; in VDJ).
            cue_data["cue_name"] = self.sanitize_marker_name(cue_name)

        for loop_data in analysis_data.get("loop_segments", []):
            stem_activity = self._stem_activity_dict(loop_data)
            if loop_data.get("assertion_source") == "calibrated_vdj_stems":
                elements = self._normalize_elements(loop_data.get("elements", []))
            else:
                elements = self._apply_stem_activity_to_elements(
                    loop_data.get("elements", []), stem_activity
                )
            loop_data["elements"] = elements
            color = loop_data.get("color", "green")
            loop_data["color"] = self.validate_color_assignment(elements, color)

            loop_name = loop_data.get("loop_name", "")
            if self._name_conflicts_with_elements(loop_name, elements):
                loop_name = self._replacement_name(
                    loop_name, elements, is_loop=True
                )
            loop_data["loop_name"] = self.sanitize_marker_name(loop_name)

        return analysis_data

    @staticmethod
    def _safe_float(value, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _safe_int(value, default: int = 0) -> int:
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default

    def _postprocess_loop_segments(
        self,
        analysis_data: Dict,
        bpm: Optional[float],
        song_length: Optional[float] = None,
    ) -> Dict:
        """Shorten or remove loops that cross obvious section boundaries."""
        if not bpm or bpm <= 0:
            return analysis_data

        beat_duration = 60.0 / bpm
        boundary_safety = beat_duration
        transition_times = sorted(
            self._safe_float(cue.get("timestamp"))
            for cue in analysis_data.get("measure_changes", [])
            if cue.get("timestamp") is not None
        )
        fixed_loops = []

        for loop_data in analysis_data.get("loop_segments", []):
            start = self._safe_float(loop_data.get("start"))
            requested_beats = self._safe_int(loop_data.get("length_beats"), 16)
            if requested_beats <= 0:
                continue

            max_duration = requested_beats * beat_duration
            loop_end = start + max_duration
            later_transitions = [
                timestamp
                for timestamp in transition_times
                if timestamp > start + (beat_duration * 0.5)
            ]
            next_boundary = min(later_transitions) if later_transitions else None
            if song_length:
                end_boundary = self._safe_float(song_length)
                next_boundary = (
                    min(next_boundary, end_boundary)
                    if next_boundary is not None
                    else end_boundary
                )

            if next_boundary is None or loop_end <= next_boundary - boundary_safety:
                fixed_loops.append(loop_data)
                continue

            available_duration = max(0.0, next_boundary - start - boundary_safety)
            allowed_beats = [
                beats
                for beats in LOOP_BEAT_CHOICES
                if beats <= requested_beats
                and (beats * beat_duration) <= available_duration
            ]
            if not allowed_beats:
                print(
                    f"  🧹 Dropping loop '{loop_data.get('loop_name', 'loop')}' "
                    "because a section change lands too soon inside it"
                )
                continue

            new_beats = max(allowed_beats)
            if new_beats < requested_beats:
                print(
                    f"  ✂️  Shortened loop '{loop_data.get('loop_name', 'loop')}' "
                    f"from {requested_beats} to {new_beats} beats to avoid a "
                    "section change"
                )
                loop_data = dict(loop_data)
                loop_data["length_beats"] = new_beats
            fixed_loops.append(loop_data)

        analysis_data["loop_segments"] = fixed_loops
        return analysis_data

    def _validate_structural_assertions(
        self, analysis_data: Dict, audio_file_path: Optional[str]
    ) -> Dict:
        """Downgrade strong transition names that lack waveform evidence."""
        if not audio_file_path:
            return analysis_data

        try:
            cache = getattr(self, "_track_audio_cache", None)
            if cache is not None:
                mix_profile = cache.get_or_load_mix_profile(audio_file_path)
            else:
                mix_profile = StemProfile.decode(audio_file_path)
        except Exception as exc:
            print(f"⚠️  Could not validate cue energy shapes: {exc}")
            return analysis_data

        for cue_data in analysis_data.get("measure_changes", []):
            timestamp = self._safe_float(cue_data.get("timestamp"))
            ratio = energy_ratio(mix_profile, timestamp)
            cue_data["energy_ratio"] = round(ratio, 4)
            name = str(cue_data.get("cue_name", ""))
            role = str(cue_data.get("role", "")).lower()
            lower_name = name.lower()

            invalid_drop = ("drop" in lower_name or role == "drop") and ratio < 1.12
            invalid_breakdown = (
                "breakdown" in lower_name or role == "breakdown"
            ) and ratio > 0.88
            if not invalid_drop and not invalid_breakdown:
                continue

            replacement = self._element_label(cue_data.get("elements", []))
            if lower_name.startswith("main "):
                replacement = f"Main {replacement}"
            elif lower_name.startswith("final "):
                replacement = f"Final {replacement}"
            cue_data["cue_name"] = replacement
            cue_data["role"] = "section"
            cue_data["assertion_downgraded"] = True
            print(
                f"  🔎 Renamed unsupported transition '{name}' to "
                f"'{replacement}' (energy ratio {ratio:.2f}x)"
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
