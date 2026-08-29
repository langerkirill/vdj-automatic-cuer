"""Drive the shipped preroll, beat-seek, crate-label, and color-meaning helpers."""

from __future__ import annotations

import json
import subprocess
import unittest

try:
    from tests.js_assets import UI_STATIC
except ImportError:
    from js_assets import UI_STATIC


def _node(script: str) -> dict:
    proc = subprocess.run(
        ["node", "-e", script],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"node failed ({proc.returncode}): {proc.stderr or proc.stdout}"
        )
    return json.loads(proc.stdout.strip() or "{}")


def _req(name: str) -> str:
    return json.dumps(str(UI_STATIC / name))


class CuePrerollHelperTests(unittest.TestCase):
    def test_preroll_is_four_beats_when_bpm_known(self) -> None:
        out = _node(
            f"""
const T = require({_req("transport.js")});
console.log(JSON.stringify({{
  at120: T.cuePrerollTime(10, 120),
  seconds: T.cuePrerollSeconds(120),
  fallback: T.cuePrerollSeconds(null),
  nearZero: T.cuePrerollTime(0.5, 120),
  nearZeroUnknown: T.cuePrerollTime(0.5, 0),
}}));
"""
        )
        # 4 beats at 120 BPM = 2.0s, so cue 10 lands at 8.
        self.assertAlmostEqual(out["seconds"], 2.0, places=4)
        self.assertAlmostEqual(out["at120"], 8.0, places=4)
        self.assertAlmostEqual(out["fallback"], 2.0, places=4)
        self.assertGreaterEqual(out["nearZero"], 0.0)
        self.assertGreaterEqual(out["nearZeroUnknown"], 0.0)
        self.assertAlmostEqual(out["nearZero"], 0.0, places=4)
        self.assertAlmostEqual(out["nearZeroUnknown"], 0.0, places=4)

    def test_preroll_never_goes_negative(self) -> None:
        out = _node(
            f"""
const T = require({_req("transport.js")});
const cases = [0, 0.1, 0.5, 1, 1.9].map((cue) => ({{
  cue,
  known: T.cuePrerollTime(cue, 120),
  unknown: T.cuePrerollTime(cue, null),
}}));
console.log(JSON.stringify({{ cases }}));
"""
        )
        for row in out["cases"]:
            self.assertGreaterEqual(row["known"], 0.0)
            self.assertGreaterEqual(row["unknown"], 0.0)


class BeatBarSeekHelperTests(unittest.TestCase):
    def test_left_from_zero_stays_zero(self) -> None:
        out = _node(
            f"""
const T = require({_req("transport.js")});
console.log(JSON.stringify({{
  known: T.beatSeekTime(0, 120, {{ direction: -1, bar: false, duration: 200 }}),
  unknown: T.beatSeekTime(0, null, {{ direction: -1, bar: false, duration: 200 }}),
  bar: T.beatSeekTime(0, 128, {{ direction: -1, bar: true, duration: 200 }}),
}}));
"""
        )
        self.assertEqual(out["known"], 0)
        self.assertEqual(out["unknown"], 0)
        self.assertEqual(out["bar"], 0)

    def test_shift_seek_is_four_times_unshifted_when_bpm_known(self) -> None:
        out = _node(
            f"""
const T = require({_req("transport.js")});
const bpm = 120;
const start = 20;
const beat = T.beatSeekTime(start, bpm, {{ direction: 1, bar: false, duration: 200 }});
const bar = T.beatSeekTime(start, bpm, {{ direction: 1, bar: true, duration: 200 }});
const left = T.beatSeekTime(start, bpm, {{ direction: -1, bar: false, duration: 200 }});
const clampEnd = T.beatSeekTime(199.9, bpm, {{ direction: 1, bar: true, duration: 200 }});
console.log(JSON.stringify({{
  beatStep: beat - start,
  barStep: bar - start,
  ratio: (bar - start) / (beat - start),
  left,
  clampEnd,
}}));
"""
        )
        self.assertAlmostEqual(out["beatStep"], 0.5, places=4)
        self.assertAlmostEqual(out["barStep"], 2.0, places=4)
        self.assertAlmostEqual(out["ratio"], 4.0, places=4)
        self.assertAlmostEqual(out["left"], 19.5, places=4)
        self.assertAlmostEqual(out["clampEnd"], 200.0, places=4)

    def test_unknown_bpm_uses_two_and_eight_second_steps(self) -> None:
        out = _node(
            f"""
const T = require({_req("transport.js")});
const start = 30;
const beat = T.beatSeekTime(start, 0, {{ direction: 1, bar: false, duration: 200 }});
const bar = T.beatSeekTime(start, undefined, {{ direction: 1, bar: true, duration: 200 }});
console.log(JSON.stringify({{ beat: beat - start, bar: bar - start }}));
"""
        )
        self.assertAlmostEqual(out["beat"], 2.0, places=4)
        self.assertAlmostEqual(out["bar"], 8.0, places=4)


class CrateBpmKeyLabelTests(unittest.TestCase):
    def test_labels_from_track_like_object(self) -> None:
        out = _node(
            f"""
const T = require({_req("transport.js")});
const full = T.crateBpmKeyLabels({{ cues: {{ bpm: 128.4, key: "Am", camelot: "8A" }} }});
const keyOnly = T.crateBpmKeyLabels({{ cues: {{ key: "F#m" }} }});
const missing = T.crateBpmKeyLabels({{ name: "Untitled.flac" }});
const empty = T.crateBpmKeyLabels(null);
console.log(JSON.stringify({{ full, keyOnly, missing, empty }}));
"""
        )
        self.assertIn("128", out["full"]["bpm"])
        self.assertRegex(out["full"]["bpm"], r"BPM|128")
        self.assertIn("Am", out["full"]["key"])
        self.assertIn("8A", out["full"]["key"])
        self.assertIn("F#m", out["keyOnly"]["key"])
        self.assertIn("11A", out["keyOnly"]["key"])
        self.assertEqual(out["missing"]["bpm"], "—")
        self.assertEqual(out["missing"]["key"], "—")
        self.assertEqual(out["empty"]["bpm"], "—")
        self.assertEqual(out["empty"]["key"], "—")


class CueColorMeaningTests(unittest.TestCase):
    def test_shipped_color_language(self) -> None:
        out = _node(
            f"""
const T = require({_req("transport.js")});
const colors = ["blue", "green", "purple", "yellow", "orange"];
const meanings = {{}};
for (const color of colors) meanings[color] = T.cueColorMeaning(color);
console.log(JSON.stringify({{
  meanings,
  unknown: T.cueColorMeaning("pink"),
}}));
"""
        )
        blue = out["meanings"]["blue"].lower()
        green = out["meanings"]["green"].lower()
        purple = out["meanings"]["purple"].lower()
        yellow = out["meanings"]["yellow"].lower()
        orange = out["meanings"]["orange"].lower()
        self.assertIn("melodic", blue)
        self.assertIn("melodic", green)
        self.assertIn("drum", green)
        self.assertIn("drum", purple)
        self.assertNotIn("vocal", purple)
        self.assertIn("drum", yellow)
        self.assertIn("vocal", yellow)
        self.assertIn("vocal", orange)
        self.assertTrue(out["unknown"])


if __name__ == "__main__":
    unittest.main()
