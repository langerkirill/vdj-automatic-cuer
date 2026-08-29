"""Delete individual cue/loop markers from VDJ Song XML."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sorter import cue_edit as cue_mod


SAMPLE_SONG = (
    '<Song FilePath="{path}">\r\n'
    '  <Tags Author="A" Title="T" />\r\n'
    '  <Scan Bpm="0.5" Phase="0.1" />\r\n'
    '  <Poi Pos="0.100000" Type="beatgrid" />\r\n'
    '  <Poi Name="Intro" Pos="0.100000" Num="1" Color="4278190335" Type="cue" />\r\n'
    '  <Poi Name="Drop" Pos="32.000000" Num="2" Color="4278255360" Type="cue" />\r\n'
    '  <Poi Name="Loop A" Pos="16.000000" Num="-1" Color="1" Type="loop" Size="16.0" Slot="1" />\r\n'
    '  <Poi Name="Loop B" Pos="48.000000" Num="-1" Color="1" Type="loop" Size="32.0" Slot="2" />\r\n'
    "</Song>\r\n"
)


class CueEditXmlTests(unittest.TestCase):
    def test_remove_cue_by_pos_and_num(self):
        xml = SAMPLE_SONG.format(path="/music/a.flac")
        out, removed = cue_mod.remove_manual_poi_from_song_xml(
            xml, kind="cue", pos=32.0, num="2"
        )
        self.assertEqual(removed["name"], "Drop")
        self.assertNotIn('Name="Drop"', out)
        self.assertIn('Name="Intro"', out)
        self.assertIn('Type="beatgrid"', out)
        self.assertIn('Name="Loop A"', out)

    def test_remove_loop_keeps_other_loop(self):
        xml = SAMPLE_SONG.format(path="/music/a.flac")
        out, removed = cue_mod.remove_manual_poi_from_song_xml(
            xml, kind="loop", pos=16.0, num="-1", name="Loop A"
        )
        self.assertEqual(removed["name"], "Loop A")
        self.assertNotIn('Name="Loop A"', out)
        self.assertIn('Name="Loop B"', out)
        self.assertIn('Name="Intro"', out)

    def test_remove_missing_raises(self):
        xml = SAMPLE_SONG.format(path="/music/a.flac")
        with self.assertRaises(KeyError):
            cue_mod.remove_manual_poi_from_song_xml(xml, kind="cue", pos=99.0, num="9")

    def test_scale_loop_half_and_double(self):
        xml = SAMPLE_SONG.format(path="/music/a.flac")
        half, ch = cue_mod.scale_loop_size_in_song_xml(
            xml, pos=16.0, factor=0.5, num="-1", slot="1"
        )
        self.assertEqual(ch["beats_before"], 16.0)
        self.assertEqual(ch["beats_after"], 8.0)
        self.assertIn('Size="8.0"', half)
        self.assertIn('Size="32.0"', half)  # other loop unchanged

        doubled, ch2 = cue_mod.scale_loop_size_in_song_xml(
            half, pos=16.0, factor=2.0, num="-1", slot="1"
        )
        self.assertEqual(ch2["beats_after"], 16.0)
        self.assertIn('Size="16.0"', doubled)

    def test_set_poi_color_cue_and_loop(self):
        xml = SAMPLE_SONG.format(path="/music/a.flac")
        out, ch = cue_mod.set_poi_color_in_song_xml(
            xml, kind="cue", pos=0.1, color="orange", num="1"
        )
        self.assertEqual(ch["color_name"], "orange")
        self.assertIn(f'Color="{cue_mod.VDJ_CUE_COLORS["orange"]}"', out)
        self.assertIn('Name="Intro"', out)
        # Loop color change
        out2, ch2 = cue_mod.set_poi_color_in_song_xml(
            out, kind="loop", pos=16.0, color="purple", slot="1"
        )
        self.assertEqual(ch2["color_name"], "purple")
        self.assertIn(f'Color="{cue_mod.VDJ_CUE_COLORS["purple"]}"', out2)

    def test_set_poi_color_finds_cue_by_num_after_move(self):
        """UI sends the dragged time; XML still has the old Pos until color uses Num."""
        xml = SAMPLE_SONG.format(path="/music/a.flac")
        moved, _ = cue_mod.set_poi_position_in_song_xml(
            xml, kind="cue", pos=32.0, new_pos=40.25, num="2"
        )
        out, ch = cue_mod.set_poi_color_in_song_xml(
            moved, kind="cue", pos=32.0, color="yellow", num="2"
        )
        self.assertEqual(ch["color_name"], "yellow")
        self.assertIn(f'Color="{cue_mod.VDJ_CUE_COLORS["yellow"]}"', out)
        self.assertIn('Name="Drop"', out)
        self.assertIn('Pos="40.25"', out)

    def test_fill_missing_poi_colors_inserts_only_bare_tags(self):
        xml = (
            '<Song FilePath="/music/a.flac">\r\n'
            '  <Poi Name="Intro" Pos="0.1" Num="1" Type="cue" />\r\n'
            '  <Poi Name="Drop" Pos="32.0" Num="2" Color="4278255360" Type="cue" />\r\n'
            '  <Poi Name="Loop A" Pos="16.0" Num="-1" Type="loop" Size="8.0" Slot="1" />\r\n'
            "</Song>\r\n"
        )
        out, changes = cue_mod.fill_missing_poi_colors_in_song_xml(
            xml, default_color="green"
        )
        names = {c["name"] for c in changes}
        self.assertEqual(names, {"Intro", "Loop A"})
        self.assertIn(f'Color="{cue_mod.VDJ_CUE_COLORS["green"]}"', out)
        # Existing Drop color stays green (already set), Intro/Loop get Color.
        self.assertEqual(out.count("Color="), 3)
        self.assertIn('Name="Drop"', out)
        self.assertIn("4278255360", out)

    def test_fill_replaces_unknown_placeholder_color(self):
        xml = (
            '<Song FilePath="/music/a.flac">\r\n'
            '  <Poi Name="Intro" Pos="0.1" Num="1" Color="4294901760" Type="cue" />\r\n'
            '  <Poi Name="Drop" Pos="32.0" Num="2" Color="4278255360" Type="cue" />\r\n'
            "</Song>\r\n"
        )
        out, changes = cue_mod.fill_missing_poi_colors_in_song_xml(
            xml, default_color="yellow"
        )
        self.assertEqual([c["name"] for c in changes], ["Intro"])
        self.assertIn(f'Color="{cue_mod.VDJ_CUE_COLORS["yellow"]}"', out)
        self.assertNotIn("4294901760", out)
        self.assertIn("4278255360", out)

    def test_set_poi_position_moves_loop(self):
        xml = SAMPLE_SONG.format(path="/music/a.flac")
        out, ch = cue_mod.set_poi_position_in_song_xml(
            xml, kind="loop", pos=16.0, new_pos=20.5, slot="1"
        )
        self.assertAlmostEqual(ch["pos_before"], 16.0)
        self.assertAlmostEqual(ch["pos_after"], 20.5)
        self.assertIn('Pos="20.5"', out)
        self.assertIn('Name="Loop A"', out)
        self.assertIn('Pos="48.000000"', out)  # other loop unchanged

    def test_set_poi_position_moves_cue(self):
        xml = SAMPLE_SONG.format(path="/music/a.flac")
        out, ch = cue_mod.set_poi_position_in_song_xml(
            xml, kind="cue", pos=32.0, new_pos=40.25, num="2"
        )
        self.assertAlmostEqual(ch["pos_before"], 32.0)
        self.assertAlmostEqual(ch["pos_after"], 40.25)
        self.assertIn('Name="Drop"', out)
        self.assertIn('Pos="40.25"', out)
        self.assertIn('Pos="0.100000"', out)  # intro + beatgrid stay

    def test_add_cue_poi_uses_next_free_num(self):
        xml = SAMPLE_SONG.format(path="/music/a.flac")
        out, ch = cue_mod.add_cue_poi_in_song_xml(xml, pos=8.0, name="Verse")
        self.assertEqual(ch["num"], "3")
        self.assertEqual(ch["name"], "Verse")
        self.assertAlmostEqual(ch["pos"], 8.0)
        self.assertIn('Name="Verse"', out)
        self.assertIn('Num="3"', out)
        self.assertIn('Type="cue"', out)
        self.assertIn('Name="Intro"', out)
        self.assertIn('Name="Drop"', out)
        self.assertIn("</Song>", out)

    def test_add_cue_poi_rejects_when_slots_full(self):
        xml = SAMPLE_SONG.format(path="/music/a.flac")
        for num in range(3, 9):
            xml, _ = cue_mod.add_cue_poi_in_song_xml(xml, pos=float(num), name=f"C{num}")
        with self.assertRaises(ValueError):
            cue_mod.add_cue_poi_in_song_xml(xml, pos=99.0)

    def test_add_cue_poi_keeps_existing_markers(self):
        xml = SAMPLE_SONG.format(path="/music/a.flac")
        out, _ = cue_mod.add_cue_poi_in_song_xml(xml, pos=8.0, name="Verse")
        self.assertEqual(out.count('Type="cue"'), 3)
        self.assertIn('Name="Loop A"', out)
        self.assertIn('Type="beatgrid"', out)

    def test_add_cue_poi_rejects_occupied_downbeat(self):
        xml = SAMPLE_SONG.format(path="/music/a.flac")
        with self.assertRaises(ValueError):
            cue_mod.add_cue_poi_in_song_xml(xml, pos=32.0, name="Dup")

    def test_add_cue_poi_escapes_name_quotes(self):
        xml = SAMPLE_SONG.format(path="/music/a.flac")
        out, ch = cue_mod.add_cue_poi_in_song_xml(xml, pos=8.0, name='Verse "A"')
        self.assertEqual(ch["name"], 'Verse "A"')
        self.assertIn("Verse &quot;A&quot;", out)
        self.assertNotIn('Name="Verse "A""', out)

    def test_add_loop_poi_uses_next_free_slot(self):
        xml = SAMPLE_SONG.format(path="/music/a.flac")
        out, ch = cue_mod.add_loop_poi_in_song_xml(xml, pos=8.0, name="Fill")
        self.assertEqual(ch["slot"], "3")
        self.assertEqual(ch["num"], "-1")
        self.assertAlmostEqual(ch["beats"], 8.0)
        self.assertIn('Type="loop"', out)
        self.assertIn('Size="8.0"', out)
        self.assertIn('Slot="3"', out)
        self.assertIn('Name="Loop A"', out)
        self.assertIn('Name="Intro"', out)

    def test_add_loop_poi_rejects_occupied_start(self):
        xml = SAMPLE_SONG.format(path="/music/a.flac")
        with self.assertRaises(ValueError):
            cue_mod.add_loop_poi_in_song_xml(xml, pos=16.0, name="Dup")


class CueEditDeleteTests(unittest.TestCase):
    def test_delete_cue_point_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "track.flac"
            audio.write_bytes(b"x")
            path = str(audio.resolve())
            db = root / "database.xml"
            db.write_bytes(
                (
                    "<VirtualDJ_Database>\r\n"
                    + SAMPLE_SONG.format(path=path)
                    + "</VirtualDJ_Database>\r\n"
                ).encode("utf-8")
            )
            with patch.object(cue_mod, "CUES_ROOT", root), patch.object(
                cue_mod, "LIBRARIES", {}
            ), patch.object(cue_mod, "VDJ_DATABASE", db), patch(
                "sorter.cue_edit.is_virtualdj_running", return_value=False
            ):
                result = cue_mod.delete_cue_point(
                    audio,
                    kind="cue",
                    pos=0.1,
                    num="1",
                    database_path=db,
                    create_backup=True,
                )
            self.assertTrue(result["ok"])
            self.assertEqual(result["removed"]["name"], "Intro")
            self.assertEqual(result["cue_count_after"], 1)
            text = db.read_text(encoding="utf-8")
            self.assertNotIn('Name="Intro"', text)
            self.assertIn('Name="Drop"', text)
            self.assertIn('Type="beatgrid"', text)
            self.assertTrue(Path(result["database_backup"]).is_file())

    def test_scale_loop_point_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "track.flac"
            audio.write_bytes(b"x")
            path = str(audio.resolve())
            db = root / "database.xml"
            db.write_bytes(
                (
                    "<VirtualDJ_Database>\r\n"
                    + SAMPLE_SONG.format(path=path)
                    + "</VirtualDJ_Database>\r\n"
                ).encode("utf-8")
            )
            with patch.object(cue_mod, "CUES_ROOT", root), patch.object(
                cue_mod, "LIBRARIES", {}
            ), patch.object(cue_mod, "VDJ_DATABASE", db), patch(
                "sorter.cue_edit.is_virtualdj_running", return_value=False
            ):
                result = cue_mod.scale_loop_point(
                    audio,
                    pos=48.0,
                    factor=0.5,
                    num="-1",
                    slot="2",
                    database_path=db,
                    create_backup=True,
                )
            self.assertTrue(result["ok"])
            self.assertEqual(result["change"]["beats_after"], 16.0)
            text = db.read_text(encoding="utf-8")
            self.assertIn('Name="Loop B"', text)
            self.assertIn('Size="16.0"', text)
            # Loop A still 16
            self.assertRegex(text, r'Name="Loop A"[^>]*Size="16\.0"')

    def test_add_cue_point_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "track.flac"
            audio.write_bytes(b"x")
            path = str(audio.resolve())
            db = root / "database.xml"
            db.write_bytes(
                (
                    "<VirtualDJ_Database>\r\n"
                    + SAMPLE_SONG.format(path=path)
                    + "</VirtualDJ_Database>\r\n"
                ).encode("utf-8")
            )
            with patch.object(cue_mod, "CUES_ROOT", root), patch.object(
                cue_mod, "LIBRARIES", {}
            ), patch.object(cue_mod, "VDJ_DATABASE", db), patch(
                "sorter.cue_edit.is_virtualdj_running", return_value=False
            ):
                result = cue_mod.add_cue_point(
                    audio,
                    pos=8.0,
                    name="Verse",
                    database_path=db,
                    create_backup=True,
                )
            self.assertTrue(result["ok"])
            self.assertEqual(result["change"]["num"], "3")
            self.assertEqual(result["change"]["name"], "Verse")
            self.assertEqual(result["cue_count"], 3)
            text = db.read_text(encoding="utf-8")
            self.assertIn('Name="Verse"', text)
            self.assertIn('Name="Intro"', text)
            self.assertIn('Name="Drop"', text)
            self.assertIn('Type="beatgrid"', text)
            self.assertTrue(Path(result["database_backup"]).is_file())

    def test_add_cue_point_refuses_when_vdj_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "track.flac"
            audio.write_bytes(b"x")
            path = str(audio.resolve())
            db = root / "database.xml"
            db.write_bytes(
                (
                    "<VirtualDJ_Database>\r\n"
                    + SAMPLE_SONG.format(path=path)
                    + "</VirtualDJ_Database>\r\n"
                ).encode("utf-8")
            )
            before = db.read_bytes()
            with patch.object(cue_mod, "CUES_ROOT", root), patch.object(
                cue_mod, "LIBRARIES", {}
            ), patch.object(cue_mod, "VDJ_DATABASE", db), patch(
                "sorter.cue_edit.is_virtualdj_running", return_value=True
            ):
                with self.assertRaises(RuntimeError):
                    cue_mod.add_cue_point(
                        audio,
                        pos=8.0,
                        name="Verse",
                        database_path=db,
                        create_backup=False,
                    )
            self.assertEqual(db.read_bytes(), before)

    def test_add_loop_point_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "track.flac"
            audio.write_bytes(b"x")
            path = str(audio.resolve())
            db = root / "database.xml"
            db.write_bytes(
                (
                    "<VirtualDJ_Database>\r\n"
                    + SAMPLE_SONG.format(path=path)
                    + "</VirtualDJ_Database>\r\n"
                ).encode("utf-8")
            )
            with patch.object(cue_mod, "CUES_ROOT", root), patch.object(
                cue_mod, "LIBRARIES", {}
            ), patch.object(cue_mod, "VDJ_DATABASE", db), patch(
                "sorter.cue_edit.is_virtualdj_running", return_value=False
            ):
                result = cue_mod.add_loop_point(
                    audio,
                    pos=8.0,
                    name="Fill",
                    database_path=db,
                    create_backup=True,
                )
            self.assertTrue(result["ok"])
            self.assertEqual(result["change"]["slot"], "3")
            self.assertEqual(result["loop_count"], 3)
            text = db.read_text(encoding="utf-8")
            self.assertIn('Name="Fill"', text)
            self.assertIn('Type="loop"', text)
            self.assertIn('Name="Loop A"', text)
            self.assertIn('Name="Intro"', text)
            self.assertTrue(Path(result["database_backup"]).is_file())


class CueEditAnywhereTests(unittest.TestCase):
    def test_assert_allowed_accepts_sets_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio = (
                Path(tmp)
                / "Sets"
                / "Pajamathon 2026"
                / "014. Simon Vuarambon - Quimera.flac"
            )
            audio.parent.mkdir(parents=True)
            audio.write_bytes(b"x")
            resolved = cue_mod._assert_allowed(audio)
            self.assertEqual(resolved, audio.resolve())

    def test_assert_allowed_rejects_missing_file(self):
        missing = Path("/tmp/does-not-exist-cue-edit.flac")
        with self.assertRaises(FileNotFoundError):
            cue_mod._assert_allowed(missing)

    def test_delete_cue_on_sets_track_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "Sets" / "Pajamathon 2026" / "track.flac"
            audio.parent.mkdir(parents=True)
            audio.write_bytes(b"x")
            path = str(audio.resolve())
            db = Path(tmp) / "database.xml"
            db.write_bytes(
                (
                    "<VirtualDJ_Database>\r\n"
                    + SAMPLE_SONG.format(path=path)
                    + "</VirtualDJ_Database>\r\n"
                ).encode("utf-8")
            )
            with patch.object(cue_mod, "VDJ_DATABASE", db), patch(
                "sorter.cue_edit.is_virtualdj_running", return_value=False
            ):
                result = cue_mod.delete_cue_point(
                    audio,
                    kind="cue",
                    pos=0.1,
                    num="1",
                    database_path=db,
                    create_backup=False,
                )
            self.assertTrue(result["ok"])
            self.assertEqual(result["removed"]["name"], "Intro")
            self.assertNotIn('Name="Intro"', db.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
