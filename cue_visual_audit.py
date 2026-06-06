#!/usr/bin/env python3
"""Visual and stem-aware audit for VirtualDJ cue placement."""

from __future__ import annotations

import argparse
import html
import json
import math
import os
import re
import struct
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


DEFAULT_DATABASE = (
    Path.home() / "Library" / "Application Support" / "VirtualDJ" / "database.xml"
)
CUE_COLOR_VALUES = {
    "4278190335": "blue",
    "4278255360": "green",
    "4288020735": "purple",
    "4294967040": "yellow",
    "4294934272": "orange",
}
COLOR_HEX = {
    "blue": "#1d4ed8",
    "green": "#16a34a",
    "purple": "#9333ea",
    "yellow": "#facc15",
    "orange": "#f97316",
    "unknown": "#9ca3af",
}
STEM_HEX = {
    "drums": "#ef4444",
    "vocal": "#22c55e",
    "bass": "#06b6d4",
    "instruments": "#3b82f6",
    "mix": "#cbd5e1",
}
STEM_NAMES = ("vocal", "hihat", "bass", "instruments", "kick")


@dataclass
class Poi:
    name: str
    pos: float
    poi_type: str
    color_value: str
    color_name: str
    size: str = ""
    slot: str = ""


@dataclass
class Track:
    path: str
    title: str
    artist: str
    length: float
    pois: list[Poi]
    beatgrid: Optional[float] = None


@dataclass
class CueIssue:
    track: str
    cue: str
    timestamp: float
    severity: str
    issue: str
    cue_color: str
    expected_color: str
    elements: str


@dataclass
class CueObservation:
    track: str
    cue: str
    timestamp: float
    cue_type: str
    cue_color: str
    expected_color: str
    elements: str
    before_energy: float
    after_energy: float
    issues: list[str] = field(default_factory=list)


@dataclass
class AudioAnalysis:
    duration: float
    bin_seconds: float
    mix: list[float]
    stems: dict[str, list[float]] = field(default_factory=dict)


def preprocess_xml(xml_content: str) -> str:
    xml_content = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", xml_content)
    xml_content = re.sub(r"(</[^>]+>)\s*\1+", r"\1", xml_content)
    xml_content = re.sub(
        r"(</VirtualDJ_Database>)\s*</VirtualDJ_Database>",
        r"\1",
        xml_content,
    )
    if "</VirtualDJ_Database>" in xml_content:
        xml_content = (
            xml_content.split("</VirtualDJ_Database>")[0]
            + "</VirtualDJ_Database>"
        )
    return xml_content


def parse_database(database_path: Path) -> ET.Element:
    with database_path.open("r", encoding="utf-8") as handle:
        return ET.fromstring(preprocess_xml(handle.read()))


def load_tracks(database_path: Path, audio_paths: list[str]) -> list[Track]:
    root = parse_database(database_path)
    songs = {song.get("FilePath", ""): song for song in root.findall("Song")}
    tracks = []

    for audio_path in audio_paths:
        song = songs.get(audio_path)
        if song is None:
            continue

        tags = song.find("Tags")
        infos = song.find("Infos")
        title = tags.get("Title", "") if tags is not None else ""
        artist = tags.get("Author", "") if tags is not None else ""
        length = float(infos.get("SongLength", "0")) if infos is not None else 0.0
        beatgrid = None
        pois = []

        for poi in song.findall("Poi"):
            if poi.get("Type") == "beatgrid":
                try:
                    beatgrid = float(poi.get("Pos", "0"))
                except ValueError:
                    beatgrid = None
                continue
            if poi.get("Type") not in {"cue", "loop"} or poi.get("Num", "0") == "0":
                continue

            color_value = poi.get("Color", "")
            pois.append(
                Poi(
                    name=poi.get("Name", ""),
                    pos=float(poi.get("Pos", "0") or 0),
                    poi_type=poi.get("Type", ""),
                    color_value=color_value,
                    color_name=CUE_COLOR_VALUES.get(color_value, "unknown"),
                    size=poi.get("Size", ""),
                    slot=poi.get("Slot", ""),
                )
            )

        pois.sort(key=lambda item: item.pos)
        tracks.append(
            Track(
                path=audio_path,
                title=title,
                artist=artist,
                length=length,
                pois=pois,
                beatgrid=beatgrid,
            )
        )

    return tracks


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


