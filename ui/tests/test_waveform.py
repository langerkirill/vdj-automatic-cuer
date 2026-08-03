"""Waveform envelope builder (uses ffmpeg when available)."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from sorter.waveform import build_waveform, decode_envelope


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


@unittest.skipUnless(ffmpeg_available(), "ffmpeg/ffprobe required")
class WaveformTests(unittest.TestCase):
    def _make_tone(self, path: Path, seconds: float = 0.5) -> None:
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency=440:duration={seconds}",
                "-y",
                str(path),
            ],
            check=True,
        )

    def test_decode_envelope_has_bins(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "tone.wav"
            self._make_tone(audio)
            peaks = decode_envelope(str(audio), bins=64)
            self.assertEqual(len(peaks), 64)
            self.assertTrue(any(p > 0 for p in peaks))

    def test_build_waveform_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "tone.wav"
            self._make_tone(audio)
            data = build_waveform(audio, bins=128, use_cache=False)
            self.assertEqual(data["bins"], 128)
            self.assertEqual(len(data["peaks"]), 128)
            self.assertGreater(data["duration"], 0.3)


if __name__ == "__main__":
    unittest.main()
