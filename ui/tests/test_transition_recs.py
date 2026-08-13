"""Transition rec candidate filtering (BPM + key) without Gemini."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sorter import transition_recs as tr
from sorter.transition_recs import (
    Candidate,
    build_candidates,
    _fallback_buckets,
    is_same_track,
    sanitize_recommendation_buckets,
    track_block_keys,
    track_identity_key,
)


class TransitionRecsTests(unittest.TestCase):
    def test_build_candidates_filters_bpm_and_key(self):
        songs = [
            {
                "path": "/lib/Zouk/Chill/a.flac",
                "name": "a.flac",
                "artist": "A",
                "title": "Track A",
                "bpm": 122.0,
                "key": "Am",
                "camelot": "8A",
                "cue_count": 3,
                "library": "Zouk",
                "relative_path": "Chill/a.flac",
                "energy_hint": "lower",
            },
            {
                "path": "/lib/Zouk/Energy/b.flac",
                "name": "b.flac",
                "artist": "B",
                "title": "Track B",
                "bpm": 140.0,  # too far from 120
                "key": "Am",
                "camelot": "8A",
                "cue_count": 3,
                "library": "Zouk",
                "relative_path": "Energy/b.flac",
                "energy_hint": "higher",
            },
            {
                "path": "/lib/Zouk/Chill/c.flac",
                "name": "c.flac",
                "artist": "C",
                "title": "Track C",
                "bpm": 121.0,
                "key": "F#m",  # not compatible with Am
                "camelot": "11A",
                "cue_count": 2,
                "library": "Zouk",
                "relative_path": "Chill/c.flac",
                "energy_hint": "same",
            },
            {
                "path": "/lib/House/Party/d.flac",
                "name": "d.flac",
                "artist": "D",
                "title": "Track D",
                "bpm": 123.0,
                "key": "C",  # relative of Am
                "camelot": "8B",
                "cue_count": 4,
                "library": "House",
                "relative_path": "Party/d.flac",
                "energy_hint": "higher",
            },
        ]
        with patch.object(tr, "_scan_library_songs_from_database", return_value=songs), patch.object(
            tr, "_history_counts_for", return_value={}
        ), patch.object(tr, "audio_file_exists", return_value=True):
            cands = build_candidates(
                source_path="/source/now.flac",
                source_bpm=120.0,
                source_key="Am",
                source_artist="X",
                source_title="Now",
                bpm_tolerance=5,
            )
        paths = {c.path for c in cands}
        self.assertIn("/lib/Zouk/Chill/a.flac", paths)
        self.assertIn("/lib/House/Party/d.flac", paths)
        self.assertNotIn("/lib/Zouk/Energy/b.flac", paths)
        self.assertNotIn("/lib/Zouk/Chill/c.flac", paths)

    def test_fallback_buckets_nonempty(self):
        cands = [
            Candidate(
                path="/a",
                name="a",
                artist="A",
                title="T",
                bpm=120,
                key="Am",
                camelot="8A",
                cue_count=2,
                library="Zouk",
                relative_path="x",
                history_count=3,
                energy_hint="same",
                score=30,
            )
        ]
        with patch.object(tr, "audio_file_exists", return_value=True):
            out = _fallback_buckets(
                cands, source={"path": "/now.flac", "artist": "X", "title": "Now"}
            )
        self.assertTrue(
            out["higher_energy"] or out["same_energy"] or out["lower_energy"]
        )
        self.assertEqual(out["model"], "fallback-heuristic")

    def test_is_same_track_ignores_library_copy(self):
        self.assertTrue(
            is_same_track(
                source_path="/Cues/Cues Sorted/Energy/31. Sensu - Simple.m4a",
                source_artist="Sensu",
                source_title="Simple",
                path="/Music/Zouk/Energy/31. Sensu - Simple.m4a",
                artist="Sensu",
                title="Simple",
            )
        )
        self.assertFalse(
            is_same_track(
                source_path="/a/Sensu - Simple.m4a",
                source_artist="Sensu",
                source_title="Simple",
                path="/b/Jellis - If You Want.flac",
                artist="Jellis",
                title="If You Want",
            )
        )

    def test_sanitize_drops_source_and_duplicates(self):
        source = {
            "path": "/lib/Cues Sorted/Energy/31. Sensu - Simple.m4a",
            "artist": "Sensu",
            "title": "Simple",
            "name": "31. Sensu - Simple.m4a",
        }
        recs = {
            "higher_energy": [
                {
                    "path": "/lib/Zouk/Energy/31. Sensu - Simple.m4a",
                    "artist": "Sensu",
                    "title": "Simple",
                    "name": "31. Sensu - Simple.m4a",
                },
                {
                    "path": "/lib/a/Jellis.flac",
                    "artist": "Jellis",
                    "title": "If You Want",
                    "name": "Jellis.flac",
                },
            ],
            "same_energy": [
                {
                    "path": "/lib/b/Jellis.flac",
                    "artist": "Jellis",
                    "title": "If You Want",
                    "name": "Jellis.flac",
                },
                {
                    "path": "/lib/c/SubLab.flac",
                    "artist": "SubLab",
                    "title": "In My Blood",
                    "name": "SubLab.flac",
                },
            ],
            "lower_energy": [
                {
                    "path": "/lib/d/Jellis again.flac",
                    "artist": "Jellis",
                    "title": "If You Want",
                    "name": "Jellis again.flac",
                },
            ],
        }
        with patch("sorter.transition_recs.audio_file_exists", return_value=True):
            out = sanitize_recommendation_buckets(recs, source=source, allowed_paths=None)
        higher = out["higher_energy"]
        same = out["same_energy"]
        lower = out["lower_energy"]
        # current track removed
        self.assertFalse(any(p["artist"] == "Sensu" for p in higher + same + lower))
        # Jellis only once (kept in higher)
        jellis = [
            p
            for p in higher + same + lower
            if track_identity_key(
                path=p["path"], artist=p["artist"], title=p["title"], name=p["name"]
            )
            == track_identity_key(artist="Jellis", title="If You Want")
        ]
        self.assertEqual(len(jellis), 1)
        self.assertEqual(higher[0]["title"], "If You Want")
        self.assertEqual(same[0]["title"], "In My Blood")
        self.assertEqual(lower, [])

    def test_track_block_keys_match_filename_and_tags(self):
        played = track_block_keys(
            path="/Cues/Cues Sorted/Energy/Light/03 - Zhu - Chasing Marrakech.flac",
            artist="Zhu",
            title="Chasing Marrakech",
            name="03 - Zhu - Chasing Marrakech.flac",
        )
        cand = track_block_keys(
            path="/Music/Zouk/Energy/Zhu - Chasing Marrakech.m4a",
            artist="Zhu",
            title="Chasing Marrakech",
            name="Zhu - Chasing Marrakech.m4a",
        )
        self.assertTrue(played & cand)

    def test_sanitize_drops_played_today(self):
        source = {
            "path": "/now/current.flac",
            "artist": "Now",
            "title": "Playing",
            "name": "current.flac",
        }
        recs = {
            "higher_energy": [
                {
                    "path": "/lib/Energy/Light/03 - Zhu - Chasing Marrakech.flac",
                    "artist": "Zhu",
                    "title": "Chasing Marrakech",
                    "name": "03 - Zhu - Chasing Marrakech.flac",
                },
                {
                    "path": "/lib/next.flac",
                    "artist": "Next",
                    "title": "Track",
                    "name": "next.flac",
                },
            ],
            "same_energy": [],
            "lower_energy": [],
        }
        blocked = track_block_keys(
            artist="Zhu",
            title="Chasing Marrakech",
            name="03 - Zhu - Chasing Marrakech.flac",
        )
        with patch("sorter.transition_recs.audio_file_exists", return_value=True):
            out = sanitize_recommendation_buckets(
                recs, source=source, allowed_paths=None, blocked_idents=blocked
            )
        titles = [p["title"] for p in out["higher_energy"]]
        self.assertNotIn("Chasing Marrakech", titles)
        self.assertEqual(titles, ["Track"])

    def test_build_candidates_skips_played_today(self):
        songs = [
            {
                "path": "/lib/Zouk/Energy/Light/zhu.flac",
                "name": "03 - Zhu - Chasing Marrakech.flac",
                "artist": "Zhu",
                "title": "Chasing Marrakech",
                "bpm": 77.0,
                "key": "F",
                "camelot": "7B",
                "cue_count": 3,
                "library": "Zouk",
                "relative_path": "Energy/Light/zhu.flac",
                "energy_hint": "higher",
            },
            {
                "path": "/lib/Zouk/Energy/other.flac",
                "name": "other.flac",
                "artist": "Other",
                "title": "Fresh",
                "bpm": 78.0,
                "key": "F",
                "camelot": "7B",
                "cue_count": 3,
                "library": "Zouk",
                "relative_path": "Energy/other.flac",
                "energy_hint": "same",
            },
        ]
        blocked = track_block_keys(
            artist="Zhu",
            title="Chasing Marrakech",
            path="/Cues/Energy/Light/03 - Zhu - Chasing Marrakech.flac",
        )
        with patch.object(tr, "_scan_library_songs_from_database", return_value=songs), patch.object(
            tr, "_history_counts_for", return_value={}
        ), patch.object(tr, "audio_file_exists", return_value=True), patch.object(
            tr, "played_today_block_keys", return_value=blocked
        ):
            cands = build_candidates(
                source_path="/now/seadoo.m4a",
                source_bpm=77.0,
                source_key="F",
                source_artist="X",
                source_title="Now",
                bpm_tolerance=5,
            )
        titles = {c.title for c in cands}
        self.assertNotIn("Chasing Marrakech", titles)
        self.assertIn("Fresh", titles)

    def test_build_candidates_boosts_matching_genre_family(self):
        songs = [
            {
                "path": "/lib/Zouk/India/dwellers.flac",
                "name": "dwellers.flac",
                "artist": "Desert Dwellers",
                "title": "Anahata",
                "bpm": 90.0,
                "key": "Am",
                "camelot": "8A",
                "cue_count": 3,
                "library": "Zouk",
                "relative_path": "India/dwellers.flac",
                "energy_hint": "same",
                "genre": "Tribal",
                "vibe": "India",
            },
            {
                "path": "/lib/Zouk/Chill/saia.flac",
                "name": "saia.flac",
                "artist": "Saia",
                "title": "Slow",
                "bpm": 91.0,
                "key": "Am",
                "camelot": "8A",
                "cue_count": 3,
                "library": "Zouk",
                "relative_path": "Chill/saia.flac",
                "energy_hint": "same",
                "genre": "R&B",
                "vibe": "Chill",
            },
        ]
        with patch.object(tr, "_scan_library_songs_from_database", return_value=songs), patch.object(
            tr, "_history_counts_for", return_value={}
        ), patch.object(tr, "audio_file_exists", return_value=True):
            cands = build_candidates(
                source_path="/Cues/Add Cues/seadoo.m4a",
                source_bpm=90.0,
                source_key="Am",
                source_artist="Rubí",
                source_title="Seadoo",
                source_genre="alternative R&B",
                source_vibe="Add Cues / Screenshots",
                bpm_tolerance=5,
            )
        by_artist = {c.artist: c for c in cands}
        self.assertIn("Saia", by_artist)
        self.assertIn("Desert Dwellers", by_artist)
        self.assertGreater(by_artist["Saia"].score, by_artist["Desert Dwellers"].score)

    def test_build_candidates_trusts_resolved_family_over_artist_name(self):
        """India Arie must not be scored as tribal just because 'India' is in the name."""
        songs = [
            {
                "path": "/lib/Zouk/India/dwellers.flac",
                "name": "dwellers.flac",
                "artist": "Desert Dwellers",
                "title": "Anahata",
                "bpm": 90.0,
                "key": "Am",
                "camelot": "8A",
                "cue_count": 3,
                "library": "Zouk",
                "relative_path": "India/dwellers.flac",
                "energy_hint": "same",
                "genre": "Tribal",
                "vibe": "India",
            },
            {
                "path": "/lib/Zouk/Chill/arie.flac",
                "name": "arie.flac",
                "artist": "Saia",
                "title": "Slow",
                "bpm": 91.0,
                "key": "Am",
                "camelot": "8A",
                "cue_count": 3,
                "library": "Zouk",
                "relative_path": "Chill/arie.flac",
                "energy_hint": "same",
                "genre": "R&B",
                "vibe": "Chill",
            },
        ]
        with patch.object(tr, "_scan_library_songs_from_database", return_value=songs), patch.object(
            tr, "_history_counts_for", return_value={}
        ), patch.object(tr, "audio_file_exists", return_value=True):
            cands = build_candidates(
                source_path="/Cues/Add Cues/india-arie.m4a",
                source_bpm=90.0,
                source_key="Am",
                source_artist="India Arie",
                source_title="Ready for Love",
                source_genre="neo-soul",
                source_vibe="Add Cues / Screenshots",
                source_genre_family="rnb_soul_zouk",
                bpm_tolerance=5,
            )
        by_artist = {c.artist: c for c in cands}
        self.assertGreater(by_artist["Saia"].score, by_artist["Desert Dwellers"].score)

    def test_recommend_applies_gemini_guess_when_path_unclear(self):
        class _Cues:
            author = "Rubí"
            title = "Seadoo"
            bpm = 90.0
            cue_count = 4
            is_cued = True

        guess = {
            "genre": "alternative R&B",
            "family": "rnb_soul_zouk",
            "confidence": 0.84,
            "reason": "modern vocal R&B",
            "cached": False,
        }
        with patch("sorter.relocate.summarize_cues", return_value=_Cues()), patch(
            "sorter.vdj_now_playing._song_key_from_database", return_value="Am"
        ), patch(
            "sorter.vdj_now_playing._song_genre_and_vibe",
            return_value=("", "Add Cues / Screenshots 7-15-26"),
        ), patch(
            "sorter.genre_guess.guess_genre", return_value=guess
        ) as guess_fn, patch.object(
            tr, "build_candidates", return_value=[]
        ) as build, patch(
            "sorter.vdj_sideview_recs.write_sideview_recs", return_value={"ok": True}
        ), patch.object(
            tr, "lookup_options", return_value=[]
        ):
            out = tr.recommend_transitions(
                path="/Music/Cues/Add Cues/seadoo.m4a",
                use_gemini=True,
            )
        guess_fn.assert_called_once()
        self.assertEqual(build.call_args.kwargs["source_genre"], "alternative R&B")
        self.assertEqual(build.call_args.kwargs["source_genre_family"], "rnb_soul_zouk")
        self.assertEqual(out["source"]["genre"], "alternative R&B")
        self.assertEqual(out["source"]["genre_source"], "gemini")
        self.assertEqual(out["source"]["genre_family"], "rnb_soul_zouk")

    def test_recommend_skips_guess_when_folder_genre_is_clear(self):
        class _Cues:
            author = "Ott"
            title = "The Queen of All Everything"
            bpm = 100.0
            cue_count = 3
            is_cued = True

        with patch("sorter.relocate.summarize_cues", return_value=_Cues()), patch(
            "sorter.vdj_now_playing._song_key_from_database", return_value="Am"
        ), patch(
            "sorter.vdj_now_playing._song_genre_and_vibe",
            return_value=("", "India"),
        ), patch(
            "sorter.genre_guess.guess_genre"
        ) as guess_fn, patch.object(
            tr, "build_candidates", return_value=[]
        ) as build, patch(
            "sorter.vdj_sideview_recs.write_sideview_recs", return_value={"ok": True}
        ), patch.object(
            tr, "lookup_options", return_value=[]
        ):
            out = tr.recommend_transitions(
                path="/Music/Zouk/India/ott.flac",
                use_gemini=True,
            )
        guess_fn.assert_not_called()
        self.assertEqual(build.call_args.kwargs["source_vibe"], "India")
        self.assertEqual(out["source"]["genre_source"], "path")
        self.assertNotEqual(out["source"].get("genre_source"), "gemini")


if __name__ == "__main__":
    unittest.main()
