"""Playhead must stay visible at every waveform zoom while audio is moving.

Align-grid zooms to a short window. If the canvas needle is only painted when
currentTime is inside that window, it walks off-screen (or vanishes after a
zoom) at several scope widths. These tests lock the paging math and the
shipped JS/HTML contract.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

try:
    from tests.js_assets import UI_STATIC, read_shipped_js, read_static
except ImportError:
    from js_assets import UI_STATIC, read_shipped_js, read_static


def visible_wave_window(duration: float, zoom: float, offset: float) -> dict[str, float]:
    zoom = min(48.0, max(1.0, float(zoom or 1.0)))
    if duration <= 0:
        return {"start": 0.0, "end": 0.0, "span": 0.0, "offset": 0.0, "zoom": zoom}
    span = duration / zoom
    start = max(0.0, min(float(offset or 0.0), max(0.0, duration - span)))
    return {"start": start, "end": start + span, "span": span, "offset": start, "zoom": zoom}


def keep_playhead_in_view(
    duration: float,
    time_sec: float,
    *,
    zoom: float,
    offset: float,
    playing: bool,
    allow_follow: bool,
    lead: float = 0.08,
) -> dict[str, float]:
    view = visible_wave_window(duration, zoom, offset)
    if duration <= 0 or time_sec != time_sec:
        return view
    if not playing or not allow_follow:
        return view
    if view["start"] <= time_sec <= view["end"]:
        return view
    start = time_sec - view["span"] * lead
    return visible_wave_window(duration, zoom, start)


def time_to_wave_x(time_sec: float, pad_x: float, plot_w: float, view: dict[str, float]) -> float:
    if not view["span"]:
        return pad_x
    return pad_x + ((time_sec - view["start"]) / view["span"]) * plot_w


def playhead_draw_x(
    time_sec: float,
    pad_x: float,
    plot_w: float,
    view: dict[str, float],
    *,
    playing: bool,
) -> float | None:
    """X to paint. Moving needle is never dropped; paused off-screen may hide."""
    in_view = view["span"] > 0 and view["start"] <= time_sec <= view["end"]
    if not in_view and not playing:
        return None
    x = time_to_wave_x(time_sec, pad_x, plot_w, view)
    return max(pad_x, min(pad_x + plot_w, x))


class PlayheadFollowMathTests(unittest.TestCase):
    def test_full_track_scope_keeps_offset_zero(self) -> None:
        view = keep_playhead_in_view(
            240.0, 90.0, zoom=1, offset=0, playing=True, allow_follow=True
        )
        self.assertEqual(view["start"], 0.0)
        self.assertEqual(view["end"], 240.0)
        self.assertTrue(view["start"] <= 90.0 <= view["end"])

    def test_playhead_inside_window_does_not_nudge(self) -> None:
        view = keep_playhead_in_view(
            240.0, 62.0, zoom=8, offset=60.0, playing=True, allow_follow=True
        )
        self.assertAlmostEqual(view["start"], 60.0)
        self.assertTrue(view["start"] <= 62.0 <= view["end"])

    def test_pages_forward_when_needle_walks_off_right(self) -> None:
        # 12s window starting at 60s; playhead at 80s is off-screen.
        view = keep_playhead_in_view(
            240.0, 80.0, zoom=20, offset=60.0, playing=True, allow_follow=True
        )
        self.assertLessEqual(view["start"], 80.0)
        self.assertGreaterEqual(view["end"], 80.0)
        self.assertGreater(view["start"], 60.0)

    def test_pages_back_when_needle_is_left_of_window(self) -> None:
        view = keep_playhead_in_view(
            240.0, 10.0, zoom=16, offset=80.0, playing=True, allow_follow=True
        )
        self.assertLessEqual(view["start"], 10.0)
        self.assertGreaterEqual(view["end"], 10.0)

    def test_paused_does_not_follow_offscreen_needle(self) -> None:
        view = keep_playhead_in_view(
            240.0, 80.0, zoom=20, offset=60.0, playing=False, allow_follow=True
        )
        self.assertAlmostEqual(view["start"], 60.0)
        self.assertLess(view["end"], 80.0)

    def test_drag_disables_follow(self) -> None:
        view = keep_playhead_in_view(
            240.0, 80.0, zoom=20, offset=60.0, playing=True, allow_follow=False
        )
        self.assertAlmostEqual(view["start"], 60.0)

    def test_moving_needle_stays_in_view_at_every_zoom(self) -> None:
        duration = 247.3
        times = (0.0, 0.4, 15.7, 61.2, 180.05, 246.9)
        zooms = (1, 1.7, 2, 3.5, 6, 8, 12, 18, 24, 32, 40, 48)
        offsets = (0.0, 12.0, 60.0, 180.0)
        for zoom in zooms:
            for offset in offsets:
                for t in times:
                    view = keep_playhead_in_view(
                        duration,
                        t,
                        zoom=zoom,
                        offset=offset,
                        playing=True,
                        allow_follow=True,
                    )
                    self.assertLessEqual(
                        view["start"],
                        t + 1e-9,
                        f"start {view['start']} > t {t} at zoom={zoom} offset={offset}",
                    )
                    self.assertGreaterEqual(
                        view["end"],
                        t - 1e-9,
                        f"end {view['end']} < t {t} at zoom={zoom} offset={offset}",
                    )
                    x = playhead_draw_x(t, 8.0, 640.0, view, playing=True)
                    self.assertIsNotNone(x)
                    self.assertGreaterEqual(x, 8.0)
                    self.assertLessEqual(x, 8.0 + 640.0)

    def test_moving_needle_is_drawn_even_before_page(self) -> None:
        view = visible_wave_window(240.0, 20, 60.0)
        # 80s is past the right edge — still paint, clamped to the plot.
        x = playhead_draw_x(80.0, 8.0, 640.0, view, playing=True)
        self.assertEqual(x, 8.0 + 640.0)

    def test_paused_offscreen_needle_is_hidden(self) -> None:
        view = visible_wave_window(240.0, 20, 60.0)
        self.assertIsNone(playhead_draw_x(80.0, 8.0, 640.0, view, playing=False))


class PlayheadFollowAssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = (UI_STATIC / "index.html").read_text(encoding="utf-8")
        cls.css = (UI_STATIC / "styles.css").read_text(encoding="utf-8")
        cls.app_js = read_static("app.js")
        cls.wave_js = read_static("waveform.js")
        cls.js = read_shipped_js()

    def test_js_exports_follow_helpers(self) -> None:
        self.assertIn("function keepPlayheadInView", self.wave_js)
        self.assertIn("function applyPlayheadFollow", self.wave_js)
        self.assertIn("function startPlayheadWatch", self.js)
        self.assertIn("function positionWavePlayhead", self.js)

    def test_js_does_not_drop_playhead_outside_view(self) -> None:
        # Old bug: canvas needle skipped whenever currentTime left the zoom window.
        self.assertNotRegex(
            self.js,
            r"if\s*\(\s*t\s*>=\s*view\.start\s*&&\s*t\s*<=\s*view\.end\s*\)\s*\{",
        )
        self.assertIn("applyPlayheadFollow(", self.js)
        self.assertIn("positionWavePlayhead(", self.js)

    def test_draw_and_zoom_keep_moving_needle(self) -> None:
        self.assertIn("applyPlayheadFollow(duration", self.js)
        self.assertRegex(self.js, r"function drawWaveform\([\s\S]*applyPlayheadFollow\(")
        self.assertRegex(self.js, r"function onWaveformWheel\([\s\S]*waveViewPinned")
        self.assertRegex(self.js, r"function updatePlayhead\([\s\S]*startPlayheadWatch\(")
        self.assertIn("function syncMovingPlayhead", self.js)
        self.assertIn("function snapshotWaveSeekTime", self.js)
        self.assertIn("state.waveSeekTime", self.js)

    def test_wave_playhead_overlay_is_shipped(self) -> None:
        self.assertIn('id="wavePlayhead"', self.html)
        self.assertIn(".wave-playhead", self.css)
        self.assertIn("wavePlayhead", self.js)
        self.assertRegex(self.html, r"/static/app\.js(\?[^\"']*)?")


if __name__ == "__main__":
    unittest.main()
