"""
Halve (or restore) VirtualDJ BPM when AutoCue's double-time detection is wrong.

VDJ stores Scan/Tags @Bpm either as:
  - musical BPM (e.g. 136), or
  - beat duration in seconds (e.g. 0.441 ≈ 136 BPM)

Halving musical BPM = doubling the stored period when the value is fractional.
"""

from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .autocue_path import ensure_autocue_on_path
from .config import CUES_ROOT, LIBRARIES, VDJ_DATABASE
from .relocate import is_virtualdj_running, summarize_cues, vdj_bpm_to_actual
from .db_lock import vdj_db_write

ensure_autocue_on_path()

from vdj_database_safety import (  # noqa: E402
    _find_song_span,
    normalize_database_path,
    read_vdj_database_text,
    rewrite_song_xml_in_database,
)

_BPM_ATTR_RE = re.compile(
    r'(\bBpm\s*=\s*")([^"]+)(")',
    re.IGNORECASE,
)
_SCAN_TAG_RE = re.compile(r"<Scan\b[^/]*?/?>", re.IGNORECASE | re.DOTALL)
_TAGS_TAG_RE = re.compile(r"<Tags\b[^/]*?/?>", re.IGNORECASE | re.DOTALL)


def _assert_allowed(path: Path) -> Path:
    audio = path.expanduser().resolve()
    if not audio.is_file():
        raise FileNotFoundError(f"Audio not found: {audio}")
    roots = [CUES_ROOT.resolve(), *[p.resolve() for p in LIBRARIES.values()]]
    for root in roots:
        try:
            audio.relative_to(root)
            return audio
        except ValueError:
            continue
    raise ValueError("BPM edit is only allowed under Cues/ or House/Zouk libraries")


def halve_stored_bpm_value(raw: float) -> float:
    """
    Transform one stored VDJ Bpm attribute so musical tempo becomes half.

    - Musical range 40–240 → divide by 2
    - Fractional period (< 5) → multiply by 2 (longer beat = half BPM)
    """
    if raw <= 0:
        raise ValueError(f"Invalid BPM value: {raw}")
    if 40.0 <= raw <= 240.0:
        return raw / 2.0
    if raw < 5.0:
        return raw * 2.0
    # Unknown encoding — treat as musical if 60/raw is sensible, else half raw
    as_period = 60.0 / raw
    if 40.0 <= as_period <= 240.0:
        return raw * 2.0
    return raw / 2.0


def _rewrite_bpm_in_tag_match(match: re.Match[str], *, factor_halve: bool) -> str:
    tag = match.group(0)

    def repl(m: re.Match[str]) -> str:
        raw_s = m.group(2)
        try:
            raw = float(raw_s)
        except ValueError:
            return m.group(0)
        if factor_halve:
            new = halve_stored_bpm_value(raw)
        else:
            # Double musical = reverse of halve
            if 20.0 <= raw <= 120.0:
                new = raw * 2.0
            elif raw < 5.0:
                new = raw / 2.0
            else:
                new = raw * 2.0
        # Preserve similar precision to VDJ dumps
        if abs(new - round(new)) < 1e-6:
            new_s = f"{new:.0f}"
        elif new < 5:
            new_s = f"{new:.6f}".rstrip("0").rstrip(".")
        else:
            new_s = f"{new:.6f}".rstrip("0").rstrip(".")
        return f"{m.group(1)}{new_s}{m.group(3)}"

    return _BPM_ATTR_RE.sub(repl, tag)


