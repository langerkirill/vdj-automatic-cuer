"""Drive the shipped UMD modules — no reimplementation of the helpers."""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

try:
    from tests.js_assets import SHIPPED_JS, UI_STATIC, read_static
except ImportError:
    from js_assets import SHIPPED_JS, UI_STATIC, read_static

INDEX_HTML = UI_STATIC / "index.html"


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


def _require(name: str) -> str:
    return json.dumps(str(UI_STATIC / name))


class ModuleSplitAssetTests(unittest.TestCase):
    def test_named_domains_are_distinct_shipped_files(self) -> None:
        for name in SHIPPED_JS:
            path = UI_STATIC / name
            self.assertTrue(path.is_file(), f"missing shipped module {name}")
            self.assertGreater(path.stat().st_size, 80)

    def test_index_loads_classic_scripts_in_order(self) -> None:
        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertNotIn('type="module"', html)
        indexes = []
        for name in SHIPPED_JS:
            needle = f"/static/{name}"
            self.assertIn(needle, html, f"{name} must be a classic <script src>")
            indexes.append(html.index(needle))
        self.assertEqual(indexes, sorted(indexes), "script load order must match SHIPPED_JS")
        self.assertLess(html.index("state.js"), html.index("app.js"))
        self.assertLess(html.index("waveform.js"), html.index("app.js"))
        self.assertLess(html.index("practice.js"), html.index("app.js"))
        self.assertLess(html.index("assemble.js"), html.index("app.js"))

    def test_app_js_is_no_longer_the_sole_home(self) -> None:
        app = read_static("app.js")
        state = read_static("state.js")
        wave = read_static("waveform.js")
        practice = read_static("practice.js")
        assemble = read_static("assemble.js")
        transport = read_static("transport.js")
        self.assertIn("function addCuesReadinessRank", state)
        self.assertIn("function classifyWaveMarkers", wave)
        self.assertIn("function keepPlayheadInView", wave)
        self.assertIn("function shouldAutoplayOnSelect", transport)
        self.assertIn("function practiceSongSlots", practice)
        self.assertIn("function sortAssemblePlaylist", assemble)
        self.assertIn("MusicSorterState", app)
        self.assertIn("MusicSorterWaveform", app)
        self.assertIn("MusicSorterPractice", app)
        self.assertIn("MusicSorterAssemble", app)
        self.assertIn("MusicSorterTransport", app)
        self.assertNotIn("function classifyWaveMarkers(points, view, slack = 0.05) {\n  const inView", app)

    def test_shared_types_exist_for_tsc(self) -> None:
        types_src = (UI_STATIC / "types.ts").read_text(encoding="utf-8")
        for name in ("Track", "CuePoint", "Placement", "RetryJob"):
            self.assertIn(f"export type {name}", types_src)
        self.assertIn("SHARED_TYPE_NAMES", types_src)
        tsconfig = (UI_STATIC.parent / "tsconfig.json").read_text(encoding="utf-8")
        self.assertIn('"allowJs": true', tsconfig)
        self.assertIn('"checkJs": true', tsconfig)
        self.assertIn("import('./types').Track", read_static("state.js"))
        self.assertIn("import('./types').CuePoint", read_static("waveform.js"))
        self.assertIn("import('./types').Placement", read_static("placements.js"))
        self.assertIn("import('./types').RetryJob", read_static("state.js"))

    def test_no_react_or_ui_framework(self) -> None:
        for name in SHIPPED_JS:
            src = read_static(name)
            self.assertNotIn("from 'react'", src)
            self.assertNotIn('from "react"', src)
            self.assertNotIn("createRoot(", src)
            self.assertNotIn("Vue.createApp", src)


