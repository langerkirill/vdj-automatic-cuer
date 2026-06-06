import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import automatic_music_cuer_gemini as cuer_module


def database_xml(song_count: int, cue_count: int) -> str:
    songs = []
    remaining_cues = cue_count
    for index in range(song_count):
        poi = ""
        if remaining_cues > 0:
            poi = (
                f'<Poi Name="Cue {index}" Pos="{index}.0" Num="1" '
                'Color="4278190335" Type="cue" />'
            )
            remaining_cues -= 1
        songs.append(f'<Song FilePath="/tmp/song-{index}.flac">{poi}</Song>')
    return "<VirtualDJ_Database>" + "".join(songs) + "</VirtualDJ_Database>"


class DatabaseWriteSafetyTests(unittest.TestCase):
    def setUp(self):
        with patch("builtins.print"):
            self.cuer = cuer_module.AutomaticMusicCuer(
                gemini_api_key="test-key",
                vdj_database_path="/tmp/database.xml",
            )

    def write_temp_database(self, directory: str, name: str, songs: int, cues: int) -> Path:
        path = Path(directory) / name
        path.write_text(database_xml(songs, cues), encoding="utf-8")
        return path

    def test_rejects_replacement_that_loses_songs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            original = self.write_temp_database(temp_dir, "original.xml", 10, 8)
            candidate = self.write_temp_database(temp_dir, "candidate.xml", 4, 8)
            original_stats = self.cuer._database_integrity_stats(original)

            with self.assertRaisesRegex(ValueError, "song count"):
                self.cuer._validate_database_replacement(candidate, original_stats)

    def test_rejects_replacement_that_loses_most_cues(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            original = self.write_temp_database(temp_dir, "original.xml", 30, 25)
            candidate = self.write_temp_database(temp_dir, "candidate.xml", 30, 2)
            original_stats = self.cuer._database_integrity_stats(original)

            with self.assertRaisesRegex(ValueError, "cue/loop count"):
                self.cuer._validate_database_replacement(candidate, original_stats)

    def test_accepts_same_shape_replacement(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            original = self.write_temp_database(temp_dir, "original.xml", 10, 8)
            candidate = self.write_temp_database(temp_dir, "candidate.xml", 10, 8)
            original_stats = self.cuer._database_integrity_stats(original)

            stats = self.cuer._validate_database_replacement(candidate, original_stats)

        self.assertEqual(stats["song_count"], 10)
        self.assertEqual(stats["cue_loop_count"], 8)


if __name__ == "__main__":
    unittest.main()
