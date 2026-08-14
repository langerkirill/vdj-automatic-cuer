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


if __name__ == "__main__":
    unittest.main()
