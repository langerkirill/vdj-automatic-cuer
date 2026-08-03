"""Durable action log append/read/seed."""

import tempfile
import unittest
from pathlib import Path

from sorter.action_log import append_action, read_actions, seed_historical_sorts


class ActionLogTests(unittest.TestCase):
    def test_append_and_read_newest_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "actions.jsonl"
            append_action(
                "sort",
                source_path="/a/Ready/x.flac",
                dest_path="/a/Zouk/Chill/x.flac",
                name="x.flac",
                details={"relative_folder": "Chill"},
                log_file=log,
            )
            append_action(
                "remove_ready",
                source_path="/a/Ready/y.flac",
                name="y.flac",
                log_file=log,
            )
            rows = read_actions(limit=10, log_file=log)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["action"], "remove_ready")
            self.assertEqual(rows[1]["name"], "x.flac")

    def test_seed_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "actions.jsonl"
            entries = [
                {
                    "ts": "2026-07-28T19:03:00-06:00",
                    "name": "a.m4a",
                    "dest_path": "/Zouk/Hip Hoppy/a.m4a",
                }
            ]
            n1 = seed_historical_sorts(entries, log_file=log)
            n2 = seed_historical_sorts(entries, log_file=log)
            self.assertEqual(n1, 1)
            self.assertEqual(n2, 0)
            self.assertEqual(len(read_actions(log_file=log)), 1)


if __name__ == "__main__":
    unittest.main()
