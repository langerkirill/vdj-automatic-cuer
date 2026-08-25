import os
import tempfile
import unittest
from unittest.mock import patch

from automatic_music_cuer_gemini import AutomaticMusicCuer
from vdj_cuer.stem_evidence import (
    StemProfile,
    energy_ratio,
    is_clean_cue_press,
    is_clean_phrase_entry,
    loop_is_stable,
    loop_seam_is_clean,
    measure_stem_evidence,
    vocal_onset_on_downbeat,
)


class StemEvidenceTests(unittest.TestCase):
    def test_finds_adjacent_vdj_stems_file_from_instance(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = os.path.join(temp_dir, "song.flac")
            stems_path = f"{audio_path}.vdjstems"
            with open(stems_path, "wb"):
                pass
            cuer = AutomaticMusicCuer.__new__(AutomaticMusicCuer)

            self.assertEqual(cuer._find_vdj_stems_file(audio_path), stems_path)

    def test_section_measurement_uses_audio_after_the_boundary(self):
        profile = StemProfile.from_frames(
            [0.8] * 8 + [0.005] * 16,
            frame_seconds=0.25,
        )

        measurement = profile.measure(2.0, duration_seconds=2.0)

        self.assertEqual(measurement.level, "none")
        self.assertLess(measurement.score, 0.05)

    def test_only_asserts_components_with_strong_stem_evidence(self):
        profiles = {
            "kick": StemProfile.from_frames([0.8] * 40),
            "hihat": StemProfile.from_frames([0.5] * 40),
            "instruments": StemProfile.from_frames([0.7] * 40),
            "bass": StemProfile.from_frames([0.001] * 40),
            "vocal": StemProfile.from_frames([0.8] * 16 + [0.03] * 24),
        }

        evidence = measure_stem_evidence(
            profiles,
            timestamp=4.0,
            duration_seconds=3.0,
            model_elements=["drums", "vocals", "synth"],
        )

        self.assertEqual(evidence.elements, ["drums", "synth"])
        self.assertIn("vocals", evidence.uncertain_elements)
        self.assertEqual(evidence.activity["vocal"], "low")

    def test_loop_rejects_component_changes_inside_the_loop(self):
        profiles = {
            "kick": StemProfile.from_frames([0.8] * 40),
            "hihat": StemProfile.from_frames([0.5] * 40),
            "instruments": StemProfile.from_frames([0.7] * 40),
            "bass": StemProfile.from_frames([0.001] * 40),
            "vocal": StemProfile.from_frames([0.8] * 20 + [0.005] * 20),
        }

        self.assertFalse(
            loop_is_stable(
                profiles,
                start=0.0,
                duration_seconds=9.0,
                model_elements=["drums", "vocals", "synth"],
            )
        )

    def test_loop_seam_rejects_head_tail_level_jump(self):
        """Breathe/Halsall-class failure: components stay present, wrap jumps."""
        quiet = [0.15] * 16
        loud = [0.85] * 16
        profiles = {
            "kick": StemProfile.from_frames(quiet + [0.5] * 8 + loud),
            "hihat": StemProfile.from_frames([0.05] * 40),
            "instruments": StemProfile.from_frames(quiet + [0.4] * 8 + loud),
            "bass": StemProfile.from_frames(quiet + [0.45] * 8 + loud),
            "vocal": StemProfile.from_frames([0.001] * 40),
        }

        self.assertFalse(
            loop_seam_is_clean(
                profiles,
                start=0.0,
                duration_seconds=9.0,
                elements=["drums", "bass", "synth"],
            )
        )

    def test_loop_seam_accepts_steady_repeating_groove(self):
        steady = [0.55, 0.6, 0.5, 0.58] * 20
        profiles = {
            "kick": StemProfile.from_frames(steady),
            "hihat": StemProfile.from_frames([0.2] * 80),
            "instruments": StemProfile.from_frames(steady),
            "bass": StemProfile.from_frames(steady),
            "vocal": StemProfile.from_frames([0.001] * 80),
        }

        self.assertTrue(
            loop_seam_is_clean(
                profiles,
                start=0.0,
                duration_seconds=8.0,
                elements=["drums", "bass", "synth"],
            )
        )

    def test_loop_seam_ignores_residual_kick_on_vocal_loop(self):
        """DAANCE-class: vocal loop must not fail because kick bleed dies out."""
        vocal = [0.4] * 40
        kick_bleed = [0.25] * 10 + [0.02] * 30
        profiles = {
            "kick": StemProfile.from_frames(kick_bleed),
            "hihat": StemProfile.from_frames([0.01] * 40),
            "instruments": StemProfile.from_frames([0.35] * 40),
            "bass": StemProfile.from_frames([0.01] * 40),
            "vocal": StemProfile.from_frames(vocal),
        }

        self.assertTrue(
            loop_seam_is_clean(
                profiles,
                start=0.0,
                duration_seconds=8.0,
                elements=["vocals", "synth"],
            )
        )

    def test_hihat_without_kick_is_not_asserted_as_drums(self):
        profiles = {
            "kick": StemProfile.from_frames([0.8] * 16 + [0.03] * 24),
            "hihat": StemProfile.from_frames([0.7] * 40),
            "instruments": StemProfile.from_frames([0.6] * 40),
        }

        evidence = measure_stem_evidence(
            profiles,
            timestamp=4.0,
            duration_seconds=2.0,
            model_elements=["drums", "synth"],
        )

        self.assertNotIn("drums", evidence.elements)
        self.assertIn("drums", evidence.uncertain_elements)

    def test_energy_ratio_compares_pre_and_post_transition(self):
        profile = StemProfile.from_frames(
            [0.2] * 8 + [0.8] * 8,
            frame_seconds=0.5,
        )

        self.assertGreater(energy_ratio(profile, timestamp=4.0), 3.0)

    def test_loops_dropped_without_stem_files(self):
        """Practice: never write loops without .vdjstems stability/seam proof."""
        cuer = AutomaticMusicCuer.__new__(AutomaticMusicCuer)
        analysis = {
            "measure_changes": [
                {
                    "timestamp": 1.0,
                    "elements": ["drums"],
                    "confidence": 0.9,
                }
            ],
            "loop_segments": [
                {
                    "start": 10.0,
                    "length_beats": 16,
                    "elements": ["drums"],
                    "loop_name": "Test",
                    "confidence": 0.9,
                }
            ],
        }

        result = cuer._apply_measured_stem_activity(
            analysis, stem_files=[], bpm=120.0
        )

        self.assertEqual(result["loop_segments"], [])

    def test_stem_scan_discovers_seamless_loop_when_model_loops_fail(self):
        """Fallback scan keeps useful DJ loops after model candidates are rejected."""
        cuer = AutomaticMusicCuer.__new__(AutomaticMusicCuer)
        profiles = {
            "kick": StemProfile.from_frames([0.5] * 40, frame_seconds=0.25),
            "hihat": StemProfile.from_frames([0.05] * 40, frame_seconds=0.25),
            "instruments": StemProfile.from_frames([0.5] * 40, frame_seconds=0.25),
            "bass": StemProfile.from_frames([0.5] * 40, frame_seconds=0.25),
            "vocal": StemProfile.from_frames([0.001] * 40, frame_seconds=0.25),
        }
        analysis = {
            "measure_changes": [
                {"timestamp": 48.0, "elements": ["drums"], "confidence": 0.9}
            ],
            "loop_segments": [
                {
                    "start": 0.0,
                    "length_beats": 16,
                    "elements": ["drums", "bass", "synth"],
                    "loop_name": "Bad Model",
                    "confidence": 0.9,
                }
            ],
        }
        discovered = [
            {
                "start": 8.0,
                "length_beats": 16,
                "elements": ["bass", "synth"],
                "loop_name": "Synth Loop",
                "color": "blue",
                "role": "loop",
                "confidence": 0.9,
                "assertion_source": "stem_scan_loop",
            }
        ]

        cuer._track_audio_cache = type(
            "Cache",
            (),
            {"get_or_load_stem_profiles": staticmethod(lambda files: profiles)},
        )()
        with patch(
            "vdj_cuer.stems.loop_is_stable", return_value=False
        ), patch.object(
            cuer, "_discover_stem_validated_loops", return_value=discovered
        ), patch.object(
            cuer, "_loop_discovery_song_length", return_value=60.0
        ):
            result = cuer._apply_measured_stem_activity(
                analysis,
                stem_files=[("kick", "/tmp/kick.m4a")],
                bpm=120.0,
            )

        self.assertEqual(len(result["loop_segments"]), 1)
        self.assertEqual(
            result["loop_segments"][0].get("assertion_source"), "stem_scan_loop"
        )

    def test_stem_scan_merges_intro_melodic_loop_with_model_loops(self):
        """heal something-class: keep model loop and add early melodic 8-count."""
        cuer = AutomaticMusicCuer.__new__(AutomaticMusicCuer)
        profiles = {
            "kick": StemProfile.from_frames([0.5] * 40, frame_seconds=0.25),
            "hihat": StemProfile.from_frames([0.05] * 40, frame_seconds=0.25),
            "instruments": StemProfile.from_frames([0.5] * 40, frame_seconds=0.25),
            "bass": StemProfile.from_frames([0.5] * 40, frame_seconds=0.25),
            "vocal": StemProfile.from_frames([0.001] * 40, frame_seconds=0.25),
        }
        analysis = {
            "measure_changes": [
                {"timestamp": 0.0, "elements": ["synth"], "confidence": 0.9},
                {"timestamp": 32.0, "elements": ["drums", "vocals"], "confidence": 0.9},
            ],
            "loop_segments": [
                {
                    "start": 32.0,
                    "length_beats": 16,
                    "elements": ["drums", "bass", "synth"],
                    "loop_name": "Rhythm Section Loop",
                    "confidence": 0.9,
                }
            ],
        }
        discovered = [
            {
                "start": 0.0,
                "length_beats": 8,
                "elements": ["synth"],
                "loop_name": "Melodic Loop",
                "color": "blue",
                "role": "loop",
                "confidence": 0.9,
                "assertion_source": "stem_scan_loop",
            }
        ]
        cuer._track_audio_cache = type(
            "Cache",
            (),
            {"get_or_load_stem_profiles": staticmethod(lambda files: profiles)},
        )()
        with patch(
            "vdj_cuer.stems.loop_is_stable", return_value=True
        ), patch(
            "vdj_cuer.stems.loop_seam_is_clean", return_value=True
        ), patch(
            "vdj_cuer.stems.measure_stem_evidence",
            return_value=type(
                "E",
                (),
                {
                    "activity": {"kick": "medium", "instruments": "medium"},
                    "scores": {"kick": 0.5, "instruments": 0.5},
                    "elements": ["drums", "synth"],
                    "uncertain_elements": [],
                    "confidence": 0.5,
                },
            )(),
        ), patch.object(
            cuer, "_discover_stem_validated_loops", return_value=discovered
        ), patch.object(
            cuer, "_loop_discovery_song_length", return_value=60.0
        ), patch(
            "vdj_cuer.stems._stem_gate_confidence", return_value=0.9
        ):
            result = cuer._apply_measured_stem_activity(
                analysis,
                stem_files=[("kick", "/tmp/kick.m4a")],
                bpm=120.0,
            )

        starts = sorted(float(loop["start"]) for loop in result["loop_segments"])
        self.assertIn(0.0, starts)
        self.assertGreaterEqual(len(result["loop_segments"]), 2)

    def test_loop_discovery_label_prefers_melodic_for_instrument_only(self):
        cuer = AutomaticMusicCuer.__new__(AutomaticMusicCuer)
        self.assertEqual(cuer._loop_discovery_label(["synth"]), "Melodic")
        self.assertEqual(cuer._loop_discovery_label(["drums"]), "Drums")

    def test_phrase_entry_rejects_pre_chorus_words_into_chorus(self):
        """Need it Bad-class: words already running before the chorus hit."""
        # Quiet then loud would be clean; loud-before-loud is mid-phrase.
        vocal = [0.7] * 20 + [0.9] * 20
        profiles = {
            "vocal": StemProfile.from_frames(vocal, frame_seconds=0.25),
            "kick": StemProfile.from_frames([0.5] * 40, frame_seconds=0.25),
            "instruments": StemProfile.from_frames([0.4] * 40, frame_seconds=0.25),
            "bass": StemProfile.from_frames([0.4] * 40, frame_seconds=0.25),
            "hihat": StemProfile.from_frames([0.1] * 40, frame_seconds=0.25),
        }
        # t=5s is mid frames (index ~20) where pre-window is still loud
        self.assertFalse(
            is_clean_phrase_entry(
                profiles, timestamp=5.0, elements=["drums", "vocals", "synth"]
            )
        )

    def test_phrase_entry_allows_clean_vocal_attack(self):
        vocal = [0.02] * 24 + [0.8] * 16
        profiles = {
            "vocal": StemProfile.from_frames(vocal, frame_seconds=0.25),
            "kick": StemProfile.from_frames([0.5] * 40, frame_seconds=0.25),
            "instruments": StemProfile.from_frames([0.4] * 40, frame_seconds=0.25),
            "bass": StemProfile.from_frames([0.4] * 40, frame_seconds=0.25),
            "hihat": StemProfile.from_frames([0.1] * 40, frame_seconds=0.25),
        }
        self.assertTrue(
            is_clean_phrase_entry(
                profiles, timestamp=6.0, elements=["drums", "vocals"]
            )
        )
        self.assertTrue(vocal_onset_on_downbeat(profiles, 6.0, beat_seconds=0.5))

    def test_vocal_onset_on_1_vs_already_singing_through(self):
        onset = {
            "vocal": StemProfile.from_frames(
                [0.02] * 24 + [0.8] * 16, frame_seconds=0.25
            )
        }
        through = {
            "vocal": StemProfile.from_frames([0.7] * 40, frame_seconds=0.25)
        }
        silent = {
            "vocal": StemProfile.from_frames([0.01] * 40, frame_seconds=0.25)
        }
        self.assertTrue(vocal_onset_on_downbeat(onset, 6.0, beat_seconds=0.5))
        self.assertFalse(vocal_onset_on_downbeat(through, 5.0, beat_seconds=0.5))
        self.assertFalse(vocal_onset_on_downbeat(silent, 4.0, beat_seconds=0.5))
        self.assertFalse(vocal_onset_on_downbeat({}, 4.0, beat_seconds=0.5))

    def test_phrase_entry_rejects_instrumental_when_vocal_is_noisy_at_press(self):
        """Havana-class: Groove/synth cue that jumps into a singing stem."""
        vocal = [0.9] * 40
        profiles = {
            "vocal": StemProfile.from_frames(vocal, frame_seconds=0.25),
            "kick": StemProfile.from_frames([0.5] * 40, frame_seconds=0.25),
            "instruments": StemProfile.from_frames([0.5] * 40, frame_seconds=0.25),
            "bass": StemProfile.from_frames([0.5] * 40, frame_seconds=0.25),
            "hihat": StemProfile.from_frames([0.1] * 40, frame_seconds=0.25),
        }
        self.assertFalse(is_clean_cue_press(profiles, timestamp=5.0))
        self.assertFalse(
            is_clean_phrase_entry(profiles, timestamp=5.0, elements=["drums", "synth"])
        )

    def test_phrase_entry_allows_instrumental_when_vocal_is_silent(self):
        vocal = [0.01] * 40
        profiles = {
            "vocal": StemProfile.from_frames(vocal, frame_seconds=0.25),
            "kick": StemProfile.from_frames([0.5] * 40, frame_seconds=0.25),
            "instruments": StemProfile.from_frames([0.5] * 40, frame_seconds=0.25),
            "bass": StemProfile.from_frames([0.5] * 40, frame_seconds=0.25),
            "hihat": StemProfile.from_frames([0.1] * 40, frame_seconds=0.25),
        }
        self.assertTrue(is_clean_cue_press(profiles, timestamp=5.0))
        self.assertTrue(
            is_clean_phrase_entry(profiles, timestamp=5.0, elements=["drums", "synth"])
        )

    def test_cue_press_rejects_short_vocal_burst_before_the_one(self):
        """2s average stays low; last ~200ms before the 1 is already singing."""
        # 0.25s frames; t=5.0 is frame 20. Frames 18-19 are the press lead-in.
        vocal = [0.02] * 18 + [0.85, 0.85] + [0.08] * 20
        profiles = {
            "vocal": StemProfile.from_frames(vocal, frame_seconds=0.25),
            "kick": StemProfile.from_frames([0.5] * 40, frame_seconds=0.25),
            "instruments": StemProfile.from_frames([0.4] * 40, frame_seconds=0.25),
            "bass": StemProfile.from_frames([0.4] * 40, frame_seconds=0.25),
            "hihat": StemProfile.from_frames([0.1] * 40, frame_seconds=0.25),
        }
        self.assertFalse(is_clean_cue_press(profiles, timestamp=5.0))
        self.assertFalse(
            is_clean_phrase_entry(
                profiles, timestamp=5.0, elements=["drums", "vocals"]
            )
        )

    def test_havana_kizomba_keeps_clean_press_and_drops_noisy_ones(self):
        """Kaysha/Jacira Havana: remaining good 1s stay; singing-on-press fails."""
        audio = (
            "/Users/kirilllanger/Music/DJ/Music/Cues/Add Cues/Pajamathon/"
            "01 Havana - Kizomba.m4a"
        )
        stems = f"{audio}.vdjstems"
        if not os.path.isfile(audio) or not os.path.isfile(stems):
            self.skipTest("Havana Kizomba + .vdjstems not on this machine")

        import tempfile

        from vdj_cuer.stems import StemMixin
        from vdj_cuer.stem_evidence import load_stem_profiles

        mixin = StemMixin()
        with tempfile.TemporaryDirectory() as tmp:
            files = mixin._extract_vdj_stems(stems, tmp)
            profiles = load_stem_profiles(files)

        # Clean vocal entry the user can jump to.
        self.assertTrue(is_clean_cue_press(profiles, 9.999679))
        # Intro instrumental 1.
        self.assertTrue(is_clean_cue_press(profiles, 2.173555))
        # Mid-song 1s where the vocal is already sounding.
        self.assertFalse(is_clean_cue_press(profiles, 33.478))
        self.assertFalse(is_clean_cue_press(profiles, 70.000))
        # Synth-labeled body 1 with vocal noise — old gate used to allow this.
        self.assertFalse(
            is_clean_phrase_entry(
                profiles, timestamp=70.000, elements=["drums", "synth"]
            )
        )

    def test_loop_length_capped_on_slow_tempo(self):
        """Valley Of The Winds-class: 32 beats at 75 BPM is ~26s — too long."""
        from vdj_cuer.stems import _cap_loop_length_beats, _max_loop_beats_for_tempo

        beat_duration = 60.0 / 75.0  # 0.8s
        self.assertEqual(_max_loop_beats_for_tempo(beat_duration), 16)
        self.assertEqual(_cap_loop_length_beats(32, beat_duration), 16)
        self.assertEqual(_cap_loop_length_beats(16, beat_duration), 16)
        # Fast track can keep 32 (~12.8s at 150 BPM)
        self.assertEqual(_cap_loop_length_beats(32, 60.0 / 150.0), 32)


if __name__ == "__main__":
    unittest.main()
