"""Locate the AutoCue repo root and import its VirtualDJ safety module."""

from __future__ import annotations

import sys
from pathlib import Path


def ensure_autocue_on_path() -> Path:
    """
    Add the vdj-automatic-cuer repo root to sys.path.

    Preferred layout (monorepo)::

        vdj-automatic-cuer/
          vdj_database_safety.py
          vdj_cuer/
          ui/                 ← this package
            sorter/
              autocue_path.py

    Also supports the older sibling-folder layout::

        Desktop/vdj-automatic-cuer/
        Desktop/music-sorter/
    """
    here = Path(__file__).resolve()
    candidates = [
        # Monorepo: ui/sorter → repo root
        here.parents[2],
        # If sorter is ever top-level under ui with different nesting
        here.parents[1],
        # Legacy sibling checkout on Desktop
        Path.home() / "Desktop" / "vdj-automatic-cuer",
    ]
    for candidate in candidates:
        safety = candidate / "vdj_database_safety.py"
        if safety.is_file():
            path_str = str(candidate)
            if path_str not in sys.path:
                sys.path.insert(0, path_str)
            return candidate
    raise RuntimeError(
        "Could not find vdj-automatic-cuer repo root (vdj_database_safety.py). "
        "Expected this UI under vdj-automatic-cuer/ui/ or a sibling Desktop checkout."
    )
