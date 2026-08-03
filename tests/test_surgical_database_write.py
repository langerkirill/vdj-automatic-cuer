"""Tests for low-memory surgical VirtualDJ database rewrites."""

import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

import automatic_music_cuer_gemini as cuer_module
from vdj_database_safety import (
    database_integrity_stats,
    format_vdj_poi_line,
    inject_pois_into_song_xml,
    load_song_element,
    rewrite_song_in_database,
    rewrite_song_xml_in_database,
    strip_manual_cues_from_song_xml,
)


def sample_database() -> str:
    return "".join(
        [
            "<VirtualDJ_Database>",
            '<Song FilePath="/music/keep.flac">',
            '<Scan Bpm="0.5" />',
            '<Infos SongLength="180" />',
            '<Poi Type="beatgrid" Pos="0.10" Num="0" />',
            '<Poi Name="Old" Pos="1.0" Num="1" Color="4278190335" Type="cue" />',
            "</Song>",
            '<Song FilePath="/music/target.flac">',
            '<Scan Bpm="0.5" />',
            '<Infos SongLength="200" />',
            '<Poi Type="beatgrid" Pos="0.25" Num="0" />',
            '<Poi Name="Stale" Pos="2.0" Num="1" Color="4278255360" Type="cue" />',
            "</Song>",
            '<Song FilePath="/music/ampersand &amp; name.flac">',
            '<Infos SongLength="90" />',
            "</Song>",
            "</VirtualDJ_Database>",
        ]
    )


class SurgicalDatabaseWriteTests(unittest.TestCase):
    def test_streaming_integrity_stats_count_songs_and_cues(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "database.xml"
            path.write_text(sample_database(), encoding="utf-8")
            stats = database_integrity_stats(path)
        self.assertEqual(stats["song_count"], 3)
        self.assertEqual(stats["cue_loop_count"], 2)

    def test_rewrite_only_mutates_target_song(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "database.xml"
            path.write_text(sample_database(), encoding="utf-8")

            def mutator(song: ET.Element) -> None:
                for poi in list(song.findall("Poi")):
                    if poi.get("Type") == "cue":
                        song.remove(poi)
                cue = ET.Element("Poi")
                cue.set("Name", "Intro")
                cue.set("Pos", "0.000000")
                cue.set("Num", "1")
                cue.set("Color", "4278190335")
                cue.set("Type", "cue")
                song.append(cue)

            rewrite_song_in_database(path, "/music/target.flac", mutator)

            root = ET.parse(path).getroot()
            songs = {
                song.get("FilePath"): song for song in root.findall("Song")
            }
            self.assertEqual(len(songs), 3)

            keep_cues = [
                poi
                for poi in songs["/music/keep.flac"].findall("Poi")
                if poi.get("Type") == "cue"
            ]
            self.assertEqual(len(keep_cues), 1)
            self.assertEqual(keep_cues[0].get("Name"), "Old")

            target_cues = [
                poi
                for poi in songs["/music/target.flac"].findall("Poi")
                if poi.get("Type") == "cue"
            ]
            self.assertEqual(len(target_cues), 1)
            self.assertEqual(target_cues[0].get("Name"), "Intro")
            self.assertEqual(
                songs["/music/target.flac"].find("Poi[@Type='beatgrid']").get("Pos"),
                "0.25",
            )

    def test_rewrite_handles_xml_escaped_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "database.xml"
            path.write_text(sample_database(), encoding="utf-8")

            def mutator(song: ET.Element) -> None:
                comment = ET.Element("Comment")
                comment.text = "blue"
                song.append(comment)

            rewrite_song_in_database(
                path, "/music/ampersand & name.flac", mutator
            )
            song = load_song_element(path, "/music/ampersand & name.flac")
            self.assertEqual(song.findtext("Comment"), "blue")


class CueWriterSurgicalPathTests(unittest.TestCase):
    def make_cuer(self, database_path: str):
        with patch("builtins.print"):
            return cuer_module.AutomaticMusicCuer(
                gemini_api_key="test-key",
                vdj_database_path=database_path,
            )

    def test_text_preserving_inject_keeps_scan_and_automix(self):
        original = (
            '<Song FilePath="/music/target.flac">\r\n'
            '  <Tags Title="T" />\r\n'
            '  <Scan Bpm="0.5" Phase="0.1" />\r\n'
            '  <Poi Type="automix" Point="realStart" />\r\n'
            '  <Poi Pos="0.25" Type="beatgrid" />\r\n'
            '  <Poi Name="Stale" Pos="2.0" Num="1" Color="1" Type="cue" />\r\n'
            "</Song>"
        )
        poi = format_vdj_poi_line(
            pos=1.5,
            poi_type="cue",
            num="1",
            color="4278190335",
            name="Intro",
            newline="\r\n",
        )
        updated = inject_pois_into_song_xml(original, [poi], comment="blue")
        self.assertIn('<Scan Bpm="0.5" Phase="0.1" />', updated)
        self.assertIn('Type="automix"', updated)
        self.assertIn('Type="beatgrid"', updated)
        self.assertIn('Name="Intro"', updated)
        self.assertNotIn("Stale", updated)
        self.assertIn("<Comment>blue</Comment>", updated)

    def test_apply_cues_uses_surgical_rewrite_not_full_tree_parse(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "database.xml"
            path.write_text(sample_database(), encoding="utf-8")
            cuer = self.make_cuer(str(path))

            analysis = {
                "measure_changes": [
                    {
                        "timestamp": 0.0,
                        "elements": ["synth"],
                        "cue_name": "Intro",
                        "color": "blue",
                        "confidence": 0.9,
                        "role": "intro",
                    }
                ],
                "loop_segments": [],
            }

            with patch.object(cuer, "parse_vdj_database") as full_parse:
                with patch.object(
                    cuer, "_verify_beatgrid_alignment"
                ) as verify:
                    from vdj_cuer.common import BeatgridAlignment

                    verify.return_value = BeatgridAlignment(offset=0.25)
                    with patch.object(
                        cuer, "validate_timing_hybrid", side_effect=lambda t, *a, **k: t
                    ):
                        success = cuer._apply_cues_to_database(
                            "/music/target.flac", analysis, dry_run=False
                        )

            self.assertTrue(success)
            full_parse.assert_not_called()

            song = load_song_element(path, "/music/target.flac")
            cues = [poi for poi in song.findall("Poi") if poi.get("Type") == "cue"]
            self.assertEqual(len(cues), 1)
            self.assertEqual(cues[0].get("Name"), "Intro")
            # Native markup preserved
            raw = path.read_text(encoding="utf-8")
            self.assertIn('<Scan Bpm="0.5" />', raw)
            # Unrelated song still has its original cue.
            keep = load_song_element(path, "/music/keep.flac")
            keep_cues = [poi for poi in keep.findall("Poi") if poi.get("Type") == "cue"]
            self.assertEqual(keep_cues[0].get("Name"), "Old")


if __name__ == "__main__":
    unittest.main()
