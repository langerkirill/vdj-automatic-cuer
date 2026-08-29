"""VirtualDJ requires CRLF database.xml; LF-only files get reset on open."""

import tempfile
import unittest
from pathlib import Path

from vdj_database_safety import (
    format_vdj_poi_line,
    inject_pois_into_song_xml,
    read_vdj_database_text,
    rewrite_song_xml_in_database,
)


class CrlfPreservationTests(unittest.TestCase):
    def test_read_vdj_database_text_keeps_carriage_returns(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "database.xml"
            raw = (
                b'<VirtualDJ_Database Version="2025">\r\n'
                b'<Song FilePath="/music/a.flac">\r\n'
                b'  <Scan Bpm="0.5" />\r\n'
                b"</Song>\r\n"
                b"</VirtualDJ_Database>\r\n"
            )
            path.write_bytes(raw)
            text = read_vdj_database_text(path)
            self.assertIn("\r\n", text)
            self.assertEqual(text.encode("utf-8"), raw)

    def test_read_strips_trailing_nul_so_color_edits_can_parse(self):
        """VDJ/writers sometimes leave a 0x00 after </VirtualDJ_Database>."""
        import xml.etree.ElementTree as ET

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "database.xml"
            raw = (
                b'<VirtualDJ_Database Version="2025">\r\n'
                b'<Song FilePath="/music/a.flac">\r\n'
                b'  <Poi Name="Intro" Pos="0.1" Num="1" Color="1" Type="cue" />\r\n'
                b"</Song>\r\n"
                b"</VirtualDJ_Database>\r\n"
                b"\x00"
            )
            path.write_bytes(raw)
            text = read_vdj_database_text(path)
            self.assertNotIn("\x00", text)
            self.assertTrue(text.rstrip().endswith("</VirtualDJ_Database>"))
            ET.fromstring(text.replace("\r\n", "\n"))

    def test_rewrite_preserves_crlf_and_song_count(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "database.xml"
            original = (
                '<VirtualDJ_Database Version="2025">\r\n'
                '<Song FilePath="/music/keep.flac">\r\n'
                '  <Scan Bpm="0.5" />\r\n'
                '  <Poi Name="Old" Pos="1.0" Num="1" Color="1" Type="cue" />\r\n'
                "</Song>\r\n"
                '<Song FilePath="/music/target.flac">\r\n'
                '  <Tags Title="T" />\r\n'
                '  <Scan Bpm="0.5" Phase="0.1" />\r\n'
                '  <Poi Type="automix" Point="realStart" />\r\n'
                '  <Poi Pos="0.25" Type="beatgrid" />\r\n'
                "</Song>\r\n"
                "</VirtualDJ_Database>\r\n"
            )
            path.write_bytes(original.encode("utf-8"))

            song_xml = (
                '<Song FilePath="/music/target.flac">\r\n'
                '  <Tags Title="T" />\r\n'
                '  <Scan Bpm="0.5" Phase="0.1" />\r\n'
                '  <Poi Type="automix" Point="realStart" />\r\n'
                '  <Poi Pos="0.25" Type="beatgrid" />\r\n'
                "</Song>"
            )
            poi = format_vdj_poi_line(
                pos=12.5,
                poi_type="cue",
                num="1",
                color="4278190335",
                name="Intro",
                newline="\r\n",
            )
            updated_song = inject_pois_into_song_xml(song_xml, [poi], comment="blue")
            rewrite_song_xml_in_database(path, "/music/target.flac", updated_song)

            raw = path.read_bytes()
            self.assertGreater(raw.count(b"\r\n"), 5)
            self.assertIn(b'Name="Intro"', raw)
            self.assertIn(b'<Scan Bpm="0.5" Phase="0.1" />', raw)
            self.assertIn(b"/music/keep.flac", raw)
            self.assertNotIn(b"<?xml", raw)
            # Must not become LF-only
            self.assertEqual(raw.count(b"\n") - raw.count(b"\r\n"), 0)


if __name__ == "__main__":
    unittest.main()
