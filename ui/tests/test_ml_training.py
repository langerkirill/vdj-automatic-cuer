"""ML ingest after AutoCue must not import sklearn into the UI process."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from sorter.ml_training import ingest_cli_argv, ingest_subprocess_env


class MlTrainingSubprocessTests(unittest.TestCase):
    def test_ingest_argv_is_isolated_module(self) -> None:
        cmd = ingest_cli_argv("/tmp/song.flac")
        self.assertEqual(cmd[1:3], ["-m", "vdj_cuer.ml.ingest"])
        self.assertIn("--path", cmd)
        self.assertIn("/tmp/song.flac", cmd)
        self.assertNotIn("--drop", cmd)

    def test_drop_argv_sets_flag(self) -> None:
        cmd = ingest_cli_argv("/tmp/song.flac", drop=True)
        self.assertIn("--drop", cmd)

    def test_env_caps_openmp_and_puts_repo_on_path(self) -> None:
        env = ingest_subprocess_env()
        self.assertEqual(env.get("OMP_NUM_THREADS"), "1")
        self.assertEqual(env.get("PYTHONUNBUFFERED"), "1")
        repo_root = str(Path(__file__).resolve().parents[2])
        self.assertIn(repo_root, env.get("PYTHONPATH", ""))

    def test_ingest_slot_serializes_children(self) -> None:
        import threading
        import time
        from unittest.mock import Mock

        from sorter.ml_training import _run_ingest_subprocess

        current = 0
        overlap: list[int] = []

        def fake_run(*_a, **_k):
            nonlocal current
            current += 1
            overlap.append(current)
            time.sleep(0.05)
            current -= 1
            return Mock(returncode=0)

        with patch("sorter.ml_training.subprocess.run", side_effect=fake_run):
            threads = [
                threading.Thread(target=_run_ingest_subprocess, args=("/tmp/a.flac",)),
                threading.Thread(target=_run_ingest_subprocess, args=("/tmp/b.flac",)),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=2)
        self.assertTrue(overlap)
        self.assertEqual(max(overlap), 1)

    def test_schedule_update_does_not_import_ingest(self) -> None:
        with patch("sorter.ml_training._run_ingest_subprocess") as run:
            from sorter.ml_training import schedule_training_update

            with patch("sorter.ml_training.threading.Thread") as thread_cls:
                started = []

                class FakeThread:
                    def __init__(self, target=None, args=(), kwargs=None, **_k):
                        self._target = target
                        self._args = args
                        self._kwargs = kwargs or {}

                    def start(self):
                        started.append(1)
                        self._target(*self._args, **self._kwargs)

                thread_cls.side_effect = FakeThread
                schedule_training_update("/tmp/song.flac")
        self.assertEqual(started, [1])
        run.assert_called_once_with("/tmp/song.flac")


if __name__ == "__main__":
    unittest.main()
