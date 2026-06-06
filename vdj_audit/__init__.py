"""Visual and stem-aware cue audit package."""

from .audio import (
    analyze_audio,
    decode_envelope,
    energy_before_after,
    ffprobe_duration,
    percentile,
    probe_stems,
    window_score,
)
from .cli import expand_audio_paths, main
from .common import (
    AudioAnalysis,
    COLOR_HEX,
    CUE_COLOR_VALUES,
    DEFAULT_DATABASE,
    STEM_HEX,
    STEM_NAMES,
    CueIssue,
    CueObservation,
    Poi,
    Track,
)
from .database import load_tracks, parse_database, preprocess_xml
from .inspection import (
    audit_track,
    combined_stem_lanes,
    energy_shape_issue,
    expected_color,
    infer_elements_from_activity,
    inspect_track,
    name_element_issue,
    normalize_activity,
    suggested_section_label,
)
from .reports import render_svg, safe_slug, waveform_path, write_all_cues, write_reports

__all__ = [name for name in globals() if not name.startswith("_")]
