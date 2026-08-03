"""Lightweight audio metadata via ffprobe (bitrate, duration, codec)."""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from typing import Any, Optional


_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_lock = threading.Lock()


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
