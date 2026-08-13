"""Lightweight audio metadata via ffprobe (bitrate, duration, codec)."""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from typing import Any, Optional


_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_lock = threading.Lock()
_disk_lock = threading.Lock()

LOSSLESS_EXTS = {".flac", ".wav", ".aiff", ".aif", ".wv"}
MIN_BITRATE_KBPS = 320


def probe_audio_meta(audio_path: str | Path, *, use_cache: bool = True) -> dict[str, Any]:
    path = Path(audio_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Audio not found: {path}")

    mtime = path.stat().st_mtime
    key = str(path)
    if use_cache:
        with _lock:
            cached = _cache.get(key)
            if cached and cached[0] == mtime:
                return dict(cached[1])

    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout or "{}")
    fmt = data.get("format") or {}
    streams = data.get("streams") or []
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), {})

    bit_rate = _to_int(fmt.get("bit_rate")) or _to_int(audio_stream.get("bit_rate"))
    # Fallback: size / duration
    duration = _to_float(fmt.get("duration")) or _to_float(audio_stream.get("duration"))
    size_bytes = path.stat().st_size
    if (not bit_rate or bit_rate <= 0) and duration and duration > 0:
        bit_rate = int((size_bytes * 8) / duration)

    bitrate_kbps: Optional[int] = None
    if bit_rate and bit_rate > 0:
        bitrate_kbps = max(1, int(round(bit_rate / 1000.0)))

    sample_rate = _to_int(audio_stream.get("sample_rate"))
    channels = _to_int(audio_stream.get("channels"))
    codec = (audio_stream.get("codec_name") or fmt.get("format_name") or "").split(",")[0]

    payload = {
        "path": str(path),
        "bitrate_kbps": bitrate_kbps,
        "bit_rate": bit_rate,
        "duration": duration,
        "sample_rate": sample_rate,
        "channels": channels,
        "codec": codec or None,
        "size_bytes": size_bytes,
    }
    if use_cache:
        with _lock:
            _cache[key] = (mtime, payload)
            if len(_cache) > 256:
                _cache.pop(next(iter(_cache)))
    return payload


def is_lossless_path(path: str | Path) -> bool:
    return Path(path).suffix.lower() in LOSSLESS_EXTS


def _bitrate_cache_path() -> Path:
    from .config import DJ_NOTES_ROOT

    return DJ_NOTES_ROOT / "audio_bitrates.json"


def _load_bitrate_disk() -> dict[str, Any]:
    cache_path = _bitrate_cache_path()
    if not cache_path.is_file():
        return {}
    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _save_bitrate_disk(data: dict[str, Any]) -> None:
    cache_path = _bitrate_cache_path()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(cache_path)


def cached_bitrate_kbps(path: str | Path, *, probe: bool = True) -> Optional[int]:
    """Return kbps from lossless hint, disk cache, or ffprobe."""
    audio = Path(path)
    if is_lossless_path(audio):
        return 1411
    key = str(audio)
    mtime = None
    try:
        if audio.is_file():
            mtime = audio.stat().st_mtime
    except OSError:
        mtime = None
    with _disk_lock:
        disk = _load_bitrate_disk()
        hit = disk.get(key)
        if isinstance(hit, dict) and hit.get("kbps") is not None:
            if mtime is None or float(hit.get("mtime") or 0) == float(mtime):
                try:
                    return int(hit["kbps"])
                except (TypeError, ValueError):
                    pass
        if not probe or not audio.is_file():
            return None
    try:
        meta = probe_audio_meta(audio)
        kbps = meta.get("bitrate_kbps")
        kbps_i = int(kbps) if kbps else None
    except Exception:
        kbps_i = None
    if kbps_i is None:
        return None
    with _disk_lock:
        disk = _load_bitrate_disk()
        disk[key] = {"kbps": kbps_i, "mtime": mtime}
        try:
            _save_bitrate_disk(disk)
        except OSError:
            pass
    return kbps_i


def track_meets_bitrate_floor(
    track: dict[str, Any],
    min_kbps: int = MIN_BITRATE_KBPS,
    *,
    probe: bool = False,
) -> bool:
    """True if lossless, known kbps >= floor, or (when probe=False) bitrate unknown."""
    path = track.get("path") or track.get("name") or ""
    if is_lossless_path(path):
        return True
    raw = track.get("bitrate_kbps")
    if raw is not None:
        try:
            return float(raw) >= float(min_kbps)
        except (TypeError, ValueError):
            return False
    if not probe:
        return True
    kbps = cached_bitrate_kbps(path, probe=True)
    if kbps is None:
        return False
    return kbps >= min_kbps


def _to_int(value: Any) -> Optional[int]:
    try:
        if value in (None, ""):
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
