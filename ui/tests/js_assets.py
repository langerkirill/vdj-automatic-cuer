"""Helpers for reading the shipped Music Sorter classic-script modules."""

from __future__ import annotations

from pathlib import Path

UI_STATIC = Path(__file__).resolve().parents[1] / "static"

SHIPPED_JS = (
    "status_handoff.js",
    "placements.js",
    "state.js",
    "transport.js",
    "waveform.js",
    "practice.js",
    "assemble.js",
    "app.js",
)


def read_static(name: str) -> str:
    return (UI_STATIC / name).read_text(encoding="utf-8")


def read_shipped_js() -> str:
    """Concatenate the real browser scripts in load order."""
    return "\n".join(read_static(name) for name in SHIPPED_JS)
