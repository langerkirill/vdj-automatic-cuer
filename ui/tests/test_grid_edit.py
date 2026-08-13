"""Beatgrid anchor rewrite in Song XML."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sorter import grid_edit as grid_mod

SAMPLE_SONG = (
    '<Song FilePath="{path}">\r\n'
    '  <Tags Author="A" Title="T" Bpm="0.5" />\r\n'
    '  <Scan Bpm="0.5" Phase="0.100000" />\r\n'
    '  <Poi Pos="0.100000" Type="beatgrid" />\r\n'
    '  <Poi Name="Intro" Pos="0.100000" Num="1" Type="cue" />\r\n'
    "</Song>\r\n"
)

SAMPLE_NO_BG = (
    '<Song FilePath="{path}">\r\n'
    '  <Tags Author="A" Title="T" />\r\n'
    '  <Scan Bpm="0.46875" Phase="1.25" />\r\n'
    '  <Poi Name="Intro" Pos="1.25" Num="1" Type="cue" />\r\n'
    "</Song>\r\n"
)


class GridEditXmlTests(unittest.TestCase):
    def test_updates_phase_and_beatgrid_poi(self):
        xml = SAMPLE_SONG.format(path="/music/a.flac")
        out, meta = grid_mod.apply_beatgrid_anchor_to_song_xml(xml, 0.55)
        self.assertTrue(meta["scan_phase_updated"])
        self.assertTrue(meta["beatgrid_poi_updated"])
        self.assertIn('Phase="0.55"', out)
        self.assertIn('Pos="0.55" Type="beatgrid"', out.replace("  ", " "))
        self.assertIn('Type="beatgrid"', out)
        self.assertIn('Name="Intro"', out)

    def test_creates_beatgrid_poi_when_missing(self):
        xml = SAMPLE_NO_BG.format(path="/music/a.flac")
        out, meta = grid_mod.apply_beatgrid_anchor_to_song_xml(xml, 2.0)
        self.assertTrue(meta["beatgrid_poi_created"])
        self.assertIn('Phase="2.0"', out)
        self.assertIn('Type="beatgrid"', out)
        self.assertIn('Pos="2.0"', out)


class GridEditWriteTests(unittest.TestCase):
    def test_set_beatgrid_anchor_writes(self):
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
            with patch.object(grid_mod, "CUES_ROOT", root), patch.object(
                grid_mod, "LIBRARIES", {}
            ), patch.object(grid_mod, "VDJ_DATABASE", db), patch(
                "sorter.grid_edit.is_virtualdj_running", return_value=False
            ):
                result = grid_mod.set_beatgrid_anchor(
                    audio,
                    anchor_seconds=0.42,
                    database_path=db,
                    create_backup=True,
                )
            self.assertTrue(result["ok"])
            self.assertAlmostEqual(result["anchor"], 0.42)
            text = db.read_text(encoding="utf-8")
            self.assertIn('Phase="0.42"', text)
            self.assertIn('Type="beatgrid"', text)
            self.assertIn("0.42", text)
            self.assertTrue(Path(result["database_backup"]).is_file())


if __name__ == "__main__":
    unittest.main()
