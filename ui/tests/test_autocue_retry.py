"""Path guards and write_scope helpers for AutoCue retry jobs."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sorter import autocue_retry as retry_mod


class AutoCueRetryPathTests(unittest.TestCase):
    def test_rejects_paths_outside_cues_and_libraries(self):
        with tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp) / "outside.flac"
            outside.write_bytes(b"x")
            with self.assertRaises(ValueError):
                retry_mod._assert_allowed_path(outside)

    def test_allows_ready_for_sort_under_cues(self):
        with tempfile.TemporaryDirectory() as tmp:
            cues = Path(tmp) / "Cues"
            ready = cues / "Ready For Sort"
            ready.mkdir(parents=True)
            audio = ready / "song.flac"
            audio.write_bytes(b"x")
            with patch.object(retry_mod, "CUES_ROOT", cues), patch.object(
                retry_mod, "LIBRARIES", {"House": Path(tmp) / "House"}
            ):
                resolved = retry_mod._assert_allowed_path(audio)
            self.assertEqual(resolved, audio.resolve())

    def test_normalize_write_scope(self):
        self.assertEqual(retry_mod.normalize_write_scope("all"), "all")
        self.assertEqual(retry_mod.normalize_write_scope("both"), "all")
        self.assertEqual(retry_mod.normalize_write_scope("cues"), "cues")
        self.assertEqual(retry_mod.normalize_write_scope("cues_only"), "cues")
        self.assertEqual(retry_mod.normalize_write_scope("loops-only"), "loops")
        with self.assertRaises(ValueError):
            retry_mod.normalize_write_scope("stems")

    def test_write_scope_label(self):
        self.assertIn("cues", retry_mod.write_scope_label("cues"))
        self.assertIn("loops", retry_mod.write_scope_label("loops"))
        self.assertIn("+", retry_mod.write_scope_label("all"))


if __name__ == "__main__":
    unittest.main()
