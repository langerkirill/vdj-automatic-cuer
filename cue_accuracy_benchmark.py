#!/usr/bin/env python3
"""Regression benchmark for stem-backed component/color assertions."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from vdj_cuer.analysis_postprocess import AnalysisPostprocessMixin
from vdj_cuer.stem_evidence import load_stem_profiles, measure_stem_evidence
from vdj_cuer.stems import StemMixin


DEFAULT_PATCH = (
    Path(__file__).resolve().parent
    / "cue_fixes"
    / "screenshots_6_6_26_manual.json"
)
COLOR_NAMES = {
    "4278190335": "blue",
    "4278255360": "green",
    "4288020735": "purple",
    "4294967040": "yellow",
    "4294934272": "orange",
}


class BenchmarkHelper(StemMixin, AnalysisPostprocessMixin):
    """Use production extraction and classification without creating an API client."""


def resolve_benchmark_audio(path: str | Path) -> Path:
    """Follow files that were sorted out of Add Cues into Cues Sorted / libraries."""
    audio = Path(path)
    if audio.is_file():
        return audio
    name = audio.name
    roots = [
        Path.home() / "Music" / "DJ" / "Music" / "Cues" / "Cues Sorted",
        Path.home() / "Music" / "DJ" / "Music" / "Zouk",
        Path.home() / "Music" / "DJ" / "Music" / "House",
    ]
    for root in roots:
        if not root.is_dir():
            continue
        hits = sorted(root.rglob(name))
        if hits:
            return hits[0]
    return audio


def run_benchmark(patch_path: Path) -> tuple[int, int, int, list[str]]:
    patch = json.loads(patch_path.read_text(encoding="utf-8"))
    tracks = [track for track in patch["tracks"] if track.get("stem_ground_truth")]
    helper = BenchmarkHelper()
    asserted = 0
    matched = 0
    abstained = 0
    errors = []

    for track in tracks:
        audio_path = resolve_benchmark_audio(track["path"])
        stems_path = Path(f"{audio_path}.vdjstems")
        if not audio_path.is_file() or not stems_path.is_file():
            # Sorted-away Add Cues paths are resolved above; skip leftovers
            # that have no stems anywhere so the color floor stays about color.
            continue

        with tempfile.TemporaryDirectory(prefix="vdj-cue-benchmark-") as temp_dir:
            stem_files = helper._extract_vdj_stems(str(stems_path), temp_dir)
            profiles = load_stem_profiles(stem_files)
            for poi in track["pois"]:
                evidence = measure_stem_evidence(
                    profiles,
                    timestamp=float(poi.get("Pos", 0.0)),
                    duration_seconds=4.0,
                    model_elements=[poi.get("Name", "")],
                    centered=True,
                    strict_drums=False,
                )
                if not evidence.elements:
                    abstained += 1
                    continue

                asserted += 1
                expected = COLOR_NAMES[str(poi["Color"])]
                predicted = helper.validate_color_assignment(evidence.elements, "green")
                if predicted == expected:
                    matched += 1
                    continue
                errors.append(
                    f"{Path(audio_path).name} | {poi.get('Name')} | "
                    f"expected={expected} predicted={predicted} "
                    f"elements={','.join(evidence.elements)}"
                )

    return asserted, matched, abstained, errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check cue component precision against stem-backed corrections."
    )
    parser.add_argument("--patch", type=Path, default=DEFAULT_PATCH)
    parser.add_argument("--min-precision", type=float, default=0.95)
    parser.add_argument("--min-coverage", type=float, default=0.95)
    args = parser.parse_args()

    asserted, matched, abstained, errors = run_benchmark(args.patch)
    total = asserted + abstained
    precision = matched / asserted if asserted else 0.0
    coverage = asserted / total if total else 0.0
    print(
        f"asserted={asserted} matched={matched} abstained={abstained} "
        f"precision={precision:.3f} coverage={coverage:.3f}"
    )
    for error in errors:
        print(f"ERROR: {error}")

    if errors or precision < args.min_precision or coverage < args.min_coverage:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
