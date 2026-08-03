"""Undo sort / promote using relocate primitives."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sorter import action_log as log_mod
from sorter import relocate as relocate_mod
from sorter import undo as undo_mod


def sample_db(path: str) -> bytes:
    return (
        "<VirtualDJ_Database>\r\n"
        f'<Song FilePath="{path}" Flag="1">\r\n'
        '  <Tags Author="A" Title="T" />\r\n'
        '  <Scan Bpm="0.5" Phase="0.1" />\r\n'
        '  <Poi Pos="0.1" Type="beatgrid" />\r\n'
        '  <Poi Name="Intro" Pos="0.1" Num="1" Color="4278190335" Type="cue" />\r\n'
        "</Song>\r\n"
        "</VirtualDJ_Database>\r\n"
    ).encode("utf-8")


class UndoTests(unittest.TestCase):
    def test_undo_promote_moves_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            add = root / "Add Cues" / "Batch"
            ready = root / "Ready for Sort"
            add.mkdir(parents=True)
            ready.mkdir()
            src = add / "track.flac"
            src.write_bytes(b"audio")
            db = root / "database.xml"
            db.write_bytes(sample_db(str(src.resolve())))
            log_file = root / "actions.jsonl"

            with patch.object(relocate_mod, "ADD_CUES", root / "Add Cues"), patch.object(
                relocate_mod, "READY_FOR_SORT", ready
            ), patch.object(relocate_mod, "CUES_ROOT", root), patch.object(
                relocate_mod, "VDJ_DATABASE", db
            ), patch.object(
                relocate_mod, "CUE_STAGES", {"ready_for_sort": ready}
            ), patch(
                "sorter.relocate.is_virtualdj_running", return_value=False
            ):
                result = relocate_mod.promote_add_cues_track(
                    src,
                    destination_stage="ready_for_sort",
                    database_path=db,
                    create_backup=False,
                    require_cued=True,
                )

            dest = Path(result.dest_path)
            self.assertTrue(dest.is_file())
            self.assertFalse(src.exists())

            record = log_mod.append_action(
                "promote",
                source_path=str(src),
                dest_path=str(dest),
                name=src.name,
                log_file=log_file,
            )

            with patch.object(undo_mod, "read_actions", return_value=[record]), patch.object(
                undo_mod, "append_action"
            ) as mock_append, patch.object(undo_mod, "VDJ_DATABASE", db), patch.object(
                undo_mod, "ADD_CUES", root / "Add Cues"
            ), patch.object(undo_mod, "READY_FOR_SORT", ready), patch.object(
                undo_mod, "CUES_ROOT", root
            ), patch.object(
                undo_mod, "LIBRARIES", {"House": root / "House", "Zouk": root / "Zouk"}
            ), patch(
                "sorter.relocate.is_virtualdj_running", return_value=False
            ), patch.object(relocate_mod, "VDJ_DATABASE", db):
                out = undo_mod.undo_action(record["id"], create_backup=False)

            self.assertTrue(src.is_file())
            self.assertFalse(dest.exists())
            self.assertTrue(out["ok"])
            mock_append.assert_called_once()
            self.assertEqual(mock_append.call_args.kwargs.get("details", {}).get("original_id"), record["id"])

    def test_already_undone_raises(self):
        original = {
            "id": "abc123",
            "action": "sort",
            "success": True,
            "source_path": "/a",
            "dest_path": "/b",
        }
        undo_row = {
            "id": "und1",
            "action": "undo",
            "ts": "t",
            "details": {"original_id": "abc123"},
        }
        with patch.object(undo_mod, "find_action", return_value=original), patch.object(
            undo_mod, "already_undone", return_value=undo_row
        ):
            with self.assertRaises(ValueError):
                undo_mod.undo_action("abc123")


if __name__ == "__main__":
    unittest.main()
