"""Now-playing detection prefers LastPlay (played) over deck-load."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from sorter import vdj_now_playing as np_mod
from sorter.vdj_now_playing import (
    best_lastplay_from_xml,
    get_now_playing,
    history_plays_on_dates,
    latest_deck_waveform,
    now_playing_stamp,
    pick_now_playing,
    recent_history_play_groups,
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

    def test_pick_ignores_deck_load_while_history_is_fresh(self):
        hist = (1_000, "/played.flac", "P", "Played")
        deck = (1_030, "/only-loaded.flac", "", "Loaded")
        picked, source = pick_now_playing(hist, None, deck)
        self.assertEqual(source, "history")
        self.assertEqual(picked[1], "/played.flac")

    def test_pick_uses_deck_when_history_lastplay_is_stale(self):
        hist = (1_000, "/yesterday.flac", "Y", "Yesterday")
        deck = (1_000 + 16 * 60, "/now.flac", "", "Now")
        picked, source = pick_now_playing(hist, None, deck)
        self.assertEqual(source, "deck")
        self.assertEqual(picked[1], "/now.flac")

    def test_pick_uses_deck_when_database_lastplay_is_also_stale(self):
        hist = (100, "/old.flac", "O", "Old")
        db = (400, "/db.flac", "D", "Db")
        deck = (400 + 20 * 60, "/now.flac", "", "Now")
        picked, source = pick_now_playing(hist, db, deck)
        self.assertEqual(source, "deck")
        self.assertEqual(picked[1], "/now.flac")

    def test_pick_uses_deck_when_no_play_source(self):
        deck = (9_000, "/loaded.flac", "", "Loaded")
        picked, source = pick_now_playing(None, None, deck)
        self.assertEqual(source, "deck")
        self.assertEqual(picked[3], "Loaded")

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

    def test_get_now_playing_follows_deck_when_history_is_stale(self):
        hist = (1_000, "/yesterday.flac", "Y", "Yesterday")
        loaded = (1_000 + 40 * 60, "/now.flac", "", "Now Playing")
        with patch("sorter.vdj_now_playing.latest_history_entry", return_value=hist), patch(
            "sorter.vdj_now_playing.latest_database_play", return_value=None
        ), patch(
            "sorter.vdj_now_playing.latest_deck_waveform", return_value=loaded
        ):
            np = get_now_playing(enrich=False)
        self.assertIsNotNone(np)
        assert np is not None
        self.assertEqual(np.source, "deck")
        self.assertEqual(np.path, "/now.flac")
        self.assertIn("Now Playing", np.title)

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

    def test_history_plays_on_dates_reads_yesterday_and_nested(self):
        import tempfile
        from datetime import date
        from pathlib import Path

        yesterday = date(2026, 8, 28)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "2026-08-28.m3u").write_text(
                "#EXTVDJ:<lastplaytime>10</lastplaytime>"
                "<artist>June Freedom</artist><title>Collabo</title>\n"
                "/lib/040. June Freedom - Collabo.flac\n",
                encoding="utf-8",
            )
            nested = root / "2026" / "08"
            nested.mkdir(parents=True)
            (nested / "2026-08-27.m3u").write_text(
                "#EXTVDJ:<lastplaytime>20</lastplaytime>"
                "<artist>Sean Finn, Corona</artist>"
                "<title>The Rhythm of the Night (Jay Frog Remix)</title>\n"
                "/lib/jay-frog.m4a\n",
                encoding="utf-8",
            )
            with patch("sorter.vdj_now_playing.VDJ_HISTORY_DIR", root):
                plays = history_plays_on_dates({yesterday, date(2026, 8, 27)})
        titles = {p[3] for p in plays}
        self.assertEqual(titles, {"Collabo", "The Rhythm of the Night (Jay Frog Remix)"})

    def test_recent_history_play_groups_splits_today_yesterday_earlier(self):
        import tempfile
        from datetime import date
        from pathlib import Path

        today = date(2026, 8, 29)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "2026-08-29.m3u").write_text(
                "#EXTVDJ:<lastplaytime>30</lastplaytime>"
                "<artist>Today</artist><title>Now</title>\n/lib/now.flac\n",
                encoding="utf-8",
            )
            (root / "2026-08-28.m3u").write_text(
                "#EXTVDJ:<lastplaytime>20</lastplaytime>"
                "<artist>Yest</artist><title>Yesterday</title>\n/lib/yest.flac\n",
                encoding="utf-8",
            )
            (root / "2026-08-27.m3u").write_text(
                "#EXTVDJ:<lastplaytime>10</lastplaytime>"
                "<artist>Thu</artist><title>Earlier</title>\n/lib/earlier.flac\n",
                encoding="utf-8",
            )
            (root / "2026-08-20.m3u").write_text(
                "#EXTVDJ:<lastplaytime>1</lastplaytime>"
                "<artist>Old</artist><title>TooOld</title>\n/lib/old.flac\n",
                encoding="utf-8",
            )
            with patch("sorter.vdj_now_playing.VDJ_HISTORY_DIR", root):
                groups = recent_history_play_groups(days=3, today=today)
        self.assertEqual([p[3] for p in groups["today"]], ["Now"])
        self.assertEqual([p[3] for p in groups["yesterday"]], ["Yesterday"])
        self.assertEqual([p[3] for p in groups["earlier"]], ["Earlier"])
        all_titles = {p[3] for p in groups["all"]}
        self.assertEqual(all_titles, {"Now", "Yesterday", "Earlier"})
        self.assertNotIn("TooOld", all_titles)

    def test_now_playing_stamp_is_history_only(self):
        hist = (500, "/played.flac", "P", "Played")
        with patch("sorter.vdj_now_playing.latest_history_entry", return_value=hist), patch(
            "sorter.vdj_now_playing._newest_history_file", return_value=None
        ), patch("sorter.vdj_now_playing.VDJ_DATABASE") as db, patch(
            "sorter.vdj_now_playing._waveform_cache_mtime", return_value=500.0
        ), patch(
            "sorter.vdj_now_playing.latest_deck_waveform",
            return_value=(530, "/only-loaded.flac", "", "Loaded"),
        ):
            db.is_file.return_value = False
            stamp = now_playing_stamp()
        self.assertEqual(stamp["path"], "/played.flac")
        self.assertEqual(stamp["lastplay"], 500)
        self.assertEqual(stamp["title"], "Played")
        self.assertEqual(stamp["source"], "history")

    def test_latest_deck_waveform_caches_until_cache_mtime_changes(self):
        np_mod._deck_scan_cache["mtime"] = None
        np_mod._deck_scan_cache["entry"] = None
        with patch.object(np_mod, "_waveform_cache_mtime", return_value=10.0), patch.object(
            np_mod, "_waveforms_latest_row", return_value=("/lib/", "now.flac")
        ) as row_fn:
            first = latest_deck_waveform()
            second = latest_deck_waveform()
        self.assertEqual(row_fn.call_count, 1)
        self.assertEqual(first[1], "/lib/now.flac")
        self.assertEqual(second[1], first[1])
        np_mod._deck_scan_cache["mtime"] = None
        np_mod._deck_scan_cache["entry"] = None

    def test_now_playing_stamp_follows_deck_when_history_is_stale(self):
        hist = (1_000, "/yesterday.flac", "Y", "Yesterday")
        deck = (1_000 + 25 * 60, "/now.flac", "", "Now Playing")
        with patch("sorter.vdj_now_playing.latest_history_entry", return_value=hist), patch(
            "sorter.vdj_now_playing._newest_history_file", return_value=None
        ), patch("sorter.vdj_now_playing.VDJ_DATABASE") as db, patch(
            "sorter.vdj_now_playing._waveform_cache_mtime", return_value=float(deck[0])
        ), patch(
            "sorter.vdj_now_playing.latest_deck_waveform", return_value=deck
        ):
            db.is_file.return_value = False
            stamp = now_playing_stamp()
        self.assertEqual(stamp["path"], "/now.flac")
        self.assertEqual(stamp["source"], "deck")
        self.assertEqual(stamp["title"], "Now Playing")
        self.assertEqual(stamp["lastplay"], 0)
        self.assertEqual(stamp["mtime"], 0)


if __name__ == "__main__":
    unittest.main()
