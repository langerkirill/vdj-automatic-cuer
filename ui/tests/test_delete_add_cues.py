"""delete_add_cues_track: trash audio + remove VDJ Song under Add Cues only."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sorter import relocate as relocate_mod
from sorter.relocate import delete_add_cues_track


class DeleteAddCuesTrackTests(unittest.TestCase):
    def test_rejects_paths_outside_add_cues(self):
        with tempfile.TemporaryDirectory() as tmp:
            add = Path(tmp) / "Add Cues"
            add.mkdir()
            outsider = Path(tmp) / "other.flac"
            outsider.write_bytes(b"x")
            with self.assertRaises(ValueError):
                delete_add_cues_track(
                    outsider,
                    add_root=add,
                    dry_run=True,
                )

    def test_dry_run_lists_audio_and_stems(self):
        with tempfile.TemporaryDirectory() as tmp:
            add = Path(tmp) / "Add Cues"
            add.mkdir()
            audio = (add / "track.flac").resolve()
            audio.write_bytes(b"audio")
            stems = Path(f"{audio}.vdjstems")
            stems.write_bytes(b"stems")
            db = Path(tmp) / "database.xml"
            db.write_bytes(
                (
                    "<VirtualDJ_Database>\r\n"
                    f'<Song FilePath="{audio}">\r\n'
                    '  <Poi Name="Intro" Pos="0.0" Num="1" Type="cue" />\r\n'
                    "</Song>\r\n"
                    "</VirtualDJ_Database>\r\n"
                ).encode("utf-8")
            )
            with patch("sorter.relocate.is_virtualdj_running", return_value=False):
                result = delete_add_cues_track(
                    audio,
                    add_root=add.resolve(),
                    database_path=db,
                    dry_run=True,
                )
            self.assertTrue(result["ok"])
            self.assertTrue(result["dry_run"])
            self.assertIn(str(audio), result["removed_files"])
            self.assertIn(str(stems), result["removed_files"])
            self.assertTrue(audio.is_file())
            self.assertTrue(stems.is_file())

    def test_deletes_files_and_song_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            add = root / "Add Cues"
            add.mkdir()
            audio = (add / "drop.flac").resolve()
            audio.write_bytes(b"audio")
            stems = Path(f"{audio}.vdjstems")
            stems.write_bytes(b"stems")
            keep = (add / "keep.flac").resolve()
            keep.write_bytes(b"keep")
            db = root / "database.xml"
            db.write_bytes(
                (
                    "<VirtualDJ_Database>\r\n"
                    f'<Song FilePath="{audio}" Flag="1">\r\n'
                    '  <Tags Author="A" Title="T" />\r\n'
                    '  <Scan Bpm="0.5" />\r\n'
                    '  <Poi Pos="0.1" Type="beatgrid" />\r\n'
                    '  <Poi Name="Intro" Pos="0.1" Num="1" Color="4278190335" Type="cue" />\r\n'
                    '  <Poi Name="Loop" Pos="8.0" Num="-1" Color="1" Type="loop" Size="16.0" Slot="1" />\r\n'
                    "</Song>\r\n"
                    f'<Song FilePath="{keep}">\r\n'
                    '  <Poi Pos="0.1" Type="beatgrid" />\r\n'
                    '  <Poi Name="Keep" Pos="1.0" Num="1" Type="cue" />\r\n'
                    "</Song>\r\n"
                    "</VirtualDJ_Database>\r\n"
                ).encode("utf-8")
            )

            with patch(
                "sorter.relocate.is_virtualdj_running", return_value=False
            ), patch.object(relocate_mod, "VDJ_DATABASE", db):
                result = delete_add_cues_track(
                    audio,
                    add_root=add.resolve(),
                    database_path=db,
                    dry_run=False,
                    to_trash=False,
                    create_backup=True,
                )

            self.assertTrue(result["ok"])
            self.assertFalse(audio.exists())
            self.assertFalse(stems.exists())
            self.assertTrue(keep.exists())
            self.assertTrue(result["database"]["removed_from_db"])
            self.assertEqual(result["had_cues"], 1)
            self.assertEqual(result["had_loops"], 1)
            text = db.read_text(encoding="utf-8")
            self.assertNotIn(str(audio), text)
            self.assertIn(str(keep), text)
            self.assertIn('Name="Keep"', text)
            self.assertNotIn('Name="Intro"', text)


if __name__ == "__main__":
    unittest.main()
