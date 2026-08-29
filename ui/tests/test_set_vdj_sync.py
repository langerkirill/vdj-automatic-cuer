"""Stale Sets/Pajamathon FilePaths drop; whites paint from one sibling lane."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sorter import set_vdj_sync as sync


def _db(songs: list[tuple[str, str]]) -> str:
    body = []
    for path, extra in songs:
        body.append(
            f'<Song FilePath="{path}">\n'
            f"  <Tags Title=\"x\" />\n"
            f"  {extra}\n"
            f"</Song>\n"
        )
    return "<VirtualDJ_Database>\n" + "".join(body) + "</VirtualDJ_Database>\n"


class SetVdjSyncTests(unittest.TestCase):
    def test_drops_missing_pajamathon_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            sets = tmp_p / "Sets"
            live = sets / "Pajamathon" / "keep.flac"
            live.parent.mkdir(parents=True)
            live.write_bytes(b"x")
            missing = sets / "Pajamathon" / "gone.flac"
            zouk_missing = tmp_p / "Zouk" / "Bassy" / "other.flac"
            db = tmp_p / "database.xml"
            db.write_text(
                _db(
                    [
                        (str(missing), "<Infos />"),
                        (str(live), "<Infos />"),
                        (str(zouk_missing), "<Infos />"),
                    ]
                ),
                encoding="utf-8",
            )
            with (
                patch.object(sync, "SETS_ROOT", sets),
                patch.object(sync, "VDJ_DATABASE", db),
                patch.object(sync, "is_virtualdj_running", return_value=False),
                patch.object(sync, "cached_placement_indexes", return_value=({}, {})),
                patch.object(sync, "find_library_matches", return_value=[]),
                patch.object(sync, "backup_database", return_value=None),
            ):
                result = sync.sync_pajamathon_vdj(database_path=db, dry_run=False, paint=False)
            text = db.read_text(encoding="utf-8")
            self.assertEqual(result["dropped"], 1)
            self.assertIn("keep.flac", text)
            self.assertNotIn("gone.flac", text)
            self.assertIn("other.flac", text)

    def test_paints_white_from_one_sibling_lane(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            sets = tmp_p / "Sets"
            live = sets / "Pajamathon" / "song.flac"
            live.parent.mkdir(parents=True)
            live.write_bytes(b"x")
            sib = tmp_p / "Zouk" / "R&B" / "song.flac"
            db = tmp_p / "database.xml"
            db.write_text(
                _db([(str(live), "<Infos />")]),
                encoding="utf-8",
            )
            sibling = type("C", (), {"user_color": "4294941081"})()  # pink
            with (
                patch.object(sync, "SETS_ROOT", sets),
                patch.object(sync, "is_virtualdj_running", return_value=False),
                patch.object(sync, "cached_placement_indexes", return_value=({}, {})),
                patch.object(
                    sync,
                    "find_library_matches",
                    return_value=[{"path": str(sib)}],
                ),
                patch.object(sync, "summarize_cues", return_value=sibling),
                patch.object(sync, "is_pajamathon_set_audio", return_value=False),
                patch.object(sync, "backup_database", return_value=None),
                patch.object(Path, "is_file", lambda self: True),
            ):
                # keep live file check: Path.is_file patched True for all
                result = sync.sync_pajamathon_vdj(database_path=db, dry_run=False, paint=True)
            text = db.read_text(encoding="utf-8")
            self.assertEqual(result["painted"], 1)
            self.assertIn("4294941081", text)


if __name__ == "__main__":
    unittest.main()
