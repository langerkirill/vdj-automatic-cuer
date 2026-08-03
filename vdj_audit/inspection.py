"""Cue issue inference for visual audits."""

from .audio import energy_before_after, percentile, window_score
from .common import *

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
    cue_name: str,
    before_energy: float,
    after_energy: float,
    elements: Optional[set[str]] = None,
) -> Optional[str]:
    name = cue_name.lower()
    if "drop" in name and after_energy < before_energy * 1.15:
        suggestion = suggested_section_label(elements or set(), "Groove")
        return (
            "Drop cue does not show a clear visible energy rise; "
            f"suggest '{suggestion}' or move to a true drop"
        )
    if "breakdown" in name and after_energy > before_energy * 0.9:
        suggestion = suggested_section_label(elements or set(), "Groove")
        return (
            "Breakdown cue does not show a clear visible energy drop; "
            f"suggest '{suggestion}' or move to a true breakdown"
        )
    return None


def suggested_section_label(elements: set[str], fallback: str) -> str:
    has_drums = "drums" in elements
    has_vocals = "vocals" in elements
    has_melody = bool(elements.intersection({"bass", "synth", "piano", "guitar"}))

    if has_vocals and has_drums:
        return "Vocal Mix"
    if has_vocals:
        return "Vocal Break"
    if has_drums and has_melody:
        return "Rhythm Section"
    if has_melody:
        return "Synth Section"
    if has_drums:
        return "Drum Section"
    return fallback


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


def beat_duration_seconds(track: Track) -> Optional[float]:
    if track.scan_bpm and 60 <= track.scan_bpm <= 200:
        return 60.0 / track.scan_bpm
    return None


def grid_alignment_issues(track: Track) -> list[CueIssue]:
    """Flag cues/loops that are not on the stored VirtualDJ beat/bar grid."""
    issues: list[CueIssue] = []
    beat = beat_duration_seconds(track)
    if beat is None:
        return issues

    # Prefer beatgrid POI; fall back to Scan Phase (VDJ's stored '1'). Using 0.0
    # when Phase is non-zero falsely flags phase-aligned cues (No Rhyme / Hold Me).
    if track.beatgrid is not None:
        offset = track.beatgrid
    elif track.scan_phase is not None:
        offset = track.scan_phase
    else:
        offset = 0.0
    bar = beat * 4.0
    track_name = Path(track.path).name

    if (
        track.beatgrid is not None
        and track.scan_phase is not None
        and abs(track.beatgrid - track.scan_phase) > 0.02
    ):
        issues.append(
            CueIssue(
                track=track_name,
                cue="beatgrid",
                timestamp=track.beatgrid,
                severity="high",
                issue=(
                    f"Scan Phase ({track.scan_phase:.3f}s) disagrees with beatgrid "
                    f"({track.beatgrid:.3f}s); VirtualDJ may draw the wrong '1'"
                ),
                cue_color="unknown",
                expected_color="unknown",
                elements="grid",
            )
        )

    for poi in track.pois:
        if poi.poi_type == "cue":
            # Cues should land on bar downbeats (beat 1).
            within = (poi.pos - offset) % bar
            distance = min(within, bar - within)
            if distance > beat * 0.12:  # > ~12% of a beat off the bar line
                beat_in_bar = within / beat + 1.0
                issues.append(
                    CueIssue(
                        track=track_name,
                        cue=poi.name,
                        timestamp=poi.pos,
                        severity="high",
                        issue=(
                            f"Cue is off the bar '1' (≈ beat {beat_in_bar:.2f} of the bar, "
                            f"{distance * 1000:.0f}ms from downbeat)"
                        ),
                        cue_color=poi.color_name,
                        expected_color=poi.color_name,
                        elements="grid",
                    )
                )
        elif poi.poi_type == "loop":
            within = (poi.pos - offset) % beat
            distance = min(within, beat - within)
            if distance > beat * 0.12:
                issues.append(
                    CueIssue(
                        track=track_name,
                        cue=poi.name,
                        timestamp=poi.pos,
                        severity="medium",
                        issue=(
                            f"Loop start is off the beat grid "
                            f"({distance * 1000:.0f}ms from nearest beat)"
                        ),
                        cue_color=poi.color_name,
                        expected_color=poi.color_name,
                        elements="grid",
                    )
                )
    return issues


def inspect_track(track: Track, analysis: AudioAnalysis) -> tuple[list[CueObservation], list[CueIssue]]:
    observations = []
    issues = []
    stem_envelopes = analysis.stems
    has_stem_evidence = bool(stem_envelopes)
    issues.extend(grid_alignment_issues(track))

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

        shape_issue = energy_shape_issue(
            poi.name, before_energy, after_energy, elements
        )
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
