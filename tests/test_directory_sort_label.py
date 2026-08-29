"""Directory Sort label: omit library roots; leaf vs bottom-two."""

from __future__ import annotations

import unittest

from vdj_database_safety import (
    directory_sort_label,
    normalize_user2_dest,
    patch_song_infos_and_user2,
    song_xml_with_directory_sort_user2,
    song_xml_with_user2_label,
)


class DirectorySortLabelTests(unittest.TestCase):
    def test_deep_under_zouk(self):
        self.assertEqual(
            directory_sort_label(
                "/Users/x/Music/DJ/Music/Zouk/Chill/Shaman/track.flac"
            ),
            "Chill/Shaman",
        )

    def test_single_under_zouk(self):
        self.assertEqual(
            directory_sort_label("/Users/x/Music/DJ/Music/Zouk/Energy/track.flac"),
            "Energy",
        )

    def test_single_under_house(self):
        self.assertEqual(
            directory_sort_label("/Users/x/Music/DJ/Music/House/Chill/track.flac"),
            "Chill",
        )

    def test_cues_sorted_single(self):
        self.assertEqual(
            directory_sort_label(
                "/Users/x/Music/DJ/Music/Cues/Cues Sorted/Chill/track.flac"
            ),
            "Chill",
        )

    def test_cues_sorted_deep(self):
        self.assertEqual(
            directory_sort_label(
                "/Users/x/Music/DJ/Music/Cues/Cues Sorted/Chill/Mystical/track.flac"
            ),
            "Chill/Mystical",
        )

    def test_does_not_include_zouk_or_cues_sorted_root(self):
        label = directory_sort_label(
            "/Users/x/Music/DJ/Music/Zouk/Chill/Shaman/t.flac"
        )
        self.assertNotIn("Zouk", label)
        label2 = directory_sort_label(
            "/Users/x/Music/DJ/Music/Cues/Cues Sorted/Energy/Trappy/t.flac"
        )
        self.assertNotIn("Cues Sorted", label2)
        self.assertEqual(label2, "Energy/Trappy")

    def test_cues_sorted_copy_variant(self):
        self.assertEqual(
            directory_sort_label(
                "/Users/x/Music/DJ/Music/Cues/Cues Sorted copy/Favs/t.flac"
            ),
            "Favs",
        )

    def test_zouk_add_cues_leaf(self):
        # Under Zouk root, single folder "Add Cues" is the leaf label.
        self.assertEqual(
            directory_sort_label(
                "/Users/x/Music/DJ/Music/Zouk/Add Cues/track.m4a"
            ),
            "Add Cues",
        )


class User2WriterRefuseTests(unittest.TestCase):
    SONG = (
        '<Song FilePath="/lib/t.flac">\r\n'
        '  <Tags Author="A" Title="T" User2="R&amp;B" />\r\n'
        '  <Infos UserColor="1" SongLength="100" />\r\n'
        '  <Poi Name="cue 1" Pos="1.2" Type="cue" />\r\n'
        '  <Poi Name="loop" Pos="10" Type="loop" />\r\n'
        "</Song>"
    )

    def test_normalize_refuses_crate_dests(self):
        self.assertEqual(normalize_user2_dest("Add Cues"), "")
        self.assertEqual(normalize_user2_dest("Add Cues/Pajamathon"), "")
        self.assertEqual(normalize_user2_dest("Cues Sorted"), "")
        self.assertEqual(normalize_user2_dest("Cues Sorted/Chill/Favs"), "")
        self.assertEqual(normalize_user2_dest("Sets/Goth"), "")
        self.assertEqual(normalize_user2_dest("Sets/Pajamathon 2026"), "")
        self.assertEqual(normalize_user2_dest("Kizouk"), "Kizouk")
        self.assertEqual(normalize_user2_dest("Sets/Kizouk"), "Kizouk")
        self.assertEqual(normalize_user2_dest("Chill/Shaman"), "Chill/Shaman")
        self.assertEqual(normalize_user2_dest("R&B"), "R&B")

    def test_writer_leaves_user2_when_path_is_add_cues(self):
        path = "/Users/x/Music/DJ/Music/Cues/Add Cues/Pajamathon/t.flac"
        out = song_xml_with_directory_sort_user2(self.SONG, path)
        self.assertIn('User2="R&amp;B"', out)
        self.assertNotIn("Add Cues", out)

    def test_writer_leaves_user2_when_path_is_sets(self):
        path = "/Users/x/Music/DJ/Music/Sets/Pajamathon 2026/t.flac"
        out = song_xml_with_directory_sort_user2(self.SONG, path)
        self.assertIn('User2="R&amp;B"', out)
        self.assertNotIn("Sets/", out)

    def test_writer_keeps_genre_leaf_from_cues_sorted_path(self):
        path = "/Users/x/Music/DJ/Music/Cues/Cues Sorted/Chill/Mystical/t.flac"
        out = song_xml_with_directory_sort_user2(self.SONG, path)
        self.assertIn('User2="Chill/Mystical"', out)

    def test_label_writer_refuses_add_cues_string(self):
        out = song_xml_with_user2_label(self.SONG, "Add Cues/Pajamathon")
        self.assertEqual(out, self.SONG)

    def test_restore_patches_infos_and_user2_without_wiping_pois(self):
        out = patch_song_infos_and_user2(
            self.SONG, user_color="4294901760", user2="Energy/Housey"
        )
        self.assertIn('UserColor="4294901760"', out)
        self.assertIn('User2="Energy/Housey"', out)
        self.assertIn('Name="cue 1"', out)
        self.assertIn('Name="loop"', out)
        self.assertIn('Pos="1.2"', out)

    def test_restore_refuses_sets_user2(self):
        out = patch_song_infos_and_user2(self.SONG, user2="Sets/Goth")
        self.assertIn('User2="R&amp;B"', out)
        self.assertNotIn("Sets/Goth", out)

if __name__ == "__main__":
    unittest.main()
