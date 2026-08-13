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

    def test_scale_loop_half_and_double(self):
        xml = SAMPLE_SONG.format(path="/music/a.flac")
        half, ch = cue_mod.scale_loop_size_in_song_xml(
            xml, pos=16.0, factor=0.5, num="-1", slot="1"
        )
        self.assertEqual(ch["beats_before"], 16.0)
        self.assertEqual(ch["beats_after"], 8.0)
        self.assertIn('Size="8.0"', half)
        self.assertIn('Size="32.0"', half)  # other loop unchanged

        doubled, ch2 = cue_mod.scale_loop_size_in_song_xml(
            half, pos=16.0, factor=2.0, num="-1", slot="1"
        )
        self.assertEqual(ch2["beats_after"], 16.0)
        self.assertIn('Size="16.0"', doubled)

    def test_set_poi_color_cue_and_loop(self):
        xml = SAMPLE_SONG.format(path="/music/a.flac")
        out, ch = cue_mod.set_poi_color_in_song_xml(
            xml, kind="cue", pos=0.1, color="orange", num="1"
        )
        self.assertEqual(ch["color_name"], "orange")
        self.assertIn(f'Color="{cue_mod.VDJ_CUE_COLORS["orange"]}"', out)
        self.assertIn('Name="Intro"', out)
        # Loop color change
        out2, ch2 = cue_mod.set_poi_color_in_song_xml(
            out, kind="loop", pos=16.0, color="purple", slot="1"
        )
        self.assertEqual(ch2["color_name"], "purple")
        self.assertIn(f'Color="{cue_mod.VDJ_CUE_COLORS["purple"]}"', out2)

    def test_set_poi_position_moves_loop(self):
        xml = SAMPLE_SONG.format(path="/music/a.flac")
        out, ch = cue_mod.set_poi_position_in_song_xml(
            xml, kind="loop", pos=16.0, new_pos=20.5, slot="1"
        )
        self.assertAlmostEqual(ch["pos_before"], 16.0)
        self.assertAlmostEqual(ch["pos_after"], 20.5)
        self.assertIn('Pos="20.5"', out)
        self.assertIn('Name="Loop A"', out)
        self.assertIn('Pos="48.000000"', out)  # other loop unchanged


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

    def test_scale_loop_point_writes(self):
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
                result = cue_mod.scale_loop_point(
                    audio,
                    pos=48.0,
                    factor=0.5,
                    num="-1",
                    slot="2",
                    database_path=db,
                    create_backup=True,
                )
            self.assertTrue(result["ok"])
            self.assertEqual(result["change"]["beats_after"], 16.0)
            text = db.read_text(encoding="utf-8")
            self.assertIn('Name="Loop B"', text)
            self.assertIn('Size="16.0"', text)
            # Loop A still 16
            self.assertRegex(text, r'Name="Loop A"[^>]*Size="16\.0"')


if __name__ == "__main__":
    unittest.main()
