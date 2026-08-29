"""Behavioral tests of shipped domain helpers — require the real static files."""

from __future__ import annotations

import json
import subprocess
import unittest

try:
    from tests.js_assets import UI_STATIC, read_static
except ImportError:
    from js_assets import UI_STATIC, read_static


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


class ReadyFirstRankingTests(unittest.TestCase):
    def test_sort_add_cues_indexes_puts_ready_first(self) -> None:
        out = _node(
            f"""
const S = require({_req("state.js")});
S.state.tracks = [
  {{ path: "/c.flac", readiness: {{ status: "not_cued" }} }},
  {{ path: "/a.flac", readiness: {{ status: "ready" }} }},
  {{ path: "/b.flac", readiness: {{ status: "partial" }} }},
  {{ path: "/d.flac", readiness: {{ status: "missing" }} }},
];
const order = S.sortAddCuesIndexes([0, 1, 2, 3]).map((i) => S.state.tracks[i].path);
console.log(JSON.stringify({{
  order,
  ranks: S.state.tracks.map(S.addCuesReadinessRank),
}}));
"""
        )
        self.assertEqual(
            out["order"],
            ["/a.flac", "/b.flac", "/c.flac", "/d.flac"],
        )
        self.assertEqual(out["ranks"], [2, 0, 1, 2])

    def test_unknown_readiness_sorts_last_and_stable(self) -> None:
        out = _node(
            f"""
const S = require({_req("state.js")});
S.state.tracks = [
  {{ path: "/x.flac" }},
  {{ path: "/y.flac", readiness: {{ status: "ready" }} }},
  {{ path: "/z.flac" }},
];
const order = S.sortAddCuesIndexes([0, 1, 2]).map((i) => S.state.tracks[i].path);
console.log(JSON.stringify({{ order, unknown: S.addCuesReadinessRank({{ path: "/x.flac" }}) }}));
"""
        )
        self.assertEqual(out["order"], ["/y.flac", "/x.flac", "/z.flac"])
        self.assertEqual(out["unknown"], 3)


class RetryKindTests(unittest.TestCase):
    def test_history_and_finished_job_overlay(self) -> None:
        out = _node(
            f"""
const S = require({_req("state.js")});
const none = S.trackRetryKind({{ path: "/t.flac" }});
const cues = S.trackRetryKind({{ path: "/t.flac", retry_history: {{ kind: "cues" }} }});
const loops = S.trackRetryKind({{ path: "/t.flac", retry_history: {{ tried_loops: true }} }});
const bothHist = S.trackRetryKind({{ path: "/t.flac", retry_history: {{ tried_both: true }} }});
const jobLoops = S.trackRetryKind(
  {{ path: "/t.flac", retry_history: {{ tried_cues: true }} }},
  {{ status: "ok", writeScope: "loops" }}
);
const runningIgnored = S.trackRetryKind(
  {{ path: "/t.flac" }},
  {{ status: "running", writeScope: "both" }}
);
console.log(JSON.stringify({{ none, cues, loops, bothHist, jobLoops, runningIgnored }}));
"""
        )
        self.assertIsNone(out["none"])
        self.assertEqual(out["cues"], "cues")
        self.assertEqual(out["loops"], "loops")
        self.assertEqual(out["bothHist"], "both")
        self.assertEqual(out["jobLoops"], "both")
        self.assertIsNone(out["runningIgnored"])

    def test_recently_cued_uses_retry_timestamp(self) -> None:
        out = _node(
            f"""
const S = require({_req("state.js")});
const now = Date.parse("2026-08-16T18:00:00-07:00");
const fresh = S.isRecentlyCued(
  {{ retry_history: {{ last_ts: "2026-08-16T12:00:00-07:00" }} }},
  null,
  now
);
const stale = S.isRecentlyCued(
  {{ retry_history: {{ last_ts: "2026-08-01T12:00:00-07:00" }} }},
  null,
  now
);
const fromJob = S.isRecentlyCued(
  {{}},
  {{ status: "ok", finished_at: "2026-08-16T17:30:00-07:00" }},
  now
);
const none = S.isRecentlyCued({{}}, null, now);
console.log(JSON.stringify({{ fresh, stale, fromJob, none, window: S.RECENTLY_CUED_MS }}));
"""
        )
        self.assertTrue(out["fresh"])
        self.assertFalse(out["stale"])
        self.assertTrue(out["fromJob"])
        self.assertFalse(out["none"])
        self.assertGreater(out["window"], 24 * 60 * 60 * 1000)


