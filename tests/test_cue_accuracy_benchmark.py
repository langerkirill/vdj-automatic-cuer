"""Color benchmark must follow files that left Add Cues."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cue_accuracy_benchmark import resolve_benchmark_audio


class ResolveBenchmarkAudioTests(unittest.TestCase):
    def test_uses_cues_sorted_copy_when_add_cues_path_is_gone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            missing = root / "Add Cues" / "gone.flac"
            archive = root / "Cues Sorted" / "Chill" / "gone.flac"
            archive.parent.mkdir(parents=True)
            archive.write_bytes(b"x")
            with patch(
                "cue_accuracy_benchmark.Path.home",
                return_value=root,
            ):
                # home()/Music/DJ/... will not exist; still accept a live file.
                self.assertEqual(resolve_benchmark_audio(archive), archive)
            self.assertFalse(missing.is_file())


if __name__ == "__main__":
    unittest.main()
