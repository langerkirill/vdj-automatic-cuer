"""Stem ffmpeg/EPIPE failures must fail over to mix-only, not crash AutoCue."""

import errno
import unittest
from unittest.mock import Mock, patch

import automatic_music_cuer_gemini as cuer_module
from vdj_cuer.beatgrid_sources import (
    BeatgridSourceMixin,
    run_with_mix_only_stem_failover,
)
from vdj_cuer.common import StemDecodeError, is_stem_decode_error


class StemDecodeErrorTests(unittest.TestCase):
    def test_is_stem_decode_error_detects_epipe_and_wrapper(self):
        self.assertTrue(is_stem_decode_error(BrokenPipeError(32, "Broken pipe")))
        self.assertTrue(is_stem_decode_error(OSError(errno.EPIPE, "Broken pipe")))
        self.assertTrue(is_stem_decode_error(StemDecodeError("kick stem ffmpeg failed")))
        self.assertTrue(
            is_stem_decode_error(RuntimeError("[Errno 32] Broken pipe"))
        )
        self.assertFalse(is_stem_decode_error(ValueError("bad bpm")))


class DecodeOnsetEnvelopeStemFailoverTests(unittest.TestCase):
    def setUp(self):
        self.helper = BeatgridSourceMixin()

    def test_stem_epipe_becomes_stem_decode_error(self):
        with (
            patch("vdj_cuer.beatgrid_sources.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch(
                "vdj_cuer.beatgrid_sources.subprocess.run",
                side_effect=BrokenPipeError(errno.EPIPE, "Broken pipe"),
            ),
        ):
            with self.assertRaises(StemDecodeError) as ctx:
                self.helper._decode_onset_envelope("/tmp/song.m4a.vdjstems", "0:2")
        self.assertIn("stem", str(ctx.exception).lower())

    def test_mix_epipe_is_not_rewritten_as_stem_success(self):
        with (
            patch("vdj_cuer.beatgrid_sources.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch(
                "vdj_cuer.beatgrid_sources.subprocess.run",
                side_effect=BrokenPipeError(errno.EPIPE, "Broken pipe"),
            ),
        ):
            with self.assertRaises(BrokenPipeError):
                self.helper._decode_onset_envelope("/tmp/song.m4a", None)


class VerifyBeatgridMixOnlyRetryTests(unittest.TestCase):
    def setUp(self):
        with patch("builtins.print"):
            self.cuer = cuer_module.AutomaticMusicCuer(
                gemini_api_key="test-key",
                vdj_database_path="/tmp/database.xml",
            )
        self.cuer.get_beatgrid_offset = Mock(return_value=0.25)
        self.cuer._beatgrid_alignment_cache.clear()

    def test_kick_stem_epipe_retries_mix_and_succeeds(self):
        mix_onsets = [0.0] + [0.08] * 250
        hop = 0.01
        calls = []

        def fake_decode(path, stream_map):
            calls.append(stream_map)
            if stream_map:
                raise BrokenPipeError(errno.EPIPE, "Broken pipe")
            return mix_onsets, hop

        sources = [
            ("kick stem", "/tmp/song.m4a.vdjstems", "0:2"),
            ("mix", "/tmp/song.m4a", None),
        ]
        with (
            patch.object(self.cuer, "_beatgrid_audio_sources", return_value=sources),
            patch.object(self.cuer, "_decode_onset_envelope", side_effect=fake_decode),
            patch("builtins.print"),
        ):
            result = self.cuer._verify_beatgrid_alignment("/tmp/song.m4a", 120.0)

        self.assertTrue(result.stems_skipped)
        self.assertEqual(result.source, "mix")
        self.assertTrue(any(call is None for call in calls))
        self.assertTrue(getattr(self.cuer, "_beatgrid_mix_only", False))

    def test_mix_only_flag_skips_stem_sources(self):
        self.cuer._beatgrid_mix_only = True
        sources = self.cuer._beatgrid_audio_sources("/tmp/song.m4a")
        self.assertEqual(sources, [("mix", "/tmp/song.m4a", None)])

    def test_release_track_resources_clears_mix_only_for_next_track(self):
        self.cuer._beatgrid_mix_only = True
        with patch("builtins.print"):
            self.cuer._release_track_resources("/tmp/song.m4a")
        self.assertFalse(self.cuer._beatgrid_mix_only)

    def test_prepare_stems_skipped_when_mix_only(self):
        self.cuer._beatgrid_mix_only = True
        with (
            patch.object(
                self.cuer, "_find_vdj_stems_file", return_value="/tmp/song.m4a.vdjstems"
            ),
            patch.object(self.cuer, "_extract_vdj_stems") as extract,
            patch("builtins.print"),
        ):
            uploads, files, temp_dir = self.cuer._prepare_vdj_stems_with_retry(
                "/tmp/song.m4a"
            )
        extract.assert_not_called()
        self.assertEqual(uploads, [])
        self.assertEqual(files, [])
        self.assertIsNone(temp_dir)


class MixOnlyFailoverHelperTests(unittest.TestCase):
    def test_stem_epipe_retries_without_stem_map_and_succeeds(self):
        cuer = cuer_module.AutomaticMusicCuer.__new__(cuer_module.AutomaticMusicCuer)
        cuer._beatgrid_mix_only = False
        cuer._beatgrid_alignment_cache = {}
        calls = []

        def work():
            calls.append(bool(cuer._beatgrid_mix_only))
            if not cuer._beatgrid_mix_only:
                raise BrokenPipeError(errno.EPIPE, "Broken pipe")
            return "cued"

        with patch("builtins.print"):
            result = run_with_mix_only_stem_failover(cuer, work)

        self.assertEqual(result, "cued")
        self.assertEqual(calls, [False, True])
        self.assertTrue(cuer._beatgrid_mix_only)

    def test_non_stem_errors_are_not_retried(self):
        cuer = cuer_module.AutomaticMusicCuer.__new__(cuer_module.AutomaticMusicCuer)
        cuer._beatgrid_mix_only = False
        cuer._beatgrid_alignment_cache = {}

        def work():
            raise ValueError("gemini empty")

        with self.assertRaises(ValueError):
            run_with_mix_only_stem_failover(cuer, work)
        self.assertFalse(cuer._beatgrid_mix_only)


if __name__ == "__main__":
    unittest.main()