class QuietAutoplayTests(unittest.TestCase):
    def test_quiet_flags_and_autoplay_gates(self) -> None:
        out = _node(
            f"""
const T = require({_req("transport.js")});
const cases = {{
  quiet1: T.wantsQuietSession({{ location: {{ search: "?quiet=1" }}, navigator: {{}} }}),
  muteYes: T.wantsQuietSession({{ location: {{ search: "?mute=yes" }}, navigator: {{}} }}),
  quiet0: T.wantsQuietSession({{ location: {{ search: "?quiet=0" }}, navigator: {{}} }}),
  empty: T.wantsQuietSession({{ location: {{ search: "" }}, navigator: {{}} }}),
  playOk: T.shouldAutoplayOnSelect({{ quietSession: false, allowAutoplay: true }}, false),
  noUserPlay: T.shouldAutoplayOnSelect({{ quietSession: false, allowAutoplay: false }}, false),
  quietBlocks: T.shouldAutoplayOnSelect({{ quietSession: true, allowAutoplay: true }}, false),
  practiceBlocks: T.shouldAutoplayOnSelect({{ quietSession: false, allowAutoplay: true }}, true),
}};
console.log(JSON.stringify(cases));
"""
        )
        self.assertTrue(out["quiet1"])
        self.assertTrue(out["muteYes"])
        self.assertFalse(out["quiet0"])
        self.assertFalse(out["empty"])
        self.assertTrue(out["playOk"])
        self.assertFalse(out["noUserPlay"])
        self.assertFalse(out["quietBlocks"])
        self.assertFalse(out["practiceBlocks"])


class WaveformFollowTests(unittest.TestCase):
    def test_pinned_and_align_do_not_page_playing_needle(self) -> None:
        out = _node(
            f"""
const W = require({_req("waveform.js")});
const playing = {{
  waveZoom: 20, waveOffset: 60, waveViewPinned: false,
  gridAlignDragging: false, loopDrag: null, gridAlignMode: false,
}};
const followed = W.applyPlayheadFollow(playing, 240, 80, true);
const pinned = {{
  waveZoom: 20, waveOffset: 60, waveViewPinned: true,
  gridAlignDragging: false, loopDrag: null, gridAlignMode: false,
}};
const pinStay = W.applyPlayheadFollow(pinned, 240, 80, true);
const align = {{
  waveZoom: 20, waveOffset: 60, waveViewPinned: false,
  gridAlignDragging: false, loopDrag: null, gridAlignMode: true,
}};
const alignStay = W.applyPlayheadFollow(align, 240, 80, true);
const paused = W.keepPlayheadInView(240, 80, {{
  zoom: 20, offset: 60, playing: false, allowFollow: true,
}});
console.log(JSON.stringify({{
  followedStart: followed.start,
  pinStart: pinStay.start,
  stillPinned: pinned.waveViewPinned,
  alignStart: alignStay.start,
  pausedStart: paused.start,
}}));
"""
        )
        self.assertLessEqual(out["followedStart"], 80.0)
        self.assertAlmostEqual(out["pinStart"], 60.0, places=1)
        self.assertTrue(out["stillPinned"])
        self.assertAlmostEqual(out["alignStart"], 60.0, places=1)
        self.assertAlmostEqual(out["pausedStart"], 60.0, places=1)

    def test_pin_clears_when_needle_returns_on_screen(self) -> None:
        out = _node(
            f"""
const W = require({_req("waveform.js")});
const st = {{
  waveZoom: 20, waveOffset: 60, waveViewPinned: true,
  gridAlignDragging: false, loopDrag: null, gridAlignMode: false,
}};
W.applyPlayheadFollow(st, 240, 62, true);
console.log(JSON.stringify({{ pinned: st.waveViewPinned, offset: st.waveOffset }}));
"""
        )
        self.assertFalse(out["pinned"])
        self.assertAlmostEqual(out["offset"], 60.0, places=1)

    def test_offscreen_cue_labels_match_skinny_remix_window(self) -> None:
        out = _node(
            f"""
const W = require({_req("waveform.js")});
const points = [
  {{ kind: "cue", pos: 0.467846, name: "Intro" }},
  {{ kind: "loop", pos: 0.467846, name: "Intro loop" }},
  {{ kind: "cue", pos: 21.467846 }},
  {{ kind: "cue", pos: 54.467846 }},
  {{ kind: "cue", pos: 84.467846 }},
  {{ kind: "cue", pos: 96.467846 }},
  {{ kind: "cue", pos: 108.467846 }},
];
const view = W.visibleWaveWindow(200.44, 5.2, 123.0);
const c = W.classifyWaveMarkers(points, view);
console.log(JSON.stringify({{
  start: view.start,
  inView: c.inView.length,
  offLeft: c.offLeft.length,
  offRight: c.offRight.length,
  left: W.formatOffscreenCueLabel(c.offLeft, "left"),
  right: W.formatOffscreenCueLabel(c.offRight, "right"),
  oneCue: W.formatOffscreenCueLabel([{{ kind: "cue", pos: 1 }}], "left"),
  oneLoop: W.formatOffscreenCueLabel([{{ kind: "loop", pos: 1 }}], "right"),
}}));
"""
        )
        self.assertAlmostEqual(out["start"], 123.0, places=1)
        self.assertEqual(out["inView"], 0)
        self.assertEqual(out["offLeft"], 7)
        self.assertEqual(out["offRight"], 0)
        self.assertEqual(out["left"], "← 6 cues · 1 loop")
        self.assertEqual(out["right"], "")
        self.assertEqual(out["oneCue"], "← 1 cue")
        self.assertEqual(out["oneLoop"], "1 loop →")


