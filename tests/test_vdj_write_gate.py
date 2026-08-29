"""Every database.xml mutation must take the atomic write gate + file lock."""

from __future__ import annotations

import inspect
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import vdj_database_safety as safety


def _crlf_db(*, songs: int = 1) -> str:
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<VirtualDJ_Database Version="2026">',
    ]
    for i in range(songs):
        parts.append(
            f'<Song FilePath="/tmp/song-{i}.flac">'
            f'<Poi Name="Intro" Pos="0.0" Num="1" Color="1" Type="cue" />'
            f"</Song>"
        )
    parts.append("</VirtualDJ_Database>")
    return "\r\n".join(parts) + "\r\n"


class WriteGateTests(unittest.TestCase):
    def test_write_vdj_database_text_goes_through_atomic_replace(self) -> None:
        src = inspect.getsource(safety.write_vdj_database_text)
        self.assertIn("atomic_replace_database", src)
        self.assertNotIn("write_bytes", src)

    def test_write_vdj_database_text_is_atomic_and_refuses_when_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "database.xml"
            original = _crlf_db(songs=2)
            db.write_bytes(original.encode("utf-8"))
            with patch.object(safety, "is_virtualdj_running", return_value=True), patch.dict(
                os.environ, {}, clear=False
            ):
                os.environ.pop(safety._ALLOW_RUNNING_WRITES_ENV, None)
                with self.assertRaises(safety.VirtualDJRunningError):
                    safety.write_vdj_database_text(db, _crlf_db(songs=2))
            self.assertEqual(db.read_bytes(), original.encode("utf-8"))

            with patch.object(safety, "is_virtualdj_running", return_value=False):
                safety.write_vdj_database_text(db, _crlf_db(songs=3))
            raw = db.read_bytes()
            self.assertEqual(raw.count(b"<Song"), 3)
            self.assertIn(b"\r\n", raw)

    def test_rewrite_song_re_reads_under_exclusive_lock(self) -> None:
        src = inspect.getsource(safety.rewrite_song_xml_in_database)
        self.assertIn("vdj_database_exclusive_lock", src)
        self.assertLess(
            src.find("vdj_database_exclusive_lock"),
            src.find("read_vdj_database_text"),
        )

    def test_concurrent_song_rewrites_keep_both_edits(self) -> None:
        """Two AutoCue/UI writers must not last-write-wins each other's Song."""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "database.xml"
            db.write_bytes(_crlf_db(songs=2).encode("utf-8"))
            barrier = threading.Barrier(2)

            def rewrite(index: int, poi: str) -> None:
                barrier.wait(timeout=2)
                xml = (
                    f'<Song FilePath="/tmp/song-{index}.flac">\n'
                    f'<Poi Name="{poi}" Pos="1.0" Num="1" Color="1" Type="cue" />\n'
                    f"</Song>\n"
                )
                safety.rewrite_song_xml_in_database(
                    db, f"/tmp/song-{index}.flac", xml, validate=False
                )

            with patch.object(safety, "is_virtualdj_running", return_value=False):
                t0 = threading.Thread(target=rewrite, args=(0, "AlphaCue"))
                t1 = threading.Thread(target=rewrite, args=(1, "BetaCue"))
                t0.start()
                t1.start()
                t0.join(timeout=5)
                t1.join(timeout=5)
            raw = db.read_bytes()
            self.assertIn(b"AlphaCue", raw)
            self.assertIn(b"BetaCue", raw)
            self.assertEqual(raw.count(b"<Song"), 2)

    def test_atomic_replace_holds_exclusive_file_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "database.xml"
            db.write_bytes(_crlf_db(songs=2).encode("utf-8"))
            lock_path = safety.vdj_database_lock_path(db)
            order: list[str] = []
            started = threading.Event()
            release = threading.Event()

            def holder() -> None:
                with safety.vdj_database_exclusive_lock(db):
                    order.append("hold")
                    started.set()
                    release.wait(timeout=2)

            t = threading.Thread(target=holder)
            with patch.object(safety, "is_virtualdj_running", return_value=False):
                t.start()
                self.assertTrue(started.wait(timeout=2))
                blocked = threading.Event()

                def writer() -> None:
                    blocked.set()
                    safety.atomic_replace_database(db, _crlf_db(songs=2), original_stats=None)
                    order.append("wrote")

                w = threading.Thread(target=writer)
                w.start()
                self.assertTrue(blocked.wait(timeout=2))
                time.sleep(0.05)
                self.assertNotIn("wrote", order)
                release.set()
                w.join(timeout=2)
                t.join(timeout=2)
            self.assertEqual(order, ["hold", "wrote"])
            self.assertTrue(lock_path.is_file())

    def test_clone_cues_source_does_not_raw_write_database(self) -> None:
        from sorter.playlist_assemble import clone_cues_for_set_paths

        src = inspect.getsource(clone_cues_for_set_paths)
        self.assertIn("atomic_replace_database", src)
        self.assertNotRegex(src, r"write_text\(\s*content")


if __name__ == "__main__":
    unittest.main()
