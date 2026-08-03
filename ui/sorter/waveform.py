"""Peak envelopes for the sorter waveform UI (ffmpeg, same idea as AutoCue audit)."""

from __future__ import annotations

import struct
import subprocess
import threading
from pathlib import Path
from typing import Any, Optional


_cache: dict[str, tuple[float, int, dict[str, Any]]] = {}
_lock = threading.Lock()


def _ffprobe_duration(audio_path: str) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            audio_path,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(result.stdout.strip() or 0.0)


def decode_envelope(
    audio_path: str,
    bins: int = 900,
    sample_rate: int = 800,
) -> list[float]:
    """
    Downsample mono PCM via ffmpeg and return per-bin peak amplitudes in [0, 1].
    """
    if bins < 32:
        bins = 32
    if bins > 4000:
        bins = 4000

    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        audio_path,
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-f",
        "s16le",
        "-",
    ]
    result = subprocess.run(command, capture_output=True, check=True)
    sample_count = len(result.stdout) // 2
    if sample_count == 0:
        return [0.0] * bins

    samples = struct.unpack(f"<{sample_count}h", result.stdout)
    envelope: list[float] = []
    for bin_index in range(bins):
        start = int(bin_index * sample_count / bins)
        end = int((bin_index + 1) * sample_count / bins)
        if end <= start:
            envelope.append(0.0)
            continue
        window = samples[start:end]
        peak = max(abs(value) for value in window) / 32768.0
        envelope.append(min(1.0, peak))
    return envelope


def build_waveform(
    audio_path: str | Path,
    *,
    bins: int = 900,
    use_cache: bool = True,
) -> dict[str, Any]:
    path = Path(audio_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Audio not found: {path}")

    mtime = path.stat().st_mtime
    cache_key = f"{path}|{bins}"
    if use_cache:
        with _lock:
            cached = _cache.get(cache_key)
            if cached and cached[0] == mtime and cached[1] == bins:
                return dict(cached[2])

    peaks = decode_envelope(str(path), bins=bins)
    duration = _ffprobe_duration(str(path))
    payload = {
        "path": str(path),
        "duration": duration,
        "bins": bins,
        "peaks": peaks,
    }
    if use_cache:
        with _lock:
            _cache[cache_key] = (mtime, bins, payload)
            # Bound cache size roughly.
            if len(_cache) > 64:
                # Drop an arbitrary old entry.
                _cache.pop(next(iter(_cache)))
    return payload
