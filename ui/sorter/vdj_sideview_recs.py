"""Push transition recs into VirtualDJ My Lists + Sideview tabs."""

from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any, Optional
from xml.sax.saxutils import escape

from .config import CUES_ROOT, VDJ_DATABASE
from .musical_key import unescape_xml_text
from .transition_recs import PICKS_PER_BUCKET, audio_file_exists

VDJ_SUPPORT = VDJ_DATABASE.parent
VDJ_MYLISTS = VDJ_SUPPORT / "MyLists"
VDJ_SETTINGS = VDJ_SUPPORT / "settings.xml"
VDJ_CUES_LISTS = CUES_ROOT

COMBINED_NAME = "Next Recs"
BUCKET_FOLDERS = {
    "higher_energy": "Recs Higher",
    "same_energy": "Recs Same",
    "lower_energy": "Recs Lower",
}
ENERGY_MARK = {
    "higher_energy": "HIGHER ·",
    "same_energy": "SAME ·",
    "lower_energy": "LOWER ·",
}
BUCKET_ORDER = ("higher_energy", "same_energy", "lower_energy")

_write_lock = threading.Lock()


def _xml_attr(value: Any) -> str:
    return escape(str(value or ""), {'"': "&quot;"})


def _existing_audio_path(raw: str) -> str:
    path = unescape_xml_text(raw or "")
    if audio_file_exists(path):
        return path
    return ""


def _song_attrs(pick: dict[str, Any], *, title_prefix: str = "") -> str:
    path = _existing_audio_path(pick.get("path") or "") or unescape_xml_text(
        pick.get("path") or ""
    )
    p = Path(path)
    size = 0
    try:
        if p.is_file():
            size = p.stat().st_size
    except OSError:
        size = 0
    artist = pick.get("artist") or ""
    title = pick.get("title") or pick.get("name") or p.stem
    if title_prefix:
        title = f"{title_prefix} {title}".strip()
    bits = [
        f'path="{_xml_attr(path)}"',
        f'size="{size}"',
        f'artist="{_xml_attr(artist)}"',
        f'title="{_xml_attr(title)}"',
    ]
    bpm = pick.get("bpm")
    if bpm not in (None, ""):
        try:
            bits.append(f'bpm="{float(bpm):.3f}"')
        except (TypeError, ValueError):
            pass
    key = pick.get("key") or pick.get("camelot") or ""
    if key:
        bits.append(f'key="{_xml_attr(key)}"')
    return " ".join(bits)


def build_virtual_folder_xml(
    picks: list[dict[str, Any]],
    *,
    prefixes: list[str] | None = None,
) -> str:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<VirtualFolder noDuplicates="yes" ordered="yes">',
    ]
    idx = 0
    for i, pick in enumerate(picks):
        raw = pick.get("path") or ""
        if not _existing_audio_path(raw):
            continue
        if int(pick.get("cue_count") or 0) <= 0 and "cue_count" in pick:
            continue
        prefix = ""
        if prefixes and i < len(prefixes):
            prefix = prefixes[i]
        lines.append(f'<song {_song_attrs(pick, title_prefix=prefix)} idx="{idx}" />')
        idx += 1
    lines.append("</VirtualFolder>")
    lines.append("")
    return "\n".join(lines)


