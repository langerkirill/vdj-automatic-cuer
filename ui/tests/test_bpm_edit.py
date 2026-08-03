"""Halve stored VDJ BPM attributes (musical vs period encodings)."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sorter import bpm_edit as bpm_mod


class HalveStoredBpmTests(unittest.TestCase):
    def test_halve_musical(self):
        self.assertAlmostEqual(bpm_mod.halve_stored_bpm_value(136.0), 68.0)
        self.assertAlmostEqual(bpm_mod.halve_stored_bpm_value(140.0), 70.0)

    def test_halve_period(self):
        # 0.5s beat = 120 BPM → 1.0s = 60 BPM
        self.assertAlmostEqual(bpm_mod.halve_stored_bpm_value(0.5), 1.0)

    def test_apply_to_song_xml_scan(self):
        song = (
            '<Song FilePath="/a.flac">\r\n'
            '  <Tags Author="A" Title="T" Bpm="136" />\r\n'
            '  <Scan Bpm="136" Phase="0.1" />\r\n'
            '  <Poi Pos="0.1" Type="beatgrid" />\r\n'
            "</Song>\r\n"
        )
        out, meta = bpm_mod.apply_bpm_factor_to_song_xml(song, halve=True)
        self.assertTrue(meta["scan"])
        self.assertTrue(meta["tags"])
        self.assertIn('Bpm="68"', out)
        self.assertNotIn('Bpm="136"', out)

    def test_apply_to_song_xml_period(self):
        song = (
            '<Song FilePath="/a.flac">\r\n'
            '  <Scan Bpm="0.441176" Phase="0.0" />\r\n'
            "</Song>\r\n"
        )
        out, meta = bpm_mod.apply_bpm_factor_to_song_xml(song, halve=True)
        self.assertTrue(meta["scan"])
        # ~0.882 period
        self.assertRegex(out, r'Bpm="0\.88')

    def test_halve_track_bpm_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "t.flac"
            audio.write_bytes(b"x")
            db = root / "database.xml"
            db.write_bytes(
                (
                    "<VirtualDJ_Database>\r\n"
                    f'<Song FilePath="{audio.resolve()}">\r\n'
                    '  <Scan Bpm="136" Phase="0.05" />\r\n'
                    '  <Poi Pos="0.05" Type="beatgrid" />\r\n'
                    "</Song>\r\n"
                    "</VirtualDJ_Database>\r\n"
                ).encode("utf-8")
            )
            with patch.object(bpm_mod, "CUES_ROOT", root), patch.object(
                bpm_mod, "LIBRARIES", {}
            ), patch.object(bpm_mod, "VDJ_DATABASE", db), patch(
                "sorter.bpm_edit.is_virtualdj_running", return_value=False
            ):
                result = bpm_mod.halve_track_bpm(
                    audio, database_path=db, dry_run=True, create_backup=False
                )
            self.assertTrue(result["ok"])
            self.assertEqual(result["bpm_before"], 136.0)
            self.assertEqual(result["bpm_after"], 68.0)

    def test_halve_track_bpm_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "t.flac"
            audio.write_bytes(b"x")
            db = root / "database.xml"
            path = str(audio.resolve())
            db.write_bytes(
                (
                    "<VirtualDJ_Database>\r\n"
                    f'<Song FilePath="{path}">\r\n'
                    '  <Scan Bpm="136" Phase="0.05" />\r\n'
                    '  <Poi Pos="0.05" Type="beatgrid" />\r\n'
                    "</Song>\r\n"
                    "</VirtualDJ_Database>\r\n"
                ).encode("utf-8")
            )
            with patch.object(bpm_mod, "CUES_ROOT", root), patch.object(
                bpm_mod, "LIBRARIES", {}
            ), patch.object(bpm_mod, "VDJ_DATABASE", db), patch(
                "sorter.bpm_edit.is_virtualdj_running", return_value=False
            ):
                result = bpm_mod.halve_track_bpm(
                    audio, database_path=db, dry_run=False, create_backup=True
                )
            self.assertEqual(result["bpm_after"], 68.0)
            text = db.read_text(encoding="utf-8")
            self.assertIn('Bpm="68"', text)
            self.assertTrue(Path(result["database_backup"]).is_file())


if __name__ == "__main__":
    unittest.main()