class AssembleLogicTests(unittest.TestCase):
    def test_sort_by_fit_then_title_and_job_busy(self) -> None:
        out = _node(
            f"""
const A = require({_req("assemble.js")});
const rows = [
  {{ path: "/b", artist: "B", title: "Zed", fit: 0.4 }},
  {{ path: "/a", artist: "A", title: "Yak", fit: 0.9 }},
  {{ path: "/c", artist: "A", title: "Yak 2", fit: 0.9 }},
];
const byFit = A.sortAssemblePlaylist(rows, "fit").map((t) => t.title);
const byCrate = A.sortAssemblePlaylist(rows, "crate").map((t) => t.title);
console.log(JSON.stringify({{
  byFit,
  byCrate,
  queued: A.assembleJobBusy({{ id: "j", status: "queued" }}),
  running: A.assembleJobBusy({{ id: "j", status: "running" }}),
  noId: A.assembleJobBusy({{ status: "running" }}),
  done: A.assembleJobBusy({{ id: "j", status: "ok" }}),
}}));
"""
        )
        self.assertEqual(out["byFit"], ["Yak", "Yak 2", "Zed"])
        self.assertEqual(out["byCrate"], ["Zed", "Yak", "Yak 2"])
        self.assertTrue(out["queued"])
        self.assertTrue(out["running"])
        self.assertFalse(out["noId"])
        self.assertFalse(out["done"])


class StatusHandoffShippedTests(unittest.TestCase):
    def test_promote_and_sort_composers(self) -> None:
        out = _node(
            f"""
const H = require({_req("status_handoff.js")});
const promo = H.composePromoteSuccessHandoff(
  {{ database_updated: true, stems_moved: true }},
  "ready_for_sort"
);
const other = H.composePromoteSuccessHandoff({{}}, "no_cues_found");
const empty = H.composeSortSuccessHandoff({{ database_updated: true }}, 0);
const more = H.composeSortSuccessHandoff({{ database_updated: true }}, 3, ["archived"]);
const loadThen = H.applyStatusAfterLoad(
  {{ message: "Loaded 4" }},
  promo,
  {{ skipStatus: true }}
);
console.log(JSON.stringify({{ promo, other, empty, more, loadThen }}));
"""
        )
        self.assertEqual(out["promo"]["action"]["gotoMode"], "sort")
        self.assertIn("Ready for Sort", out["promo"]["message"])
        self.assertIsNone(out["other"]["action"])
        self.assertEqual(out["empty"]["action"]["gotoMode"], "add_cues")
        self.assertIn("3 left", out["more"]["message"])
        self.assertIsNone(out["more"]["action"])
        self.assertEqual(out["loadThen"]["message"], out["promo"]["message"])

    def test_domain_modules_import_shared_type_aliases(self) -> None:
        for name, needle in (
            ("state.js", "import('./types').Track"),
            ("state.js", "import('./types').RetryJob"),
            ("state.js", "import('./types').TrackRetryKind"),
            ("waveform.js", "import('./types').CuePoint"),
            ("waveform.js", "import('./types').ClassifyWaveMarkers"),
            ("assemble.js", "import('./types').SortAssemblePlaylist"),
            ("placements.js", "import('./types').Placement"),
            ("placements.js", "import('./types').PlacementCardModel"),
            ("transport.js", "import('./types').TrackDisplayTitle"),
        ):
            src = read_static(name)
            self.assertIn(needle, src, f"{name} must use {needle}")
        tsconfig = (UI_STATIC.parent / "tsconfig.json").read_text(encoding="utf-8")
        self.assertIn('"checkJs": true', tsconfig)


if __name__ == "__main__":
    unittest.main()
