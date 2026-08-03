"""VdjDatabaseMixin for AutomaticMusicCuer."""

from .common import *


@dataclass(frozen=True)
class VdjSongMetadata:
    """Small read-only subset of one VirtualDJ song entry."""

    scan_bpm: Optional[float] = None
    tags_bpm: Optional[float] = None
    song_length: Optional[float] = None
    beatgrid_offset: float = 0.0
    scan_phase: Optional[float] = None


class VdjDatabaseMixin:
    @staticmethod
    def _normalize_database_path(file_path: str) -> str:
        return unicodedata.normalize("NFC", file_path)

    @staticmethod
    def _optional_float(value: Optional[str]) -> Optional[float]:
        try:
            return float(value) if value not in (None, "") else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _resolve_beatgrid_offset(
        beatgrid_poi_pos: Optional[float],
        scan_phase: Optional[float],
    ) -> float:
        """Prefer explicit beatgrid POI; otherwise use Scan Phase (VDJ's '1').

        Hold Me (Y U QT) failed here: no beatgrid POI, Phase≈56.1s, but cues
        were quantized to 0.0 and landed ~1 beat early in the VirtualDJ UI.
        """
        if beatgrid_poi_pos is not None:
            return beatgrid_poi_pos
        if scan_phase is not None:
            return scan_phase
        return 0.0

    def _database_fingerprint(self) -> Tuple[int, int]:
        stat = os.stat(self.vdj_database_path)
        return stat.st_mtime_ns, stat.st_size

    def _metadata_from_song(self, song) -> Tuple[str, VdjSongMetadata]:
        scan = song.find("Scan")
        tags = song.find("Tags")
        infos = song.find("Infos")
        scan_phase = self._optional_float(scan.get("Phase") if scan is not None else None)
        beatgrid_poi_pos = None
        for poi in song.findall("Poi"):
            if poi.get("Type") == "beatgrid":
                beatgrid_poi_pos = self._optional_float(poi.get("Pos"))
                break

        return (
            self._normalize_database_path(song.get("FilePath", "")),
            VdjSongMetadata(
                scan_bpm=self._optional_float(scan.get("Bpm") if scan is not None else None),
                tags_bpm=self._optional_float(tags.get("Bpm") if tags is not None else None),
                song_length=self._optional_float(
                    infos.get("SongLength") if infos is not None else None
                ),
                beatgrid_offset=self._resolve_beatgrid_offset(
                    beatgrid_poi_pos, scan_phase
                ),
                scan_phase=scan_phase,
            ),
        )

    def _build_metadata_index(self) -> Dict[str, VdjSongMetadata]:
        """Stream the database once without retaining its XML tree."""
        metadata = {}
        try:
            for _, element in ET.iterparse(self.vdj_database_path, events=("end",)):
                if element.tag != "Song":
                    continue
                path, song_metadata = self._metadata_from_song(element)
                if path:
                    metadata[path] = song_metadata
                element.clear()
            return metadata
        except ET.ParseError:
            # Preserve compatibility with older malformed databases, but only as a
            # fallback. A healthy database stays on the low-memory streaming path.
            root = self.parse_vdj_database()
            if root is None:
                return {}
            for song in root.findall("Song"):
                path, song_metadata = self._metadata_from_song(song)
                if path:
                    metadata[path] = song_metadata
            return metadata

    def _invalidate_metadata_cache(self) -> None:
        with self._vdj_metadata_lock:
            self._vdj_metadata_cache = None
            self._vdj_metadata_fingerprint = None

    def _get_metadata_index(self) -> Dict[str, VdjSongMetadata]:
        fingerprint = self._database_fingerprint()
        if (
            self._vdj_metadata_cache is not None
            and self._vdj_metadata_fingerprint == fingerprint
        ):
            return self._vdj_metadata_cache

        with self._vdj_metadata_lock:
            fingerprint = self._database_fingerprint()
            if (
                self._vdj_metadata_cache is None
                or self._vdj_metadata_fingerprint != fingerprint
            ):
                self._vdj_metadata_cache = self._build_metadata_index()
                self._vdj_metadata_fingerprint = self._database_fingerprint()
            return self._vdj_metadata_cache

    def _get_song_metadata(self, file_path: str) -> Optional[VdjSongMetadata]:
        return self._get_metadata_index().get(self._normalize_database_path(file_path))

    def get_song_bpm_from_database(self, file_path: str) -> Optional[float]:
        """Extract BPM from VDJ database for timing validation"""
        try:
            metadata = self._get_song_metadata(file_path)
            if metadata is None:
                return None

            # Try Scan element first (more accurate)
            if metadata.scan_bpm is not None:
                vdj_bpm = metadata.scan_bpm
                # VDJ normally stores beat duration in seconds, so BPM is 60/value.
                if vdj_bpm > 0:
                    actual_bpm = 60.0 / vdj_bpm
                    if actual_bpm < 60 or actual_bpm > 200:
                        if 60 < vdj_bpm < 200:
                            actual_bpm = vdj_bpm
                            print(
                                f"🎵 VDJ BPM: {vdj_bpm:.6f} (direct) → "
                                f"Actual BPM: {actual_bpm:.1f}"
                            )
                        else:
                            actual_bpm = vdj_bpm * 120
                            if actual_bpm > 200:
                                actual_bpm = 120
                            print(
                                f"🎵 VDJ BPM: {vdj_bpm:.6f} (alt conversion) → "
                                f"Actual BPM: {actual_bpm:.1f}"
                            )
                    else:
                        print(
                            f"🎵 VDJ BPM: {vdj_bpm:.6f} → "
                            f"Actual BPM: {actual_bpm:.1f}"
                        )
                    return actual_bpm

            # Fallback to Tags element
            if metadata.tags_bpm is not None:
                vdj_bpm = metadata.tags_bpm
                if vdj_bpm > 0:
                    actual_bpm = 60.0 / vdj_bpm
                    print(
                        f"🎵 VDJ BPM (Tags): {vdj_bpm:.6f} → "
                        f"Actual BPM: {actual_bpm:.1f}"
                    )
                    return actual_bpm
            return None
        except ET.ParseError as e:
            print(f"⚠️  VDJ database XML is corrupted: {e}")
            print("⚠️  Using fallback BPM estimation")
            return None
        except Exception as e:
            print(f"⚠️  Could not get BPM from database: {e}")
            return None


    def preprocess_xml_for_parsing(self, xml_content: str) -> str:
        """Clean up XML content for Python's ElementTree parser"""
        import re

        # Remove any null bytes or control characters
        # (except tab, newline, carriage return)
        xml_content = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", xml_content)

        # Fix any duplicate closing tags by removing extras
        # This pattern looks for duplicate closing tags like </Song>\n</Song>
        xml_content = re.sub(r"(</[^>]+>)\s*\1+", r"\1", xml_content)

        # Remove any duplicate root closing tags
        xml_content = re.sub(
            r"(</VirtualDJ_Database>)\s*</VirtualDJ_Database>",
            r"\1",
            xml_content,
        )

        # Remove any stray content after the root closing tag
        if "</VirtualDJ_Database>" in xml_content:
            xml_content = (
                xml_content.split("</VirtualDJ_Database>")[0] + "</VirtualDJ_Database>"
            )

        return xml_content

    def parse_vdj_database(self):
        """Parse VDJ database with preprocessing for compatibility"""
        try:
            # Healthy VirtualDJ databases take this low-memory path. Avoid reading
            # and regex-copying the entire file unless compatibility repair is needed.
            return ET.parse(self.vdj_database_path).getroot()
        except ET.ParseError:
            pass

        try:
            # Preserve exact bytes/CRLF; only used on the rare malformed-DB fallback.
            from vdj_database_safety import read_vdj_database_text

            xml_content = read_vdj_database_text(self.vdj_database_path)

            # Preprocess for Python parser compatibility
            cleaned_xml = self.preprocess_xml_for_parsing(xml_content)

            # Parse the cleaned XML
            root = ET.fromstring(cleaned_xml)
            return root
        except Exception as e:
            print(f"⚠️  Could not parse VDJ database: {e}")
            return None

    def _database_integrity_stats(self, database_path) -> Dict[str, int]:
        """Return structural stats used to guard VirtualDJ database writes."""
        return database_integrity_stats(database_path)

    def _validate_database_replacement(
        self, candidate_path, original_stats: Dict[str, int]
    ) -> Dict[str, int]:
        """Reject parseable but structurally broken replacement databases."""
        return validate_database_replacement(candidate_path, original_stats)


    def _validate_file_in_database(self, audio_file_path: str) -> bool:
        """Check if a single file exists in VDJ database"""
        try:
            if self._get_song_metadata(audio_file_path) is not None:
                return True

            print(
                f"❌ File not found in VDJ database: "
                f"{os.path.basename(audio_file_path)}"
            )
            return False

        except Exception as e:
            print(f"❌ Error validating file: {e}")
            import traceback

            traceback.print_exc()
            return False
