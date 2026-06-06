"""CueWriterMixin for AutomaticMusicCuer."""

from .common import *


class CueWriterMixin:
    def _apply_cues_to_database(
        self, audio_file_path: str, analysis_data: Dict, dry_run: bool = False
    ) -> bool:
        """Apply analysis results to VDJ database for a single song"""
        try:
            print(f"\n🎶 Applying cues: {os.path.basename(audio_file_path)}")

            if dry_run:
                print("🔍 DRY RUN - Would create:")
                # Show what would be created
                cues = analysis_data.get("measure_changes", [])
                loops = analysis_data.get("loop_segments", [])
                working_bpm = self.get_song_bpm_from_database(audio_file_path) or 120
                song_length = self.get_song_length(audio_file_path)
                analysis_data = self._postprocess_loop_segments(
                    analysis_data, working_bpm, song_length
                )
                cues = analysis_data.get("measure_changes", [])
                loops = analysis_data.get("loop_segments", [])
                alignment = self._verify_beatgrid_alignment(
                    audio_file_path, working_bpm
                )
                if alignment.corrected:
                    print(
                        f"  Would update beatgrid '1': "
                        f"{self.get_beatgrid_offset(audio_file_path):.6f}s → "
                        f"{alignment.offset:.6f}s"
                    )

                for i, cue_data in enumerate(cues[:6], 1):
                    cue_name = cue_data.get("cue_name", f"cue{i}")
                    timestamp = cue_data.get("timestamp", 0)
                    aligned_time = self.validate_timing_hybrid(
                        timestamp, working_bpm, audio_file_path
                    )
                    color = cue_data.get("color", "green")
                    elements = cue_data.get("elements", [])
                    print(
                        f"  Cue {i}: '{cue_name}' at {aligned_time:.1f}s | "
                        f"Color: {color.capitalize()} | Elements: {elements}"
                    )

                for i, loop_data in enumerate(loops[:3], 1):
                    loop_name = loop_data.get("loop_name", f"loop{i}l")
                    start = loop_data.get("start", 0)
                    aligned_start = self.validate_timing_hybrid(
                        start, working_bpm, audio_file_path
                    )
                    beats = loop_data.get("length_beats", 16)
                    color = loop_data.get("color", "green")
                    elements = loop_data.get("elements", [])
                    print(
                        f"  Loop {i}: '{loop_name}' at {aligned_start:.1f}s "
                        f"({beats} beats) | Color: {color.capitalize()} | "
                        f"Elements: {elements}"
                    )

                return True

            # Load and modify VDJ database
            root = self.parse_vdj_database()
            if root is None:
                print("❌ Could not parse VDJ database for modification")
                return False

            # Find the song in database
            song_element = None
            import unicodedata

            normalized_target = unicodedata.normalize("NFC", audio_file_path)

            for song in root.findall("Song"):
                db_path = song.get("FilePath", "")
                normalized_db_path = unicodedata.normalize("NFC", db_path)

                if normalized_db_path == normalized_target:
                    song_element = song
                    break

            if song_element is None:
                print(f"❌ Song not found in VDJ database: {audio_file_path}")
                return False

            # Remove existing manual cues and loops
            pois_to_remove = []
            for poi in song_element.findall("Poi"):
                if poi.get("Type") in ["cue", "loop"] and poi.get("Num", "0") != "0":
                    pois_to_remove.append(poi)

            for poi in pois_to_remove:
                song_element.remove(poi)

            # Get song info for validation
            song_length = self.get_song_length(audio_file_path)
            database_bpm = self.get_song_bpm_from_database(audio_file_path)
            working_bpm = database_bpm or 120
            self._apply_verified_beatgrid_to_song(
                song_element, audio_file_path, working_bpm
            )
            analysis_data = self._postprocess_loop_segments(
                analysis_data, working_bpm, song_length
            )

            # Process cues
            all_pois = []
            cue_count = 0

            for cue_data in analysis_data.get("measure_changes", [])[:6]:
                # Validate timestamp
                gemini_time = cue_data.get("timestamp", 0)
                aligned_time = self.validate_timing_hybrid(
                    gemini_time, working_bpm, audio_file_path
                )

                # Skip cues beyond song length
                if song_length and aligned_time >= song_length:
                    continue

                cue_count += 1
                elements = cue_data.get("elements", [])
                if not elements:
                    continue

                # Validate color assignment
                gemini_color = cue_data.get("color", "green")
                validated_color = self.validate_color_assignment(elements, gemini_color)
                color = self.color_mappings.get(
                    validated_color, self.color_mappings["green"]
                )

                # Get cue name
                cue_name = cue_data.get("cue_name") or self.create_cue_name(
                    elements, cue_count
                )
                cue_name = self.sanitize_xml_content(cue_name)

                # Create cue POI
                cue_poi = ET.Element("Poi")
                cue_poi.set("Name", cue_name)
                cue_poi.set("Pos", f"{aligned_time:.6f}")
                cue_poi.set("Num", str(cue_count))
                cue_poi.set("Color", color)
                cue_poi.set("Type", "cue")

                all_pois.append((aligned_time, cue_poi))

            # Process loops
            loop_count = 0
            used_loop_types = set()

            loops = analysis_data.get("loop_segments", [])

            # Sort loops by priority (drum-only first, then vocal, then melodic)
            def loop_priority(loop_data):
                elements = loop_data.get("elements", [])
                has_drums = any(elem in elements for elem in ["drums", "percussion"])
                has_vocals = "vocals" in elements
                has_melody = any(
                    elem in elements for elem in ["piano", "synth", "strings", "guitar"]
                )

                if has_drums and not has_vocals and len(elements) <= 2:
                    return 0  # Drum-only (highest priority)
                elif has_vocals:
                    return 1  # Vocal sections
                elif has_melody and not has_drums and not has_vocals:
                    return 2  # Melodic-only
                else:
                    return 3  # Other

            loops.sort(key=loop_priority)

            for loop_data in loops[:3]:  # Max 3 loops
                # Validate timestamp
                gemini_time = loop_data.get("start", 0)
                aligned_time = self.validate_timing_hybrid(
                    gemini_time, working_bpm, audio_file_path
                )

                # Skip loops too close to song end
                if song_length and aligned_time >= (song_length - 10):
                    continue

                elements = loop_data.get("elements", [])
                if not elements:
                    continue

                # Get loop name
                loop_name = loop_data.get("loop_name") or self.create_loop_name(
                    elements
                )
                if not loop_name.endswith("l"):
                    loop_name = f"{loop_name}l"
                loop_name = self.sanitize_xml_content(loop_name)

                # Skip duplicate loop types
                if loop_name in used_loop_types:
                    continue

                loop_count += 1
                used_loop_types.add(loop_name)

                # Validate color assignment
                gemini_color = loop_data.get("color", "green")
                validated_color = self.validate_color_assignment(elements, gemini_color)
                color = self.color_mappings.get(
                    validated_color, self.color_mappings["green"]
                )

                # Create loop POI
                loop_poi = ET.Element("Poi")
                loop_poi.set("Name", loop_name)
                loop_poi.set("Pos", f"{aligned_time:.6f}")
                loop_poi.set("Num", "-1")
                loop_poi.set("Color", color)
                loop_poi.set("Type", "loop")
                loop_poi.set("Size", str(float(loop_data.get("length_beats", 16))))
                loop_poi.set("Slot", str(loop_count))

                all_pois.append((aligned_time, loop_poi))

            # Sort all POIs by timestamp and add to song
            all_pois.sort(key=lambda x: x[0])

            # Reassign loop slots in chronological order
            loop_slot_counter = 1
            for _, poi_element in all_pois:
                if poi_element.get("Type") == "loop":
                    poi_element.set("Slot", str(loop_slot_counter))
                    loop_slot_counter += 1
                song_element.append(poi_element)

            # Add comment with colors
            existing_comment = song_element.find("Comment")
            if existing_comment is not None:
                song_element.remove(existing_comment)

            # Generate comment from used colors
            used_colors = set()
            for _, poi_element in all_pois:
                color_value = poi_element.get("Color")
                for color_name, value in self.color_mappings.items():
                    if value == color_value:
                        used_colors.add(color_name)
                        break

            full_comment = " ".join(sorted(used_colors))
            full_comment = self.sanitize_xml_content(full_comment)
            comment_element = ET.Element("Comment")
            comment_element.text = full_comment
            song_element.append(comment_element)

            print(
                f"✅ Applied {cue_count} cues and {loop_count} loops to "
                f"{os.path.basename(audio_file_path)}"
            )
            return True

        except Exception as e:
            print(f"❌ Error applying cues to {audio_file_path}: {e}")
            import traceback

            traceback.print_exc()
            return False

    def _apply_cues_to_batch_database(
        self, root, audio_file_path: str, analysis_data: Dict
    ) -> bool:
        """Apply analysis results to XML tree for batch processing"""
        try:
            print(f"🎶 Applying cues: {os.path.basename(audio_file_path)}")

            # Find the song in database
            song_element = None
            import unicodedata

            normalized_target = unicodedata.normalize("NFC", audio_file_path)

            for song in root.findall("Song"):
                db_path = song.get("FilePath", "")
                normalized_db_path = unicodedata.normalize("NFC", db_path)

                if normalized_db_path == normalized_target:
                    song_element = song
                    break

            if song_element is None:
                print(f"❌ Song not found in VDJ database: {audio_file_path}")
                return False

            # Remove existing manual cues and loops
            pois_to_remove = []
            for poi in song_element.findall("Poi"):
                if poi.get("Type") in ["cue", "loop"] and poi.get("Num", "0") != "0":
                    pois_to_remove.append(poi)

            for poi in pois_to_remove:
                song_element.remove(poi)

            # Get song info for validation
            song_length = self.get_song_length(audio_file_path)
            database_bpm = self.get_song_bpm_from_database(audio_file_path)
            working_bpm = database_bpm or 120
            self._apply_verified_beatgrid_to_song(
                song_element, audio_file_path, working_bpm
            )
            analysis_data = self._postprocess_loop_segments(
                analysis_data, working_bpm, song_length
            )

            # Process cues
            all_pois = []
            cue_count = 0

            for cue_data in analysis_data.get("measure_changes", [])[:6]:
                # Validate timestamp
                gemini_time = cue_data.get("timestamp", 0)
                aligned_time = self.validate_timing_hybrid(
                    gemini_time, working_bpm, audio_file_path
                )

                # Skip cues beyond song length
                if song_length and aligned_time >= song_length:
                    continue

                cue_count += 1
                elements = cue_data.get("elements", [])
                if not elements:
                    continue

                # Validate color assignment
                gemini_color = cue_data.get("color", "green")
                validated_color = self.validate_color_assignment(elements, gemini_color)
                color = self.color_mappings.get(
                    validated_color, self.color_mappings["green"]
                )

                # Get cue name
                cue_name = cue_data.get("cue_name") or self.create_cue_name(
                    elements, cue_count
                )
                cue_name = self.sanitize_xml_content(cue_name)

                # Create cue POI
                cue_poi = ET.Element("Poi")
                cue_poi.set("Name", cue_name)
                cue_poi.set("Pos", f"{aligned_time:.6f}")
                cue_poi.set("Num", str(cue_count))
                cue_poi.set("Color", color)
                cue_poi.set("Type", "cue")

                all_pois.append((aligned_time, cue_poi))

            # Process loops
            loop_count = 0
            used_loop_types = set()

            loops = analysis_data.get("loop_segments", [])

            # Sort loops by priority (drum-only first, then vocal, then melodic)
            def loop_priority(loop_data):
                elements = loop_data.get("elements", [])
                has_drums = any(elem in elements for elem in ["drums", "percussion"])
                has_vocals = "vocals" in elements
                has_melody = any(
                    elem in elements for elem in ["piano", "synth", "strings", "guitar"]
                )

                if has_drums and not has_vocals and len(elements) <= 2:
                    return 0  # Drum-only (highest priority)
                elif has_vocals:
                    return 1  # Vocal sections
                elif has_melody and not has_drums and not has_vocals:
                    return 2  # Melodic-only
                else:
                    return 3  # Other

            loops.sort(key=loop_priority)

            for loop_data in loops[:3]:  # Max 3 loops
                # Validate timestamp
                gemini_time = loop_data.get("start", 0)
                aligned_time = self.validate_timing_hybrid(
                    gemini_time, working_bpm, audio_file_path
                )

                # Skip loops too close to song end
                if song_length and aligned_time >= (song_length - 10):
                    continue

                elements = loop_data.get("elements", [])
                if not elements:
                    continue

                # Get loop name
                loop_name = loop_data.get("loop_name") or self.create_loop_name(
                    elements
                )
                if not loop_name.endswith("l"):
                    loop_name = f"{loop_name}l"
                loop_name = self.sanitize_xml_content(loop_name)

                # Skip duplicate loop types
                if loop_name in used_loop_types:
                    continue

                loop_count += 1
                used_loop_types.add(loop_name)

                # Validate color assignment
                gemini_color = loop_data.get("color", "green")
                validated_color = self.validate_color_assignment(elements, gemini_color)
                color = self.color_mappings.get(
                    validated_color, self.color_mappings["green"]
                )

                # Create loop POI
                loop_poi = ET.Element("Poi")
                loop_poi.set("Name", loop_name)
                loop_poi.set("Pos", f"{aligned_time:.6f}")
                loop_poi.set("Num", "-1")
                loop_poi.set("Color", color)
                loop_poi.set("Type", "loop")
                loop_poi.set("Size", str(float(loop_data.get("length_beats", 16))))
                loop_poi.set("Slot", str(loop_count))

                all_pois.append((aligned_time, loop_poi))

            # Sort all POIs by timestamp and add to song
            all_pois.sort(key=lambda x: x[0])

            # Reassign loop slots in chronological order
            loop_slot_counter = 1
            for _, poi_element in all_pois:
                if poi_element.get("Type") == "loop":
                    poi_element.set("Slot", str(loop_slot_counter))
                    loop_slot_counter += 1
                song_element.append(poi_element)

            # Add comment with colors
            existing_comment = song_element.find("Comment")
            if existing_comment is not None:
                song_element.remove(existing_comment)

            # Generate comment from used colors
            used_colors = set()
            for _, poi_element in all_pois:
                color_value = poi_element.get("Color")
                for color_name, value in self.color_mappings.items():
                    if value == color_value:
                        used_colors.add(color_name)
                        break

            full_comment = " ".join(sorted(used_colors))
            full_comment = self.sanitize_xml_content(full_comment)
            comment_element = ET.Element("Comment")
            comment_element.text = full_comment
            song_element.append(comment_element)

            print(
                f"✅ Applied {cue_count} cues and {loop_count} loops to "
                f"{os.path.basename(audio_file_path)} (in memory)"
            )
            return True

        except Exception as e:
            print(f"❌ Error applying cues to {audio_file_path}: {e}")
            import traceback

            traceback.print_exc()
            return False

