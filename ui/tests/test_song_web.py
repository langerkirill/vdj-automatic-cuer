"""Public-web song blurbs for assemble evals."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sorter import song_web as sw


class SongWebTests(unittest.TestCase):
    def test_cache_hit_skips_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "blurbs.json"
            cache.write_text(
                json.dumps(
                    {
                        "label:sade by your side": {
                            "blurb": "Quiet-storm soul ballad.",
                        }
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(sw, "CACHE_PATH", cache), patch.object(
                sw, "wikipedia_blurb", side_effect=AssertionError("no net")
            ):
                out = sw.lookup_song_blurb(artist="Sade", title="By Your Side")
        self.assertEqual(out, "Quiet-storm soul ballad.")

    def test_wikipedia_then_itunes_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "blurbs.json"
            with patch.object(sw, "CACHE_PATH", cache), patch.object(
                sw, "wikipedia_blurb", return_value=""
            ), patch.object(
                sw, "itunes_blurb", return_value="R&B/Soul · Lovers Rock"
            ):
                out = sw.lookup_song_blurb(artist="Sade", title="By Your Side")
        self.assertEqual(out, "R&B/Soul · Lovers Rock")

    def test_clip_blurb_keeps_two_sentences(self):
        text = (
            "Neo-soul single released in 2000. It became a quiet-storm staple. "
            "Later covered by many artists."
        )
        out = sw._clip_blurb(text)
        self.assertIn("Neo-soul", out)
        self.assertIn("quiet-storm", out)
        self.assertNotIn("Later covered", out)

    def test_lookup_blurbs_for_tracks_keys_by_path(self):
        tracks = [
            {
                "path": "/zouk/a.flac",
                "artist": "Sade",
                "title": "By Your Side",
            }
        ]
        with patch.object(
            sw, "lookup_song_blurb", return_value="Quiet-storm soul ballad."
        ):
            out = sw.lookup_blurbs_for_tracks(tracks)
        self.assertEqual(out["/zouk/a.flac"], "Quiet-storm soul ballad.")


if __name__ == "__main__":
    unittest.main()
