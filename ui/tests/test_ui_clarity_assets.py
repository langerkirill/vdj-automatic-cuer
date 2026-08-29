"""Structural checks on shipped Music Sorter UI assets (clarity redesign).

These tests read the real static files the server mounts — not copies — so a
regressed HTML/CSS/JS layout fails the suite.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

try:
    from tests.js_assets import UI_STATIC, read_shipped_js, read_static
except ImportError:
    from js_assets import UI_STATIC, read_shipped_js, read_static


class UiClarityAssetsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = (UI_STATIC / "index.html").read_text(encoding="utf-8")
        cls.css = (UI_STATIC / "styles.css").read_text(encoding="utf-8")
        cls.app_js = read_static("app.js")
        cls.js = read_shipped_js()
        cls.placements_js = read_static("placements.js")

    def test_add_cues_lower_tempo_options_visible(self) -> None:
        self.assertIn('id="speedPanel"', self.html)
        self.assertNotIn('<details class="tool-drawer" id="speedPanel">', self.html)
        self.assertIn('data-target-bpm="65"', self.html)
        self.assertIn('data-target-bpm="70"', self.html)
        self.assertIn('data-target-bpm="75"', self.html)
        self.assertIn('data-target-bpm="80"', self.html)
        self.assertIn('id="zoukSpeedBtn"', self.html)
        self.assertIn('id="halfBpmBtn"', self.html)
        self.assertIn("enableZoukSpeed", self.js)

    def test_add_cues_grid_align_is_visible(self) -> None:
        self.assertIn('id="gridAlignBtn"', self.html)
        self.assertIn('id="autoAlignGridBtn"', self.html)
        self.assertIn('id="gridPreflightCard"', self.html)
        self.assertIn('id="gridAlignBar"', self.html)
        self.assertIn("function openGridAlignMode", self.js)
        self.assertIn("function attemptAutoGridAlign", self.js)
        self.assertIn("/api/grid-align/attempt", self.js)
        self.assertIn('id="waveToolsInline"', self.html)
        self.assertIn("wave-tools-inline", self.css)
        self.assertNotIn('id="waveToolsDrawer"', self.html)

    def test_align_apply_stamps_saved_one_without_deep_refetch(self) -> None:
        m = re.search(
            r"async function applyGridAlign\(\) \{.*?\nasync function attemptAutoGridAlign",
            self.app_js,
            re.S,
        )
        self.assertIsNotNone(m)
        body = m.group(0)
        self.assertNotIn("loadDeepGridPreflight", body)
        self.assertIn("scan_phase: anchor", body)
        self.assertIn("drawWaveform()", body)

    def test_best_set_pause_stop_uses_stage_transport(self) -> None:
        self.assertIn('id="bestSetPauseBtn"', self.html)
        self.assertIn('id="bestSetStopBtn"', self.html)
        self.assertIn("toggleStagePlayback", self.app_js)
        self.assertIn("stopStagePlayback", self.app_js)
        self.assertIn("const RECS_NOW_STAMP_MS = 250;", self.app_js)

    def test_waveform_cue_drag_and_place(self) -> None:
        self.assertIn('id="placeCueBtn"', self.html)
        self.assertIn('id="placeLoopBtn"', self.html)
        self.assertIn("function placeLoopAtTime", self.js)
        self.assertIn("function togglePlaceLoopMode", self.js)
        self.assertIn('"/api/add-loop"', self.js)
        self.assertIn("function snapCueDragTime", self.js)
        self.assertIn("function hitTestCueAtClientX", self.js)
        self.assertIn("function existingCueNear", self.js)
        self.assertIn("function commitCueMove", self.js)
        self.assertIn("function placeCueAtTime", self.js)
        self.assertIn("function togglePlaceCueMode", self.js)
        self.assertIn('"/api/add-cue"', self.js)
        self.assertIn('kind: "cue"', self.js)
        self.assertIn("/api/move-poi", self.js)
        self.assertIn("!state.placeCueMode && !state.placeLoopMode && onLoopDragPointerDown", self.js)
        self.assertIn("place-cue-mode", self.css)
        self.assertIn("cue-hover", self.css)

    def test_waveform_cue_x_and_time_sit_on_the_one(self) -> None:
        """Come back to me Cue 1 at 0.030522 must not paint/label as file start."""
        self.assertIn("const x = timeToWaveX(t, padX, plotW, view);", self.app_js)
        self.assertNotIn(
            "timeToWaveX(Math.max(t, view.start)",
            self.app_js,
            "clamping cue x to view.start paints Cue 1 at the left edge",
        )
        self.assertIn("${name} ${fmtTime(t)}", self.app_js)
        transport = read_static("transport.js")
        self.assertIn("Math.round(Math.max(0, Number(seconds) || 0) * 1000)", transport)
        self.assertNotIn("(rem - whole) * 10)", transport)

    def test_recs_follows_now_playing_stamp(self) -> None:
        self.assertIn("RECS_NOW_STAMP_MS = 250", self.js)
        self.assertIn("function pollRecsNowStamp", self.js)
        self.assertIn("/api/recs/now-playing/stamp", self.js)
        self.assertIn("function startRecsNowPlayingPoll", self.js)
        self.assertIn("function renderRecsPollCountdown", self.js)
        self.assertIn('id="recsPollCountdown"', self.html)
        self.assertIn("Live", self.js)
        self.assertIn("startRecsNowPlayingPoll()", self.js)

    def test_recs_shows_removed_because_played(self) -> None:
        self.assertIn('id="recsRemovedPlays"', self.html)
        self.assertIn("removed_recent_label", self.app_js)
        self.assertIn("recsRemovedPlays", self.app_js)

    def test_three_modes_present(self) -> None:
        for mode in (
            "add_cues",
            "set_overview",
            "practice",
            "best_set",
            "recs",
            "assemble",
        ):
            self.assertIn(f'data-mode="{mode}"', self.html)
        self.assertIn("Add Cues", self.html)
        self.assertIn("Set Overview", self.html)
        self.assertIn("Practice", self.html)
        self.assertIn("Assemble", self.html)
        self.assertIn("assemblePanel", self.html)
        self.assertIn("Assemble Pajamathon", self.html)

    def test_add_cues_retried_filters_are_visible(self) -> None:
        self.assertIn('data-filter="retried_cues"', self.html)
        self.assertIn('data-filter="retried_loops"', self.html)
        self.assertIn('data-filter="retried_both"', self.html)
        self.assertIn("Retried cues", self.html)
        self.assertIn("Retried loops", self.html)
        self.assertIn("Tried both", self.html)
        self.assertIn("function trackRetryKind", self.js)
        self.assertIn("retry_history", self.js)
        self.assertIn('retried_cues"', self.js)

    def test_add_cues_ready_requires_two_loops(self) -> None:
        self.assertIn("loopN >= 2", self.js)
        self.assertIn("cueN >= 2 && loopN >= 2 && hasGrid", self.js)

    def test_add_cues_lists_ready_tracks_first(self) -> None:
        self.assertIn("function addCuesReadinessRank", self.js)
        self.assertIn("function sortAddCuesIndexes", self.js)
        self.assertIn("sortAddCuesIndexes(", self.js)
        self.assertIn('if (status === "ready") return 0', self.js)

    def test_add_cues_soft_refresh_during_autocue(self) -> None:
        self.assertIn("function loadTracks", self.js)
        self.assertIn("silent", self.js)
        self.assertIn("scheduleLoadTracks", self.js)
        self.assertIn("Updating cue list", self.js)

    def test_ready_for_sort_copy_cues_to_pajamathon(self) -> None:
        self.assertIn("/api/copy-cues", self.js)
        self.assertIn("function copyCuesToPlacement", self.js)
        self.assertIn("Copy cues", self.js)
        self.assertIn("placements.sets", self.js)
        self.assertIn("Pajamathon", self.js)
        self.assertIn("placement-copy-cues-btn", self.js)
        self.assertIn("placement-copy-cues-btn", self.css)
        self.assertIn("/api/copy-cues-all", self.js)
        self.assertIn("function copyCuesToAllPlacements", self.js)
        self.assertIn("Copy cues to all", self.js)
        self.assertIn("placement-copy-cues-all-btn", self.js)
        self.assertIn("placement-copy-cues-all-btn", self.css)
        self.assertIn("/api/add-to-set", self.js)
        self.assertIn("function addTrackToPajamathon", self.js)
        self.assertIn("Add to Pajamathon", self.js)
        self.assertIn("Delete from folder", self.js)
        self.assertIn("allowDelete: true", self.js)
        self.assertNotIn("allowDelete: false", self.js)
        self.assertIn("placement-add-set-btn", self.js)
        self.assertIn('p.root_name === "Zouk"', self.js)
        self.assertIn('p.root_name === "House"', self.js)
        self.assertIn("function loadTrackPlacements", self.js)
        self.assertIn("function applyExistingSetPlacement", self.js)
        self.assertIn("function mergeLoadedPlacements", self.js)
        self.assertIn("loadTrackPlacements(selected)", self.js)
        self.assertIn("Looking up House / Zouk / Pajamathon", self.placements_js)
        self.assertIn("already_exists", self.js)
        self.assertIn("placementsLoaded", self.js)
        self.assertIn("/api/track-placements", self.js)

    def test_add_cues_pajamathon_section(self) -> None:
        self.assertIn('id="crateFilter"', self.html)
        self.assertIn('data-crate="pajamathon"', self.html)
        self.assertIn('data-crate="inbox"', self.html)
        self.assertIn('data-crate="cueing"', self.html)
        self.assertIn("function isTrackCueing", self.js)
        self.assertIn("function hydrateAutocueJobs", self.js)
        self.assertIn("Currently cueing", self.js)
        self.assertIn("function addCuesSection", self.js)
        self.assertIn('id="batchPajamathonCuesBtn"', self.html)
        self.assertIn("pajamathon_not_cued", self.js)
        self.assertIn("Batch Pajamathon cues", self.html)
        self.assertIn('id="batchFixGridsBtn"', self.html)
        self.assertIn("function batchFixPajamathonGrids", self.js)
        self.assertIn("/api/grid-fix/batch", self.js)
        self.assertIn("Fix Pajamathon grids", self.html)

    def test_add_cues_delete_button_is_visible(self) -> None:
        self.assertIn('id="deleteAddCuesBtn"', self.html)
        self.assertIn("Delete from Add Cues", self.html)
        self.assertIn("function deleteAddCuesTrack", self.js)
        self.assertIn("function isPajamathonSetQueueTrack", self.js)
        self.assertIn("Delete from Pajamathon", self.js)
        self.assertIn("/api/delete-add-cues", self.js)
        self.assertIn("kept_hardlinks", self.js)
        self.assertIn("Cue in set · confirm a lane to sort", self.js)
        self.assertIn("Sets/${track.relative_path", self.js)
        self.assertIn("Sets/Pajamathon — Approve after you listen", self.js)
        self.assertIn("Next Pajamathon track", self.js)
        self.assertIn("Approve set cues", self.js)
        self.assertIn("/api/approve-set-cues", self.js)
        self.assertIn('data-filter="needs_sort"', self.html)
        self.assertIn('data-filter="recently_cued"', self.html)
        self.assertIn("Recently cued", self.html)
        self.assertIn("isRecentlyCued", self.js)
        self.assertIn('id="filterReadyBtn"', self.html)
        self.assertIn("function syncReadinessFilterLabels", self.js)
        self.assertIn('readyBtn.textContent = "Ready"', self.js)
        self.assertIn('const UI_BUILD = "20260829-recs-event-plays"', self.js)
        self.assertIn("20260829-recs-event-plays", self.html)
        app_py = (Path(__file__).resolve().parents[1] / "app.py").read_text(
            encoding="utf-8"
        )
        js_build = re.search(r'const UI_BUILD = "([^"]+)"', self.js)
        py_build = re.search(r'^UI_BUILD = "([^"]+)"', app_py, re.M)
        self.assertTrue(js_build and py_build)
        self.assertEqual(
            js_build.group(1),
            py_build.group(1),
            "app.js UI_BUILD must match app.py or /api/health reloads forever",
        )
        self.assertIn('http-equiv="Cache-Control"', self.html)
        delete_at = self.html.find('id="deleteAddCuesBtn"')
        drawer_at = self.html.find("review-section-secondary")
        self.assertGreater(delete_at, 0)
        self.assertGreater(drawer_at, 0)
        self.assertLess(delete_at, drawer_at)
        self.assertIn("function renderAddCuesTrackSections", self.js)
        self.assertIn("track-section-head", self.css)
        self.assertIn("pajamathon", self.js)
        self.assertIn("crateFilter", self.js)

    def test_primary_action_regions(self) -> None:
        # Add Cues rail primary + Sort CTA + Practice analyze
        for element_id in (
            "approveBtnSide",
            "sortBtn",
            "practiceAnalyzeBtn",
            "reviewPanel",
            "foldersPanel",
            "trackList",
            "pipelineStrip",
        ):
            self.assertIn(f'id="{element_id}"', self.html)

    def test_zone_hierarchy_markup(self) -> None:
        for cls in ("zone-queue", "zone-stage", "zone-rail", "pipeline-strip", "btn-cta"):
            self.assertTrue(
                cls in self.html or cls in self.css,
                f"missing zone/class {cls}",
            )

    def test_design_tokens_in_css(self) -> None:
        for token in ("--accent", "--accent-rgb", "--bg-elevated", "--good", "--bad"):
            self.assertIn(token, self.css)
        self.assertIn("pipeline-strip", self.css)
        self.assertIn("sticky-actions", self.css)
        self.assertIn("empty-state", self.css)
        # Inspire polish spacing / elevation tokens
        self.assertIn("--space-3", self.css)
        self.assertIn("--elev-2", self.css)
        self.assertIn("--surface-glow", self.css)

    def test_practice_stack_layout_in_css(self) -> None:
        """Playback + map above recs; full-width map (not a narrow side column)."""
        self.assertIn("practice-stack-layout", self.css)
        # Stacked main column (not 240–300px | 1fr side-by-side for practice)
        self.assertIn("body.mode-practice .shell .main", self.css)
        self.assertRegex(
            self.css,
            r"body\.mode-practice\s+\.shell\s+\.main\s*\{[^}]*grid-template-columns:\s*1fr\s*!important",
        )
        self.assertIn("body.mode-practice #practiceWaveformWrap", self.css)
        self.assertRegex(
            self.css,
            r"body\.mode-practice\s+#practiceWaveformWrap\s*\{[^}]*width:\s*100%\s*!important",
        )
        self.assertIn("schedulePracticeWaveRedraw", self.js)

    def test_assemble_keeps_cached_lists_while_scoring(self) -> None:
        self.assertIn("previousResult", self.js)
        self.assertIn("state.assemblePreview?.result", self.js)
        self.assertIn("if (!latest.job.result && data.result)", self.js)
        self.assertIn("assemblePollSeq", self.js)
        self.assertIn("recoverAssembleJob", self.js)
        self.assertIn("function unstickAssembleJob", self.js)
        self.assertIn("function assembleJobBusy", self.js)

    def test_assemble_playlist_can_sort_by_fit(self) -> None:
        self.assertIn('id="assemblePlaylistSort"', self.html)
        self.assertIn('data-pl-sort="fit"', self.html)
        self.assertIn('data-pl-sort="crate"', self.html)
        self.assertIn("function sortAssemblePlaylist", self.js)
        self.assertIn("assemblePlaylistSort", self.js)
        self.assertIn("state.assemblePlaylistSort", self.js)
        self.assertIn("sortAssemblePlaylist(", self.js)
        self.assertRegex(self.html, r"/static/styles\.css(\?[^\"']*)?")

    def test_assemble_mix_tuners_present(self) -> None:
        self.assertIn('id="assembleMixLanes"', self.html)
        self.assertIn('id="assembleMixApply"', self.html)
        self.assertIn('data-lane="chill"', self.html)
        self.assertIn('data-lane="rnb"', self.html)
        self.assertIn('data-lane="tribal"', self.html)
        self.assertIn('data-lane="remixes"', self.html)
        self.assertIn("320 kbps", self.html)
        self.assertIn('data-lane="neo_zouk"', self.html)
        self.assertGreaterEqual(self.html.count('class="assemble-mix-pct"'), 18)
        self.assertIn("assemble-mix-pct", self.html)
        self.assertIn("function readAssembleMixShares", self.js)
        self.assertIn("/api/assemble/rebalance", self.js)
        self.assertIn("function onAssembleChromeClick", self.js)
        self.assertIn('closest("#assembleStartBtn")', self.js)

    def test_assemble_min_fit_control(self) -> None:
        self.assertIn('id="assembleMinFit"', self.html)
        self.assertIn("function readAssembleMinFit", self.js)
        self.assertIn("min_fit", self.js)
        self.assertIn("assemble-mix-pct", self.js)
        self.assertIn('getAttribute("data-pl-sort")', self.js)

    def test_assemble_mix_prefs_persist(self) -> None:
        self.assertIn("/api/assemble/mix-prefs", self.js)
        self.assertIn("function loadAssembleShares", self.js)
        self.assertIn("function persistAssembleShares", self.js)
        self.assertIn("function scheduleSaveAssembleMixPrefs", self.js)
        self.assertIn("mix_prefs", self.js)
        self.assertIn('data-lane="chill"', self.html)
        self.assertIn('value="24" data-lane="chill"', self.html)
        self.assertIn('value="6" data-lane="remixes"', self.html)
        self.assertIn('value="0" data-lane="tribal"', self.html)
        self.assertIn('addEventListener("input"', self.js)

    def test_state_messaging_helpers_in_js(self) -> None:
        self.assertIn("function updatePipelineStrip", self.js)
        self.assertIn("function emptyStateHtml", self.js)
        self.assertIn("function setStatus", self.js)
        # empty-state CTAs for pipeline handoff
        self.assertIn("Open Sort", self.js)
        self.assertIn("Open Add Cues", self.js)
        # Success handoff must not be clobbered by loadTracks (skeptic regression).
        self.assertIn("skipStatus: true", self.js)
        self.assertIn("composePromoteSuccessHandoff", self.js)
        self.assertIn("composeSortSuccessHandoff", self.js)
        self.assertIn("status_handoff.js", self.html)

    def test_recs_show_gemini_genre_guess(self) -> None:
        self.assertIn("genre_source", self.js)
        self.assertIn("Gemini genre guess", self.js)
        self.assertIn("badge.genre.guessed", self.css)

    def test_recs_now_playing_ignores_stale_fetches(self) -> None:
        self.assertIn("recsNowSeq", self.js)
        self.assertIn("refresh=1", self.js)
        self.assertIn("recsLastPlayLabel", self.js)
        self.assertIn("timeoutMs", self.js)

    def test_recs_show_transition_timing(self) -> None:
        self.assertIn("recsNowTiming", self.html)
        self.assertIn("recs-card-timing", self.js)
        self.assertIn("fills", self.js)
        self.assertIn("badge.timing", self.css)

    def test_html_references_clarity_assets(self) -> None:
        # Cache-bust query may change; require the shipped asset paths.
        self.assertRegex(self.html, r"/static/styles\.css(\?[^\"']*)?")
        self.assertRegex(self.html, r"/static/app\.js(\?[^\"']*)?")
        self.assertRegex(self.html, r"/static/state\.js(\?[^\"']*)?")
        self.assertRegex(self.html, r"/static/transport\.js(\?[^\"']*)?")
        self.assertRegex(self.html, r"/static/waveform\.js(\?[^\"']*)?")
        self.assertRegex(self.html, r"/static/practice\.js(\?[^\"']*)?")
        self.assertRegex(self.html, r"/static/assemble\.js(\?[^\"']*)?")
        self.assertRegex(self.html, r"/static/status_handoff\.js(\?[^\"']*)?")
        # not an empty shell
        self.assertGreater(len(self.html), 2000)
        self.assertIsNotNone(re.search(r"<body[^>]*>", self.html))

    def test_opening_a_track_does_not_autoplay(self) -> None:
        """Selecting a track must not start audio unless the user already hit Play."""
        self.assertIn("function shouldAutoplayOnSelect", self.js)
        self.assertIn("function playAudio", self.js)
        self.assertIn("state.allowAutoplay", self.js)
        self.assertIn("allowAutoplay = true", self.js)
        self.assertIn("if (!audio.src) return Promise.resolve()", self.js)
        self.assertIn("shouldAutoplayOnSelect()", self.js)
        self.assertRegex(self.js, r"if\s*\(\s*shouldAutoplayOnSelect\(\)\s*\)")
        self.assertNotRegex(
            self.js,
            r"if\s*\(\s*!isPracticeMode\(\)\s*\)\s*\{\s*audio\.play\(\)\.catch\(\(\)\s*=>\s*\{\}\s*\)",
        )

    def test_practice_mix_can_be_excluded_from_best(self) -> None:
        self.assertIn('id="practiceExcludeBestBtn"', self.html)
        self.assertIn("Exclude from Best", self.html)
        self.assertIn("function togglePracticeExcludeFromBest", self.app_js)
        self.assertIn("/api/practice/mix-settings", self.app_js)
        self.assertIn("exclude_from_best", self.app_js)
        self.assertIn("practice-exclude-best", self.css)

    def test_quiet_session_mutes_agent_checks(self) -> None:
        """?quiet=1 / ?mute=1 / webdriver must keep the sorter silent."""
        self.assertIn("function wantsQuietSession", self.js)
        self.assertIn("function applyQuietSession", self.js)
        self.assertIn("function syncQuietSessionUi", self.js)
        self.assertIn('params.get("quiet")', self.js)
        self.assertIn('params.get("mute")', self.js)
        self.assertIn("nav.webdriver", read_static("transport.js"))
        self.assertIn("state.quietSession", self.js)
        self.assertIn("applyQuietSession()", self.js)
        apply_at = self.js.find("applyQuietSession()")
        load_at = self.js.find("await loadTracks()")
        self.assertGreater(apply_at, 0)
        self.assertGreater(load_at, apply_at)
        self.assertIn('id="quietSessionChip"', self.html)
        self.assertIn("quiet-session-chip", self.css)
        self.assertIn("Sound off", self.html)

    def test_fmt_time_milliseconds_not_file_start(self) -> None:
        transport = read_static("transport.js")
        self.assertIn("function fmtTime", transport)
        self.assertIn("* 1000)", transport)
        self.assertNotIn("(rem - whole) * 10", transport)
        self.assertIn("MusicSorterTransport.fmtTime", self.app_js)
        self.assertIn("timeToWaveX(t, padX, plotW, view", self.app_js)
        self.assertIn("let t = Number(p.pos) || 0", self.app_js)



if __name__ == "__main__":
    unittest.main()
