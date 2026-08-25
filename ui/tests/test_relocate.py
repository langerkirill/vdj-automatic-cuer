"""Move + VDJ FilePath relocate, including uncued gate."""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sorter import relocate as relocate_mod


def sample_db(path: str) -> bytes:
    return (
        "<VirtualDJ_Database>\r\n"
        f'<Song FilePath="{path}" Flag="1">\r\n'
        '  <Tags Author="A" Title="T" />\r\n'
        '  <Scan Bpm="0.5" />\r\n'
        '  <Poi Pos="0.1" Type="beatgrid" />\r\n'
        '  <Poi Name="Intro" Pos="0.1" Num="1" Color="4278190335" Type="cue" />\r\n'
        '  <Poi Name="Loop" Pos="8.0" Num="-1" Color="1" Type="loop" Size="16.0" Slot="1" />\r\n'
        "</Song>\r\n"
        "</VirtualDJ_Database>\r\n"
    ).encode("utf-8")


def sample_db_uncued(path: str) -> bytes:
    return (
        "<VirtualDJ_Database>\r\n"
        f'<Song FilePath="{path}">\r\n'
        '  <Poi Pos="0.1" Type="beatgrid" />\r\n'
        "</Song>\r\n"
        "</VirtualDJ_Database>\r\n"
    ).encode("utf-8")


