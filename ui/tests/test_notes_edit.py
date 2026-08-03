"""VirtualDJ Comment notes surgical rewrite."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sorter import notes_edit as notes_mod


class NotesXmlTests(unittest.TestCase):
    def test_insert_comment(self):
        song = (
            '<Song FilePath="/a.flac">\r\n'
            '  <Tags Author="A" Title="T" />\r\n'
            '  <Scan Bpm="0.5" />\r\n'
            "</Song>\r\n"
        )
        out = notes_mod.apply_comment_to_song_xml(song, "hello & world")
        self.assertIn("<Comment>hello &amp; world</Comment>", out)
        self.assertIn("</Song>", out)

    def test_replace_comment(self):
        song = (
            '<Song FilePath="/a.flac">\r\n'
            "  <Comment>old</Comment>\r\n"
            "</Song>\r\n"
        )
        out = notes_mod.apply_comment_to_song_xml(song, "new note")
        self.assertIn("<Comment>new note</Comment>", out)
        self.assertNotIn("old", out)
        self.assertEqual(out.count("<Comment>"), 1)

    def test_clear_comment(self):
        song = (
            '<Song FilePath="/a.flac">\r\n'
            "  <Comment>gone</Comment>\r\n"
            '  <Poi Pos="0.1" Type="beatgrid" />\r\n'
            "</Song>\r\n"
        )
        out = notes_mod.apply_comment_to_song_xml(song, "  ")
        self.assertNotIn("<Comment>", out)
        self.assertIn('Type="beatgrid"', out)


class NotesWriteTests(unittest.TestCase):
    def test_set_track_comment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "t.flac"
            audio.write_bytes(b"x")
            path = str(audio.resolve())
            db = root / "database.xml"
            db.write_bytes(
                (
                    "<VirtualDJ_Database>\r\n"
                    f'<Song FilePath="{path}">\r\n'
                    '  <Tags Author="A" Title="T" />\r\n'
                    '  <Scan Bpm="0.5" Phase="0.1" />\r\n'
                    '  <Poi Pos="0.1" Type="beatgrid" />\r\n'
                    '  <Poi Name="Intro" Pos="0.1" Num="1" Color="4278190335" Type="cue" />\r\n'
                    "</Song>\r\n"
                    "</VirtualDJ_Database>\r\n"
                ).encode("utf-8")
            )
            with patch.object(notes_mod, "CUES_ROOT", root), patch.object(
                notes_mod, "LIBRARIES", {}
            ), patch.object(notes_mod, "VDJ_DATABASE", db), patch(
                "sorter.notes_edit.is_virtualdj_running", return_value=False
            ):
                result = notes_mod.set_track_comment(
                    audio, "zouk opener · warm", database_path=db
                )
            self.assertTrue(result["ok"])
            self.assertEqual(result["comment"], "zouk opener · warm")
            text = db.read_text(encoding="utf-8")
            self.assertIn("<Comment>zouk opener · warm</Comment>", text)
            self.assertIn('Type="cue"', text)


if __name__ == "__main__":
    unittest.main()
