"""Incremental label ingest + retrain after a successful cue pass."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from vdj_cuer.ml.dataset import (
    collect_training_audio,
    drop_track_rows,
    export_training_labels,
    load_jsonl,
    track_identity,
    upsert_track_rows,
    write_jsonl,
)
from vdj_cuer.ml.features import FEATURE_NAMES
from vdj_cuer.ml.ingest import drop_cued_track, ingest_cued_track
from vdj_cuer.ml.model import load_cue_bar_model


def _row(track: str, vocal_dprev: float, is_cue: int) -> dict:
    base = {name: 0.0 for name in FEATURE_NAMES}
    base["timestamp"] = vocal_dprev
    base["vocal_dprev"] = vocal_dprev
    base["track_id"] = track
    base["is_cue"] = is_cue
    base["is_loop_start"] = 0
    return base


def _cued_summary(*, cues: int = 3, loops: int = 2, bpm: float = 120.0) -> SimpleNamespace:
    points = [{"kind": "cue", "pos": float(i * 8)} for i in range(cues)]
    points.extend({"kind": "loop", "pos": float(i * 16)} for i in range(loops))
    return SimpleNamespace(
        in_database=True,
        bpm=bpm,
        cue_count=cues,
        loop_count=loops,
        song_length=64.0,
        scan_phase=0.0,
        beatgrid_pos=0.0,
        points=points,
    )


class UpsertLabelTests(unittest.TestCase):
    def test_upsert_replaces_same_track_and_moved_basename(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "bars.jsonl"
            add_path = "/music/Cues/Add Cues/song.flac"
            ready_path = "/music/Cues/Ready For Sort/song.flac"
            other = "/music/Cues/Cues Sorted/Pop/other.flac"
            write_jsonl(
                dest,
                [
                    {"track_id": add_path, "timestamp": 0.0, "is_cue": 1},
                    {"track_id": other, "timestamp": 0.0, "is_cue": 0},
                ],
            )
            upsert_track_rows(
                dest,
                ready_path,
                [{"track_id": ready_path, "timestamp": 8.0, "is_cue": 1}],
            )
            rows = load_jsonl(dest)
            ids = [row["track_id"] for row in rows]
            self.assertEqual(ids.count(ready_path), 1)
            self.assertNotIn(add_path, ids)
            self.assertIn(other, ids)
            self.assertEqual(track_identity(add_path), track_identity(ready_path))

    def test_drop_removes_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "bars.jsonl"
            write_jsonl(
                dest,
                [
                    {"track_id": "/Cues/Add Cues/song.flac", "is_cue": 1},
                    {"track_id": "/Cues/Cues Sorted/keep.flac", "is_cue": 0},
                ],
            )
            dropped = drop_track_rows(dest, "/Cues/Ready For Sort/song.flac")
            self.assertEqual(dropped, 1)
            ids = [row["track_id"] for row in load_jsonl(dest)]
            self.assertEqual(ids, ["/Cues/Cues Sorted/keep.flac"])


class IngestCuedTrackTests(unittest.TestCase):
    def test_skips_library_and_uncued_add_cues(self) -> None:
        library = ingest_cued_track(
            "/Users/x/Music/DJ/Music/Zouk/Pop/track.flac",
            _cued_summary(),
            retrain=False,
        )
        self.assertFalse(library["ok"])
        self.assertEqual(library["reason"], "not_training_source")

        skip = ingest_cued_track(
            "/Users/x/Music/DJ/Music/Cues/No Cues Found/miss.flac",
            _cued_summary(),
            retrain=False,
        )
        self.assertEqual(skip["reason"], "not_training_source")

        empty = ingest_cued_track(
            "/Users/x/Music/DJ/Music/Cues/Add Cues/track.flac",
            SimpleNamespace(
                in_database=True, bpm=120.0, cue_count=0, loop_count=0, points=[]
            ),
            retrain=False,
        )
        self.assertFalse(empty["ok"])
        self.assertEqual(empty["reason"], "no_accepted_cues")

    def test_ingests_add_cues_and_retrains(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            labels = Path(tmp) / "bars.jsonl"
            artifact = Path(tmp) / "cue_bar_clf.joblib"
            add_path = "/Users/x/Music/DJ/Music/Cues/Add Cues/new.flac"
            existing = [
                _row("/Users/x/Music/DJ/Music/Cues/Cues Sorted/old.flac", 0.05, 0),
                _row("/Users/x/Music/DJ/Music/Cues/Cues Sorted/old.flac", 0.80, 1),
            ]
            write_jsonl(labels, existing)
            fake_rows = [
                _row(add_path, 0.04, 0),
                _row(add_path, 0.85, 1),
            ]
            with patch(
                "vdj_cuer.ml.ingest.labeled_rows_for_track", return_value=fake_rows
            ), patch(
                "vdj_cuer.ml.ingest.assess_autocue_match",
                return_value={"matches": False, "status": "no_proposal"},
            ):
                result = ingest_cued_track(
                    add_path,
                    _cued_summary(),
                    labels_path=labels,
                    model_path=artifact,
                    retrain=True,
                )
            self.assertTrue(result["ok"])
            self.assertEqual(result["reason"], "ingested")
            self.assertEqual(result["rows"], 2)
            self.assertTrue(result["retrained"])
            rows = load_jsonl(labels)
            self.assertEqual({row["track_id"] for row in rows}, {
                "/Users/x/Music/DJ/Music/Cues/Cues Sorted/old.flac",
                add_path,
            })
            model = load_cue_bar_model(artifact)
            self.assertIsNotNone(model)
            sidecar = json.loads(artifact.with_suffix(".json").read_text(encoding="utf-8"))
            self.assertEqual(sidecar["metrics"]["n_tracks"], 2)

    def test_ready_for_sort_replaces_add_cues_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            labels = Path(tmp) / "bars.jsonl"
            add_path = "/Users/x/Music/DJ/Music/Cues/Add Cues/song.flac"
            ready_path = "/Users/x/Music/DJ/Music/Cues/Ready For Sort/song.flac"
            write_jsonl(labels, [_row(add_path, 0.5, 1)])
            with patch(
                "vdj_cuer.ml.ingest.labeled_rows_for_track",
                return_value=[_row(ready_path, 0.6, 1)],
            ), patch(
                "vdj_cuer.ml.ingest.assess_autocue_match",
                return_value={"matches": False, "status": "mismatch"},
            ):
                result = ingest_cued_track(
                    ready_path,
                    _cued_summary(),
                    labels_path=labels,
                    retrain=False,
                )
            self.assertTrue(result["ok"])
            ids = [row["track_id"] for row in load_jsonl(labels)]
            self.assertEqual(ids, [ready_path])

    def test_skips_when_autocue_already_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            labels = Path(tmp) / "bars.jsonl"
            path = "/Users/x/Music/DJ/Music/Cues/Add Cues/song.flac"
            write_jsonl(labels, [_row(path, 0.5, 1)])
            with patch(
                "vdj_cuer.ml.ingest.labeled_rows_for_track",
                return_value=[_row(path, 0.9, 1)],
            ), patch(
                "vdj_cuer.ml.ingest.assess_autocue_match",
                return_value={"matches": True, "status": "match", "reason": "match"},
            ):
                result = ingest_cued_track(
                    path,
                    _cued_summary(),
                    labels_path=labels,
                    retrain=False,
                )
            self.assertFalse(result["ok"])
            self.assertEqual(result["reason"], "autocue_matches")
            self.assertEqual(result["dropped"], 1)
            self.assertEqual(load_jsonl(labels), [])

    def test_drop_after_empty_cues(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            labels = Path(tmp) / "bars.jsonl"
            path = "/Users/x/Music/DJ/Music/Cues/Add Cues/song.flac"
            write_jsonl(labels, [_row(path, 0.5, 1), _row("/other/keep.flac", 0.1, 0)])
            result = drop_cued_track(path, labels_path=labels, retrain=False)
            self.assertTrue(result["ok"])
            self.assertEqual(result["dropped"], 1)
            self.assertEqual(
                [row["track_id"] for row in load_jsonl(labels)],
                ["/other/keep.flac"],
            )


class IngestCliTests(unittest.TestCase):
    def test_parse_cli_path_and_drop(self) -> None:
        from vdj_cuer.ml.ingest import parse_cli

        args = parse_cli(["--path", "/tmp/song.flac", "--drop", "--no-retrain"])
        self.assertEqual(args.path, "/tmp/song.flac")
        self.assertTrue(args.drop)
        self.assertTrue(args.no_retrain)


class CollectTrainingAudioTests(unittest.TestCase):
    def test_export_walks_sorted_ready_and_add_cues(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sorted_dir = root / "Cues Sorted" / "Pop"
            ready_dir = root / "Ready For Sort"
            add_dir = root / "Add Cues" / "Inbox"
            skip_dir = root / "No Cues Found"
            lib_dir = root / "Zouk" / "Pop"
            for folder in (sorted_dir, ready_dir, add_dir, skip_dir, lib_dir):
                folder.mkdir(parents=True)
            (sorted_dir / "kept.flac").write_bytes(b"a")
            (ready_dir / "ready.flac").write_bytes(b"a")
            (add_dir / "new.flac").write_bytes(b"a")
            (skip_dir / "miss.flac").write_bytes(b"a")
            (lib_dir / "lib.flac").write_bytes(b"a")
            files = collect_training_audio([sorted_dir.parent, ready_dir, add_dir, skip_dir, lib_dir])
            names = {p.name for p in files}
            self.assertEqual(names, {"kept.flac", "ready.flac", "new.flac"})

    def test_export_skips_uncued_add_cues(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            add_dir = root / "Add Cues"
            add_dir.mkdir()
            cued = add_dir / "cued.flac"
            empty = add_dir / "empty.flac"
            cued.write_bytes(b"a")
            empty.write_bytes(b"a")
            dest = Path(tmp) / "bars.jsonl"
            summaries = {
                str(cued.resolve()): _cued_summary(),
                str(empty.resolve()): SimpleNamespace(
                    in_database=True, bpm=120.0, cue_count=0, loop_count=0, points=[]
                ),
            }
            fake = [_row(str(cued.resolve()), 0.8, 1)]
            with patch(
                "vdj_cuer.ml.dataset.labeled_rows_for_track", return_value=fake
            ):
                stats = export_training_labels(
                    dest, roots=[add_dir], summaries=summaries
                )
            self.assertEqual(stats["written"], 1)
            self.assertEqual(stats["skipped"], 1)
            rows = load_jsonl(dest)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["track_id"], str(cued.resolve()))


if __name__ == "__main__":
    unittest.main()
