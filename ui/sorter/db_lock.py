"""
Process-wide VirtualDJ database.xml write lock.

Every surgical rewrite of database.xml (AutoCue apply, cue edit, notes, sort
relocate/clone/remove, BPM, grid) must take this lock so concurrent Music Sorter
jobs never last-write-wins each other.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator

# Re-entrant: a held lock may call helpers that also request the lock.
_db_write_lock = threading.RLock()


def get_db_write_lock() -> threading.RLock:
    """Shared lock instance (for rare call sites that need the lock object)."""
    return _db_write_lock


@contextmanager
def vdj_db_write() -> Iterator[None]:
    """Serialize database.xml mutations across the Music Sorter process."""
    with _db_write_lock:
        yield
