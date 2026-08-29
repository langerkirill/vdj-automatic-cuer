"""Durable Must Play stamp for Sets/Pajamathon files.

Keeps the numbered crate file in place and copies audio (+ .vdjstems) into
Sets/Pajamathon/Must Play so the crate folder matches the sorter stamp.
"""

from __future__ import annotations

import json
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .config import DJ_NOTES_ROOT, SETS_ROOT
from .library import is_must_play_folder_path, is_pajamathon_set_audio
from .set_approval import approval_key

MUST_PLAY_PATH = DJ_NOTES_ROOT / "pajamathon_must_play.json"

_lock = threading.Lock()


def _empty() -> dict[str, Any]:
    return {"version": 1, "must_play": {}}


def load_must_play(path: Path | None = None) -> dict[str, Any]:
    store = Path(path or MUST_PLAY_PATH)
    if not store.is_file():
        return _empty()
    try:
        data = json.loads(store.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    recs = data.get("must_play")
    if not isinstance(recs, dict):
        recs = {}
    return {"version": 1, "must_play": recs}


def save_must_play(data: dict[str, Any], path: Path | None = None) -> None:
    store = Path(path or MUST_PLAY_PATH)
    store.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "must_play": data.get("must_play") or {}}
    tmp = store.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(store)


def get_must_play(
    path: str | Path,
    *,
    store_path: Path | None = None,
    sets_root: Path | None = None,
) -> Optional[dict[str, Any]]:
    key = approval_key(path, sets_root=sets_root)
    rec = load_must_play(store_path).get("must_play", {}).get(key)
    return rec if isinstance(rec, dict) else None


def has_must_play(
    path: str | Path,
    *,
    store_path: Path | None = None,
    sets_root: Path | None = None,
) -> bool:
    return get_must_play(path, store_path=store_path, sets_root=sets_root) is not None


def must_play_file_paths(*, store_path: Path | None = None) -> list[str]:
    out: list[str] = []
    for rec in (load_must_play(store_path).get("must_play") or {}).values():
        if isinstance(rec, dict) and rec.get("path"):
            out.append(str(rec["path"]))
    return out


def must_play_folder(*, sets_root: Path | None = None) -> Path:
    from .relocate import pajamathon_must_play_folder

    return pajamathon_must_play_folder(sets_root)


def copy_into_must_play_folder(
    path: str | Path,
    *,
    sets_root: Path | None = None,
) -> Path:
    """Copy set audio + stems into Must Play/. Leaves the numbered crate file."""
    audio = Path(path).expanduser().resolve()
    if not audio.is_file():
        raise FileNotFoundError(f"Audio not found: {audio}")
    folder = must_play_folder(sets_root=sets_root)
    folder.mkdir(parents=True, exist_ok=True)
    dest = (folder / audio.name).resolve()
    if dest == audio:
        return dest
    if is_must_play_folder_path(audio):
        return audio
    if not dest.exists():
        shutil.copy2(str(audio), str(dest))
    stems_src = Path(f"{audio}.vdjstems")
    stems_dest = Path(f"{dest}.vdjstems")
    if stems_src.is_file() and not stems_dest.exists():
        shutil.copy2(str(stems_src), str(stems_dest))
    return dest


def mark_must_play(
    path: str | Path,
    *,
    store_path: Path | None = None,
    sets_root: Path | None = None,
) -> dict[str, Any]:
    audio = Path(path).expanduser().resolve()
    if not is_pajamathon_set_audio(audio, sets_root=sets_root):
        raise ValueError("Must Play is only for Sets/Pajamathon event-crate files")
    if not audio.is_file():
        raise FileNotFoundError(f"Audio not found: {audio}")
    folder_copy = copy_into_must_play_folder(audio, sets_root=sets_root)
    key = approval_key(audio, sets_root=sets_root)
    rec = {
        "path": str(audio),
        "key": key,
        "must_play_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "folder_copy": str(folder_copy),
    }
    with _lock:
        data = load_must_play(store_path)
        recs = dict(data.get("must_play") or {})
        recs[key] = rec
        data["must_play"] = recs
        save_must_play(data, store_path)
    return rec


def sync_must_play_folder(
    *,
    store_path: Path | None = None,
    sets_root: Path | None = None,
) -> dict[str, Any]:
    """Copy every stamped Must Play track into the Must Play folder."""
    copied: list[str] = []
    skipped: list[str] = []
    missing: list[str] = []
    for rec in (load_must_play(store_path).get("must_play") or {}).values():
        if not isinstance(rec, dict) or not rec.get("path"):
            continue
        src = Path(str(rec["path"])).expanduser()
        if not src.is_file():
            missing.append(str(src))
            continue
        dest = must_play_folder(sets_root=sets_root) / src.name
        existed = dest.is_file()
        copy_into_must_play_folder(src, sets_root=sets_root)
        if existed:
            skipped.append(str(dest))
        else:
            copied.append(str(dest))
    return {
        "ok": True,
        "copied": len(copied),
        "skipped": len(skipped),
        "missing": len(missing),
        "copied_paths": copied,
        "missing_paths": missing,
    }
