"""VdjDatabaseMixin for AutomaticMusicCuer."""

from .common import *


class VdjDatabaseMixin:
    def get_song_bpm_from_database(self, file_path: str) -> Optional[float]:
        """Extract BPM from VDJ database for timing validation"""
        try:
            root = self.parse_vdj_database()
            if root is None:
                return None

            for song in root.findall("Song"):
                if song.get("FilePath") == file_path:
                    # Try Scan element first (more accurate)
                    scan = song.find("Scan")
                    if scan is not None:
                        bpm_str = scan.get("Bpm", "0")
                        vdj_bpm = float(bpm_str)
                        # VDJ stores BPM as fractional value, convert to actual
                        # Formula: actual_bpm = 60 / vdj_bpm (approximately)
                        if vdj_bpm > 0:
                            actual_bpm = 60.0 / vdj_bpm
                            # Sanity check - if BPM seems wrong, try different
                            if actual_bpm < 60 or actual_bpm > 200:
                                # Try alternative: maybe it's already in BPM
                                if vdj_bpm > 60 and vdj_bpm < 200:
                                    actual_bpm = vdj_bpm
                                    print(
                                        f"🎵 VDJ BPM: {vdj_bpm:.6f} (direct) → "
                                        f"Actual BPM: {actual_bpm:.1f}"
                                    )
                                else:
                                    # Try another common conversion
                                    actual_bpm = vdj_bpm * 120
                                    if actual_bpm > 200:
                                        actual_bpm = 120  # fallback
                                    print(
                                        f"🎵 VDJ BPM: {vdj_bpm:.6f} (alt "
                                        f"conversion) → Actual BPM: "
                                        f"{actual_bpm:.1f}"
                                    )
                            else:
                                print(
                                    f"🎵 VDJ BPM: {vdj_bpm:.6f} → Actual BPM: "
                                    f"{actual_bpm:.1f}"
                                )
                            return actual_bpm

                    # Fallback to Tags element
                    tags = song.find("Tags")
                    if tags is not None:
                        bpm_str = tags.get("Bpm", "0")
                        vdj_bpm = float(bpm_str)
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
            # Read the raw XML content
            with open(self.vdj_database_path, "r", encoding="utf-8") as f:
                xml_content = f.read()

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

            root = self.parse_vdj_database()
            if root is None:
                return False

            import unicodedata

            normalized_target = unicodedata.normalize("NFC", audio_file_path)

            for song in root.findall("Song"):
                db_path = song.get("FilePath", "")
                normalized_db_path = unicodedata.normalize("NFC", db_path)

                if normalized_db_path == normalized_target:
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

