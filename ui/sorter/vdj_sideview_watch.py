"""Background loop: when VDJ is open, refresh Next Recs Sideview lists."""

from __future__ import annotations

import threading
import time
from typing import Optional

from .relocate import is_virtualdj_running
from .vdj_now_playing import get_now_playing

_watch_started = False
_watch_lock = threading.Lock()
_last_path = ""


def _loop(interval_s: float = 10.0) -> None:
    global _last_path
    # Delay so uvicorn finishes booting
    time.sleep(4.0)
    while True:
        try:
            if not is_virtualdj_running():
                time.sleep(interval_s)
                continue
            np = get_now_playing(enrich=False)
            path = np.path if np else ""
            if not path or path == _last_path:
                time.sleep(interval_s)
                continue
            from .transition_recs import recommend_transitions

            recommend_transitions(path=path, use_gemini=True)
            _last_path = path
        except Exception:
            pass
        time.sleep(interval_s)


def start_sideview_recs_watch(*, interval_s: float = 10.0) -> None:
    global _watch_started
    with _watch_lock:
        if _watch_started:
            return
        _watch_started = True
    t = threading.Thread(
        target=_loop,
        kwargs={"interval_s": interval_s},
        name="vdj-sideview-recs",
        daemon=True,
    )
    t.start()


def last_watched_path() -> Optional[str]:
    return _last_path or None
