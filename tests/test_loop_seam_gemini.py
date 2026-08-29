"""Tests for perceptual loop wrap clips and seam retry behavior."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from vdj_cuer.loop_seam_gemini import (
    LOOP_SEAM_CLIP_SECONDS,
    LOOP_SEAM_MAX_ATTEMPTS,
    build_loop_wrap_clip,
    seam_half_seconds,
)
from vdj_cuer.stems import StemMixin


class LoopSeamClipTests(unittest.TestCase):
    def test_default_half_is_three_seconds(self):
        self.assertEqual(LOOP_SEAM_CLIP_SECONDS, 3.0)
        self.assertEqual(seam_half_seconds(20.0), 3.0)
        # Short loops use half the loop, not a fixed 3s.
        self.assertEqual(seam_half_seconds(4.0), 2.0)

    def test_build_loop_wrap_clip_end_then_start(self):
        if not os.path.exists("/opt/homebrew/bin/ffmpeg") and not os.path.exists(
            "/usr/bin/ffmpeg"
        ):
            self.skipTest("ffmpeg not available")

        # 10s of silence with a marker tone in first 0.2s and last 0.2s of a
        # 6s loop region starting at t=2 — clip should be ~6s of audio (3+3).
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "src.wav")
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:duration=10",
                    "-ac",
                    "1",
                    src,
                ],
                check=True,
            )
            out, half = build_loop_wrap_clip(src, loop_start=2.0, loop_duration=6.0)
            try:
                self.assertEqual(half, 3.0)
                self.assertTrue(os.path.isfile(out))
                self.assertGreater(os.path.getsize(out), 1000)
            finally:
                if os.path.exists(out):
                    os.remove(out)


class LoopSeamClientTests(unittest.TestCase):
    def test_available_when_dedicated_gemini_client_is_set(self):
        from automatic_music_cuer_gemini import AutomaticMusicCuer

        cuer = AutomaticMusicCuer.__new__(AutomaticMusicCuer)
        cuer.client = object()
        self.assertTrue(cuer._gemini_loop_seam_available())
        cuer.client = None
        self.assertFalse(cuer._gemini_loop_seam_available())


class LoopSeamRetryTests(unittest.TestCase):
    def test_retry_placements_stay_on_the_phrase_one(self):
        placements = StemMixin._loop_retry_placements(
            10.0, 16, 0.5, max_attempts=LOOP_SEAM_MAX_ATTEMPTS
        )
        self.assertEqual(placements[0], (10.0, 16))
        starts = {start for start, _ in placements}
        self.assertEqual(starts, {10.0})
        beats = {beats for _, beats in placements}
        self.assertTrue(beats <= {4, 8, 16, 32})
        self.assertIn(8, beats)
        self.assertEqual(LOOP_SEAM_MAX_ATTEMPTS, 3)

    def test_validate_keeps_stem_clean_loop_on_the_original_one(self):
        """Gemini wrap is advisory; never nudge off the yellow [1]."""
        from vdj_cuer.stem_evidence import StemProfile
        from automatic_music_cuer_gemini import AutomaticMusicCuer

        cuer = AutomaticMusicCuer.__new__(AutomaticMusicCuer)
        steady = [0.55, 0.6, 0.5, 0.58] * 80
        profiles = {
            "kick": StemProfile.from_frames(steady, frame_seconds=0.25),
            "hihat": StemProfile.from_frames([0.2] * 320, frame_seconds=0.25),
            "instruments": StemProfile.from_frames(steady, frame_seconds=0.25),
            "bass": StemProfile.from_frames(steady, frame_seconds=0.25),
            "vocal": StemProfile.from_frames([0.001] * 320, frame_seconds=0.25),
        }

        with patch.object(cuer, "validate_color_assignment", return_value="green"), patch.object(
            cuer, "_evaluate_loop_seam_with_gemini", return_value=False
        ) as gemini:
            accepted = cuer._validate_loop_candidate(
                profiles=profiles,
                start=0.0,
                length_beats=16,
                beat_duration=0.5,
                model_elements=["drums", "bass", "synth"],
                loop_name="Test",
                audio_file_path="/tmp/does-not-need-to-exist-for-mock.m4a",
                require_gemini_seam=True,
            )

        self.assertIsNotNone(accepted)
        self.assertEqual(accepted["start"], 0.0)
        self.assertEqual(gemini.call_count, 1)

    def test_validate_accepts_loop_when_vocals_are_already_on_the_one(self):
        from vdj_cuer.stem_evidence import StemProfile
        from automatic_music_cuer_gemini import AutomaticMusicCuer

        cuer = AutomaticMusicCuer.__new__(AutomaticMusicCuer)
        steady = [0.55, 0.6, 0.5, 0.58] * 80
        profiles = {
            "kick": StemProfile.from_frames(steady, frame_seconds=0.25),
            "hihat": StemProfile.from_frames([0.2] * 320, frame_seconds=0.25),
            "instruments": StemProfile.from_frames(steady, frame_seconds=0.25),
            "bass": StemProfile.from_frames(steady, frame_seconds=0.25),
            "vocal": StemProfile.from_frames([0.7] * 320, frame_seconds=0.25),
        }

        with patch.object(cuer, "validate_color_assignment", return_value="green"):
            accepted = cuer._validate_loop_candidate(
                profiles=profiles,
                start=0.0,
                length_beats=16,
                beat_duration=0.5,
                model_elements=["drums", "vocals", "bass", "synth"],
                loop_name="Vocal Groove",
                audio_file_path=None,
                require_gemini_seam=False,
            )

        self.assertIsNotNone(accepted)
        self.assertEqual(accepted["start"], 0.0)

    def test_zero_loops_ok_when_nothing_passes(self):
        """Song may correctly keep no loops after all retries fail."""
        from automatic_music_cuer_gemini import AutomaticMusicCuer
        from vdj_cuer.stem_evidence import StemProfile

        cuer = AutomaticMusicCuer.__new__(AutomaticMusicCuer)
        silent = StemProfile.from_frames([0.001] * 40, frame_seconds=0.25)
        profiles = {
            "kick": silent,
            "hihat": silent,
            "instruments": silent,
            "bass": silent,
            "vocal": silent,
        }
        analysis = {
            "measure_changes": [],
            "loop_segments": [
                {
                    "start": 0.0,
                    "length_beats": 16,
                    "elements": ["drums"],
                    "loop_name": "Nope",
                    "confidence": 0.9,
                }
            ],
        }
        cuer._track_audio_cache = type(
            "Cache",
            (),
            {"get_or_load_stem_profiles": staticmethod(lambda files: profiles)},
        )()
        with patch.object(cuer, "_discover_stem_validated_loops", return_value=[]):
            result = cuer._apply_measured_stem_activity(
                analysis,
                stem_files=[("kick", "/tmp/kick.m4a")],
                bpm=120.0,
                audio_file_path=None,
            )
        self.assertEqual(result["loop_segments"], [])


if __name__ == "__main__":
    unittest.main()
