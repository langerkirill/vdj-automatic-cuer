"""Camelot compatibility for transition recommendations."""

from __future__ import annotations

import unittest

from sorter.musical_key import (
    camelot_compatible,
    key_to_camelot,
    path_genre_is_clear,
    song_key_from_element,
)


class MusicalKeyTests(unittest.TestCase):
    def test_key_to_camelot_common(self):
        self.assertEqual(key_to_camelot("Am"), "8A")
        self.assertEqual(key_to_camelot("C"), "8B")
        self.assertEqual(key_to_camelot("F#m"), "11A")
        self.assertEqual(key_to_camelot("11A"), "11A")

    def test_song_key_from_element_prefers_tags_then_scan(self):
        class Fake:
            def __init__(self, attrs):
                self._attrs = attrs

            def get(self, name, default=None):
                return self._attrs.get(name, default)

            def find(self, name):
                return self._attrs.get(f"_{name}")

        tags_only = Fake({"_Tags": Fake({"Key": "F#m"}), "_Scan": Fake({"Key": "C"})})
        scan_only = Fake({"_Tags": Fake({}), "_Scan": Fake({"Key": "Em"})})
        empty = Fake({})
        self.assertEqual(song_key_from_element(tags_only), "F#m")
        self.assertEqual(song_key_from_element(scan_only), "Em")
        self.assertEqual(song_key_from_element(empty), "")
        self.assertEqual(song_key_from_element(None), "")

    def test_compatible_relative_and_adjacent(self):
        self.assertTrue(camelot_compatible("Am", "C"))  # relative
        self.assertTrue(camelot_compatible("Am", "Em"))  # adjacent minor
        self.assertTrue(camelot_compatible("Am", "Am"))
        self.assertFalse(camelot_compatible("Am", "F#m"))  # far


class PathGenreClarityTests(unittest.TestCase):
    def test_inbox_and_energy_folders_are_not_clear(self):
        self.assertFalse(path_genre_is_clear("", "Add Cues / Screenshots 7-15-26"))
        self.assertFalse(path_genre_is_clear(None, "Ready For Sort"))
        self.assertFalse(path_genre_is_clear("", "AC Low Quality"))
        self.assertFalse(path_genre_is_clear("", "Cues Sorted / Energy"))
        self.assertFalse(path_genre_is_clear("", "Energy"))
        self.assertFalse(path_genre_is_clear("", "Chill / Fire"))
        self.assertFalse(path_genre_is_clear("", ""))
        self.assertFalse(path_genre_is_clear("Unknown", "Party"))

    def test_tag_or_folder_family_is_clear(self):
        self.assertTrue(path_genre_is_clear("R&B", "Add Cues / Screenshots 7-15-26"))
        self.assertTrue(path_genre_is_clear("Tribal", ""))
        self.assertTrue(path_genre_is_clear("", "India"))
        self.assertTrue(path_genre_is_clear("", "Cues Sorted / India"))
        self.assertTrue(path_genre_is_clear("", "Chill / Mystical"))
        self.assertTrue(path_genre_is_clear("", "Energy / Housey"))
        self.assertTrue(path_genre_is_clear("Deep House", "Ready For Sort"))


if __name__ == "__main__":
    unittest.main()
