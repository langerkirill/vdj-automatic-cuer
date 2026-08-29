"""Locate the AutoCue repo root and import its VirtualDJ safety module."""

from __future__ import annotations

import sys
from pathlib import Path


def ensure_autocue_on_path() -> Path:
    """
    Add the VDJ Station repo root to sys.path.

    Preferred layout (monorepo)::

        vdj-station/
          vdj_database_safety.py
          vdj_cuer/
          ui/                 ← this package
            sorter/
              autocue_path.py

    Also supports older checkouts named vdj-automatic-cuer.
    """
    here = Path(__file__).resolve()
    home = Path.home()
    candidates = [
        # Monorepo: ui/sorter → repo root
        here.parents[2],
        # If sorter is ever top-level under ui with different nesting
        here.parents[1],
        home / "src" / "vdj-station",
        home / "src" / "vdj-automatic-cuer",
        home / "Desktop" / "vdj-station",
        home / "Desktop" / "vdj-automatic-cuer",
    ]
    for candidate in candidates:
        safety = candidate / "vdj_database_safety.py"
        if safety.is_file():
            path_str = str(candidate)
            if path_str not in sys.path:
                sys.path.insert(0, path_str)
            return candidate
    raise RuntimeError(
        "Could not find VDJ Station repo root (vdj_database_safety.py). "
        "Expected this UI under vdj-station/ui/ (or a legacy vdj-automatic-cuer checkout)."
    )
