"""Regression: Sort must show House/Zouk/Pajamathon copies, not lie + error.

The broken path (2026-08-14):
- /api/tracks?mode=sort ships empty placements (lazy, by design)
- loadTracks rendered the selected Ready track and never called /api/track-placements
- Already-in-library said "Not in Pajamathon" for 01 - YASMINE - Apaixona.flac
- Add to Pajamathon then found Sets/Pajamathon 2026/187. YASMINE - Apaixona.flac
  and surfaced that as a red error

These tests drive the real lookup helpers, add-to-set return value, the shipped
placements.js card model (via Node), and the app.js call sites.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sorter import library as library_mod
from sorter import relocate as relocate_mod

UI_DIR = Path(__file__).resolve().parents[1]
UI_STATIC = UI_DIR / "static"
PLACEMENTS_JS = UI_STATIC / "placements.js"
APP_JS = UI_STATIC / "app.js"
APP_PY = UI_DIR / "app.py"
INDEX_HTML = UI_STATIC / "index.html"


def _js_function_body(src: str, name: str) -> str:
    for prefix in (f"async function {name}(", f"function {name}("):
        start = src.find(prefix)
        if start >= 0:
            break
    else:
        raise AssertionError(f"missing function {name}")
    brace = src.find("{", start)
    depth = 0
    for index, char in enumerate(src[brace:], brace):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return src[start : index + 1]
    raise AssertionError(f"unclosed function {name}")


def _node_placements(script: str) -> dict:
    full = f"""
const placements = require({json.dumps(str(PLACEMENTS_JS))});
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


