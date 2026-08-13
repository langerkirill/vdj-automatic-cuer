"""Safety checks and low-memory VirtualDJ database.xml mutation."""

from __future__ import annotations

import gc
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence


VDJ_DATABASE_ROOT = "VirtualDJ_Database"
MANUAL_CUE_TYPES = {"cue", "loop"}

# Set VDJ_ALLOW_RUNNING_WRITES=1 only for emergency recovery with VDJ force-quit.
_ALLOW_RUNNING_WRITES_ENV = "VDJ_ALLOW_RUNNING_WRITES"


class VirtualDJRunningError(RuntimeError):
    """Raised when a database.xml write is refused because VirtualDJ is open."""


def allow_vdj_running_writes() -> bool:
    """True only when emergency override env is explicitly enabled."""
    return os.environ.get(_ALLOW_RUNNING_WRITES_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def is_virtualdj_running() -> bool:
    """
    Return True when a VirtualDJ process appears to be active.

    Prefer exact process-name matches so paths containing "VirtualDJ" (e.g.
    Application Support folders in another process's argv) do not false-positive.
    """
    checks: List[List[str]] = [
        ["pgrep", "-x", "VirtualDJ"],
        ["pgrep", "-f", "/VirtualDJ.app/Contents/MacOS/VirtualDJ"],
        ["pgrep", "-f", "VirtualDJ.exe"],
    ]
    for cmd in checks:
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
            )
        except Exception:
            continue
        if result.returncode == 0 and result.stdout.strip():
            return True

    # Fallback: scan full command lines, require the app binary name.
    try:
        result = subprocess.run(
            ["pgrep", "-fl", "VirtualDJ"],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return False
    if result.returncode != 0:
        return False
    for line in result.stdout.splitlines():
        lower = line.lower()
        if "virtualdj.app" in lower or lower.rstrip().endswith("virtualdj"):
            # Ignore our own tooling that only mentions the folder name.
            if "python" in lower and "virtualdj.app" not in lower:
                continue
            return True
    return False


def assert_safe_to_write_vdj_database() -> None:
    """
    Refuse database.xml mutations while VirtualDJ is running.

    Writing while VDJ is open is the main cause of the “database corrupted”
    popup and lost Cues Sorted clones: VDJ overwrites our edits on save/exit.
    """
    if not is_virtualdj_running():
        return
    if allow_vdj_running_writes():
        return
    raise VirtualDJRunningError(
        "VirtualDJ is running — refusing to write database.xml. "
        "Close VirtualDJ completely, then retry. "
        "Writing while it is open causes the corruption popup and drops "
        "recent library entries. Emergency only: "
        f"{_ALLOW_RUNNING_WRITES_ENV}=1"
    )


# --- Auto-heal: VDJ "fix" can wipe a full library down to ~160KB -------------
# Full libraries here are ~30–40MB. Wipes are ~100–200KB. Use size as the primary signal
# so small test databases are not treated as catastrophic.
WIPE_SIZE_BYTES = 2_000_000  # under this → catastrophic wipe candidate
AUTO_GOLDEN_PREFIX = "database.xml.golden.auto."
AUTO_GOLDEN_KEEP = 8
LAST_GOOD_META_NAME = "database.xml.last-good.json"


def _vdj_home(database_path: os.PathLike | str) -> Path:
    return Path(database_path).expanduser().resolve().parent


def quick_database_fingerprint(database_path: os.PathLike | str) -> Dict[str, Any]:
    """
    Cheap health signal without full XML parse.

    Uses size + Song tag count from a binary scan (works on multi‑MB libraries).
    """
    path = Path(database_path)
    if not path.is_file():
        return {
            "exists": False,
            "size_bytes": 0,
            "song_count": 0,
            "has_crlf": False,
            "has_root": False,
            "healthy": False,
            "reason": "missing",
        }
    raw = path.read_bytes()
    size = len(raw)
    songs = raw.count(b"<Song")
    # Prefer exact CRLF count match; also accept head check for huge files.
    lf = raw.count(b"\n")
    crlf = raw.count(b"\r\n")
    has_crlf = lf == 0 or crlf == lf
    has_root = b"<VirtualDJ_Database" in raw[:512]
    reason = "ok"
    healthy = True
    if not has_root:
        healthy = False
        reason = "missing_root"
    elif size < WIPE_SIZE_BYTES:
        # Only treat as wipe when we *know* a much larger good copy exists nearby,
        # or the file is the classic near-empty stub. Callers that only have a tiny
        # test fixture remain healthy unless a golden is present for recovery tests.
        healthy = False
        reason = "wipe_size"
    elif not has_crlf:
        # LF-only full libraries make VDJ reset the DB.
        healthy = False
        reason = "missing_crlf"
    return {
        "exists": True,
        "size_bytes": size,
        "song_count": songs,
        "has_crlf": has_crlf,
        "has_root": has_root,
        "healthy": healthy,
        "reason": reason,
    }


def _candidate_recovery_files(vdj_dir: Path) -> List[Path]:
    """Prefer auto goldens, then manual goldens, then VDJ 'broken' full copies, then sorter backups."""
    patterns = [
        f"{AUTO_GOLDEN_PREFIX}*",
        "database.xml.golden.*",
        "database.xml.backup.*",
    ]
    found: List[Path] = []
    for pattern in patterns:
        found.extend(vdj_dir.glob(pattern))
    backup_dir = vdj_dir / "Backup"
    if backup_dir.is_dir():
        found.extend(backup_dir.glob("*broken database.xml"))
        found.extend(backup_dir.glob("*Database Backup*"))
    # Newest first among healthy candidates later
    return found


def find_best_database_recovery_source(
    database_path: os.PathLike | str,
) -> Optional[Path]:
    """Return the largest healthy recovery candidate, preferring recent auto goldens."""
    vdj_dir = _vdj_home(database_path)
    ranked: List[tuple] = []
    for cand in _candidate_recovery_files(vdj_dir):
        if not cand.is_file():
            continue
        if cand.resolve() == Path(database_path).expanduser().resolve():
            continue
        try:
            fp = quick_database_fingerprint(cand)
        except Exception:
            continue
        if not fp["healthy"]:
            continue
        # Prefer auto goldens, then explicit goldens, then everything else; then recency + size
        name = cand.name
        tier = 0
        if name.startswith(AUTO_GOLDEN_PREFIX):
            tier = 3
        elif "golden" in name:
            tier = 2
        elif "broken" in name:
            tier = 1
        mtime = cand.stat().st_mtime
        ranked.append((tier, mtime, fp["size_bytes"], fp["song_count"], cand))
    if not ranked:
        return None
    ranked.sort(key=lambda row: (row[0], row[1], row[2], row[3]), reverse=True)
    return ranked[0][4]


def snapshot_last_good_database(database_path: os.PathLike | str) -> Optional[Path]:
    """
    After a successful write, keep a rolling golden so wipe recovery needs no user.

    Safe to call often; rotates old auto goldens.
    """
    path = Path(database_path)
    if not path.is_file():
        return None
    fp = quick_database_fingerprint(path)
    if not fp["healthy"]:
        return None
    # Never snapshot while VDJ is open — it may be mid-rewrite to a wipe.
    if is_virtualdj_running() and not allow_vdj_running_writes():
        return None

    vdj_dir = path.parent
    from datetime import datetime

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = vdj_dir / f"{AUTO_GOLDEN_PREFIX}{ts}"
    shutil.copy2(path, dest)

    # Persist last-good meta for health UI / tests
    meta = {
        "path": str(dest),
        "size_bytes": fp["size_bytes"],
        "song_count": fp["song_count"],
        "source": str(path),
        "ts": ts,
    }
    try:
        import json

        (vdj_dir / LAST_GOOD_META_NAME).write_text(
            json.dumps(meta, indent=2) + "\n", encoding="utf-8"
        )
    except Exception:
        pass

    # Rotate
    autos = sorted(
        vdj_dir.glob(f"{AUTO_GOLDEN_PREFIX}*"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in autos[AUTO_GOLDEN_KEEP:]:
        try:
            old.unlink()
        except OSError:
            pass
    return dest


def _is_strictly_better_recovery(source: Path, current_fp: Dict[str, Any]) -> bool:
    """Recovery source must be a real full library, not another tiny stub."""
    src = quick_database_fingerprint(source)
    if not src.get("has_root") or not src.get("has_crlf"):
        return False
    if src["size_bytes"] < WIPE_SIZE_BYTES:
        return False
    # Must be substantially larger than the wiped file (or current missing).
    return src["size_bytes"] >= max(current_fp.get("size_bytes", 0) * 5, WIPE_SIZE_BYTES)


def recover_vdj_database_if_wiped(
    database_path: os.PathLike | str,
    *,
    force: bool = False,
) -> Dict[str, Any]:
    """
    If database.xml looks wiped/corrupt-small, restore the best full golden.

    Refuses to restore while VirtualDJ is running (unless emergency env), so we
    do not fight a live VDJ rewrite. Returns a status dict always.
    """
    path = Path(database_path)
    fp = quick_database_fingerprint(path)
    result: Dict[str, Any] = {
        "recovered": False,
        "needed": not fp["healthy"],
        "fingerprint": fp,
        "source": None,
        "error": None,
    }
    if fp["healthy"] and not force:
        return result

    if is_virtualdj_running() and not allow_vdj_running_writes():
        result["error"] = "virtualdj_running"
        return result

    source = find_best_database_recovery_source(path)
    if source is None or not _is_strictly_better_recovery(source, fp):
        # Tiny DBs with no full golden nearby are test fixtures / fresh installs —
        # not catastrophic wipes. Leave them alone so writers still work.
        result["error"] = "no_recovery_source"
        result["needed"] = fp.get("reason") in {"wipe_size", "missing_crlf", "missing_root"}
        return result

    # Stash the bad file for forensics
    try:
        from datetime import datetime

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        if path.is_file():
            wiped_copy = path.parent / f"database.xml.wiped.{ts}"
            shutil.copy2(path, wiped_copy)
            result["wiped_saved_as"] = str(wiped_copy)
    except Exception:
        pass

    try:
        # Copy without going through write_vdj_database_text (already asserted VDJ closed).
        tmp = path.parent / f".database.xml.recovering.{os.getpid()}"
        shutil.copy2(source, tmp)
        src_fp = quick_database_fingerprint(tmp)
        if src_fp["size_bytes"] < WIPE_SIZE_BYTES:
            tmp.unlink(missing_ok=True)
            result["error"] = "source_unhealthy"
            return result
        os.replace(tmp, path)
        result["recovered"] = True
        result["source"] = str(source)
        result["fingerprint"] = quick_database_fingerprint(path)
        # After restore the file is large + CRLF → mark healthy even if fingerprint
        # thresholds change later.
        snapshot_last_good_database(path)
    except Exception as exc:
        result["error"] = str(exc)
    return result


def ensure_healthy_vdj_database(database_path: os.PathLike | str) -> Dict[str, Any]:
    """Health check + auto-heal entry point used by Music Sorter and writers."""
    path = Path(database_path)
    fp = quick_database_fingerprint(path)
    if fp["healthy"]:
        return {"ok": True, "recovered": False, "fingerprint": fp}

    recovery = recover_vdj_database_if_wiped(path)
    if recovery.get("recovered"):
        return {
            "ok": True,
            "recovered": True,
            "fingerprint": recovery.get("fingerprint") or fp,
            "recovery": recovery,
        }

    # No full golden available: allow small/test DBs to proceed; flag real wipes
    # only when a better source exists but restore failed.
    if recovery.get("error") == "no_recovery_source" and fp.get("reason") == "wipe_size":
        return {
            "ok": True,
            "recovered": False,
            "fingerprint": {**fp, "healthy": True, "reason": "ok_small_or_test"},
            "recovery": recovery,
        }

    return {
        "ok": False,
        "recovered": False,
        "fingerprint": recovery.get("fingerprint") or fp,
        "recovery": recovery,
    }


# Match Song open tags. FilePath may contain XML entities.
_SONG_OPEN_RE = re.compile(
    r"<Song\b(?P<attrs>[^>]*)>",
    re.IGNORECASE | re.DOTALL,
)
_FILEPATH_RE = re.compile(
    r'\bFilePath\s*=\s*"([^"]*)"',
    re.IGNORECASE,
)


def _unescape_xml_attr(value: str) -> str:
    return (
        value.replace("&quot;", '"')
        .replace("&apos;", "'")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&amp;", "&")
    )


def normalize_database_path(file_path: str) -> str:
    return unicodedata.normalize("NFC", file_path)


def read_vdj_database_text(database_path: os.PathLike | str) -> str:
    """
    Read database.xml while preserving VirtualDJ's CRLF line endings.

    Path.read_text() / open(..., encoding=utf-8) use universal newlines and
    strip \\r. VirtualDJ then treats the file as corrupted and resets the library.
    """
    raw = Path(database_path).read_bytes()
    return raw.decode("utf-8")


def write_vdj_database_text(database_path: os.PathLike | str, content: str) -> None:
    """Write database.xml as UTF-8 bytes without newline translation."""
    assert_safe_to_write_vdj_database()
    Path(database_path).write_bytes(content.encode("utf-8"))


def database_integrity_stats(database_path: os.PathLike | str) -> Dict[str, int]:
    """Stream structural stats without retaining the full XML tree."""
    path = Path(database_path)
    song_count = 0
    cue_loop_count = 0
    root_tag = None

    for event, element in ET.iterparse(path, events=("start", "end")):
        if event == "start" and root_tag is None:
            root_tag = element.tag
            if root_tag != VDJ_DATABASE_ROOT:
                raise ValueError(f"Unexpected VirtualDJ database root: {root_tag}")
            continue

        if event != "end" or element.tag != "Song":
            continue

        song_count += 1
        for poi in element.findall("Poi"):
            if poi.get("Type") in MANUAL_CUE_TYPES and poi.get("Num", "0") != "0":
                cue_loop_count += 1
        element.clear()

    if root_tag is None:
        raise ValueError("Empty or unreadable VirtualDJ database")

    return {
        "size_bytes": path.stat().st_size,
        "song_count": song_count,
        "cue_loop_count": cue_loop_count,
    }


def serialize_song_element(song: ET.Element) -> str:
    """Serialize one Song element. Prefer text-preserving rewrite over this."""
    return ET.tostring(song, encoding="unicode")


def _escape_xml_attr(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def format_vdj_poi_line(
    *,
    pos: float,
    poi_type: str,
    num: str,
    color: str,
    name: Optional[str] = None,
    size: Optional[str] = None,
    slot: Optional[str] = None,
    indent: str = "  ",
    newline: str = "\r\n",
) -> str:
    """Emit one VirtualDJ POI line in the native spaced/self-closing style."""
    attrs = []
    if name:
        attrs.append(f'Name="{_escape_xml_attr(name)}"')
    attrs.append(f'Pos="{pos:.6f}"')
    attrs.append(f'Num="{num}"')
    attrs.append(f'Color="{color}"')
    attrs.append(f'Type="{poi_type}"')
    if size is not None:
        # Native VDJ loops use Size="16.0" / "32.0"
        try:
            size_num = float(size)
            size_text = f"{size_num:.1f}" if size_num.is_integer() else f"{size_num:g}"
        except (TypeError, ValueError):
            size_text = str(size)
        attrs.append(f'Size="{size_text}"')
    if slot is not None:
        attrs.append(f'Slot="{slot}"')
    return f"{indent}<Poi {' '.join(attrs)} />{newline}"


_POI_LINE_RE = re.compile(
    r"[ \t]*<Poi\b[^>]*/>[ \t]*(?:\r?\n)?",
    re.IGNORECASE,
)
_COMMENT_RE = re.compile(
    r"[ \t]*<Comment\b[^>]*>.*?</Comment>[ \t]*(?:\r?\n)?",
    re.IGNORECASE | re.DOTALL,
)


def _is_manual_cue_or_loop_poi(poi_tag: str) -> bool:
    """True for user cue/loop markers (keep Num=0 hotcues and automix/beatgrid)."""
    type_match = re.search(r'\bType\s*=\s*"([^"]*)"', poi_tag, re.IGNORECASE)
    if type_match is None:
        return False
    poi_type = type_match.group(1).lower()
    if poi_type == "loop":
        return True
    if poi_type != "cue":
        return False
    num_match = re.search(r'\bNum\s*=\s*"([^"]*)"', poi_tag, re.IGNORECASE)
    num = num_match.group(1) if num_match else "0"
    return num != "0"


def _poi_attr(poi_tag: str, name: str) -> Optional[str]:
    match = re.search(rf'\b{name}\s*=\s*"([^"]*)"', poi_tag, re.IGNORECASE)
    return match.group(1) if match else None


def iter_manual_poi_tags(song_xml: str):
    """Yield raw POI tags for manual cue/loop markers in song order."""
    for match in _POI_LINE_RE.finditer(song_xml):
        tag = match.group(0)
        if _is_manual_cue_or_loop_poi(tag):
            yield tag


def parse_manual_poi_tag(poi_tag: str) -> Optional[Dict[str, Any]]:
    """Parse one manual cue/loop POI into a plain dict (or None if not manual)."""
    if not _is_manual_cue_or_loop_poi(poi_tag):
        return None
    poi_type = (_poi_attr(poi_tag, "Type") or "").lower()
    try:
        pos = float(_poi_attr(poi_tag, "Pos") or "0")
    except ValueError:
        pos = 0.0
    length_beats: Optional[float] = None
    if poi_type == "loop":
        size_raw = _poi_attr(poi_tag, "Size")
        if size_raw is not None:
            try:
                length_beats = float(size_raw)
            except ValueError:
                length_beats = 16.0
        else:
            length_beats = 16.0
    return {
        "kind": "loop" if poi_type == "loop" else "cue",
        "name": _poi_attr(poi_tag, "Name") or ("Loop" if poi_type == "loop" else "Cue"),
        "position": pos,
        "color": _poi_attr(poi_tag, "Color") or "",
        "num": _poi_attr(poi_tag, "Num") or ("-1" if poi_type == "loop" else "1"),
        "length_beats": length_beats,
    }


def extract_manual_pois_from_song_xml(song_xml: str) -> Dict[str, List[Dict[str, Any]]]:
    """Split existing manual markers into cues and loops (database order)."""
    cues: List[Dict[str, Any]] = []
    loops: List[Dict[str, Any]] = []
    for tag in iter_manual_poi_tags(song_xml):
        parsed = parse_manual_poi_tag(tag)
        if parsed is None:
            continue
        if parsed["kind"] == "loop":
            loops.append(parsed)
        else:
            cues.append(parsed)
    return {"cues": cues, "loops": loops}


def strip_manual_cues_from_song_xml(song_xml: str) -> str:
    """Remove cue/loop POIs and Comment nodes while keeping native VDJ markup."""
    cleaned = _POI_LINE_RE.sub(
        lambda match: "" if _is_manual_cue_or_loop_poi(match.group(0)) else match.group(0),
        song_xml,
    )
    cleaned = _COMMENT_RE.sub("", cleaned)
    return cleaned


def inject_pois_into_song_xml(
    song_xml: str,
    poi_lines: Sequence[str],
    comment: Optional[str] = None,
) -> str:
    """
    Insert formatted POI lines before </Song>, preserving original Song markup.

    This avoids ElementTree re-serialization, which VirtualDJ rejects and can
    trigger a library reset on open.
    """
    newline = _detect_newline(song_xml)
    cleaned = strip_manual_cues_from_song_xml(song_xml)
    close_idx = cleaned.rfind("</Song>")
    if close_idx < 0:
        raise ValueError("Song XML is missing </Song>")

    indent = "  "
    body = cleaned[:close_idx].rstrip(" \t")
    if not body.endswith("\n"):
        body += newline

    insertion = "".join(poi_lines)
    if comment:
        insertion += (
            f"{indent}<Comment>{_escape_xml_attr(comment)}</Comment>{newline}"
        )

    return body + insertion + cleaned[close_idx:]


def serialize_vdj_database(root: ET.Element) -> str:
    """Serialize a full database tree (legacy path; prefer surgical rewrite)."""
    xml_str = ET.tostring(root, encoding="unicode")
    if "\r\n" not in xml_str and "\n" in xml_str:
        xml_str = xml_str.replace("\n", "\r\n")
    return xml_str


def validate_database_replacement(
    candidate_path: os.PathLike | str,
    original_stats: Dict[str, int],
    stats_fn: Optional[Callable[[os.PathLike | str], Dict[str, int]]] = None,
) -> Dict[str, int]:
    """Reject parseable but structurally broken replacement databases."""
    counter = stats_fn or database_integrity_stats
    candidate_stats = counter(candidate_path)

    if candidate_stats["song_count"] < original_stats["song_count"]:
        raise ValueError(
            "Generated database failed integrity check: song count dropped "
            f"from {original_stats['song_count']} to "
            f"{candidate_stats['song_count']}"
        )

    original_cues = original_stats["cue_loop_count"]
    candidate_cues = candidate_stats["cue_loop_count"]
    if original_cues >= 20 and candidate_cues < int(original_cues * 0.75):
        raise ValueError(
            "Generated database failed integrity check: cue/loop count dropped "
            f"from {original_cues} to {candidate_cues}"
        )
    if 0 < original_cues < 20 and candidate_cues == 0:
        raise ValueError(
            "Generated database failed integrity check: cue/loop count dropped "
            f"from {original_cues} to 0"
        )

    original_size = original_stats["size_bytes"]
    candidate_size = candidate_stats["size_bytes"]
    if original_size >= 1_000_000 and candidate_size < int(original_size * 0.75):
        raise ValueError(
            "Generated database failed integrity check: file size dropped "
            f"from {original_size} to {candidate_size} bytes"
        )

    return candidate_stats


def _detect_newline(content: str) -> str:
    return "\r\n" if "\r\n" in content else "\n"


def _find_song_span(content: str, audio_file_path: str) -> Optional[tuple[int, int]]:
    """Return [start, end) byte/char offsets of the Song element for a path."""
    target = normalize_database_path(audio_file_path)
    for match in _SONG_OPEN_RE.finditer(content):
        attrs = match.group("attrs")
        path_match = _FILEPATH_RE.search(attrs)
        if path_match is None:
            continue
        song_path = normalize_database_path(_unescape_xml_attr(path_match.group(1)))
        if song_path != target:
            continue

        start = match.start()
        # Scan for the matching close tag from this Song open.
        depth = 1
        pos = match.end()
        while depth > 0:
            next_open = content.find("<Song", pos)
            next_close = content.find("</Song>", pos)
            if next_close < 0:
                return None
            if next_open >= 0 and next_open < next_close:
                depth += 1
                pos = next_open + 5
            else:
                depth -= 1
                if depth == 0:
                    return start, next_close + len("</Song>")
                pos = next_close + len("</Song>")
    return None


def load_song_element(database_path: os.PathLike | str, audio_file_path: str) -> ET.Element:
    """Parse only the Song element for one track (low memory)."""
    content = read_vdj_database_text(database_path)
    span = _find_song_span(content, audio_file_path)
    if span is None:
        raise KeyError(f"Song not found in database: {audio_file_path}")
    start, end = span
    return ET.fromstring(content[start:end])


# Top-level library roots omitted from Directory Sort labels.
# Nested folders like "Add Cues" under Zouk are *not* roots — they are leaf labels.
_DIRECTORY_SORT_ROOT_NAMES = frozenset({"Zouk", "House", "Cues Sorted"})


def _is_directory_sort_root(part: str) -> bool:
    """True for Zouk/House/Cues Sorted and variants like 'Cues Sorted copy'."""
    if part in _DIRECTORY_SORT_ROOT_NAMES:
        return True
    # Backup / copy crates that mirror Cues Sorted
    if part.startswith("Cues Sorted"):
        return True
    return False


def directory_sort_label(file_path: str) -> str:
    """Folder label for VirtualDJ User2 / Directory Sort column.

    Strips library roots (Zouk, House, Cues Sorted, …). Then:
    - one folder under the root → that leaf only (``Energy``)
    - deeper nesting → bottom two folders (``Chill/Shaman``)

    Examples::

        .../Zouk/Chill/Shaman/t.flac     → Chill/Shaman
        .../Zouk/Energy/t.flac           → Energy
        .../Cues Sorted/Chill/t.flac     → Chill
        .../Cues Sorted/Chill/Mystical/t → Chill/Mystical
        .../House/Chill/t.flac           → Chill
    """
    path = Path(normalize_database_path(file_path))
    parent = path.parent
    if not parent.name or parent == parent.parent:
        return ""

    parts = [p for p in parent.parts if p and p != "/"]
    # Path relative to the *innermost* library root (Cues Sorted wins over Cues).
    rel: list[str] | None = None
    for index, part in enumerate(parts):
        if _is_directory_sort_root(part):
            rel = list(parts[index + 1 :])

    if rel is None:
        # Outside known roots: at most last two folder names.
        rel = parts[-2:] if len(parts) >= 2 else parts

    if not rel:
        return ""
    if len(rel) == 1:
        return rel[0]
    return f"{rel[-2]}/{rel[-1]}"


def song_xml_with_directory_sort_user2(song_xml: str, file_path: str) -> str:
    """Set Tags User2 to the Directory Sort label for ``file_path``."""
    label = directory_sort_label(file_path)
    if not label:
        return song_xml
    escaped = _escape_xml_attr(label)
    # Prefer self-closing Tags; do not swallow the '/' into attrs.
    tags_m = re.search(r"(<Tags\b)(.*?)(\s*/>)", song_xml, flags=re.DOTALL)
    if tags_m is None:
        tags_m = re.search(r"(<Tags\b)([^>]*)(>)", song_xml)
    if tags_m is None:
        return song_xml
    attrs = tags_m.group(2)
    if re.search(r"\bUser2\s*=", attrs):
        new_attrs = re.sub(
            r'\bUser2\s*=\s*"[^"]*"',
            f'User2="{escaped}"',
            attrs,
            count=1,
        )
    elif re.search(r"\bFlag\s*=", attrs):
        new_attrs = re.sub(
            r"(\bFlag\s*=)",
            f'User2="{escaped}" \\1',
            attrs,
            count=1,
        )
    else:
        new_attrs = attrs.rstrip() + f' User2="{escaped}"'
    new_tags = f"{tags_m.group(1)}{new_attrs}{tags_m.group(3)}"
    return song_xml[: tags_m.start()] + new_tags + song_xml[tags_m.end() :]


def song_xml_with_new_filepath(
    song_xml: str,
    new_file_path: str,
    *,
    directory_sort_path: str | None = None,
) -> str:
    """
    Change only the FilePath attribute on the Song open tag.

    Also refreshes Tags User2 (Directory Sort). Default is the new path's
    folders; pass directory_sort_path to keep the origin crate (e.g. Zouk
    Chill/Mystical after copying into Sets/).
    """
    match = _SONG_OPEN_RE.search(song_xml)
    if match is None:
        raise ValueError("Song XML is missing a <Song> open tag")

    attrs = match.group("attrs")
    if _FILEPATH_RE.search(attrs) is None:
        raise ValueError("Song open tag is missing FilePath")

    escaped = _escape_xml_attr(normalize_database_path(new_file_path))
    new_attrs = _FILEPATH_RE.sub(f'FilePath="{escaped}"', attrs, count=1)
    new_open = f"<Song{new_attrs}>"
    updated = song_xml[: match.start()] + new_open + song_xml[match.end() :]
    label_from = directory_sort_path or new_file_path
    return song_xml_with_directory_sort_user2(updated, label_from)


def relocate_song_filepath_in_database(
    database_path: os.PathLike | str,
    old_file_path: str,
    new_file_path: str,
    *,
    validate: bool = True,
) -> Dict[str, int]:
    """
    Surgically retarget one Song entry after the audio file was moved/renamed.

    Uses the same text-preserving rewrite path as cue injection: CRLF kept,
    native Song body untouched, atomic replace with integrity checks.
    """
    content = read_vdj_database_text(database_path)
    span = _find_song_span(content, old_file_path)
    if span is None:
        raise KeyError(f"Song not found in database: {old_file_path}")
    start, end = span
    updated_song_xml = song_xml_with_new_filepath(content[start:end], new_file_path)
    del content
    gc.collect()
    return rewrite_song_xml_in_database(
        database_path,
        old_file_path,
        updated_song_xml,
        validate=validate,
    )


def insert_song_xml_in_database(
    database_path: os.PathLike | str,
    song_xml: str,
    *,
    validate: bool = True,
) -> Dict[str, int]:
    """
    Append one Song block before </VirtualDJ_Database>, preserving CRLF.

    Used when a cued track is copied to a second path (e.g. Cues Sorted) and
    needs its own database entry without re-serializing the library.
    """
    path = Path(database_path)
    content = read_vdj_database_text(path)
    original_stats = _lightweight_rewrite_stats(content, path) if validate else None
    newline = _detect_newline(content)

    song_xml = song_xml.strip()
    if newline == "\r\n":
        song_xml = song_xml.replace("\r\n", "\n").replace("\n", "\r\n")
    if not song_xml.endswith(newline):
        song_xml = song_xml + newline

    close_tag = "</VirtualDJ_Database>"
    close_idx = content.rfind(close_tag)
    if close_idx < 0:
        raise ValueError("Database is missing </VirtualDJ_Database>")

    # Ensure separation from previous content.
    prefix = content[:close_idx].rstrip(" \t")
    if not prefix.endswith("\n"):
        prefix += newline
    suffix = content[close_idx:]
    del content
    gc.collect()

    return atomic_replace_database_parts(
        path,
        (prefix, song_xml, suffix),
        original_stats,
        stats_fn=_lightweight_content_stats if validate else None,
    )


def clone_song_entry_to_path(
    database_path: os.PathLike | str,
    source_file_path: str,
    new_file_path: str,
    *,
    validate: bool = True,
    skip_if_exists: bool = True,
) -> Dict[str, Any]:
    """
    Duplicate one Song entry under a new FilePath (cues/loops preserved).

    Returns stats plus flags describing what happened.
    """
    content = read_vdj_database_text(database_path)
    new_norm = normalize_database_path(new_file_path)
    existing = _find_song_span(content, new_norm)
    if existing is not None:
        if skip_if_exists:
            return {
                "cloned": False,
                "already_present": True,
                "song_count": content.count("<Song"),
            }
        raise FileExistsError(f"Song already exists in database: {new_file_path}")

    span = _find_song_span(content, source_file_path)
    if span is None:
        raise KeyError(f"Song not found in database: {source_file_path}")
    start, end = span
    cloned_xml = song_xml_with_new_filepath(content[start:end], new_norm)
    del content
    gc.collect()
    stats = insert_song_xml_in_database(
        database_path, cloned_xml, validate=validate
    )
    return {
        "cloned": True,
        "already_present": False,
        **stats,
    }


def _lightweight_rewrite_stats(content: str, path: Path) -> Dict[str, int]:
    """Fast structural counts for surgical rewrites (no full XML tree)."""
    return {
        "size_bytes": path.stat().st_size if path.exists() else len(content.encode("utf-8")),
        "song_count": content.count("<Song"),
        # Cheap upper bound used only for catastrophic-drop detection.
        "cue_loop_count": content.count('Type="cue"') + content.count("Type='cue'")
        + content.count('Type="loop"') + content.count("Type='loop'"),
    }


def rewrite_song_xml_in_database(
    database_path: os.PathLike | str,
    audio_file_path: str,
    new_song_xml: str,
    *,
    validate: bool = True,
) -> Dict[str, int]:
    """Replace one Song block with pre-built XML that keeps native VDJ formatting."""
    path = Path(database_path)
    content = read_vdj_database_text(path)
    original_stats = _lightweight_rewrite_stats(content, path) if validate else None
    span = _find_song_span(content, audio_file_path)
    if span is None:
        raise KeyError(f"Song not found in database: {audio_file_path}")

    start, end = span
    newline = _detect_newline(content)
    song_xml = new_song_xml
    if newline == "\r\n":
        # Force CRLF for any injected markup (Path.read_text would have stripped it).
        song_xml = song_xml.replace("\r\n", "\n").replace("\n", "\r\n")

    # Guard: never allow a rewritten Song that lost most of its original bulk
    # (Tags/Infos/Scan/automix markers), which indicates bad re-serialization.
    original_song = content[start:end]
    if len(original_song) >= 400 and len(song_xml) < int(len(original_song) * 0.5):
        raise ValueError(
            "Refusing song rewrite that shrinks the Song block by more than 50% "
            f"({len(original_song)} -> {len(song_xml)} chars)"
        )

    # Also refuse converting a CRLF database into LF-only (VDJ resets the library).
    if newline == "\r\n" and "\r\n" not in song_xml:
        raise ValueError("Refusing song rewrite that would drop CRLF line endings")

    prefix = content[:start]
    suffix = content[end:]
    del content
    gc.collect()

    return atomic_replace_database_parts(
        path,
        (prefix, song_xml, suffix),
        original_stats,
        stats_fn=_lightweight_content_stats if validate else None,
    )


def rewrite_song_in_database(
    database_path: os.PathLike | str,
    audio_file_path: str,
    mutator: Callable[[ET.Element], None],
    *,
    validate: bool = True,
) -> Dict[str, int]:
    """
    Legacy ElementTree mutator path.

    Prefer rewrite_song_xml_in_database / inject_pois_into_song_xml so VirtualDJ
    keeps the original Tags/Scan/automix markup intact.
    """
    content = read_vdj_database_text(database_path)
    span = _find_song_span(content, audio_file_path)
    if span is None:
        raise KeyError(f"Song not found in database: {audio_file_path}")
    start, end = span
    song = ET.fromstring(content[start:end])
    mutator(song)
    # Still go through the guarded XML rewriter so size checks apply.
    return rewrite_song_xml_in_database(
        database_path,
        audio_file_path,
        serialize_song_element(song),
        validate=validate,
    )


def _lightweight_content_stats(database_path: os.PathLike | str) -> Dict[str, int]:
    path = Path(database_path)
    content = read_vdj_database_text(path)
    return _lightweight_rewrite_stats(content, path)


def atomic_replace_database_parts(
    database_path: os.PathLike | str,
    parts: Sequence[str],
    original_stats: Optional[Dict[str, int]] = None,
    stats_fn: Optional[Callable[[os.PathLike | str], Dict[str, int]]] = None,
) -> Dict[str, int]:
    """Write candidate XML parts to a temp file, validate, then replace atomically."""
    # Central hard gate: every surgical write path ends here.
    assert_safe_to_write_vdj_database()
    # If a previous VDJ "fix" wiped the library, heal before we write one Song.
    ensure_healthy_vdj_database(database_path)

    path = Path(database_path)
    directory = path.parent
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(directory),
    )
    temp_path = Path(temp_name)
    counter = stats_fn or database_integrity_stats
    try:
        # Binary write preserves exact \\r\\n bytes (text mode can still mangle them).
        with os.fdopen(fd, "wb") as handle:
            for part in parts:
                handle.write(part.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())

        # Free large string parts before re-reading the candidate for stats.
        del parts
        gc.collect()

        # Hard reject LF-only rewrites of a CRLF VirtualDJ database.
        candidate_raw = temp_path.read_bytes()
        if original_stats is not None:
            # If original was large and CRLF-based, require CRLF in candidate.
            if path.exists():
                original_raw_head = path.read_bytes()[:4096]
                if b"\r\n" in original_raw_head and b"\r\n" not in candidate_raw[:8192]:
                    raise ValueError(
                        "Generated database dropped CRLF line endings; "
                        "VirtualDJ will treat this as corrupt and reset the library"
                    )

        if original_stats is not None:
            stats = validate_database_replacement(
                temp_path, original_stats, stats_fn=counter
            )
        else:
            stats = counter(temp_path)

        # Re-check immediately before replace (user may open VDJ mid-write).
        assert_safe_to_write_vdj_database()
        os.replace(temp_path, path)
        # Keep a rolling golden so the next wipe can self-heal without a human.
        try:
            snapshot_last_good_database(path)
        except Exception:
            pass
        return stats
    except Exception:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        raise


def atomic_replace_database(
    database_path: os.PathLike | str,
    xml_content: str,
    original_stats: Optional[Dict[str, int]] = None,
    stats_fn: Optional[Callable[[os.PathLike | str], Dict[str, int]]] = None,
) -> Dict[str, int]:
    """Write candidate XML to a temp file, validate, then replace atomically."""
    return atomic_replace_database_parts(
        database_path, (xml_content,), original_stats, stats_fn=stats_fn
    )


def copy_database_atomically(source: os.PathLike | str, destination: os.PathLike | str) -> None:
    """Copy a validated database file into place."""
    shutil.copy2(source, destination)
