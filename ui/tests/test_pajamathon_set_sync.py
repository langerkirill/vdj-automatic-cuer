"""Pajamathon Add Cues deletions propagate to Sets/Pajamathon only."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sorter.pajamathon_set_sync import (
    deleted_add_cues_names,
    prune_m3u_paths,
    prune_vdjfolder_paths,
    push_cues_to_sibling_copies,
    push_library_cues_to_pajamathon,
    sync_pajamathon_set_deletes,
)


def _write_db(db: Path, paths: list[Path]) -> None:
    songs = []
    for path in paths:
        songs.append(
            f'<Song FilePath="{path}">\r\n'
            '  <Poi Name="Intro" Pos="0.1" Num="1" Type="cue" />\r\n'
            "</Song>\r\n"
        )
    db.write_bytes(
        ("<VirtualDJ_Database>\r\n" + "".join(songs) + "</VirtualDJ_Database>\r\n").encode(
            "utf-8"
        )
    )


class PajamathonSetSyncTests(unittest.TestCase):
    def _tree(self, tmp: Path) -> dict[str, Path]:
        add = tmp / "Add Cues"
        paj = add / "Pajamathon"
        sets = tmp / "Sets" / "Pajamathon 2026"
        ready = tmp / "Ready For Sort"
        zouk = tmp / "Zouk" / "Chill"
        notes = tmp / "Notes"
        for path in (paj, sets, ready, zouk, notes):
            path.mkdir(parents=True)
        return {
            "add": add,
            "paj": paj,
            "sets": sets,
            "ready": ready,
            "zouk": zouk,
            "notes": notes,
            "snapshot": notes / "pajamathon_add_cues_snapshot.json",
            "db": tmp / "database.xml",
            "m3u": notes / "pajamathon-2026.m3u",
            "vdjfolder": notes / "Pajamathon 2026.vdjfolder",
        }

    def test_deleted_names_are_snapshot_minus_current(self) -> None:
        gone = deleted_add_cues_names(
            previous_names=["keep.flac", "drop.flac"],
            current_names=["keep.flac"],
            extra_names=["also.flac"],
        )
        self.assertEqual(gone, {"drop.flac", "also.flac"})

    def test_dry_run_lists_set_match_and_leaves_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._tree(Path(tmp))
            add_file = paths["paj"] / "01 - Ayelle - Mind and Body.flac"
            keep_add = paths["paj"] / "01 - Keep Me.flac"
            set_file = paths["sets"] / "049. Ayelle - Mind and Body.flac"
            keep_set = paths["sets"] / "001. Keep Me.flac"
            lib = paths["zouk"] / "01 - Ayelle - Mind and Body.flac"
            for file in (add_file, keep_add, set_file, keep_set, lib):
                file.write_bytes(b"audio")
            Path(f"{set_file}.vdjstems").write_bytes(b"stems")
            _write_db(paths["db"], [add_file, set_file, keep_set, lib])
            paths["snapshot"].write_text(
                json.dumps(
                    {
                        "files": [
                            {"name": add_file.name},
                            {"name": keep_add.name},
                            {"name": "01 - Ayelle - Mind and Body.flac"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            add_file.unlink()

            result = sync_pajamathon_set_deletes(
                add_cues_root=paths["add"],
                sets_root=paths["sets"].parent,
                snapshot_path=paths["snapshot"],
                ready_root=paths["ready"],
                database_path=paths["db"],
                playlist_paths=[],
                dry_run=True,
                to_trash=False,
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["removed_count"], 1)
            self.assertEqual(Path(result["removed"][0]["set_path"]).name, set_file.name)
            self.assertTrue(set_file.is_file())
            self.assertTrue(lib.is_file())
            self.assertTrue(keep_set.is_file())

    def test_removes_set_copy_not_library_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._tree(Path(tmp))
            add_file = paths["paj"] / "01 - Galimatias - South.flac"
            set_file = paths["sets"] / "062. Galimatias - South.flac"
            lib = paths["zouk"] / "01 - Galimatias - South.flac"
            for file in (add_file, set_file, lib):
                file.write_bytes(b"audio")
            stems = Path(f"{set_file}.vdjstems")
            stems.write_bytes(b"stems")
            _write_db(paths["db"], [set_file, lib])
            paths["m3u"].write_text(
                "#EXTM3U\n"
                "#EXTINF:-1,Galimatias - South\n"
                f"{set_file}\n"
                "#EXTINF:-1,Keep\n"
                f"{paths['sets'] / '001. Keep.flac'}\n",
                encoding="utf-8",
            )
            paths["vdjfolder"].write_text(
                '<?xml version="1.0"?>\n<VirtualFolder>\n'
                f'  <song path="{set_file}" idx="0" />\n'
                f'  <song path="{paths["sets"] / "001. Keep.flac"}" idx="1" />\n'
                "</VirtualFolder>\n",
                encoding="utf-8",
            )
            add_file.unlink()

            with patch(
                "sorter.pajamathon_set_sync.is_virtualdj_running", return_value=False
            ), patch(
                "sorter.relocate.is_virtualdj_running", return_value=False
            ):
                result = sync_pajamathon_set_deletes(
                    add_cues_root=paths["add"],
                    sets_root=paths["sets"].parent,
                    snapshot_path=paths["snapshot"],
                    staged_seed_path=None,
                    extra_deleted=[add_file.name],
                    ready_root=paths["ready"],
                    database_path=paths["db"],
                    playlist_paths=[paths["m3u"], paths["vdjfolder"]],
                    dry_run=False,
                    to_trash=False,
                )

            self.assertTrue(result["ok"])
            self.assertEqual(result["removed_count"], 1)
            self.assertFalse(set_file.exists())
            self.assertFalse(stems.exists())
            self.assertTrue(lib.is_file())
            db_text = paths["db"].read_text(encoding="utf-8")
            self.assertNotIn(str(set_file), db_text)
            self.assertIn(str(lib), db_text)
            m3u = paths["m3u"].read_text(encoding="utf-8")
            self.assertNotIn(str(set_file), m3u)
            self.assertIn("001. Keep.flac", m3u)
            folder = paths["vdjfolder"].read_text(encoding="utf-8")
            self.assertNotIn(str(set_file), folder)
            self.assertIn("001. Keep.flac", folder)

    def test_promoted_to_ready_is_not_removed_from_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._tree(Path(tmp))
            set_file = paths["sets"] / "140. Amaria - Moon.flac"
            ready = paths["ready"] / "01 - Amaria - Moon.flac"
            set_file.write_bytes(b"set")
            ready.write_bytes(b"ready")
            result = sync_pajamathon_set_deletes(
                add_cues_root=paths["add"],
                sets_root=paths["sets"].parent,
                snapshot_path=paths["snapshot"],
                extra_deleted=["01 - Amaria - Moon.flac"],
                ready_root=paths["ready"],
                database_path=paths["db"],
                playlist_paths=[],
                dry_run=False,
                to_trash=False,
            )
            self.assertEqual(result["removed_count"], 0)
            self.assertTrue(set_file.is_file())
            self.assertIn("01 - Amaria - Moon.flac", result["skipped_promoted"])

    def test_inbox_move_is_not_treated_as_delete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._tree(Path(tmp))
            inbox = paths["add"] / "01 - Keep Inbox.flac"
            set_file = paths["sets"] / "010. Keep Inbox.flac"
            inbox.write_bytes(b"in")
            set_file.write_bytes(b"set")
            result = sync_pajamathon_set_deletes(
                add_cues_root=paths["add"],
                sets_root=paths["sets"].parent,
                snapshot_path=paths["snapshot"],
                extra_deleted=["01 - Keep Inbox.flac"],
                ready_root=paths["ready"],
                database_path=paths["db"],
                playlist_paths=[],
                dry_run=False,
                to_trash=False,
            )
            self.assertEqual(result["removed_count"], 0)
            self.assertTrue(set_file.is_file())

    def test_uncued_set_file_without_add_cues_history_stays(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._tree(Path(tmp))
            already_cued = paths["sets"] / "001. noevdv - nobody like you.m4a"
            already_cued.write_bytes(b"cued")
            result = sync_pajamathon_set_deletes(
                add_cues_root=paths["add"],
                sets_root=paths["sets"].parent,
                snapshot_path=paths["snapshot"],
                extra_deleted=[],
                ready_root=paths["ready"],
                database_path=paths["db"],
                playlist_paths=[],
                dry_run=False,
                to_trash=False,
            )
            self.assertEqual(result["removed_count"], 0)
            self.assertTrue(already_cued.is_file())

    def test_ambiguous_set_matches_are_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._tree(Path(tmp))
            first = paths["sets"] / "010. Same Title.flac"
            second = paths["sets"] / "011. Same Title.flac"
            first.write_bytes(b"a")
            second.write_bytes(b"b")
            result = sync_pajamathon_set_deletes(
                add_cues_root=paths["add"],
                sets_root=paths["sets"].parent,
                snapshot_path=paths["snapshot"],
                extra_deleted=["01 - Same Title.flac"],
                ready_root=paths["ready"],
                database_path=paths["db"],
                playlist_paths=[],
                dry_run=False,
                to_trash=False,
            )
            self.assertEqual(result["removed_count"], 0)
            self.assertTrue(first.is_file())
            self.assertTrue(second.is_file())
            self.assertEqual(len(result["skipped_ambiguous"]), 1)

    def test_sorted_into_library_is_not_removed_from_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._tree(Path(tmp))
            set_file = paths["sets"] / "140. Amaria - Moon.flac"
            lib = paths["zouk"] / "01 - Amaria - Moon.flac"
            set_file.write_bytes(b"set")
            lib.write_bytes(b"lib")
            paths["snapshot"].write_text(
                json.dumps({"files": [{"name": "01 - Amaria - Moon.flac"}]}),
                encoding="utf-8",
            )
            result = sync_pajamathon_set_deletes(
                add_cues_root=paths["add"],
                sets_root=paths["sets"].parent,
                snapshot_path=paths["snapshot"],
                ready_root=paths["ready"],
                library_roots=[paths["zouk"].parent],
                cues_sorted_root=Path(tmp) / "Cues Sorted",
                database_path=paths["db"],
                playlist_paths=[],
                dry_run=False,
                to_trash=False,
            )
            self.assertEqual(result["removed_count"], 0)
            self.assertTrue(set_file.is_file())
            self.assertTrue(lib.is_file())
            self.assertIn("01 - Amaria - Moon.flac", result["skipped_promoted"])

    def test_explicit_delete_removes_set_even_if_library_copy_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._tree(Path(tmp))
            set_file = paths["sets"] / "062. Galimatias - South.flac"
            lib = paths["zouk"] / "01 - Galimatias - South.flac"
            set_file.write_bytes(b"set")
            lib.write_bytes(b"lib")
            result = sync_pajamathon_set_deletes(
                add_cues_root=paths["add"],
                sets_root=paths["sets"].parent,
                snapshot_path=paths["snapshot"],
                extra_deleted=["01 - Galimatias - South.flac"],
                ready_root=paths["ready"],
                library_roots=[paths["zouk"].parent],
                cues_sorted_root=Path(tmp) / "Cues Sorted",
                database_path=paths["db"],
                playlist_paths=[],
                dry_run=False,
                to_trash=False,
            )
            self.assertEqual(result["removed_count"], 1)
            self.assertFalse(set_file.exists())
            self.assertTrue(lib.is_file())

    def test_missing_snapshot_writes_snapshot_and_deletes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._tree(Path(tmp))
            keep_add = paths["paj"] / "keep.flac"
            set_file = paths["sets"] / "001. keep.flac"
            keep_add.write_bytes(b"a")
            set_file.write_bytes(b"s")
            seed = Path(tmp) / "staged.json"
            seed.write_text(
                json.dumps({"names": ["01 - Would Replay.flac"]}),
                encoding="utf-8",
            )
            replay = paths["sets"] / "010. Would Replay.flac"
            replay.write_bytes(b"r")
            result = sync_pajamathon_set_deletes(
                add_cues_root=paths["add"],
                sets_root=paths["sets"].parent,
                snapshot_path=paths["snapshot"],
                staged_seed_path=seed,
                ready_root=paths["ready"],
                database_path=paths["db"],
                playlist_paths=[],
                dry_run=False,
                to_trash=False,
            )
            self.assertEqual(result["removed_count"], 0)
            self.assertTrue(set_file.is_file())
            self.assertTrue(replay.is_file())
            self.assertTrue(paths["snapshot"].is_file())

    def test_second_run_is_noop_after_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._tree(Path(tmp))
            keep = paths["paj"] / "keep.flac"
            keep.write_bytes(b"k")
            first = sync_pajamathon_set_deletes(
                add_cues_root=paths["add"],
                sets_root=paths["sets"].parent,
                snapshot_path=paths["snapshot"],
                ready_root=paths["ready"],
                database_path=paths["db"],
                playlist_paths=[],
                dry_run=False,
                to_trash=False,
            )
            second = sync_pajamathon_set_deletes(
                add_cues_root=paths["add"],
                sets_root=paths["sets"].parent,
                snapshot_path=paths["snapshot"],
                ready_root=paths["ready"],
                database_path=paths["db"],
                playlist_paths=[],
                dry_run=False,
                to_trash=False,
            )
            self.assertEqual(first["removed_count"], 0)
            self.assertEqual(second["removed_count"], 0)
            self.assertTrue(paths["snapshot"].is_file())

    def test_prune_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            m3u = Path(tmp) / "list.m3u"
            gone = "/Sets/Pajamathon 2026/010. Drop.flac"
            keep = "/Sets/Pajamathon 2026/001. Keep.flac"
            m3u.write_text(
                f"#EXTM3U\n#EXTINF:-1,Drop\n{gone}\n#EXTINF:-1,Keep\n{keep}\n",
                encoding="utf-8",
            )
            self.assertEqual(prune_m3u_paths(m3u, {gone}), 1)
            text = m3u.read_text(encoding="utf-8")
            self.assertNotIn("Drop", text)
            self.assertIn("Keep", text)

            folder = Path(tmp) / "crate.vdjfolder"
            folder.write_text(
                f'<VirtualFolder>\n  <song path="{gone}" idx="0" />\n'
                f'  <song path="{keep}" idx="1" />\n</VirtualFolder>\n',
                encoding="utf-8",
            )
            self.assertEqual(prune_vdjfolder_paths(folder, {gone}), 1)
            xml = folder.read_text(encoding="utf-8")
            self.assertNotIn(gone, xml)
            self.assertIn(keep, xml)

    def test_push_library_cues_onto_uncued_pajamathon_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            zouk = root / "Zouk" / "Classics"
            paj = root / "Sets" / "Pajamathon 2026"
            zouk.mkdir(parents=True)
            paj.mkdir(parents=True)
            src = zouk / "01 Can't Be Friends (Saxo-Kizomba).m4a"
            dest = paj / "411. Can't Be Friends (Saxo-Kizomba).m4a"
            src.write_bytes(b"lib")
            dest.write_bytes(b"set")
            db = root / "database.xml"
            db.write_bytes(
                (
                    "<VirtualDJ_Database>\r\n"
                    f'<Song FilePath="{src.resolve()}">\r\n'
                    '  <Tags Author="A" Title="T" />\r\n'
                    '  <Poi Name="Intro" Pos="0.1" Num="1" Type="cue" />\r\n'
                    '  <Poi Name="Loop" Pos="8.0" Num="-1" Type="loop" Size="16.0" />\r\n'
                    "</Song>\r\n"
                    f'<Song FilePath="{dest.resolve()}">\r\n'
                    '  <Tags Author="A" Title="T" />\r\n'
                    "</Song>\r\n"
                    "</VirtualDJ_Database>\r\n"
                ).encode("utf-8")
            )
            with patch(
                "sorter.pajamathon_set_sync.LIBRARIES",
                {"Zouk": root / "Zouk", "House": root / "House"},
            ), patch(
                "sorter.pajamathon_set_sync.CUES_SORTED", root / "Cues Sorted"
            ), patch(
                "sorter.pajamathon_set_sync.READY_FOR_SORT", root / "Ready"
            ), patch(
                "sorter.pajamathon_set_sync.ADD_CUES", root / "Add Cues"
            ), patch(
                "sorter.pajamathon_set_sync.SETS_ROOT", root / "Sets"
            ), patch(
                "sorter.library.SETS_ROOT", root / "Sets"
            ), patch(
                "sorter.pajamathon_set_sync.is_virtualdj_running", return_value=False
            ), patch(
                "sorter.relocate.is_virtualdj_running", return_value=False
            ):
                result = push_library_cues_to_pajamathon(
                    sets_root=root / "Sets",
                    library_roots=[root / "Zouk"],
                    database_path=db,
                    dry_run=False,
                    create_backup=False,
                )
            self.assertGreaterEqual(result["copied"], 1)
            dest_xml = db.read_text(encoding="utf-8")
            dest_at = dest_xml.index(str(dest.resolve()))
            dest_block = dest_xml[dest_at : dest_xml.index("</Song>", dest_at)]
            self.assertIn('Name="Intro"', dest_block)

    def test_push_cues_to_sibling_house_zouk_and_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            zouk = root / "Zouk" / "Classics"
            lamba = root / "Zouk" / "Lamba"
            archive = root / "Cues Sorted" / "Classics"
            house = root / "House" / "Chill"
            for folder in (zouk, lamba, archive, house):
                folder.mkdir(parents=True)
            src = zouk / "01 Track.m4a"
            dest_lamba = lamba / "01 Track.m4a"
            dest_arch = archive / "01 Track.m4a"
            dest_house = house / "01 Track.m4a"
            for path in (src, dest_lamba, dest_arch, dest_house):
                path.write_bytes(b"x")
            db = root / "database.xml"
            songs = [
                (
                    f'<Song FilePath="{src.resolve()}">\r\n'
                    '  <Poi Name="Intro" Pos="0.1" Num="1" Type="cue" />\r\n'
                    "</Song>\r\n"
                ),
                f'<Song FilePath="{dest_lamba.resolve()}">\r\n</Song>\r\n',
                f'<Song FilePath="{dest_arch.resolve()}">\r\n</Song>\r\n',
            ]
            db.write_bytes(
                ("<VirtualDJ_Database>\r\n" + "".join(songs) + "</VirtualDJ_Database>\r\n").encode()
            )
            with patch(
                "sorter.pajamathon_set_sync.LIBRARIES",
                {"Zouk": root / "Zouk", "House": root / "House"},
            ), patch(
                "sorter.pajamathon_set_sync.CUES_SORTED", root / "Cues Sorted"
            ), patch(
                "sorter.pajamathon_set_sync.SETS_ROOT", root / "Sets"
            ), patch(
                "sorter.pajamathon_set_sync.READY_FOR_SORT", root / "Ready"
            ), patch(
                "sorter.pajamathon_set_sync.ADD_CUES", root / "Add Cues"
            ), patch(
                "sorter.pajamathon_set_sync.is_virtualdj_running", return_value=False
            ), patch(
                "sorter.relocate.is_virtualdj_running", return_value=False
            ):
                result = push_cues_to_sibling_copies(
                    dest_roots=[root / "Zouk", root / "House", root / "Cues Sorted"],
                    source_roots=[],
                    database_path=db,
                    dry_run=False,
                    create_backup=False,
                )
            self.assertGreaterEqual(result["copied"], 2)
            text = db.read_text(encoding="utf-8")
            for dest in (dest_lamba, dest_arch):
                at = text.index(str(dest.resolve()))
                block = text[at : text.index("</Song>", at)]
                self.assertIn('Name="Intro"', block)


if __name__ == "__main__":
    unittest.main()
