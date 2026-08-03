"""Surgical FilePath relocate for moving cued tracks between folders."""

import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from vdj_database_safety import (
    _find_song_span,
    clone_song_entry_to_path,
    read_vdj_database_text,
    relocate_song_filepath_in_database,
    song_xml_with_new_filepath,
)


def sample_crlf_database() -> str:
    return (
        "<VirtualDJ_Database>\r\n"
        '<Song FilePath="/music/Ready For Sort/track.flac" Flag="1">\r\n'
        '  <Tags Author="Artist" Title="Song" />\r\n'
        '  <Scan Bpm="0.5" Phase="0.1" />\r\n'
        '  <Infos SongLength="180.0" />\r\n'
        '  <Poi Type="automix" Point="realStart" />\r\n'
        '  <Poi Pos="0.25" Type="beatgrid" />\r\n'
        '  <Poi Name="Intro" Pos="0.25" Num="1" Color="4278190335" Type="cue" />\r\n'
        '  <Poi Name="Loop" Pos="16.0" Num="-1" Color="4278255360" Type="loop" Size="16.0" Slot="1" />\r\n'
        "  <Comment>blue</Comment>\r\n"
        "</Song>\r\n"
        '<Song FilePath="/music/keep.flac">\r\n'
        '  <Poi Name="KeepMe" Pos="1.0" Num="1" Color="1" Type="cue" />\r\n'
        "</Song>\r\n"
        "</VirtualDJ_Database>\r\n"
    )


class FilePathRelocateTests(unittest.TestCase):
    def test_song_xml_with_new_filepath_only_changes_open_tag(self):
        original = (
            '<Song FilePath="/old/path.flac" Flag="1">\r\n'
            '  <Poi Name="Intro" Pos="0.25" Num="1" Color="1" Type="cue" />\r\n'
            "</Song>"
        )
        updated = song_xml_with_new_filepath(original, "/new/folder/path.flac")
        self.assertIn('FilePath="/new/folder/path.flac"', updated)
        self.assertNotIn("/old/path.flac", updated)
        self.assertIn('Flag="1"', updated)
        self.assertIn('Name="Intro"', updated)
        self.assertTrue(updated.endswith("</Song>"))

    def test_song_xml_escapes_ampersands_in_new_path(self):
        original = '<Song FilePath="/old.flac">\r\n</Song>'
        updated = song_xml_with_new_filepath(original, "/music/a & b.flac")
        self.assertIn('FilePath="/music/a &amp; b.flac"', updated)

    def test_relocate_preserves_cues_crlf_and_other_songs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "database.xml"
            path.write_bytes(sample_crlf_database().encode("utf-8"))

            old = "/music/Ready For Sort/track.flac"
            new = "/music/House/Chill/track.flac"
            relocate_song_filepath_in_database(path, old, new)

            raw = path.read_bytes()
            self.assertIn(b"\r\n", raw)
            self.assertNotIn(old.encode("utf-8"), raw)
            self.assertIn(new.encode("utf-8"), raw)

            content = read_vdj_database_text(path)
            self.assertIsNone(_find_song_span(content, old))
            span = _find_song_span(content, new)
            self.assertIsNotNone(span)
            song_xml = content[span[0] : span[1]]
            self.assertIn('Name="Intro"', song_xml)
            self.assertIn('Type="loop"', song_xml)
            self.assertIn('Type="beatgrid"', song_xml)
            self.assertIn("<Comment>blue</Comment>", song_xml)
            self.assertIn('<Scan Bpm="0.5" Phase="0.1" />', song_xml)

            # Neighbor song untouched
            keep = _find_song_span(content, "/music/keep.flac")
            self.assertIsNotNone(keep)
            self.assertIn("KeepMe", content[keep[0] : keep[1]])

            # Parsed shape still valid
            root = ET.fromstring(content.replace("\r\n", "\n"))
            paths = [song.get("FilePath") for song in root.findall("Song")]
            self.assertEqual(paths, [new, "/music/keep.flac"])

    def test_relocate_missing_song_raises(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "database.xml"
            path.write_bytes(sample_crlf_database().encode("utf-8"))
            with self.assertRaises(KeyError):
                relocate_song_filepath_in_database(
                    path, "/missing.flac", "/elsewhere.flac"
                )

    def test_clone_song_entry_preserves_cues(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "database.xml"
            path.write_bytes(sample_crlf_database().encode("utf-8"))
            source = "/music/Ready For Sort/track.flac"
            clone = "/music/Cues Sorted/Chill/track.flac"
            result = clone_song_entry_to_path(path, source, clone)
            self.assertTrue(result["cloned"])

            content = read_vdj_database_text(path)
            self.assertIsNotNone(_find_song_span(content, source))
            span = _find_song_span(content, clone)
            self.assertIsNotNone(span)
            song_xml = content[span[0] : span[1]]
            self.assertIn('Name="Intro"', song_xml)
            self.assertIn('Type="loop"', song_xml)
            self.assertIn(b"\r\n", path.read_bytes())

            # Second clone is a no-op when skip_if_exists
            again = clone_song_entry_to_path(path, source, clone)
            self.assertFalse(again["cloned"])
            self.assertTrue(again["already_present"])


if __name__ == "__main__":
    unittest.main()
