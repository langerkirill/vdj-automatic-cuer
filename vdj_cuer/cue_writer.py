"""Single path for building and writing VirtualDJ cue/loop POIs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .common import *
from vdj_database_safety import (
    extract_manual_pois_from_song_xml,
    format_vdj_poi_line,
    inject_pois_into_song_xml,
    load_song_element,
    read_vdj_database_text,
    rewrite_song_xml_in_database,
    _find_song_span,
    _detect_newline,
)


@dataclass(frozen=True)
class PreparedPoi:
    """One cue or loop ready to write."""

    kind: str  # "cue" | "loop"
    name: str
    position: float
    color_name: str
    color_value: str
    elements: List[str]
    length_beats: Optional[float] = None


@dataclass
class PreparedSongCues:
    """Deterministic POI plan shared by dry-run and write paths."""

    cues: List[PreparedPoi]
    loops: List[PreparedPoi]
    beatgrid_offset: Optional[float] = None
    beatgrid_corrected: bool = False
    comment: str = ""


class CueWriterMixin:
    def _finalize_analysis_for_write(
        self, audio_file_path: str, analysis_data: Dict
    ) -> Tuple[Dict, float, Optional[float]]:
        """Post-process loops, re-apply precision gate once, return working BPM."""
        from .precision_gate import apply_precision_gate

        song_length = self.get_song_length(audio_file_path)
        working_bpm = self.get_song_bpm_from_database(audio_file_path) or 120.0
        analysis_data = self._postprocess_loop_segments(
            analysis_data, working_bpm, song_length
        )
        actual_bpm = self._actual_bpm(working_bpm)
        beatgrid_offset = self._get_verified_beatgrid_offset(
            audio_file_path, working_bpm
        )
        analysis_data = apply_precision_gate(
            analysis_data, actual_bpm, beatgrid_offset
        )
        return analysis_data, working_bpm, song_length

    def _loop_priority(self, loop_data: Dict) -> int:
        elements = loop_data.get("elements", [])
        has_drums = any(elem in elements for elem in ["drums", "percussion"])
        has_vocals = "vocals" in elements
        has_melody = any(
            elem in elements for elem in ["piano", "synth", "strings", "guitar"]
        )
        if has_drums and not has_vocals and len(elements) <= 2:
            return 0
        if has_vocals:
            return 1
        if has_melody and not has_drums and not has_vocals:
            return 2
        return 3

    def _write_scope(self) -> str:
        scope = getattr(self, "write_scope", WRITE_SCOPE_ALL)
        if scope not in WRITE_SCOPES:
            return WRITE_SCOPE_ALL
        return scope

    def _color_name_for_value(self, color_value: str) -> str:
        for name, value in self.color_mappings.items():
            if str(value) == str(color_value):
                return name
        return "green"

    def _load_existing_prepared_pois(
        self, audio_file_path: str
    ) -> Tuple[List[PreparedPoi], List[PreparedPoi]]:
        """Read current cue/loop POIs from database.xml for preserve-on-retry."""
        try:
            database_text = read_vdj_database_text(self.vdj_database_path)
            span = _find_song_span(database_text, audio_file_path)
            if span is None:
                return [], []
            song_xml = database_text[span[0] : span[1]]
            extracted = extract_manual_pois_from_song_xml(song_xml)
        except Exception as error:
            print(f"  ⚠️  Could not load existing POIs to preserve: {error}")
            return [], []

        cues: List[PreparedPoi] = []
        for item in extracted.get("cues", []):
            color_value = str(item.get("color") or self.color_mappings["green"])
            cues.append(
                PreparedPoi(
                    kind="cue",
                    name=self.sanitize_marker_name(str(item.get("name") or "Cue")),
                    position=float(item.get("position") or 0.0),
                    color_name=self._color_name_for_value(color_value),
                    color_value=color_value,
                    elements=["preserved"],
                )
            )
        loops: List[PreparedPoi] = []
        for item in extracted.get("loops", []):
            color_value = str(item.get("color") or self.color_mappings["green"])
            length = item.get("length_beats")
            loops.append(
                PreparedPoi(
                    kind="loop",
                    name=self.sanitize_marker_name(str(item.get("name") or "Loop")),
                    position=float(item.get("position") or 0.0),
                    color_name=self._color_name_for_value(color_value),
                    color_value=color_value,
                    elements=["preserved"],
                    length_beats=float(length if length is not None else 16),
                )
            )
        return cues, loops

    def prepare_song_cues(
        self, audio_file_path: str, analysis_data: Dict
    ) -> PreparedSongCues:
        """Build the exact cue/loop plan that dry-run and writes will share.

        Honors ``self.write_scope``:
        - ``all``: rewrite cues and loops from analysis
        - ``cues``: rewrite cues only; keep existing loops from the database
        - ``loops``: rewrite loops only; keep existing cues from the database
        """
        scope = self._write_scope()
        analysis_data, working_bpm, song_length = self._finalize_analysis_for_write(
            audio_file_path, analysis_data
        )
        alignment = self._verify_beatgrid_alignment(audio_file_path, working_bpm)

        prepared_cues: List[PreparedPoi] = []
        if scope != WRITE_SCOPE_LOOPS:
            for cue_data in analysis_data.get("measure_changes", [])[:6]:
                aligned_time = self.validate_timing_hybrid(
                    cue_data.get("timestamp", 0), working_bpm, audio_file_path
                )
                if song_length and aligned_time >= song_length:
                    continue
                elements = cue_data.get("elements", [])
                if not elements:
                    continue
                gemini_color = cue_data.get("color", "green")
                color_name = self.validate_color_assignment(elements, gemini_color)
                color_value = self.color_mappings.get(
                    color_name, self.color_mappings["green"]
                )
                cue_name = cue_data.get("cue_name") or self.create_cue_name(
                    elements, len(prepared_cues) + 1
                )
                prepared_cues.append(
                    PreparedPoi(
                        kind="cue",
                        name=self.sanitize_marker_name(cue_name),
                        position=aligned_time,
                        color_name=color_name,
                        color_value=color_value,
                        elements=list(elements),
                    )
                )

        prepared_loops: List[PreparedPoi] = []
        if scope != WRITE_SCOPE_CUES:
            used_loop_names = set()
            loops = sorted(
                analysis_data.get("loop_segments", []), key=self._loop_priority
            )
            for loop_data in loops:
                if len(prepared_loops) >= 3:
                    break
                aligned_time = self.validate_timing_hybrid(
                    loop_data.get("start", 0),
                    working_bpm,
                    audio_file_path,
                    grid_beats=1,
                )
                if song_length and aligned_time >= (song_length - 10):
                    continue
                elements = loop_data.get("elements", [])
                if not elements:
                    continue
                loop_name = loop_data.get("loop_name") or self.create_loop_name(elements)
                # Avoid "Loop" + "l" => "Loopl"
                if loop_name.lower().endswith("loop"):
                    pass
                elif not loop_name.endswith("l"):
                    loop_name = f"{loop_name}l"
                loop_name = self.sanitize_marker_name(loop_name)
                if loop_name in used_loop_names:
                    continue
                used_loop_names.add(loop_name)
                gemini_color = loop_data.get("color", "green")
                color_name = self.validate_color_assignment(elements, gemini_color)
                color_value = self.color_mappings.get(
                    color_name, self.color_mappings["green"]
                )
                prepared_loops.append(
                    PreparedPoi(
                        kind="loop",
                        name=loop_name,
                        position=aligned_time,
                        color_name=color_name,
                        color_value=color_value,
                        elements=list(elements),
                        length_beats=float(loop_data.get("length_beats", 16)),
                    )
                )

        if scope in (WRITE_SCOPE_CUES, WRITE_SCOPE_LOOPS):
            existing_cues, existing_loops = self._load_existing_prepared_pois(
                audio_file_path
            )
            if scope == WRITE_SCOPE_CUES:
                prepared_loops = existing_loops
                print(
                    f"  🔒 Cues-only retry: keeping {len(prepared_loops)} existing "
                    f"loop(s), rewriting {len(prepared_cues)} cue(s)"
                )
            else:
                prepared_cues = existing_cues
                print(
                    f"  🔒 Loops-only retry: keeping {len(prepared_cues)} existing "
                    f"cue(s), rewriting {len(prepared_loops)} loop(s)"
                )

        used_colors = sorted(
            {poi.color_name for poi in prepared_cues + prepared_loops}
        )
        return PreparedSongCues(
            cues=prepared_cues,
            loops=prepared_loops,
            beatgrid_offset=alignment.offset if alignment.corrected else None,
            beatgrid_corrected=alignment.corrected,
            comment=self.sanitize_xml_content(" ".join(used_colors)),
        )

    def _build_native_poi_lines(
        self, prepared: PreparedSongCues, newline: str = "\r\n"
    ) -> List[str]:
        """Build VirtualDJ-native POI lines (never ElementTree-serialized)."""
        entries: List[Tuple[float, str, dict]] = []
        for index, cue in enumerate(prepared.cues, start=1):
            entries.append(
                (
                    cue.position,
                    "cue",
                    {
                        "pos": cue.position,
                        "poi_type": "cue",
                        "num": str(index),
                        "color": cue.color_value,
                        "name": cue.name,
                    },
                )
            )
        for loop in prepared.loops:
            entries.append(
                (
                    loop.position,
                    "loop",
                    {
                        "pos": loop.position,
                        "poi_type": "loop",
                        "num": "-1",
                        "color": loop.color_value,
                        "name": loop.name,
                        "size": str(loop.length_beats or 16),
                        "slot": "0",
                    },
                )
            )
        entries.sort(key=lambda item: item[0])
        loop_slot = 1
        lines: List[str] = []
        for _, kind, attrs in entries:
            if kind == "loop":
                attrs["slot"] = str(loop_slot)
                loop_slot += 1
            lines.append(format_vdj_poi_line(newline=newline, **attrs))
        return lines

    def _apply_prepared_cues_to_song_xml(
        self, song_xml: str, prepared: PreparedSongCues
    ) -> str:
        """
        Inject cues into original Song XML text.

        Preserves Tags/Infos/Scan/automix/remix markers exactly so VirtualDJ
        does not treat the database as corrupted on open.
        """
        newline = _detect_newline(song_xml)
        # VirtualDJ displays beat "1" from Scan Phase AND the beatgrid POI.
        # Library analysis shows Phase == beatgrid Pos on ~23k tracks; when we
        # only update the POI, cues land on our grid while VDJ still draws the
        # old "1" from Phase (exactly the Vortex Number 9 failure mode).
        if prepared.beatgrid_corrected and prepared.beatgrid_offset is not None:
            song_xml = self._apply_beatgrid_to_song_xml(
                song_xml, prepared.beatgrid_offset, newline=newline
            )

        poi_lines = self._build_native_poi_lines(prepared, newline=newline)
        return inject_pois_into_song_xml(
            song_xml, poi_lines, comment=prepared.comment or None
        )

    @staticmethod
    def _apply_beatgrid_to_song_xml(
        song_xml: str, beatgrid_offset: float, newline: str = "\r\n"
    ) -> str:
        """Keep Scan Phase and beatgrid POI in lockstep for VirtualDJ."""
        phase = f"{beatgrid_offset:.6f}"
        if re.search(r"<Scan\b[^>]*\bPhase\s*=", song_xml, flags=re.IGNORECASE):
            song_xml = re.sub(
                r'(<Scan\b[^>]*\bPhase\s*=\s*")[^"]*(")',
                rf"\g<1>{phase}\2",
                song_xml,
                count=1,
                flags=re.IGNORECASE,
            )
        else:
            song_xml = re.sub(
                r"(<Scan\b)(\s*)",
                rf'\1 Phase="{phase}"\2',
                song_xml,
                count=1,
                flags=re.IGNORECASE,
            )

        # Replace the entire beatgrid POI line so we never create duplicate Pos=.
        beatgrid_line = f'  <Poi Pos="{phase}" Type="beatgrid" />{newline}'
        beatgrid_poi_re = re.compile(
            r"[ \t]*<Poi\b[^>]*\bType\s*=\s*\"beatgrid\"[^>]*/>[ \t]*(?:\r?\n)?",
            re.IGNORECASE,
        )
        if beatgrid_poi_re.search(song_xml):
            song_xml = beatgrid_poi_re.sub(beatgrid_line, song_xml, count=1)
        else:
            song_xml = inject_pois_into_song_xml(song_xml, [beatgrid_line])
        return song_xml

    def _apply_prepared_cues_to_song(
        self, song_element, prepared: PreparedSongCues
    ) -> None:
        """Legacy in-memory ElementTree mutator (batch compatibility only)."""
        for poi in list(song_element.findall("Poi")):
            if poi.get("Type") in ["cue", "loop"] and poi.get("Num", "0") != "0":
                song_element.remove(poi)

        if prepared.beatgrid_corrected and prepared.beatgrid_offset is not None:
            beatgrid_poi = None
            for poi in song_element.findall("Poi"):
                if poi.get("Type") == "beatgrid":
                    beatgrid_poi = poi
                    break
            if beatgrid_poi is None:
                beatgrid_poi = ET.Element("Poi")
                beatgrid_poi.set("Type", "beatgrid")
                song_element.append(beatgrid_poi)
            beatgrid_poi.set("Pos", f"{prepared.beatgrid_offset:.6f}")

        all_pois: List[Tuple[float, ET.Element]] = []
        for index, cue in enumerate(prepared.cues, start=1):
            cue_poi = ET.Element("Poi")
            cue_poi.set("Pos", f"{cue.position:.6f}")
            cue_poi.set("Num", str(index))
            cue_poi.set("Color", cue.color_value)
            cue_poi.set("Type", "cue")
            cue_poi.set("Name", cue.name)
            all_pois.append((cue.position, cue_poi))

        for loop in prepared.loops:
            loop_poi = ET.Element("Poi")
            loop_poi.set("Pos", f"{loop.position:.6f}")
            loop_poi.set("Num", "-1")
            loop_poi.set("Color", loop.color_value)
            loop_poi.set("Type", "loop")
            loop_poi.set("Size", str(int(loop.length_beats or 16)))
            loop_poi.set("Slot", "0")
            loop_poi.set("Name", loop.name)
            all_pois.append((loop.position, loop_poi))

        all_pois.sort(key=lambda item: item[0])
        loop_slot = 1
        for _, poi_element in all_pois:
            if poi_element.get("Type") == "loop":
                poi_element.set("Slot", str(loop_slot))
                loop_slot += 1
            song_element.append(poi_element)

        existing_comment = song_element.find("Comment")
        if existing_comment is not None:
            song_element.remove(existing_comment)
        if prepared.comment:
            comment_element = ET.Element("Comment")
            comment_element.text = prepared.comment
            song_element.append(comment_element)

    def _preview_prepared_cues(
        self, audio_file_path: str, prepared: PreparedSongCues
    ) -> bool:
        print(f"\n🎶 Applying cues: {os.path.basename(audio_file_path)}")
        print("🔍 DRY RUN - Would create:")
        if prepared.beatgrid_corrected and prepared.beatgrid_offset is not None:
            print(
                f"  Would update beatgrid '1' → {prepared.beatgrid_offset:.6f}s"
            )
        for index, cue in enumerate(prepared.cues, start=1):
            print(
                f"  Cue {index}: '{cue.name}' at {cue.position:.1f}s | "
                f"Color: {cue.color_name.capitalize()} | Elements: {cue.elements}"
            )
        for index, loop in enumerate(prepared.loops, start=1):
            print(
                f"  Loop {index}: '{loop.name}' at {loop.position:.1f}s "
                f"({loop.length_beats:g} beats) | Color: {loop.color_name.capitalize()} | "
                f"Elements: {loop.elements}"
            )
        return True

    def _apply_cues_to_database(
        self, audio_file_path: str, analysis_data: Dict, dry_run: bool = False
    ) -> bool:
        """Apply analysis results using one prepared plan and a surgical DB rewrite."""
        try:
            prepared = self.prepare_song_cues(audio_file_path, analysis_data)
            if dry_run:
                return self._preview_prepared_cues(audio_file_path, prepared)

            print(f"\n🎶 Applying cues: {os.path.basename(audio_file_path)}")

            # Text-preserving rewrite: keep native Tags/Scan/automix markup and CRLF.
            database_text = read_vdj_database_text(self.vdj_database_path)
            span = _find_song_span(database_text, audio_file_path)
            if span is None:
                raise KeyError(f"Song not found in database: {audio_file_path}")
            original_song_xml = database_text[span[0] : span[1]]
            del database_text
            updated_song_xml = self._apply_prepared_cues_to_song_xml(
                original_song_xml, prepared
            )
            rewrite_song_xml_in_database(
                self.vdj_database_path,
                audio_file_path,
                updated_song_xml,
                validate=True,
            )
            # Metadata may have changed (beatgrid/cues); force refresh next read.
            self._invalidate_metadata_cache()

            scope = self._write_scope()
            if scope == WRITE_SCOPE_CUES:
                print(
                    f"✅ Applied {len(prepared.cues)} cue(s) "
                    f"(kept {len(prepared.loops)} loop(s)) to "
                    f"{os.path.basename(audio_file_path)}"
                )
            elif scope == WRITE_SCOPE_LOOPS:
                print(
                    f"✅ Applied {len(prepared.loops)} loop(s) "
                    f"(kept {len(prepared.cues)} cue(s)) to "
                    f"{os.path.basename(audio_file_path)}"
                )
            else:
                print(
                    f"✅ Applied {len(prepared.cues)} cues and "
                    f"{len(prepared.loops)} loops to "
                    f"{os.path.basename(audio_file_path)}"
                )
            return True
        except KeyError:
            print(f"❌ Song not found in VDJ database: {audio_file_path}")
            return False
        except Exception as e:
            print(f"❌ Error applying cues to {audio_file_path}: {e}")
            import traceback

            traceback.print_exc()
            return False
        finally:
            # Drop per-track decode caches after dry-run or write completes.
            if hasattr(self, "_release_track_resources"):
                self._release_track_resources(audio_file_path)

    def _apply_cues_to_batch_database(
        self, root, audio_file_path: str, analysis_data: Dict
    ) -> bool:
        """
        Legacy in-memory batch mutator kept for compatibility.

        Prefer surgical per-song writes via _apply_cues_to_database.
        """
        try:
            prepared = self.prepare_song_cues(audio_file_path, analysis_data)
            import unicodedata

            normalized_target = unicodedata.normalize("NFC", audio_file_path)
            song_element = None
            for song in root.findall("Song"):
                db_path = song.get("FilePath", "")
                if unicodedata.normalize("NFC", db_path) == normalized_target:
                    song_element = song
                    break
            if song_element is None:
                print(f"❌ Song not found in VDJ database: {audio_file_path}")
                return False
            self._apply_prepared_cues_to_song(song_element, prepared)
            print(
                f"✅ Applied {len(prepared.cues)} cues and {len(prepared.loops)} loops to "
                f"{os.path.basename(audio_file_path)} (in memory)"
            )
            return True
        except Exception as e:
            print(f"❌ Error applying cues to {audio_file_path}: {e}")
            import traceback

            traceback.print_exc()
            return False
