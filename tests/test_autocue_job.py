"""Isolated AutoCue worker: analyze+apply without living in the UI process."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from vdj_cuer.autocue_job import (
    analyze_audio_until_data,
    parse_args,
    run_one,
    write_result,
)


class ParseArgsTests(unittest.TestCase):
    def test_required_audio_and_result(self) -> None:
        args = parse_args(
            [
                "--audio",
                "/tmp/song.flac",
                "--result",
                "/tmp/out.json",
                "--write-scope",
                "loops",
                "--dry-run",
                "--stems-skipped",
                "--grid-confirmed",
                "--model",
                "gemini-pro",
                "--database",
                "/tmp/database.xml",
            ]
        )
        self.assertEqual(args.audio, "/tmp/song.flac")
        self.assertEqual(args.result, "/tmp/out.json")
        self.assertEqual(args.write_scope, "loops")
        self.assertTrue(args.dry_run)
        self.assertTrue(args.stems_skipped)
        self.assertTrue(args.grid_confirmed)
        self.assertEqual(args.model, "gemini-pro")
        self.assertEqual(args.database, "/tmp/database.xml")


class AnalyzeUntilDataTests(unittest.TestCase):
    def test_retries_then_returns(self) -> None:
        calls = {"n": 0}

        def analyze(_path: str):
            calls["n"] += 1
            if calls["n"] < 2:
                return None
            return {"measure_changes": [{"timestamp": 1.0}]}

        sleeps: list[float] = []
        out = analyze_audio_until_data(
            analyze,
            "/tmp/song.mp3",
            attempts=3,
            sleep_fn=sleeps.append,
        )
        self.assertEqual(calls["n"], 2)
        self.assertEqual(out["measure_changes"][0]["timestamp"], 1.0)
        self.assertEqual(len(sleeps), 1)


class WriteResultTests(unittest.TestCase):
    def test_writes_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "result.json"
            write_result(dest, {"ok": True, "warn": ""})
            payload = json.loads(dest.read_text(encoding="utf-8"))
            self.assertTrue(payload["ok"])


class RunOneTests(unittest.TestCase):
    def test_applies_analysis_and_reports_ok(self) -> None:
        analysis = {
            "measure_changes": [{"timestamp": 1.0}],
            "loop_segments": [{}, {}],
            "song_structure": {"bpm": 120},
        }
        cuer = MagicMock()
        cuer.model_name = "gemini"
        cuer.analyze_audio_with_gemini.return_value = analysis
        cuer.get_song_length.return_value = 200.0
        cuer.get_song_bpm_from_database.return_value = 120.0
        cuer._postprocess_loop_segments.side_effect = lambda data, *_a, **_k: data
        cuer._apply_cues_to_database.return_value = True
        cuer.backup_database.return_value = "/tmp/backup.xml"

        with patch("vdj_cuer.autocue_job._build_cuer", return_value=cuer), patch(
            "vdj_cuer.autocue_job.load_gemini_api_key"
        ), patch(
            "vdj_cuer.autocue_job.apply_compute_thread_limits"
        ), tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "song.flac"
            audio.write_bytes(b"x")
            stems = Path(f"{audio}.vdjstems")
            stems.write_bytes(b"stems")
            result = run_one(
                str(audio),
                database_path=str(Path(tmp) / "database.xml"),
                write_scope="all",
                dry_run=False,
            )
        self.assertTrue(result["ok"])
        self.assertFalse(result["analysis_empty"])
        self.assertEqual(result["analysis_cues"], 1)
        self.assertEqual(result["analysis_loops"], 2)
        cuer._apply_cues_to_database.assert_called_once()

    def test_empty_analysis_is_not_ok(self) -> None:
        cuer = MagicMock()
        cuer.model_name = "gemini"
        cuer.analyze_audio_with_gemini.return_value = None
        with patch("vdj_cuer.autocue_job._build_cuer", return_value=cuer), patch(
            "vdj_cuer.autocue_job.load_gemini_api_key"
        ), patch(
            "vdj_cuer.autocue_job.apply_compute_thread_limits"
        ), patch(
            "vdj_cuer.autocue_job.analyze_audio_until_data", return_value=None
        ), tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "song.flac"
            audio.write_bytes(b"x")
            Path(f"{audio}.vdjstems").write_bytes(b"stems")
            result = run_one(str(audio), database_path=str(Path(tmp) / "database.xml"))
        self.assertFalse(result["ok"])
        self.assertTrue(result["analysis_empty"])


if __name__ == "__main__":
    unittest.main()
