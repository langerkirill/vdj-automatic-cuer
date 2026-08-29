"""Cue 1 must be the disk 1. Earlier grid 1s are dropped."""

from __future__ import annotations

import unittest
from dataclasses import dataclass

from vdj_cuer.beatgrid_alignment import BeatgridAlignmentMixin
from vdj_cuer.common import is_on_phrase_one, quantize_to_phrase_one
from vdj_cuer.user_one import (
    has_marker_on_user_one,
    phrase_grid_offset,
    pin_markers_to_user_one,
    ensure_loops_on_user_one,
)


@dataclass
class _Poi:
    position: float
    name: str = "cue"
    length_beats: float = 16.0


class UserOneTests(unittest.TestCase):
    def test_rodrigo_drops_cues_before_phase(self) -> None:
        phase = 24.028934
        kept = pin_markers_to_user_one(
            phase,
            [_Poi(0.029670, "intro"), _Poi(12.029302, "synth"), _Poi(24.028934, "1")],
        )
        self.assertEqual([p.name for p in kept], ["1"])
        self.assertTrue(has_marker_on_user_one(phase, kept))

    def test_come_back_keeps_phase_and_later_ones(self) -> None:
        phase = 0.030522
        kept = pin_markers_to_user_one(
            phase,
            [_Poi(0.030522, "1"), _Poi(10.697194, "section"), _Poi(21.363866, "entry")],
        )
        self.assertEqual([p.name for p in kept], ["1", "section", "entry"])

    def test_empty_when_all_before_one(self) -> None:
        kept = pin_markers_to_user_one(24.0, [_Poi(0.03), _Poi(12.0)])
        self.assertEqual(kept, [])
        self.assertFalse(has_marker_on_user_one(24.0, kept))

    def test_vocal_break_early_snaps_forward_to_the_one(self) -> None:
        bpm = 90.0
        phase = 0.030522
        beat = 60.0 / bpm
        phrase = beat * 16.0
        loud_one = phase + 5 * phrase
        early = loud_one - beat * 0.6
        snapped = quantize_to_phrase_one(early, bpm, phase)
        self.assertTrue(is_on_phrase_one(snapped, bpm, phase))
        self.assertAlmostEqual(snapped, loud_one, places=4)
        self.assertGreater(snapped, early)

    def test_one_bar_early_still_the_phrase_one(self) -> None:
        bpm = 90.0
        phase = 0.030522
        beat = 60.0 / bpm
        phrase = beat * 16.0
        loud_one = phase + 8 * phrase
        early = loud_one - 4 * beat
        snapped = quantize_to_phrase_one(early, bpm, phase)
        self.assertAlmostEqual(snapped, loud_one, places=4)

    def test_just_late_stays_on_this_phrase_one(self) -> None:
        bpm = 90.0
        phase = 0.030522
        beat = 60.0 / bpm
        phrase = beat * 16.0
        this_one = phase + 8 * phrase
        late = this_one + 1.05 * beat
        snapped = quantize_to_phrase_one(late, bpm, phase)
        self.assertAlmostEqual(snapped, this_one, places=4)
        self.assertNotAlmostEqual(snapped, this_one + phrase, places=3)

    def test_refuse_generic_section_name(self) -> None:
        from vdj_cuer.analysis_postprocess import AnalysisPostprocessMixin

        class _H(AnalysisPostprocessMixin):
            pass

        self.assertEqual(
            _H._refuse_generic_section("Section", ["drums", "bass"]),
            "Groove",
        )
        self.assertEqual(
            _H._refuse_generic_section("Rhythm Section", ["drums"]),
            "Beat Entry",
        )
        self.assertEqual(
            _H._refuse_generic_section("Vocal Break", ["vocals"]),
            "Vocal Break",
        )

    def test_come_back_phrase_targets(self) -> None:
        bpm = 90.0
        phase = 0.030522
        origin = phrase_grid_offset(phase, bpm)
        self.assertAlmostEqual(origin, phase, places=5)
        q = BeatgridAlignmentMixin._quantize_grid_time
        # Yellow [1] every 16 beats from Phase.
        self.assertAlmostEqual(q(10.697194, bpm, origin, 16), 10.697189, places=3)
        self.assertAlmostEqual(q(80.030562, bpm, origin, 16), 85.363855, places=3)
        self.assertAlmostEqual(q(90.697234, bpm, origin, 16), 96.030522, places=3)

    def test_rodrigo_phrase_stays_on_disk_one(self) -> None:
        bpm = 80.0
        phase = 24.028934
        origin = phrase_grid_offset(phase, bpm)
        self.assertAlmostEqual(origin, phase, places=5)
        q = BeatgridAlignmentMixin._quantize_grid_time
        self.assertAlmostEqual(q(45.028290, bpm, origin, 16), 48.028934, places=3)
        self.assertAlmostEqual(q(129.025714, bpm, origin, 16), 132.028934, places=3)


    def test_rodrigo_relocates_intro_loops_onto_disk_one(self) -> None:
        phase = 24.028934
        bpm = 80.0
        loops = [
            _Poi(0.029670, "Intro Synth", 16.0),
            _Poi(12.029302, "Synth", 16.0),
            _Poi(180.02415, "Synth late", 8.0),
        ]
        got = ensure_loops_on_user_one(phase, loops, bpm=bpm, song_length=198.06)
        self.assertEqual(len(got), 2)
        self.assertAlmostEqual(got[0].position, phase, places=4)
        self.assertEqual(got[0].length_beats, 8.0)
        self.assertAlmostEqual(got[1].position, phase + (60.0 / bpm) * 32.0, places=4)
        self.assertEqual(got[1].length_beats, 8.0)
        self.assertTrue(has_marker_on_user_one(phase, got))

    def test_come_back_keeps_existing_post_one_loops(self) -> None:
        phase = 0.030522
        bpm = 90.0
        loops = [_Poi(0.030522, "a", 8.0), _Poi(184.030614, "b", 8.0)]
        got = ensure_loops_on_user_one(phase, loops, bpm=bpm, song_length=200.0)
        self.assertAlmostEqual(got[0].position, phase, places=4)
        self.assertEqual(len(got), 2)

    def test_loop_at_same_time_as_cue_inherits_cue_color(self) -> None:
        from vdj_cuer.cue_writer import PreparedPoi, _tint_loops_from_cues

        cues = [
            PreparedPoi("cue", "Intro Groove", 0.008, "green", "4278255360", ["drums", "synth"]),
            PreparedPoi(
                "cue",
                "Beat Entry",
                19.208,
                "yellow",
                "4294967040",
                ["drums", "vocals", "synth"],
            ),
        ]
        loops = [
            PreparedPoi("loop", "Intro Loop", 0.008, "green", "4278255360", ["drums", "synth"], 8.0),
            PreparedPoi("loop", "Beat Entry Loop", 19.208, "green", "4278255360", ["drums", "synth"], 8.0),
        ]
        got = _tint_loops_from_cues(cues, loops)
        self.assertEqual(got[0].color_name, "green")
        self.assertEqual(got[1].color_name, "yellow")
        self.assertIn("vocals", got[1].elements)

        from vdj_cuer.cue_writer import _share_frequency_colors

        cues2, loops2 = _share_frequency_colors(cues, loops)
        self.assertEqual(cues2[1].color_name, "yellow")
        self.assertEqual(loops2[1].color_name, "yellow")
        # Vocal loop upgrades a green cue on the same 1.
        green_cue = [
            PreparedPoi("cue", "Beat Entry", 19.208, "green", "4278255360", ["drums", "synth"]),
        ]
        yellow_loop = [
            PreparedPoi(
                "loop",
                "Verse Groove Loop",
                19.208,
                "yellow",
                "4294967040",
                ["drums", "vocals", "synth"],
                8.0,
            ),
        ]
        cues3, loops3 = _share_frequency_colors(green_cue, yellow_loop)
        self.assertEqual(cues3[0].color_name, "yellow")
        self.assertEqual(loops3[0].color_name, "yellow")
        # Disk 1 intro stays green even if a vocal loop was cloned onto it.
        intro_cue = [
            PreparedPoi("cue", "Intro Groove", 0.008, "green", "4278255360", ["drums", "synth"]),
        ]
        cloned = [
            PreparedPoi(
                "loop",
                "Verse Groove Loop",
                0.008,
                "yellow",
                "4294967040",
                ["drums", "vocals"],
                8.0,
            ),
        ]
        cues4, loops4 = _share_frequency_colors(
            intro_cue, cloned, disk_one=0.008
        )
        self.assertEqual(cues4[0].color_name, "green")
        self.assertEqual(loops4[0].color_name, "green")

    def test_cloned_loops_get_distinct_names(self) -> None:
        from vdj_cuer.cue_writer import PreparedPoi, _unique_poi_names

        loops = [
            PreparedPoi("loop", "Intro Synth and Groove Loop", 0.001, "cyan", "#0ff", ["loop"], 8.0),
            PreparedPoi("loop", "Intro Synth and Groove Loop", 12.145, "cyan", "#0ff", ["loop"], 8.0),
        ]
        got = _unique_poi_names(loops)
        self.assertEqual(got[0].name, "Intro Synth and Groove Loop")
        self.assertEqual(got[1].name, "Beat Entry Loop")
        self.assertNotIn("2", got[1].name)

    def test_introl_becomes_intro_loop(self) -> None:
        from vdj_cuer.cue_writer import _with_loop_suffix

        self.assertEqual(_with_loop_suffix("Intro Synth Intro"), "Intro Synth Intro Loop")
        self.assertEqual(_with_loop_suffix("Intro"), "Intro Loop")
        self.assertEqual(_with_loop_suffix("synth"), "synth Loop")
        self.assertEqual(_with_loop_suffix("Groove Loop"), "Groove Loop")
        self.assertNotIn("introl", _with_loop_suffix("Intro").lower())

    def test_beat_entry_dup_gets_a_part_name(self) -> None:
        from vdj_cuer.cue_writer import PreparedPoi, _unique_poi_names

        cues = [
            PreparedPoi("cue", "Beat Entry", 23.0, "green", "#0f0", ["drums"]),
            PreparedPoi("cue", "Beat Entry", 158.5, "yellow", "#ff0", ["drums"]),
            PreparedPoi("cue", "Beat Entry", 169.8, "green", "#0f0", ["drums"]),
        ]
        got = _unique_poi_names(cues)
        self.assertEqual([c.name for c in got], ["Beat Entry", "Build", "Drop"])

    def test_duplicate_vocal_mix_gets_a_part_name(self) -> None:
        from vdj_cuer.cue_writer import PreparedPoi, _unique_poi_names

        cues = [
            PreparedPoi("cue", "Vocal Mix", 36.434, "yellow", "#ff0", ["vocals", "drums"]),
            PreparedPoi("cue", "Vocal Mix", 60.723, "yellow", "#ff0", ["vocals", "drums"]),
        ]
        got = _unique_poi_names(cues)
        names = [c.name for c in got]
        self.assertEqual(len(set(names)), 2)
        self.assertTrue(all(" 2" not in n and " 3" not in n for n in names))
        self.assertNotIn("Section", "".join(names))

    def test_create_loop_name_does_not_glue_l(self) -> None:
        from vdj_cuer.analysis_postprocess import AnalysisPostprocessMixin

        class _H(AnalysisPostprocessMixin):
            pass

        host = _H()
        name = host.create_loop_name(["synth"])
        self.assertNotEqual(name.lower()[-1:], "l")
        self.assertNotIn("introl", name.lower())
        self.assertNotIn("synthl", name.lower())

    def test_element_label_never_says_section(self) -> None:
        from vdj_cuer.analysis_postprocess import AnalysisPostprocessMixin

        class _H(AnalysisPostprocessMixin):
            pass

        host = _H()
        for elements in (
            ["drums", "bass"],
            ["drums"],
            ["synth"],
            ["vocals"],
            ["vocals", "drums"],
            ["bass"],
        ):
            label = host._element_label(elements)
            self.assertNotIn("section", label.lower(), elements)


if __name__ == "__main__":
    unittest.main()
