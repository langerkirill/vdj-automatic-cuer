"""Single path for building and writing VirtualDJ cue/loop POIs."""

from __future__ import annotations

import re

from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Sequence, Tuple

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
    jumpable: bool = True


_COLOR_RANK = {
    "yellow": 4,  # drums + vocals
    "orange": 3,  # vocals, no drums
    "green": 2,  # drums + melody
    "purple": 1,  # drums only
    "blue": 0,  # melody only
}


def _share_frequency_colors(
    cues: Sequence[PreparedPoi],
    loops: Sequence[PreparedPoi],
    *,
    eps: float = 0.05,
    disk_one: Optional[float] = None,
) -> tuple[List[PreparedPoi], List[PreparedPoi]]:
    """Co-located cue+loop keep the same frequency color (prefer vocals).

    Cue 1 / disk 1 keeps its own color so an instrumental intro is not painted
    yellow by a vocal loop cloned onto that slot.
    """

    def rank(item: PreparedPoi) -> int:
        return _COLOR_RANK.get(item.color_name, 0)

    def paint(item: PreparedPoi, donor: PreparedPoi) -> PreparedPoi:
        if rank(donor) <= rank(item):
            return item
        return replace(
            item,
            color_name=donor.color_name,
            color_value=donor.color_value,
            elements=list(donor.elements) or list(item.elements),
        )

    def on_disk_one(pos: float) -> bool:
        return disk_one is not None and abs(pos - float(disk_one)) <= eps

    new_loops: List[PreparedPoi] = []
    new_cues = list(cues)
    for loop in loops:
        match_i = next(
            (
                i
                for i, cue in enumerate(new_cues)
                if abs(cue.position - loop.position) <= eps
            ),
            None,
        )
        if match_i is None:
            new_loops.append(loop)
            continue
        cue = new_cues[match_i]
        if on_disk_one(cue.position):
            new_loops.append(
                replace(
                    loop,
                    color_name=cue.color_name,
                    color_value=cue.color_value,
                    elements=list(cue.elements) or list(loop.elements),
                )
            )
            continue
        if rank(loop) > rank(cue):
            new_cues[match_i] = paint(cue, loop)
            new_loops.append(loop)
        else:
            new_loops.append(paint(loop, cue))
    return new_cues, new_loops


def _tint_loops_from_cues(
    cues: Sequence[PreparedPoi], loops: Sequence[PreparedPoi], *, eps: float = 0.05
) -> List[PreparedPoi]:
    """A loop on the same 1 as a cue takes that cue's frequency color."""
    _, painted = _share_frequency_colors(cues, loops, eps=eps)
    return painted


_DISTINCT_PARTS = (
    "Beat Entry",
    "Build",
    "Drop",
    "Chorus",
    "Groove",
    "Verse",
    "Synth Intro",
    "Bass",
    "Vocal Mix",
    "Vocal Break",
    "Breakdown",
    "Bridge",
    "Outro",
    "Hook",
)


