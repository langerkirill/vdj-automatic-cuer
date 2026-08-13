"""Complementary-frequency transition timing from VDJ cue names."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from sorter.transition_timing import (
    best_timing,
    classify_marker,
    format_timestamp,
    mix_out_windows,
    parse_markers,
    parse_pois_from_song_xml,
    timing_score_pair,
)
from sorter.transition_recs import build_candidates
from sorter import transition_recs as tr


class ClassifyMarkerTests(unittest.TestCase):
    def test_downsection_is_melodic_hole_for_drums(self):
        m = classify_marker("Down section")
        self.assertEqual(m.structure, "breakdown")
        self.assertIn("melody", m.layers)
        self.assertIn("drums", m.missing)
        self.assertNotIn("drums", m.layers)

    def test_breakdown_and_down_aliases(self):
        for name in ("Breakdown", "down", "Down Section", "melody"):
            m = classify_marker(name)
            self.assertIn("drums", m.missing, name)

    def test_drums_in_fills_drum_hole(self):
        m = classify_marker("Drums In")
        self.assertIn("drums", m.layers)
        self.assertIn("melody", m.missing)

    def test_compact_autocue_tokens(self):
        self.assertIn("drums", classify_marker("d2").layers)
        self.assertIn("drums", classify_marker("dl").layers)
        self.assertIn("melody", classify_marker("ml").layers)
        self.assertIn("vocals", classify_marker("vl").layers)
        self.assertIn("bass", classify_marker("drumsBass").layers)
        self.assertIn("drums", classify_marker("drumsBass").layers)

    def test_drop_and_intro_structure(self):
        self.assertEqual(classify_marker("Drop").structure, "drop")
        self.assertIn("drums", classify_marker("Drop").layers)
        self.assertEqual(classify_marker("Intro").structure, "intro")
        self.assertEqual(classify_marker("Outro").structure, "outro")

    def test_skips_tempo_and_empty(self):
        self.assertIsNone(classify_marker("Tempo: 128.0"))
        self.assertIsNone(classify_marker(""))
        self.assertIsNone(classify_marker("Cue 3"))


class ComplementScoreTests(unittest.TestCase):
    def test_melodic_down_plus_drums_scores_higher_than_another_melody(self):
        down = classify_marker("Down section", pos=200.0)
        drums = classify_marker("Drums In", pos=32.0)
        melody = classify_marker("melody", pos=40.0)
        good = timing_score_pair(down, drums, out_length=300.0, in_length=240.0)
        bad = timing_score_pair(down, melody, out_length=300.0, in_length=240.0)
        self.assertGreater(good, bad)
        self.assertGreater(good, 8)

    def test_vocal_on_vocal_is_penalized(self):
        out_v = classify_marker("Vocals In", pos=180.0)
        in_v = classify_marker("Vocals In", pos=20.0)
        in_d = classify_marker("Drums In", pos=20.0)
        self.assertGreater(
            timing_score_pair(out_v, in_d, out_length=240.0, in_length=200.0),
            timing_score_pair(out_v, in_v, out_length=240.0, in_length=200.0),
        )

    def test_does_not_want_incoming_outro(self):
        down = classify_marker("Breakdown", pos=190.0)
        drums = classify_marker("Drums In", pos=28.0)
        outro = classify_marker("Outro", pos=200.0)
        self.assertGreater(
            timing_score_pair(down, drums, out_length=240.0, in_length=230.0),
            timing_score_pair(down, outro, out_length=240.0, in_length=230.0),
        )
        self.assertLess(
            timing_score_pair(down, outro, out_length=240.0, in_length=230.0),
            0,
        )


class BestTimingTests(unittest.TestCase):
    def test_picks_downsection_to_drums(self):
        source = parse_markers(
            [
                {"name": "Intro", "pos": 8.0},
                {"name": "Drop", "pos": 64.0},
                {"name": "Down section", "pos": 198.0},
                {"name": "Outro", "pos": 240.0},
            ]
        )
        incoming = parse_markers(
            [
                {"name": "Intro", "pos": 4.0},
                {"name": "Drums In", "pos": 28.0},
                {"name": "Vocals In", "pos": 80.0},
                {"name": "Outro", "pos": 210.0},
            ]
        )
        timing = best_timing(source, incoming, source_length=260.0, cand_length=230.0)
        self.assertIsNotNone(timing)
        assert timing is not None
        self.assertEqual(timing["out_label"], "Down section")
        self.assertEqual(timing["in_label"], "Drums In")
        self.assertIn("drums", timing["fills"])
        self.assertIn("3:18", timing["out_time"])
        self.assertIn("0:28", timing["in_time"])

    def test_prefers_early_drums_in_over_late_drop(self):
        source = parse_markers(
            [
                {"name": "Intro", "pos": 8.0},
                {"name": "Down section", "pos": 198.0},
            ]
        )
        incoming = parse_markers(
            [
                {"name": "Intro", "pos": 8.0},
                {"name": "Drums In", "pos": 28.0},
                {"name": "Drop", "pos": 200.0},
                {"name": "Outro", "pos": 210.0},
            ]
        )
        timing = best_timing(source, incoming, source_length=260.0, cand_length=230.0)
        self.assertIsNotNone(timing)
        assert timing is not None
        self.assertEqual(timing["in_label"], "Drums In")
        self.assertNotEqual(timing["in_label"], "Drop")
        self.assertNotEqual(timing["in_structure"], "outro")

    def test_mix_out_windows_prefer_late_breakdown(self):
        markers = parse_markers(
            [
                {"name": "Intro", "pos": 0.0},
                {"name": "Drop", "pos": 40.0},
                {"name": "Down section", "pos": 180.0},
            ]
        )
        windows = mix_out_windows(markers, song_length=240.0)
        self.assertTrue(windows)
        self.assertEqual(windows[0]["label"], "Down section")
        self.assertIn("drums", windows[0]["missing"])

    def test_format_timestamp(self):
        self.assertEqual(format_timestamp(0), "0:00")
        self.assertEqual(format_timestamp(28), "0:28")
        self.assertEqual(format_timestamp(198), "3:18")


class ParsePoisTests(unittest.TestCase):
    def test_parses_named_cues_and_skips_automix(self):
        chunk = """
        <Song FilePath="/x.flac">
          <Poi Name="Intro" Pos="8.0" Type="cue" Num="1" />
          <Poi Name="automix" Pos="0" Type="cue" Num="0" />
          <Poi Type="loop" Name="dl" Pos="32.5" Num="-1" />
          <Poi Type="beatgrid" Pos="0.02" />
        </Song>
        """
        pois = parse_pois_from_song_xml(chunk)
        self.assertEqual([p["name"] for p in pois], ["Intro", "dl"])
        self.assertEqual(pois[1]["kind"], "loop")


class CandidateTimingRankTests(unittest.TestCase):
    def test_build_candidates_boosts_complementary_drum_fill(self):
        songs = [
            {
                "path": "/lib/Zouk/a-drums.flac",
                "name": "a-drums.flac",
                "artist": "A",
                "title": "Drums Bed",
                "bpm": 90.0,
                "key": "Am",
                "camelot": "8A",
                "cue_count": 3,
                "library": "Zouk",
                "relative_path": "Chill/a-drums.flac",
                "energy_hint": "same",
                "genre": "R&B",
                "vibe": "Chill",
                "cues": [
                    {"name": "Intro", "pos": 4.0, "kind": "cue"},
                    {"name": "Drums In", "pos": 24.0, "kind": "cue"},
                ],
                "song_length": 220.0,
            },
            {
                "path": "/lib/Zouk/b-melody.flac",
                "name": "b-melody.flac",
                "artist": "B",
                "title": "More Pads",
                "bpm": 91.0,
                "key": "Am",
                "camelot": "8A",
                "cue_count": 3,
                "library": "Zouk",
                "relative_path": "Chill/b-melody.flac",
                "energy_hint": "same",
                "genre": "R&B",
                "vibe": "Chill",
                "cues": [
                    {"name": "melody", "pos": 20.0, "kind": "cue"},
                    {"name": "Down section", "pos": 40.0, "kind": "cue"},
                ],
                "song_length": 220.0,
            },
        ]
        source_cues = [
            {"name": "Drop", "pos": 40.0, "kind": "cue"},
            {"name": "Down section", "pos": 180.0, "kind": "cue"},
        ]
        with patch.object(tr, "_scan_library_songs_from_database", return_value=songs), patch.object(
            tr, "_history_counts_for", return_value={}
        ), patch.object(tr, "audio_file_exists", return_value=True):
            cands = build_candidates(
                source_path="/now/seadoo.m4a",
                source_bpm=90.0,
                source_key="Am",
                source_artist="Rubí",
                source_title="Seadoo",
                source_genre="alternative R&B",
                source_vibe="Add Cues",
                source_genre_family="rnb_soul_zouk",
                source_cues=source_cues,
                source_length=240.0,
            )
        by_title = {c.title: c for c in cands}
        self.assertGreater(by_title["Drums Bed"].score, by_title["More Pads"].score)
        self.assertGreater(
            by_title["Drums Bed"].timing_score, by_title["More Pads"].timing_score or 0
        )
        self.assertIsNotNone(by_title["Drums Bed"].timing)
        self.assertEqual(by_title["Drums Bed"].timing["in_label"], "Drums In")
        self.assertIn("drums", by_title["Drums Bed"].timing["fills"])


if __name__ == "__main__":
    unittest.main()
