"""Now-playing detection prefers LastPlay (played) over deck-load."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from sorter.vdj_now_playing import (
    best_lastplay_from_xml,
    get_now_playing,
    pick_now_playing,
    todays_history_plays,
)


class NowPlayingParseTests(unittest.TestCase):
    def test_lastplay_regex_picks_newest(self):
        xml = """
<VirtualDJ_Database>
 <Song FilePath="/lib/old.flac">
  <Tags Author="A" Title="Old" />
  <Infos LastPlay="100" />
 </Song>
 <Song FilePath="/lib/playing.flac">
  <Tags Author="B" Title="Now" />
  <Infos SongLength="1" LastModified="200" LastPlay="200" PlayCount="3" />
 </Song>
 <Song FilePath="/lib/loaded-not-played.flac">
  <Tags Author="C" Title="Cued" />
  <Infos LastModified="250" LastPlay="50" />
 </Song>
</VirtualDJ_Database>
"""
        entry = best_lastplay_from_xml(xml)
        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertEqual(entry[0], 200)
        self.assertEqual(entry[1], "/lib/playing.flac")
        self.assertEqual(entry[2], "B")
        self.assertEqual(entry[3], "Now")

    def test_pick_prefers_newer_history_over_older_database(self):
        hist = (300, "/lib/new.flac", "N", "New")
        db = (200, "/lib/old.flac", "O", "Old")
        picked, source = pick_now_playing(hist, db)
        self.assertEqual(source, "history")
        self.assertEqual(picked[1], "/lib/new.flac")

    def test_pick_prefers_newer_database_over_stale_history_line(self):
        hist = (100, "/lib/old.flac", "O", "Old")
        db = (400, "/lib/new.flac", "N", "New")
        picked, source = pick_now_playing(hist, db)
        self.assertEqual(source, "database")
        self.assertEqual(picked[3], "New")

    def test_get_now_playing_does_not_use_waveform_load(self):
        hist = (500, "/played.flac", "P", "Played")
        loaded = (999, "/only-loaded.flac", "", "Loaded")
        with patch("sorter.vdj_now_playing.latest_history_entry", return_value=hist), patch(
            "sorter.vdj_now_playing.latest_database_play", return_value=None
        ), patch(
            "sorter.vdj_now_playing.latest_deck_waveform", return_value=loaded
        ):
            np = get_now_playing(enrich=False)
        self.assertIsNotNone(np)
        assert np is not None
        self.assertEqual(np.title, "Played")
        self.assertNotIn("Loaded", np.title)

    def test_todays_history_plays_reads_dated_m3u(self):
        import tempfile
        from datetime import datetime
        from pathlib import Path

        today = datetime.now().date().isoformat()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / f"{today}.m3u").write_text(
                "#EXTVDJ:<lastplaytime>1</lastplaytime>"
                "<artist>Zhu</artist><title>Chasing Marrakech</title>\n"
                "/lib/03 - Zhu - Chasing Marrakech.flac\n",
                encoding="utf-8",
            )
            with patch("sorter.vdj_now_playing.VDJ_HISTORY_DIR", root), patch(
                "sorter.vdj_now_playing._newest_history_file",
                return_value=root / f"{today}.m3u",
            ):
                plays = todays_history_plays()
        self.assertEqual(len(plays), 1)
        self.assertEqual(plays[0][2], "Zhu")
        self.assertEqual(plays[0][3], "Chasing Marrakech")


if __name__ == "__main__":
    unittest.main()