def infer_elements_from_activity(activity: dict[str, float]) -> set[str]:
    elements = set()
    if max(activity.get("kick", 0.0), activity.get("hihat", 0.0)) >= 0.18:
        elements.add("drums")
    if activity.get("vocal", 0.0) >= 0.18:
        elements.add("vocals")
    if activity.get("bass", 0.0) >= 0.18:
        elements.add("bass")
    if activity.get("instruments", 0.0) >= 0.18:
        elements.add("synth")
    return elements


def expected_color(elements: set[str]) -> str:
    has_drums = "drums" in elements
    has_vocals = "vocals" in elements
    has_melody = bool(elements.intersection({"bass", "synth", "piano", "guitar"}))

    if has_vocals and has_drums:
        return "yellow"
    if has_vocals and not has_drums:
        return "orange"
    if has_drums and has_melody:
        return "green"
    if has_drums:
        return "purple"
    if has_melody:
        return "blue"
    return "unknown"


def name_element_issue(
    cue_name: str, elements: set[str], timestamp: float, song_length: float
) -> Optional[str]:
    name = cue_name.lower()
    has_drums = "drums" in elements
    has_vocals = "vocals" in elements
    non_drum = bool(elements.difference({"drums"}))

    if "drum" in name and non_drum:
        return "Name says drums-only, but non-drum elements are active"
    if "vocal" in name and not has_vocals:
        return "Name says vocal, but vocal stem is not active"
    if ("melod" in name or "synth" in name or "instrumental" in name) and has_vocals:
        return "Name suggests instrumental/melodic, but vocals are active"
    if "outro" in name and song_length and timestamp < song_length * 0.65:
        return "Name says outro, but cue is early in the track"
    if "intro" in name and song_length and timestamp > song_length * 0.25:
        return "Name says intro, but cue is late in the track"
    if "drop" in name and not has_drums:
        return "Name says drop, but drums are not active"
    return None


def energy_shape_issue(
    cue_name: str, before_energy: float, after_energy: float
) -> Optional[str]:
    name = cue_name.lower()
    if "drop" in name and after_energy < before_energy * 1.15:
        return "Drop cue does not show a clear visible energy rise"
    if "breakdown" in name and after_energy > before_energy * 0.9:
        return "Breakdown cue does not show a clear visible energy drop"
    return None


def normalize_activity(raw_scores: dict[str, float], stem_envelopes: dict[str, list[float]]) -> dict[str, float]:
    normalized = {}
    for stem_name, score in raw_scores.items():
        reference = percentile(stem_envelopes.get(stem_name, []), 95)
        if reference <= 0.001:
            normalized[stem_name] = 0.0
        else:
            normalized[stem_name] = min(1.0, score / reference)
    return normalized


def combined_stem_lanes(stems: dict[str, list[float]]) -> dict[str, list[float]]:
    lanes = {}
    if "kick" in stems or "hihat" in stems:
        kick = stems.get("kick", [])
        hihat = stems.get("hihat", [])
        length = max(len(kick), len(hihat))
        lanes["drums"] = [
            max(kick[i] if i < len(kick) else 0.0, hihat[i] if i < len(hihat) else 0.0)
            for i in range(length)
        ]
    if "vocal" in stems:
        lanes["vocal"] = stems["vocal"]
    if "bass" in stems:
        lanes["bass"] = stems["bass"]
    if "instruments" in stems:
        lanes["instruments"] = stems["instruments"]
    return lanes


