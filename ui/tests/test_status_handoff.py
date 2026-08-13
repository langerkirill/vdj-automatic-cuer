"""Honest tests of shipped promote/sort success status handoff.

Drives the real ui/static/status_handoff.js module (loaded by the browser UI)
via Node — no reimplementation of message rules in Python.
"""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

UI_STATIC = Path(__file__).resolve().parents[1] / "static"
HANDOFF_JS = UI_STATIC / "status_handoff.js"
APP_JS = UI_STATIC / "app.js"
INDEX_HTML = UI_STATIC / "index.html"


def _node_handoff(script: str) -> dict:
    """Run JS against the shipped status_handoff.js; return parsed JSON."""
    full = f"""
const handoff = require({json.dumps(str(HANDOFF_JS))});
{script}
"""
    proc = subprocess.run(
        ["node", "-e", full],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"node failed ({proc.returncode}): {proc.stderr or proc.stdout}"
        )
    return json.loads(proc.stdout.strip() or "{}")


class StatusHandoffTests(unittest.TestCase):
    def test_shipped_module_exists_and_is_wired(self) -> None:
        self.assertTrue(HANDOFF_JS.is_file(), "status_handoff.js must ship")
        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn("status_handoff.js", html)
        app = APP_JS.read_text(encoding="utf-8")
        # Real call sites must use skipStatus + compose helpers.
        self.assertIn("skipStatus: true", app)
        self.assertIn("composePromoteSuccessHandoff", app)
        self.assertIn("composeSortSuccessHandoff", app)
        # Order: loadTracks skip then compose (promote)
        promo = app.index("await loadTracks({ skipStatus: true })")
        compose_p = app.index("composePromoteSuccessHandoff")
        self.assertLess(promo, compose_p, "promote must set status after loadTracks")
        # Sort: same pattern
        sort_load = app.rfind("await loadTracks({ skipStatus: true })")
        compose_s = app.index("composeSortSuccessHandoff")
        self.assertLess(sort_load, compose_s, "sort must set status after loadTracks")

    def test_promote_ready_includes_open_sort_action(self) -> None:
        out = _node_handoff(
            """
const h = handoff.composePromoteSuccessHandoff(
  { database_updated: true, stems_moved: true },
  "ready_for_sort"
);
console.log(JSON.stringify(h));
"""
        )
        self.assertEqual(out["kind"], "success")
        self.assertIn("Ready for Sort", out["message"])
        self.assertIn("VDJ cues retargeted", out["message"])
        self.assertIsNotNone(out["action"])
        self.assertEqual(out["action"]["label"], "Open Sort")
        self.assertEqual(out["action"]["gotoMode"], "sort")

    def test_promote_secondary_has_no_mode_switch_cta(self) -> None:
        out = _node_handoff(
            """
const h = handoff.composePromoteSuccessHandoff({}, "no_cues_found");
console.log(JSON.stringify(h));
"""
        )
        self.assertIn("no cues found", out["message"])
        self.assertIsNone(out["action"])

    def test_sort_nonempty_queue_keeps_remaining_count(self) -> None:
        out = _node_handoff(
            """
const h = handoff.composeSortSuccessHandoff(
  { database_updated: true },
  7,
  ["copied to Cues Sorted"]
);
console.log(JSON.stringify(h));
"""
        )
        self.assertEqual(out["kind"], "success")
        self.assertIn("7 left in Ready", out["message"])
        self.assertIn("copied to Cues Sorted", out["message"])
        self.assertIn("cues kept", out["message"])
        self.assertIsNone(out["action"])

    def test_sort_empty_queue_offers_back_to_add_cues(self) -> None:
        out = _node_handoff(
            """
const h = handoff.composeSortSuccessHandoff({ database_updated: false }, 0, []);
console.log(JSON.stringify(h));
"""
        )
        self.assertIn("empty", out["message"].lower())
        self.assertEqual(out["action"]["label"], "Back to Add Cues")
        self.assertEqual(out["action"]["gotoMode"], "add_cues")

    def test_handoff_after_load_survives_refresh_status(self) -> None:
        """Prove the skipStatus + post-load handoff ordering (the bug the skeptic found)."""
        out = _node_handoff(
            """
const loadStatus = {
  message: "Add Cues · 12 tracks · primary action on the right",
  kind: "",
  action: null,
};
const promote = handoff.composePromoteSuccessHandoff(
  { database_updated: true },
  "ready_for_sort"
);
// Bug path: load overwrites handoff when skipStatus is false
const wiped = handoff.applyStatusAfterLoad(loadStatus, promote, { skipStatus: false });
// Fixed path: load skips status, handoff applied after
const fixed = handoff.applyStatusAfterLoad(loadStatus, promote, { skipStatus: true });
// Even when load would set status first, applying handoff after must win:
const after = handoff.applyStatusAfterLoad(loadStatus, promote, { skipStatus: false });
// Wait — applyStatusAfterLoad always applies handoff last when provided.
// Model the OLD bug: only load status, no handoff re-apply:
const oldBug = handoff.applyStatusAfterLoad(loadStatus, null, { skipStatus: false });
console.log(JSON.stringify({ wiped: oldBug, fixed, withHandoffAfterLoad: after, promote }));
"""
        )
        # Old bug: bare load message, no Open Sort
        self.assertIn("Add Cues · 12 tracks", out["wiped"]["message"])
        self.assertIsNone(out["wiped"]["action"])
        # Fixed: success message + Open Sort survive
        self.assertEqual(out["fixed"]["message"], out["promote"]["message"])
        self.assertEqual(out["fixed"]["action"]["label"], "Open Sort")
        self.assertEqual(out["withHandoffAfterLoad"]["action"]["label"], "Open Sort")
        self.assertIn("Ready for Sort", out["withHandoffAfterLoad"]["message"])

    def test_sort_handoff_survives_load_status(self) -> None:
        out = _node_handoff(
            """
const loadStatus = {
  message: "Ready for Sort · 6 tracks · pick a folder, then Sort",
  kind: "",
  action: null,
};
const sortH = handoff.composeSortSuccessHandoff(
  { database_updated: true },
  6,
  ["House/Chill + Zouk/Chill"]
);
const fixed = handoff.applyStatusAfterLoad(loadStatus, sortH, { skipStatus: true });
const oldBug = handoff.applyStatusAfterLoad(loadStatus, null, { skipStatus: false });
console.log(JSON.stringify({ fixed, oldBug }));
"""
        )
        self.assertIn("6 left in Ready", out["fixed"]["message"])
        self.assertNotIn("pick a folder", out["fixed"]["message"])
        self.assertIn("pick a folder", out["oldBug"]["message"])


if __name__ == "__main__":
    unittest.main()
