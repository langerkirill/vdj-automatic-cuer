"""Audio and stem envelope extraction for cue audits."""

from .common import *

def probe_stems(stems_path: str) -> dict[str, int]:
    if not Path(stems_path).exists():
        return {}
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-select_streams",
            "a",
            stems_path,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout)
    streams = {}
    for stream in data.get("streams", []):
        title = stream.get("tags", {}).get("title", "").lower()
        index = stream.get("index")
        if title in STEM_NAMES and index is not None:
            streams[title] = int(index)
    return streams


def decode_envelope(
    audio_path: str,
    bins: int,
    stream_map: Optional[str] = None,
    sample_rate: int = 800,
) -> list[float]:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        audio_path,
    ]
    if stream_map:
        command.extend(["-map", stream_map])
    command.extend(["-ac", "1", "-ar", str(sample_rate), "-f", "s16le", "-"])
    result = subprocess.run(command, capture_output=True, check=True)
    sample_count = len(result.stdout) // 2
    if sample_count == 0:
        return [0.0] * bins

    samples = struct.unpack(f"<{sample_count}h", result.stdout)
    envelope = []
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


def analyze_audio(track: Track, bins: int = 1200) -> AudioAnalysis:
    mix = decode_envelope(track.path, bins)
    stems = {}
    stems_path = f"{track.path}.vdjstems"
    for stem_name, stream_index in probe_stems(stems_path).items():
        stems[stem_name] = decode_envelope(stems_path, bins, f"0:{stream_index}")
    duration = track.length or ffprobe_duration(track.path)
    return AudioAnalysis(
        duration=duration,
        bin_seconds=(duration / bins) if bins else 0.0,
        mix=mix,
        stems=stems,
    )


def ffprobe_duration(audio_path: str) -> float:
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


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    index = min(len(values) - 1, max(0, int(round((pct / 100.0) * (len(values) - 1)))))
    return values[index]


def window_score(envelope: list[float], timestamp: float, analysis: AudioAnalysis) -> float:
    if not envelope or analysis.duration <= 0:
        return 0.0
    center = int((timestamp / analysis.duration) * len(envelope))
    radius = max(1, int(2.0 / max(analysis.bin_seconds, 0.001)))
    lo = max(0, center - radius)
    hi = min(len(envelope), center + radius + 1)
    if hi <= lo:
        return 0.0
    return sum(envelope[lo:hi]) / (hi - lo)


def energy_before_after(
    envelope: list[float], timestamp: float, analysis: AudioAnalysis
) -> tuple[float, float]:
    if not envelope or analysis.duration <= 0:
        return 0.0, 0.0
    center = int((timestamp / analysis.duration) * len(envelope))
    span = max(1, int(4.0 / max(analysis.bin_seconds, 0.001)))
    pre = envelope[max(0, center - span) : center]
    post = envelope[center : min(len(envelope), center + span)]
    before = sum(pre) / len(pre) if pre else 0.0
    after = sum(post) / len(post) if post else 0.0
    return before, after


