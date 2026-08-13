"""Rename cue/loop Name attributes via surgical Song XML rewrite."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sorter import cue_edit as cue_mod
from sorter import poi_rename as rename_mod


SAMPLE_SONG = (
    '<Song FilePath="{path}">\r\n'
    '  <Tags Author="A" Title="T" />\r\n'
    '  <Scan Bpm="0.5" Phase="0.1" />\r\n'
    '  <Poi Pos="0.100000" Type="beatgrid" />\r\n'
    '  <Poi Name="Intro" Pos="0.100000" Num="1" Color="4278190335" Type="cue" />\r\n'
    '  <Poi Name="Drop" Pos="32.000000" Num="2" Color="4278255360" Type="cue" />\r\n'
    '  <Poi Name="Loop A" Pos="16.000000" Num="-1" Color="1" Type="loop" Size="16.0" Slot="1" />\r\n'
    '  <Poi Name="Loop B" Pos="48.000000" Num="-1" Color="1" Type="loop" Size="32.0" Slot="2" />\r\n'
    "</Song>\r\n"
)


class PoiRenameXmlTests(unittest.TestCase):
    def test_rename_cue(self):
        xml = SAMPLE_SONG.format(path="/music/a.flac")
        out, ch = rename_mod.set_poi_name_in_song_xml(
            xml, kind="cue", pos=0.1, new_name="Cold Open", num="1"
        )
        self.assertEqual(ch["name_before"], "Intro")
        self.assertEqual(ch["name_after"], "Cold Open")
        self.assertIn('Name="Cold Open"', out)
        self.assertNotIn('Name="Intro"', out)
        self.assertIn('Name="Drop"', out)
        self.assertIn('Type="beatgrid"', out)

    def test_rename_loop(self):
        xml = SAMPLE_SONG.format(path="/music/a.flac")
        out, ch = rename_mod.set_poi_name_in_song_xml(
            xml, kind="loop", pos=16.0, new_name="Build Loop", slot="1"
        )
        self.assertEqual(ch["name_before"], "Loop A")
        self.assertEqual(ch["name_after"], "Build Loop")
        self.assertIn('Name="Build Loop"', out)
        self.assertIn('Name="Loop B"', out)
        self.assertIn('Size="16.0"', out)

    def test_escape_xml_in_name(self):
        xml = SAMPLE_SONG.format(path="/music/a.flac")
        out, ch = rename_mod.set_poi_name_in_song_xml(
            xml, kind="cue", pos=32.0, new_name='A & B "Drop"', num="2"
        )
        self.assertEqual(ch["name_after"], 'A & B "Drop"')
        self.assertIn('Name="A &amp; B &quot;Drop&quot;"', out)

    def test_empty_name_rejected(self):
        xml = SAMPLE_SONG.format(path="/music/a.flac")
        with self.assertRaises(ValueError):
            rename_mod.set_poi_name_in_song_xml(
                xml, kind="cue", pos=0.1, new_name="   ", num="1"
            )

    def test_missing_poi_raises(self):
        xml = SAMPLE_SONG.format(path="/music/a.flac")
        with self.assertRaises(KeyError):
            rename_mod.set_poi_name_in_song_xml(
                xml, kind="cue", pos=99.0, new_name="Nope", num="9"
            )


class PoiRenameWriteTests(unittest.TestCase):
    def test_set_poi_name_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "track.flac"
            audio.write_bytes(b"x")
            path = str(audio.resolve())
            db = root / "database.xml"
            db.write_bytes(
                (
                    "<VirtualDJ_Database>\r\n"
                    + SAMPLE_SONG.format(path=path)
                    + "</VirtualDJ_Database>\r\n"
                ).encode("utf-8")
            )
            with (
                patch.object(cue_mod, "CUES_ROOT", root),
                patch.object(cue_mod, "LIBRARIES", {}),
                patch.object(rename_mod, "CUES_ROOT", root),
                patch.object(rename_mod, "LIBRARIES", {}),
                patch.object(rename_mod, "VDJ_DATABASE", db),
                patch.object(rename_mod, "is_virtualdj_running", return_value=False),
            ):
                result = rename_mod.set_poi_name(
                    audio,
                    kind="cue",
                    pos=0.1,
                    new_name="Renamed Intro",
                    num="1",
                    database_path=db,
                    create_backup=True,
                )
            self.assertTrue(result["ok"])
            self.assertEqual(result["change"]["name_after"], "Renamed Intro")
            text = db.read_text(encoding="utf-8")
            self.assertIn('Name="Renamed Intro"', text)
            self.assertNotIn('Name="Intro"', text)
            self.assertIn('Name="Drop"', text)
            self.assertTrue(Path(result["database_backup"]).is_file())


if __name__ == "__main__":
    unittest.main()
