"""Shared VirtualDJ database write lock is process-wide and re-entrant."""

from __future__ import annotations

import threading
import unittest

from sorter.db_lock import get_db_write_lock, vdj_db_write
from sorter import autocue_retry


class DbLockTests(unittest.TestCase):
    def test_shared_with_autocue(self):
        self.assertIs(get_db_write_lock(), autocue_retry._db_write_lock)

    def test_reentrant(self):
        lock = get_db_write_lock()
        with vdj_db_write():
            self.assertTrue(lock._is_owned())  # type: ignore[attr-defined]
            with vdj_db_write():
                self.assertTrue(lock._is_owned())  # type: ignore[attr-defined]

    def test_serializes_writers(self):
        order: list[str] = []
        barrier = threading.Barrier(2)

        def worker(name: str) -> None:
            barrier.wait()
            with vdj_db_write():
                order.append(f"{name}-enter")
                order.append(f"{name}-exit")

        t1 = threading.Thread(target=worker, args=("a",))
        t2 = threading.Thread(target=worker, args=("b",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        # Fully nested pairs only — no interleaving of enter/exit across writers.
        self.assertEqual(len(order), 4)
        self.assertEqual(order[0].endswith("-enter"), True)
        self.assertEqual(order[1], order[0].replace("-enter", "-exit"))


if __name__ == "__main__":
    unittest.main()