def apply_bpm_factor_to_song_xml(song_xml: str, *, halve: bool = True) -> tuple[str, dict[str, Any]]:
    """Rewrite Scan/Tags Bpm attrs inside one Song block. Returns (xml, meta)."""
    changed = {"scan": False, "tags": False, "values": []}

    def scan_sub(m: re.Match[str]) -> str:
        before = m.group(0)
        after = _rewrite_bpm_in_tag_match(m, factor_halve=halve)
        if after != before:
            changed["scan"] = True
            for bm in _BPM_ATTR_RE.finditer(before):
                try:
                    old = float(bm.group(2))
                    new = float(_BPM_ATTR_RE.search(after).group(2))  # type: ignore[union-attr]
                    changed["values"].append(
                        {"where": "Scan", "raw_before": old, "raw_after": new}
                    )
                except Exception:
                    pass
        return after

    def tags_sub(m: re.Match[str]) -> str:
        before = m.group(0)
        after = _rewrite_bpm_in_tag_match(m, factor_halve=halve)
        if after != before:
            changed["tags"] = True
            for bm in _BPM_ATTR_RE.finditer(before):
                try:
                    old = float(bm.group(2))
                    new = float(_BPM_ATTR_RE.search(after).group(2))  # type: ignore[union-attr]
                    changed["values"].append(
                        {"where": "Tags", "raw_before": old, "raw_after": new}
                    )
                except Exception:
                    pass
        return after

    out = _SCAN_TAG_RE.sub(scan_sub, song_xml)
    out = _TAGS_TAG_RE.sub(tags_sub, out)
    if not changed["scan"] and not changed["tags"]:
        raise ValueError("No Scan/Tags Bpm attribute found to update on this Song")
    return out, changed


def halve_track_bpm(
    source_path: str | Path,
    *,
    database_path: Path | None = None,
    dry_run: bool = False,
    allow_vdj_running: bool = False,
    create_backup: bool = True,
    double_instead: bool = False,
) -> dict[str, Any]:
    """
    Halve musical BPM for a track in VirtualDJ database.xml (double-time fix).

    double_instead=True restores a previous half (×2 musical BPM).
    """
    audio = _assert_allowed(Path(source_path))
    db = Path(database_path) if database_path else VDJ_DATABASE
    if not db.is_file():
        raise FileNotFoundError(f"VDJ database not found: {db}")

    if is_virtualdj_running() and not dry_run and not allow_vdj_running:
        raise RuntimeError(
            "VirtualDJ is running. Close it before rewriting BPM, or pass "
            "allow_vdj_running=true (not recommended)."
        )

    before_cues = summarize_cues(audio, db)
    if not before_cues.in_database:
        raise KeyError(f"Track is not in VirtualDJ database: {audio}")
    if not before_cues.bpm:
        raise ValueError("Track has no usable BPM in VirtualDJ to halve")

    content = read_vdj_database_text(db)
    # Try path variants like summarize_cues
    candidates = [
        normalize_database_path(str(audio)),
        normalize_database_path(str(Path(source_path))),
    ]
    span = None
    path_in_db = None
    for cand in candidates:
        span = _find_song_span(content, cand)
        if span is not None:
            path_in_db = cand
            break
    if span is None or path_in_db is None:
        raise KeyError(f"Song not found in database: {audio}")

    start, end = span
    song_xml = content[start:end]
    new_song, change_meta = apply_bpm_factor_to_song_xml(
        song_xml, halve=not double_instead
    )

    musical_before = before_cues.bpm
    musical_after = (
        (musical_before / 2.0) if not double_instead else (musical_before * 2.0)
    )

    backup: Optional[str] = None
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "path": str(audio),
            "bpm_before": musical_before,
            "bpm_after": musical_after,
            "changes": change_meta,
            "database_backup": None,
        }

    if create_backup:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = f"{db}.backup.{ts}.music-sorter-bpm"
        shutil.copy2(db, backup)

    with vdj_db_write():
        rewrite_song_xml_in_database(db, path_in_db, new_song, validate=True)
    after_cues = summarize_cues(audio, db)

    return {
        "ok": True,
        "dry_run": False,
        "path": str(audio),
        "name": audio.name,
        "bpm_before": musical_before,
        "bpm_after": after_cues.bpm or musical_after,
        "changes": change_meta,
        "database_backup": backup,
        "action": "double_bpm" if double_instead else "halve_bpm",
    }
