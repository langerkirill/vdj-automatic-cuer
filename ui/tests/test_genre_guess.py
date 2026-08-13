"""Gemini genre guess when path/tag family is unclear."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sorter import genre_guess as gg


class GenreGuessTests(unittest.TestCase):
    def test_cache_hit_skips_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "genre_guesses.json"
            payload = {
                "label:rubi seadoo": {
                    "genre": "alternative R&B",
                    "family": "rnb_soul_zouk",
                    "confidence": 0.88,
                    "reason": "contemporary vocal R&B",
                }
            }
            cache.write_text(json.dumps(payload), encoding="utf-8")
            with patch.object(gg, "CACHE_PATH", cache), patch.object(
                gg, "_ask_gemini", side_effect=AssertionError("should not call Gemini")
            ):
                out = gg.guess_genre(artist="Rubí", title="Seadoo", path="/inbox/x.m4a")
        self.assertEqual(out["genre"], "alternative R&B")
        self.assertEqual(out["family"], "rnb_soul_zouk")
        self.assertTrue(out["cached"])

    def test_network_result_is_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "genre_guesses.json"
            guess = {
                "genre": "organic tribal",
                "family": "psy_tribal_world",
                "confidence": 0.91,
                "reason": "Desert Dwellers live in the tribal/psy pocket",
            }
            with patch.object(gg, "CACHE_PATH", cache), patch.object(
                gg, "_ask_gemini", return_value=guess
            ) as ask:
                first = gg.guess_genre(
                    artist="Desert Dwellers",
                    title="Anahata",
                    path="/Add Cues/x.flac",
                )
                second = gg.guess_genre(
                    artist="Desert Dwellers",
                    title="Anahata",
                    path="/Add Cues/other-copy.flac",
                )
            self.assertEqual(ask.call_count, 1)
            self.assertEqual(first["genre"], "organic tribal")
            self.assertFalse(first["cached"])
            self.assertTrue(second["cached"])
            saved = json.loads(cache.read_text(encoding="utf-8"))
            self.assertIn("label:desert dwellers anahata", saved)

    def test_guess_failure_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "genre_guesses.json"
            with patch.object(gg, "CACHE_PATH", cache), patch.object(
                gg, "_ask_gemini", side_effect=RuntimeError("no key")
            ):
                self.assertIsNone(
                    gg.guess_genre(artist="X", title="Y", path="/z.m4a")
                )

    def test_cache_key_needs_both_artist_and_title(self):
        self.assertEqual(
            gg.guess_cache_key(artist="Drake", title=""),
            "",
        )
        self.assertTrue(
            gg.guess_cache_key(
                artist="Drake", title="", name="12. Drake - Passionfruit.m4a"
            ).startswith("name:")
        )
        self.assertEqual(
            gg.guess_cache_key(artist="Rubí", title="Seadoo"),
            "label:rubi seadoo",
        )

    def test_low_confidence_cache_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "genre_guesses.json"
            cache.write_text(
                json.dumps(
                    {
                        "label:x y": {
                            "genre": "jazz",
                            "family": "other",
                            "confidence": 0.1,
                        }
                    }
                ),
                encoding="utf-8",
            )
            fresh = {
                "genre": "alternative R&B",
                "family": "rnb_soul_zouk",
                "confidence": 0.8,
                "reason": "retry",
            }
            with patch.object(gg, "CACHE_PATH", cache), patch.object(
                gg, "_ask_gemini", return_value=fresh
            ):
                out = gg.guess_genre(artist="X", title="Y", path="/z.m4a")
        self.assertEqual(out["genre"], "alternative R&B")
        self.assertFalse(out["cached"])

    def test_resolve_does_not_forward_unclear_tag_to_gemini(self):
        with patch.object(
            gg,
            "guess_genre",
            return_value={
                "genre": "alternative R&B",
                "family": "rnb_soul_zouk",
                "confidence": 0.8,
                "reason": "ok",
                "cached": False,
            },
        ) as guess:
            gg.resolve_source_genre(
                genre="Unknown",
                vibe="Add Cues / Screenshots",
                artist="Rubí",
                title="Seadoo",
                path="/x.m4a",
            )
        self.assertEqual(guess.call_args.kwargs["genre"], "")

    def test_normalize_family_from_free_text(self):
        self.assertEqual(gg.normalize_family("R&B / neo-soul"), "rnb_soul_zouk")
        self.assertEqual(gg.normalize_family("tribal psy"), "psy_tribal_world")
        self.assertEqual(gg.normalize_family("deep house"), "house_dance")
        self.assertEqual(gg.normalize_family("rnb_soul_zouk"), "rnb_soul_zouk")
        self.assertEqual(gg.normalize_family("jazz fusion"), "other")

    def test_resolve_skips_gemini_when_path_is_clear(self):
        with patch.object(gg, "guess_genre") as guess:
            out = gg.resolve_source_genre(
                genre="",
                vibe="India",
                artist="Ott",
                title="The Queen of All Everything",
            )
        guess.assert_not_called()
        self.assertEqual(out["genre_source"], "path")
        self.assertEqual(out["genre_family"], "psy_tribal_world")
        self.assertEqual(out["genre"], "")

    def test_resolve_uses_tag_without_guessing(self):
        with patch.object(gg, "guess_genre") as guess:
            out = gg.resolve_source_genre(
                genre="R&B",
                vibe="Add Cues / Screenshots 7-15-26",
                artist="Rubí",
                title="Seadoo",
            )
        guess.assert_not_called()
        self.assertEqual(out["genre_source"], "tag")
        self.assertEqual(out["genre"], "R&B")
        self.assertEqual(out["genre_family"], "rnb_soul_zouk")

    def test_resolve_guesses_when_inbox_path_is_unclear(self):
        with patch.object(
            gg,
            "guess_genre",
            return_value={
                "genre": "alternative R&B",
                "family": "rnb_soul_zouk",
                "confidence": 0.84,
                "reason": "modern vocal R&B",
                "cached": False,
            },
        ) as guess:
            out = gg.resolve_source_genre(
                genre="",
                vibe="Add Cues / Screenshots 7-15-26",
                artist="Rubí",
                title="Seadoo",
                path="/Music/Cues/Add Cues/x.m4a",
            )
        guess.assert_called_once()
        self.assertEqual(out["genre"], "alternative R&B")
        self.assertEqual(out["genre_source"], "gemini")
        self.assertEqual(out["genre_family"], "rnb_soul_zouk")
        self.assertEqual(out["vibe"], "Add Cues / Screenshots 7-15-26")


if __name__ == "__main__":
    unittest.main()
