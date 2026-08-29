"""Durable human approval for Sets/Pajamathon cue review.

Set files already live in the event crate, so Ready-for-Sort is the wrong
gate. Approval is the “I listened, these cues are good” stamp. AutoCue or a
cue-count change invalidates it so AI-rewritten markers need another listen.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .config import DJ_NOTES_ROOT, SETS_ROOT
from .library import is_pajamathon_set_audio

APPROVALS_PATH = DJ_NOTES_ROOT / "pajamathon_cue_approvals.json"

_lock = threading.Lock()


def approval_key(path: str | Path, *, sets_root: Path | None = None) -> str:
    audio = Path(path).expanduser().resolve()
    root = Path(sets_root or SETS_ROOT).expanduser().resolve()
    try:
        return audio.relative_to(root).as_posix()
    except ValueError:
        return str(audio)


def _empty() -> dict[str, Any]:
    return {"version": 1, "approved": {}}


def load_approvals(path: Path | None = None) -> dict[str, Any]:
    store = Path(path or APPROVALS_PATH)
    if not store.is_file():
        return _empty()
    try:
        data = json.loads(store.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    approved = data.get("approved")
    if not isinstance(approved, dict):
        approved = {}
    return {"version": 1, "approved": approved}


def save_approvals(data: dict[str, Any], path: Path | None = None) -> None:
    store = Path(path or APPROVALS_PATH)
    store.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "approved": data.get("approved") or {}}
    tmp = store.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(store)


def get_approval(
    path: str | Path,
    *,
    store_path: Path | None = None,
    sets_root: Path | None = None,
) -> Optional[dict[str, Any]]:
    key = approval_key(path, sets_root=sets_root)
    rec = load_approvals(store_path).get("approved", {}).get(key)
    return rec if isinstance(rec, dict) else None


def approved_file_paths(
    *,
    store_path: Path | None = None,
) -> list[str]:
    """Kirill-approved set files (persist flag, ignore cue-count fingerprint)."""
    out: list[str] = []
    for rec in (load_approvals(store_path).get("approved") or {}).values():
        if isinstance(rec, dict) and rec.get("path"):
            out.append(str(rec["path"]))
    return out


def has_approval(
    path: str | Path,
    *,
    store_path: Path | None = None,
    sets_root: Path | None = None,
) -> bool:
    return get_approval(path, store_path=store_path, sets_root=sets_root) is not None


def is_approved(

    path: str | Path,
    *,
    cue_count: int | None = None,
    loop_count: int | None = None,
    store_path: Path | None = None,
    sets_root: Path | None = None,
) -> bool:
    rec = get_approval(path, store_path=store_path, sets_root=sets_root)
    if not rec:
        return False
    if cue_count is not None and int(rec.get("cue_count") or -1) != int(cue_count):
        return False
    if loop_count is not None and int(rec.get("loop_count") or -1) != int(loop_count):
        return False
    return True


def approve_set_cues(
    path: str | Path,
    *,
    cue_count: int,
    loop_count: int,
    store_path: Path | None = None,
    sets_root: Path | None = None,
) -> dict[str, Any]:
    audio = Path(path).expanduser().resolve()
    if not is_pajamathon_set_audio(audio, sets_root=sets_root):
        raise ValueError("Approval is only for Sets/Pajamathon event-crate files")
    if not audio.is_file():
        raise FileNotFoundError(f"Audio not found: {audio}")
    if int(cue_count) < 1:
        raise ValueError("Cannot approve a set file with no cue points")
    key = approval_key(audio, sets_root=sets_root)
    rec = {
        "path": str(audio),
        "key": key,
        "cue_count": int(cue_count),
        "loop_count": int(loop_count),
        "approved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    with _lock:
        data = load_approvals(store_path)
        approved = dict(data.get("approved") or {})
        approved[key] = rec
        data["approved"] = approved
        save_approvals(data, store_path)
    return rec


def revoke_set_approval(
    path: str | Path,
    *,
    store_path: Path | None = None,
    sets_root: Path | None = None,
) -> bool:
    key = approval_key(path, sets_root=sets_root)
    with _lock:
        data = load_approvals(store_path)
        approved = dict(data.get("approved") or {})
        if key not in approved:
            return False
        approved.pop(key, None)
        data["approved"] = approved
        save_approvals(data, store_path)
    return True


def apply_set_review_status(
    readiness: dict[str, Any],
    *,
    approved: bool,
    is_cued: bool,
) -> dict[str, Any]:
    """Overlay human sign-off on structural readiness for set files."""
    out = dict(readiness)
    out["set_approved"] = bool(approved and is_cued)
    if not is_cued:
        return out
    if approved:
        out["status"] = "approved"
        out["label"] = "Approved"
        return out
    out["status"] = "needs_review"
    out["label"] = "Needs review"
    out["ready"] = False
    return out
