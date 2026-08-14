"""Top-k cue proposal: spacing + clean-entry gate."""

from __future__ import annotations

import unittest

from vdj_cuer.ml.propose import propose_cues


class ProposeCuesTests(unittest.TestCase):
    def test_caps_at_six_and_respects_spacing(self) -> None:
        # 120 BPM → 12 beats = 6s. Bars every 2s.
        rows = [
            {
                "timestamp": i * 2.0,
                "score": 1.0 - i * 0.01,
                "clean_entry": 1.0,
                "elements": ["drums"],
            }
            for i in range(20)
        ]
        picked = propose_cues(rows, bpm=120.0, max_cues=6, min_spacing_beats=12.0)
        self.assertLessEqual(len(picked), 6)
        times = [float(r["timestamp"]) for r in picked]
        for a, b in zip(times, times[1:]):
            self.assertGreaterEqual(b - a, 6.0 - 1e-6)

    def test_rejects_unclean_press(self) -> None:
        rows = [
            {
                "timestamp": 0.0,
                "score": 0.99,
                "clean_entry": 0.0,
                "elements": ["vocals"],
            },
            {
                "timestamp": 8.0,
                "score": 0.5,
                "clean_entry": 1.0,
                "elements": ["drums"],
            },
        ]
        picked = propose_cues(rows, bpm=120.0, max_cues=6)
        self.assertEqual(len(picked), 1)
        self.assertEqual(picked[0]["timestamp"], 8.0)

    def test_empty_when_no_rows(self) -> None:
        self.assertEqual(propose_cues([], bpm=120.0), [])


if __name__ == "__main__":
    unittest.main()
