"""Regression: refuse database.xml writes while VirtualDJ is running."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import vdj_database_safety as safety


def _minimal_db(*, songs: int = 2, crlf: bool = True) -> str:
    nl = "\r\n" if crlf else "\n"
    parts = ['<?xml version="1.0" encoding="UTF-8"?>', "<VirtualDJ_Database Version=\"2026\">"]
    for i in range(songs):
        parts.append(
            f'<Song FilePath="/tmp/song-{i}.flac">'
            f'<Poi Name="Intro" Pos="0.0" Num="1" Color="1" Type="cue" />'
            f"</Song>"
        )
    parts.append("</VirtualDJ_Database>")
    return nl.join(parts) + nl


class VirtualDJRunningWriteGuardTests(unittest.TestCase):
    def test_assert_blocks_when_running(self):
        with patch.object(safety, "is_virtualdj_running", return_value=True), patch.dict(
            os.environ, {}, clear=False
        ):
            os.environ.pop(safety._ALLOW_RUNNING_WRITES_ENV, None)
            with self.assertRaises(safety.VirtualDJRunningError):
                safety.assert_safe_to_write_vdj_database()

    def test_assert_allows_when_closed(self):
        with patch.object(safety, "is_virtualdj_running", return_value=False):
            safety.assert_safe_to_write_vdj_database()  # no raise

    def test_env_override_allows_running_write(self):
        with patch.object(safety, "is_virtualdj_running", return_value=True), patch.dict(
            os.environ, {safety._ALLOW_RUNNING_WRITES_ENV: "1"}
        ):
            safety.assert_safe_to_write_vdj_database()

    def test_atomic_replace_refuses_when_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "database.xml"
            db.write_bytes(_minimal_db().encode("utf-8"))
            with patch.object(safety, "is_virtualdj_running", return_value=True), patch.dict(
                os.environ, {}, clear=False
            ):
                os.environ.pop(safety._ALLOW_RUNNING_WRITES_ENV, None)
                with self.assertRaises(safety.VirtualDJRunningError):
                    safety.atomic_replace_database(
                        db,
                        _minimal_db(songs=2),
                        original_stats=None,
                    )
            # Unchanged on disk
            self.assertIn(b"<Song", db.read_bytes())

    def test_atomic_replace_succeeds_when_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "database.xml"
            original = _minimal_db(songs=3)
            db.write_bytes(original.encode("utf-8"))
            with patch.object(safety, "is_virtualdj_running", return_value=False):
                safety.atomic_replace_database(db, _minimal_db(songs=3), original_stats=None)
            self.assertEqual(db.read_bytes().count(b"<Song"), 3)

    def test_atomic_replace_snapshots_golden_for_full_library(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "database.xml"
            body = _minimal_db(songs=5, crlf=True)
            body = body.replace(
                "</VirtualDJ_Database>",
                "<!--" + ("g" * (safety.WIPE_SIZE_BYTES + 80)) + "-->\r\n</VirtualDJ_Database>\r\n",
            )
            db.write_bytes(body.encode("utf-8"))
            safety._LAST_AUTO_GOLDEN_MONO = 0.0
            with patch.object(safety, "is_virtualdj_running", return_value=False):
                safety.atomic_replace_database(db, body, original_stats=None)
            goldens = list(Path(tmp).glob(f"{safety.AUTO_GOLDEN_PREFIX}*"))
            self.assertTrue(goldens, "expected rolling auto golden after full-library write")


class DatabaseAutoHealTests(unittest.TestCase):
    def test_quick_fingerprint_flags_wipe(self):
        with tempfile.TemporaryDirectory() as tmp:
            wiped = Path(tmp) / "database.xml"
            # Classic VDJ wipe: tiny stub library
            wiped.write_bytes(
                b'<?xml version="1.0"?>\r\n'
                b'<VirtualDJ_Database Version="2026">\r\n'
                b'<Song FilePath="/a.mp3"></Song>\r\n'
                b"</VirtualDJ_Database>\r\n"
            )
            fp = safety.quick_database_fingerprint(wiped)
            self.assertFalse(fp["healthy"])
            self.assertEqual(fp["reason"], "wipe_size")

    def test_quick_fingerprint_flags_missing_crlf_on_large_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "database.xml"
            body = _minimal_db(songs=5, crlf=False)
            pad = "<!--" + ("x" * (safety.WIPE_SIZE_BYTES + 100)) + "-->\n"
            body = body.replace("</VirtualDJ_Database>", pad + "</VirtualDJ_Database>")
            db.write_bytes(body.encode("utf-8"))
            fp = safety.quick_database_fingerprint(db)
            self.assertFalse(fp["healthy"])
            self.assertEqual(fp["reason"], "missing_crlf")

    def test_recover_from_golden_when_wiped(self):
        with tempfile.TemporaryDirectory() as tmp:
            vdj = Path(tmp)
            db = vdj / "database.xml"
            healthy = _minimal_db(songs=20, crlf=True)
            healthy = healthy.replace(
                "</VirtualDJ_Database>",
                "<!--" + ("y" * (safety.WIPE_SIZE_BYTES + 50)) + "-->\r\n</VirtualDJ_Database>\r\n",
            )
            golden = vdj / f"{safety.AUTO_GOLDEN_PREFIX}20260101_000000"
            golden.write_bytes(healthy.encode("utf-8"))

            db.write_bytes(
                b'<?xml version="1.0"?>\r\n<VirtualDJ_Database Version="2026">\r\n'
                b"</VirtualDJ_Database>\r\n"
            )

            with patch.object(safety, "is_virtualdj_running", return_value=False):
                result = safety.recover_vdj_database_if_wiped(db)

            self.assertTrue(result["recovered"], result)
            fp = safety.quick_database_fingerprint(db)
            self.assertTrue(fp["healthy"], fp)
            self.assertGreaterEqual(fp["size_bytes"], safety.WIPE_SIZE_BYTES)

    def test_recover_refuses_while_vdj_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "database.xml"
            db.write_bytes(b"<VirtualDJ_Database></VirtualDJ_Database>\r\n")
            with patch.object(safety, "is_virtualdj_running", return_value=True), patch.dict(
                os.environ, {}, clear=False
            ):
                os.environ.pop(safety._ALLOW_RUNNING_WRITES_ENV, None)
                result = safety.recover_vdj_database_if_wiped(db)
            self.assertFalse(result["recovered"])
            self.assertEqual(result["error"], "virtualdj_running")

    def test_ensure_healthy_noops_when_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "database.xml"
            body = _minimal_db(songs=5, crlf=True)
            body = body.replace(
                "</VirtualDJ_Database>",
                "<!--" + ("z" * (safety.WIPE_SIZE_BYTES + 50)) + "-->\r\n</VirtualDJ_Database>\r\n",
            )
            db.write_bytes(body.encode("utf-8"))
            with patch.object(safety, "is_virtualdj_running", return_value=False):
                out = safety.ensure_healthy_vdj_database(db)
            self.assertTrue(out["ok"])
            self.assertFalse(out["recovered"])

    def test_ensure_healthy_allows_tiny_test_db_without_golden(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "database.xml"
            db.write_bytes(_minimal_db(songs=2, crlf=True).encode("utf-8"))
            with patch.object(safety, "is_virtualdj_running", return_value=False):
                out = safety.ensure_healthy_vdj_database(db)
            self.assertTrue(out["ok"])
            self.assertFalse(out["recovered"])


if __name__ == "__main__":
    unittest.main()
