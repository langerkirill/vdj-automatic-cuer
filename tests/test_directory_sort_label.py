"""Directory Sort label: omit library roots; leaf vs bottom-two."""

from __future__ import annotations

import unittest

from vdj_database_safety import directory_sort_label


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


if __name__ == "__main__":
    unittest.main()
