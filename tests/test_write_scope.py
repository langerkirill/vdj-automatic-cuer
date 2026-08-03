"""Tests for cues-only / loops-only write scope (preserve the other side)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import automatic_music_cuer_gemini as cuer_module
from vdj_cuer.common import WRITE_SCOPE_CUES, WRITE_SCOPE_LOOPS
from vdj_cuer.cue_writer import PreparedPoi, PreparedSongCues
from vdj_database_safety import (
    extract_manual_pois_from_song_xml,
    inject_pois_into_song_xml,
    format_vdj_poi_line,
)


def sample_song_xml() -> str:
    newline = "\r\n"
    lines = [
        '<Song FilePath="/music/track.flac" FileSize="1000">',
        '  <Tags Title="Track" />',
        '  <Infos SongLength="120" />',
        '  <Scan Version="801" Bpm="0.5" Phase="0.0" />',
        '  <Poi Pos="0.0" Type="beatgrid" />',
        '  <Poi Name="Intro" Pos="1.0" Num="1" Color="4278190335" Type="cue" />',
        '  <Poi Name="Drop" Pos="30.0" Num="2" Color="4278255360" Type="cue" />',
        '  <Poi Name="Synthl" Pos="10.0" Num="-1" Color="4278190335" Type="loop" Size="16.0" Slot="1" />',
        "  <Comment>blue green</Comment>",
        "</Song>",
    ]
    return newline.join(lines) + newline


class ExtractManualPoisTests(unittest.TestCase):
    def test_extract_splits_cues_and_loops(self):
        extracted = extract_manual_pois_from_song_xml(sample_song_xml())
        self.assertEqual(len(extracted["cues"]), 2)
        self.assertEqual(len(extracted["loops"]), 1)
        self.assertEqual(extracted["cues"][0]["name"], "Intro")
        self.assertEqual(extracted["loops"][0]["name"], "Synthl")
        self.assertEqual(extracted["loops"][0]["length_beats"], 16.0)


class WriteScopePrepareTests(unittest.TestCase):
    def _cuer(self):
        with patch("builtins.print"):
            return cuer_module.AutomaticMusicCuer(
                gemini_api_key="test-key",
                vdj_database_path="/tmp/database.xml",
            )

    def test_cues_only_keeps_existing_loops(self):
        cuer = self._cuer()
        cuer.write_scope = WRITE_SCOPE_CUES
        existing_loops = [
            PreparedPoi(
                kind="loop",
                name="Synthl",
                position=10.0,
                color_name="blue",
                color_value="4278190335",
                elements=["preserved"],
                length_beats=16.0,
            )
        ]
        analysis = {
            "measure_changes": [
                {
                    "timestamp": 5.0,
                    "elements": ["drums"],
                    "cue_name": "New Cue",
                    "color": "green",
                    "confidence": 0.9,
                }
            ],
            "loop_segments": [
                {
                    "start": 20.0,
                    "length_beats": 16,
                    "elements": ["drums"],
                    "loop_name": "ShouldNotWrite",
                    "color": "green",
                    "confidence": 0.9,
                }
            ],
        }

        with patch.object(
            cuer, "_finalize_analysis_for_write", return_value=(analysis, 120.0, 200.0)
        ), patch.object(
            cuer,
            "_verify_beatgrid_alignment",
            return_value=type("A", (), {"offset": 0.0, "corrected": False})(),
        ), patch.object(
            cuer, "validate_timing_hybrid", side_effect=lambda *a, **k: float(a[0])
        ), patch.object(
            cuer, "validate_color_assignment", return_value="green"
        ), patch.object(
            cuer, "_load_existing_prepared_pois", return_value=([], existing_loops)
        ):
            prepared = cuer.prepare_song_cues("/music/track.flac", analysis)

        self.assertEqual(len(prepared.cues), 1)
        self.assertEqual(prepared.cues[0].name, "New Cue")
        self.assertEqual(len(prepared.loops), 1)
        self.assertEqual(prepared.loops[0].name, "Synthl")
        self.assertEqual(prepared.loops[0].position, 10.0)

    def test_loops_only_keeps_existing_cues(self):
        cuer = self._cuer()
        cuer.write_scope = WRITE_SCOPE_LOOPS
        existing_cues = [
            PreparedPoi(
                kind="cue",
                name="Intro",
                position=1.0,
                color_name="orange",
                color_value="4294934272",
                elements=["preserved"],
            )
        ]
        analysis = {
            "measure_changes": [
                {
                    "timestamp": 5.0,
                    "elements": ["drums"],
                    "cue_name": "ShouldNotWrite",
                    "color": "green",
                    "confidence": 0.9,
                }
            ],
            "loop_segments": [
                {
                    "start": 20.0,
                    "length_beats": 16,
                    "elements": ["bass", "synth"],
                    "loop_name": "Bass",
                    "color": "blue",
                    "confidence": 0.9,
                }
            ],
        }

        with patch.object(
            cuer, "_finalize_analysis_for_write", return_value=(analysis, 120.0, 200.0)
        ), patch.object(
            cuer,
            "_verify_beatgrid_alignment",
            return_value=type("A", (), {"offset": 0.0, "corrected": False})(),
        ), patch.object(
            cuer, "validate_timing_hybrid", side_effect=lambda *a, **k: float(a[0])
        ), patch.object(
            cuer, "validate_color_assignment", return_value="blue"
        ), patch.object(
            cuer, "create_loop_name", return_value="Bass"
        ), patch.object(
            cuer, "_load_existing_prepared_pois", return_value=(existing_cues, [])
        ):
            prepared = cuer.prepare_song_cues("/music/track.flac", analysis)

        self.assertEqual(len(prepared.cues), 1)
        self.assertEqual(prepared.cues[0].name, "Intro")
        self.assertEqual(len(prepared.loops), 1)
        self.assertIn("Bass", prepared.loops[0].name)

    def test_cli_flags_are_mutually_exclusive(self):
        from automatic_music_cuer_gemini import main
        import sys

        with patch.object(
            sys,
            "argv",
            [
                "automatic_music_cuer_gemini.py",
                "--cues-only",
                "--loops-only",
                "x.mp3",
            ],
        ):
            with self.assertRaises(SystemExit):
                main()


if __name__ == "__main__":
    unittest.main()