def _atomic_write(path: Path, text: str) -> None:
    """Overwrite in place when possible so VDJ keeps watching the same inode."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.write_text(text, encoding="utf-8")
        return
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _ensure_mylists_order(names: list[str]) -> None:
    order_path = VDJ_MYLISTS / "order"
    existing: list[str] = []
    if order_path.is_file():
        existing = [
            ln.strip()
            for ln in order_path.read_text(encoding="utf-8", errors="replace").splitlines()
            if ln.strip()
        ]
    changed = False
    for name in reversed(names):
        if name in existing:
            existing.remove(name)
        existing.insert(0, name)
        changed = True
    if changed:
        order_path.write_text("\n".join(existing) + "\n", encoding="utf-8")


def _parse_shortcuts(raw: str) -> list[str]:
    if not raw:
        return []
    # VDJ uses ";" between groups and "," between mylists: entries
    parts = re.split(r"[;,\n\r]+", raw)
    return [p.strip() for p in parts if p.strip()]


_RECS_TAB_DROP = re.compile(
    r"(recs higher|recs same|recs lower)",
    re.I,
)


def _is_energy_recs_tab(entry: str) -> bool:
    """True for Recs Higher/Same/Lower tabs (file path or mylists: form)."""
    if not entry:
        return False
    if _RECS_TAB_DROP.search(entry.replace("\\", "/")):
        return True
    return False


def _mylists_ref(path: Path) -> str:
    """VDJ Sideview form that actually reloads: mylists:/Recs Higher.subfolders"""
    return f"mylists:/{path.stem}.subfolders"


def ensure_sideview_shortcuts(
    folder_paths: list[Path],
    *,
    also_mylists_refs: bool = False,
) -> dict[str, Any]:
    """
    Merge rec folders into settings.xml sideviewShortcuts.

    VirtualDJ may overwrite settings on quit if it was already running.
    Lists still live in My Lists regardless.
    """
    wanted: list[str] = []
    for p in folder_paths:
        if not p:
            continue
        if also_mylists_refs:
            wanted.append(_mylists_ref(p))
        else:
            wanted.append(str(p.resolve()))
    if not VDJ_SETTINGS.is_file():
        return {"ok": False, "reason": "settings.xml missing"}

    text = VDJ_SETTINGS.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"<sideviewShortcuts>(.*?)</sideviewShortcuts>", text, re.S)
    empty = bool(re.search(r"<sideviewShortcuts\s*/>", text))
    if m:
        current = _parse_shortcuts(m.group(1))
    else:
        current = []

    merged = list(current)
    added: list[str] = []
    for path in wanted:
        if path not in merged:
            merged.append(path)
            added.append(path)

    if not added and (m or empty):
        return {"ok": True, "added": [], "shortcuts": merged}

    # Never rewrite settings.xml while VirtualDJ is running — that breaks
    # the live Sideview link so lists stop refreshing.
    try:
        from .relocate import is_virtualdj_running

        if is_virtualdj_running():
            return {
                "ok": True,
                "added": [],
                "shortcuts": current,
                "skipped": "vdj_running",
            }
    except Exception:
        pass

    body = ";".join(merged)
    if m:
        new_text = text[: m.start(1)] + body + text[m.end(1) :]
    elif "<sideviewShortcuts />" in text or "<sideviewShortcuts/>" in text:
        new_text = re.sub(
            r"<sideviewShortcuts\s*/>",
            f"<sideviewShortcuts>{body}</sideviewShortcuts>",
            text,
            count=1,
        )
    else:
        # insert before closing Settings-ish root is unknown — append near top
        new_text = text.replace(
            "</VirtualDJ>",
            f"<sideviewShortcuts>{body}</sideviewShortcuts>\n</VirtualDJ>",
            1,
        )
        if new_text == text:
            new_text = text + f"\n<sideviewShortcuts>{body}</sideviewShortcuts>\n"

    backup = VDJ_SETTINGS.with_suffix(".xml.backup.before-recs-sideview")
    if not backup.exists():
        backup.write_text(text, encoding="utf-8")
    VDJ_SETTINGS.write_text(new_text, encoding="utf-8")
    return {"ok": True, "added": added, "shortcuts": merged, "backup": str(backup)}


def remove_sideview_shortcuts(folder_paths: list[Path]) -> dict[str, Any]:
    """Drop Recs Higher/Same/Lower tabs from the Sideview strip."""
    unwanted = {str(p.resolve()) for p in folder_paths if p}
    if not VDJ_SETTINGS.is_file() or not unwanted:
        return {"ok": False, "removed": []}
    text = VDJ_SETTINGS.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"<sideviewShortcuts>(.*?)</sideviewShortcuts>", text, re.S)
    if not m:
        return {"ok": True, "removed": []}
    current = _parse_shortcuts(m.group(1))
    kept = [
        p
        for p in current
        if p not in unwanted and not _is_energy_recs_tab(p)
    ]
    removed = [p for p in current if p not in kept]
    if not removed:
        return {"ok": True, "removed": []}
    body = ";".join(kept)
    new_text = text[: m.start(1)] + body + text[m.end(1) :]
    VDJ_SETTINGS.write_text(new_text, encoding="utf-8")
    return {"ok": True, "removed": removed, "shortcuts": kept}


def write_sideview_recs(result: dict[str, Any]) -> dict[str, Any]:
    """
    Write Next Recs + Higher/Same/Lower VirtualFolders for VDJ Sideview.

    Returns paths written and shortcut status.
    """
    recs = (result or {}).get("recommendations") or {}
    written: dict[str, str] = {}
    combined: list[dict[str, Any]] = []
    combined_prefixes: list[str] = []

    with _write_lock:
        VDJ_MYLISTS.mkdir(parents=True, exist_ok=True)
        VDJ_CUES_LISTS.mkdir(parents=True, exist_ok=True)
        for bucket in BUCKET_ORDER:
            picks = list(recs.get(bucket) or [])[:PICKS_PER_BUCKET]
            mark = ENERGY_MARK[bucket]
            name = BUCKET_FOLDERS[bucket]
            xml = build_virtual_folder_xml(picks, prefixes=[mark] * len(picks))
            dest = VDJ_MYLISTS / f"{name}.vdjfolder"
            _atomic_write(dest, xml)
            cues_dest = VDJ_CUES_LISTS / f"{name}.vdjfolder"
            _atomic_write(cues_dest, xml)
            written[bucket] = str(cues_dest)
            written[f"{bucket}_mylists"] = str(dest)
            for pick in picks:
                combined.append(pick)
                combined_prefixes.append(mark)

        combined_xml = build_virtual_folder_xml(
            combined, prefixes=combined_prefixes
        )
        combined_path = VDJ_MYLISTS / f"{COMBINED_NAME}.vdjfolder"
        _atomic_write(combined_path, combined_xml)
        written["combined"] = str(combined_path)

        _ensure_mylists_order(
            [*[BUCKET_FOLDERS[b] for b in BUCKET_ORDER], COMBINED_NAME]
        )
        # Pin the three energy tabs so energy is obvious in Sideview.
        shortcut = ensure_sideview_shortcuts(
            [
                VDJ_MYLISTS / f"{BUCKET_FOLDERS[b]}.vdjfolder"
                for b in BUCKET_ORDER
            ],
            also_mylists_refs=True,
        )

    return {
        "ok": True,
        "written": written,
        "count": len(combined),
        "source": (result or {}).get("source") or {},
        "shortcuts": shortcut,
    }