class RelocateTests(unittest.TestCase):
    def test_sort_moves_file_and_updates_filepath(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ready = root / "Ready"
            house = root / "House" / "Chill"
            cues_sorted = root / "Cues Sorted"
            ready.mkdir()
            house.mkdir(parents=True)
            cues_sorted.mkdir()
            src = ready / "track.flac"
            src.write_bytes(b"audio-bytes")
            stems = Path(f"{src}.vdjstems")
            stems.write_bytes(b"stems")

            db = root / "database.xml"
            db.write_bytes(sample_db(str(src.resolve())))

            with patch(
                "sorter.library.LIBRARIES",
                {"House": root / "House", "Zouk": root / "Zouk"},
            ), patch.object(
                relocate_mod, "CUES_SORTED", cues_sorted
            ), patch.object(
                relocate_mod, "SETS_ROOT", root / "Sets"
            ), patch(
                "sorter.relocate.is_virtualdj_running", return_value=False
            ), patch(
                "sorter.relocate.VDJ_DATABASE", db
            ):
                result = relocate_mod.sort_track(
                    src,
                    library_name="House",
                    relative_folder="Chill",
                    database_path=db,
                    ready_root=ready,
                    create_backup=True,
                    also_cues_sorted=True,
                )

            dest = house / "track.flac"
            archive = cues_sorted / "Chill" / "track.flac"
            self.assertTrue(dest.is_file())
            self.assertFalse(src.exists())
            self.assertTrue(Path(f"{dest}.vdjstems").is_file())
            self.assertTrue(archive.is_file())
            self.assertTrue(Path(f"{archive}.vdjstems").is_file())
            self.assertTrue(result.database_updated)
            self.assertTrue(result.cues_sorted_copied)
            self.assertTrue(result.cues_sorted_db_cloned)
            self.assertEqual(result.library_mode, "House")
            raw = db.read_bytes()
            self.assertIn(str(dest.resolve()).encode(), raw)
            self.assertIn(str(archive.resolve()).encode(), raw)
            self.assertNotIn(str(src.resolve()).encode(), raw)
            self.assertIn(b'Name="Intro"', raw)
            self.assertIn(b"\r\n", raw)

    def test_sort_both_writes_house_and_zouk(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ready = root / "Ready"
            house = root / "House"
            zouk = root / "Zouk" / "Chill"
            cues_sorted = root / "Cues Sorted"
            ready.mkdir()
            house.mkdir()
            zouk.mkdir(parents=True)
            cues_sorted.mkdir()
            src = ready / "both.flac"
            src.write_bytes(b"audio")
            db = root / "database.xml"
            db.write_bytes(sample_db(str(src.resolve())))

            with patch(
                "sorter.library.LIBRARIES",
                {"House": house, "Zouk": root / "Zouk"},
            ), patch.object(
                relocate_mod, "CUES_SORTED", cues_sorted
            ), patch.object(
                relocate_mod, "SETS_ROOT", root / "Sets"
            ), patch(
                "sorter.relocate.is_virtualdj_running", return_value=False
            ):
                result = relocate_mod.sort_track(
                    src,
                    library_name="Both",
                    relative_folder="Chill",
                    database_path=db,
                    ready_root=ready,
                    create_backup=True,
                )

            zouk_dest = zouk / "both.flac"
            house_dest = house / "Chill" / "both.flac"
            self.assertTrue(zouk_dest.is_file(), "Zouk is primary for Both")
            self.assertTrue(house_dest.is_file(), "House receives a copy")
            self.assertTrue((cues_sorted / "Chill" / "both.flac").is_file())
            self.assertEqual(result.library_mode, "Both")
            libs = {d["library"] for d in result.library_dests}
            self.assertEqual(libs, {"House", "Zouk"})
            raw = db.read_bytes()
            self.assertIn(str(zouk_dest.resolve()).encode(), raw)
            self.assertIn(str(house_dest.resolve()).encode(), raw)

    def test_sort_copies_cues_to_matching_pajamathon_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ready = root / "Ready"
            zouk = root / "Zouk" / "Chill"
            cues_sorted = root / "Cues Sorted"
            paj = root / "Sets" / "Pajamathon 2026"
            ready.mkdir()
            zouk.mkdir(parents=True)
            cues_sorted.mkdir()
            paj.mkdir(parents=True)
            src = ready / "01 - Amaria - Moon.flac"
            set_copy = paj / "140. Amaria - Moon.flac"
            src.write_bytes(b"audio")
            set_copy.write_bytes(b"set-audio")
            db = root / "database.xml"
            db.write_bytes(
                (
                    "<VirtualDJ_Database>\r\n"
                    f'<Song FilePath="{src.resolve()}" Flag="1">\r\n'
                    '  <Tags Author="A" Title="Moon" />\r\n'
                    '  <Scan Bpm="0.5" />\r\n'
                    '  <Poi Pos="0.1" Type="beatgrid" />\r\n'
                    '  <Poi Name="Intro" Pos="0.1" Num="1" Color="4278190335" Type="cue" />\r\n'
                    '  <Poi Name="Loop" Pos="8.0" Num="-1" Color="1" Type="loop" Size="16.0" Slot="1" />\r\n'
                    "</Song>\r\n"
                    f'<Song FilePath="{set_copy.resolve()}">\r\n'
                    '  <Tags Author="A" Title="Moon" User2="Pajamathon 2026"/>\r\n'
                    '  <Scan Bpm="0.465" />\r\n'
                    '  <Poi Pos="0.2" Type="beatgrid" />\r\n'
                    "</Song>\r\n"
                    "</VirtualDJ_Database>\r\n"
                ).encode("utf-8")
            )

            with patch(
                "sorter.library.LIBRARIES",
                {"House": root / "House", "Zouk": root / "Zouk"},
            ), patch.object(
                relocate_mod, "CUES_SORTED", cues_sorted
            ), patch.object(
                relocate_mod, "SETS_ROOT", root / "Sets"
            ), patch.object(
                relocate_mod, "READY_FOR_SORT", ready
            ), patch.object(
                relocate_mod, "ADD_CUES", root / "Add Cues"
            ), patch(
                "sorter.library.SETS_ROOT", root / "Sets"
            ), patch(
                "sorter.relocate.is_virtualdj_running", return_value=False
            ), patch(
                "vdj_database_safety.is_virtualdj_running", return_value=False
            ):
                result = relocate_mod.sort_track(
                    src,
                    library_name="Zouk",
                    relative_folder="Chill",
                    database_path=db,
                    ready_root=ready,
                    create_backup=False,
                )

            self.assertGreaterEqual(result.sets_cues_copied, 1)
            self.assertTrue(any("Pajamathon" in p for p in result.sets_paths))
            self.assertEqual(set_copy.read_bytes(), b"set-audio")
            text = db.read_text(encoding="utf-8")
            set_span = text.index(str(set_copy.resolve()))
            set_block = text[set_span : text.index("</Song>", set_span)]
            self.assertIn('Name="Intro"', set_block)
            self.assertIn('Type="loop"', set_block)

    def test_sort_multi_destinations_house_and_zouk_different_folders(self):
        """Explicit destinations list can target different folders per library."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ready = root / "Ready"
            house = root / "House"
            zouk = root / "Zouk"
            cues_sorted = root / "Cues Sorted"
            ready.mkdir()
            (house / "Deep").mkdir(parents=True)
            (zouk / "Chill").mkdir(parents=True)
            cues_sorted.mkdir()
            src = ready / "multi.flac"
            src.write_bytes(b"audio")
            db = root / "database.xml"
            db.write_bytes(sample_db(str(src.resolve())))

            with patch(
                "sorter.library.LIBRARIES",
                {"House": house, "Zouk": zouk},
            ), patch.object(
                relocate_mod, "CUES_SORTED", cues_sorted
            ), patch.object(
                relocate_mod, "SETS_ROOT", root / "Sets"
            ), patch(
                "sorter.relocate.is_virtualdj_running", return_value=False
            ):
                result = relocate_mod.sort_track(
                    src,
                    library_name="Zouk",
                    relative_folder="",
                    destinations=[
                        {"library": "Zouk", "relative_folder": "Chill"},
                        {"library": "House", "relative_folder": "Deep"},
                    ],
                    database_path=db,
                    ready_root=ready,
                    create_backup=True,
                )

            zouk_dest = zouk / "Chill" / "multi.flac"
            house_dest = house / "Deep" / "multi.flac"
            self.assertTrue(zouk_dest.is_file())
            self.assertTrue(house_dest.is_file())
            self.assertFalse(src.exists())
            # Cues Sorted uses primary (Zouk) relative folder.
            self.assertTrue((cues_sorted / "Chill" / "multi.flac").is_file())
            libs = {(d["library"], d.get("relative_folder")) for d in result.library_dests}
            self.assertEqual(libs, {("Zouk", "Chill"), ("House", "Deep")})

    def test_uncued_track_cannot_sort(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ready = root / "Ready"
            house = root / "House" / "Chill"
            ready.mkdir()
            house.mkdir(parents=True)
            src = ready / "track.flac"
            src.write_bytes(b"audio")
            db = root / "database.xml"
            db.write_bytes(sample_db_uncued(str(src.resolve())))

            with patch(
                "sorter.library.LIBRARIES",
                {"House": root / "House"},
            ), patch(
                "sorter.relocate.is_virtualdj_running", return_value=False
            ):
                with self.assertRaises(PermissionError):
                    relocate_mod.sort_track(
                        src,
                        library_name="House",
                        relative_folder="Chill",
                        database_path=db,
                        ready_root=ready,
                    )
            self.assertTrue(src.exists())

    def test_summarize_cues_for_paths_reads_database_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = root / "a.flac"
            b = root / "b.flac"
            a.write_bytes(b"x")
            b.write_bytes(b"y")
            db = root / "database.xml"
            db.write_text(
                "<VirtualDJ_Database>\r\n"
                f'<Song FilePath="{a.resolve()}">\r\n'
                '  <Poi Name="Intro" Pos="0.1" Num="1" Type="cue" />\r\n'
                "</Song>\r\n"
                f'<Song FilePath="{b.resolve()}">\r\n'
                '  <Poi Pos="0.1" Type="beatgrid" />\r\n'
                "</Song>\r\n"
                "</VirtualDJ_Database>\r\n",
                encoding="utf-8",
            )
            reads = {"n": 0}
            real_read = relocate_mod.read_vdj_database_text

            def counting_read(path):
                reads["n"] += 1
                return real_read(path)

            with patch.object(relocate_mod, "read_vdj_database_text", side_effect=counting_read):
                out = relocate_mod.summarize_cues_for_paths(
                    [str(a), str(b), str(root / "missing.flac")],
                    database_path=db,
                )
            self.assertEqual(reads["n"], 1)
            self.assertTrue(out[str(a)].is_cued)
            self.assertEqual(out[str(a)].cue_count, 1)
            self.assertTrue(out[str(b)].in_database)
            self.assertFalse(out[str(b)].is_cued)
            self.assertFalse(out[str(root / "missing.flac")].in_database)

    def test_summarize_is_cued(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "a.flac"
            audio.write_bytes(b"x")
            db = root / "database.xml"
            db.write_bytes(sample_db(str(audio.resolve())))
            cues = relocate_mod.summarize_cues(audio, db)
            self.assertTrue(cues.is_cued)
            self.assertEqual(cues.cue_count, 1)
            self.assertEqual(cues.loop_count, 1)
            self.assertEqual(len(cues.points), 2)
            self.assertEqual(cues.points[0].name, "Intro")
            self.assertAlmostEqual(cues.points[0].pos, 0.1)
            self.assertEqual(cues.points[0].color_name, "blue")
            self.assertEqual(cues.points[1].kind, "loop")
            self.assertAlmostEqual(cues.bpm or 0, 120.0, places=1)
            payload = cues.to_dict()
            self.assertEqual(payload["points"][0]["name"], "Intro")

    def test_vdj_bpm_conversion(self):
        self.assertAlmostEqual(relocate_mod.vdj_bpm_to_actual(0.5), 120.0)
        self.assertAlmostEqual(relocate_mod.vdj_bpm_to_actual(128.0), 128.0)
        self.assertIsNone(relocate_mod.vdj_bpm_to_actual(None))

    def test_sort_secondary_failure_leaves_source_on_ready(self):
        """If a secondary destination copy fails, Ready source must remain."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ready = root / "Ready"
            house = root / "House"
            zouk = root / "Zouk" / "Chill"
            cues_sorted = root / "Cues Sorted"
            ready.mkdir()
            house.mkdir()
            zouk.mkdir(parents=True)
            cues_sorted.mkdir()
            src = ready / "partial.flac"
            src.write_bytes(b"audio")
            db = root / "database.xml"
            db.write_bytes(sample_db(str(src.resolve())))

            real_copy = relocate_mod._copy_file_and_stems

            def flaky_copy(source, dest):
                # Fail when copying the House secondary (not primary Zouk).
                if "House" in str(dest):
                    raise OSError("simulated secondary copy failure")
                return real_copy(source, dest)

            with patch(
                "sorter.library.LIBRARIES",
                {"House": house, "Zouk": root / "Zouk"},
            ), patch.object(
                relocate_mod, "CUES_SORTED", cues_sorted
            ), patch(
                "sorter.relocate.is_virtualdj_running", return_value=False
            ), patch.object(
                relocate_mod, "_copy_file_and_stems", side_effect=flaky_copy
            ):
                with self.assertRaises(RuntimeError):
                    relocate_mod.sort_track(
                        src,
                        library_name="Both",
                        relative_folder="Chill",
                        database_path=db,
                        ready_root=ready,
                        create_backup=False,
                    )

            self.assertTrue(src.is_file(), "source must stay on Ready after failed multi-dest")
            self.assertFalse((zouk / "partial.flac").exists())
            self.assertFalse((house / "Chill" / "partial.flac").exists())

    def test_trash_failure_does_not_hard_unlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "keep.flac"
            path.write_bytes(b"audio")
            with patch("sorter.relocate.subprocess.run") as run:
                run.return_value = type(
                    "R",
                    (),
                    {"returncode": 1, "stderr": "Finder busy", "stdout": ""},
                )()
                with self.assertRaises(RuntimeError):
                    relocate_mod._trash_or_unlink(path, to_trash=True)
            self.assertTrue(path.is_file())

    def test_remove_from_ready_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            ready = Path(tmp) / "Ready"
            ready.mkdir()
            src = ready / "skip-me.flac"
            src.write_bytes(b"audio")
            stems = Path(f"{src}.vdjstems")
            stems.write_bytes(b"stems")

            result = relocate_mod.remove_from_ready_for_sort(
                src,
                ready_root=ready,
                to_trash=False,
                remove_from_database=False,
            )
            self.assertFalse(src.exists())
            self.assertFalse(stems.exists())
            self.assertEqual(result["name"], "skip-me.flac")
            self.assertEqual(len(result["removed"]), 2)

            with self.assertRaises(ValueError):
                other = Path(tmp) / "elsewhere.flac"
                other.write_bytes(b"x")
                relocate_mod.remove_from_ready_for_sort(
                    other,
                    ready_root=ready,
                    to_trash=False,
                    remove_from_database=False,
                )

    def test_assess_cue_readiness(self):
        empty = relocate_mod.CueSummary(
            cue_count=0,
            loop_count=0,
            has_beatgrid=False,
            title="",
            author="",
            in_database=False,
        )
        self.assertEqual(relocate_mod.assess_cue_readiness(empty)["status"], "missing")

        partial = relocate_mod.CueSummary(
            cue_count=1,
            loop_count=0,
            has_beatgrid=True,
            title="t",
            author="a",
            in_database=True,
        )
        self.assertEqual(relocate_mod.assess_cue_readiness(partial)["status"], "partial")

        one_loop = relocate_mod.CueSummary(
            cue_count=3,
            loop_count=1,
            has_beatgrid=True,
            title="t",
            author="a",
            in_database=True,
        )
        one = relocate_mod.assess_cue_readiness(one_loop)
        self.assertFalse(one["ready"])
        self.assertEqual(one["status"], "partial")
        self.assertIn("2 loops", one["label"].lower())

        ready = relocate_mod.CueSummary(
            cue_count=3,
            loop_count=2,
            has_beatgrid=True,
            title="t",
            author="a",
            in_database=True,
        )
        assessment = relocate_mod.assess_cue_readiness(ready)
        self.assertTrue(assessment["ready"])
        self.assertEqual(assessment["status"], "ready")

    def test_promote_add_cues_to_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            add = root / "Add Cues" / "Batch"
            ready = root / "Ready For Sort"
            add.mkdir(parents=True)
            ready.mkdir()
            src = add / "track.flac"
            src.write_bytes(b"audio")
            db = root / "database.xml"
            db.write_bytes(sample_db(str(src.resolve())))

            with patch.object(relocate_mod, "ADD_CUES", root / "Add Cues"), patch.object(
                relocate_mod, "READY_FOR_SORT", ready
            ), patch.object(
                relocate_mod,
                "CUE_STAGES",
                {
                    "ready_for_sort": ready,
                    "no_cues_found": root / "No Cues Found",
                    "ac_low_quality": root / "AC Low Quality",
                    "low_quality_skip": root / "Low Quality Skip",
                },
            ), patch.object(relocate_mod, "CUES_ROOT", root), patch(
                "sorter.relocate.is_virtualdj_running", return_value=False
            ), patch(
                "sorter.ml_training.schedule_training_update"
            ) as ingest:
                result = relocate_mod.promote_add_cues_track(
                    src,
                    destination_stage="ready_for_sort",
                    database_path=db,
                    create_backup=True,
                )
            ingest.assert_called_once()
            self.assertEqual(Path(ingest.call_args.args[0]).name, "track.flac")

            dest = ready / "track.flac"
            self.assertTrue(dest.is_file())
            self.assertFalse(src.exists())
            self.assertTrue(result.database_updated)
            self.assertIn(str(dest.resolve()).encode(), db.read_bytes())

    def test_demote_ready_to_add_cues(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            add = root / "Add Cues"
            ready = root / "Ready For Sort"
            add.mkdir(parents=True)
            ready.mkdir()
            src = ready / "track.flac"
            src.write_bytes(b"audio")
            stems = Path(f"{src}.vdjstems")
            stems.write_bytes(b"stems")
            db = root / "database.xml"
            db.write_bytes(sample_db(str(src.resolve())))

            with patch.object(relocate_mod, "ADD_CUES", add), patch.object(
                relocate_mod, "READY_FOR_SORT", ready
            ), patch.object(relocate_mod, "CUES_ROOT", root), patch.object(
                relocate_mod, "VDJ_DATABASE", db
            ), patch(
                "sorter.relocate.is_virtualdj_running", return_value=False
            ):
                result = relocate_mod.demote_ready_to_add_cues(
                    src,
                    database_path=db,
                    create_backup=False,
                    subfolder="Back from Ready",
                )

            dest = add / "Back from Ready" / "track.flac"
            self.assertTrue(dest.is_file())
            self.assertFalse(src.exists())
            self.assertTrue(Path(f"{dest}.vdjstems").is_file())
            self.assertEqual(Path(result.dest_path).resolve(), dest.resolve())
            self.assertTrue(result.database_updated)
            self.assertIn(str(dest.resolve()).encode(), db.read_bytes())

    def test_delete_library_placement_removes_file_and_vdj_song(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            zouk = root / "Zouk" / "Chill"
            zouk.mkdir(parents=True)
            audio = zouk / "dup.flac"
            audio.write_bytes(b"audio")
            stems = Path(f"{audio}.vdjstems")
            stems.write_bytes(b"stems")
            other = root / "other.flac"
            other.write_bytes(b"keep")
            db = root / "database.xml"
            # Two songs: placement + unrelated keep.
            db.write_bytes(
                (
                    "<VirtualDJ_Database>\r\n"
                    f'<Song FilePath="{audio.resolve()}" Flag="1">\r\n'
                    '  <Tags Author="A" Title="T" />\r\n'
                    '  <Scan Bpm="0.5" />\r\n'
                    '  <Poi Pos="0.1" Type="beatgrid" />\r\n'
                    '  <Poi Name="Intro" Pos="0.1" Num="1" Color="4278190335" Type="cue" />\r\n'
                    '  <Poi Name="Loop" Pos="8.0" Num="-1" Color="1" Type="loop" Size="16.0" Slot="1" />\r\n'
                    "</Song>\r\n"
                    f'<Song FilePath="{other.resolve()}">\r\n'
                    '  <Poi Pos="0.1" Type="beatgrid" />\r\n'
                    '  <Poi Name="Keep" Pos="1.0" Num="1" Type="cue" />\r\n'
                    "</Song>\r\n"
                    "</VirtualDJ_Database>\r\n"
                ).encode("utf-8")
            )

            with patch.object(
                relocate_mod, "LIBRARIES", {"Zouk": root / "Zouk", "House": root / "House"}
            ), patch.object(
                relocate_mod, "CUES_SORTED", root / "Cues Sorted"
            ), patch.object(
                relocate_mod, "VDJ_DATABASE", db
            ), patch(
                "sorter.relocate.is_virtualdj_running", return_value=False
            ), patch(
                "vdj_database_safety.is_virtualdj_running", return_value=False
            ):
                result = relocate_mod.delete_library_placement(
                    audio,
                    database_path=db,
                    to_trash=False,
                    create_backup=True,
                )

            self.assertTrue(result["ok"])
            self.assertFalse(audio.exists())
            self.assertFalse(stems.exists())
            self.assertTrue(other.exists())
            text = db.read_text(encoding="utf-8")
            self.assertNotIn(str(audio.resolve()), text)
            self.assertIn(str(other.resolve()), text)
            self.assertIn('Name="Keep"', text)
            self.assertNotIn('Name="Intro"', text)
            self.assertTrue(result["database"]["removed_from_db"])
            self.assertEqual(result["had_cues"], 1)
            self.assertEqual(result["had_loops"], 1)

            # Safety: refuse Ready for Sort / random paths.
            with patch.object(
                relocate_mod, "LIBRARIES", {"Zouk": root / "Zouk"}
            ), patch.object(relocate_mod, "CUES_SORTED", root / "Cues Sorted"):
                with self.assertRaises(ValueError):
                    relocate_mod.delete_library_placement(
                        other, database_path=db, to_trash=False
                    )

    def test_delete_pajamathon_set_placement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paj = root / "Sets" / "Pajamathon 2026"
            paj.mkdir(parents=True)
            audio = paj / "407. 01 Dusk Till Dawn - Kizomba Remix.m4a"
            audio.write_bytes(b"set-audio")
            stems = Path(f"{audio}.vdjstems")
            stems.write_bytes(b"stems")
            db = root / "database.xml"
            db.write_bytes(sample_db(str(audio.resolve())))
            with patch.object(
                relocate_mod, "LIBRARIES", {"Zouk": root / "Zouk", "House": root / "House"}
            ), patch.object(
                relocate_mod, "CUES_SORTED", root / "Cues Sorted"
            ), patch.object(
                relocate_mod, "SETS_ROOT", root / "Sets"
            ), patch.object(
                relocate_mod, "VDJ_DATABASE", db
            ), patch(
                "sorter.relocate.is_virtualdj_running", return_value=False
            ), patch(
                "vdj_database_safety.is_virtualdj_running", return_value=False
            ):
                result = relocate_mod.delete_library_placement(
                    audio,
                    database_path=db,
                    to_trash=False,
                    create_backup=False,
                )
            self.assertTrue(result["ok"])
            self.assertFalse(audio.exists())
            self.assertFalse(stems.exists())
            self.assertEqual(result["root_name"], "Pajamathon 2026")
            self.assertIn("Pajamathon 2026/", result["relative_path"])
            self.assertNotIn(str(audio.resolve()), db.read_text(encoding="utf-8"))

    def test_delete_missing_pajamathon_placement_still_removes_vdj_song(self):
        """Already-trashed set copies must still drop their VirtualDJ Song."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paj = root / "Sets" / "Pajamathon 2026"
            paj.mkdir(parents=True)
            audio = paj / "407. 01 Dusk Till Dawn - Kizomba Remix.m4a"
            audio.write_bytes(b"set-audio")
            stems = Path(f"{audio}.vdjstems")
            stems.write_bytes(b"stems")
            db = root / "database.xml"
            db.write_bytes(sample_db(str(audio.resolve())))
            audio.unlink()
            self.assertFalse(audio.exists())
            with patch.object(
                relocate_mod, "LIBRARIES", {"Zouk": root / "Zouk", "House": root / "House"}
            ), patch.object(
                relocate_mod, "CUES_SORTED", root / "Cues Sorted"
            ), patch.object(
                relocate_mod, "SETS_ROOT", root / "Sets"
            ), patch.object(
                relocate_mod, "VDJ_DATABASE", db
            ), patch(
                "sorter.relocate.is_virtualdj_running", return_value=False
            ), patch(
                "vdj_database_safety.is_virtualdj_running", return_value=False
            ):
                result = relocate_mod.delete_library_placement(
                    audio,
                    database_path=db,
                    to_trash=False,
                    create_backup=False,
                )
            self.assertTrue(result["ok"])
            self.assertTrue(result.get("missing_file"))
            self.assertFalse(stems.exists())
            self.assertTrue(result["database"]["removed_from_db"])
            self.assertNotIn(str(audio.resolve()), db.read_text(encoding="utf-8"))

    def test_delete_missing_placement_ok_when_not_in_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paj = root / "Sets" / "Pajamathon 2026"
            paj.mkdir(parents=True)
            audio = paj / "090. Vlad Ivan - Dusk Till Dawn - Kizomba Remix.m4a"
            db = root / "database.xml"
            db.write_bytes(sample_db(str((paj / "other.m4a").resolve())))
            with patch.object(
                relocate_mod, "LIBRARIES", {"Zouk": root / "Zouk", "House": root / "House"}
            ), patch.object(
                relocate_mod, "CUES_SORTED", root / "Cues Sorted"
            ), patch.object(
                relocate_mod, "SETS_ROOT", root / "Sets"
            ), patch.object(
                relocate_mod, "VDJ_DATABASE", db
            ), patch(
                "sorter.relocate.is_virtualdj_running", return_value=False
            ):
                result = relocate_mod.delete_library_placement(
                    audio,
                    database_path=db,
                    to_trash=False,
                    create_backup=False,
                )
            self.assertTrue(result["ok"])
            self.assertTrue(result.get("missing_file"))
            self.assertEqual(result["database"].get("reason"), "not_in_database")

    def test_delete_missing_ghost_ok_when_vdj_open_and_not_in_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paj = root / "Sets" / "Pajamathon 2026"
            paj.mkdir(parents=True)
            audio = paj / "407. 01 Dusk Till Dawn - Kizomba Remix.m4a"
            db = root / "database.xml"
            db.write_bytes(sample_db(str((paj / "other.m4a").resolve())))
            with patch.object(
                relocate_mod, "LIBRARIES", {"Zouk": root / "Zouk", "House": root / "House"}
            ), patch.object(
                relocate_mod, "CUES_SORTED", root / "Cues Sorted"
            ), patch.object(
                relocate_mod, "SETS_ROOT", root / "Sets"
            ), patch.object(
                relocate_mod, "VDJ_DATABASE", db
            ), patch(
                "sorter.relocate.is_virtualdj_running", return_value=True
            ):
                result = relocate_mod.delete_library_placement(
                    audio,
                    database_path=db,
                    to_trash=False,
                    create_backup=False,
                )
            self.assertTrue(result["ok"])
            self.assertTrue(result.get("missing_file"))
            self.assertEqual(result["database"].get("reason"), "not_in_database")


def _copy_cues_db(source_path: str, dest_path: str, *, dest_cued: bool = False) -> bytes:
    dest_cues = (
        '  <Poi Name="Old Cue" Pos="1.0" Num="1" Color="4294967040" Type="cue" />\r\n'
        if dest_cued
        else ""
    )
    return (
        "<VirtualDJ_Database>\r\n"
        f'<Song FilePath="{source_path}" Flag="1">\r\n'
        '  <Tags Author="A" Title="T" />\r\n'
        '  <Scan Bpm="0.5" />\r\n'
        '  <Poi Pos="0.1" Type="beatgrid" />\r\n'
        '  <Poi Name="Intro" Pos="0.1" Num="1" Color="4278190335" Type="cue" />\r\n'
        '  <Poi Name="Loop" Pos="8.0" Num="-1" Color="1" Type="loop" Size="16.0" Slot="1" />\r\n'
        "</Song>\r\n"
        f'<Song FilePath="{dest_path}">\r\n'
        '  <Tags Author="A" Title="T" User2="RnB" />\r\n'
        '  <Scan Bpm="0.465" />\r\n'
        '  <Poi Pos="0.2" Type="beatgrid" />\r\n'
        f"{dest_cues}"
        "  <Comment>keep-me</Comment>\r\n"
        "</Song>\r\n"
        "</VirtualDJ_Database>\r\n"
    ).encode("utf-8")


class CopyCuesToPlacementTests(unittest.TestCase):
    def _setup(self, tmp: str, *, dest_cued: bool = False, dest_in_db: bool = True):
        root = Path(tmp)
        ready = root / "Ready For Sort"
        zouk = root / "Zouk" / "RnB"
        ready.mkdir(parents=True)
        zouk.mkdir(parents=True)
        src = ready / "Moon.flac"
        dest = zouk / "01 - Amaria - Moon.flac"
        src.write_bytes(b"ready-audio")
        dest.write_bytes(b"library-audio")
        db = root / "database.xml"
        if dest_in_db:
            db.write_bytes(_copy_cues_db(str(src.resolve()), str(dest.resolve()), dest_cued=dest_cued))
        else:
            db.write_bytes(sample_db(str(src.resolve())))
        patches = [
            patch.object(
                relocate_mod, "LIBRARIES", {"Zouk": root / "Zouk", "House": root / "House"}
            ),
            patch.object(relocate_mod, "CUES_SORTED", root / "Cues Sorted"),
            patch.object(relocate_mod, "READY_FOR_SORT", ready),
            patch.object(relocate_mod, "ADD_CUES", root / "Add Cues"),
            patch.object(relocate_mod, "VDJ_DATABASE", db),
            patch("sorter.relocate.is_virtualdj_running", return_value=False),
            patch("vdj_database_safety.is_virtualdj_running", return_value=False),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return src, dest, db

    def test_injects_cues_into_existing_uncued_library_song(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dest, db = self._setup(tmp)
            result = relocate_mod.copy_cues_to_placement(
                src, dest, database_path=db, create_backup=True
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["mode"], "injected")
            self.assertEqual(result["copied_cues"], 1)
            self.assertEqual(result["copied_loops"], 1)
            self.assertTrue(src.is_file(), "Ready for Sort file must stay")
            self.assertTrue(dest.is_file())
            self.assertEqual(dest.read_bytes(), b"library-audio")

            raw = db.read_bytes()
            self.assertIn(b"\r\n", raw)
            text = raw.decode("utf-8")
            dest_span_start = text.index(str(dest.resolve()))
            dest_block = text[dest_span_start : text.index("</Song>", dest_span_start)]
            self.assertIn('Name="Intro"', dest_block)
            self.assertIn('Type="loop"', dest_block)
            self.assertIn('Pos="0.2"', dest_block)
            self.assertIn('Bpm="0.465"', dest_block)
            self.assertIn("keep-me", dest_block)
            self.assertIn("User2=", dest_block)
            self.assertNotIn('Name="Old Cue"', dest_block)
            self.assertIn(str(src.resolve()), text)

    def test_clones_song_when_dest_missing_from_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dest, db = self._setup(tmp, dest_in_db=False)
            result = relocate_mod.copy_cues_to_placement(
                src, dest, database_path=db, create_backup=False
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["mode"], "cloned")
            text = db.read_text(encoding="utf-8")
            self.assertIn(dest.name, text)
            self.assertIn(str(src.resolve()), text)
            self.assertIn('Name="Intro"', text)
            dest_span = text.index(dest.name)
            dest_block = text[dest_span : text.index("</Song>", dest_span)]
            self.assertIn('Name="Intro"', dest_block)

    def test_refuses_overwrite_of_loop_only_dest(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dest, db = self._setup(tmp)
            db.write_bytes(
                (
                    "<VirtualDJ_Database>\r\n"
                    f'<Song FilePath="{src.resolve()}" Flag="1">\r\n'
                    '  <Poi Name="Intro" Pos="0.1" Num="1" Color="4278190335" Type="cue" />\r\n'
                    "</Song>\r\n"
                    f'<Song FilePath="{dest.resolve()}">\r\n'
                    '  <Poi Pos="0.2" Type="beatgrid" />\r\n'
                    '  <Poi Name="Loop" Pos="8.0" Num="-1" Type="loop" Size="16.0" Slot="1" />\r\n'
                    "</Song>\r\n"
                    "</VirtualDJ_Database>\r\n"
                ).encode("utf-8")
            )
            with self.assertRaises(ValueError) as ctx:
                relocate_mod.copy_cues_to_placement(
                    src, dest, database_path=db, overwrite=False, create_backup=False
                )
            self.assertIn("already has", str(ctx.exception).lower())

    def test_refuses_overwrite_without_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dest, db = self._setup(tmp, dest_cued=True)
            with self.assertRaises(ValueError) as ctx:
                relocate_mod.copy_cues_to_placement(
                    src, dest, database_path=db, overwrite=False, create_backup=False
                )
            self.assertIn("already has", str(ctx.exception).lower())
            text = db.read_text(encoding="utf-8")
            self.assertIn('Name="Old Cue"', text)
            self.assertEqual(text.count('Name="Intro"'), 1)

    def test_overwrite_replaces_dest_cues(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dest, db = self._setup(tmp, dest_cued=True)
            result = relocate_mod.copy_cues_to_placement(
                src,
                dest,
                database_path=db,
                overwrite=True,
                create_backup=False,
            )
            self.assertTrue(result["ok"])
            self.assertTrue(result["overwrote"])
            text = db.read_text(encoding="utf-8")
            dest_span = text.index(str(dest.resolve()))
            dest_block = text[dest_span : text.index("</Song>", dest_span)]
            self.assertIn('Name="Intro"', dest_block)
            self.assertNotIn('Name="Old Cue"', dest_block)
            self.assertIn("keep-me", dest_block)

    def test_requires_source_cued(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dest, db = self._setup(tmp)
            db.write_bytes(
                (
                    "<VirtualDJ_Database>\r\n"
                    f'<Song FilePath="{src.resolve()}">\r\n'
                    '  <Poi Pos="0.1" Type="beatgrid" />\r\n'
                    "</Song>\r\n"
                    f'<Song FilePath="{dest.resolve()}">\r\n'
                    '  <Poi Pos="0.2" Type="beatgrid" />\r\n'
                    "</Song>\r\n"
                    "</VirtualDJ_Database>\r\n"
                ).encode("utf-8")
            )
            with self.assertRaises(ValueError) as ctx:
                relocate_mod.copy_cues_to_placement(
                    src, dest, database_path=db, create_backup=False
                )
            self.assertIn("cue", str(ctx.exception).lower())

    def test_refuses_non_library_dest_and_non_queue_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dest, db = self._setup(tmp)
            other = Path(tmp) / "elsewhere.flac"
            other.write_bytes(b"x")
            with self.assertRaises(ValueError):
                relocate_mod.copy_cues_to_placement(
                    src, other, database_path=db, create_backup=False
                )
            with self.assertRaises(ValueError):
                relocate_mod.copy_cues_to_placement(
                    dest, dest, database_path=db, create_backup=False
                )

    def test_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dest, db = self._setup(tmp)
            before = db.read_bytes()
            result = relocate_mod.copy_cues_to_placement(
                src, dest, database_path=db, dry_run=True, create_backup=False
            )
            self.assertTrue(result["ok"])
            self.assertTrue(result["dry_run"])
            self.assertEqual(result["mode"], "injected")
            self.assertEqual(db.read_bytes(), before)

    def test_allows_pajamathon_set_dest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ready = root / "Ready For Sort"
            paj = root / "Sets" / "Pajamathon 2026"
            ready.mkdir(parents=True)
            paj.mkdir(parents=True)
            src = ready / "01 - Amaria - Moon.flac"
            dest = paj / "140. Amaria - Moon.flac"
            src.write_bytes(b"ready")
            dest.write_bytes(b"set")
            db = root / "database.xml"
            db.write_bytes(_copy_cues_db(str(src.resolve()), str(dest.resolve())))
            with patch.object(
                relocate_mod, "LIBRARIES", {"Zouk": root / "Zouk", "House": root / "House"}
            ), patch.object(relocate_mod, "CUES_SORTED", root / "Cues Sorted"), patch.object(
                relocate_mod, "READY_FOR_SORT", ready
            ), patch.object(relocate_mod, "ADD_CUES", root / "Add Cues"), patch.object(
                relocate_mod, "SETS_ROOT", root / "Sets"
            ), patch.object(relocate_mod, "VDJ_DATABASE", db), patch(
                "sorter.relocate.is_virtualdj_running", return_value=False
            ):
                result = relocate_mod.copy_cues_to_placement(
                    src, dest, database_path=db, create_backup=False
                )
            self.assertTrue(result["ok"])
            self.assertEqual(result["mode"], "injected")
            self.assertEqual(result["root_name"], "Pajamathon 2026")
            dest_block = db.read_text(encoding="utf-8")
            self.assertIn('Name="Intro"', dest_block)

    def test_allows_add_cues_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            add = root / "Add Cues"
            zouk = root / "Zouk" / "Chill"
            add.mkdir(parents=True)
            zouk.mkdir(parents=True)
            src = add / "track.flac"
            dest = zouk / "track.flac"
            src.write_bytes(b"a")
            dest.write_bytes(b"b")
            db = root / "database.xml"
            db.write_bytes(_copy_cues_db(str(src.resolve()), str(dest.resolve())))
            with patch.object(
                relocate_mod, "LIBRARIES", {"Zouk": root / "Zouk", "House": root / "House"}
            ), patch.object(relocate_mod, "CUES_SORTED", root / "Cues Sorted"), patch.object(
                relocate_mod, "READY_FOR_SORT", root / "Ready For Sort"
            ), patch.object(relocate_mod, "ADD_CUES", add), patch.object(
                relocate_mod, "VDJ_DATABASE", db
            ), patch("sorter.relocate.is_virtualdj_running", return_value=False):
                result = relocate_mod.copy_cues_to_placement(
                    src, dest, database_path=db, create_backup=False
                )
            self.assertTrue(result["ok"])
            self.assertEqual(result["mode"], "injected")

    def test_copy_cues_to_all_library_locations(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dest, db = self._setup(tmp)
            house = Path(tmp) / "House" / "Chill"
            house.mkdir(parents=True)
            dest2 = house / dest.name
            dest2.write_bytes(b"house-audio")
            db.write_bytes(
                (
                    "<VirtualDJ_Database>\r\n"
                    f'<Song FilePath="{src.resolve()}" Flag="1">\r\n'
                    '  <Poi Name="Intro" Pos="0.1" Num="1" Color="4278190335" Type="cue" />\r\n'
                    '  <Poi Name="Loop" Pos="8.0" Num="-1" Type="loop" Size="16.0" Slot="1" />\r\n'
                    "</Song>\r\n"
                    f'<Song FilePath="{dest.resolve()}">\r\n'
                    '  <Poi Pos="0.2" Type="beatgrid" />\r\n'
                    "</Song>\r\n"
                    f'<Song FilePath="{dest2.resolve()}">\r\n'
                    '  <Poi Pos="0.3" Type="beatgrid" />\r\n'
                    "</Song>\r\n"
                    "</VirtualDJ_Database>\r\n"
                ).encode("utf-8")
            )
            result = relocate_mod.copy_cues_to_placements(
                src, [dest, dest2, dest], database_path=db, create_backup=False
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["copied"], 2)
            self.assertEqual(result["failed"], 0)
            self.assertEqual(result["skipped"], 0)
            text = db.read_bytes().decode("utf-8")
            for path in (dest, dest2):
                start = text.index(str(path.resolve()))
                block = text[start : text.index("</Song>", start)]
                self.assertIn('Name="Intro"', block)
                self.assertIn('Type="loop"', block)

    def test_add_track_to_pajamathon_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dest, db = self._setup(tmp)
            sets = Path(tmp) / "Sets"
            paj = sets / "Pajamathon 2026"
            paj.mkdir(parents=True)
            (paj / "083. Other.flac").write_bytes(b"x")
            with patch.object(relocate_mod, "SETS_ROOT", sets), patch(
                "sorter.library.SETS_ROOT", sets
            ):
                result = relocate_mod.add_track_to_event_set(
                    src, sets_root=sets, database_path=db, create_backup=False
                )
            added = Path(result["dest_path"])
            self.assertTrue(added.is_file())
            self.assertEqual(added.parent.resolve(), paj.resolve())
            self.assertTrue(added.name.startswith("084. "))
            self.assertEqual(added.read_bytes(), b"ready-audio")
            self.assertIn(str(added.resolve()).encode(), db.read_bytes())
            self.assertIn(b'Name="Intro"', db.read_bytes())
            self.assertTrue(src.is_file())

            with patch.object(relocate_mod, "SETS_ROOT", sets), patch(
                "sorter.library.SETS_ROOT", sets
            ):
                again = relocate_mod.add_track_to_event_set(
                    src, sets_root=sets, database_path=db, create_backup=False
                )
            self.assertTrue(again["already_exists"])
            self.assertEqual(Path(again["dest_path"]).resolve(), added.resolve())
            self.assertEqual(again["relative_path"], f"{paj.name}/{added.name}")

    def test_add_track_refuses_parenthetical_and_version_set_copies(self):
        with tempfile.TemporaryDirectory() as tmp:
            ready = Path(tmp) / "Ready For Sort"
            ready.mkdir()
            chantaje = ready / "14 - Dj Kakah - Chantaje (Kizomba Remix).mp3"
            tunnel = ready / "Dj Kakah - Tunnel Vision 2.mp3"
            chantaje.write_bytes(b"chantaje")
            tunnel.write_bytes(b"tunnel")
            sets = Path(tmp) / "Sets"
            paj = sets / "Pajamathon 2026"
            paj.mkdir(parents=True)
            (paj / "165. Dj Kakah - Chantaje (Shakira & Maluma).mp3").write_bytes(
                b"set-chantaje"
            )
            (paj / "385. Dj Kakah - Tunnel Vision Version 2.mp3").write_bytes(
                b"set-tunnel"
            )
            with patch.object(relocate_mod, "SETS_ROOT", sets), patch(
                "sorter.library.SETS_ROOT", sets
            ), patch.object(relocate_mod, "READY_FOR_SORT", ready):
                chantaje_hit = relocate_mod.add_track_to_event_set(
                    chantaje, sets_root=sets, create_backup=False
                )
                tunnel_hit = relocate_mod.add_track_to_event_set(
                    tunnel, sets_root=sets, create_backup=False
                )
            self.assertTrue(chantaje_hit["already_exists"])
            self.assertIn("Chantaje (Shakira & Maluma)", chantaje_hit["relative_path"])
            self.assertTrue(tunnel_hit["already_exists"])
            self.assertIn("Tunnel Vision Version 2", tunnel_hit["relative_path"])

    def test_set_copy_basename_strips_space_padded_track_number(self):
        name = relocate_mod._set_copy_basename(
            Path("/ready/01 Dusk Till Dawn - Kizomba Remix.m4a")
        )
        self.assertEqual(name, "Dusk Till Dawn - Kizomba Remix.m4a")


if __name__ == "__main__":
    unittest.main()
