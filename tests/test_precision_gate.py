import unittest

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

        self.assertEqual([item["timestamp"] for item in result["measure_changes"]], [30.0])
        self.assertEqual([item["start"] for item in result["loop_segments"]], [60.0])
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


if __name__ == "__main__":
    unittest.main()
