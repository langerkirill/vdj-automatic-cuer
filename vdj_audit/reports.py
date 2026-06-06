"""Audit report writers and SVG rendering."""

from .audio import analyze_audio
from .common import *
from .inspection import audit_track, combined_stem_lanes, inspect_track

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
