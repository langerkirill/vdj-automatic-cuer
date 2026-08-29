"""Persist AutoCue Gemini JSON so retries do not re-upload audio."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from vdj_cuer.analysis_cache import (
    analysis_is_usable,
    load_cached_analysis,
    save_cached_analysis,
)


def _write_audio(folder: Path, name: str = "song.flac", payload: bytes = b"audio") -> Path:
    audio = folder / name
    audio.write_bytes(payload)
    return audio


class AnalysisCacheTests(unittest.TestCase):
    def test_empty_analysis_is_not_usable(self) -> None:
        self.assertFalse(analysis_is_usable(None))
        self.assertFalse(analysis_is_usable({}))
        self.assertFalse(analysis_is_usable({"measure_changes": [], "loop_segments": []}))

    def test_cues_or_loops_make_analysis_usable(self) -> None:
        self.assertTrue(analysis_is_usable({"measure_changes": [{"timestamp": 1.2}]}))
        self.assertTrue(analysis_is_usable({"loop_segments": [{"start": 4.0, "length": 8}]}))

    def test_round_trip_hit_on_same_audio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = _write_audio(root)
            analysis = {
                "measure_changes": [{"timestamp": 0.47, "name": "Intro"}],
                "loop_segments": [
                    {"start": 0.47, "length": 8},
                    {"start": 16.0, "length": 8},
                ],
            }
            saved = save_cached_analysis(
                audio, analysis, model="gemini-3.5-flash-lite", cache_dir=root / "cache"
            )
            self.assertIsNotNone(saved)
            hit = load_cached_analysis(audio, cache_dir=root / "cache")
            self.assertEqual(hit["measure_changes"][0]["name"], "Intro")
            self.assertEqual(len(hit["loop_segments"]), 2)

    def test_miss_when_audio_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = _write_audio(root)
            save_cached_analysis(
                audio,
                {
                    "measure_changes": [{"timestamp": 1.0}],
                    "loop_segments": [{"start": 1.0}, {"start": 9.0}],
                },
                cache_dir=root / "cache",
            )
            audio.write_bytes(b"changed-audio")
            self.assertIsNone(load_cached_analysis(audio, cache_dir=root / "cache"))

    def test_miss_when_stems_sidecar_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = _write_audio(root)
            stems = Path(f"{audio}.vdjstems")
            stems.write_bytes(b"stems-v1")
            save_cached_analysis(
                audio,
                {
                    "measure_changes": [{"timestamp": 1.0}],
                    "loop_segments": [{"start": 1.0}, {"start": 9.0}],
                },
                cache_dir=root / "cache",
            )
            stems.write_bytes(b"stems-v2")
            self.assertIsNone(load_cached_analysis(audio, cache_dir=root / "cache"))

    def test_does_not_save_empty_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = _write_audio(root)
            self.assertIsNone(
                save_cached_analysis(audio, {"measure_changes": []}, cache_dir=root / "cache")
            )
            self.assertIsNone(load_cached_analysis(audio, cache_dir=root / "cache"))

    def test_zero_loop_cache_is_not_reused(self) -> None:
        """Stem-gate fixes must re-run; a 0-loop cache hid Make You Feel / Swimmers."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = _write_audio(root)
            save_cached_analysis(
                audio,
                {"measure_changes": [{"timestamp": 2.4, "name": "Vocal Intro"}]},
                cache_dir=root / "cache",
            )
            self.assertIsNone(load_cached_analysis(audio, cache_dir=root / "cache"))

    def test_refresh_flag_skips_hit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = _write_audio(root)
            save_cached_analysis(
                audio,
                {
                    "measure_changes": [{"timestamp": 2.0}],
                    "loop_segments": [{"start": 2.0}, {"start": 18.0}],
                },
                cache_dir=root / "cache",
            )
            self.assertIsNone(
                load_cached_analysis(audio, cache_dir=root / "cache", refresh=True)
            )

    def test_analyze_wrapper_uses_cache_before_calling_gemini(self) -> None:
        from vdj_cuer.analysis_cache import analyze_with_cache

        calls = {"n": 0}

        def analyze(_path: str):
            calls["n"] += 1
            return {
                "measure_changes": [{"timestamp": 9.0}],
                "loop_segments": [
                    {"start": 9.0, "length": 8},
                    {"start": 25.0, "length": 8},
                ],
            }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = _write_audio(root)
            first = analyze_with_cache(analyze, audio, cache_dir=root / "cache")
            second = analyze_with_cache(analyze, audio, cache_dir=root / "cache")
            self.assertEqual(first["measure_changes"][0]["timestamp"], 9.0)
            self.assertEqual(second["measure_changes"][0]["timestamp"], 9.0)
            self.assertEqual(calls["n"], 1)

    def test_analyze_wrapper_does_not_cache_empty(self) -> None:
        from vdj_cuer.analysis_cache import analyze_with_cache

        def analyze(_path: str):
            return None

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = _write_audio(root)
            out = analyze_with_cache(analyze, audio, cache_dir=root / "cache")
            self.assertIsNone(out)
            self.assertIsNone(load_cached_analysis(audio, cache_dir=root / "cache"))


class AnalysisCacheAssetTests(unittest.TestCase):
    def test_gemini_analysis_consults_cache(self) -> None:
        src = (
            Path(__file__).resolve().parents[1] / "vdj_cuer" / "gemini_analysis.py"
        ).read_text(encoding="utf-8")
        retry = (
            Path(__file__).resolve().parents[1]
            / "ui"
            / "sorter"
            / "autocue_retry.py"
        ).read_text(encoding="utf-8")
        cache_src = (
            Path(__file__).resolve().parents[1] / "vdj_cuer" / "analysis_cache.py"
        ).read_text(encoding="utf-8")
        self.assertIn("analyze_with_cache", src)
        self.assertIn("analyze_with_cache", retry)
        self.assertIn("AUTOCUE_REFRESH_ANALYSIS", cache_src)


if __name__ == "__main__":
    unittest.main()
