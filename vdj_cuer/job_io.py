"""Thread-local stdout/stderr so concurrent AutoCue jobs do not leak logs.

``contextlib.redirect_stdout`` replaces ``sys.stdout`` process-wide. Two
AutoCue threads then write into whichever StringIO won the race (Calm lines
showed up on the 9PM job). This proxy keeps the real streams, and each thread
opts into its own buffer.
"""

from __future__ import annotations

import sys
import threading
from contextlib import contextmanager
from typing import Iterator, TextIO

_tls = threading.local()
_real_stdout = sys.__stdout__ if sys.__stdout__ is not None else sys.stdout
_real_stderr = sys.__stderr__ if sys.__stderr__ is not None else sys.stderr
_installed = False


class _ThreadStream:
    def __init__(self, kind: str) -> None:
        self.kind = kind

    def _target(self) -> TextIO:
        stream = getattr(_tls, self.kind, None)
        if stream is not None:
            return stream
        return _real_stdout if self.kind == "stdout" else _real_stderr

    def write(self, s: str) -> int:
        return self._target().write(s)

    def flush(self) -> None:
        self._target().flush()

    def isatty(self) -> bool:
        return False

    def __getattr__(self, name: str):
        return getattr(self._target(), name)


def install_thread_streams() -> None:
    """Swap sys.stdout/stderr for the thread-aware proxy once per process."""
    global _installed
    if _installed:
        return
    sys.stdout = _ThreadStream("stdout")
    sys.stderr = _ThreadStream("stderr")
    _installed = True


@contextmanager
def capture_job_io(stream: TextIO) -> Iterator[TextIO]:
    """Route this thread's print/traceback into ``stream`` only."""
    install_thread_streams()
    old_out = getattr(_tls, "stdout", None)
    old_err = getattr(_tls, "stderr", None)
    _tls.stdout = stream
    _tls.stderr = stream
    try:
        yield stream
    finally:
        if old_out is None:
            if hasattr(_tls, "stdout"):
                delattr(_tls, "stdout")
        else:
            _tls.stdout = old_out
        if old_err is None:
            if hasattr(_tls, "stderr"):
                delattr(_tls, "stderr")
        else:
            _tls.stderr = old_err