class YasmineLibraryLookupTests(unittest.TestCase):
    """The exact Ready vs Zouk vs Pajamathon filenames from the bug."""

    def _tree(self, tmp: str) -> dict[str, Path]:
        root = Path(tmp)
        ready = root / "Ready For Sort"
        zouk = root / "Zouk" / "Lamba"
        house = root / "House"
        archive = root / "Cues Sorted"
        paj = root / "Sets" / "Pajamathon 2026"
        other_set = root / "Sets" / "Z4"
        for folder in (ready, zouk, house, archive, paj, other_set):
            folder.mkdir(parents=True)
        ready_file = ready / "01 - YASMINE - Apaixona.flac"
        zouk_file = zouk / "01 - YASMINE - Apaixona.flac"
        set_file = paj / "187. YASMINE - Apaixona.flac"
        decoy = other_set / "01 - YASMINE - Apaixona.flac"
        ready_file.write_bytes(b"ready")
        zouk_file.write_bytes(b"zouk")
        set_file.write_bytes(b"set")
        decoy.write_bytes(b"z4")
        return {
            "ready": ready,
            "ready_file": ready_file,
            "zouk": root / "Zouk",
            "house": house,
            "archive": archive,
            "sets": root / "Sets",
            "zouk_file": zouk_file,
            "set_file": set_file,
        }

    def test_fuzzy_lookup_finds_zouk_and_pajamathon_not_other_sets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tree = self._tree(tmp)
            with patch.dict(
                library_mod.LIBRARIES,
                {"House": tree["house"], "Zouk": tree["zouk"]},
                clear=True,
            ), patch.object(library_mod, "CUES_SORTED", tree["archive"]), patch.object(
                library_mod, "SETS_ROOT", tree["sets"]
            ):
                library_mod.invalidate_placement_indexes()
                libs = library_mod.find_library_matches(tree["ready_file"].name)
                sets = library_mod.find_set_matches(tree["ready_file"].name)
                archive = library_mod.find_cues_sorted_matches(tree["ready_file"].name)

            self.assertEqual(len(libs), 1)
            self.assertEqual(libs[0]["root_name"], "Zouk")
            self.assertEqual(libs[0]["relative_path"], "Lamba/01 - YASMINE - Apaixona.flac")
            self.assertEqual(len(sets), 1)
            self.assertEqual(
                sets[0]["relative_path"],
                "Pajamathon 2026/187. YASMINE - Apaixona.flac",
            )
            self.assertEqual(sets[0]["event"], "Pajamathon 2026")
            self.assertEqual(archive, [])

    def test_add_to_set_returns_existing_pajamathon_instead_of_raising(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tree = self._tree(tmp)
            with patch.object(relocate_mod, "SETS_ROOT", tree["sets"]), patch(
                "sorter.library.SETS_ROOT", tree["sets"]
            ), patch.object(relocate_mod, "READY_FOR_SORT", tree["ready"]):
                result = relocate_mod.add_track_to_event_set(
                    tree["ready_file"],
                    sets_root=tree["sets"],
                    create_backup=False,
                    dry_run=True,
                )
            self.assertTrue(result["ok"])
            self.assertTrue(result["already_exists"])
            self.assertEqual(
                Path(result["dest_path"]).resolve(), tree["set_file"].resolve()
            )
            self.assertEqual(
                result["relative_path"],
                "Pajamathon 2026/187. YASMINE - Apaixona.flac",
            )
            self.assertEqual(result["event"], "Pajamathon 2026")
            self.assertTrue(tree["ready_file"].is_file())
            self.assertEqual(tree["set_file"].read_bytes(), b"set")


class LazyListContractTests(unittest.TestCase):
    def test_sort_and_add_cues_lists_skip_placements_and_expose_lazy_lookup(self) -> None:
        app_src = APP_PY.read_text(encoding="utf-8")
        self.assertGreaterEqual(
            app_src.count("include_placements=False"),
            2,
            "Sort and Add Cues list loads must stay lazy",
        )
        self.assertIn('@app.get("/api/track-placements")', app_src)
        self.assertIn("find_set_matches", app_src)
        self.assertIn("find_library_matches", app_src)
        start = app_src.index("def get_tracks")
        end = app_src.index("def get_track_placements")
        body = app_src[start:end]
        self.assertIn("include_placements=False", body)
        self.assertNotIn("include_placements=True", body)

    def test_lazy_endpoint_enriches_selected_track(self) -> None:
        app_src = APP_PY.read_text(encoding="utf-8")
        start = app_src.index("def get_track_placements")
        end = app_src.index("def get_libraries")
        body = app_src[start:end]
        self.assertIn("include_placements=True", body)
        self.assertIn("find_set_matches", body)
        self.assertIn("find_library_matches", body)


class PlacementCardModelTests(unittest.TestCase):
    def test_module_is_shipped_and_wired(self) -> None:
        self.assertTrue(PLACEMENTS_JS.is_file(), "placements.js must ship")
        html = INDEX_HTML.read_text(encoding="utf-8")
        js = APP_JS.read_text(encoding="utf-8")
        self.assertIn("placements.js", html)
        html_idx = html.index("placements.js")
        app_idx = html.index("app.js")
        self.assertLess(html_idx, app_idx, "placements.js must load before app.js")
        self.assertIn("MusicSorterPlacements", js)
        self.assertIn("placementCardModel", js)

    def test_selected_pajamathon_set_file_shows_as_in_pajamathon(self) -> None:
        """Add Cues Pajamathon tab is the set file — Finder and the card must agree."""
        out = _node_placements(
            """
const track = {
  path: "/Music/DJ/Music/Sets/Pajamathon 2026/029. Naked Trimmed.wav",
  relative_path: "Pajamathon 2026/029. Naked Trimmed.wav",
  group: "Pajamathon 2026",
  is_cued: true,
  cues: { cue_count: 6, loop_count: 3 },
  placements: placements.emptyPlacements(),
};
console.log(JSON.stringify(placements.placementCardModel(track)));
"""
        )
        self.assertTrue(out["inPajamathon"])
        self.assertFalse(out["showAddButton"])
        self.assertEqual(out["state"], "found")
        self.assertGreaterEqual(out["totalN"], 1)
        self.assertEqual(
            out["sets"][0]["relative_path"],
            "Pajamathon 2026/029. Naked Trimmed.wav",
        )
        self.assertTrue(out["sets"][0]["is_current"])

    def test_track_is_pajamathon_set_file_path_and_group(self) -> None:
        out = _node_placements(
            """
const hits = {
  setPath: placements.trackIsPajamathonSetFile({
    path: "/Users/x/Music/DJ/Music/Sets/Pajamathon 2026/087. Give A Little.mp3",
  }),
  setGroup: placements.trackIsPajamathonSetFile({
    path: "/Users/x/Music/DJ/Music/Sets/Pajamathon 2026/087. Give A Little.mp3",
    group: "Pajamathon 2026",
  }),
  inbox: placements.trackIsPajamathonSetFile({
    path: "/Users/x/Music/DJ/Music/Cues/Add Cues/Pajamathon/Give A Little.mp3",
    group: "Pajamathon",
    section: "pajamathon",
  }),
  zouk: placements.trackIsPajamathonSetFile({
    path: "/Users/x/Music/DJ/Music/Zouk/Energy/Give A Little.mp3",
  }),
};
console.log(JSON.stringify(hits));
"""
        )
        self.assertTrue(out["setPath"])
        self.assertTrue(out["setGroup"])
        self.assertFalse(out["inbox"])
        self.assertFalse(out["zouk"])

    def test_empty_list_payload_does_not_claim_missing(self) -> None:
        """The Sort list stub is empty — that is unknown, not 'not in Pajamathon'."""
        out = _node_placements(
            """
const track = {
  path: "/ready/01 - YASMINE - Apaixona.flac",
  placements: placements.emptyPlacements(),
};
console.log(JSON.stringify(placements.placementCardModel(track)));
"""
        )
        self.assertEqual(out["state"], "loading")
        self.assertNotEqual(out["title"], "Not in Pajamathon")
        self.assertFalse(out["showAddButton"])
        self.assertIn("House / Zouk / Pajamathon", out["note"])

    def test_loaded_hits_show_already_in_library(self) -> None:
        out = _node_placements(
            """
const track = {
  placementsLoaded: true,
  is_cued: true,
  placements: {
    library: [{ path: "/Zouk/Lamba/01 - YASMINE - Apaixona.flac", root_name: "Zouk", relative_path: "Lamba/01 - YASMINE - Apaixona.flac", is_cued: true }],
    cues_sorted: [],
    sets: [{ path: "/Sets/Pajamathon 2026/187. YASMINE - Apaixona.flac", root_name: "Pajamathon 2026", event: "Pajamathon 2026", relative_path: "Pajamathon 2026/187. YASMINE - Apaixona.flac", is_cued: false }],
  },
};
console.log(JSON.stringify(placements.placementCardModel(track)));
"""
        )
        self.assertEqual(out["state"], "found")
        self.assertTrue(out["title"].startswith("Already in library"))
        self.assertTrue(out["inPajamathon"])
        self.assertEqual(out["totalN"], 2)
        self.assertFalse(out["showAddButton"])

    def test_loaded_empty_is_the_only_missing_state(self) -> None:
        out = _node_placements(
            """
const track = {
  placementsLoaded: true,
  placements: placements.emptyPlacements(),
};
console.log(JSON.stringify(placements.placementCardModel(track)));
"""
        )
        self.assertEqual(out["state"], "missing")
        self.assertEqual(out["title"], "Not in Pajamathon")
        self.assertTrue(out["showAddButton"])

    def test_already_exists_payload_paints_pajamathon_row(self) -> None:
        out = _node_placements(
            """
const track = { placements: placements.emptyPlacements() };
placements.applyExistingSetPlacement(track, {
  dest_path: "/Sets/Pajamathon 2026/187. YASMINE - Apaixona.flac",
  relative_path: "Pajamathon 2026/187. YASMINE - Apaixona.flac",
  event: "Pajamathon 2026",
});
const model = placements.placementCardModel(track);
console.log(JSON.stringify({
  ...model,
  placementsLoaded: Boolean(track.placementsLoaded),
}));
"""
        )
        self.assertEqual(out["state"], "found")
        self.assertTrue(out["inPajamathon"])
        self.assertFalse(out["showAddButton"])
        self.assertTrue(
            out["placementsLoaded"],
            "optimistic set row must survive the next lazy list refresh",
        )
        self.assertEqual(out["sets"][0]["relative_path"], "Pajamathon 2026/187. YASMINE - Apaixona.flac")

    def test_list_refresh_keeps_loaded_zouk_and_set_rows(self) -> None:
        out = _node_placements(
            """
const prev = [{
  path: "/ready/01 - YASMINE - Apaixona.flac",
  placementsLoaded: true,
  placements: {
    library: [{ path: "/Zouk/x.flac", root_name: "Zouk", relative_path: "x.flac" }],
    cues_sorted: [],
    sets: [{ path: "/Sets/Pajamathon 2026/187. YASMINE - Apaixona.flac", event: "Pajamathon 2026", root_name: "Pajamathon 2026", relative_path: "Pajamathon 2026/187. YASMINE - Apaixona.flac" }],
    in_sets: true,
    already_sorted: true,
  },
}];
const next = [{
  path: "/ready/01 - YASMINE - Apaixona.flac",
  name: "01 - YASMINE - Apaixona.flac",
  placements: placements.emptyPlacements(),
}];
const merged = placements.mergeLoadedPlacements(prev, next);
console.log(JSON.stringify(placements.placementCardModel(merged[0])));
"""
        )
        self.assertEqual(out["state"], "found")
        self.assertEqual(out["totalN"], 2)
        self.assertTrue(out["inPajamathon"])


class AppJsPlacementCallsiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.js = APP_JS.read_text(encoding="utf-8")

    def test_load_tracks_fetches_placements_for_the_selected_row(self) -> None:
        start = self.js.index("async function loadTracks")
        end = self.js.index("function applyModeUi")
        body = self.js[start:end]
        assign = body.index("mergeLoadedPlacements")
        fetch = body.index("loadTrackPlacements(currentTrack())")
        self.assertLess(
            assign,
            fetch,
            "list refresh must keep loaded rows, then look up the selected track",
        )
        render = body.index("renderPlayer()")
        self.assertLess(render, fetch)

    def test_select_track_always_loads_placements(self) -> None:
        body = _js_function_body(self.js, "selectTrack")
        self.assertIn("loadTrackPlacements(selected)", body)
        self.assertNotIn(
            "already_sorted",
            body,
            "empty list placements must not skip the lazy lookup",
        )
        self.assertNotIn("in_sets", body)

    def test_add_to_pajamathon_already_exists_is_not_an_error(self) -> None:
        body = _js_function_body(self.js, "addTrackToPajamathon")
        self.assertIn("already_exists", body)
        self.assertIn("applyExistingSetPlacement", body)
        exists_at = body.index("already_exists")
        window = body[exists_at : exists_at + 500]
        self.assertNotIn(
            '"error"',
            window,
            "already in Pajamathon must paint the row, not setStatus(..., error)",
        )
        first_already = body.index("Already in Pajamathon")
        self.assertNotIn('"error"', body[first_already : first_already + 180])
        self.assertIn(
            "currentTrack()?.path !== track.path",
            body,
            "do not paint Already-in-Pajamathon onto a different selected track",
        )

    def test_cue_copy_receipt_lists_destinations(self) -> None:
        out = _node_placements(
            """
const receipt = placements.normalizeCueCopyReceipt({
  copied: 3,
  skipped: 0,
  failed: 0,
  copied_cues: 6,
  copied_loops: 3,
  source_path: "/Sets/Pajamathon 2026/014. Quimera.flac",
  results: [
    { ok: true, dest_path: "/Zouk/Chill/Quimera.flac", root_name: "Zouk" },
    { ok: true, dest_path: "/House/Chill/Quimera.flac", root_name: "House" },
    { ok: true, dest_path: "/Cues Sorted/Chill/Quimera.flac", root_name: "Cues Sorted" },
    { ok: false, dest_path: "/Sets/Pajamathon 2026/014. Quimera.flac", status: "skipped" },
  ],
}, "/Sets/Pajamathon 2026/014. Quimera.flac");
console.log(JSON.stringify({
  label: placements.cueCopyReceiptLabel(receipt),
  zouk: Boolean(placements.cueCopyDestForPath(receipt, "/Zouk/Chill/Quimera.flac")),
  archiveName: placements.cueCopyDestName({ root: "Cues Sorted" }),
  sourceHit: placements.cueCopyDestForPath(receipt, "/Sets/Pajamathon 2026/014. Quimera.flac"),
  copied: receipt.copied,
  cues: receipt.cues,
}));
"""
        )
        self.assertIn("Just copied", out["label"])
        self.assertIn("6 cue", out["label"])
        self.assertIn("3 loop", out["label"])
        self.assertIn("Zouk", out["label"])
        self.assertIn("House", out["label"])
        self.assertIn("Archive", out["label"])
        self.assertTrue(out["zouk"])
        self.assertEqual(out["archiveName"], "Archive")
        self.assertIsNone(out["sourceHit"])
        self.assertEqual(out["copied"], 3)
        self.assertEqual(out["cues"], 6)

    def test_copy_paints_receipt_on_card(self) -> None:
        self.assertIn("function rememberCueCopy", self.js)
        self.assertIn("placement-copy-receipt", self.js)
        self.assertIn("Just copied", self.js)
        self.assertIn("placement-just-copied", self.js)
        self.assertIn("rememberCueCopy(track.path, r)", self.js)
        css = (UI_STATIC / "styles.css").read_text(encoding="utf-8")
        self.assertIn("placement-copy-receipt", css)
        self.assertIn("is-just-copied", css)

    def test_delete_and_copy_force_refresh_placements(self) -> None:
        for name, nxt in (
            ("deleteLibraryPlacement", "function allPlacementHits"),
            ("copyCuesToPlacement", "async function copyCuesToAllPlacements"),
            ("copyCuesToAllPlacements", "async function addTrackToPajamathon"),
        ):
            start = self.js.index(f"async function {name}")
            end = self.js.index(nxt)
            body = self.js[start:end]
            self.assertIn(
                "loadTrackPlacements",
                body,
                f"{name} must refetch House/Zouk/Sets after the write",
            )
            self.assertIn(
                "{ force: true }",
                body,
                f"{name} must force-refresh so mergeLoadedPlacements cannot keep a deleted row",
            )


if __name__ == "__main__":
    unittest.main()
