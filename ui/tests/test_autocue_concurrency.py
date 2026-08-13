"""AutoCue concurrency defaults, semaphore sizing, and batch-list lock safety."""

from __future__ import annotations

import importlib
import os
import threading
import unittest
from unittest import mock


class AutocueConcurrencyTests(unittest.TestCase):
    def tearDown(self) -> None:
        with mock.patch.dict(
            os.environ, {"MUSIC_SORTER_AUTOCUE_CONCURRENCY": "5"}, clear=False
        ):
            import sorter.autocue_retry as mod

            importlib.reload(mod)

    def test_default_max_concurrent_is_five(self):
        env = {
            k: v for k, v in os.environ.items() if k != "MUSIC_SORTER_AUTOCUE_CONCURRENCY"
        }
        with mock.patch.dict(os.environ, env, clear=True):
            import sorter.autocue_retry as mod

            importlib.reload(mod)
            self.assertEqual(mod._MAX_CONCURRENT, 5)
            self.assertEqual(mod._parse_max_concurrent(), 5)
            self.assertEqual(mod.max_concurrent_jobs(), 5)

    def test_env_override_clamped(self):
        with mock.patch.dict(os.environ, {"MUSIC_SORTER_AUTOCUE_CONCURRENCY": "12"}):
            import sorter.autocue_retry as mod

            importlib.reload(mod)
            self.assertEqual(mod._MAX_CONCURRENT, 8)  # hard cap
        with mock.patch.dict(os.environ, {"MUSIC_SORTER_AUTOCUE_CONCURRENCY": "0"}):
            import sorter.autocue_retry as mod

            importlib.reload(mod)
            self.assertEqual(mod._MAX_CONCURRENT, 1)  # floor

    def test_list_batches_does_not_deadlock(self):
        import sorter.autocue_retry as mod

        job = mod.RetryJob(
            id="j-deadlock",
            path="/tmp/song.flac",
            name="song.flac",
            status="running",
            created_at="t",
        )
        batch = mod.BatchJob(
            id="b-deadlock",
            status="running",
            created_at="t",
            total=1,
            item_job_ids=["j-deadlock"],
        )
        with mod._lock:
            mod._jobs[job.id] = job
            mod._batches[batch.id] = batch

        result: list = []

        def run() -> None:
            result.append(mod.list_batches())

        thread = threading.Thread(target=run)
        thread.start()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive(), "list_batches deadlocked on _lock")
        self.assertTrue(result)
        items = result[0][0]["items"]
        self.assertEqual(items[0]["path"], "/tmp/song.flac")
        self.assertEqual(items[0]["status"], "running")

        with mod._lock:
            payload = batch.to_dict()
        self.assertEqual(payload["active_count"], 1)
        mod._jobs.pop(job.id, None)
        mod._batches.pop(batch.id, None)

    def test_batch_to_dict_omits_finished_items(self):
        import sorter.autocue_retry as mod

        running = mod.RetryJob(
            id="j-run",
            path="/tmp/a.flac",
            name="a.flac",
            status="queued",
            created_at="t",
        )
        done = mod.RetryJob(
            id="j-ok",
            path="/tmp/b.flac",
            name="b.flac",
            status="ok",
            created_at="t",
        )
        batch = mod.BatchJob(
            id="b-slim",
            status="running",
            created_at="t",
            total=2,
            item_job_ids=["j-run", "j-ok"],
        )
        with mod._lock:
            mod._jobs[running.id] = running
            mod._jobs[done.id] = done
            mod._batches[batch.id] = batch
        try:
            payload = batch.to_dict()
            paths = [item["path"] for item in payload["items"]]
            self.assertEqual(paths, ["/tmp/a.flac"])
            self.assertEqual(payload["active_count"], 1)
        finally:
            with mod._lock:
                mod._jobs.pop(running.id, None)
                mod._jobs.pop(done.id, None)
                mod._batches.pop(batch.id, None)


if __name__ == "__main__":
    unittest.main()