def _base_part_name(name: str) -> str:
    """Strip loop suffix and ' 2' so Beat Entry 2 is still Beat Entry."""
    raw = (name or "").strip()
    raw = re.sub(r"\s+loop$", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s+\d+$", "", raw)
    if raw.lower().endswith("introl"):
        raw = raw[:-1]
    return raw


def _with_loop_suffix(name: str) -> str:
    """Always ' Loop'. Never glue 'l' (Introl / Groovel / synthl)."""
    raw = (name or "").strip()
    if not raw:
        return "Loop"
    if raw.lower().endswith("loop"):
        return raw
    return f"{raw} Loop"


def _next_distinct_part(used: set) -> str:
    for part in _DISTINCT_PARTS:
        if part not in used:
            return part
    return "Groove"


def _unique_poi_names(items: List[PreparedPoi]) -> List[PreparedPoi]:
    """Cloned 1 / +32 loops and repeat cues get a different part, not ' 2'."""
    used: set = set()
    out: List[PreparedPoi] = []
    for item in items:
        name = _base_part_name(item.name or "")
        if not name or name in used:
            name = _next_distinct_part(used)
        used.add(name)
        if item.kind == "loop":
            name = _with_loop_suffix(name)
        out.append(item if name == item.name else replace(item, name=name))
    return out


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
        analysis_data = self._reassert_stem_elements(analysis_data, audio_file_path)
        actual_bpm = self._actual_bpm(working_bpm)
        beatgrid_offset = float(self.get_beatgrid_offset(audio_file_path) or 0.0)
        analysis_data = apply_precision_gate(
            analysis_data, actual_bpm, beatgrid_offset
        )
        return analysis_data, working_bpm, song_length

    def _reassert_stem_elements(
        self, analysis_data: Dict, audio_file_path: str
    ) -> Dict:
        """Re-measure VDJ stems at apply so cached Gemini JSON cannot keep
        yellow on an instrumental (vocal-stem bleed)."""
        finder = getattr(self, "_find_vdj_stems_file", None)
        extract = getattr(self, "_extract_vdj_stems", None)
        if not callable(finder) or not callable(extract):
            return analysis_data
        vdj_stems = finder(audio_file_path)
        if not vdj_stems:
            return analysis_data
        import shutil
        import tempfile

        from .stem_evidence import load_stem_profiles, measure_stem_evidence

        tmp = tempfile.mkdtemp(prefix="vdj-stems-reassert-")
        try:
            stem_files = extract(vdj_stems, tmp)
            if not stem_files:
                return analysis_data
            cache = getattr(self, "_track_audio_cache", None)
            if cache is not None and hasattr(cache, "get_or_load_stem_profiles"):
                profiles = cache.get_or_load_stem_profiles(list(stem_files))
            else:
                profiles = load_stem_profiles(stem_files)
            items = list(analysis_data.get("measure_changes") or []) + list(
                analysis_data.get("loop_segments") or []
            )
            for item in items:
                if "timestamp" in item:
                    timestamp = float(item.get("timestamp") or 0.0)
                else:
                    timestamp = float(item.get("start") or 0.0)
                model_elements = item.get("elements") or [
                    "drums",
                    "vocals",
                    "bass",
                    "synth",
                ]
                evidence = measure_stem_evidence(
                    profiles,
                    timestamp=timestamp,
                    duration_seconds=4.0,
                    model_elements=model_elements,
                    centered=True,
                )
                item["elements"] = list(evidence.elements)
                item["stem_activity"] = dict(evidence.activity)
                item["stem_scores"] = dict(evidence.scores)
        except Exception as exc:
            print(f"⚠️  Stem reassert skipped: {exc}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return analysis_data

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
        vdj_offset = float(self.get_beatgrid_offset(audio_file_path) or 0.0)

        prepared_cues: List[PreparedPoi] = []
        if scope != WRITE_SCOPE_LOOPS:
            for cue_data in analysis_data.get("measure_changes", [])[:6]:
                aligned_time = self.validate_timing_hybrid(
                    cue_data.get("timestamp", 0), working_bpm, audio_file_path
                )
                actual = self._actual_bpm(working_bpm)
                if (
                    aligned_time is None
                    or actual is None
                    or not is_on_phrase_one(
                        aligned_time, actual, vdj_offset
                    )
                ):
                    print(
                        f"🚫 Hard-fail cue '{cue_data.get('cue_name', 'cue')}' "
                        f"at {cue_data.get('timestamp')}s — not on a phrase 1"
                    )
                    continue
                if song_length and aligned_time >= song_length:
                    continue
                elements = cue_data.get("elements", [])
                if not elements:
                    continue
                gemini_color = cue_data.get("color", "green")
                color_name = self.validate_color_assignment(
                    elements,
                    gemini_color,
                    cue_data.get("stem_activity"),
                )
                color_value = self.color_mappings.get(
                    color_name, self.color_mappings["green"]
                )
                cue_name = cue_data.get("cue_name") or self.create_cue_name(
                    elements, len(prepared_cues) + 1
                )
                jumpable = bool(cue_data.get("jumpable", True))
                if not jumpable and not str(cue_name).lower().startswith("info"):
                    cue_name = f"Info {cue_name}"
                cue_name = self._refuse_generic_section(
                    self.sanitize_marker_name(cue_name), elements
                )
                prepared_cues.append(
                    PreparedPoi(
                        kind="cue",
                        name=cue_name,
                        position=aligned_time,
                        color_name=color_name,
                        color_value=color_value,
                        elements=list(elements),
                        jumpable=jumpable,
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
                    grid_beats=4,
                )
                actual = self._actual_bpm(working_bpm)
                if (
                    aligned_time is None
                    or actual is None
                    or not is_on_phrase_one(
                        aligned_time, actual, vdj_offset
                    )
                ):
                    print(
                        f"🚫 Hard-fail loop '{loop_data.get('loop_name', 'loop')}' "
                        f"at {loop_data.get('start')}s — not on a phrase 1"
                    )
                    continue
                if song_length and aligned_time >= (song_length - 10):
                    continue
                elements = loop_data.get("elements", [])
                if not elements:
                    continue
                loop_name = loop_data.get("loop_name") or self.create_loop_name(elements)
                loop_name = _with_loop_suffix(
                    self._refuse_generic_section(
                        self.sanitize_marker_name(loop_name), elements
                    )
                )
                base_part = _base_part_name(loop_name)
                if base_part in {_base_part_name(n) for n in used_loop_names}:
                    loop_name = _with_loop_suffix(_next_distinct_part(
                        {_base_part_name(n) for n in used_loop_names}
                    ))
                used_loop_names.add(loop_name)
                gemini_color = loop_data.get("color", "green")
                color_name = self.validate_color_assignment(
                    elements,
                    gemini_color,
                    loop_data.get("stem_activity"),
                )
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

        from .user_one import (
            ensure_loops_on_user_one,
            has_marker_on_user_one,
            pin_markers_to_user_one,
        )

        prepared_cues = _unique_poi_names(
            pin_markers_to_user_one(vdj_offset, prepared_cues)
        )
        actual_for_loops = self._actual_bpm(working_bpm) or working_bpm
        if scope == WRITE_SCOPE_CUES:
            prepared_loops = list(prepared_loops)
        else:
            parked = _unique_poi_names(
                ensure_loops_on_user_one(
                    vdj_offset,
                    prepared_loops,
                    bpm=float(actual_for_loops or 0),
                    song_length=song_length,
                )
            )
            prepared_cues, prepared_loops = _share_frequency_colors(
                prepared_cues, parked, disk_one=vdj_offset
            )
        if scope != WRITE_SCOPE_LOOPS and not has_marker_on_user_one(
            vdj_offset, prepared_cues
        ):
            color_name = "green"
            color_value = self.color_mappings.get(
                color_name, self.color_mappings["green"]
            )
            prepared_cues.insert(
                0,
                PreparedPoi(
                    kind="cue",
                    name="1",
                    position=float(vdj_offset),
                    color_name=color_name,
                    color_value=color_value,
                    elements=["downbeat"],
                    jumpable=True,
                ),
            )
            print(
                f"🎯 Inserted cue 1 on the disk 1 at {vdj_offset:.3f}s "
                f"(Gemini had no marker there)"
            )
        used_names: list[str] = []
        unique_cues: List[PreparedPoi] = []
        for index, cue in enumerate(prepared_cues):
            name = _base_part_name(
                self._refuse_generic_section(cue.name, cue.elements)
            )
            if index == 0 and (name in {"1", ""} or "section" in name.lower()):
                name = "Beat Entry"
            if name in used_names:
                name = _next_distinct_part(set(used_names))
            used_names.append(name)
            if name != cue.name:
                print(f"  🏷️  {cue.name} → {name}")
                cue = replace(cue, name=name)
            unique_cues.append(cue)
        prepared_cues = unique_cues
        print(
            f"🎯 User 1 at {vdj_offset:.3f}s · "
            f"{len(prepared_cues)} cue(s) / {len(prepared_loops)} loop(s) "
            f"on or after that 1"
        )

        used_colors = sorted(
            {poi.color_name for poi in prepared_cues + prepared_loops}
        )
        return PreparedSongCues(
            cues=prepared_cues,
            loops=prepared_loops,
            beatgrid_offset=vdj_offset,
            beatgrid_corrected=False,
            comment=self.sanitize_xml_content(" ".join(used_colors)),
        )

    def _build_native_poi_lines(
        self, prepared: PreparedSongCues, newline: str = "\r\n"
    ) -> List[str]:
        """Build VirtualDJ-native POI lines (never ElementTree-serialized)."""
        entries: List[Tuple[float, str, dict]] = []
        # Number jump cues in time order so Num 1 is the disk 1, not Gemini order.
        timed_cues = sorted(prepared.cues, key=lambda c: c.position)
        jump_num = 1
        for cue in timed_cues:
            if cue.jumpable:
                num = str(jump_num)
                jump_num += 1
            else:
                num = "0"
            entries.append(
                (
                    cue.position,
                    "cue",
                    {
                        "pos": cue.position,
                        "poi_type": "cue",
                        "num": num,
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
        # AutoCue never rewrites Scan Phase / beatgrid POI. Align grid is separate.

        poi_lines = self._build_native_poi_lines(prepared, newline=newline)
        # Lock yellow 1s to the disk 1. Do not move Scan Phase — write the
        # missing beatgrid POI on that same Phase so Ones and cues share it.
        if prepared.beatgrid_offset is not None and not re.search(
            r'Type\s*=\s*"beatgrid"', song_xml, flags=re.IGNORECASE
        ):
            poi_lines.insert(
                0,
                f'  <Poi Pos="{float(prepared.beatgrid_offset):.6f}" Type="beatgrid" />{newline}',
            )
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

        all_pois: List[Tuple[float, ET.Element]] = []
        jump_num = 1
        for cue in prepared.cues:
            cue_poi = ET.Element("Poi")
            cue_poi.set("Pos", f"{cue.position:.6f}")
            if cue.jumpable:
                cue_poi.set("Num", str(jump_num))
                jump_num += 1
            else:
                cue_poi.set("Num", "0")
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

            # Refuse if VirtualDJ opened during the long Gemini analysis.
            from vdj_database_safety import assert_safe_to_write_vdj_database
            from .grid_gate import assert_user_one_settled

            assert_safe_to_write_vdj_database()
            assert_user_one_settled(
                getattr(self, "grid_preflight", None),
                confirmed=bool(getattr(self, "grid_confirmed", False)),
            )

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
