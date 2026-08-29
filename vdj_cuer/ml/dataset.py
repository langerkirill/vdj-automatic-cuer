"""Export bar labels from cued tracks and upsert the on-disk training set."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterable

from .features import rows_for_track
from .labels import (
    apply_vocal_onset_negatives,
    attach_labels,
    has_training_cue_points,
    is_trainable_track,
    is_training_source_path,
    label_bars,
    path_leaf,
)
from .model import DEFAULT_ARTIFACT, save_cue_bar_model, train_cue_bar_model

AUDIO_EXT = {".mp3", ".flac", ".m4a", ".wav", ".aiff", ".aif", ".ogg", ".opus"}
DEFAULT_LABELS = (
    Path.home() / "Music" / "DJ" / "Music" / "Cues" / ".cache" / "ml-labels" / "bars.jsonl"
)

ProgressFn = Callable[[str], None]


def ensure_ui_on_path() -> Path:
    ui_dir = Path(__file__).resolve().parents[2] / "ui"
    if str(ui_dir) not in sys.path:
        sys.path.insert(0, str(ui_dir))
    return ui_dir


def training_audio_roots() -> list[Path]:
    """Cues Sorted + Ready For Sort + Add Cues."""
    ensure_ui_on_path()
    from sorter.config import ADD_CUES, CUES_SORTED, READY_FOR_SORT

    return [CUES_SORTED, READY_FOR_SORT, ADD_CUES]


def collect_audio(roots: Iterable[Path]) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        files.extend(
            p
            for p in root.rglob("*")
            if p.is_file() and p.suffix.lower() in AUDIO_EXT
        )
    return sorted({p.resolve() for p in files})


def collect_training_audio(roots: Iterable[Path] | None = None) -> list[Path]:
    candidates = collect_audio(list(roots) if roots is not None else training_audio_roots())
    return [p for p in candidates if is_training_source_path(str(p))]


def _summary_field(summary: Any, name: str, default: Any = None) -> Any:
    if summary is None:
        return default
    if isinstance(summary, dict):
        return summary.get(name, default)
    return getattr(summary, name, default)


def track_identity(path: str) -> str:
    """Stable key so Add Cues → Ready / Cues Sorted moves replace, not duplicate."""
    return path_leaf(path).lower()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _same_track(row: dict[str, Any], track_id: str) -> bool:
    rid = str(row.get("track_id") or "")
    if not rid:
        return False
    if rid == track_id:
        return True
    return track_identity(rid) == track_identity(track_id)


def upsert_track_rows(
    labels_path: Path, track_id: str, new_rows: list[dict[str, Any]]
) -> int:
    """Replace any existing rows for this file (path or basename) and append new ones."""
    kept = [row for row in load_jsonl(labels_path) if not _same_track(row, track_id)]
    write_jsonl(labels_path, kept + list(new_rows))
    return len(new_rows)


def drop_track_rows(labels_path: Path, track_id: str) -> int:
    """Remove previously ingested rows for this file. Returns how many were dropped."""
    if not labels_path.is_file():
        return 0
    existing = load_jsonl(labels_path)
    kept = [row for row in existing if not _same_track(row, track_id)]
    dropped = len(existing) - len(kept)
    if dropped:
        write_jsonl(labels_path, kept)
    return dropped


def labeled_rows_for_track(
    path: str | Path,
    summary: Any,
    *,
    stem_helper: Any = None,
) -> list[dict[str, Any]]:
    """Feature + label rows for one cued track. Empty if it should not train."""
    audio = Path(path)
    track_id = str(audio)
    if not is_trainable_track(track_id, summary):
        return []
    if not audio.is_file():
        return []

    from vdj_cuer.stem_evidence import StemProfile, load_stem_profiles
    from vdj_cuer.stems import StemMixin

    class _StemIO(StemMixin):
        pass

    helper = stem_helper if stem_helper is not None else _StemIO()
    bpm = float(_summary_field(summary, "bpm") or 0.0)
    duration = float(_summary_field(summary, "song_length") or 0.0)
    scan_phase = _summary_field(summary, "scan_phase")
    beatgrid_pos = _summary_field(summary, "beatgrid_pos")
    offset = float(scan_phase if scan_phase is not None else (beatgrid_pos or 0.0))
    raw_points = list(_summary_field(summary, "points") or [])
    points = [p.to_dict() if hasattr(p, "to_dict") else p for p in raw_points]

    try:
        mix = StemProfile.decode(str(audio))
    except Exception:
        return []
    if duration <= 0:
        duration = mix.frame_seconds * len(mix.frames)
    profiles = {"mix": mix}
    stems_path = Path(f"{audio}.vdjstems")
    if stems_path.is_file():
        try:
            with tempfile.TemporaryDirectory(prefix="ml-stems-") as tmp:
                extracted = helper._extract_vdj_stems(str(stems_path), tmp)
                if extracted:
                    profiles.update(load_stem_profiles(extracted))
        except Exception:
            pass
    feature_rows = rows_for_track(
        profiles,
        duration=duration,
        bpm=bpm,
        offset=offset,
        audio_path=str(audio),
    )
    labeled = apply_vocal_onset_negatives(
        attach_labels(
            feature_rows,
            label_bars(points, duration=duration, bpm=bpm, offset=offset),
        ),
        profiles,
        bpm=bpm,
    )
    if not labeled:
        return []
    has_stems = int(stems_path.is_file())
    return [
        {
            **row,
            "track_id": track_id,
            "duration": duration,
            "offset": offset,
            "has_stems": has_stems,
        }
        for row in labeled
    ]


def export_training_labels(
    dest: Path,
    roots: Iterable[Path] | None = None,
    *,
    summaries: dict[str, Any] | None = None,
    progress: ProgressFn | None = None,
) -> dict[str, int]:
    """Full rewrite of bars.jsonl from current cued pipeline folders."""
    ensure_ui_on_path()
    from sorter.relocate import summarize_cues_for_paths

    files = collect_training_audio(roots)
    cue_index = summaries if summaries is not None else summarize_cues_for_paths(
        [str(p) for p in files]
    )
    written = 0
    skipped = 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8") as handle:
        for index, path in enumerate(files, start=1):
            if progress and (index == 1 or index % 25 == 0 or index == len(files)):
                progress(f"Export {index}/{len(files)} · {path.name}")
            summary = cue_index.get(str(path))
            if not has_training_cue_points(summary):
                skipped += 1
                continue
            rows = labeled_rows_for_track(path, summary)
            if not rows:
                skipped += 1
                continue
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                written += 1
    return {"written": written, "skipped": skipped, "tracks": len(files)}


def train_from_labels_file(
    labels_path: Path,
    artifact_path: Path | None = None,
    *,
    seed: int = 0,
) -> dict[str, Any]:
    """Fit cue/loop heads on every labeled row and write the model artifact.

    Incremental ingest uses this so a newly cued track is in the next AutoCue
    model. ``vdj_cuer.ml train`` still reports holdout metrics separately.
    """
    rows = load_jsonl(labels_path)
    if not rows:
        raise ValueError(f"No label rows in {labels_path}")
    track_ids = {str(row.get("track_id") or "") for row in rows}
    track_ids.discard("")
    if not track_ids:
        raise ValueError(f"No track_id rows in {labels_path}")
    model = train_cue_bar_model(rows, seed=int(seed))
    metrics: dict[str, Any] = {
        "n_rows": len(rows),
        "n_tracks": len(track_ids),
        "seed": int(seed),
    }
    dest = save_cue_bar_model(
        model, Path(artifact_path) if artifact_path else DEFAULT_ARTIFACT, metrics=metrics
    )
    metrics["artifact"] = str(dest)
    return metrics
