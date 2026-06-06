import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

import vdj_cue_patch


def write_database(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                '<?xml version="1.0" encoding="UTF-8"?>',
                "<VirtualDJ_Database>",
                '  <Song FilePath="/music/test.flac">',
                '    <Tags Title="Test Track" />',
                '    <Poi Name="BeatGrid" Pos="0.100000" Num="0" Type="beatgrid" />',
                '    <Poi Name="Old Cue" Pos="10.000000" Num="1" Color="4278255360" Type="cue" />',
                '    <Poi Name="Old Loop" Pos="20.000000" Num="-1" Color="4278255360" Type="loop" Size="16.0" Slot="1" />',
                "  </Song>",
                '  <Song FilePath="/music/other.flac">',
                '    <Poi Name="Other Cue" Pos="1.000000" Num="1" Color="4278190335" Type="cue" />',
                "  </Song>",
                "</VirtualDJ_Database>",
            ]
        ),
        encoding="utf-8",
    )


def write_patch(path: Path, track_path: str = "/music/test.flac") -> None:
    path.write_text(
        json.dumps(
            {
                "tracks": [
                    {
                        "path": track_path,
                        "comment": "blue orange",
                        "pois": [
                            {
                                "Type": "cue",
                                "Name": "Intro",
                                "Pos": "0.000000",
                                "Num": "1",
                                "Color": "4278190335",
                            },
                            {
                                "Type": "loop",
                                "Name": "Vocal Loopl",
                                "Pos": "32.000000",
                                "Num": "-1",
                                "Color": "4294934272",
                                "Size": "16.0",
                                "Slot": "9",
                            },
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


class VdjCuePatchTests(unittest.TestCase):
    def test_applies_patch_and_preserves_system_pois(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "database.xml"
            patch = Path(temp_dir) / "patch.json"
            write_database(database)
            write_patch(patch)

            results, backup_path, stats = vdj_cue_patch.apply_patch_file(
                database, patch, allow_vdj_running=True
            )

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].removed, 2)
            self.assertEqual(results[0].added, 2)
            self.assertIsNotNone(backup_path)
            self.assertTrue(Path(backup_path).exists())
            self.assertEqual(stats["song_count"], 2)
            self.assertEqual(stats["cue_loop_count"], 3)
            written = database.read_bytes()
            self.assertFalse(written.startswith(b"<?xml"))
            self.assertIn(b"\r\n", written)

            root = ET.parse(database).getroot()
            song = vdj_cue_patch.find_song(root, "/music/test.flac")
            self.assertIsNotNone(song)
            self.assertIsNotNone(song.find('./Poi[@Type="beatgrid"]'))
            self.assertIsNone(song.find('./Poi[@Name="Old Cue"]'))
            self.assertIsNone(song.find('./Poi[@Name="Old Loop"]'))
            self.assertIsNotNone(song.find('./Poi[@Name="Intro"]'))
            patched_loop = song.find('./Poi[@Name="Vocal Loopl"]')
            self.assertIsNotNone(patched_loop)
            self.assertEqual(patched_loop.get("Slot"), "1")
            self.assertEqual(song.findtext("Comment"), "blue orange")

    def test_missing_track_fails_without_writing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "database.xml"
            patch = Path(temp_dir) / "patch.json"
            write_database(database)
            before = database.read_text(encoding="utf-8")
            write_patch(patch, track_path="/music/missing.flac")

            with self.assertRaisesRegex(ValueError, "Track not found"):
                vdj_cue_patch.apply_patch_file(
                    database, patch, allow_vdj_running=True
                )

            self.assertEqual(database.read_text(encoding="utf-8"), before)
            self.assertFalse(Path(f"{database}.tmp").exists())


if __name__ == "__main__":
    unittest.main()
