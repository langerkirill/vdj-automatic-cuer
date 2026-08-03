"""Delete individual cue/loop markers from VDJ Song XML."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sorter import cue_edit as cue_mod


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


class CueEditXmlTests(unittest.TestCase):
    def test_remove_cue_by_pos_and_num(self):
        xml = SAMPLE_SONG.format(path="/music/a.flac")
        out, removed = cue_mod.remove_manual_poi_from_song_xml(
            xml, kind="cue", pos=32.0, num="2"
        )
        self.assertEqual(removed["name"], "Drop")
        self.assertNotIn('Name="Drop"', out)
        self.assertIn('Name="Intro"', out)
        self.assertIn('Type="beatgrid"', out)
        self.assertIn('Name="Loop A"', out)

    def test_remove_loop_keeps_other_loop(self):
        xml = SAMPLE_SONG.format(path="/music/a.flac")
        out, removed = cue_mod.remove_manual_poi_from_song_xml(
            xml, kind="loop", pos=16.0, num="-1", name="Loop A"
        )
        self.assertEqual(removed["name"], "Loop A")
        self.assertNotIn('Name="Loop A"', out)
        self.assertIn('Name="Loop B"', out)
        self.assertIn('Name="Intro"', out)

    def test_remove_missing_raises(self):
        xml = SAMPLE_SONG.format(path="/music/a.flac")
        with self.assertRaises(KeyError):
            cue_mod.remove_manual_poi_from_song_xml(xml, kind="cue", pos=99.0, num="9")


class CueEditDeleteTests(unittest.TestCase):
    def test_delete_cue_point_writes(self):
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
            with patch.object(cue_mod, "CUES_ROOT", root), patch.object(
                cue_mod, "LIBRARIES", {}
            ), patch.object(cue_mod, "VDJ_DATABASE", db), patch(
                "sorter.cue_edit.is_virtualdj_running", return_value=False
            ):
                result = cue_mod.delete_cue_point(
                    audio,
                    kind="cue",
                    pos=0.1,
                    num="1",
                    database_path=db,
                    create_backup=True,
                )
            self.assertTrue(result["ok"])
            self.assertEqual(result["removed"]["name"], "Intro")
            self.assertEqual(result["cue_count_after"], 1)
            text = db.read_text(encoding="utf-8")
            self.assertNotIn('Name="Intro"', text)
            self.assertIn('Name="Drop"', text)
            self.assertIn('Type="beatgrid"', text)
            self.assertTrue(Path(result["database_backup"]).is_file())


if __name__ == "__main__":
    unittest.main()
