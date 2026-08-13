"""Folder discovery and create-folder safety."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sorter import library as library_mod


class LibraryTests(unittest.TestCase):
    def test_create_folder_at_root_and_nested(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            house = root / "House"
            zouk = root / "Zouk"
            chill = zouk / "Chill"
            house.mkdir()
            chill.mkdir(parents=True)

            with patch.dict(
                library_mod.LIBRARIES, {"House": house, "Zouk": zouk}, clear=True
            ):
                created = library_mod.create_folder("House", name="Dreamy")
                self.assertEqual(created["relative_path"], "Dreamy")
                self.assertTrue((house / "Dreamy").is_dir())

                nested = library_mod.create_folder(
                    "Zouk", name="Amber", parent_relative_path="Chill"
                )
                self.assertEqual(nested["relative_path"], "Chill/Amber")
                self.assertTrue((chill / "Amber").is_dir())

    def test_create_folder_rejects_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            house = root / "House"
            house.mkdir()
            with patch.dict(library_mod.LIBRARIES, {"House": house}, clear=True):
                with self.assertRaises(ValueError):
                    library_mod.create_folder("House", name="..")

    def test_tree_includes_nested_and_skips_backups(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            zouk = root / "Zouk"
            (zouk / "Chill" / "Mystical").mkdir(parents=True)
            (zouk / "Chill" / "low_quality_backups").mkdir()
            (zouk / "Chill" / "Mystical" / "song.flac").write_bytes(b"x")
            with patch.dict(library_mod.LIBRARIES, {"Zouk": zouk}, clear=True):
                tree = library_mod.list_library_tree("Zouk")
                paths = library_mod.flatten_folder_paths(tree["folders"])
                self.assertIn("Chill", paths)
                self.assertIn("Chill/Mystical", paths)
                self.assertNotIn("Chill/low_quality_backups", paths)

    def test_list_ready_tracks_ignores_stems(self):
        with tempfile.TemporaryDirectory() as tmp:
            ready = Path(tmp)
            (ready / "a.flac").write_bytes(b"audio")
            (ready / "a.flac.vdjstems").write_bytes(b"stems")
            (ready / "notes.txt").write_text("x")
            tracks = library_mod.list_ready_tracks(ready)
            self.assertEqual(len(tracks), 1)
            self.assertEqual(tracks[0].name, "a.flac")
            self.assertTrue(tracks[0].stems_path.endswith(".vdjstems"))

    def test_find_cues_sorted_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "Cues Sorted" / "Tribal"
            archive.mkdir(parents=True)
            (archive / "song.flac").write_bytes(b"x")
            with patch.object(library_mod, "CUES_SORTED", root / "Cues Sorted"):
                hits = library_mod.find_cues_sorted_matches("song.flac")
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0]["relative_path"], "Tribal/song.flac")

    def test_list_add_cues_tracks_recursive_and_skips_junk(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch = root / "Screenshots 7-15-26"
            batch.mkdir()
            (batch / "song.m4a").write_bytes(b"a")
            junk = root / ".temp_download"
            junk.mkdir()
            (junk / "ignore.flac").write_bytes(b"b")
            (root / "Playlists").mkdir()
            (root / "artist").mkdir()
            (root / "artist" / "tune.flac").write_bytes(b"c")
            tracks = library_mod.list_add_cues_tracks(root)
            names = sorted(t.name for t in tracks)
            self.assertEqual(names, ["song.m4a", "tune.flac"])
            groups = {t.name: t.group for t in tracks}
            self.assertEqual(groups["song.m4a"], "Screenshots 7-15-26")
            self.assertEqual(groups["tune.flac"], "artist")

    def test_add_cues_section_splits_pajamathon(self):
        self.assertEqual(
            library_mod.add_cues_section(
                group="Pajamathon", relative_path="Pajamathon/Moonlight.flac"
            ),
            "pajamathon",
        )
        self.assertEqual(
            library_mod.add_cues_section(
                group="pajamathon 2026", relative_path="Pajamathon 2026/x.flac"
            ),
            "pajamathon",
        )
        self.assertEqual(
            library_mod.add_cues_section(group="Add Cues", relative_path="tune.flac"),
            "inbox",
        )
        self.assertEqual(
            library_mod.add_cues_section(
                group="Screenshots 7-15-26",
                relative_path="Screenshots 7-15-26/song.m4a",
            ),
            "inbox",
        )

    def test_list_add_cues_tracks_marks_pajamathon_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paj = root / "Pajamathon"
            paj.mkdir()
            (paj / "Moonlight.flac").write_bytes(b"a")
            (root / "inbox.m4a").write_bytes(b"b")
            tracks = library_mod.list_add_cues_tracks(root)
            by_name = {t.name: t for t in tracks}
            self.assertEqual(by_name["Moonlight.flac"].section, "pajamathon")
            self.assertEqual(by_name["Moonlight.flac"].group, "Pajamathon")
            self.assertEqual(by_name["inbox.m4a"].section, "inbox")
            self.assertEqual(by_name["inbox.m4a"].group, "Add Cues")
            paj_only = library_mod.add_cues_tracks_by_crate("pajamathon", root)
            self.assertEqual([t.name for t in paj_only], ["Moonlight.flac"])
            inbox_only = library_mod.add_cues_tracks_by_crate("inbox", root)
            self.assertEqual([t.name for t in inbox_only], ["inbox.m4a"])


if __name__ == "__main__":
    unittest.main()
