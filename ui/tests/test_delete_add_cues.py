"""delete_add_cues_track: trash audio + remove VDJ Song under Add Cues only."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sorter import relocate as relocate_mod
from sorter.relocate import delete_add_cues_track, remove_set_copy, send_set_copy_to_add_cues


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

    def test_rejects_library_copy_even_if_same_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            add = Path(tmp) / "Add Cues"
            zouk = Path(tmp) / "Zouk" / "Chill"
            add.mkdir()
            zouk.mkdir(parents=True)
            lib = zouk / "Give A Little.flac"
            lib.write_bytes(b"lib")
            with self.assertRaisesRegex(ValueError, "Pajamathon set copies"):
                delete_add_cues_track(
                    lib,
                    add_root=add,
                    sets_root=Path(tmp) / "Sets",
                    dry_run=True,
                )

    def test_deletes_pajamathon_set_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            add = Path(tmp) / "Add Cues"
            sets = Path(tmp) / "Sets" / "Pajamathon 2026"
            add.mkdir()
            sets.mkdir(parents=True)
            audio = (sets / "087. Give A Little (Pmak ZRemix).mp3").resolve()
            audio.write_bytes(b"set")
            stems = Path(f"{audio}.vdjstems")
            stems.write_bytes(b"stems")
            keep = (sets / "001. Keep.mp3").resolve()
            keep.write_bytes(b"keep")
            db = Path(tmp) / "database.xml"
            db.write_bytes(
                (
                    "<VirtualDJ_Database>\r\n"
                    f'<Song FilePath="{audio}">\r\n'
                    '  <Poi Name="Intro" Pos="0.0" Num="1" Type="cue" />\r\n'
                    "</Song>\r\n"
                    f'<Song FilePath="{keep}">\r\n'
                    '  <Poi Name="Keep" Pos="1.0" Num="1" Type="cue" />\r\n'
                    "</Song>\r\n"
                    "</VirtualDJ_Database>\r\n"
                ).encode("utf-8")
            )
            with patch("sorter.relocate.is_virtualdj_running", return_value=False):
                result = delete_add_cues_track(
                    audio,
                    add_root=add.resolve(),
                    sets_root=Path(tmp) / "Sets",
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
            text = db.read_text(encoding="utf-8")
            self.assertNotIn(str(audio), text)
            self.assertIn(str(keep), text)

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

    def test_delete_pajamathon_set_keeps_library_hardlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            add = Path(tmp) / "Add Cues"
            sets = Path(tmp) / "Sets" / "Pajamathon 2026"
            zouk = Path(tmp) / "Zouk" / "Energy"
            add.mkdir()
            sets.mkdir(parents=True)
            zouk.mkdir(parents=True)
            audio = (sets / "087. Give A Little (Pmak ZRemix).mp3").resolve()
            audio.write_bytes(b"shared-audio")
            lib = (zouk / "Ash & Naila - Give A Little (Pmak ZRemix).mp3").resolve()
            os.link(audio, lib)
            inbox = add / "Pajamathon" / "Ash & Naila - Give A Little (Pmak ZRemix).mp3"
            inbox.parent.mkdir()
            os.link(audio, inbox)
            db = Path(tmp) / "database.xml"
            db.write_bytes(
                (
                    "<VirtualDJ_Database>\r\n"
                    f'<Song FilePath="{audio}">\r\n'
                    '  <Poi Name="Intro" Pos="0.0" Num="1" Type="cue" />\r\n'
                    "</Song>\r\n"
                    f'<Song FilePath="{lib}">\r\n'
                    '  <Poi Name="Lib" Pos="1.0" Num="1" Type="cue" />\r\n'
                    "</Song>\r\n"
                    "</VirtualDJ_Database>\r\n"
                ).encode("utf-8")
            )
            with patch("sorter.relocate.is_virtualdj_running", return_value=False):
                result = delete_add_cues_track(
                    audio,
                    add_root=add.resolve(),
                    sets_root=Path(tmp) / "Sets",
                    database_path=db,
                    dry_run=False,
                    to_trash=True,
                    create_backup=True,
                )
            self.assertTrue(result["ok"])
            self.assertTrue(result["unlink_only"])
            self.assertEqual(result["kept_hardlinks"], 2)
            self.assertFalse(audio.exists())
            self.assertTrue(lib.is_file())
            self.assertEqual(lib.read_bytes(), b"shared-audio")
            self.assertTrue(inbox.is_file())
            text = db.read_text(encoding="utf-8")
            self.assertNotIn(str(audio), text)
            self.assertIn(str(lib), text)



class RemoveSetCopyTests(unittest.TestCase):
    def test_remove_set_copy_keeps_siblings(self):
        with tempfile.TemporaryDirectory() as tmp:
            add = Path(tmp) / "Add Cues"
            sets = Path(tmp) / "Sets" / "Pajamathon 2026"
            zouk = Path(tmp) / "Zouk" / "Energy" / "Light"
            cues = Path(tmp) / "Cues Sorted" / "Energy" / "Light"
            add.mkdir()
            sets.mkdir(parents=True)
            zouk.mkdir(parents=True)
            cues.mkdir(parents=True)
            audio = (sets / "087. Give A Little.mp3").resolve()
            audio.write_bytes(b"shared-audio")
            lib = (zouk / "Give A Little.mp3").resolve()
            arch = (cues / "Give A Little.mp3").resolve()
            inbox = add / "Pajamathon" / "Give A Little.mp3"
            inbox.parent.mkdir()
            os.link(audio, lib)
            os.link(audio, arch)
            os.link(audio, inbox)
            db = Path(tmp) / "database.xml"
            db.write_bytes(
                (
                    "<VirtualDJ_Database>\r\n"
                    f'<Song FilePath="{audio}">\r\n'
                    '  <Poi Name="Intro" Pos="0.0" Num="1" Type="cue" />\r\n'
                    "</Song>\r\n"
                    f'<Song FilePath="{lib}">\r\n'
                    '  <Poi Name="Lib" Pos="1.0" Num="1" Type="cue" />\r\n'
                    "</Song>\r\n"
                    "</VirtualDJ_Database>\r\n"
                ).encode("utf-8")
            )
            with patch("sorter.relocate.is_virtualdj_running", return_value=False):
                result = remove_set_copy(
                    audio,
                    sets_root=Path(tmp) / "Sets",
                    database_path=db,
                    to_trash=True,
                )
            self.assertTrue(result["ok"])
            self.assertTrue(result["unlink_only"])
            self.assertFalse(audio.exists())
            self.assertTrue(lib.is_file())
            self.assertTrue(arch.is_file())
            self.assertTrue(inbox.is_file())
            self.assertEqual(lib.read_bytes(), b"shared-audio")
            text = db.read_text(encoding="utf-8")
            self.assertNotIn(str(audio), text)
            self.assertIn(str(lib), text)

    def test_remove_rejects_zouk_sibling(self):
        with tempfile.TemporaryDirectory() as tmp:
            sets = Path(tmp) / "Sets"
            zouk = Path(tmp) / "Zouk" / "Chill" / "Lounge"
            sets.mkdir()
            zouk.mkdir(parents=True)
            lib = zouk / "x.mp3"
            lib.write_bytes(b"lib")
            with self.assertRaisesRegex(ValueError, "Sets/"):
                remove_set_copy(lib, sets_root=sets, dry_run=True)


class SendBackSetTests(unittest.TestCase):
    def test_send_back_moves_to_add_cues_keeps_zouk(self):
        with tempfile.TemporaryDirectory() as tmp:
            add = Path(tmp) / "Add Cues"
            sets = Path(tmp) / "Sets" / "Pajamathon 2026"
            zouk = Path(tmp) / "Zouk" / "Energy" / "Light"
            add.mkdir()
            sets.mkdir(parents=True)
            zouk.mkdir(parents=True)
            audio = (sets / "087. Give A Little.mp3").resolve()
            audio.write_bytes(b"set-audio")
            lib = (zouk / "Give A Little.mp3").resolve()
            os.link(audio, lib)
            db = Path(tmp) / "database.xml"
            db.write_bytes(
                (
                    "<VirtualDJ_Database>\r\n"
                    f'<Song FilePath="{audio}">\r\n'
                    '  <Poi Name="Intro" Pos="0.0" Num="1" Type="cue" />\r\n'
                    "</Song>\r\n"
                    f'<Song FilePath="{lib}">\r\n'
                    '  <Poi Name="Lib" Pos="1.0" Num="1" Type="cue" />\r\n'
                    "</Song>\r\n"
                    "</VirtualDJ_Database>\r\n"
                ).encode("utf-8")
            )
            with patch("sorter.relocate.is_virtualdj_running", return_value=False):
                result = send_set_copy_to_add_cues(
                    audio,
                    add_root=add,
                    sets_root=Path(tmp) / "Sets",
                    database_path=db,
                )
            dest = add / "Pajamathon" / "087. Give A Little.mp3"
            self.assertTrue(result["ok"])
            self.assertFalse(audio.exists())
            self.assertTrue(dest.is_file())
            self.assertTrue(lib.is_file())
            self.assertEqual(lib.read_bytes(), b"set-audio")
            self.assertEqual(dest.read_bytes(), b"set-audio")

    def test_send_back_existing_inbox_unlinks_set_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            add = Path(tmp) / "Add Cues" / "Pajamathon"
            sets = Path(tmp) / "Sets" / "Pajamathon 2026"
            zouk = Path(tmp) / "Zouk" / "Chill" / "Lounge"
            add.mkdir(parents=True)
            sets.mkdir(parents=True)
            zouk.mkdir(parents=True)
            audio = (sets / "087. Give A Little.mp3").resolve()
            audio.write_bytes(b"set-audio")
            inbox = (add / "087. Give A Little.mp3").resolve()
            lib = (zouk / "Give A Little.mp3").resolve()
            os.link(audio, inbox)
            os.link(audio, lib)
            with patch("sorter.relocate.is_virtualdj_running", return_value=False):
                result = send_set_copy_to_add_cues(
                    audio,
                    add_root=add.parent,
                    sets_root=Path(tmp) / "Sets",
                    database_path=Path(tmp) / "database.xml",
                )
            self.assertTrue(result["already_in_inbox"])
            self.assertFalse(audio.exists())
            self.assertTrue(inbox.is_file())
            self.assertTrue(lib.is_file())


if __name__ == "__main__":
    unittest.main()
