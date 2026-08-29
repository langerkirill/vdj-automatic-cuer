"""Concurrent AutoCue prints stay on their own job stream."""

from __future__ import annotations

import io
import threading
import time
import unittest

from vdj_cuer.job_io import capture_job_io, install_thread_streams


class JobIoTests(unittest.TestCase):
    def test_concurrent_prints_do_not_leak(self) -> None:
        install_thread_streams()
        a = io.StringIO()
        b = io.StringIO()
        started = threading.Barrier(2)
        done = threading.Barrier(3)

        def worker(stream: io.StringIO, token: str) -> None:
            with capture_job_io(stream):
                started.wait()
                for _ in range(40):
                    print(token)
                    time.sleep(0.002)
            done.wait()

        t1 = threading.Thread(target=worker, args=(a, "CALM"))
        t2 = threading.Thread(target=worker, args=(b, "NINEPM"))
        t1.start()
        t2.start()
        done.wait(timeout=5)
        t1.join(timeout=2)
        t2.join(timeout=2)
        self.assertTrue(a.getvalue())
        self.assertTrue(b.getvalue())
        self.assertNotIn("NINEPM", a.getvalue())
        self.assertNotIn("CALM", b.getvalue())
        self.assertIn("CALM", a.getvalue())
        self.assertIn("NINEPM", b.getvalue())


if __name__ == "__main__":
    unittest.main()