def inspect_track(track: Track, analysis: AudioAnalysis) -> tuple[list[CueObservation], list[CueIssue]]:
    observations = []
    issues = []
    stem_envelopes = analysis.stems
    has_stem_evidence = bool(stem_envelopes)

    for poi in track.pois:
        raw_scores = {
            stem_name: window_score(envelope, poi.pos, analysis)
            for stem_name, envelope in stem_envelopes.items()
        }
        activity = normalize_activity(raw_scores, stem_envelopes)
        elements = infer_elements_from_activity(activity)
        expected = expected_color(elements)
        element_text = ",".join(sorted(elements)) or "none"
        before_energy, after_energy = energy_before_after(analysis.mix, poi.pos, analysis)
        cue_issues = []

        if (
            has_stem_evidence
            and expected != "unknown"
            and poi.color_name != "unknown"
            and poi.color_name != expected
        ):
            issue_text = f"Color is {poi.color_name}, expected {expected} from stem activity"
            cue_issues.append(issue_text)
            issues.append(
                CueIssue(
                    track=Path(track.path).name,
                    cue=poi.name,
                    timestamp=poi.pos,
                    severity="high",
                    issue=issue_text,
                    cue_color=poi.color_name,
                    expected_color=expected,
                    elements=element_text,
                )
            )

        name_issue = (
            name_element_issue(poi.name, elements, poi.pos, track.length)
            if has_stem_evidence
            else None
        )
        if name_issue:
            cue_issues.append(name_issue)
            issues.append(
                CueIssue(
                    track=Path(track.path).name,
                    cue=poi.name,
                    timestamp=poi.pos,
                    severity="medium",
                    issue=name_issue,
                    cue_color=poi.color_name,
                    expected_color=expected,
                    elements=element_text,
                )
            )

        shape_issue = energy_shape_issue(poi.name, before_energy, after_energy)
        if shape_issue:
            cue_issues.append(shape_issue)
            issues.append(
                CueIssue(
                    track=Path(track.path).name,
                    cue=poi.name,
                    timestamp=poi.pos,
                    severity="review",
                    issue=shape_issue,
                    cue_color=poi.color_name,
                    expected_color=expected,
                    elements=element_text,
                )
            )

        observations.append(
            CueObservation(
                track=Path(track.path).name,
                cue=poi.name,
                timestamp=poi.pos,
                cue_type=poi.poi_type,
                cue_color=poi.color_name,
                expected_color=expected if has_stem_evidence else "unknown",
                elements=element_text if has_stem_evidence else "no-stems",
                before_energy=before_energy,
                after_energy=after_energy,
                issues=cue_issues,
            )
        )

    return observations, issues


def audit_track(track: Track, analysis: AudioAnalysis) -> list[CueIssue]:
    return inspect_track(track, analysis)[1]


def write_all_cues(observations: list[CueObservation], output_path: Path) -> None:
    with output_path.open("w", encoding="utf-8") as handle:
        handle.write(
            "track\tcue\ttimestamp\ttype\tcue_color\texpected_color\t"
            "elements\tbefore_energy\tafter_energy\tissues\n"
        )
        for observation in observations:
            handle.write(
                f"{observation.track}\t{observation.cue}\t"
                f"{observation.timestamp:.3f}\t{observation.cue_type}\t"
                f"{observation.cue_color}\t{observation.expected_color}\t"
                f"{observation.elements}\t{observation.before_energy:.4f}\t"
                f"{observation.after_energy:.4f}\t"
                f"{'; '.join(observation.issues)}\n"
            )


def waveform_path(envelope: list[float], x: int, y: int, width: int, height: int) -> str:
    if not envelope:
        return ""
    mid = y + height / 2
    points_top = []
    points_bottom = []
    for index, value in enumerate(envelope):
        px = x + (index / max(1, len(envelope) - 1)) * width
        amp = min(1.0, value) * (height / 2)
        points_top.append(f"{px:.1f},{mid - amp:.1f}")
        points_bottom.append(f"{px:.1f},{mid + amp:.1f}")
    return " ".join(points_top + list(reversed(points_bottom)))


def render_svg(track: Track, analysis: AudioAnalysis, issues: list[CueIssue], output_path: Path) -> None:
    width = 1400
    lane_h = 46
    top = 70
    lanes = [("mix", analysis.mix)] + list(combined_stem_lanes(analysis.stems).items())
    height = top + (lane_h + 12) * len(lanes) + 130
    plot_x = 130
    plot_w = width - plot_x - 30

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#0f172a"/>',
        f'<text x="24" y="34" fill="#e5e7eb" font-family="Arial" font-size="22">{html.escape(Path(track.path).name)}</text>',
        f'<text x="24" y="56" fill="#94a3b8" font-family="Arial" font-size="13">{html.escape(track.artist)} - {html.escape(track.title)}</text>',
    ]

    y = top
    for lane_name, envelope in lanes:
        color = STEM_HEX.get(lane_name, "#94a3b8")
        parts.append(f'<text x="24" y="{y + 28}" fill="{color}" font-family="Arial" font-size="14">{lane_name}</text>')
        parts.append(f'<rect x="{plot_x}" y="{y}" width="{plot_w}" height="{lane_h}" fill="#111827" stroke="#334155"/>')
        polygon = waveform_path(envelope, plot_x, y + 4, plot_w, lane_h - 8)
        parts.append(f'<polygon points="{polygon}" fill="{color}" opacity="0.78"/>')
        y += lane_h + 12

    plot_y = top
    plot_h = (lane_h + 12) * len(lanes) - 12
    for poi in track.pois:
        if analysis.duration <= 0:
            continue
        x = plot_x + (poi.pos / analysis.duration) * plot_w
        color = COLOR_HEX.get(poi.color_name, COLOR_HEX["unknown"])
        dash = ' stroke-dasharray="5 5"' if poi.poi_type == "loop" else ""
        parts.append(f'<line x1="{x:.1f}" y1="{plot_y}" x2="{x:.1f}" y2="{plot_y + plot_h}" stroke="{color}" stroke-width="2"{dash}/>')
        label = html.escape(poi.name[:24])
        parts.append(f'<text x="{x + 4:.1f}" y="{plot_y + plot_h + 18}" fill="{color}" font-family="Arial" font-size="11" transform="rotate(45 {x + 4:.1f},{plot_y + plot_h + 18})">{label}</text>')

    issue_y = height - 72
    parts.append(f'<text x="24" y="{issue_y}" fill="#e5e7eb" font-family="Arial" font-size="14">Issues: {len(issues)}</text>')
    for index, issue in enumerate(issues[:4]):
        text = html.escape(f"{issue.severity}: {issue.cue} @ {issue.timestamp:.1f}s - {issue.issue}")
        parts.append(f'<text x="24" y="{issue_y + 20 + index * 16}" fill="#fca5a5" font-family="Arial" font-size="12">{text}</text>')

    parts.append("</svg>")
    output_path.write_text("\n".join(parts), encoding="utf-8")


