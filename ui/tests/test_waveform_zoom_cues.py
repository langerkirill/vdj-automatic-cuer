"""Zoomed waveform must not swallow cues that sit outside the window.

Thank You (Skinny Remix) is the live case: 6 cues + 1 loop all live in the
first 109s. Align-grid / playhead follow then zooms to ~2:03–2:41 (5.2×)
and the canvas drops every marker. Off-screen chips + overview ticks keep
them reachable; align zoom must center on the 1, not the playhead.
"""

from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path

try:
    from tests.js_assets import UI_STATIC, read_shipped_js, read_static
except ImportError:
    from js_assets import UI_STATIC, read_shipped_js, read_static

# Live VDJ points from Dido - Thank You (Skinny Remix).m4a
SKINNY_DURATION = 200.44
SKINNY_ANCHOR = 12.452488
SKINNY_POINTS = [
    {"kind": "cue", "pos": 0.467846, "name": "Intro Rhythm Section"},
    {"kind": "loop", "pos": 0.467846, "name": "Intro Synthl", "size": 8.0},
    {"kind": "cue", "pos": 21.467846, "name": "Vocal Verse In"},
    {"kind": "cue", "pos": 54.467846, "name": "Vocal Mix"},
    {"kind": "cue", "pos": 84.467846, "name": "Vocal Drop"},
    {"kind": "cue", "pos": 96.467846, "name": "Vocal Mix"},
    {"kind": "cue", "pos": 108.467846, "name": "Vocal Mix"},
]


def visible_wave_window(duration: float, zoom: float, offset: float) -> dict[str, float]:
    zoom = min(48.0, max(1.0, float(zoom or 1.0)))
    if duration <= 0:
        return {"start": 0.0, "end": 0.0, "span": 0.0, "offset": 0.0, "zoom": zoom}
    span = duration / zoom
    start = max(0.0, min(float(offset or 0.0), max(0.0, duration - span)))
    return {"start": start, "end": start + span, "span": span, "offset": start, "zoom": zoom}


def classify_wave_markers(
    points: list[dict],
    view: dict[str, float],
    slack: float = 0.05,
) -> dict[str, list[dict]]:
    in_view: list[dict] = []
    off_left: list[dict] = []
    off_right: list[dict] = []
    for point in points:
        pos = float(point.get("pos") or 0.0)
        if pos < view["start"] - slack:
            off_left.append(point)
        elif pos > view["end"] + slack:
            off_right.append(point)
        else:
            in_view.append(point)
    return {"in_view": in_view, "off_left": off_left, "off_right": off_right}


def marker_kind_counts(points: list[dict]) -> tuple[int, int]:
    cues = sum(1 for p in points if str(p.get("kind") or "cue") != "loop")
    loops = sum(1 for p in points if str(p.get("kind") or "") == "loop")
    return cues, loops


def format_offscreen_cue_label(points: list[dict], side: str) -> str:
    cues, loops = marker_kind_counts(points)
    parts: list[str] = []
    if cues:
        parts.append(f"{cues} cue" + ("s" if cues != 1 else ""))
    if loops:
        parts.append(f"{loops} loop" + ("s" if loops != 1 else ""))
    if not parts:
        return ""
    body = " · ".join(parts)
    return f"← {body}" if side == "left" else f"{body} →"


def pan_wave_to_time(
    duration: float, zoom: float, time_sec: float, frac: float = 0.22
) -> dict[str, float]:
    view = visible_wave_window(duration, zoom, 0.0)
    return visible_wave_window(duration, zoom, time_sec - view["span"] * frac)


def zoom_window_around(duration: float, center: float, want_span: float) -> dict[str, float]:
    zoom = min(48.0, max(1.0, duration / max(want_span, 0.001)))
    start = center - (duration / zoom) / 2.0
    return visible_wave_window(duration, zoom, start)


