"""Must Play persist flag plus a copy in Sets/Pajamathon/Must Play."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sorter import set_must_play as sm


class SetMustPlayStoreTests(unittest.TestCase):
    def test_mark_and_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sets = Path(tmp) / "Sets"
            event = sets / "Pajamathon 2026"
            event.mkdir(parents=True)
            audio = event / "001. nobody.m4a"
            audio.write_bytes(b"x")
            store = Path(tmp) / "must_play.json"
            rec = sm.mark_must_play(audio, store_path=store, sets_root=sets)
            self.assertEqual(rec["key"], "Pajamathon 2026/001. nobody.m4a")
            self.assertTrue(sm.has_must_play(audio, store_path=store, sets_root=sets))
            self.assertIn(str(audio.resolve()), sm.must_play_file_paths(store_path=store))
            copy = event / "Must Play" / audio.name
            self.assertTrue(copy.is_file())
            self.assertTrue(audio.is_file())
            self.assertEqual(copy.read_bytes(), b"x")
            self.assertEqual(Path(rec["folder_copy"]), copy.resolve())

    def test_mark_copies_stems_and_skips_existing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sets = Path(tmp) / "Sets"
            event = sets / "Pajamathon 2026"
            folder = event / "Must Play"
            folder.mkdir(parents=True)
            audio = event / "030. Changes Trimmed.wav"
            audio.write_bytes(b"song")
            Path(f"{audio}.vdjstems").write_bytes(b"stems")
            store = Path(tmp) / "must_play.json"
            first = sm.mark_must_play(audio, store_path=store, sets_root=sets)
            copy = Path(first["folder_copy"])
            self.assertTrue(Path(f"{copy}.vdjstems").is_file())
            copy.write_bytes(b"keep")
            second = sm.mark_must_play(audio, store_path=store, sets_root=sets)
            self.assertEqual(Path(second["folder_copy"]), copy)
            self.assertEqual(copy.read_bytes(), b"keep")

    def test_sync_copies_stamped_tracks_missing_from_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sets = Path(tmp) / "Sets"
            event = sets / "Pajamathon 2026"
            event.mkdir(parents=True)
            audio = event / "079. Same Place.flac"
            audio.write_bytes(b"joy")
            store = Path(tmp) / "must_play.json"
            sm.mark_must_play(audio, store_path=store, sets_root=sets)
            copy = event / "Must Play" / audio.name
            copy.unlink()
            result = sm.sync_must_play_folder(store_path=store, sets_root=sets)
            self.assertTrue(copy.is_file())
            self.assertEqual(result["copied"], 1)

    def test_inbox_file_cannot_be_must_play(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            inbox = Path(tmp) / "Add Cues" / "x.flac"
            inbox.parent.mkdir(parents=True)
            inbox.write_bytes(b"x")
            with self.assertRaises(ValueError):
                sm.mark_must_play(
                    inbox,
                    store_path=Path(tmp) / "m.json",
                    sets_root=Path(tmp) / "Sets",
                )


from sorter.library import is_must_play_folder_path


class MustPlayFolderHideTests(unittest.TestCase):
    def test_detects_must_play_folder(self) -> None:
        self.assertTrue(is_must_play_folder_path("Pajamathon 2026/Must Play/Children.m4a"))
        self.assertFalse(is_must_play_folder_path("Pajamathon 2026/001. nobody.m4a"))
