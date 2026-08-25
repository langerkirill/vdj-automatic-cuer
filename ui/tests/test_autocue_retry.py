"""Path guards and write_scope helpers for AutoCue retry jobs."""

import errno
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from sorter import autocue_retry as retry_mod
from vdj_cuer.beatgrid_sources import BeatgridSourceMixin
from vdj_cuer.common import StemDecodeError


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

    def test_autocue_fail_message_uses_real_log_line(self):
        log = (
            "📋 Scope=all · analysis cues=4 loops=1\n"
            "❌ Error applying cues to song.flac: VirtualDJ is running — refusing\n"
        )
        msg = retry_mod.autocue_fail_message(log, analysis_empty=False)
        self.assertIn("VirtualDJ is running", msg)
        self.assertNotIn("beatgrid", msg)

    def test_autocue_fail_message_when_analysis_empty(self):
        msg = retry_mod.autocue_fail_message("", analysis_empty=True)
        self.assertIn("no data", msg.lower())

    def test_analyze_audio_until_data_retries_then_succeeds(self):
        calls = {"n": 0}

        def analyze(_path):
            calls["n"] += 1
            if calls["n"] < 3:
                return None
            return {"measure_changes": [{"timestamp": 1.0}]}

        sleeps: list[float] = []
        retries: list[tuple[int, int]] = []
        out = retry_mod.analyze_audio_until_data(
            analyze,
            "/tmp/song.mp3",
            attempts=3,
            sleep_fn=sleeps.append,
            on_retry=lambda i, n: retries.append((i, n)),
        )
        self.assertEqual(calls["n"], 3)
        self.assertEqual(out["measure_changes"][0]["timestamp"], 1.0)
        self.assertEqual(len(sleeps), 2)
        self.assertEqual(retries, [(1, 3), (2, 3)])

    def test_analyze_audio_until_data_gives_up(self):
        out = retry_mod.analyze_audio_until_data(
            lambda _p: None,
            "/tmp/song.mp3",
            attempts=2,
            sleep_fn=lambda _s: None,
        )
        self.assertIsNone(out)

    def test_retry_history_buckets_cues_loops_and_both(self):
        from sorter.action_log import append_action

        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "actions.jsonl"
            cues_only = str((Path(tmp) / "a.flac").resolve())
            loops_only = str((Path(tmp) / "b.flac").resolve())
            both_once = str((Path(tmp) / "c.flac").resolve())
            both_split = str((Path(tmp) / "d.flac").resolve())
            append_action(
                "retry_cues_complete",
                source_path=cues_only,
                name="a.flac",
                details={"write_scope": "cues"},
                log_file=log,
            )
            append_action(
                "retry_cues_complete",
                source_path=loops_only,
                name="b.flac",
                details={"write_scope": "loops"},
                log_file=log,
            )
            append_action(
                "retry_cues_complete",
                source_path=both_once,
                name="c.flac",
                details={"write_scope": "all"},
                log_file=log,
            )
            append_action(
                "retry_cues",
                source_path=both_split,
                name="d.flac",
                details={"write_scope": "cues"},
                log_file=log,
            )
            append_action(
                "retry_cues_complete",
                source_path=both_split,
                name="d.flac",
                details={"write_scope": "loops"},
                log_file=log,
            )
            hist = retry_mod.summarize_retry_history(log_file=log)
            self.assertEqual(retry_mod.retry_history_for_path(cues_only, hist)["kind"], "cues")
            self.assertEqual(retry_mod.retry_history_for_path(loops_only, hist)["kind"], "loops")
            self.assertEqual(retry_mod.retry_history_for_path(both_once, hist)["kind"], "both")
            self.assertEqual(retry_mod.retry_history_for_path(both_split, hist)["kind"], "both")
            self.assertIsNone(retry_mod.retry_history_for_path(str(Path(tmp) / "none.flac"), hist))

    def test_adjacent_vdj_stems_requires_sidecar(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "song.flac"
            audio.write_bytes(b"x")
            self.assertIsNone(retry_mod.adjacent_vdj_stems(audio))
            sidecar = Path(f"{audio}.vdjstems")
            sidecar.write_bytes(b"stems")
            self.assertEqual(retry_mod.adjacent_vdj_stems(audio), sidecar)

    def test_start_retry_cues_skips_without_stems(self):
        with tempfile.TemporaryDirectory() as tmp:
            cues = Path(tmp) / "Cues"
            inbox = cues / "Add Cues"
            inbox.mkdir(parents=True)
            audio = inbox / "song.flac"
            audio.write_bytes(b"x")
            with (
                patch.object(retry_mod, "CUES_ROOT", cues),
                patch.object(retry_mod, "LIBRARIES", {}),
                patch.object(retry_mod, "is_virtualdj_running", return_value=False),
                patch.object(
                    retry_mod,
                    "summarize_cues",
                    return_value=type("C", (), {"cue_count": 0})(),
                ),
                patch.object(
                    retry_mod,
                    "assess_grid_for_autocue",
                    return_value={"can_autocue": True},
                ),
            ):
                job = retry_mod.start_retry_cues(audio, require_grid=False)
            self.assertEqual(job.status, "skipped")
            self.assertIn(".vdjstems", job.message)
            self.assertFalse(job.preflight.get("has_stems", True))

    def test_start_retry_cues_queues_when_stems_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            cues = Path(tmp) / "Cues"
            inbox = cues / "Add Cues"
            inbox.mkdir(parents=True)
            audio = inbox / "song.flac"
            audio.write_bytes(b"x")
            Path(f"{audio}.vdjstems").write_bytes(b"stems")
            with (
                patch.object(retry_mod, "CUES_ROOT", cues),
                patch.object(retry_mod, "LIBRARIES", {}),
                patch.object(retry_mod, "is_virtualdj_running", return_value=False),
                patch.object(
                    retry_mod,
                    "summarize_cues",
                    return_value=type("C", (), {"cue_count": 0})(),
                ),
                patch.object(
                    retry_mod,
                    "assess_grid_for_autocue",
                    return_value={"can_autocue": True},
                ),
                patch.object(retry_mod.threading.Thread, "start", lambda self: None),
            ):
                job = retry_mod.start_retry_cues(audio, require_grid=False)
            self.assertEqual(job.status, "queued")
            self.assertTrue(job.preflight.get("has_stems"))

    def test_start_retry_honors_preflight_stems_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            cues = Path(tmp) / "Cues"
            inbox = cues / "Add Cues"
            inbox.mkdir(parents=True)
            audio = inbox / "song.flac"
            audio.write_bytes(b"x")
            Path(f"{audio}.vdjstems").write_bytes(b"stems")
            captured = {}

            def capture_thread(self, *args, **kwargs):
                captured["target"] = self._target
                captured["args"] = self._args

            with (
                patch.object(retry_mod, "CUES_ROOT", cues),
                patch.object(retry_mod, "LIBRARIES", {}),
                patch.object(retry_mod, "is_virtualdj_running", return_value=False),
                patch.object(
                    retry_mod,
                    "summarize_cues",
                    return_value=type("C", (), {"cue_count": 0})(),
                ),
                patch.object(
                    retry_mod,
                    "assess_grid_for_autocue",
                    return_value={
                        "can_autocue": True,
                        "stems_skipped": True,
                        "warnings": ["VDJ stems were skipped"],
                    },
                ),
                patch.object(
                    retry_mod.threading.Thread,
                    "start",
                    lambda self: capture_thread(self),
                ),
            ):
                job = retry_mod.start_retry_cues(audio, require_grid=False)

            self.assertEqual(job.status, "queued")
            self.assertTrue(job.preflight.get("stems_skipped"))
            self.assertTrue(job.preflight.get("has_stems"))

    def test_configure_cuer_mix_only_from_preflight(self):
        cuer = type("Cuer", (), {})()
        retry_mod.apply_preflight_stem_failover(
            cuer, {"stems_skipped": True, "can_autocue": True}
        )
        self.assertTrue(cuer._beatgrid_mix_only)

    def test_run_job_decode_onset_epipe_retries_mix_only(self):
        """Preflight can_autocue=true is not enough: the job must catch stem EPIPE."""
        usable = {
            "measure_changes": [
                {"timestamp": 4.0, "elements": ["drums"], "cue_name": "Drop"}
            ],
            "loop_segments": [],
        }

        class FakeCuer(BeatgridSourceMixin):
            def __init__(self, *args, **kwargs):
                self._beatgrid_mix_only = False
                self._beatgrid_alignment_cache = {}
                self.post_cue_audit_enabled = True
                self.write_scope = "all"
                self.model_name = "test"
                self.decode_maps = []

            def analyze_audio_with_gemini(self, path):
                return usable

            def get_song_length(self, path):
                return 180.0

            def get_song_bpm_from_database(self, path):
                return 120.0

            def backup_database(self):
                return "/tmp/database.xml.backup"

            def _apply_cues_to_database(self, path, analysis, dry_run=False):
                stream_map = None if self._beatgrid_mix_only else "0:2"
                audio = path if self._beatgrid_mix_only else f"{path}.vdjstems"
                self.decode_maps.append(stream_map)
                self._decode_onset_envelope(audio, stream_map)
                return True

        fake = FakeCuer()

        def fake_run(command, **kwargs):
            if "-map" in command:
                raise BrokenPipeError(errno.EPIPE, "Broken pipe")
            return Mock(stdout=b"\x00\x00" * 20000, returncode=0)

        with tempfile.TemporaryDirectory() as tmp:
            cues = Path(tmp) / "Cues"
            inbox = cues / "Add Cues"
            inbox.mkdir(parents=True)
            audio = inbox / "song.flac"
            audio.write_bytes(b"x")
            Path(f"{audio}.vdjstems").write_bytes(b"stems")
            job = retry_mod.RetryJob(
                id="stem-epipe-job",
                path=str(audio.resolve()),
                name=audio.name,
                status="queued",
                created_at=retry_mod._now(),
                preflight={"can_autocue": True, "stems_skipped": False},
                write_scope="all",
            )
            retry_mod._jobs[job.id] = job
            env = {**os.environ, "AUTOCUE_DISABLE_ANALYSIS_CACHE": "1"}
            with (
                patch.dict(os.environ, env, clear=False),
                patch.object(retry_mod, "VDJ_DATABASE", Path(tmp) / "database.xml"),
                patch.object(retry_mod, "ensure_autocue_on_path", return_value=Path(tmp)),
                patch("vdj_cuer.AutomaticMusicCuer", return_value=fake),
                patch(
                    "vdj_cuer.beatgrid_sources.shutil.which",
                    return_value="/usr/bin/ffmpeg",
                ),
                patch("vdj_cuer.beatgrid_sources.subprocess.run", side_effect=fake_run),
                patch.object(
                    retry_mod,
                    "summarize_cues",
                    return_value=type("C", (), {"cue_count": 1, "loop_count": 0})(),
                ),
                patch("builtins.print"),
            ):
                retry_mod._run_job(job.id, dry_run=True, model_name=None)

            live = retry_mod.get_job(job.id)
            self.assertIsNotNone(live)
            self.assertEqual(live.status, "ok", live.message)
            self.assertIn("0:2", fake.decode_maps)
            self.assertIn(None, fake.decode_maps)
            self.assertTrue(fake._beatgrid_mix_only)


if __name__ == "__main__":
    unittest.main()

