import unittest

from vdj_cuer.common import is_on_downbeat, is_on_phrase_one
from vdj_cuer.precision_gate import apply_precision_gate


def cue(timestamp, confidence=0.9, elements=None, name="Section"):
    return {
        "timestamp": timestamp,
        "confidence": confidence,
        "elements": ["drums"] if elements is None else elements,
        "cue_name": name,
    }


def loop(start, confidence=0.9, elements=None, beats=16):
    return {
        "start": start,
        "confidence": confidence,
        "elements": ["drums"] if elements is None else elements,
        "length_beats": beats,
        "loop_name": "Groovel",
    }


class PrecisionGateTests(unittest.TestCase):
    def test_rejects_low_confidence_and_componentless_assertions(self):
        analysis = {
            "measure_changes": [
                cue(10.0, confidence=0.69),
                cue(20.0, elements=[]),
                cue(30.0),
            ],
            "loop_segments": [
                loop(40.0, confidence=0.50),
                loop(50.0, elements=[]),
                loop(60.0),
            ],
        }

        result = apply_precision_gate(analysis, bpm=120.0)

        self.assertEqual(
            [item["timestamp"] for item in result["measure_changes"]], [32.0]
        )
        self.assertEqual([item["start"] for item in result["loop_segments"]], [64.0])
        self.assertEqual(result["precision_gate"]["rejected"]["low_confidence_cues"], 1)
        self.assertEqual(result["precision_gate"]["rejected"]["invalid_cues"], 1)

    def test_keeps_only_best_cue_that_snaps_to_same_downbeat(self):
        analysis = {
            "measure_changes": [
                cue(31.6, confidence=0.80, name="Early Guess"),
                cue(32.4, confidence=0.95, name="Best Guess"),
            ],
            "loop_segments": [],
        }

        result = apply_precision_gate(
            analysis, bpm=120.0, beatgrid_offset=0.0
        )

        self.assertEqual(len(result["measure_changes"]), 1)
        self.assertEqual(result["measure_changes"][0]["cue_name"], "Best Guess")
        self.assertEqual(result["precision_gate"]["rejected"]["duplicate_cues"], 1)

    def test_hard_fails_cues_not_on_the_one(self):
        """Sozinho-class: a cue on beat 2 of the Phase grid is dropped."""
        phase = 22.846775
        bpm = 84.0
        beat = 60.0 / bpm
        on_two = phase + beat
        on_one = phase
        analysis = {
            "measure_changes": [
                cue(on_two, name="Off"),
                cue(on_one, name="On"),
            ],
            "loop_segments": [loop(on_two), loop(on_one + 16 * beat)],
        }
        # After snap, on_two becomes nearest 1 (phase or phase+bar).
        # A time *exactly* one beat after Phase snaps to Phase or Phase+bar.
        result = apply_precision_gate(analysis, bpm=bpm, beatgrid_offset=phase)
        self.assertGreaterEqual(len(result["measure_changes"]), 1)
        for marker in result["measure_changes"]:
            self.assertTrue(
                is_on_phrase_one(float(marker["timestamp"]), bpm, phase)
            )
        for marker in result["loop_segments"]:
            self.assertTrue(is_on_phrase_one(float(marker["start"]), bpm, phase))

    def test_rejects_non_finite_times_and_unsupported_loop_lengths(self):
        analysis = {
            "measure_changes": [cue(float("nan")), cue(-1.0)],
            "loop_segments": [loop(10.0, beats=12), loop(float("inf"))],
        }

        result = apply_precision_gate(analysis)

        self.assertEqual(result["measure_changes"], [])
        self.assertEqual(result["loop_segments"], [])
        self.assertEqual(result["precision_gate"]["rejected"]["invalid_cues"], 2)
        self.assertEqual(result["precision_gate"]["rejected"]["invalid_loops"], 2)

    def test_eight_beats_is_a_bar_one_not_a_phrase_one(self) -> None:
        bpm = 90.0
        phase = 0.030522
        beat = 60.0 / bpm
        two_bars = phase + 8 * beat
        four_bars = phase + 16 * beat
        self.assertTrue(is_on_downbeat(two_bars, bpm, phase))
        self.assertFalse(is_on_phrase_one(two_bars, bpm, phase))
        self.assertTrue(is_on_phrase_one(four_bars, bpm, phase))

    def test_mid_phrase_bar_snaps_to_phrase_one(self) -> None:
        """Come back: a 4-beat 1 that is not a yellow [1] must move to the phrase 1."""
        phase = 0.030522
        bpm = 90.0
        beat = 60.0 / bpm
        two_bars = phase + 8 * beat
        next_phrase = phase + 16 * beat
        analysis = {
            "measure_changes": [cue(two_bars, name="Groove")],
            "loop_segments": [loop(two_bars, beats=8)],
        }
        result = apply_precision_gate(
            analysis, bpm=bpm, beatgrid_offset=phase
        )
        self.assertEqual(len(result["measure_changes"]), 1)
        self.assertAlmostEqual(
            result["measure_changes"][0]["timestamp"], next_phrase, places=4
        )
        self.assertTrue(
            is_on_phrase_one(
                result["measure_changes"][0]["timestamp"], bpm, phase
            )
        )
        self.assertAlmostEqual(result["loop_segments"][0]["start"], next_phrase, places=4)


if __name__ == "__main__":
    unittest.main()
