"""Dry-run preview helpers for a single audio file."""

from .common import *


class FilePreviewMixin:
    def _preview_single_file_cues(
        self, analysis: Dict, working_bpm: float, audio_file_path: str
    ) -> bool:
        print("🔍 DRY RUN - Would create:")
        for i, cue_data in enumerate(analysis.get("measure_changes", [])[:6], 1):
            gemini_time = cue_data.get("timestamp", 0)
            aligned_time = self.validate_timing_hybrid(
                gemini_time, working_bpm, audio_file_path
            )
            cue_name = cue_data.get("cue_name") or self.create_cue_name(
                cue_data.get("elements", []), cue_data.get("measure", i)
            )
            gemini_color = cue_data.get("color", "green")
            color_name = gemini_color.capitalize()

            print(
                f"  Cue {i}: '{cue_name}' at {aligned_time:.1f}s | "
                f"Color: {color_name} | "
                f"Elements: {cue_data.get('elements', [])}"
            )

        loops = analysis.get("loop_segments", [])
        loops.sort(key=self._single_file_loop_priority)

        selected_loops = []
        loop_count = 0
        used_loop_types = set()
        for loop_data in loops:
            if loop_count >= 3:
                break

            loop_name = loop_data.get("loop_name") or self.create_loop_name(
                loop_data.get("elements", [])
            )
            if not loop_name.endswith("l"):
                loop_name = f"{loop_name}l"

            if loop_name in used_loop_types:
                continue

            loop_count += 1
            used_loop_types.add(loop_name)

            gemini_time = loop_data.get("start", 0)
            aligned_time = self.validate_timing_hybrid(
                gemini_time, working_bpm, audio_file_path
            )
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

        selected_loops.sort(key=lambda x: x["time"])
        for i, loop_info in enumerate(selected_loops, 1):
            print(
                f"  Loop {i}: '{loop_info['name']}' at "
                f"{loop_info['time']:.1f}s ({loop_info['beats']} beats) | "
                f"Color: {loop_info['color']} | "
                f"Elements: {loop_info['elements']}"
            )

        used_colors = {
            cue_data.get("color", "green")
            for cue_data in analysis.get("measure_changes", [])[:6]
        }
        for loop_info in selected_loops:
            for loop_data in loops:
                loop_name = loop_data.get("loop_name") or self.create_loop_name(
                    loop_data.get("elements", [])
                )
                if not loop_name.endswith("l"):
                    loop_name = f"{loop_name}l"
                if loop_name == loop_info["name"]:
                    used_colors.add(loop_data.get("color", "green"))
                    break

        full_comment = " ".join(sorted(used_colors))
        print(f"\n  Comment: '{full_comment}'")
        return True

    @staticmethod
    def _single_file_loop_priority(loop_data: Dict) -> int:
        elements = loop_data.get("elements", [])
        element_count = len(elements)
        has_drums = any(elem in elements for elem in ["drums", "percussion"])
        has_vocals = "vocals" in elements
        has_melody = any(
            elem in elements for elem in ["piano", "synth", "strings", "guitar"]
        )

        if has_drums and not has_vocals and element_count <= 2:
            return 0
        if has_vocals:
            return 1
        if has_melody and not has_drums and not has_vocals:
            return 2
        if element_count <= 2:
            return 3
        return 4
