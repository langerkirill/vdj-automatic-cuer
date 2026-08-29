"""Structural checks for shipped cue-review UX: preroll, crate chips, overlay."""

from __future__ import annotations

import unittest

try:
    from tests.js_assets import UI_STATIC, read_shipped_js, read_static
except ImportError:
    from js_assets import UI_STATIC, read_shipped_js, read_static


class UxReviewAssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = (UI_STATIC / "index.html").read_text(encoding="utf-8")
        cls.css = (UI_STATIC / "styles.css").read_text(encoding="utf-8")
        cls.app = read_static("app.js")
        cls.transport = read_static("transport.js")
        cls.state = read_static("state.js")
        cls.js = read_shipped_js()

    def test_overlay_control_and_question_handler_exist(self) -> None:
        self.assertIn('id="keyboardOverlay"', self.html)
        self.assertIn('id="shortcutsHelpBtn"', self.html)
        self.assertIn("function toggleKeyboardOverlay", self.app)
        self.assertIn('e.key === "?"', self.app)
        self.assertIn("toggleKeyboardOverlay", self.app)
        self.assertIn("Space", self.html)
        self.assertIn("Arrow Left", self.html)
        self.assertRegex(self.html, r"1\s*[–-]\s*9|1–9")

    def test_queue_row_markup_includes_bpm_and_key_slots(self) -> None:
        self.assertIn("function crateBpmKeyLabels", self.transport)
        self.assertIn("crate-bpm", self.css)
        self.assertIn("function renderQueueTrackRow", self.app)

    def test_color_legend_on_cue_review_surface(self) -> None:
        self.assertIn('id="cueColorLegend"', self.html)
        html_l = self.html.lower()
        self.assertIn("melodic", html_l)
        self.assertIn("drums", html_l)
        self.assertIn("vocals", html_l)
        self.assertIn("cueColorMeaning", self.app)
        self.assertIn("cue-color-meaning", self.app)

    def test_preroll_control_exists(self) -> None:
        self.assertIn('id="exactCueJump"', self.html)
        self.assertIn("exactCueJump", self.state)
        self.assertIn("cuePrerollTime", self.app)
        self.assertIn("function jumpToCue", self.app)
        self.assertIn("MusicSorterTransport.cuePrerollTime", self.app)

    def test_left_right_bound_in_real_keydown_path(self) -> None:
        self.assertIn('e.key === "ArrowLeft"', self.app)
        self.assertIn('e.key === "ArrowRight"', self.app)
        self.assertIn("function seekByBeat", self.app)
        self.assertIn("beatSeekTime", self.app)
        self.assertIn("document.addEventListener(\"keydown\"", self.app)
        # Seek lives in the same keydown listener as Space / 1–9, not a stub.
        keydown = self.app.split("document.addEventListener(\"keydown\"", 1)[1]
        self.assertIn("ArrowLeft", keydown)
        self.assertIn("ArrowRight", keydown)
        self.assertIn("seekByBeat", keydown)
        self.assertIn("e.shiftKey", keydown)

    def test_shortcuts_hint_lists_new_seeks(self) -> None:
        self.assertIn("←", self.app)
        self.assertIn("→", self.app)
        self.assertIn("shortcutsHint", self.app)
        for token in ("Space", "1", "9", "L", "C", "O", "Z", "H", "G"):
            self.assertIn(token, self.app)

    def test_transport_exports_shipped_helpers(self) -> None:
        for name in (
            "cuePrerollTime",
            "cuePrerollSeconds",
            "beatSeekTime",
            "crateBpmKeyLabels",
            "cueColorMeaning",
            "keyToCamelot",
        ):
            self.assertIn(f"function {name}", self.transport)
            self.assertIn(name, self.transport.split("return {", 1)[1])

    def test_queue_row_can_start_autocue_while_another_job_runs(self) -> None:
        """A second track can join AutoCue from the list without a global lock."""
        self.assertIn("function retryCuesForTrack", self.app)
        self.assertIn("retryCuesForTrack(currentTrack(), writeScope)", self.app)
        self.assertIn("track-autocue-btn", self.app)
        self.assertIn('querySelectorAll(".track-autocue-btn")', self.app)
        self.assertIn("event.stopPropagation()", self.app)
        self.assertIn("{ fromQueue: true }", self.app)
        self.assertIn("queueAlongside", self.app)
        self.assertIn('j.status === "queued" || j.status === "running"', self.app)
        self.assertIn("let confirmTail = Promise.resolve()", self.app)
        self.assertIn("function showConfirmDialogUnlocked", self.app)
        self.assertIn("if (dialog.open) await waitForConfirmDialogIdle()", self.app)
        self.assertNotIn(
            "if (dialog.open) return Promise.resolve(false)",
            self.app,
        )
        self.assertIn("btn.disabled = busyHere", self.app)
        sync_fn = self.app.split("function syncAutocueUi", 1)[1]
        sync_body = sync_fn.split("function updateAutocueButtonLabels", 1)[0]
        self.assertNotIn("isAutocueJobRunning()", sync_body)
        self.assertIn("function isAutocueBusyForCurrentTrack", self.app)
        busy_fn = self.app.split("function isAutocueBusyForCurrentTrack", 1)[1]
        busy_body = busy_fn.split("function ", 1)[0]
        self.assertNotIn("batchPollTimer", busy_body)
        self.assertIn("currentTrack()?.path", busy_body)
        self.assertIn(".track-row", self.css)
        self.assertIn(".track-autocue-btn", self.css)
        self.assertIn("Queue AutoCue", self.app)


if __name__ == "__main__":
    unittest.main()