class ClassifyZoomedCuesTests(unittest.TestCase):
    def test_skinny_remix_523_zoom_hides_every_marker(self) -> None:
        # Screenshot: 5.2× · 2:03.0–2:41.3
        view = visible_wave_window(SKINNY_DURATION, 5.2, 123.0)
        self.assertAlmostEqual(view["start"], 123.0, places=1)
        self.assertGreater(view["end"], 160.0)
        classified = classify_wave_markers(SKINNY_POINTS, view)
        self.assertEqual(len(classified["in_view"]), 0)
        self.assertEqual(len(classified["off_left"]), 7)
        self.assertEqual(len(classified["off_right"]), 0)
        self.assertEqual(
            format_offscreen_cue_label(classified["off_left"], "left"),
            "← 6 cues · 1 loop",
        )

    def test_full_track_keeps_every_marker_in_view(self) -> None:
        view = visible_wave_window(SKINNY_DURATION, 1.0, 0.0)
        classified = classify_wave_markers(SKINNY_POINTS, view)
        self.assertEqual(len(classified["in_view"]), 7)
        self.assertEqual(classified["off_left"], [])
        self.assertEqual(classified["off_right"], [])
        self.assertEqual(format_offscreen_cue_label([], "left"), "")

    def test_early_zoom_keeps_intro_and_flags_later_cues(self) -> None:
        view = visible_wave_window(SKINNY_DURATION, 8.0, 0.0)
        classified = classify_wave_markers(SKINNY_POINTS, view)
        names = [p["name"] for p in classified["in_view"]]
        self.assertIn("Intro Rhythm Section", names)
        self.assertIn("Vocal Verse In", names)
        self.assertGreaterEqual(len(classified["off_right"]), 3)
        self.assertEqual(classified["off_left"], [])
        self.assertRegex(
            format_offscreen_cue_label(classified["off_right"], "right"),
            r"^\d+ cues →$",
        )

    def test_single_marker_label_is_singular(self) -> None:
        self.assertEqual(
            format_offscreen_cue_label([{"kind": "cue", "pos": 10}], "left"),
            "← 1 cue",
        )
        self.assertEqual(
            format_offscreen_cue_label([{"kind": "loop", "pos": 10}], "right"),
            "1 loop →",
        )

    def test_clicking_left_chip_pans_to_last_hidden_cue(self) -> None:
        last_left = max(p["pos"] for p in SKINNY_POINTS)
        view = pan_wave_to_time(SKINNY_DURATION, 5.2, last_left)
        self.assertLessEqual(view["start"], last_left)
        self.assertGreaterEqual(view["end"], last_left)
        classified = classify_wave_markers(SKINNY_POINTS, view)
        self.assertTrue(any(abs(p["pos"] - last_left) < 0.02 for p in classified["in_view"]))

    def test_align_zoom_on_the_one_keeps_intro_cues(self) -> None:
        # 12 bars at 80 BPM = 36s. Center on Scan Phase / beatgrid 1, not 2:20.
        bar_sec = (60.0 / 80.0) * 4.0
        view = zoom_window_around(SKINNY_DURATION, SKINNY_ANCHOR, bar_sec * 12)
        self.assertLess(view["start"], 2.0)
        classified = classify_wave_markers(SKINNY_POINTS, view)
        names = [p["name"] for p in classified["in_view"]]
        self.assertIn("Intro Rhythm Section", names)
        self.assertIn("Intro Synthl", names)
        # Playhead-centered 2:03 window must NOT be what align uses.
        playhead_view = visible_wave_window(SKINNY_DURATION, 5.2, 123.0)
        playhead_names = [
            p["name"]
            for p in classify_wave_markers(SKINNY_POINTS, playhead_view)["in_view"]
        ]
        self.assertEqual(playhead_names, [])


class WaveformZoomCueAssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = (UI_STATIC / "index.html").read_text(encoding="utf-8")
        cls.css = (UI_STATIC / "styles.css").read_text(encoding="utf-8")
        cls.app_js = read_static("app.js")
        cls.wave_js = read_static("waveform.js")
        cls.js = read_shipped_js()

    def test_js_classifies_markers_against_the_zoom_window(self) -> None:
        self.assertIn("function classifyWaveMarkers", self.wave_js)
        self.assertIn("function formatOffscreenCueLabel", self.wave_js)
        self.assertIn("function drawOffscreenCueHints", self.js)
        self.assertIn("function drawWaveCueOverview", self.js)
        self.assertIn("offLeft", self.wave_js)
        self.assertIn("offRight", self.wave_js)

    def test_draw_waveform_keeps_offscreen_cues_reachable(self) -> None:
        self.assertRegex(
            self.js,
            r"function drawWaveform\([\s\S]*classifyWaveMarkers\(",
        )
        self.assertRegex(
            self.js,
            r"function drawWaveform\([\s\S]*drawOffscreenCueHints\(",
        )
        self.assertRegex(
            self.js,
            r"function drawWaveform\([\s\S]*drawWaveCueOverview\(",
        )

    def test_align_zoom_centers_on_grid_anchor_not_playhead(self) -> None:
        fn = re.search(
            r"function zoomWaveForGridAlign\([\s\S]*?\n\}",
            self.js,
        )
        self.assertIsNotNone(fn)
        body = fn.group(0)
        self.assertIn("gridAnchorSeconds", body)
        self.assertNotIn("audio.currentTime", body)

    def test_cancel_align_restores_previous_wave_view(self) -> None:
        self.assertIn("function snapshotWaveView", self.js)
        self.assertIn("function restoreWaveView", self.js)
        self.assertIn("function exitGridAlignMode", self.js)
        self.assertRegex(
            self.js,
            r"function openGridAlignMode\([\s\S]*snapshotWaveView\(",
        )
        self.assertRegex(
            self.js,
            r"function cancelGridAlignMode\([\s\S]*restoreView:\s*true",
        )

    def test_playhead_follow_does_not_steal_align_or_pinned_view(self) -> None:
        self.assertRegex(
            self.wave_js,
            r"function applyPlayheadFollow\([\s\S]*!appState\.gridAlignMode[\s\S]*!appState\.waveViewPinned",
        )

    def test_place_cue_keeps_align_zoom(self) -> None:
        self.assertRegex(
            self.js,
            r"function togglePlaceCueMode\([\s\S]*exitGridAlignMode\(\{\s*restoreView:\s*false",
        )
        self.assertRegex(
            self.js,
            r"function togglePlaceLoopMode\([\s\S]*exitGridAlignMode\(\{\s*restoreView:\s*false",
        )

    def test_offscreen_chips_are_clickable(self) -> None:
        self.assertIn("function hitTestWaveCueChrome", self.js)
        self.assertIn("function panWaveToTime", self.js)
        self.assertIn("waveCueChromeHits", self.js)
        self.assertIn("cue-chrome-hover", self.css)
        self.assertRegex(
            self.js,
            r"function seekFromWaveformEvent\([\s\S]*hitTestWaveCueChrome\(",
        )


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


class ComeBackToMeCuePaintTests(unittest.TestCase):
    """Cue 1 / Scan Phase 0.030522 must paint on the yellow 1, not file start."""

    POS = 0.030522
    DURATION = 198.067302
    PAD_X = 8.0
    PLOT_W = 900.0

    def test_fmt_time_does_not_round_the_one_to_zero(self) -> None:
        out = _node(
            f"""
const T = require({_req("transport.js")});
const W = require({_req("waveform.js")});
const pos = {self.POS};
const view = W.visibleWaveWindow({self.DURATION}, 48, 0);
const cueX = W.timeToWaveX(pos, {self.PAD_X}, {self.PLOT_W}, view);
const oneX = W.timeToWaveX(pos, {self.PAD_X}, {self.PLOT_W}, view);
const zeroX = W.timeToWaveX(0, {self.PAD_X}, {self.PLOT_W}, view);
const clamped = W.timeToWaveX(Math.max(pos, view.start), {self.PAD_X}, {self.PLOT_W}, view);
console.log(JSON.stringify({{
  label: T.fmtTime(pos),
  zoom1: T.fmtTime(pos),
  cueX, oneX, zeroX, clamped,
  viewStart: view.start,
}}));
"""
        )
        self.assertEqual(out["label"], "0:00.031")
        self.assertNotEqual(out["label"], "0:00.0")
        self.assertNotEqual(out["label"], "0:00.00")
        self.assertAlmostEqual(out["cueX"], out["oneX"], places=6)
        self.assertGreater(out["cueX"] - out["zeroX"], 5.0)
        # view.start is 0, so clamp was a no-op here; still must not equal file start.
        self.assertAlmostEqual(out["clamped"], out["cueX"], places=6)

    def test_paint_uses_stored_pos_not_view_start(self) -> None:
        js = read_static("app.js")
        self.assertIn("const x = timeToWaveX(t, padX, plotW, view);", js)
        self.assertNotIn(
            "timeToWaveX(Math.max(t, view.start)",
            js,
            "clamping cue x to view.start paints Cue 1 at the left edge",
        )
        self.assertIn("${name} ${fmtTime(t)}", js)
        self.assertIn("* 1000)", read_static("transport.js"))


if __name__ == "__main__":
    unittest.main()
