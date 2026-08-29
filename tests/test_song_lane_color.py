"""Song-name UserColor: folder lane classification and Infos rewrite."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from song_lane_color import (
    LANE_COLORS,
    PENDING_COLOR,
    PENDING_LANE,
    apply_lane_color_after_move,
    apply_user_color_to_infos,
    classify_path,
    classify_placement_path,
    classify_placements,
    classify_song,
    classify_user2,
    current_user_color,
    path_in_scope,
    plan_color_updates,
    rewrite_database_user_colors,
    song_has_manual_cues,
    song_identity,
)


class ClassifyPathTests(unittest.TestCase):
    def test_chill_is_blue(self):
        self.assertEqual(
            classify_path("/Users/x/Music/DJ/Music/Zouk/Chill/Shaman/t.flac"),
            "blue",
        )

    def test_chill_fire_stays_blue_not_yellow(self):
        self.assertEqual(
            classify_path("/Users/x/Music/DJ/Music/Zouk/Chill/Fire/t.flac"),
            "blue",
        )

    def test_energy_is_green(self):
        self.assertEqual(
            classify_path("/Users/x/Music/DJ/Music/Zouk/Energy/Sex/t.flac"),
            "green",
        )

    def test_energy_trappy_stays_green_not_pink(self):
        self.assertEqual(
            classify_path("/Users/x/Music/DJ/Music/Zouk/Energy/Trappy/t.flac"),
            "green",
        )

    def test_intense_is_yellow(self):
        self.assertEqual(
            classify_path("/Users/x/Music/DJ/Music/Zouk/Intense/t.flac"),
            "yellow",
        )

    def test_trancy_is_cyan(self):
        self.assertEqual(
            classify_path("/Users/x/Music/DJ/Music/Zouk/Trancy/t.flac"),
            "cyan",
        )

    def test_lamba_is_orange(self):
        self.assertEqual(
            classify_path("/Users/x/Music/DJ/Music/Zouk/Lamba/t.m4a"),
            "orange",
        )

    def test_tribal_is_orange(self):
        self.assertEqual(
            classify_path("/Users/x/Music/DJ/Music/Zouk/Tribal/t.flac"),
            "orange",
        )

    def test_kizouk_is_magenta(self):
        self.assertEqual(
            classify_path("/Users/x/Music/DJ/Music/Zouk/Kizouk/t.mp3"),
            "magenta",
        )

    def test_neo_zouk_is_magenta(self):
        self.assertEqual(
            classify_path("/Users/x/Music/DJ/Music/Zouk/Neo Zouk/t.mp3"),
            "magenta",
        )

    def test_kiz_is_pink_not_magenta(self):
        self.assertEqual(
            classify_path("/Users/x/Music/DJ/Music/Zouk/Kiz/Gouyad_Kompa/t.mp3"),
            "pink",
        )

    def test_rnb_is_pink(self):
        self.assertEqual(
            classify_path("/Users/x/Music/DJ/Music/Zouk/R&B/t.flac"),
            "pink",
        )

    def test_remixes_is_red(self):
        self.assertEqual(
            classify_path("/Users/x/Music/DJ/Music/Zouk/Remixes/t.mp3"),
            "red",
        )

    def test_sets_kizouk_is_not_a_lane(self):
        self.assertIsNone(
            classify_path("/Users/x/Music/DJ/Music/Sets/Kizouk/t.mp3")
        )

    def test_sets_only_has_no_lane(self):
        self.assertIsNone(
            classify_path("/Users/x/Music/DJ/Music/Sets/ZM/t.mp3")
        )

    def test_artist_folder_has_no_lane(self):
        self.assertIsNone(
            classify_path("/Users/x/Music/DJ/Music/Zouk/Kakah/t.mp3")
        )

    def test_bassy_has_no_lane(self):
        self.assertIsNone(
            classify_path("/Users/x/Music/DJ/Music/Zouk/Bassy/t.flac")
        )

    def test_house_only_is_out_of_scope(self):
        self.assertIsNone(
            classify_path("/Users/x/Music/DJ/Music/House/Chenergy/t.mp3")
        )

    def test_cues_sorted_chill(self):
        self.assertEqual(
            classify_path(
                "/Users/x/Music/DJ/Music/Cues/Cues Sorted/Chill/Mystical/t.flac"
            ),
            "blue",
        )

    def test_cuessorted_energy_is_green(self):
        self.assertEqual(
            classify_path(
                "/Users/x/Music/DJ/Music/CuesSorted/Energy/t.flac"
            ),
            "green",
        )

    def test_cues_sorted_backup_is_out_of_scope(self):
        self.assertFalse(
            path_in_scope(
                "/Users/x/Music/DJ/Music/Cues/Cues Sorted Backup 20250707/"
                "Energy/t.flac"
            )
        )
        self.assertIsNone(
            classify_path(
                "/Users/x/Music/DJ/Music/Cues/Cues Sorted Backup 20250707/"
                "Energy/t.flac"
            )
        )

    def test_cues_sorted_copy_is_out_of_scope(self):
        self.assertFalse(
            path_in_scope(
                "/Users/x/Music/DJ/Music/Cues/Cues Sorted copy/Energy/t.flac"
            )
        )

    def test_house_chill_placement_is_blue(self):
        self.assertEqual(
            classify_placement_path("/Users/x/Music/DJ/Music/House/Chill/t.flac"),
            "blue",
        )

    def test_zouk_energy_placement_is_green(self):
        self.assertEqual(
            classify_placement_path(
                "/Users/x/Music/DJ/Music/Zouk/Energy/Party/t.flac"
            ),
            "green",
        )

    def test_kakah_placement_is_pending(self):
        self.assertIsNone(
            classify_placement_path("/Users/x/Music/DJ/Music/Zouk/Kakah/t.flac")
        )
        self.assertEqual(
            classify_placements(["/Users/x/Music/DJ/Music/Zouk/Kakah/t.flac"]),
            PENDING_LANE,
        )

    def test_beautiful_sound_is_blue(self):
        self.assertEqual(
            classify_path("/Users/x/Music/DJ/Music/Zouk/Beautiful Sound/t.flac"),
            "blue",
        )


class ClassifySongTests(unittest.TestCase):
    def test_chill_plus_trancy_is_cyan(self):
        self.assertEqual(
            classify_song(
                [
                    "/x/Music/DJ/Music/Zouk/Chill/t.flac",
                    "/x/Music/DJ/Music/Zouk/Trancy/t.flac",
                ]
            ),
            "cyan",
        )

    def test_energy_plus_intense_is_yellow(self):
        self.assertEqual(
            classify_song(
                [
                    "/x/Music/DJ/Music/Zouk/Energy/t.flac",
                    "/x/Music/DJ/Music/Zouk/Intense/t.flac",
                ]
            ),
            "yellow",
        )

    def test_kizouk_plus_energy_is_magenta(self):
        self.assertEqual(
            classify_song(
                [
                    "/x/Music/DJ/Music/Zouk/Energy/t.flac",
                    "/x/Music/DJ/Music/Zouk/Kizouk/t.flac",
                ]
            ),
            "magenta",
        )

    def test_sets_copy_uses_zouk_folder(self):
        self.assertEqual(
            classify_song(
                [
                    "/x/Music/DJ/Music/Sets/ZM/t.flac",
                    "/x/Music/DJ/Music/Zouk/Energy/t.flac",
                ]
            ),
            "green",
        )

    def test_sets_kizouk_does_not_override_trancy(self):
        self.assertEqual(
            classify_song(
                [
                    "/x/Music/DJ/Music/Sets/Kizouk/t.flac",
                    "/x/Music/DJ/Music/Zouk/Trancy/t.flac",
                    "/x/Music/DJ/Music/Zouk/Chill/All Chill/t.flac",
                ]
            ),
            "cyan",
        )

    def test_no_lane_paths(self):
        self.assertIsNone(
            classify_song(["/x/Music/DJ/Music/Sets/ZM/t.flac"])
        )


class IdentityAndCueTests(unittest.TestCase):
    def test_identity_ignores_conflicted_and_case(self):
        self.assertEqual(
            song_identity("/a/Foo (conflicted).mp3"),
            song_identity("/b/foo.mp3"),
        )

    def test_identity_strips_set_track_numbers(self):
        self.assertEqual(
            song_identity(
                "/Sets/Pajamathon 2026/021. Prayer of Protection (Nyrus Remix).flac"
            ),
            song_identity(
                "/Zouk/Tribal/Prayer of Protection (Nyrus Remix).flac"
            ),
        )
        self.assertEqual(
            song_identity("/Sets/Pajamathon 2026/162. 07 - Liquid Bloom - Heart.flac"),
            "07 - liquid bloom - heart.flac",
        )

    def test_user2_chill_is_blue(self):
        self.assertEqual(classify_user2("Chill/Shaman"), "blue")

    def test_user2_lamba_is_orange(self):
        self.assertEqual(classify_user2("Lamba"), "orange")

    def test_user2_add_cues_is_not_a_lane(self):
        self.assertIsNone(classify_user2("Add Cues/Pajamathon"))

    def test_pajamathon_uses_library_twin_not_sets_folder(self):
        self.assertEqual(
            classify_song(
                [
                    "/x/Music/DJ/Music/Sets/Pajamathon 2026/021. Prayer.flac",
                    "/x/Music/DJ/Music/Zouk/Tribal/Prayer.flac",
                ]
            ),
            "orange",
        )

    def test_pajamathon_user2_fallback(self):
        self.assertEqual(
            classify_song(
                ["/x/Music/DJ/Music/Sets/Pajamathon 2026/001. nobody like you.m4a"],
                ["R&B"],
            ),
            "pink",
        )

    def test_library_path_beats_user2(self):
        self.assertEqual(
            classify_song(
                ["/x/Music/DJ/Music/Zouk/Tribal/Prayer.flac"],
                ["Chill/Shaman"],
            ),
            "orange",
        )

    def test_stems_are_not_audio_identities(self):
        self.assertIsNone(song_identity("/a/foo.flac.vdjstems"))

    def test_has_manual_cues(self):
        cued = '<Song FilePath="/a.flac"><Poi Type="cue" Num="1" /></Song>'
        looped = '<Song FilePath="/a.flac"><Poi Type="loop" Num="2" /></Song>'
        automix = '<Song FilePath="/a.flac"><Poi Type="automix" /></Song>'
        self.assertTrue(song_has_manual_cues(cued))
        self.assertTrue(song_has_manual_cues(looped))
        self.assertFalse(song_has_manual_cues(automix))


class AfterMoveTests(unittest.TestCase):
    def test_apply_lane_color_after_move_sets_dest_color(self):
        dest = "/Users/x/Music/DJ/Music/Zouk/Trancy/moved.flac"
        db_xml = (
            "<VirtualDJ_Database>\r\n"
            f'<Song FilePath="{dest}">\r\n'
            '<Infos SongLength="100" Cover="1" />\r\n'
            '<Poi Name="Intro" Type="cue" Num="1" Color="1" />\r\n'
            "</Song>\r\n"
            "</VirtualDJ_Database>\r\n"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "database.xml"
            path.write_bytes(db_xml.encode("utf-8"))
            with patch("vdj_database_safety.assert_safe_to_write_vdj_database"):
                painted = apply_lane_color_after_move(path, [dest])
            self.assertEqual(painted["lane"], "cyan")
            text = path.read_bytes().decode("utf-8")
            self.assertIn(f'UserColor="{LANE_COLORS["cyan"]}"', text)
            self.assertIn('Type="cue"', text)

    def test_confirmed_lane_paints_unmapped_bassy(self):
        dest = "/Users/x/Music/DJ/Music/Zouk/Bassy/moved.flac"
        set_path = "/Users/x/Music/DJ/Music/Sets/Pajamathon 2026/moved.flac"
        db_xml = (
            "<VirtualDJ_Database>\r\n"
            f'<Song FilePath="{dest}">\r\n'
            '<Infos SongLength="100" UserColor="4294967295" Cover="1" />\r\n'
            '<Poi Name="Intro" Type="cue" Num="1" />\r\n'
            "</Song>\r\n"
            f'<Song FilePath="{set_path}">\r\n'
            '<Infos SongLength="100" UserColor="4294967295" Cover="1" />\r\n'
            '<Poi Name="Intro" Type="cue" Num="1" />\r\n'
            "</Song>\r\n"
            "</VirtualDJ_Database>\r\n"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "database.xml"
            path.write_bytes(db_xml.encode("utf-8"))
            with patch("vdj_database_safety.assert_safe_to_write_vdj_database"):
                painted = apply_lane_color_after_move(path, [dest, set_path], lane="pink")
            self.assertEqual(painted["lane"], "pink")
            text = path.read_bytes().decode("utf-8")
            self.assertIn(f'UserColor="{LANE_COLORS["pink"]}"', text)
            self.assertNotIn('UserColor="4294967295"', text)

    def test_bassy_without_lane_does_not_write_white(self):
        dest = "/Users/x/Music/DJ/Music/Zouk/Bassy/moved.flac"
        db_xml = (
            "<VirtualDJ_Database>\r\n"
            f'<Song FilePath="{dest}">\r\n'
            '<Infos SongLength="100" Cover="1" />\r\n'
            '<Poi Name="Intro" Type="cue" Num="1" />\r\n'
            "</Song>\r\n"
            "</VirtualDJ_Database>\r\n"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "database.xml"
            path.write_bytes(db_xml.encode("utf-8"))
            with patch("vdj_database_safety.assert_safe_to_write_vdj_database"):
                painted = apply_lane_color_after_move(path, [dest])
            self.assertIsNone(painted["lane"])
            self.assertEqual(painted["updated"], 0)
            text = path.read_bytes().decode("utf-8")
            self.assertNotIn("UserColor", text)


class InfosRewriteTests(unittest.TestCase):
    def test_inserts_usercolor(self):
        xml = '<Infos SongLength="1" Bitrate="128" Cover="1" />'
        out = apply_user_color_to_infos(xml, LANE_COLORS["green"])
        self.assertIn(f'UserColor="{LANE_COLORS["green"]}"', out)
        self.assertIn('Cover="1"', out)

    def test_replaces_existing_usercolor(self):
        xml = '<Infos SongLength="1" UserColor="1" Cover="1" />'
        out = apply_user_color_to_infos(xml, LANE_COLORS["blue"])
        self.assertIn(f'UserColor="{LANE_COLORS["blue"]}"', out)
        self.assertNotIn('UserColor="1"', out)

    def test_removes_usercolor(self):
        xml = '<Infos SongLength="1" UserColor="1" Cover="1" />'
        out = apply_user_color_to_infos(xml, None)
        self.assertNotIn("UserColor", out)
        self.assertIn('Cover="1"', out)

    def test_current_user_color(self):
        xml = '<Infos SongLength="1" UserColor="4278255360" Cover="1" />'
        self.assertEqual(current_user_color(xml), "4278255360")


class PlanAndRewriteTests(unittest.TestCase):
    def sample_db(self) -> str:
        green = LANE_COLORS["green"]
        return (
            "<VirtualDJ_Database>\r\n"
            '<Song FilePath="/Users/x/Music/DJ/Music/Zouk/Energy/cued.flac">\r\n'
            '<Infos SongLength="100" Cover="1" />\r\n'
            '<Poi Name="Intro" Type="cue" Num="1" Color="1" />\r\n'
            "</Song>\r\n"
            '<Song FilePath="/Users/x/Music/DJ/Music/Zouk/Chill/uncued.flac">\r\n'
            '<Infos SongLength="100" UserColor="4294901760" Cover="1" />\r\n'
            '<Poi Type="automix" />\r\n'
            "</Song>\r\n"
            '<Song FilePath="/Users/x/Music/DJ/Music/House/Dark/house.flac">\r\n'
            '<Infos SongLength="100" Cover="1" />\r\n'
            '<Poi Name="Drop" Type="cue" Num="1" Color="1" />\r\n'
            "</Song>\r\n"
            '<Song FilePath="/Users/x/Music/DJ/Music/Zouk/Kakah/only-artist.flac">\r\n'
            '<Infos SongLength="100" UserColor="4278255360" Cover="1" />\r\n'
            '<Poi Name="Outro" Type="cue" Num="1" Color="1" />\r\n'
            "</Song>\r\n"
            '<Song FilePath="/Users/x/Music/DJ/Music/Sets/ZM/cued.flac">\r\n'
            '<Infos SongLength="100" Cover="1" />\r\n'
            '<Poi Name="Verse" Type="cue" Num="1" Color="1" />\r\n'
            "</Song>\r\n"
            "</VirtualDJ_Database>\r\n"
        )

    def test_plan_cued_only_skips_uncued_and_out_of_scope(self):
        plan = plan_color_updates(self.sample_db(), cued_only=True)
        paths = {item.path: item for item in plan}
        self.assertEqual(paths["/Users/x/Music/DJ/Music/Zouk/Energy/cued.flac"].lane, "green")
        self.assertEqual(
            paths["/Users/x/Music/DJ/Music/Sets/ZM/cued.flac"].lane, "green"
        )
        self.assertNotIn("/Users/x/Music/DJ/Music/Zouk/Chill/uncued.flac", paths)
        self.assertNotIn("/Users/x/Music/DJ/Music/House/Dark/house.flac", paths)
        self.assertEqual(
            paths["/Users/x/Music/DJ/Music/Zouk/Kakah/only-artist.flac"].lane,
            PENDING_LANE,
        )
        self.assertEqual(
            paths["/Users/x/Music/DJ/Music/Zouk/Kakah/only-artist.flac"].color_value,
            PENDING_COLOR,
        )

    def test_plan_does_not_paint_house_twin(self):
        db = (
            "<VirtualDJ_Database>\r\n"
            '<Song FilePath="/Users/x/Music/DJ/Music/Zouk/Energy/same.flac">\r\n'
            '<Infos SongLength="100" Cover="1" />\r\n'
            '<Poi Name="Intro" Type="cue" Num="1" Color="1" />\r\n'
            "</Song>\r\n"
            '<Song FilePath="/Users/x/Music/DJ/Music/House/Dark/same.flac">\r\n'
            '<Infos SongLength="100" Cover="1" />\r\n'
            '<Poi Name="Intro" Type="cue" Num="1" Color="1" />\r\n'
            "</Song>\r\n"
            "</VirtualDJ_Database>\r\n"
        )
        plan = plan_color_updates(db, cued_only=True)
        paths = {item.path: item for item in plan}
        self.assertEqual(
            paths["/Users/x/Music/DJ/Music/Zouk/Energy/same.flac"].lane, "green"
        )
        self.assertNotIn("/Users/x/Music/DJ/Music/House/Dark/same.flac", paths)

    def test_plan_colors_numbered_pajamathon_from_user2(self):
        db = (
            "<VirtualDJ_Database>\r\n"
            '<Song FilePath="/Users/x/Music/DJ/Music/Sets/Pajamathon 2026/004. USHER - U Got It Bad.m4a">\r\n'
            '<Tags Author="USHER" Title="U Got It Bad" User2="R&amp;B" Flag="1" />\r\n'
            '<Infos SongLength="100" Cover="1" />\r\n'
            '<Poi Name="Verse" Type="cue" Num="1" Color="1" />\r\n'
            "</Song>\r\n"
            "</VirtualDJ_Database>\r\n"
        )
        plan = plan_color_updates(db, cued_only=True)
        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0].lane, "pink")

    def test_plan_clears_leaked_legend_color_on_house_twin(self):
        db = (
            "<VirtualDJ_Database>\r\n"
            '<Song FilePath="/Users/x/Music/DJ/Music/Zouk/Energy/same.flac">\r\n'
            '<Infos SongLength="100" Cover="1" />\r\n'
            '<Poi Name="Intro" Type="cue" Num="1" Color="1" />\r\n'
            "</Song>\r\n"
            '<Song FilePath="/Users/x/Music/DJ/Music/House/Dark/same.flac">\r\n'
            f'<Infos SongLength="100" UserColor="{LANE_COLORS["green"]}" Cover="1" />\r\n'
            '<Poi Name="Intro" Type="cue" Num="1" Color="1" />\r\n'
            "</Song>\r\n"
            "</VirtualDJ_Database>\r\n"
        )
        plan = plan_color_updates(db, cued_only=True)
        house = next(
            item
            for item in plan
            if item.path.endswith("House/Dark/same.flac")
        )
        self.assertIsNone(house.lane)
        self.assertIsNone(house.color_value)

    def test_rewrite_sets_color_and_keeps_cue_markup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "database.xml"
            path.write_bytes(self.sample_db().encode("utf-8"))
            with patch("vdj_database_safety.assert_safe_to_write_vdj_database"):
                stats = rewrite_database_user_colors(path, cued_only=True)
            text = path.read_bytes().decode("utf-8")
            self.assertIn("\r\n", text)
            self.assertIn('Type="cue"', text)
            energy = text.split("Energy/cued.flac")[1].split("</Song>")[0]
            self.assertIn(f'UserColor="{LANE_COLORS["green"]}"', energy)
            uncued = text.split("Chill/uncued.flac")[1].split("</Song>")[0]
            self.assertIn('UserColor="4294901760"', uncued)
            artist = text.split("Kakah/only-artist.flac")[1].split("</Song>")[0]
            self.assertIn(f'UserColor="{PENDING_COLOR}"', artist)
            self.assertGreaterEqual(stats["updated"], 2)
            self.assertEqual(stats["song_count"], 5)


if __name__ == "__main__":
    unittest.main()