def write_reports(tracks: list[Track], output_dir: Path, bins: int) -> list[CueIssue]:
    output_dir.mkdir(parents=True, exist_ok=True)
    all_issues = []
    all_observations = []
    links = []

    for index, track in enumerate(tracks, 1):
        print(f"[{index}/{len(tracks)}] auditing {Path(track.path).name}", flush=True)
        analysis = analyze_audio(track, bins=bins)
        observations, issues = inspect_track(track, analysis)
        all_observations.extend(observations)
        all_issues.extend(issues)
        svg_name = f"{index:02d}-{safe_slug(Path(track.path).stem)}.svg"
        render_svg(track, analysis, issues, output_dir / svg_name)
        links.append((track, svg_name, issues))

    write_all_cues(all_observations, output_dir / "all_cues.tsv")

    issues_path = output_dir / "issues.tsv"
    with issues_path.open("w", encoding="utf-8") as handle:
        handle.write("track\tcue\ttimestamp\tseverity\tissue\tcue_color\texpected_color\telements\n")
        for issue in all_issues:
            handle.write(
                f"{issue.track}\t{issue.cue}\t{issue.timestamp:.3f}\t"
                f"{issue.severity}\t{issue.issue}\t{issue.cue_color}\t"
                f"{issue.expected_color}\t{issue.elements}\n"
            )

    index_parts = [
        "<!doctype html><meta charset='utf-8'><title>VDJ Cue Visual Audit</title>",
        "<style>body{font-family:Arial;background:#0f172a;color:#e5e7eb}a{color:#93c5fd}.bad{color:#fca5a5}</style>",
        "<h1>VDJ Cue Visual Audit</h1>",
        f"<p>Total issues: {len(all_issues)}</p>",
        "<ol>",
    ]
    for track, svg_name, issues in links:
        klass = " class='bad'" if issues else ""
        index_parts.append(
            f"<li{klass}><a href='{html.escape(svg_name)}'>{html.escape(Path(track.path).name)}</a> - {len(issues)} issues</li>"
        )
    index_parts.append("</ol>")
    (output_dir / "index.html").write_text("\n".join(index_parts), encoding="utf-8")

    return all_issues


def safe_slug(text: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", text)
    return text.strip("-")[:80] or "track"


def expand_audio_paths(paths: list[str]) -> list[str]:
    audio_exts = {".flac", ".mp3", ".m4a", ".wav", ".aac", ".ogg", ".opus"}
    expanded = []
    for raw_path in paths:
        path = Path(raw_path).expanduser()
        if path.is_dir():
            expanded.extend(
                str(item)
                for item in sorted(path.iterdir())
                if item.suffix.lower() in audio_exts
            )
        elif path.suffix.lower() in audio_exts:
            expanded.append(str(path))
    return expanded


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", help="audio files or folders to audit")
    parser.add_argument("--database", default=str(DEFAULT_DATABASE))
    parser.add_argument("--output", default="cue_visual_audit")
    parser.add_argument("--bins", type=int, default=1200)
    args = parser.parse_args()

    audio_paths = expand_audio_paths(args.paths)
    tracks = load_tracks(Path(args.database).expanduser(), audio_paths)
    issues = write_reports(tracks, Path(args.output), args.bins)
    print(f"wrote {len(tracks)} track reports to {args.output}")
    print(f"found {len(issues)} potential issues")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