class ShippedModuleBehaviorTests(unittest.TestCase):
    def test_state_readiness_rank_and_retry_kind(self) -> None:
        out = _node(
            f"""
const S = require({_require("state.js")});
const ready = S.addCuesReadinessRank({{ readiness: {{ status: "ready" }} }});
const missing = S.addCuesReadinessRank({{ readiness: {{ status: "not_cued" }} }});
const kind = S.trackRetryKind(
  {{ retry_history: {{ kind: "cues", tried_cues: true }} }},
  {{ status: "ok", writeScope: "loops" }}
);
const section = S.addCuesSection({{ group: "Pajamathon 2026", relative_path: "x.flac" }});
S.state.mode = "practice";
console.log(JSON.stringify({{
  ready, missing, kind, section,
  isPractice: S.isPracticeMode(),
  typeNames: ["Track", "CuePoint", "Placement", "RetryJob"]
}}));
"""
        )
        self.assertEqual(out["ready"], 0)
        self.assertEqual(out["missing"], 2)
        self.assertEqual(out["kind"], "both")
        self.assertEqual(out["section"], "pajamathon")
        self.assertTrue(out["isPractice"])
        self.assertEqual(out["typeNames"], ["Track", "CuePoint", "Placement", "RetryJob"])

    def test_waveform_zoom_and_playhead_follow(self) -> None:
        out = _node(
            f"""
const W = require({_require("waveform.js")});
const duration = 200.44;
const view = W.visibleWaveWindow(duration, 5.2, 123.0);
const points = [
  {{ kind: "cue", pos: 0.467846 }},
  {{ kind: "loop", pos: 0.467846 }},
  {{ kind: "cue", pos: 21.467846 }},
  {{ kind: "cue", pos: 108.467846 }},
];
const classified = W.classifyWaveMarkers(points, view);
const label = W.formatOffscreenCueLabel(classified.offLeft, "left");
const appState = {{
  waveZoom: 20,
  waveOffset: 60,
  waveViewPinned: false,
  gridAlignDragging: false,
  loopDrag: null,
  gridAlignMode: false,
}};
const followed = W.applyPlayheadFollow(appState, 240, 80, true);
const pinned = {{
  waveZoom: 20,
  waveOffset: 60,
  waveViewPinned: true,
  gridAlignMode: false,
  gridAlignDragging: false,
  loopDrag: null,
}};
const skipped = W.applyPlayheadFollow(pinned, 240, 80, true);
console.log(JSON.stringify({{
  start: view.start,
  inView: classified.inView.length,
  offLeft: classified.offLeft.length,
  label,
  followedStart: followed.start,
  pinnedStart: skipped.start,
  unpinned: pinned.waveViewPinned,
}}));
"""
        )
        self.assertAlmostEqual(out["start"], 123.0, places=1)
        self.assertEqual(out["inView"], 0)
        self.assertEqual(out["offLeft"], 4)
        self.assertIn("cue", out["label"])
        self.assertLessEqual(out["followedStart"], 80.0)
        self.assertAlmostEqual(out["pinnedStart"], 60.0, places=1)
        self.assertTrue(out["unpinned"])

    def test_transport_quiet_and_autoplay_policy(self) -> None:
        out = _node(
            f"""
const T = require({_require("transport.js")});
const quiet = T.wantsQuietSession({{ location: {{ search: "?quiet=1" }}, navigator: {{}} }});
const webdriver = T.wantsQuietSession({{ location: {{ search: "" }}, navigator: {{ webdriver: true }} }});
const loud = T.wantsQuietSession({{ location: {{ search: "" }}, navigator: {{}} }});
const auto = T.shouldAutoplayOnSelect({{ quietSession: false, allowAutoplay: true }}, false);
const blocked = T.shouldAutoplayOnSelect({{ quietSession: true, allowAutoplay: true }}, false);
const practice = T.shouldAutoplayOnSelect({{ quietSession: false, allowAutoplay: true }}, true);
console.log(JSON.stringify({{
  quiet, webdriver, loud, auto, blocked, practice,
  clock: T.formatClock(75),
  title: T.trackDisplayTitle({{ name: "01. Hello.flac", cues: {{ title: "Hi" }} }}),
}}));
"""
        )
        self.assertTrue(out["quiet"])
        self.assertTrue(out["webdriver"])
        self.assertFalse(out["loud"])
        self.assertTrue(out["auto"])
        self.assertFalse(out["blocked"])
        self.assertFalse(out["practice"])
        self.assertEqual(out["clock"], "1:15")
        self.assertEqual(out["title"], "Hi")

    def test_transport_fmt_time_milliseconds(self) -> None:
        out = _node(
            f"""
const T = require({_require("transport.js")});
const W = require({_require("waveform.js")});
const view = {{ start: 0, end: 10, span: 10 }};
console.log(JSON.stringify({{
  zero: T.fmtTime(0),
  cue1: T.fmtTime(0.030522),
  mid: T.fmtTime(13.364),
  long: T.fmtTime(82.697),
  x0: W.timeToWaveX(0, 8, 1000, view),
  xCue1: W.timeToWaveX(0.030522, 8, 1000, view),
}}));
"""
        )
        self.assertEqual(out["zero"], "0:00.000")
        self.assertEqual(out["cue1"], "0:00.031")
        self.assertEqual(out["mid"], "0:13.364")
        self.assertEqual(out["long"], "1:22.697")
        self.assertNotEqual(out["cue1"], out["zero"])
        self.assertNotAlmostEqual(out["x0"], out["xCue1"])
        self.assertAlmostEqual(out["xCue1"], 8 + (0.030522 / 10) * 1000, places=4)


    def test_practice_map_math(self) -> None:
        out = _node(
            f"""
const P = require({_require("practice.js")});
const detail = {{
  duration_sec: 120,
  tracks: [
    {{ name: "One", pos_sec: 0 }},
    {{ name: "Two", pos_sec: 60 }},
  ],
  transitions: [{{ at_sec: 60 }}],
}};
const slots = P.practiceSongSlots(120, detail);
const x = P.practiceTimeToX(60, slots, 400, 120, 10);
const t = P.practiceXToTime(x, slots, 400, 120, 10);
const txs = P.practiceTransitions({{ practiceDetail: detail }});
console.log(JSON.stringify({{ n: slots.length, t, txs: txs.length, clamp: P.practiceClamp(9, 0, 4) }}));
"""
        )
        self.assertEqual(out["n"], 2)
        self.assertAlmostEqual(out["t"], 60.0, places=4)
        self.assertEqual(out["txs"], 1)
        self.assertEqual(out["clamp"], 4)

    def test_assemble_sort_and_job_busy(self) -> None:
        out = _node(
            f"""
const A = require({_require("assemble.js")});
const rows = [
  {{ artist: "B", title: "Z", fit: 0.4 }},
  {{ artist: "A", title: "Y", fit: 0.9 }},
];
const byFit = A.sortAssemblePlaylist(rows, "fit").map((t) => t.title);
const byCrate = A.sortAssemblePlaylist(rows, "crate").map((t) => t.title);
const shares = A.normalizeClientShares({{ chill: 80, energy: 20 }});
console.log(JSON.stringify({{
  byFit, byCrate,
  chill: shares.chill,
  busy: A.assembleJobBusy({{ id: "1", status: "running" }}),
  idle: A.assembleJobBusy({{ id: "1", status: "ok" }}),
}}));
"""
        )
        self.assertEqual(out["byFit"], ["Y", "Z"])
        self.assertEqual(out["byCrate"], ["Z", "Y"])
        self.assertAlmostEqual(out["chill"], 0.8, places=5)
        self.assertTrue(out["busy"])
        self.assertFalse(out["idle"])

    def test_classic_scripts_eval_on_window_without_node_globals(self) -> None:
        names = [
            "status_handoff.js",
            "placements.js",
            "state.js",
            "transport.js",
            "waveform.js",
            "practice.js",
            "assemble.js",
        ]
        files = {name: read_static(name) for name in names}
        payload = json.dumps(files)
        out = _node(
            f"""
const files = {payload};
const window = {{}};
const globalThis = window;
const module = undefined;
const exports = undefined;
const require = undefined;
const document = undefined;
for (const [name, src] of Object.entries(files)) {{
  const fn = new Function("window", "globalThis", "module", "exports", "require", "document", src);
  fn(window, globalThis, module, exports, require, document);
}}
console.log(JSON.stringify({{
  keys: [
    typeof window.MusicSorterState,
    typeof window.MusicSorterTransport,
    typeof window.MusicSorterWaveform,
    typeof window.MusicSorterPractice,
    typeof window.MusicSorterAssemble,
    typeof window.MusicSorterPlacements,
    typeof window.MusicSorterStatusHandoff,
  ],
  classify: typeof window.MusicSorterWaveform.classifyWaveMarkers,
  rank: typeof window.MusicSorterState.addCuesReadinessRank,
}}));
"""
        )
        self.assertEqual(out["keys"], ["object"] * 7)
        self.assertEqual(out["classify"], "function")
        self.assertEqual(out["rank"], "function")


if __name__ == "__main__":
    unittest.main()
