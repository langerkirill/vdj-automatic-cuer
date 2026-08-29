"""FileProcessorMixin for AutomaticMusicCuer."""

from .common import *


class FileProcessorMixin:
    def process_audio_file(self, audio_file_path: str, dry_run: bool = False) -> bool:
        """Process a single audio file and add cues/loops to VDJ database"""
        print(f"\n🎶 Processing: {os.path.basename(audio_file_path)}")

        # First check if song exists in VDJ database (fail fast)
        try:
            print(f"🔍 Checking VDJ database for: {audio_file_path}")
            root = self.parse_vdj_database()
            if root is None:
                print("❌ Could not parse VDJ database")
                return False

            song_found = False
            songs_checked = 0

            # Normalize the target path for comparison (handle Unicode issues)
            import unicodedata

            normalized_target = unicodedata.normalize("NFC", audio_file_path)

            for song in root.findall("Song"):
                songs_checked += 1
                db_path = song.get("FilePath", "")
                normalized_db_path = unicodedata.normalize("NFC", db_path)

                if normalized_db_path == normalized_target:
                    song_found = True
                    print(
                        f"✅ Song found in database after checking "
                        f"{songs_checked} songs"
                    )
                    break

            if not song_found:
                print(
                    f"❌ Song not found in VDJ database after checking "
                    f"{songs_checked} songs"
                )
                print("💡 Make sure the song has been analyzed in VirtualDJ first")
                return False

        except ET.ParseError as e:
            print(f"⚠️  VDJ database XML parsing issue: {e}")
            # Continue anyway - the later database update might handle it
        except Exception as e:
            print(f"⚠️  Could not check VDJ database: {e}")
            # Continue anyway

        # Get Gemini analysis
        analysis = self.analyze_audio_with_gemini(audio_file_path)
        if not analysis:
            print(f"❌ Skipping {audio_file_path} - analysis failed")
            return False

        # Get song length for validation
        song_length = self.get_song_length(audio_file_path)

        # Get BPM from database for validation
        database_bpm = self.get_song_bpm_from_database(audio_file_path)
        analysis_bpm = analysis.get("song_structure", {}).get(
            "bpm", database_bpm or 120
        )
        working_bpm = database_bpm or analysis_bpm
        analysis = self._postprocess_loop_segments(analysis, working_bpm, song_length)

        # Convert VDJ BPM fraction to actual BPM for display
        display_bpm = working_bpm
        if working_bpm and working_bpm < 5:  # If it looks like a VDJ fraction
            display_bpm = 60.0 / working_bpm

        print(
            f"📊 BPM: {display_bpm:.1f} | "
            f"Cues: {len(analysis.get('measure_changes', []))} | "
            f"Loops: {len(analysis.get('loop_segments', []))}"
        )

        if dry_run:
            return self._preview_single_file_cues(
                analysis, working_bpm, audio_file_path
            )

        # Load and modify VDJ database
        try:
            root = self.parse_vdj_database()
            if root is None:
                print("❌ Could not parse VDJ database for modification")
                return False

            # Find the song in database (with Unicode normalization)
            song_element = None
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

            # Remove existing manual cues and loops (safe removal)
            pois_to_remove = []
            for poi in song_element.findall("Poi"):
                if poi.get("Type") in ["cue", "loop"] and poi.get("Num", "0") != "0":
                    pois_to_remove.append(poi)

            for poi in pois_to_remove:
                song_element.remove(poi)

            print(f"🧹 Removed {len(pois_to_remove)} existing cues/loops")

            # Prepare all cues and loops with timing alignment
            all_pois = []

            # Process cues
            cue_count = 0
            for cue_data in analysis.get("measure_changes", [])[:6]:  # Max 6 cues
                # Cue points always land on a verified bar downbeat.
                gemini_time = cue_data.get("timestamp", 0)
                aligned_time = self.validate_timing_hybrid(
                    gemini_time, working_bpm, audio_file_path
                )
                if aligned_time is None:
                    continue

                # Skip cues that are beyond song length
                if song_length and aligned_time >= song_length:
                    print(
                        f"⚠️  Skipping cue at {aligned_time:.1f}s - beyond "
                        f"song length ({song_length:.1f}s)"
                    )
                    continue

                cue_count += 1
                # Validate and correct color assignment
                gemini_color = cue_data.get("color", "green")
                elements = cue_data.get("elements", [])  # Handle missing elements
                if not elements:
                    print(
                        "⚠️  Warning: Cue has no elements detected, "
                        f"skipping: {cue_data}"
                    )
                    continue

                validated_color = self.validate_color_assignment(elements, gemini_color)
                if validated_color != gemini_color:
                    reason = ""
                    if gemini_color == "purple" and validated_color == "blue":
                        reason = " (melodic elements prominent)"
                    print(
                        f"  🎨 Color corrected: {gemini_color} → "
                        f"{validated_color} for "
                        f"{cue_data.get('cue_name', 'cue')}{reason}"
                    )
                color = self.color_mappings.get(
                    validated_color, self.color_mappings["green"]
                )
                # Use Gemini's suggested cue name if available, otherwise fallback
                cue_name = cue_data.get("cue_name") or self.create_cue_name(
                    cue_data.get("elements", []),
                    cue_data.get("measure", cue_count),
                )

                # Sanitize cue name (no &, no double-entity encoding)
                cue_name = self.sanitize_marker_name(cue_name)

                cue_poi = ET.Element("Poi")
                cue_poi.set("Name", cue_name)
                cue_poi.set("Pos", f"{aligned_time:.6f}")
                cue_poi.set("Num", str(cue_count))
                cue_poi.set("Color", color)
                cue_poi.set("Type", "cue")

                all_pois.append((aligned_time, cue_poi))

            # Process loops (prioritize different types, ensure at least one drum loop)
            loop_count = 0
            used_loop_types = set()

            # Sort loops to prioritize breakdown/minimal sections and drum-only
            loops = analysis.get("loop_segments", [])

            def loop_priority(loop_data):
                elements = loop_data.get("elements", [])
                element_count = len(elements)
                has_drums = any(elem in elements for elem in ["drums", "percussion"])
                has_vocals = "vocals" in elements
                has_melody = any(
                    elem in elements for elem in ["piano", "synth", "strings", "guitar"]
                )

                # Priority 1: Drum-only sections (purple loops)
                if has_drums and not has_vocals and element_count <= 2:
                    return 0
                # Priority 2: Vocal sections (great for mixing)
                elif has_vocals:
                    return 1
                # Priority 3: Melodic sections without drums (blue loops)
                elif has_melody and not has_drums and not has_vocals:
                    return 2
                # Priority 4: Other minimal sections (good for transitions)
                elif element_count <= 2:
                    return 3
                # Lower priority: fuller arrangements
                else:
                    return 4

            loops.sort(key=loop_priority)

            for loop_data in loops:
                if loop_count >= 3:  # Max 3 loops
                    break

                # Use Gemini's suggested loop name if available, otherwise fallback
                from .cue_writer import _with_loop_suffix

                loop_name = loop_data.get("loop_name") or self.create_loop_name(
                    loop_data.get("elements", [])
                )
                loop_name = _with_loop_suffix(loop_name)

                # Sanitize loop name for XML safety
                loop_name = self.sanitize_marker_name(loop_name)

                # Skip if we already have this type of loop
                if loop_name in used_loop_types:
                    continue

                loop_count += 1
                used_loop_types.add(loop_name)

                # Snap loop starts to a beat (prefer 1 when already near it).
                gemini_time = loop_data.get("start", 0)
                aligned_time = self.validate_timing_hybrid(
                    gemini_time, working_bpm, audio_file_path, grid_beats=4
                )
                if aligned_time is None:
                    continue

                # Skip loops that are beyond song length (leave some buffer)
                if song_length and aligned_time >= (song_length - 10):
                    print(
                        f"⚠️  Skipping loop at {aligned_time:.1f}s - too "
                        f"close to song end ({song_length:.1f}s)"
                    )
                    continue

                # Validate and correct color assignment
                gemini_color = loop_data.get("color", "green")
                elements = loop_data.get("elements", [])  # Handle missing elements
                if not elements:
                    print(
                        "⚠️  Warning: Loop has no elements detected, "
                        f"skipping: {loop_data}"
                    )
                    continue

                validated_color = self.validate_color_assignment(elements, gemini_color)
                if validated_color != gemini_color:
                    print(
                        f"  🎨 Color corrected: {gemini_color} → "
                        f"{validated_color} for {loop_name}"
                    )
                color = self.color_mappings.get(
                    validated_color, self.color_mappings["green"]
                )

                loop_poi = ET.Element("Poi")
                loop_poi.set("Name", loop_name)
                loop_poi.set("Pos", f"{aligned_time:.6f}")
                loop_poi.set("Num", "-1")
                loop_poi.set("Color", color)
                loop_poi.set("Type", "loop")
                loop_poi.set("Size", str(float(loop_data.get("length_beats", 16))))
                # Store loop_count for now, will reassign slots after sorting
                loop_poi.set("Slot", str(loop_count))

                all_pois.append((aligned_time, loop_poi))

            # Sort all POIs by timestamp and add to song element
            all_pois.sort(key=lambda x: x[0])

            # Reassign loop slots in chronological order
            loop_slot_counter = 1
            for _, poi_element in all_pois:
                if poi_element.get("Type") == "loop":
                    poi_element.set("Slot", str(loop_slot_counter))
                    loop_slot_counter += 1
                song_element.append(poi_element)

            # Add/update comment with colors
            existing_comment = song_element.find("Comment")
            if existing_comment is not None:
                song_element.remove(existing_comment)

            # Generate comment from actually used colors only
            used_colors = set()

            # Get colors from all POIs that were actually added
            for _, poi_element in all_pois:
                # Extract color from the POI element
                color_value = poi_element.get("Color")
                # Map color value back to color name
                for color_name, value in self.color_mappings.items():
                    if value == color_value:
                        used_colors.add(color_name)
                        break

            full_comment = " ".join(sorted(used_colors))
            full_comment = self.sanitize_xml_content(full_comment)
            comment_element = ET.Element("Comment")
            comment_element.text = full_comment
            song_element.append(comment_element)

            # Save database using safe method
            # (VDJ expects no XML declaration and CRLF line endings)
            try:
                original_stats = self._database_integrity_stats(self.vdj_database_path)
                xml_str = ET.tostring(root, encoding="unicode")

                # Ensure CRLF line endings for VDJ compatibility
                if "\r\n" not in xml_str and "\n" in xml_str:
                    xml_str = xml_str.replace("\n", "\r\n")

                # Validate XML is well-formed before writing
                try:
                    ET.fromstring(xml_str)
                except ET.ParseError as e:
                    raise ValueError(f"Generated XML is malformed: {e}")

                # Write to database with proper encoding (atomic write)
                temp_path = f"{self.vdj_database_path}.tmp"
                with open(temp_path, "w", encoding="utf-8", newline="") as f:
                    f.write(xml_str)

                # Verify before replacing
                try:
                    ET.parse(temp_path)
                    self._validate_database_replacement(temp_path, original_stats)
                    # If parsing succeeds, replace the original file
                    shutil.move(temp_path, self.vdj_database_path)
                    print("✅ Database written and verified successfully")
                except Exception as e:
                    # If parsing fails, remove temp file and raise error
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                    raise ValueError(f"Generated XML file failed verification: {e}")

            except Exception as e:
                print(f"❌ Error saving database: {e}")
                print("💾 Database backup is available if needed")
                raise

            # Show color summary
            print("\n🎨 Color Summary:")
            color_summary = []
            cue_num = 1
            loop_num = 1

            for _, poi_element in sorted(all_pois, key=lambda x: x[0]):
                poi_type = poi_element.get("Type")
                poi_name = poi_element.get("Name", "unnamed")
                color_value = poi_element.get("Color")

                # Map color value back to name
                color_name = "unknown"
                for name, value in self.color_mappings.items():
                    if value == color_value:
                        color_name = name
                        break

                if poi_type == "cue":
                    color_summary.append(f"  Cue {cue_num}: {poi_name} - {color_name}")
                    cue_num += 1
                elif poi_type == "loop":
                    color_summary.append(
                        f"  Loop {loop_num}: {poi_name} - {color_name}"
                    )
                    loop_num += 1

            for line in color_summary:
                print(line)

            print(
                f"\n✅ Added {cue_count} cues and {loop_count} loops to "
                f"{os.path.basename(audio_file_path)}"
            )
            print("💡 Tip: Press Cmd+Option+R in VirtualDJ to refresh the database")
            return True

        except Exception as e:
            import traceback

            print(f"❌ Error updating VDJ database: {e}")
            print("🔍 Full traceback:")
            traceback.print_exc()
            return False

