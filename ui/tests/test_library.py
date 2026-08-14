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

    def test_find_library_matches_house_and_zouk_fuzzy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            house = root / "House" / "Chill"
            zouk = root / "Zouk" / "Energy"
            house.mkdir(parents=True)
            zouk.mkdir(parents=True)
            (zouk / "140. Amaria - Moon.flac").write_bytes(b"z")
            (house / "01 - Disclosure - ENERGY.flac").write_bytes(b"h")
            with patch.dict(
                library_mod.LIBRARIES,
                {"House": root / "House", "Zouk": root / "Zouk"},
                clear=True,
            ):
                zouk_hits = library_mod.find_library_matches("01 - Amaria - Moon.flac")
                house_hits = library_mod.find_library_matches(
                    "Disclosure - ENERGY.flac"
                )
                miss = library_mod.find_library_matches("nope.flac")
            self.assertEqual(len(zouk_hits), 1)
            self.assertEqual(zouk_hits[0]["root_name"], "Zouk")
            self.assertEqual(zouk_hits[0]["relative_path"], "Energy/140. Amaria - Moon.flac")
            self.assertEqual(len(house_hits), 1)
            self.assertEqual(house_hits[0]["root_name"], "House")
            self.assertEqual(house_hits[0]["relative_path"], "Chill/01 - Disclosure - ENERGY.flac")
            self.assertEqual(miss, [])

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

    def test_normalize_placement_key_strips_track_numbers(self):
        self.assertEqual(
            library_mod.normalize_placement_key("01 - Amaria - Moon.flac"),
            library_mod.normalize_placement_key("140. Amaria - Moon.flac"),
        )
        self.assertEqual(
            library_mod.normalize_placement_key("01 - Doja Cat - Woman(Explicit).flac"),
            library_mod.normalize_placement_key("255. Doja Cat - Woman.flac"),
        )
        self.assertEqual(
            library_mod.normalize_placement_key("01 - Good Lee - Sol - Inward.flac"),
            library_mod.normalize_placement_key("292. Good Lee - Sol Inward.flac"),
        )
        self.assertNotEqual(
            library_mod.normalize_placement_key("01 - Amaria - Moon.flac"),
            library_mod.normalize_placement_key("01 - Amaria - Moon.m4a"),
        )
        self.assertNotEqual(
            library_mod.normalize_placement_key("01 - 50 Cent - Candy Shop.flac"),
            library_mod.normalize_placement_key("140. Cent - Candy Shop.flac"),
        )
        self.assertNotEqual(
            library_mod.normalize_placement_key("01 - 99 Problems.flac"),
            library_mod.normalize_placement_key("140. 02 Problems.flac"),
        )
        self.assertNotEqual(
            library_mod.normalize_placement_key("Title_2024.flac"),
            library_mod.normalize_placement_key("Title.flac"),
        )
        # Ready "01 Title" vs set "407. 01 Title" (space after crate number, no dash).
        self.assertEqual(
            library_mod.normalize_placement_key(
                "01 Dusk Till Dawn - Kizomba Remix.m4a"
            ),
            library_mod.normalize_placement_key(
                "407. 01 Dusk Till Dawn - Kizomba Remix.m4a"
            ),
        )
        # Ready crate numbers often have a space before the dash: "14 - Title".
        self.assertEqual(
            library_mod.normalize_placement_key(
                "14 - Dj Kakah - Chantaje (Kizomba Remix).mp3"
            ),
            "dj kakah chantaje kizomba remix.mp3",
        )

    def test_placement_match_keys_parentheticals_and_version(self):
        chantaje_ready = library_mod.placement_match_keys(
            "14 - Dj Kakah - Chantaje (Kizomba Remix).mp3"
        )
        chantaje_set = library_mod.placement_match_keys(
            "165. Dj Kakah - Chantaje (Shakira & Maluma).mp3"
        )
        self.assertTrue(
            set(chantaje_ready) & set(chantaje_set),
            "Remix vs original-artist parentheticals must still share a key",
        )

        tunnel_ready = library_mod.placement_match_keys(
            "Dj Kakah - Tunnel Vision 2.mp3"
        )
        tunnel_set = library_mod.placement_match_keys(
            "385. Dj Kakah - Tunnel Vision Version 2.mp3"
        )
        self.assertTrue(
            set(tunnel_ready) & set(tunnel_set),
            "Version 2 and a trailing 2 must still share a key",
        )

        self.assertFalse(
            set(library_mod.placement_match_keys("Dj Kakah - Tunnel Vision.mp3"))
            & set(library_mod.placement_match_keys("Dj Kakah - Tunnel Vision 2.mp3")),
            "Unnumbered title must not collapse onto Version 2",
        )
        self.assertFalse(
            set(library_mod.placement_match_keys("Dj Kakah - Chantaje.mp3"))
            & set(library_mod.placement_match_keys("Dj Kakah - Tunnel Vision.mp3"))
        )

    def test_find_set_matches_parentheticals_and_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paj = root / "Pajamathon 2026"
            paj.mkdir()
            (paj / "165. Dj Kakah - Chantaje (Shakira & Maluma).mp3").write_bytes(
                b"chantaje"
            )
            (paj / "385. Dj Kakah - Tunnel Vision Version 2.mp3").write_bytes(
                b"tunnel"
            )
            with patch.object(library_mod, "SETS_ROOT", root):
                chantaje = library_mod.find_set_matches(
                    "14 - Dj Kakah - Chantaje (Kizomba Remix).mp3"
                )
                tunnel = library_mod.find_set_matches(
                    "Dj Kakah - Tunnel Vision 2.mp3"
                )
                miss = library_mod.find_set_matches("Dj Kakah - Tunnel Vision.mp3")

            self.assertEqual(len(chantaje), 1)
            self.assertTrue(
                chantaje[0]["relative_path"].endswith(
                    "165. Dj Kakah - Chantaje (Shakira & Maluma).mp3"
                )
            )
            self.assertEqual(len(tunnel), 1)
            self.assertTrue(
                tunnel[0]["relative_path"].endswith(
                    "385. Dj Kakah - Tunnel Vision Version 2.mp3"
                )
            )
            self.assertEqual(miss, [])

    def test_find_set_matches_pajamathon_fuzzy_and_exact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paj = root / "Pajamathon 2026"
            paj.mkdir(parents=True)
            (paj / "140. Amaria - Moon.flac").write_bytes(b"set")
            (paj / "407. 01 Dusk Till Dawn - Kizomba Remix.m4a").write_bytes(b"dusk")
            (paj / "Mafie Zouker - Sky.m4a").write_bytes(b"exact")
            (root / "Kizouk" / "other").mkdir(parents=True)
            (root / "Kizouk" / "other" / "unrelated.flac").write_bytes(b"no")
            (root / "Z4").mkdir()
            (root / "Z4" / "01 - Amaria - Moon.flac").write_bytes(b"z4")

            with patch.object(library_mod, "SETS_ROOT", root):
                fuzzy = library_mod.find_set_matches("01 - Amaria - Moon.flac")
                dusk = library_mod.find_set_matches(
                    "01 Dusk Till Dawn - Kizomba Remix.m4a"
                )
                exact = library_mod.find_set_matches("Mafie Zouker - Sky.m4a")
                miss = library_mod.find_set_matches("nope.flac")

            self.assertEqual(len(fuzzy), 1)
            self.assertEqual(fuzzy[0]["root_name"], "Pajamathon 2026")
            self.assertNotIn("Z4", fuzzy[0]["relative_path"])
            self.assertEqual(
                fuzzy[0]["relative_path"], "Pajamathon 2026/140. Amaria - Moon.flac"
            )
            self.assertEqual(fuzzy[0]["event"], "Pajamathon 2026")
            self.assertEqual(len(dusk), 1)
            self.assertTrue(
                dusk[0]["relative_path"].endswith(
                    "407. 01 Dusk Till Dawn - Kizomba Remix.m4a"
                )
            )
            self.assertEqual(len(exact), 1)
            self.assertEqual(exact[0]["root_name"], "Pajamathon 2026")
            self.assertEqual(miss, [])
            self.assertEqual(
                library_mod.find_set_matches("unrelated.flac"),
                [],
                "Non-Pajamathon event crates must not appear as set matches",
            )

    def test_find_set_matches_uses_shared_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paj = root / "Pajamathon 2026"
            paj.mkdir()
            (paj / "049. Ayelle - Mind and Body.flac").write_bytes(b"x")
            with patch.object(library_mod, "SETS_ROOT", root):
                index = library_mod.build_set_match_index(root)
                hits = library_mod.find_set_matches(
                    "01 - Ayelle - Mind and Body.flac", index=index
                )
            self.assertEqual(len(hits), 1)
            self.assertTrue(hits[0]["path"].endswith("049. Ayelle - Mind and Body.flac"))

    def test_find_set_matches_skips_missing_files_in_stale_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paj = root / "Pajamathon 2026"
            paj.mkdir()
            gone = paj / "407. 01 Dusk Till Dawn - Kizomba Remix.m4a"
            keep = paj / "405. 01 Dusk Till Dawn - Kizomba Remix.m4a"
            gone.write_bytes(b"gone")
            keep.write_bytes(b"keep")
            with patch.object(library_mod, "SETS_ROOT", root):
                index = library_mod.build_set_match_index(root)
                gone.unlink()
                hits = library_mod.find_set_matches(
                    "01 Dusk Till Dawn - Kizomba Remix.m4a", index=index
                )
            self.assertEqual(len(hits), 1)
            self.assertTrue(hits[0]["path"].endswith(keep.name))

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

    def test_list_pajamathon_set_tracks_and_merge_prefers_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            add = root / "Add Cues"
            paj_add = add / "Pajamathon"
            sets = root / "Sets" / "Pajamathon 2026"
            paj_add.mkdir(parents=True)
            sets.mkdir(parents=True)
            (paj_add / "01 - Amaria - Moon.flac").write_bytes(b"add")
            (paj_add / "Only In Add.flac").write_bytes(b"only")
            (sets / "140. Amaria - Moon.flac").write_bytes(b"set")
            (root / "Add Cues" / "inbox.m4a").write_bytes(b"in")
            with patch.object(library_mod, "SETS_ROOT", root / "Sets"):
                event = library_mod.list_pajamathon_set_tracks(root / "Sets")
                merged = library_mod.merge_add_cues_and_pajamathon_set(
                    library_mod.list_add_cues_tracks(add), event
                )
            names = [t.name for t in merged]
            self.assertIn("140. Amaria - Moon.flac", names)
            self.assertIn("Only In Add.flac", names)
            self.assertIn("inbox.m4a", names)
            self.assertNotIn("01 - Amaria - Moon.flac", names)
            paj = [t for t in merged if t.section == "pajamathon"]
            self.assertTrue(any(t.group == "Pajamathon 2026" for t in paj))


if __name__ == "__main__":
    unittest.main()
