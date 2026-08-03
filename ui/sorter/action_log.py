"""
Append-only action log for Music Sorter.

Every sort / promote / remove / retry is recorded as one JSON line so we can
reconstruct what happened even if VirtualDJ's database is corrupted later.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .config import CUES_ROOT

# Durable log next to the music library (survives app restarts / reinstalls).
DEFAULT_LOG_PATH = CUES_ROOT / "music-sorter-actions.jsonl"

_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def log_path() -> Path:
    return DEFAULT_LOG_PATH


def append_action(
    action: str,
    *,
    source_path: str | None = None,
    dest_path: str | None = None,
    name: str | None = None,
    details: Optional[dict[str, Any]] = None,
    success: bool = True,
    error: str | None = None,
    log_file: Path | None = None,
) -> dict[str, Any]:
    """
    Append one action record. Returns the written record.

    action examples: sort, promote, remove_ready, retry_cues, create_folder
    """
    path = log_file or DEFAULT_LOG_PATH
    record: dict[str, Any] = {
        "id": uuid.uuid4().hex[:12],
        "ts": _now_iso(),
        "action": action,
        "success": success,
        "name": name or (Path(source_path).name if source_path else None),
        "source_path": source_path,
        "dest_path": dest_path,
        "details": details or {},
    }
    if error:
        record["error"] = error

    line = json.dumps(record, ensure_ascii=False) + "\n"
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
    return record


def read_actions(
    *,
    limit: int = 200,
    action: str | None = None,
    log_file: Path | None = None,
) -> list[dict[str, Any]]:
    """Return newest-first action records."""
    path = log_file or DEFAULT_LOG_PATH
    if not path.is_file():
        return []

    rows: list[dict[str, Any]] = []
    with _lock:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if action and row.get("action") != action:
                    continue
                rows.append(row)

    rows.reverse()  # newest first
    return rows[: max(1, limit)]


def seed_historical_sorts(
    entries: list[dict[str, Any]],
    *,
    log_file: Path | None = None,
) -> int:
    """
    Write reconstructed historical sorts if the log is empty or missing those ids.

    Each entry: {ts, name, dest_path, source_path?, details?}
    """
    path = log_file or DEFAULT_LOG_PATH
    existing_keys: set[str] = set()
    if path.is_file():
        for row in read_actions(limit=10_000, log_file=path):
            key = f"{row.get('ts')}|{row.get('name')}|{row.get('dest_path')}"
            existing_keys.add(key)

    written = 0
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            for entry in entries:
                name = entry.get("name")
                dest = entry.get("dest_path")
                ts = entry.get("ts")
                key = f"{ts}|{name}|{dest}"
                if key in existing_keys:
                    continue
                record = {
                    "id": entry.get("id") or uuid.uuid4().hex[:12],
                    "ts": ts,
                    "action": entry.get("action", "sort"),
                    "success": True,
                    "name": name,
                    "source_path": entry.get("source_path"),
                    "dest_path": dest,
                    "details": {
                        **(entry.get("details") or {}),
                        "reconstructed": True,
                        "note": entry.get(
                            "note",
                            "Reconstructed from music-sorter VDJ backups / filesystem",
                        ),
                    },
                }
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                written += 1
                existing_keys.add(key)
    return written


# Known sorts from 2026-07-28 session (backup FilePath deltas + user confirmation).
HISTORICAL_SORTS_2026_07_28: list[dict[str, Any]] = [
    {
        "ts": "2026-07-28T19:03:22-06:00",
        "name": "04. Beatrice M., Jinnal, KABA - In Touch.m4a",
        "source_path": "/Users/kirilllanger/Music/DJ/Music/Cues/Ready For Sort/04. Beatrice M., Jinnal, KABA - In Touch.m4a",
        "dest_path": "/Users/kirilllanger/Music/DJ/Music/Zouk/Hip Hoppy/04. Beatrice M., Jinnal, KABA - In Touch.m4a",
        "details": {
            "library_mode": "Zouk",
            "relative_folder": "Hip Hoppy",
            "cues_sorted_path": "/Users/kirilllanger/Music/DJ/Music/Cues/Cues Sorted/Hip Hoppy/04. Beatrice M., Jinnal, KABA - In Touch.m4a",
            "stems": True,
        },
    },
    {
        "ts": "2026-07-28T19:05:00-06:00",
        "name": "01 - Good Lee - Sol - Inward.flac",
        "source_path": "/Users/kirilllanger/Music/DJ/Music/Cues/Ready For Sort/01 - Good Lee - Sol - Inward.flac",
        "dest_path": "/Users/kirilllanger/Music/DJ/Music/Zouk/Chill/Mystical/01 - Good Lee - Sol - Inward.flac",
        "details": {
            "library_mode": "Zouk",
            "relative_folder": "Chill/Mystical",
            "cues_sorted_path": "/Users/kirilllanger/Music/DJ/Music/Cues/Cues Sorted/Chill/Mystical/01 - Good Lee - Sol - Inward.flac",
            "note": "Time approximate; DB delta at 19:14 backup",
        },
    },
    {
        "ts": "2026-07-28T19:15:08-06:00",
        "name": "01 - Liquid Bloom - Eternal Horizons (Temple Step Project Remix).flac",
        "source_path": "/Users/kirilllanger/Music/DJ/Music/Cues/Ready For Sort/01 - Liquid Bloom - Eternal Horizons (Temple Step Project Remix).flac",
        "dest_path": "/Users/kirilllanger/Music/DJ/Music/Zouk/Chill/Shaman/01 - Liquid Bloom - Eternal Horizons (Temple Step Project Remix).flac",
        "details": {
            "library_mode": "Zouk",
            "relative_folder": "Chill/Shaman",
            "cues_sorted_path": "/Users/kirilllanger/Music/DJ/Music/Cues/Cues Sorted/Chill/Shaman/01 - Liquid Bloom - Eternal Horizons (Temple Step Project Remix).flac",
        },
    },
    {
        "ts": "2026-07-28T19:18:41-06:00",
        "name": "03. Igniting the Sacred (Gingai Remix).flac",
        "source_path": "/Users/kirilllanger/Music/DJ/Music/Cues/Ready For Sort/03. Igniting the Sacred (Gingai Remix).flac",
        "dest_path": "/Users/kirilllanger/Music/DJ/Music/Zouk/Chill/Journey/03. Igniting the Sacred (Gingai Remix).flac",
        "details": {
            "library_mode": "Zouk",
            "relative_folder": "Chill/Journey",
            "cues_sorted_path": "/Users/kirilllanger/Music/DJ/Music/Cues/Cues Sorted/Chill/Journey/03. Igniting the Sacred (Gingai Remix).flac",
        },
    },
    {
        "ts": "2026-07-28T19:19:30-06:00",
        "name": "03. Saturna, cHMURa, Ashez - Bad.m4a",
        "source_path": "/Users/kirilllanger/Music/DJ/Music/Cues/Ready For Sort/03. Saturna, cHMURa, Ashez - Bad.m4a",
        "dest_path": "/Users/kirilllanger/Music/DJ/Music/Zouk/Energy/Trappy/03. Saturna, cHMURa, Ashez - Bad.m4a",
        "details": {
            "library_mode": "Zouk",
            "relative_folder": "Energy/Trappy",
            "cues_sorted_path": "/Users/kirilllanger/Music/DJ/Music/Cues/Cues Sorted/Energy/Trappy/03. Saturna, cHMURa, Ashez - Bad.m4a",
            "stems": True,
            "note": "On disk in Energy/Trappy; may predate final DB write if corruption occurred",
        },
    },
]
