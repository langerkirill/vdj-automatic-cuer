const state = {
  mode: "sort", // sort | add_cues
  accentTheme: "lime",
  tracks: [],
  index: 0,
  library: "Zouk", // House | Zouk | Both
  folders: [],
  folderTrees: null,
  selectedPath: "",
  expanded: new Set(),
  filter: "",
  trackSearch: "",
  readinessFilter: "all",
  recommendation: null,
  recommendAbort: null,
  health: null,
  activeCueKey: null,
  waveform: null, // { path, duration, peaks, bins }
  waveformLoading: false,
  waveformError: null,
  waveformAbort: null,
  waveZoom: 1, // 1 = full track, higher = zoomed in
  waveOffset: 0, // visible window start (seconds)
  targetBpm: 75,
  playbackRate: 1,
  zoukSpeedOn: false,
  halfBpm: false, // VDJ double-time fix: treat source BPM as half (140 → 70)
  loopPlaybackOn: false, // re-enter VDJ loop regions to audition them
  activeLoopKey: null, // cueKey of loop currently being auditioned
  loopRaf: null,
  cueListFilter: "all", // all | cues | loops
  notesPath: null, // path notes textarea is bound to
  notesSaveTimer: null,
  notesSaveGen: 0, // ignore stale save responses
  notesDirty: false,
  // path -> { id, name, message, status, writeScope, pollTimer }
  retryJobs: {},
  batchId: null,
  batchPollTimer: null,
  gridPreflight: null, // deep preflight for current track
  trackGen: 0, // bumped on each selection to ignore stale async results
  tracksLoadGen: 0, // bumped on each list load / mode switch to drop stale /api/tracks responses
  waveformDebounce: null,
  lastDrawMs: 0,
  trackMeta: null, // { bitrate_kbps, codec, sample_rate, ... } for current path
  metaAbort: null,
};

const WAVE_PAD_X = 8;
const WAVE_ZOOM_MIN = 1;
const WAVE_ZOOM_MAX = 48;

const CUE_COLORS = {
  blue: "#3b82f6",
  green: "#22c55e",
  purple: "#a855f7",
  yellow: "#eab308",
  orange: "#f97316",
  unknown: "#94a3b8",
};

const CUE_COLORS_RGB = {
  blue: [59, 130, 246],
  green: [34, 197, 94],
  purple: [168, 85, 247],
  yellow: [234, 179, 8],
  orange: [249, 115, 22],
  unknown: [148, 163, 184],
};

function cueRgba(colorName, alpha) {
  const rgb = CUE_COLORS_RGB[colorName] || CUE_COLORS_RGB.unknown;
  return `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, ${alpha})`;
}

function accentRgba(alpha) {
  const raw = getComputedStyle(document.documentElement)
    .getPropertyValue("--accent-rgb")
    .trim();
  const rgb = raw.match(/\d+(?:\.\d+)?/g)?.slice(0, 3);
  if (!rgb || rgb.length !== 3) return `rgba(200, 255, 98, ${alpha})`;
  return `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, ${alpha})`;
}

/** VDJ loop Size is in beats → seconds via track BPM. */
function loopDurationSeconds(point, bpm) {
  const beats = Number(point?.size);
  if (!Number.isFinite(beats) || beats <= 0) return 0;
  if (bpm && bpm > 0) return (beats / bpm) * 60;
  // Fallback: assume 120 BPM if VDJ BPM missing
  return (beats / 120) * 60;
}

function isReviewMode() {
  return state.mode === "add_cues";
}

const $ = (id) => document.getElementById(id);

const ACCENT_THEME_KEY = "music-sorter-accent-theme";
const ACCENT_THEMES = new Set(["lime", "cyan", "violet", "coral"]);

function storedAccentTheme() {
  try {
    const stored = window.localStorage.getItem(ACCENT_THEME_KEY);
    return ACCENT_THEMES.has(stored) ? stored : "lime";
  } catch {
    return "lime";
  }
}

function applyAccentTheme(theme, { persist = true } = {}) {
  const nextTheme = ACCENT_THEMES.has(theme) ? theme : "lime";
  state.accentTheme = nextTheme;
  document.documentElement.dataset.accentTheme = nextTheme;

  document.querySelectorAll("#accentPicker [data-accent-theme]").forEach((button) => {
    const isActive = button.dataset.accentTheme === nextTheme;
    button.classList.toggle("active", isActive);
    button.setAttribute("aria-pressed", String(isActive));
  });

  if (persist) {
    try {
      window.localStorage.setItem(ACCENT_THEME_KEY, nextTheme);
    } catch {
      /* Theme still applies when storage is unavailable. */
    }
  }

  requestAnimationFrame(() => drawWaveform());
}

function showConfirmDialog({
  title,
  track = "",
  message,
  note = "",
  confirmLabel = "Continue",
  tone = "accent",
  cancelOnly = false,
}) {
  const dialog = $("confirmDialog");
  if (!dialog || typeof dialog.showModal !== "function") {
    if (cancelOnly) {
      window.alert([title, track, message, note].filter(Boolean).join("\n\n"));
      return Promise.resolve(false);
    }
    return Promise.resolve(
      window.confirm([title, track, message, note].filter(Boolean).join("\n\n"))
    );
  }
  if (dialog.open) return Promise.resolve(false);

  $("confirmTitle").textContent = title;
  $("confirmTrack").textContent = track;
  $("confirmTrack").hidden = !track;
  $("confirmMessage").textContent = message;
  $("confirmNote").textContent = note;
  $("confirmNote").hidden = !note;
  $("confirmAcceptBtn").textContent = confirmLabel;
  $("confirmAcceptBtn").hidden = cancelOnly;
  $("confirmCancelBtn").textContent = cancelOnly ? "Close" : "Cancel";
  dialog.className = `confirm-dialog tone-${tone}`;
  dialog.returnValue = "";

  const previousFocus = document.activeElement;
  return new Promise((resolve) => {
    const onBackdropClick = (event) => {
      if (event.target === dialog) dialog.close("cancel");
    };
    const onClose = () => {
      dialog.removeEventListener("click", onBackdropClick);
      $("confirmAcceptBtn").hidden = false;
      if (previousFocus instanceof HTMLElement) previousFocus.focus();
      resolve(dialog.returnValue === "confirm");
    };

    dialog.addEventListener("click", onBackdropClick);
    dialog.addEventListener("close", onClose, { once: true });
    dialog.showModal();
  });
}

function resetWorkspaceScroll() {
  const player = document.querySelector(".panel.player");
  const reviewBody = document.querySelector("#reviewPanel .review-body");
  if (player) player.scrollTop = 0;
  if (reviewBody) reviewBody.scrollTop = 0;
}

function fmtBytes(n) {
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function fmtTime(seconds) {
  const s = Math.max(0, Number(seconds) || 0);
  const m = Math.floor(s / 60);
  const rem = s - m * 60;
  const whole = Math.floor(rem);
  const frac = Math.round((rem - whole) * 10);
  return `${m}:${String(whole).padStart(2, "0")}.${frac}`;
}

function fmtTransportTime(seconds) {
  const s = Math.max(0, Math.floor(Number(seconds) || 0));
  const m = Math.floor(s / 60);
  return `${m}:${String(s % 60).padStart(2, "0")}`;
}

function trackDisplayTitle(track) {
  return (
    String(track?.cues?.title || "").trim() ||
    String(track?.name || "Untitled track")
      .replace(/\.[^.]+$/, "")
      .replace(/^\d+[\s._-]+/, "")
  );
}

function trackDisplayArtist(track) {
  return String(track?.cues?.author || "").trim();
}

function renderNowPlayingTitle(track) {
  const root = $("nowPlaying");
  if (!root || !track) return;

  const artist = trackDisplayArtist(track);
  const title = trackDisplayTitle(track);
  const label = artist ? `${artist} — ${title}` : title;

  root.title = label;
  root.setAttribute("aria-label", label);
  root.innerHTML = artist
    ? `<span class="now-playing-artist">${escapeHtml(artist)}</span>
       <span class="now-playing-separator" aria-hidden="true">—</span>
       <span class="now-playing-title">${escapeHtml(title)}</span>`
    : `<span class="now-playing-title">${escapeHtml(title)}</span>`;
}

function stepTrack(delta) {
  if (!currentTrack()) return;
  // Always walk the filtered list (search + readiness) so J/K skip hidden rows.
  const indexes = filteredTrackIndexes();
  if (!indexes.length) return;
  const position = indexes.indexOf(state.index);
  const nextPosition =
    position < 0 ? (delta > 0 ? 0 : indexes.length - 1) : position + delta;
  if (nextPosition >= 0 && nextPosition < indexes.length) {
    selectTrack(indexes[nextPosition]);
  }
}

function updateTransportUi() {
  const audio = $("audio");
  if (!audio) return;

  const duration = trackDuration(currentTrack(), audio);
  const current = Number.isFinite(audio.currentTime) ? audio.currentTime : 0;
  const progress = $("transportProgress");
  const playPause = $("playPauseBtn");
  const time = $("transportTime");
  const previous = $("previousTrackBtn");
  const next = $("nextTrackBtn");

  if (progress) {
    progress.max = String(duration || 0);
    progress.value = String(Math.min(current, duration || current || 0));
    progress.disabled = !duration;
  }
  if (time) {
    time.textContent = `${fmtTransportTime(current)} / ${fmtTransportTime(duration)}`;
  }
  if (playPause) {
    const isPlaying = Boolean(audio.src && !audio.paused);
    playPause.classList.toggle("is-playing", isPlaying);
    playPause.disabled = !audio.src;
    playPause.setAttribute("aria-label", isPlaying ? "Pause" : "Play");
  }

  const indexes = isReviewMode() ? filteredTrackIndexes() : state.tracks.map((_, i) => i);
  const position = indexes.indexOf(state.index);
  if (previous) previous.disabled = position <= 0;
  if (next) next.disabled = position < 0 || position >= indexes.length - 1;
}

function cueKey(point) {
  return `${point.kind}:${point.num}:${point.pos}`;
}

function trackDuration(track, audio) {
  const fromDb = Number(track?.cues?.song_length) || 0;
  const fromAudio = Number(audio?.duration);
  if (fromAudio && Number.isFinite(fromAudio) && fromAudio > 0) return fromAudio;
  if (fromDb > 0) return fromDb;
  return 0;
}

function syncLoopPlayBtn() {
  const btn = $("loopPlayBtn");
  if (!btn) return;
  btn.classList.toggle("active", state.loopPlaybackOn);
  btn.setAttribute("aria-pressed", state.loopPlaybackOn ? "true" : "false");
}

function jumpToCue(pos, point = null) {
  const audio = $("audio");
  if (!audio || !audio.src) return;
  const t = Math.max(0, Number(pos) || 0);
  const seek = () => {
    try {
      audio.currentTime = t;
    } catch {
      /* ignore seek race before metadata */
    }
    audio.play().catch(() => {});
    if (point) {
      state.activeCueKey = cueKey(point);
      if (point.kind === "loop") {
        // Clicking / hotkeying a loop always starts looping that region.
        const wasOn = state.loopPlaybackOn;
        const prevKey = state.activeLoopKey;
        state.loopPlaybackOn = true;
        state.activeLoopKey = cueKey(point);
        syncLoopPlayBtn();
        const end =
          t + loopDurationSeconds(point, trackBpm(currentTrack()));
        setStatus(
          `Looping · ${point.name || "loop"} (${fmtTime(t)}–${fmtTime(end)})`
        );
        if (!wasOn || prevKey !== state.activeLoopKey) {
          renderCues();
        } else {
          highlightActiveCue();
        }
      } else {
        // Normal cue: stop wrapping so playback continues past loop ends.
        const wasLooping = state.loopPlaybackOn || state.activeLoopKey;
        state.activeLoopKey = null;
        if (state.loopPlaybackOn) {
          state.loopPlaybackOn = false;
          syncLoopPlayBtn();
          stopLoopWatch();
        }
        setStatus(
          `Jumped to ${fmtTime(t)}${point?.name ? ` · ${point.name}` : ""}`
        );
        if (wasLooping) renderCues();
        else highlightActiveCue();
      }
    } else {
      updatePlayhead();
      setStatus(`Jumped to ${fmtTime(t)}`);
    }
    updatePlayhead();
    if (state.loopPlaybackOn) startLoopWatch();
  };

  if (audio.readyState >= 1) seek();
  else audio.addEventListener("loadedmetadata", seek, { once: true });
}

/** Wall-clock windows for VDJ loop markers (start → end via size beats + BPM). */
function getLoopWindows(track) {
  if (!track) return [];
  const bpm = trackBpm(track);
  return (track.cues?.points || [])
    .filter((p) => p.kind === "loop")
    .map((p) => {
      const start = Number(p.pos) || 0;
      const len = loopDurationSeconds(p, bpm);
      return {
        point: p,
        start,
        end: start + len,
        key: cueKey(p),
        len,
      };
    })
    .filter((w) => w.len > 0.05);
}

function stopLoopWatch() {
  if (state.loopRaf != null) {
    cancelAnimationFrame(state.loopRaf);
    state.loopRaf = null;
  }
}

function startLoopWatch() {
  if (!state.loopPlaybackOn) {
    stopLoopWatch();
    return;
  }
  if (state.loopRaf != null) return;
  const tick = () => {
    maybeLoopPlayback();
    const audio = $("audio");
    if (state.loopPlaybackOn && audio && !audio.paused) {
      state.loopRaf = requestAnimationFrame(tick);
    } else {
      state.loopRaf = null;
    }
  };
  state.loopRaf = requestAnimationFrame(tick);
}

/**
 * When loop playback is on, wrap the playhead at the end of the active (or
 * enclosing) VDJ loop so you can audition seamless loop points.
 */
function maybeLoopPlayback() {
  if (!state.loopPlaybackOn) return;
  const audio = $("audio");
  if (!audio || audio.paused || !audio.src) return;
  const track = currentTrack();
  const windows = getLoopWindows(track);
  if (!windows.length) return;

  const t = audio.currentTime;
  // Small pad so we catch the end even if a frame lands slightly past it.
  const endPad = 0.03;

  let win = null;
  if (state.activeLoopKey) {
    win = windows.find((w) => w.key === state.activeLoopKey) || null;
  }

  // Drop active loop if user seeked well outside it.
  if (win && (t < win.start - 0.25 || t > win.end + 0.35)) {
    win = null;
    state.activeLoopKey = null;
  }

  // Auto-engage a loop the playhead is currently inside.
  if (!win) {
    win =
      windows.find((w) => t >= w.start - 0.02 && t < w.end - endPad) || null;
    if (win) {
      state.activeLoopKey = win.key;
      state.activeCueKey = win.key;
      highlightActiveCue();
    }
  }

  if (!win) return;

  if (t >= win.end - endPad) {
    try {
      // Nudge slightly past start so repeated wraps don't stick on the boundary.
      audio.currentTime = win.start + 0.001;
    } catch {
      /* ignore */
    }
    updatePlayhead();
  }
}

function setLoopPlayback(on) {
  state.loopPlaybackOn = Boolean(on);
  syncLoopPlayBtn();
  if (!state.loopPlaybackOn) {
    state.activeLoopKey = null;
    stopLoopWatch();
    setStatus("Loop play off — continuous playback");
    highlightActiveCue();
    renderCues();
    return;
  }

  const windows = getLoopWindows(currentTrack());
  if (!windows.length) {
    setStatus("Loop play on — this track has no loop markers", "error");
    renderCues();
    return;
  }

  // If already inside a loop (or last active cue is a loop), engage it.
  const audio = $("audio");
  const t = audio?.currentTime || 0;
  let win = windows.find((w) => t >= w.start - 0.02 && t < w.end) || null;
  if (!win && state.activeCueKey) {
    win = windows.find((w) => w.key === state.activeCueKey) || null;
  }
  if (win) {
    state.activeLoopKey = win.key;
    state.activeCueKey = win.key;
    setStatus(
      `Loop play on · ${win.point.name || "loop"} (${fmtTime(win.start)}–${fmtTime(
        win.end
      )}) — click a loop or play into one`
    );
    if (audio && !audio.paused) startLoopWatch();
  } else {
    setStatus(
      `Loop play on · ${windows.length} loop(s) — click a loop cue or play into a region`
    );
  }
  renderCues();
}

function toggleLoopPlayback() {
  setLoopPlayback(!state.loopPlaybackOn);
}

function highlightActiveCue() {
  document.querySelectorAll(".cue-row").forEach((row) => {
    row.classList.toggle("active", row.dataset.key === state.activeCueKey);
  });
}

function updatePlayhead() {
  const audio = $("audio");
  const playhead = $("cuePlayhead");
  const track = currentTrack();
  if (!playhead || !audio || !track) return;
  const duration = trackDuration(track, audio);
  if (!duration) {
    playhead.style.left = "0%";
    return;
  }
  const pct = Math.min(100, Math.max(0, (audio.currentTime / duration) * 100));
  playhead.style.left = `calc(10px + (100% - 20px) * ${pct / 100})`;
  // Throttle canvas redraws during playback (keeps fast track switching snappy).
  const now = performance.now();
  if (now - (state.lastDrawMs || 0) > 80) {
    state.lastDrawMs = now;
    drawWaveform();
  }
}

function setWaveformStatus(text, kind = "") {
  const el = $("waveformStatus");
  if (!el) return;
  if (!text) {
    el.className = "waveform-status hidden";
    el.textContent = "";
    return;
  }
  el.className = `waveform-status ${kind}`.trim();
  el.textContent = text;
}

function setRetryStatus(text, kind = "") {
  const el = $("retryStatus");
  if (!el) return;
  if (!text) {
    el.hidden = true;
    el.textContent = "";
    el.className = "retry-status";
    return;
  }
  el.hidden = false;
  el.className = `retry-status ${kind}`.trim();
  el.textContent = text;
}

function stopRetryPoll() {
  // Stop every per-path poller (legacy helper name kept for call sites).
  for (const path of Object.keys(state.retryJobs || {})) {
    stopRetryPollForPath(path);
  }
}

/** First-time cueing (Not cued filter / uncued track) vs re-cueing existing markers. */
function isFirstTimeCueing(track = currentTrack()) {
  if (state.readinessFilter === "not_cued") return true;
  const status = trackReadinessStatus(track);
  if (status === "not_cued" || status === "missing") return true;
  if (track && track.is_cued === false) return true;
  return false;
}

function autocueActionLabels(busy = false) {
  const first = isFirstTimeCueing();
  if (busy) {
    return {
      both: first ? "Adding…" : "Retrying…",
      cues: first ? "Adding…" : "Retrying…",
      loops: first ? "Adding…" : "Retrying…",
      bothReview: first ? "Adding…" : "Retrying…",
      cuesReview: first ? "Adding…" : "Retrying…",
      loopsReview: first ? "Adding…" : "Retrying…",
      bothSide: first ? "↻ Running AutoCue…" : "↻ Retrying AutoCue…",
      cuesSide: "↻ Running…",
      loopsSide: "↻ Running…",
      section: first ? "Add cues" : "Fix cues",
    };
  }
  if (first) {
    return {
      both: "Both",
      cues: "Cues",
      loops: "Loops",
      bothReview: "Add both",
      cuesReview: "Add cues",
      loopsReview: "Add loops",
      bothSide: "↻ Add both (cues + loops)",
      cuesSide: "↻ Cues only",
      loopsSide: "↻ Loops only",
      section: "Add cues",
      titleBoth: "First-time AutoCue: write cues and loops",
      titleCues: "Write cue points only (unusual for first-time — prefer Both)",
      titleLoops: "Write loops only (unusual for first-time — prefer Both)",
    };
  }
  return {
    both: "Both",
    cues: "Cues",
    loops: "Loops",
    bothReview: "Retry both",
    cuesReview: "Retry cues",
    loopsReview: "Retry loops",
    bothSide: "↻ Retry both (cues + loops)",
    cuesSide: "↻ Retry cues only",
    loopsSide: "↻ Retry loops only",
    section: "Fix cues",
    titleBoth: "Re-run AutoCue for cues and loops (overwrites both)",
    titleCues: "Re-run AutoCue for cue points only — keeps existing loops",
    titleLoops: "Re-run AutoCue for loops only — keeps existing cue points",
  };
}

const AUTO_CUE_SCOPE_BUTTONS = [
  { id: "retryBothBtn", scope: "all", labelKey: "both", titleKey: "titleBoth" },
  { id: "retryCuesOnlyBtn", scope: "cues", labelKey: "cues", titleKey: "titleCues" },
  { id: "retryLoopsOnlyBtn", scope: "loops", labelKey: "loops", titleKey: "titleLoops" },
  {
    id: "retryBothBtnReview",
    scope: "all",
    labelKey: "bothReview",
    titleKey: "titleBoth",
  },
  {
    id: "retryCuesOnlyBtnReview",
    scope: "cues",
    labelKey: "cuesReview",
    titleKey: "titleCues",
  },
  {
    id: "retryLoopsOnlyBtnReview",
    scope: "loops",
    labelKey: "loopsReview",
    titleKey: "titleLoops",
  },
  { id: "retryBothBtnSide", scope: "all", labelKey: "bothSide", titleKey: "titleBoth" },
  {
    id: "retryCuesOnlyBtnSide",
    scope: "cues",
    labelKey: "cuesSide",
    titleKey: "titleCues",
  },
  {
    id: "retryLoopsOnlyBtnSide",
    scope: "loops",
    labelKey: "loopsSide",
    titleKey: "titleLoops",
  },
];

const AUTOCUE_ACTIVE_STATUSES = new Set([
  "starting",
  "queued",
  "running",
]);

function isAutocueJobActive(job) {
  return Boolean(job && AUTOCUE_ACTIVE_STATUSES.has(job.status));
}

function activeRetryJobs() {
  return Object.values(state.retryJobs || {}).filter(isAutocueJobActive);
}

function isAutocueJobRunning() {
  return activeRetryJobs().length > 0 || Boolean(state.batchPollTimer);
}

function retryJobForPath(path) {
  if (!path) return null;
  return state.retryJobs[path] || null;
}

/** True when the current track already has an AutoCue job in flight. */
function isAutocueBusyForCurrentTrack() {
  if (state.batchPollTimer) return true;
  const current = currentTrack()?.path;
  return isAutocueJobActive(retryJobForPath(current));
}

function stopRetryPollForPath(path) {
  const job = retryJobForPath(path);
  if (job?.pollTimer) {
    clearInterval(job.pollTimer);
    job.pollTimer = null;
  }
}

/**
 * Refresh AutoCue buttons + status for the *current* track.
 * Multiple jobs can run (queued/active); only *this* track shows Retrying…
 * and has its AutoCue buttons disabled. Other tracks stay startable.
 */
function syncAutocueUi() {
  const busyHere = isAutocueBusyForCurrentTrack();
  const labels = autocueActionLabels(busyHere);
  const first = isFirstTimeCueing();
  const others = activeRetryJobs().filter((j) => j.path !== currentTrack()?.path);
  const here = retryJobForPath(currentTrack()?.path);
  const busyMsg = here?.message || "AutoCue is running on this track…";

  for (const spec of AUTO_CUE_SCOPE_BUTTONS) {
    const btn = $(spec.id);
    if (!btn) continue;
    // Only block starting another job on *this* track while it's in flight.
    btn.disabled = busyHere;
    btn.setAttribute("aria-busy", busyHere ? "true" : "false");
    btn.classList.toggle("is-autocue-busy", busyHere);
    const text =
      labels[spec.labelKey] ||
      (spec.scope === "cues"
        ? labels.cues
        : spec.scope === "loops"
          ? labels.loops
          : labels.both);
    btn.textContent = text;
    if (busyHere) {
      btn.title = busyMsg;
    } else if (others.length) {
      btn.title = `Start AutoCue on this track (${others.length} other job${
        others.length === 1 ? "" : "s"
      } in progress)`;
    } else if (labels[spec.titleKey]) {
      btn.title = labels[spec.titleKey];
    }
    if (spec.id === "retryBothBtnSide") {
      btn.classList.toggle("primary", first && !busyHere);
    }
  }

  // Side panel: indeterminate progress + busy chrome while this track runs.
  for (const stackId of ["autocueScopeSide", "autocueScopeReview", "autocueScopeHeader"]) {
    const stack = $(stackId);
    if (stack) stack.classList.toggle("is-autocue-busy", busyHere);
  }
  const busyBar = $("autocueBusyBar");
  if (busyBar) {
    busyBar.hidden = !busyHere;
    busyBar.setAttribute("aria-hidden", busyHere ? "false" : "true");
  }
  const busyLabel = $("autocueBusyLabel");
  if (busyLabel) {
    busyLabel.hidden = !busyHere;
    if (busyHere) busyLabel.textContent = busyMsg;
  }

  const section = $("autocueSectionLabel");
  if (section && labels.section) section.textContent = labels.section;
  const header = $("autocueScopeHeader");
  if (header) header.hidden = !isReviewMode();

  const batchBtn = $("batchAddCuesBtn");
  if (batchBtn && !batchBtn.hidden) {
    const n = state.tracks.filter((t) => {
      const st = trackReadinessStatus(t);
      return st === "not_cued" || st === "missing";
    }).length;
    // Batch still blocked while a batch is active; single-track jobs OK.
    batchBtn.disabled = !n || Boolean(state.batchPollTimer);
  }

  // Status strip for the current view.
  if (busyHere && here?.message) {
    setRetryStatus(here.message, "running");
  } else if (others.length) {
    const names = others
      .slice(0, 2)
      .map((j) => j.name || "track")
      .join(", ");
    const more = others.length > 2 ? ` +${others.length - 2} more` : "";
    setRetryStatus(
      `${others.length} AutoCue job${others.length === 1 ? "" : "s"} on other tracks: ${names}${more}`,
      "running"
    );
  }
  // If nothing running, leave last success/error message alone.
}

function updateAutocueButtonLabels(busy = false) {
  // Prefer path-aware sync; `busy` is kept for call-site compatibility.
  // Always go through syncAutocueUi so disabled/loading state never drifts.
  void busy;
  syncAutocueUi();
}

function setAutocueButtonsBusy(_busy) {
  syncAutocueUi();
}

async function refreshCurrentTrackCues() {
  const track = currentTrack();
  if (!track) return;
  // Reload full track list so placements + cues stay consistent
  await loadTracks({ keepPath: track.path });
}

function writeScopeMessage(scope, first) {
  if (scope === "cues") {
    return first
      ? "AutoCue will write cue points only (no loops)."
      : "AutoCue will rewrite cue points only and keep existing loops.";
  }
  if (scope === "loops") {
    return first
      ? "AutoCue will write loops only (no cue points)."
      : "AutoCue will rewrite loops only and keep existing cue points.";
  }
  return first
    ? "AutoCue will write cue points and loops into VirtualDJ for this file."
    : "AutoCue will overwrite existing cue points and loops for this file.";
}

async function retryCuesForCurrentTrack(writeScope = "all") {
  const track = currentTrack();
  if (!track) return;
  if (!isReviewMode()) {
    setStatus("Switch to Add Cues to run AutoCue.", "error");
    return;
  }

  const pathKey = track.path;
  // Immediate lock so double-clicks / re-clicks can't start another job
  // while preflight or the confirm dialog is open.
  if (isAutocueJobActive(retryJobForPath(pathKey))) {
    setStatus("AutoCue already running on this track.", "error");
    syncAutocueUi();
    return;
  }

  const scope = ["cues", "loops"].includes(writeScope) ? writeScope : "all";
  const first = isFirstTimeCueing(track);
  const scopeWord =
    scope === "cues" ? "cues only" : scope === "loops" ? "loops only" : "cues + loops";
  const actionWord = first ? `Add ${scopeWord}` : `Retry ${scopeWord}`;

  // Mark starting *before* preflight so side buttons show loading immediately.
  stopRetryPollForPath(pathKey);
  state.retryJobs[pathKey] = {
    id: null,
    path: pathKey,
    name: track.name,
    message: `Checking beatgrid…`,
    status: "starting",
    writeScope: scope,
    pollTimer: null,
  };
  syncAutocueUi();

  // Deep grid preflight before confirming.
  setRetryStatus("Checking beatgrid…", "running");
  let preflight = null;
  try {
    const pf = await api(
      `/api/grid-preflight?path=${encodeURIComponent(track.path)}&deep=true`
    );
    preflight = pf.preflight;
    state.gridPreflight = preflight;
    renderGridPreflightCard(track);
  } catch (err) {
    delete state.retryJobs[pathKey];
    syncAutocueUi();
    setRetryStatus(`Grid check failed: ${err.message}`, "error");
    return;
  }

  if (preflight && !preflight.can_autocue) {
    delete state.retryJobs[pathKey];
    syncAutocueUi();
    const reasons = (preflight.issues || []).join("\n• ") || preflight.label;
    setRetryStatus(preflight.label || "Blocked — fix grid in VDJ", "error");
    setStatus(`Cannot AutoCue: ${preflight.label}`, "error");
    await showConfirmDialog({
      title: "Beatgrid needs attention",
      track: trackDisplayTitle(track),
      message: `• ${reasons}`,
      note: "Align the grid in VirtualDJ first, then try AutoCue again.",
      confirmLabel: "Close",
      tone: "warning",
      cancelOnly: true,
    });
    return;
  }

  // Keep buttons locked while the confirm dialog is open.
  const liveStart = state.retryJobs[pathKey];
  if (liveStart) {
    liveStart.message = `Waiting for confirm (${scopeWord})…`;
    liveStart.status = "starting";
  }
  syncAutocueUi();

  const gridNote = preflight?.needs_align
    ? " The beatgrid may be misaligned; AutoCue will try to correct the downbeat."
    : "";
  const ok = await showConfirmDialog({
    title: first ? `Add ${scopeWord}?` : `Retry ${scopeWord}?`,
    track: trackDisplayTitle(track),
    message: writeScopeMessage(scope, first),
    note: `Keep VirtualDJ closed while AutoCue updates its database.${gridNote}`,
    confirmLabel: first ? "Run AutoCue" : "Run AutoCue",
    tone: first && scope === "all" ? "accent" : "warning",
  });
  if (!ok) {
    delete state.retryJobs[pathKey];
    syncAutocueUi();
    setRetryStatus("", "");
    return;
  }

  let allowRunning = false;
  if (state.health?.virtualdj_running) {
    allowRunning = await showConfirmDialog({
      title: "VirtualDJ is still open",
      track: trackDisplayTitle(track),
      message:
        "Cue changes may be overwritten when VirtualDJ quits. Close it before continuing whenever possible.",
      confirmLabel: "Continue anyway",
      tone: "warning",
    });
    if (!allowRunning) {
      delete state.retryJobs[pathKey];
      syncAutocueUi();
      setStatus(`Close VirtualDJ, then ${actionWord.toLowerCase()}.`, "error");
      return;
    }
  }

  state.retryJobs[pathKey] = {
    id: null,
    path: pathKey,
    name: track.name,
    message: `Starting AutoCue (${scopeWord})…`,
    status: "queued",
    writeScope: scope,
    pollTimer: null,
  };
  syncAutocueUi();
  setStatus(`AutoCue (${scopeWord}): ${track.name}…`);

  try {
    const data = await api("/api/retry-cues", {
      method: "POST",
      body: JSON.stringify({
        path: track.path,
        allow_vdj_running: Boolean(allowRunning),
        require_grid: true,
        deep_grid_check: true,
        write_scope: scope,
      }),
    });
    const job = data.job;
    const entry = state.retryJobs[pathKey] || {};
    entry.id = job.id;
    entry.path = job.path || pathKey;
    entry.name = job.name || track.name;
    entry.status = job.status || "queued";
    entry.message = job.message || "Queued…";
    state.retryJobs[pathKey] = entry;

    if (job.status === "skipped") {
      stopRetryPollForPath(pathKey);
      delete state.retryJobs[pathKey];
      syncAutocueUi();
      setRetryStatus(job.message || "Skipped — fix beatgrid first", "error");
      setStatus(job.message || "AutoCue skipped", "error");
      if (job.preflight && currentTrack()?.path === pathKey) {
        state.gridPreflight = job.preflight;
        renderGridPreflightCard(track);
      }
      return;
    }

    syncAutocueUi();

    entry.pollTimer = setInterval(async () => {
      try {
        const res = await api(`/api/retry-cues/${job.id}`);
        const j = res.job;
        const live = state.retryJobs[pathKey];
        if (!live || live.id !== job.id) return;

        if (j.status === "running" || j.status === "queued") {
          live.status = j.status;
          live.message = j.message || "Running AutoCue…";
          // Always re-apply disabled + loading UI (labels/status bar).
          syncAutocueUi();
          return;
        }

        stopRetryPollForPath(pathKey);
        const finishedName = live.name || j.name;
        delete state.retryJobs[pathKey];
        syncAutocueUi();

        if (j.status === "ok") {
          const doneMsg =
            `${j.message} (was ${j.cue_count_before} cues)` +
            (j.cue_count_after != null ? ` → ${j.cue_count_after}` : "");
          if (currentTrack()?.path === pathKey) {
            setRetryStatus(doneMsg, "ok");
          }
          setStatus(`AutoCue done: ${finishedName}`, "success");
          await loadTracks({ keepPath: currentTrack()?.path });
          if (currentTrack()?.path === pathKey) {
            await loadDeepGridPreflight(currentTrack(), state.trackGen);
          }
          syncAutocueUi();
        } else {
          if (currentTrack()?.path === pathKey) {
            setRetryStatus(j.message || "AutoCue failed", "error");
          }
          setStatus(
            j.message
              ? `${finishedName}: ${j.message}`
              : `AutoCue failed: ${finishedName}`,
            "error"
          );
        }
      } catch (err) {
        stopRetryPollForPath(pathKey);
        delete state.retryJobs[pathKey];
        syncAutocueUi();
        if (currentTrack()?.path === pathKey) {
          setRetryStatus(err.message, "error");
        }
        setStatus(err.message, "error");
      }
    }, 2000);
  } catch (err) {
    stopRetryPollForPath(pathKey);
    delete state.retryJobs[pathKey];
    syncAutocueUi();
    setRetryStatus(err.message, "error");
    setStatus(err.message, "error");
  }
}

function stopBatchPoll() {
  if (state.batchPollTimer) {
    clearInterval(state.batchPollTimer);
    state.batchPollTimer = null;
  }
}

async function batchAddCuesForNotCued() {
  if (!isReviewMode()) return;
  const indexes = filteredTrackIndexes().filter((i) => {
    const st = trackReadinessStatus(state.tracks[i]);
    return st === "not_cued" || st === "missing";
  });
  // Prefer filter=not_cued server-side for full queue even if UI filter is All.
  const countHint =
    state.readinessFilter === "not_cued"
      ? indexes.length
      : state.tracks.filter((t) => {
          const st = trackReadinessStatus(t);
          return st === "not_cued" || st === "missing";
        }).length;

  if (!countHint) {
    setStatus("No not-cued tracks to queue.", "error");
    return;
  }

  const ok = await showConfirmDialog({
    title: "Batch add cues?",
    track: `About ${countHint} not-cued tracks`,
    message:
      "Each track gets a beatgrid preflight, then AutoCue runs one track at a time.",
    note:
      "Tracks without a usable BPM or grid are skipped. Keep VirtualDJ closed during the batch.",
    confirmLabel: "Start batch",
    tone: "accent",
  });
  if (!ok) return;

  let allowRunning = false;
  if (state.health?.virtualdj_running) {
    allowRunning = await showConfirmDialog({
      title: "VirtualDJ is still open",
      message:
        "Batch cue changes may be overwritten when VirtualDJ quits. Close it before continuing whenever possible.",
      confirmLabel: "Continue anyway",
      tone: "warning",
    });
    if (!allowRunning) {
      setStatus("Close VirtualDJ, then batch add cues.", "error");
      return;
    }
  }

  const batchBtn = $("batchAddCuesBtn");
  if (batchBtn) batchBtn.disabled = true;
  stopBatchPoll();
  setRetryStatus("Starting batch Add cues…", "running");
  setStatus("Batch AutoCue: queuing…");

  try {
    const data = await api("/api/retry-cues/batch", {
      method: "POST",
      body: JSON.stringify({
        filter: "not_cued",
        allow_vdj_running: Boolean(allowRunning),
        require_grid: true,
        deep_grid_check: false,
      }),
    });
    const batch = data.batch;
    state.batchId = batch.id;
    setRetryStatus(batch.message || `Batch ${batch.id}…`, "running");

    state.batchPollTimer = setInterval(async () => {
      try {
        const res = await api(`/api/retry-cues/batch/${batch.id}`);
        const b = res.batch;
        setRetryStatus(b.message || "Batch running…", "running");
        setStatus(b.message || "Batch AutoCue…");
        if (b.status === "queued" || b.status === "running") return;
        stopBatchPoll();
        if (batchBtn) batchBtn.disabled = false;
        const kind = b.failed && !b.done ? "error" : "ok";
        setRetryStatus(b.message, kind);
        setStatus(b.message, b.failed && !b.done ? "error" : "success");
        await loadTracks({ keepPath: currentTrack()?.path });
        updateBatchAddCuesButton();
      } catch (err) {
        stopBatchPoll();
        if (batchBtn) batchBtn.disabled = false;
        setRetryStatus(err.message, "error");
        setStatus(err.message, "error");
      }
    }, 2500);
  } catch (err) {
    stopBatchPoll();
    if (batchBtn) batchBtn.disabled = false;
    setRetryStatus(err.message, "error");
    setStatus(err.message, "error");
  }
}

function updateBatchAddCuesButton() {
  const btn = $("batchAddCuesBtn");
  if (!btn) return;
  const show = isReviewMode() && state.readinessFilter === "not_cued";
  btn.hidden = !show;
  if (!show) return;
  const n = state.tracks.filter((t) => {
    const st = trackReadinessStatus(t);
    return st === "not_cued" || st === "missing";
  }).length;
  btn.textContent = n ? `Batch add cues (${n})` : "Batch add cues";
  // Batch is multi-track; block only while another batch is active.
  btn.disabled = !n || Boolean(state.batchPollTimer);
}

function gridBadge(track) {
  const g = track.grid || track.grid_preflight;
  if (!g || !isReviewMode()) return "";
  if (g.manual_required || g.status === "blocked") {
    return `<span class="badge bad" title="${escapeHtml(
      (g.issues || []).join(" · ") || g.label || ""
    )}">Grid blocked</span>`;
  }
  if (g.needs_align || g.status === "fixable") {
    return `<span class="badge warn" title="${escapeHtml(
      (g.warnings || []).join(" · ") || g.label || ""
    )}">Grid fix?</span>`;
  }
  if (g.status === "warn") {
    return `<span class="badge warn" title="${escapeHtml(
      (g.warnings || []).join(" · ") || g.label || ""
    )}">${escapeHtml(g.label || "Grid warn")}</span>`;
  }
  if (g.can_autocue) {
    return `<span class="badge ok" title="Beatgrid OK for AutoCue">Grid OK</span>`;
  }
  return "";
}

function renderGridPreflightCard(track) {
  const card = $("gridPreflightCard");
  if (!card) return;
  if (!isReviewMode() || !track) {
    card.hidden = true;
    card.innerHTML = "";
    return;
  }
  const g = state.gridPreflight || track.grid;
  if (!g) {
    card.hidden = true;
    card.innerHTML = "";
    return;
  }
  const cls =
    g.status === "blocked"
      ? "blocked"
      : g.status === "fixable" || g.status === "warn"
        ? "warn"
        : "ok";
  const issues = (g.issues || [])
    .map((i) => `<li>${escapeHtml(i)}</li>`)
    .join("");
  const warnings = (g.warnings || [])
    .map((w) => `<li>${escapeHtml(w)}</li>`)
    .join("");
  const action = g.manual_required
    ? "Fix the beatgrid in VirtualDJ before AutoCue."
    : g.needs_align
      ? "AutoCue may realign the downbeat when it runs — still worth a listen in VDJ."
      : "Grid looks ready for AutoCue.";
  const showHalve =
    Boolean(g.suggest_halve_bpm) ||
    (g.bpm && Number(g.bpm) >= 120 && Number(g.bpm) <= 160);
  const halfTarget = g.halved_bpm
    ? Number(g.halved_bpm).toFixed(0)
    : g.bpm
      ? (Number(g.bpm) / 2).toFixed(0)
      : "?";
  card.hidden = false;
  card.className = `grid-preflight-card ${cls}`;
  card.innerHTML = `
    <div class="grid-preflight-title">
      <strong>Beatgrid · ${escapeHtml(g.label || g.status)}</strong>
      ${
        g.bpm
          ? `<span class="badge neutral">${Number(g.bpm).toFixed(0)} BPM</span>`
          : ""
      }
      ${
        g.can_autocue
          ? `<span class="badge ok">can AutoCue</span>`
          : `<span class="badge bad">blocked</span>`
      }
    </div>
    <div class="subtitle">${escapeHtml(action)}</div>
    ${issues ? `<ul class="grid-preflight-list">${issues}</ul>` : ""}
    ${warnings ? `<ul class="grid-preflight-list warn">${warnings}</ul>` : ""}
    <div class="grid-preflight-actions">
      ${
        showHalve
          ? `<button type="button" class="btn primary" id="halveBpmBtn"
               title="Write half BPM into VirtualDJ database (double-time fix)">
               ½ BPM → ~${escapeHtml(halfTarget)} in VDJ
             </button>`
          : ""
      }
      ${
        g.bpm && Number(g.bpm) < 90
          ? `<button type="button" class="btn ghost" id="doubleBpmBtn"
               title="Double VDJ BPM if you halved by mistake">
               ×2 BPM in VDJ
             </button>`
          : ""
      }
      <button type="button" class="btn ghost" id="halfBpmPlaybackFromGridBtn"
        title="Only affect zouk playback speed (does not rewrite VDJ)">
        ${state.halfBpm ? "Playback ½ BPM on" : "Playback ½ only"}
      </button>
    </div>
  `;

  $("halveBpmBtn")?.addEventListener("click", () => writeBpmFactor({ double: false }));
  $("doubleBpmBtn")?.addEventListener("click", () => writeBpmFactor({ double: true }));
  $("halfBpmPlaybackFromGridBtn")?.addEventListener("click", () => {
    toggleHalfBpm();
    renderGridPreflightCard(track);
  });
}

async function writeBpmFactor({ double = false } = {}) {
  const track = currentTrack();
  if (!track) return;
  const raw = trackBpm(track);
  if (!raw) {
    setStatus("No VDJ BPM on this track.", "error");
    return;
  }
  const after = double ? raw * 2 : raw / 2;
  const verb = double ? "Double" : "Halve";
  const ok = await (typeof showConfirmDialog === "function"
    ? showConfirmDialog({
        title: `${verb} BPM in VirtualDJ?`,
        track: trackDisplayTitle(track),
        message: `Rewrite database BPM: ${raw.toFixed(1)} → ${after.toFixed(1)}.`,
        note:
          "This updates Scan/Tags Bpm in database.xml so AutoCue quantizes at the correct period. Close VirtualDJ first.",
        confirmLabel: `${verb} BPM`,
        tone: "warning",
      })
    : Promise.resolve(
        confirm(
          `${verb} VDJ BPM for:\n\n${track.name}\n\n${raw.toFixed(1)} → ${after.toFixed(
            1
          )}\n\nClose VirtualDJ first. Continue?`
        )
      ));
  if (!ok) return;

  let allowRunning = false;
  if (state.health?.virtualdj_running) {
    allowRunning =
      typeof showConfirmDialog === "function"
        ? await showConfirmDialog({
            title: "VirtualDJ is still open",
            track: trackDisplayTitle(track),
            message:
              "BPM rewrite may be overwritten when VirtualDJ quits. Close it first if possible.",
            confirmLabel: "Continue anyway",
            tone: "warning",
          })
        : confirm("VirtualDJ is running. Continue anyway?");
    if (!allowRunning) {
      setStatus("Close VirtualDJ, then change BPM.", "error");
      return;
    }
  }

  try {
    setStatus(`${verb}ing BPM…`);
    const data = await api("/api/halve-bpm", {
      method: "POST",
      body: JSON.stringify({
        path: track.path,
        allow_vdj_running: Boolean(allowRunning),
        double_instead: Boolean(double),
      }),
    });
    const r = data.result || {};
    setStatus(
      `BPM ${Number(r.bpm_before).toFixed(0)} → ${Number(r.bpm_after).toFixed(0)} in VDJ`,
      "success"
    );
    // Playback half toggle no longer needed if VDJ is fixed.
    if (!double && state.halfBpm) setHalfBpm(false);
    await loadTracks({ keepPath: track.path });
    const gen = state.trackGen;
    await loadDeepGridPreflight(currentTrack(), gen);
    updateSpeedUi();
    updatePlayerMetaOnly(currentTrack());
  } catch (err) {
    setStatus(err.message, "error");
    if (typeof showConfirmDialog === "function") {
      await showConfirmDialog({
        title: "BPM rewrite failed",
        message: err.message,
        confirmLabel: "Close",
        tone: "warning",
        cancelOnly: true,
      });
    } else {
      alert(err.message);
    }
  }
}

async function loadDeepGridPreflight(track, gen) {
  if (!track || !isReviewMode()) {
    state.gridPreflight = null;
    renderGridPreflightCard(null);
    return;
  }
  // Show fast list data immediately.
  state.gridPreflight = track.grid || null;
  renderGridPreflightCard(track);
  // Deep check only when not cued / partial — expensive.
  const st = trackReadinessStatus(track);
  if (st !== "not_cued" && st !== "missing" && st !== "partial") return;
  try {
    const data = await api(
      `/api/grid-preflight?path=${encodeURIComponent(track.path)}&deep=true`
    );
    if (gen !== state.trackGen || currentTrack()?.path !== track.path) return;
    state.gridPreflight = data.preflight;
    renderGridPreflightCard(track);
  } catch {
    /* keep structural preflight */
  }
}

const ACTION_LABELS = {
  sort: "Sorted",
  promote: "Moved",
  remove_ready: "Removed",
  retry_cues: "AutoCue started",
  retry_cues_complete: "AutoCue finished",
  retry_cues_batch: "AutoCue batch",
  create_folder: "Folder created",
  undo: "Undone",
  bpm_update: "BPM updated",
};

function actionLabel(action) {
  const raw = String(action || "action");
  return (
    ACTION_LABELS[raw] ||
    raw
      .split("_")
      .filter(Boolean)
      .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
      .join(" ")
  );
}

function actionDateLabel(timestamp) {
  const date = new Date(timestamp || "");
  if (Number.isNaN(date.getTime())) return "";
  const now = new Date();
  const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const startDate = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  const dayDelta = Math.round((startToday - startDate) / 86400000);
  if (dayDelta === 0) return "Today";
  if (dayDelta === 1) return "Yesterday";
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function actionMetaParts(action) {
  const details = action.details || {};
  const parts = [];
  if (details.relative_folder) parts.push(`→ ${details.relative_folder}`);
  if (
    action.action === "retry_cues_complete" &&
    details.cue_count_before != null &&
    details.cue_count_after != null
  ) {
    parts.push(`${details.cue_count_before} → ${details.cue_count_after} cues`);
    if (details.loop_count_after != null) {
      parts.push(`${details.loop_count_after} loops`);
    }
  } else if (action.action === "retry_cues") {
    parts.push("Queued for AutoCue");
  }
  if (details.cues_sorted_path) parts.push("Cues Sorted");
  if (details.stems || details.stems_moved) parts.push("stems");
  if (details.library_mode) parts.push(details.library_mode);
  if (details.reconstructed) parts.push("reconstructed");
  if (action.undone) parts.push("undone");
  if (action.success === false) parts.push(action.error || "failed");
  return [...new Set(parts)];
}

async function loadActionsLogPanel() {
  const body = $("actionsLogBody");
  if (!body) return;
  body.innerHTML = `<div class="empty">Loading…</div>`;
  try {
    const data = await api("/api/actions?limit=100");
    const logPath = data.log_path || "";
    $("actionsLogPath").textContent = logPath || "Local action history";
    $("actionsLogPath").title = logPath;
    const rows = data.actions || [];
    const count = $("actionsLogCount");
    if (count) count.textContent = `${rows.length} ${rows.length === 1 ? "event" : "events"}`;
    if (!rows.length) {
      body.innerHTML = `<div class="empty">No actions logged yet.</div>`;
      return;
    }
    body.innerHTML = rows
      .map((a) => {
        const t = (a.ts || "").slice(11, 16) || "—";
        const rawType = String(a.action || "action");
        const type = actionLabel(rawType);
        const actionClass = rawType.replace(/[^a-z0-9-]+/gi, "-").toLowerCase();
        const sourceOrDest = a.dest_path || a.source_path || "";
        const name =
          a.name ||
          sourceOrDest.split("/").filter(Boolean).at(-1) ||
          "Unknown item";
        const extra = actionMetaParts(a);
        const fullDetail = [a.source_path, a.dest_path].filter(Boolean).join("\n") || name;
        const fail = a.success === false ? "fail" : "";
        const undoBtn =
          a.undoable && a.id
            ? `<button type="button" class="btn ghost action-undo-btn" data-undo-id="${escapeHtml(
                a.id
              )}" data-undo-name="${escapeHtml(a.name || "")}">Undo</button>`
            : a.undone
              ? `<span class="badge neutral">undone</span>`
              : "";
        return `<div class="action-row action-${escapeHtml(actionClass)} ${fail}">
          <div class="action-time" title="${escapeHtml(a.ts || "")}">
            <span class="action-clock">${escapeHtml(t)}</span>
            <span class="action-date">${escapeHtml(actionDateLabel(a.ts))}</span>
          </div>
          <div class="action-type" title="${escapeHtml(rawType)}">${escapeHtml(type)}</div>
          <div class="action-detail" title="${escapeHtml(fullDetail)}">
            <div class="action-primary">${escapeHtml(name)}</div>
            ${
              extra.length
                ? `<div class="action-meta">${extra
                    .map((item) => `<span>${escapeHtml(item)}</span>`)
                    .join("")}</div>`
                : ""
            }
          </div>
          <div class="action-undo">${undoBtn}</div>
        </div>`;
      })
      .join("");
    body.querySelectorAll("[data-undo-id]").forEach((btn) => {
      btn.addEventListener("click", () =>
        undoAction(btn.dataset.undoId, btn.dataset.undoName)
      );
    });
  } catch (err) {
    body.innerHTML = `<div class="empty">${escapeHtml(err.message)}</div>`;
  }
}

async function undoAction(actionId, name) {
  const ok = await showConfirmDialog({
    title: "Undo this move?",
    track: name || actionId,
    message:
      "The file will move back and its VirtualDJ FilePath will be retargeted.",
    note:
      "Secondary copies created by the sort will be deleted. Keep VirtualDJ closed.",
    confirmLabel: "Undo move",
    tone: "warning",
  });
  if (!ok) return;

  let allowRunning = false;
  if (state.health?.virtualdj_running) {
    allowRunning = await showConfirmDialog({
      title: "VirtualDJ is still open",
      track: name || actionId,
      message:
        "The undo may be overwritten when VirtualDJ quits. Close it before continuing whenever possible.",
      confirmLabel: "Undo anyway",
      tone: "warning",
    });
    if (!allowRunning) {
      setStatus("Close VirtualDJ, then undo.", "error");
      return;
    }
  }

  try {
    setStatus(`Undoing ${name || actionId}…`);
    const data = await api("/api/undo", {
      method: "POST",
      body: JSON.stringify({
        action_id: actionId,
        allow_vdj_running: Boolean(allowRunning),
      }),
    });
    const r = data.result || {};
    setStatus(
      `Undone → ${(r.moved_to || "").split("/").slice(-2).join("/") || "ok"}`,
      "success"
    );
    await loadTracks({ keepPath: currentTrack()?.path });
    if (!isReviewMode()) await loadFolders();
    if ($("actionsLogPanel") && !$("actionsLogPanel").hidden) {
      await loadActionsLogPanel();
    }
  } catch (err) {
    setStatus(err.message, "error");
    await showConfirmDialog({
      title: "Undo failed",
      message: err.message,
      confirmLabel: "Close",
      tone: "danger",
      cancelOnly: true,
    });
  }
}

function resetWaveZoom() {
  state.waveZoom = 1;
  state.waveOffset = 0;
}

function formatBitrate(kbps) {
  if (!kbps || kbps <= 0) return null;
  if (kbps >= 1000) return `${(kbps / 1000).toFixed(1)} Mbps`;
  return `${Math.round(kbps)} kbps`;
}

function bitrateBadgeClass(kbps) {
  if (!kbps) return "neutral";
  // Rough quality hints for lossy; lossless FLAC often >> 500
  if (kbps >= 900) return "ok"; // typical lossless territory
  if (kbps >= 256) return "ok";
  if (kbps >= 192) return "neutral";
  if (kbps >= 128) return "warn";
  return "bad";
}

async function loadTrackMeta(track, gen) {
  if (!track) {
    state.trackMeta = null;
    return;
  }
  if (state.metaAbort) state.metaAbort.abort();
  const controller = new AbortController();
  state.metaAbort = controller;
  try {
    const data = await api(`/api/meta?path=${encodeURIComponent(track.path)}`, {
      signal: controller.signal,
    });
    if (gen !== state.trackGen || currentTrack()?.path !== track.path) return;
    state.trackMeta = data;
    // Patch into track for list reuse
    track.bitrate_kbps = data.bitrate_kbps;
    track.codec = data.codec;
    track.sample_rate = data.sample_rate;
    updatePlayerMetaOnly(track);
    // Refresh list badge for this track once kbps is known
    renderTrackList();
  } catch (err) {
    if (err.name === "AbortError") return;
    if (gen !== state.trackGen) return;
    state.trackMeta = null;
  }
}

function updatePlayerMetaOnly(track) {
  const meta = $("playerMeta");
  if (!meta || !track) return;
  // Re-render full meta row (cheap) via shared builder
  meta.innerHTML = buildPlayerMetaHtml(track);
}

function buildPlayerMetaHtml(track) {
  const cues = track.cues || {};
  const metaBits = state.trackMeta;
  const kbps = metaBits?.bitrate_kbps ?? track.bitrate_kbps;
  const codec = metaBits?.codec ?? track.codec;
  const sr = metaBits?.sample_rate ?? track.sample_rate;
  const brLabel = formatBitrate(kbps);
  const brClass = bitrateBadgeClass(kbps);

  return `
    ${
      isReviewMode()
        ? readinessBadge(track)
        : track.is_cued
          ? `<span class="badge ok">Cued · ${cues.cue_count} cues</span>`
          : `<span class="badge uncued">Not cued — cannot sort</span>`
    }
    ${cues.loop_count ? `<span class="badge neutral">${cues.loop_count} loops</span>` : ""}
    ${
      cues.bpm
        ? state.halfBpm
          ? `<span class="badge ok" title="VDJ reported ${Number(cues.bpm).toFixed(
              0
            )} — halved for playback">${(Number(cues.bpm) / 2).toFixed(
              0
            )} BPM (½ of ${Number(cues.bpm).toFixed(0)})</span>`
          : `<span class="badge neutral">${Number(cues.bpm).toFixed(0)} BPM</span>`
        : ""
    }
    ${
      brLabel
        ? `<span class="badge ${brClass}" title="${codec || "audio"} ${
            sr ? sr + " Hz" : ""
          }">${escapeHtml(brLabel)}</span>`
        : `<span class="badge neutral">kbps…</span>`
    }
    ${
      codec
        ? `<span class="badge neutral">${escapeHtml(String(codec).toUpperCase())}</span>`
        : ""
    }
    ${cues.has_beatgrid ? `<span class="badge neutral">beatgrid</span>` : ""}
    ${
      cues.in_database
        ? `<span class="badge neutral">in VDJ DB</span>`
        : `<span class="badge bad">missing from VDJ DB</span>`
    }
    ${
      track.group
        ? `<span class="badge neutral">${escapeHtml(track.group)}</span>`
        : ""
    }
  `;
}

function scheduleWaveformLoad(track, gen) {
  if (state.waveformDebounce) {
    clearTimeout(state.waveformDebounce);
    state.waveformDebounce = null;
  }
  if (state.waveformAbort) {
    state.waveformAbort.abort();
    state.waveformAbort = null;
  }
  state.waveform = null;
  state.waveformLoading = true;
  state.waveformError = null;
  resetWaveZoom();
  setWaveformStatus("Loading waveform…");
  drawWaveform();

  // Debounce so rapid J/K or list clicks don't stack ffmpeg jobs.
  state.waveformDebounce = setTimeout(() => {
    state.waveformDebounce = null;
    if (gen !== state.trackGen) return;
    loadWaveform(track, gen);
  }, 140);
}

async function loadWaveform(track, gen = state.trackGen) {
  if (!track) {
    state.waveform = null;
    state.waveformError = null;
    state.waveformLoading = false;
    resetWaveZoom();
    drawWaveform();
    setWaveformStatus("No track selected");
    return;
  }
  if (gen !== state.trackGen) return;

  if (state.waveformAbort) state.waveformAbort.abort();
  const controller = new AbortController();
  state.waveformAbort = controller;
  state.waveformLoading = true;
  state.waveformError = null;
  setWaveformStatus("Loading waveform…");

  try {
    const data = await api(
      `/api/waveform?path=${encodeURIComponent(track.path)}&bins=1000`,
      { signal: controller.signal }
    );
    if (gen !== state.trackGen || currentTrack()?.path !== track.path) return;
    state.waveform = data;
    state.waveformLoading = false;
    setWaveformStatus("");
    drawWaveform();
  } catch (err) {
    if (err.name === "AbortError") return;
    if (gen !== state.trackGen || currentTrack()?.path !== track.path) return;
    state.waveform = null;
    state.waveformLoading = false;
    state.waveformError = err.message;
    setWaveformStatus(err.message || "Waveform failed", "error");
    drawWaveform();
  }
}

function waveformDuration(track, audio) {
  const fromWave = Number(state.waveform?.duration) || 0;
  if (fromWave > 0) return fromWave;
  return trackDuration(track, audio);
}

function clampWaveZoom(zoom) {
  return Math.min(WAVE_ZOOM_MAX, Math.max(WAVE_ZOOM_MIN, zoom));
}

/** Visible time window over the full track duration. */
function waveViewWindow(duration) {
  if (!duration || duration <= 0) {
    return { start: 0, end: 0, span: 0 };
  }
  const zoom = clampWaveZoom(state.waveZoom || 1);
  state.waveZoom = zoom;
  const span = duration / zoom;
  let start = Number(state.waveOffset) || 0;
  start = Math.max(0, Math.min(start, Math.max(0, duration - span)));
  state.waveOffset = start;
  return { start, end: start + span, span };
}

function wavePlotMetrics(cssW) {
  const padX = WAVE_PAD_X;
  const plotW = Math.max(1, cssW - padX * 2);
  return { padX, plotW };
}

function timeToWaveX(timeSec, padX, plotW, view) {
  if (!view.span) return padX;
  return padX + ((timeSec - view.start) / view.span) * plotW;
}

function clientXToTime(clientX, wrapRect, duration) {
  const { padX, plotW } = wavePlotMetrics(wrapRect.width);
  const x = clientX - wrapRect.left;
  const ratio = Math.min(1, Math.max(0, (x - padX) / plotW));
  const view = waveViewWindow(duration);
  return view.start + ratio * view.span;
}

function peaksForView(peaks, view, duration) {
  if (!peaks?.length || !duration || !view.span) return [];
  const n = peaks.length;
  const i0 = Math.max(0, Math.floor((view.start / duration) * n));
  const i1 = Math.min(n, Math.ceil((view.end / duration) * n));
  if (i1 <= i0) return peaks.slice(i0, i0 + 1);
  return peaks.slice(i0, i1);
}

function drawWaveform() {
  const canvas = $("waveformCanvas");
  if (!canvas) return;
  const wrap = $("waveformWrap");
  const dpr = window.devicePixelRatio || 1;
  const cssW = wrap?.clientWidth || canvas.clientWidth || 600;
  const cssH = wrap?.clientHeight || 150;
  if (canvas.width !== Math.floor(cssW * dpr) || canvas.height !== Math.floor(cssH * dpr)) {
    canvas.width = Math.floor(cssW * dpr);
    canvas.height = Math.floor(cssH * dpr);
    canvas.style.width = `${cssW}px`;
    canvas.style.height = `${cssH}px`;
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const w = cssW;
  const h = cssH;

  // Background
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = "#0a0e16";
  ctx.fillRect(0, 0, w, h);

  const peaks = state.waveform?.peaks;
  if (!peaks || !peaks.length) {
    ctx.strokeStyle = "rgba(42,51,68,0.8)";
    ctx.beginPath();
    ctx.moveTo(0, h / 2);
    ctx.lineTo(w, h / 2);
    ctx.stroke();
    return;
  }

  const track = currentTrack();
  const audio = $("audio");
  const duration = waveformDuration(track, audio);
  const view = waveViewWindow(duration || 1);
  const { padX, plotW } = wavePlotMetrics(w);
  const mid = h / 2;
  const visiblePeaks = peaksForView(peaks, view, duration || 1);

  // Center line
  ctx.strokeStyle = "rgba(42,51,68,0.9)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(padX, mid);
  ctx.lineTo(w - padX, mid);
  ctx.stroke();

  // Waveform polygon (visible window only)
  ctx.beginPath();
  const nVis = visiblePeaks.length;
  for (let i = 0; i < nVis; i++) {
    const x = padX + (i / Math.max(1, nVis - 1)) * plotW;
    const amp = Math.min(1, visiblePeaks[i]) * (h * 0.42);
    const y = mid - amp;
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }
  for (let i = nVis - 1; i >= 0; i--) {
    const x = padX + (i / Math.max(1, nVis - 1)) * plotW;
    const amp = Math.min(1, visiblePeaks[i]) * (h * 0.42);
    ctx.lineTo(x, mid + amp);
  }
  ctx.closePath();
  const grad = ctx.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, accentRgba(0.68));
  grad.addColorStop(0.5, accentRgba(0.3));
  grad.addColorStop(1, accentRgba(0.58));
  ctx.fillStyle = grad;
  ctx.fill();
  ctx.strokeStyle = accentRgba(0.38);
  ctx.stroke();

  if (!duration) return;

  // Honor Both / Cues / Loops tabs on the waveform too.
  const points = filteredCuePoints(track?.cues?.points || []);
  const bpm = trackBpm(track);

  // Loop bands first (full duration translucent fill)
  for (const p of points) {
    if (pointKind(p) !== "loop") continue;
    const start = Number(p.pos) || 0;
    const len = loopDurationSeconds(p, bpm);
    if (len <= 0) continue;
    const end = start + len;
    // Skip if entirely outside the visible window
    if (end < view.start || start > view.end) continue;

    const x0 = timeToWaveX(Math.max(start, view.start), padX, plotW, view);
    const x1 = timeToWaveX(Math.min(end, view.end), padX, plotW, view);
    const width = Math.max(2, x1 - x0);
    ctx.save();
    ctx.fillStyle = cueRgba(p.color_name, 0.22);
    ctx.fillRect(x0, 4, width, h - 8);
    // Soft edges
    ctx.strokeStyle = cueRgba(p.color_name, 0.45);
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 3]);
    ctx.strokeRect(x0 + 0.5, 4.5, width - 1, h - 9);
    ctx.setLineDash([]);
    ctx.restore();
  }

  // Cue / loop start markers (lines first). Labels laid out separately so
  // cues (top) and loops (bottom) never share the same text band.
  // Already filtered by the Both/Cues/Loops tab above.
  const labelCandidates = [];
  ctx.font = "10px SF Pro Text, system-ui, sans-serif";
  for (const p of points) {
    const kind = pointKind(p);
    const t = Number(p.pos) || 0;
    const loopLen = kind === "loop" ? loopDurationSeconds(p, bpm) : 0;
    const tEnd = kind === "loop" ? t + loopLen : t;
    // Show if start or any part of loop is in view
    if (kind === "loop") {
      if (tEnd < view.start || t > view.end) continue;
    } else if (t < view.start - 0.05 || t > view.end + 0.05) {
      continue;
    }

    const x = timeToWaveX(Math.max(t, view.start), padX, plotW, view);
    const color = CUE_COLORS[p.color_name] || CUE_COLORS.unknown;
    ctx.save();
    ctx.strokeStyle = color;
    ctx.lineWidth = kind === "loop" ? 1.5 : 2;
    if (kind === "loop") ctx.setLineDash([5, 4]);
    // Leave headroom for cue labels at top and loop labels at bottom.
    ctx.beginPath();
    ctx.moveTo(x, 18);
    ctx.lineTo(x, h - 18);
    ctx.stroke();
    // Loop end marker
    if (kind === "loop" && loopLen > 0 && tEnd >= view.start && tEnd <= view.end) {
      const xEnd = timeToWaveX(tEnd, padX, plotW, view);
      ctx.beginPath();
      ctx.moveTo(xEnd, 18);
      ctx.lineTo(xEnd, h - 18);
      ctx.stroke();
    }
    ctx.setLineDash([]);
    ctx.restore();

    const text =
      kind === "loop" && p.size
        ? `${(p.name || "Loop").slice(0, 14)} ${p.size}b`
        : (p.name || kind).slice(0, 16);
    const textW = Math.ceil(ctx.measureText(text).width);
    labelCandidates.push({
      kind,
      x,
      text,
      textW,
      color,
    });
  }

  drawWaveformLabels(ctx, labelCandidates, w, h, padX);

  // Playhead
  if (audio && Number.isFinite(audio.currentTime)) {
    const t = audio.currentTime;
    if (t >= view.start && t <= view.end) {
      const x = timeToWaveX(t, padX, plotW, view);
      ctx.strokeStyle = "#ffffff";
      ctx.lineWidth = 1.5;
      ctx.globalAlpha = 0.9;
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, h);
      ctx.stroke();
      ctx.globalAlpha = 1;
    }
  }

  // Zoom / window chrome
  if (state.waveZoom > 1.01) {
    ctx.fillStyle = "rgba(10, 14, 22, 0.72)";
    ctx.fillRect(padX, h - 22, 168, 16);
    ctx.fillStyle = "rgba(232, 237, 247, 0.9)";
    ctx.font = "11px SF Pro Text, system-ui, sans-serif";
    ctx.fillText(
      `${state.waveZoom.toFixed(1)}×  ${fmtTime(view.start)}–${fmtTime(view.end)}`,
      padX + 6,
      h - 10
    );

    // Mini overview strip at top
    const ovY = 2;
    const ovH = 4;
    ctx.fillStyle = "rgba(42, 51, 68, 0.9)";
    ctx.fillRect(padX, ovY, plotW, ovH);
    const winX = padX + (view.start / duration) * plotW;
    const winW = Math.max(2, (view.span / duration) * plotW);
    ctx.fillStyle = "rgba(110, 231, 255, 0.55)";
    ctx.fillRect(winX, ovY, winW, ovH);
  }
}

/**
 * Place cue labels along the top and loop labels along the bottom.
 * Within each band, stack / nudge so nearby markers' text doesn't collide.
 */
function drawWaveformLabels(ctx, candidates, w, h, padX) {
  if (!candidates.length) return;

  const rowH = 13;
  const pad = 3;
  const maxRows = 4;

  const cues = candidates
    .filter((c) => c.kind === "cue")
    .sort((a, b) => a.x - b.x);
  const loops = candidates
    .filter((c) => c.kind === "loop")
    .sort((a, b) => a.x - b.x);

  // Top band grows downward from y=11; bottom band grows upward from y=h-6.
  const cuePlaced = layoutLabelRows(cues, {
    baseY: 11,
    rowStep: rowH,
    direction: 1,
    maxRows,
    pad,
  });
  const loopPlaced = layoutLabelRows(loops, {
    baseY: h - 6,
    rowStep: rowH,
    direction: -1,
    maxRows,
    pad,
  });

  ctx.save();
  ctx.font = "10px SF Pro Text, system-ui, sans-serif";
  ctx.textBaseline = "alphabetic";

  for (const item of [...cuePlaced, ...loopPlaced]) {
    // Keep text inside the plot horizontally.
    let textX = item.x + 3;
    if (textX + item.textW + 4 > w - padX) {
      textX = Math.max(padX, item.x - item.textW - 3);
    }
    const boxX = textX - 2;
    const boxY = item.y - 10;
    const boxW = item.textW + 4;
    const boxH = 12;

    ctx.fillStyle = "rgba(10, 14, 22, 0.78)";
    ctx.fillRect(boxX, boxY, boxW, boxH);
    // Accent bar on the left edge of the pill for kind separation.
    ctx.fillStyle = item.color;
    ctx.fillRect(boxX, boxY, 2, boxH);
    ctx.fillStyle = item.color;
    ctx.fillText(item.text, textX, item.y);
  }
  ctx.restore();
}

function layoutLabelRows(items, { baseY, rowStep, direction, maxRows, pad }) {
  /** @type {{ x: number, textW: number, y: number, text: string, color: string }[]} */
  const placed = [];
  for (const item of items) {
    let row = 0;
    let y = baseY;
    while (row < maxRows) {
      y = baseY + direction * row * rowStep;
      const collides = placed.some((p) => {
        if (Math.abs(p.y - y) > rowStep - 1) return false;
        // Horizontal overlap of text boxes (with padding).
        const a0 = item.x;
        const a1 = item.x + item.textW + pad * 2 + 6;
        const b0 = p.x;
        const b1 = p.x + p.textW + pad * 2 + 6;
        return a0 < b1 && b0 < a1;
      });
      if (!collides) break;
      row += 1;
    }
    placed.push({
      x: item.x,
      textW: item.textW,
      y,
      text: item.text,
      color: item.color,
    });
  }
  return placed;
}

function seekFromWaveformEvent(e) {
  const wrap = $("waveformWrap");
  const track = currentTrack();
  const audio = $("audio");
  if (!wrap || !track) return;
  const duration = waveformDuration(track, audio);
  if (!duration) return;
  const rect = wrap.getBoundingClientRect();
  const t = clientXToTime(e.clientX, rect, duration);
  jumpToCue(t);
}

function onWaveformWheel(e) {
  const wrap = $("waveformWrap");
  const track = currentTrack();
  const audio = $("audio");
  if (!wrap || !track || !state.waveform?.peaks?.length) return;

  const duration = waveformDuration(track, audio);
  if (!duration) return;

  e.preventDefault();
  e.stopPropagation();

  const rect = wrap.getBoundingClientRect();
  const mouseTime = clientXToTime(e.clientX, rect, duration);
  const mouseRatio = Math.min(
    1,
    Math.max(0, (e.clientX - rect.left - WAVE_PAD_X) / Math.max(1, rect.width - WAVE_PAD_X * 2))
  );

  // Shift + scroll → pan when zoomed; plain scroll → zoom on cursor
  if (e.shiftKey && state.waveZoom > 1.01) {
    const view = waveViewWindow(duration);
    const pan = (e.deltaY > 0 ? 1 : -1) * view.span * 0.12;
    state.waveOffset = Math.max(0, Math.min(duration - view.span, view.start + pan));
    drawWaveform();
    return;
  }

  const factor = e.deltaY < 0 ? 1.18 : 1 / 1.18;
  const nextZoom = clampWaveZoom((state.waveZoom || 1) * factor);
  if (Math.abs(nextZoom - state.waveZoom) < 0.001) return;

  state.waveZoom = nextZoom;
  const span = duration / nextZoom;
  // Keep the time under the cursor fixed while zooming.
  let start = mouseTime - mouseRatio * span;
  start = Math.max(0, Math.min(start, Math.max(0, duration - span)));
  state.waveOffset = start;
  drawWaveform();
}

function pointKind(point) {
  const raw = String(point?.kind || point?.type || "").toLowerCase();
  return raw === "loop" ? "loop" : "cue";
}

function pointMatchesCueFilter(point, filter = state.cueListFilter) {
  const f = filter || "all";
  if (f === "all") return true;
  const kind = pointKind(point);
  if (f === "cues") return kind === "cue";
  if (f === "loops") return kind === "loop";
  return true;
}

function filteredCuePoints(points) {
  return (points || []).filter((p) => pointMatchesCueFilter(p));
}

function syncCueKindFilterUi() {
  const root = $("cueKindFilter");
  if (!root) return;
  root.querySelectorAll("button[data-cue-filter]").forEach((btn) => {
    const value = btn.getAttribute("data-cue-filter") || "all";
    const on = value === state.cueListFilter;
    btn.classList.toggle("active", on);
    btn.setAttribute("aria-selected", on ? "true" : "false");
  });
  const panel = $("cuesPanel");
  if (panel) panel.dataset.cueFilter = state.cueListFilter;
}

function setCueListFilter(filter) {
  const raw = String(filter || "all").toLowerCase();
  const next = raw === "cues" || raw === "loops" || raw === "all" ? raw : "all";
  state.cueListFilter = next;
  syncCueKindFilterUi();
  // Always rebuild list + timeline + waveform so isolation is visible.
  renderCues();
  drawWaveform();
}

function renderCues() {
  const track = currentTrack();
  const list = $("cueList");
  const timeline = $("cueTimeline");
  const countBadge = $("cuesCountBadge");
  const subtitle = $("cuesSubtitle");
  const filterHint = $("cueKindFilterHint");
  if (!list || !timeline) return;

  syncCueKindFilterUi();

  // Rebuild track + playhead; markers added below.
  timeline.innerHTML = `
    <div class="cue-timeline-track"></div>
    <div class="cue-timeline-playhead" id="cuePlayhead"></div>
  `;

  if (!track) {
    list.innerHTML = `<div class="empty">No track selected.</div>`;
    countBadge.textContent = "0";
    subtitle.textContent = "Scroll to zoom · click to jump";
    if (filterHint) filterHint.textContent = "";
    return;
  }

  const points = track.cues?.points || [];
  const cueN = points.filter((p) => pointKind(p) === "cue").length;
  const loopN = points.filter((p) => pointKind(p) === "loop").length;
  const visible = points
    .map((p, i) => ({ p, i }))
    .filter(({ p }) => pointMatchesCueFilter(p));

  countBadge.textContent =
    state.cueListFilter === "all"
      ? String(points.length)
      : `${visible.length}/${points.length}`;
  countBadge.className = points.length ? "badge ok" : "badge neutral";
  const filterLabel =
    state.cueListFilter === "cues"
      ? "cues only"
      : state.cueListFilter === "loops"
        ? "loops only"
        : "all markers";
  subtitle.textContent = points.length
    ? `Showing ${filterLabel} · scroll zoom · 1–9 jump · ✕ delete`
    : "No cue/loop markers · scroll still zooms the wave";
  if (filterHint) {
    filterHint.textContent = points.length
      ? `Showing ${visible.length} · ${cueN} cues · ${loopN} loops total`
      : "";
  }

  // Always clear list first so a previous tab cannot leave stale rows.
  list.innerHTML = "";

  if (!points.length) {
    list.innerHTML = `<div class="empty">No cues for this track.</div>`;
    updatePlayhead();
    return;
  }

  if (!visible.length) {
    const emptyMsg =
      state.cueListFilter === "loops"
        ? "No loops on this track."
        : state.cueListFilter === "cues"
          ? "No cue points on this track."
          : "No markers match this filter.";
    list.innerHTML = `<div class="empty">${emptyMsg}</div>`;
    updatePlayhead();
    // Still draw empty timeline (already cleared above).
    return;
  }

  const duration = trackDuration(track, $("audio"));
  const effectiveDuration =
    duration ||
    Math.max(...points.map((p) => Number(p.pos) || 0), 1) * 1.05;

  // Timeline markers only for the active tab.
  timeline.append(
    ...visible.map(({ p }) => {
      const kind = pointKind(p);
      const pct = Math.min(99.5, Math.max(0.5, ((Number(p.pos) || 0) / effectiveDuration) * 100));
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = `cue-marker ${kind} color-${p.color_name || "unknown"}`;
      btn.style.left = `calc(10px + (100% - 20px) * ${pct / 100})`;
      btn.title = `${p.name} · ${fmtTime(p.pos)}${kind === "loop" ? " (loop)" : ""}`;
      btn.dataset.pos = String(p.pos);
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        jumpToCue(p.pos, p);
      });
      return btn;
    })
  );

  list.innerHTML = visible
    .map(({ p, i }, visIdx) => {
      const kind = pointKind(p);
      const hotkey = visIdx < 9 ? `<span class="kbd">${visIdx + 1}</span>` : "";
      const key = cueKey(p);
      const isLooping =
        state.loopPlaybackOn && state.activeLoopKey === key && kind === "loop";
      const kindLabel =
        kind === "loop"
          ? `loop${p.size ? ` ${p.size}b` : ""}${isLooping ? " · ON" : ""}`
          : `cue ${p.num || ""}`.trim();
      return `
        <div class="cue-row ${
          state.activeCueKey === key ? "active" : ""
        } ${isLooping ? "looping" : ""}" data-key="${escapeHtml(key)}" data-pos="${p.pos}" data-index="${i}" data-kind="${kind}">
          <button type="button" class="cue-row-main" data-index="${i}" title="Jump to marker">
            <span class="cue-dot ${kind} color-${p.color_name || "unknown"}"></span>
            <span class="cue-time">${fmtTime(p.pos)}</span>
            <span class="cue-name">${escapeHtml(p.name)}</span>
            <span class="cue-kind">${escapeHtml(kindLabel)} ${hotkey}</span>
          </button>
          <button
            type="button"
            class="btn ghost danger cue-delete-btn"
            data-index="${i}"
            title="Delete this ${kind === "loop" ? "loop" : "cue"} from VirtualDJ"
            aria-label="Delete ${escapeHtml(p.name || kind)}"
          >✕</button>
        </div>`;
    })
    .join("");

  list.querySelectorAll(".cue-row-main").forEach((row) => {
    row.addEventListener("click", () => {
      const idx = Number(row.dataset.index);
      const point = points[idx];
      jumpToCue(point?.pos ?? points[idx]?.pos, point);
    });
  });
  list.querySelectorAll(".cue-delete-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const idx = Number(btn.dataset.index);
      const point = points[idx];
      if (point) deleteCuePoint(point);
    });
  });

  updatePlayhead();
}

/**
 * Patch the in-memory track with fresh cue summary from a mutation API
 * so the list/waveform clear immediately without waiting on full reload.
 */
function applyCueSummaryToTrack(path, cuesSummary) {
  if (!path || !cuesSummary) return null;
  const idx = state.tracks.findIndex((t) => t.path === path);
  if (idx < 0) return null;
  const prev = state.tracks[idx];
  const next = {
    ...prev,
    cues: { ...(prev.cues || {}), ...cuesSummary },
    is_cued: Boolean(cuesSummary.is_cued ?? (cuesSummary.cue_count > 0)),
  };
  // Keep readiness roughly in sync so filters/badges don't lie.
  if (next.readiness || isReviewMode()) {
    const cueN = Number(cuesSummary.cue_count) || 0;
    const loopN = Number(cuesSummary.loop_count) || 0;
    const hasGrid = Boolean(cuesSummary.has_beatgrid);
    let status = "not_cued";
    if (cueN >= 2 && hasGrid) status = "ready";
    else if (cueN > 0 || loopN > 0) status = "partial";
    else if (cuesSummary.in_database === false) status = "missing";
    next.readiness = {
      ...(next.readiness || {}),
      status,
      ready: status === "ready",
      cue_count: cueN,
      loop_count: loopN,
      has_beatgrid: hasGrid,
    };
  }
  state.tracks = state.tracks.map((t, i) => (i === idx ? next : t));
  return next;
}

async function deleteCuePoint(point) {
  const track = currentTrack();
  if (!track || !point) return;
  const kind = pointKind(point);
  const label = point.name || kind;
  const ok = await showConfirmDialog({
    title: `Delete ${kind}?`,
    track: trackDisplayTitle(track),
    message: `Remove “${label}” at ${fmtTime(point.pos)} from VirtualDJ for this file.`,
    note: "Beatgrid and other markers stay. Close VirtualDJ first if it is open.",
    confirmLabel: `Delete ${kind}`,
    tone: "danger",
  });
  if (!ok) return;

  let allowRunning = false;
  if (state.health?.virtualdj_running) {
    allowRunning = await showConfirmDialog({
      title: "VirtualDJ is still open",
      track: trackDisplayTitle(track),
      message:
        "Cue changes may be overwritten when VirtualDJ quits. Close it first when possible.",
      confirmLabel: "Delete anyway",
      tone: "warning",
    });
    if (!allowRunning) {
      setStatus("Close VirtualDJ, then delete the marker.", "error");
      return;
    }
  }

  try {
    setStatus(`Deleting ${kind}: ${label}…`);
    const data = await api("/api/delete-cue", {
      method: "POST",
      body: JSON.stringify({
        path: track.path,
        kind,
        pos: Number(point.pos) || 0,
        num: point.num != null ? String(point.num) : null,
        name: point.name || null,
        slot: point.slot != null ? String(point.slot) : null,
        allow_vdj_running: Boolean(allowRunning),
      }),
    });
    const r = data.result || {};
    // Clear loop play if we deleted the active loop.
    if (kind === "loop" && state.activeLoopKey === cueKey(point)) {
      state.activeLoopKey = null;
      if (state.loopPlaybackOn) {
        state.loopPlaybackOn = false;
        syncLoopPlayBtn();
        stopLoopWatch();
      }
    }
    if (state.activeCueKey === cueKey(point)) state.activeCueKey = null;

    // Immediate UI clear from the API's post-delete cue summary.
    if (r.cues) {
      applyCueSummaryToTrack(track.path, r.cues);
    } else {
      // Fallback: drop the marker locally if server omitted summary.
      const live = currentTrack();
      if (live?.cues?.points) {
        const key = cueKey(point);
        const points = live.cues.points.filter((p) => cueKey(p) !== key);
        applyCueSummaryToTrack(track.path, {
          ...live.cues,
          points,
          cue_count:
            r.cue_count_after != null
              ? r.cue_count_after
              : points.filter((p) => pointKind(p) === "cue").length,
          loop_count:
            r.loop_count_after != null
              ? r.loop_count_after
              : points.filter((p) => pointKind(p) === "loop").length,
        });
      }
    }
    renderCues();
    drawWaveform();
    renderReviewPanel();
    updatePlayerMetaOnly(currentTrack() || track);
    setStatus(
      `Deleted ${kind} “${label}” · ${r.cue_count_after ?? "?"} cues, ${
        r.loop_count_after ?? "?"
      } loops`,
      "success"
    );

    // Background reconcile with full library (don't block / don't wipe UI).
    const pathKeep = track.path;
    const successMsg = `Deleted ${kind} “${label}” · ${
      r.cue_count_after ?? "?"
    } cues, ${r.loop_count_after ?? "?"} loops`;
    loadTracks({ keepPath: pathKeep })
      .then(() => {
        if (currentTrack()?.path === pathKeep) {
          setStatus(successMsg, "success");
          renderCues();
          drawWaveform();
          renderReviewPanel();
        }
      })
      .catch((err) => {
        // Delete already succeeded — keep optimistic UI, surface soft warning.
        setStatus(`${successMsg} (list refresh: ${err.message})`, "success");
      });
  } catch (err) {
    setStatus(err.message, "error");
  }
}

function setStatus(msg, kind = "") {
  const el = $("status");
  el.textContent = msg || "";
  el.className = `status-bar ${kind}`;
}

function trackBpm(track) {
  /** Raw VDJ BPM from the database (may be double-time). */
  const bpm = Number(track?.cues?.bpm);
  return bpm > 0 ? bpm : null;
}

function sourceBpm(track) {
  /**
   * BPM used for playback-rate math. When halfBpm is on, VDJ's value is
   * treated as double-time (common at ~140 when the track is really ~70).
   * Loop/waveform still use raw trackBpm — cue positions are wall-clock.
   */
  const raw = trackBpm(track);
  if (!raw) return null;
  return state.halfBpm ? raw / 2 : raw;
}

function clampRate(rate) {
  return Math.min(1.15, Math.max(0.35, rate));
}

function rateForTargetBpm(originalBpm, targetBpm) {
  if (!originalBpm || originalBpm <= 0) return 1;
  return clampRate(targetBpm / originalBpm);
}

function applyPlaybackRate(rate, { fromZoukButton = false } = {}) {
  const audio = $("audio");
  const r = clampRate(Number(rate) || 1);
  state.playbackRate = r;
  if (audio) {
    audio.playbackRate = r;
    // Keep pitch linked for a natural slow-zouk feel (HTML audio default).
    try {
      audio.preservesPitch = false;
      audio.mozPreservesPitch = false;
      audio.webkitPreservesPitch = false;
    } catch {
      /* ignore */
    }
  }
  const slider = $("speedSlider");
  if (slider && Math.abs(Number(slider.value) - r) > 0.005) {
    slider.value = String(r.toFixed(2));
  }
  updateSpeedUi(fromZoukButton);
}

function enableZoukSpeed() {
  const track = currentTrack();
  const raw = trackBpm(track);
  const bpm = sourceBpm(track);
  const target = Number($("targetBpmInput")?.value) || state.targetBpm || 75;
  state.targetBpm = target;
  if (!bpm) {
    // Fallback ~half speed when BPM unknown (common for ~128–132 house → ~64–66).
    applyPlaybackRate(0.5, { fromZoukButton: true });
    state.zoukSpeedOn = true;
    setStatus("No VDJ BPM found — using 0.5× as a zouk-speed guess.", "error");
    return;
  }
  const rate = rateForTargetBpm(bpm, target);
  state.zoukSpeedOn = true;
  applyPlaybackRate(rate, { fromZoukButton: true });
  const halfNote =
    state.halfBpm && raw ? ` (½ of VDJ ${raw.toFixed(0)})` : "";
  setStatus(
    `Zouk speed: ${bpm.toFixed(1)}${halfNote} → ~${(bpm * rate).toFixed(1)} BPM (${rate.toFixed(2)}×)`
  );
}

function enableNormalSpeed() {
  state.zoukSpeedOn = false;
  applyPlaybackRate(1);
  setStatus("Playback at original speed");
}

function setHalfBpm(on) {
  state.halfBpm = Boolean(on);
  const halfBtn = $("halfBpmBtn");
  if (halfBtn) halfBtn.classList.toggle("active", state.halfBpm);
  // Re-render meta so the BPM badge shows halved / full.
  const track = currentTrack();
  if (track) updatePlayerMetaOnly(track);
  if (state.zoukSpeedOn || state.playbackRate < 0.98) {
    enableZoukSpeed();
  } else {
    updateSpeedUi();
    if (state.halfBpm) {
      const raw = trackBpm(track);
      const half = sourceBpm(track);
      setStatus(
        half && raw
          ? `Source BPM halved: VDJ ${raw.toFixed(0)} → ${half.toFixed(0)} (toggle off or press H)`
          : "½ BPM on — no VDJ BPM on this track"
      );
    } else {
      setStatus("Using full VDJ BPM");
    }
  }
}

function toggleHalfBpm() {
  setHalfBpm(!state.halfBpm);
}

function updateSpeedUi() {
  const track = currentTrack();
  const raw = trackBpm(track);
  const bpm = sourceBpm(track);
  const rate = state.playbackRate || 1;
  const bpmBadge = $("bpmBadge");
  const rateBadge = $("rateBadge");
  const hint = $("speedHint");
  const zoukBtn = $("zoukSpeedBtn");
  const halfBtn = $("halfBpmBtn");

  if (halfBtn) halfBtn.classList.toggle("active", state.halfBpm);

  if (bpmBadge) {
    if (bpm && raw) {
      const effective = bpm * rate;
      bpmBadge.textContent = state.halfBpm
        ? `${raw.toFixed(0)}÷2=${bpm.toFixed(0)} → ${effective.toFixed(0)}`
        : `${bpm.toFixed(0)} → ${effective.toFixed(0)} BPM`;
      bpmBadge.className =
        state.halfBpm || rate < 0.98 ? "badge ok" : "badge neutral";
      bpmBadge.title = state.halfBpm
        ? `VDJ reported ${raw.toFixed(1)}; treating as ${bpm.toFixed(1)} (half)`
        : `VDJ BPM ${raw.toFixed(1)}`;
    } else {
      bpmBadge.textContent = "BPM unknown";
      bpmBadge.className = "badge warn";
      bpmBadge.title = "";
    }
  }
  if (rateBadge) {
    rateBadge.textContent = `${rate.toFixed(2)}×`;
    rateBadge.className = rate < 0.98 ? "badge ok" : "badge neutral";
  }
  if (hint) {
    if (Math.abs(rate - 1) < 0.01) {
      hint.textContent = state.halfBpm
        ? "Native speed · ½ BPM on for next Zouk"
        : "Native speed";
    } else if (bpm) {
      hint.textContent = state.halfBpm
        ? `Slowed from ½ BPM (~${(bpm * rate).toFixed(0)} BPM feel)`
        : `Slowed for zouk feel (~${(bpm * rate).toFixed(0)} BPM)`;
    } else {
      hint.textContent = `Playback rate ${rate.toFixed(2)}×`;
    }
  }
  if (zoukBtn) {
    zoukBtn.classList.toggle("active", state.zoukSpeedOn || rate < 0.98);
  }
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  let data = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }
  if (!res.ok) {
    const detail = data?.detail || res.statusText || "Request failed";
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

function currentTrack() {
  return state.tracks[state.index] || null;
}

function trackReadinessStatus(track) {
  // Never invent "not_cued" for missing readiness — that flooded the Not cued
  // filter when a stale Sort-mode list (no readiness field) was still on screen.
  const status = track?.readiness?.status;
  return typeof status === "string" ? status : null;
}

function trackMatchesSearch(track, query) {
  if (!query) return true;
  const q = query.toLowerCase();
  const hay = [
    track.name,
    track.relative_path,
    track.group,
    trackDisplayTitle(track),
    trackDisplayArtist(track),
    track.cues?.title,
    track.cues?.author,
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  // All whitespace-separated tokens must match (order-independent).
  return q.split(/\s+/).filter(Boolean).every((tok) => hay.includes(tok));
}

function filteredTrackIndexes() {
  const q = (state.trackSearch || "").trim();
  return state.tracks
    .map((t, i) => i)
    .filter((i) => {
      const track = state.tracks[i];
      if (!trackMatchesSearch(track, q)) return false;
      if (!isReviewMode() || state.readinessFilter === "all") return true;
      const status = trackReadinessStatus(track);
      if (!status) return false;
      if (state.readinessFilter === "ready") return status === "ready";
      if (state.readinessFilter === "partial") return status === "partial";
      if (state.readinessFilter === "not_cued") {
        return status === "not_cued" || status === "missing";
      }
      return true;
    });
}

function readinessBadge(track) {
  const r = track.readiness;
  if (!r || !r.status) {
    if (isReviewMode()) {
      return track?.is_cued
        ? `<span class="badge warn">Cued · status unknown</span>`
        : `<span class="badge uncued">Not assessed</span>`;
    }
    return "";
  }
  if (r.status === "ready") return `<span class="badge ok">${escapeHtml(r.label)}</span>`;
  if (r.status === "partial") return `<span class="badge warn">${escapeHtml(r.label)}</span>`;
  if (r.status === "missing") return `<span class="badge bad">${escapeHtml(r.label)}</span>`;
  return `<span class="badge uncued">${escapeHtml(r.label)}</span>`;
}

function renderTrackList() {
  const root = $("trackList");
  const indexes = filteredTrackIndexes();
  if (!state.tracks.length) {
    root.innerHTML = `<div class="empty">${
      isReviewMode() ? "No tracks in Add Cues." : "No tracks in Ready for Sort."
    }</div>`;
    return;
  }
  if (!indexes.length) {
    root.innerHTML = `<div class="empty">${
      state.trackSearch.trim()
        ? "No tracks match this search."
        : "No tracks match this filter."
    }</div>`;
    return;
  }

  root.innerHTML = indexes
    .map((i) => {
      const t = state.tracks[i];
      const cued = t.is_cued;
      const badge = isReviewMode()
        ? readinessBadge(t)
        : cued
          ? `<span class="badge ok">${t.cues.cue_count} cues</span>`
          : `<span class="badge uncued">Not cued</span>`;
      const grid = isReviewMode() ? gridBadge(t) : "";
      const loops =
        t.cues?.loop_count > 0
          ? `<span class="badge neutral">${t.cues.loop_count} loops</span>`
          : "";
      const stems = t.stems_path ? `<span class="badge neutral">stems</span>` : "";
      const group =
        isReviewMode() && t.group
          ? `<span class="badge neutral">${escapeHtml(t.group)}</span>`
          : "";
      const br = formatBitrate(t.bitrate_kbps);
      const brBadge = br
        ? `<span class="badge ${bitrateBadgeClass(t.bitrate_kbps)}">${escapeHtml(br)}</span>`
        : "";
      const placements = t.placements || {};
      const libCued = (placements.library || []).some((p) => p.is_cued);
      const archCued = (placements.cues_sorted || []).some((p) => p.is_cued);
      const placementBadges = [
        placements.in_cues_sorted
          ? `<span class="badge ${archCued ? "ok" : "warn"}" title="${escapeHtml(
              (placements.cues_sorted || []).map((p) => p.relative_path).join(", ")
            )}">Archive ${archCued ? "cued" : "uncued"}</span>`
          : "",
        placements.in_library
          ? `<span class="badge ${libCued ? "ok" : "warn"}" title="${escapeHtml(
              (placements.library || [])
                .map((p) => `${p.root_name}/${p.relative_path}`)
                .join(", ")
            )}">Lib ${libCued ? "cued" : "uncued"}</span>`
          : "",
      ].join("");
      return `
        <button class="track ${i === state.index ? "active" : ""} ${cued ? "" : "uncued-row"} ${
          placements.already_sorted ? "already-sorted-row" : ""
        }"
                data-index="${i}" type="button" title="${escapeHtml(t.name)}">
          <div class="track-title">${escapeHtml(trackDisplayTitle(t))}</div>
          ${
            trackDisplayArtist(t)
              ? `<div class="track-artist">${escapeHtml(trackDisplayArtist(t))}</div>`
              : ""
          }
          <div class="track-meta">
            ${badge}
            ${grid}
            ${brBadge}
            ${placementBadges}
            ${group}
            ${loops}
            ${stems}
            <span class="badge neutral">${fmtBytes(t.size_bytes)}</span>
          </div>
        </button>`;
    })
    .join("");

  root.querySelectorAll(".track").forEach((btn) => {
    btn.addEventListener("click", () => selectTrack(Number(btn.dataset.index)));
  });
}

function escapeHtml(s) {
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function setPlayerLoading(isLoading) {
  const panel = document.querySelector(".panel.player");
  if (!panel) return;
  panel.classList.toggle("is-loading", Boolean(isLoading));
}

function setNotesStatus(text, kind = "") {
  const el = $("vdjNotesStatus");
  if (!el) return;
  el.textContent = text || "—";
  el.className = `badge ${
    kind === "ok" ? "ok" : kind === "error" ? "bad" : kind === "warn" ? "warn" : "neutral"
  }`;
}

function bindNotesToTrack(track) {
  const ta = $("vdjNotes");
  if (!ta) return;
  // Don't clobber in-progress typing for the same path.
  if (
    state.notesDirty &&
    state.notesPath &&
    track &&
    state.notesPath === track.path
  ) {
    return;
  }
  if (state.notesSaveTimer) {
    clearTimeout(state.notesSaveTimer);
    state.notesSaveTimer = null;
  }
  state.notesDirty = false;
  if (!track) {
    state.notesPath = null;
    ta.value = "";
    ta.disabled = true;
    setNotesStatus("—");
    return;
  }
  state.notesPath = track.path;
  ta.disabled = !track.cues?.in_database;
  ta.value = track.cues?.comment || "";
  if (!track.cues?.in_database) {
    setNotesStatus("not in VDJ", "warn");
  } else if (ta.value.trim()) {
    setNotesStatus("loaded", "ok");
  } else {
    setNotesStatus("empty");
  }
}

function scheduleNotesSave() {
  const ta = $("vdjNotes");
  const track = currentTrack();
  if (!ta || !track || ta.disabled) return;
  if (state.notesPath && state.notesPath !== track.path) return;

  state.notesDirty = true;
  setNotesStatus("typing…");
  if (state.notesSaveTimer) clearTimeout(state.notesSaveTimer);
  const path = track.path;
  const text = ta.value;
  const gen = ++state.notesSaveGen;
  state.notesSaveTimer = setTimeout(() => {
    state.notesSaveTimer = null;
    saveVdjNotes(path, text, gen);
  }, 550);
}

async function saveVdjNotes(path, comment, gen) {
  if (gen != null && gen !== state.notesSaveGen) return;
  if (currentTrack()?.path !== path) return;

  setNotesStatus("saving…", "warn");
  try {
    const data = await api("/api/notes", {
      method: "POST",
      body: JSON.stringify({
        path,
        comment,
        allow_vdj_running: true,
        create_backup: false,
      }),
    });
    if (gen != null && gen !== state.notesSaveGen) return;
    if (currentTrack()?.path !== path) return;

    const saved = data.result?.comment ?? comment;
    const track = currentTrack();
    if (track && track.path === path) {
      if (!track.cues) track.cues = {};
      track.cues.comment = saved;
    }
    state.notesDirty = false;
    const ta = $("vdjNotes");
    // Only sync value if user hasn't kept typing past this save.
    if (ta && ta.value === comment) {
      setNotesStatus(
        data.result?.unchanged ? "saved" : "saved to VDJ",
        "ok"
      );
    } else {
      setNotesStatus("saved · editing…", "ok");
    }
  } catch (err) {
    if (gen != null && gen !== state.notesSaveGen) return;
    setNotesStatus(err.message || "save failed", "error");
    state.notesDirty = true;
  }
}

function renderPlayer() {
  const track = currentTrack();
  const gen = state.trackGen;
  const title = $("nowPlaying");
  const meta = $("playerMeta");
  const audio = $("audio");
  const recBox = $("recommendation");
  const block = $("blockBanner");
  const sortBtn = $("sortBtn");

  if (!track) {
    title.textContent = isReviewMode() ? "Nothing to review" : "Nothing to sort";
    title.removeAttribute("title");
    title.removeAttribute("aria-label");
    meta.innerHTML = "";
    bindNotesToTrack(null);
    audio.pause();
    audio.removeAttribute("src");
    try {
      audio.load();
    } catch {
      /* ignore */
    }
    recBox.className = "recommendation loading";
    recBox.innerHTML = isReviewMode()
      ? "Pick a track from Add Cues to review its markers."
      : "Load a track to get an AI folder suggestion.";
    block.hidden = true;
    if ($("placementCard")) {
      $("placementCard").hidden = true;
      $("placementCard").innerHTML = "";
    }
    sortBtn.disabled = true;
    if ($("removeReadyBtn")) $("removeReadyBtn").disabled = true;
    if ($("demoteReadyBtn")) {
      $("demoteReadyBtn").disabled = true;
      $("demoteReadyBtn").hidden = isReviewMode();
    }
    state.activeCueKey = null;
    state.waveform = null;
    resetWaveZoom();
    setPlayerLoading(false);
    renderCues();
    drawWaveform();
    setWaveformStatus("No track selected");
    renderReviewPanel();
    syncAutocueUi();
    state.gridPreflight = null;
    renderGridPreflightCard(null);
    updateTransportUi();
    return;
  }

  renderNowPlayingTitle(track);
  setPlayerLoading(true);
  // Show known meta immediately; kbps fills in when probe returns.
  if (state.trackMeta?.path !== track.path) {
    state.trackMeta = track.bitrate_kbps
      ? {
          path: track.path,
          bitrate_kbps: track.bitrate_kbps,
          codec: track.codec,
          sample_rate: track.sample_rate,
        }
      : null;
  }
  meta.innerHTML = buildPlayerMetaHtml(track);
  loadTrackMeta(track, gen);

  renderPlacementCard(track);

  const src = `/api/audio?path=${encodeURIComponent(track.path)}`;
  if (audio.dataset.path !== track.path) {
    // Stop previous download immediately so rapid switches don't pile up.
    audio.pause();
    audio.removeAttribute("src");
    try {
      audio.load();
    } catch {
      /* ignore */
    }
    audio.dataset.path = track.path;
    audio.src = src;
    state.activeCueKey = null;
    scheduleWaveformLoad(track, gen);
    applyPlaybackRate(state.playbackRate);
    // Defer play slightly so aborted switches don't start audio.
    setTimeout(() => {
      if (gen !== state.trackGen || currentTrack()?.path !== track.path) return;
      audio.play().catch(() => {});
      setPlayerLoading(false);
    }, 160);
  } else {
    if (!state.waveform && !state.waveformLoading) {
      scheduleWaveformLoad(track, gen);
    }
    setPlayerLoading(false);
  }
  updateSpeedUi();
  updateTransportUi();
  bindNotesToTrack(track);

  if ($("removeReadyBtn")) {
    $("removeReadyBtn").disabled = isReviewMode();
    $("removeReadyBtn").hidden = isReviewMode();
  }
  if ($("demoteReadyBtn")) {
    $("demoteReadyBtn").disabled = isReviewMode() || !track;
    $("demoteReadyBtn").hidden = isReviewMode();
  }

  // blockBanner is only for uncued / blocking messages — not placement details.
  if (isReviewMode()) {
    block.hidden = true;
    sortBtn.disabled = true;
    updateApproveButtons();
  } else if (!track.is_cued) {
    block.hidden = false;
    block.className = "block-banner";
    block.textContent =
      "No VirtualDJ cue points yet. Sort is locked — you can still Remove from Ready only.";
    sortBtn.disabled = true;
  } else {
    block.hidden = true;
    sortBtn.disabled = !state.selectedPath;
  }

  renderCues();
  drawWaveform();
  if (isReviewMode()) {
    recBox.hidden = true;
    renderReviewPanel();
  } else {
    recBox.hidden = false;
    renderRecommendation();
  }
  syncAutocueUi();
  loadDeepGridPreflight(track, gen);
}

function placementCueBadge(hit) {
  if (hit.is_cued) {
    const loops = hit.loop_count ? ` · ${hit.loop_count} loops` : "";
    return `<span class="badge ok">${hit.cue_count} cues${loops}</span>`;
  }
  if (hit.in_database) {
    return `<span class="badge uncued">Not cued</span>`;
  }
  return `<span class="badge bad">Not in VDJ</span>`;
}

function placementPathRow(labelPath, hit) {
  const bpm = hit.bpm ? `<span class="badge neutral">${Number(hit.bpm).toFixed(0)} BPM</span>` : "";
  const grid = hit.has_beatgrid ? `<span class="badge neutral">grid</span>` : "";
  const pathAttr = escapeHtml(hit.path || "");
  return `
    <div class="placement-path-row" data-placement-path="${pathAttr}">
      <div class="placement-path-main">
        <div class="placement-path">${escapeHtml(labelPath)}</div>
        <div class="placement-path-meta">
          ${placementCueBadge(hit)}
          ${bpm}
          ${grid}
        </div>
      </div>
      <button
        type="button"
        class="btn ghost danger placement-delete-btn"
        data-placement-path="${pathAttr}"
        title="Delete this copy from disk and remove its cues from VirtualDJ"
        aria-label="Delete placement ${escapeHtml(labelPath)}"
      >✕ Delete</button>
    </div>`;
}

function renderPlacementCard(track) {
  const card = $("placementCard");
  if (!card) return;

  if (isReviewMode() || !track?.placements?.already_sorted) {
    card.hidden = true;
    card.innerHTML = "";
    return;
  }

  const rows = [];
  const libs = track.placements.library || [];
  const sorted = track.placements.cues_sorted || [];

  if (libs.length) {
    const paths = libs
      .map((p) =>
        placementPathRow(`${p.root_name}/${p.relative_path}`, p)
      )
      .join("");
    rows.push(`
      <div class="placement-row">
        <div class="placement-label">Library</div>
        <div class="placement-paths">${paths}</div>
      </div>`);
  }
  if (sorted.length) {
    const paths = sorted
      .map((p) => placementPathRow(`Cues Sorted/${p.relative_path}`, p))
      .join("");
    rows.push(`
      <div class="placement-row">
        <div class="placement-label">Archive</div>
        <div class="placement-paths">${paths}</div>
      </div>`);
  }

  const cuedN =
    libs.filter((h) => h.is_cued).length + sorted.filter((h) => h.is_cued).length;
  const totalN = libs.length + sorted.length;
  const titleExtra =
    totalN > 0
      ? ` · ${cuedN}/${totalN} cued`
      : "";

  card.hidden = false;
  card.innerHTML = `
    <div class="placement-card-title">Already in library${titleExtra}</div>
    <div class="placement-rows">${rows.join("")}</div>
    <div class="placement-card-note">
      Sorting still writes to your chosen folder + Cues Sorted when those paths differ.
      Delete removes that copy (Trash) and its VirtualDJ cues — Ready for Sort stays.
    </div>
  `;

  card.querySelectorAll(".placement-delete-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const path = btn.getAttribute("data-placement-path");
      if (path) deleteLibraryPlacement(path);
    });
  });
}

async function deleteLibraryPlacement(placementPath) {
  const track = currentTrack();
  if (!placementPath) return;

  // Resolve label from current placements if possible.
  const allHits = [
    ...(track?.placements?.library || []),
    ...(track?.placements?.cues_sorted || []),
  ];
  const hit = allHits.find((h) => h.path === placementPath);
  const label = hit
    ? hit.root_name === "Cues Sorted" || (hit.root || "").includes("Cues Sorted")
      ? `Cues Sorted/${hit.relative_path}`
      : `${hit.root_name}/${hit.relative_path}`
    : placementPath.split("/").slice(-3).join("/");
  const cueNote =
    hit?.is_cued
      ? ` This copy has ${hit.cue_count || 0} cues` +
        (hit.loop_count ? ` and ${hit.loop_count} loops` : "") +
        " in VirtualDJ — they will be removed for this path only."
      : hit?.in_database
        ? " The VirtualDJ Song entry for this path will be removed (no manual cues found)."
        : " No VirtualDJ entry was found for this path (file still goes to Trash).";

  const ok = await showConfirmDialog({
    title: "Delete library copy?",
    track: track ? trackDisplayTitle(track) : label,
    message: `Remove “${label}” from disk and delete its VirtualDJ database entry (cues/loops for that path).`,
    note:
      `File moves to Trash (recoverable). Ready for Sort is not touched.${cueNote} Close VirtualDJ first if it is open.`,
    confirmLabel: "Delete copy + VDJ cues",
    tone: "danger",
  });
  if (!ok) return;

  let allowRunning = false;
  if (state.health?.virtualdj_running) {
    allowRunning = await showConfirmDialog({
      title: "VirtualDJ is still open",
      track: track ? trackDisplayTitle(track) : label,
      message:
        "Database edits may be overwritten when VirtualDJ quits. Close it first when possible.",
      confirmLabel: "Delete anyway",
      tone: "warning",
    });
    if (!allowRunning) {
      setStatus("Close VirtualDJ, then delete the library copy.", "error");
      return;
    }
  }

  try {
    setStatus(`Deleting placement: ${label}…`);
    const data = await api("/api/delete-placement", {
      method: "POST",
      body: JSON.stringify({
        path: placementPath,
        to_trash: true,
        allow_vdj_running: Boolean(allowRunning),
      }),
    });
    const r = data.result || {};
    const dbBit = r.database?.removed_from_db
      ? ` · VDJ entry removed (${r.had_cues ?? 0} cues, ${r.had_loops ?? 0} loops)`
      : r.database?.reason === "not_in_database"
        ? " · not in VDJ DB"
        : "";
    setStatus(
      `Deleted ${r.root_name || ""}/${r.relative_path || label}${dbBit}`,
      "success"
    );
    // Refresh placements for the Ready track (source stays).
    await loadTracks({ keepPath: track?.path });
    if (!isReviewMode()) await loadFolders();
  } catch (err) {
    setStatus(err.message, "error");
  }
}

function renderReviewPanel() {
  const card = $("readinessCard");
  const track = currentTrack();
  if (!card) return;
  if (!track) {
    card.innerHTML = `<div class="subtitle">Select a track to assess readiness</div>`;
    updateApproveButtons();
    return;
  }

  const r = track.readiness || {};
  const checks = r.checks || {};
  const rows = [
    ["In VDJ database", checks.in_database],
    ["Beatgrid present", checks.has_beatgrid],
    ["Has cue points", checks.has_cues],
    ["At least 2 cues", checks.multiple_cues],
    ["Has loops", checks.has_loops],
  ]
    .map(
      ([label, ok]) => `
      <div class="check-row">
        <span class="${ok ? "check-ok" : "check-no"}">${ok ? "✓" : "–"}</span>
        <span>${escapeHtml(label)}</span>
      </div>`
    )
    .join("");

  const g = state.gridPreflight || track.grid || {};
  const gridLine = g.label
    ? `<div class="review-grid-status">Beatgrid: ${escapeHtml(g.label)}${
        g.can_autocue === false ? " · AutoCue blocked" : g.can_autocue ? " · AutoCue ok" : ""
      }</div>`
    : "";

  card.innerHTML = `
    <div class="readiness-heading">
      <div class="meta-row">${readinessBadge(track)}</div>
      <div class="readiness-summary">${escapeHtml(r.summary || "")}</div>
    </div>
    <div class="check-list">${rows}</div>
    ${gridLine}
    <div class="review-guidance">
      ${
        r.ready
          ? "Markers look complete. Listen through key cues, then approve."
          : "Not auto-ready — jump cues, listen, and only promote if they feel right."
      }
    </div>
    ${
      track.relative_path
        ? `<div class="review-path" title="${escapeHtml(track.relative_path)}">
            <span>File</span>
            <strong>${escapeHtml(track.relative_path)}</strong>
          </div>`
        : ""
    }
  `;
  updateApproveButtons();
}

function updateApproveButtons() {
  const track = currentTrack();
  const canApprove = Boolean(track && track.is_cued);
  const hasTrack = Boolean(track);
  ["approveBtn", "approveBtnSide"].forEach((id) => {
    const el = $(id);
    if (el) el.disabled = !canApprove;
  });
  ["toNoCuesBtn", "toLowSkipBtn", "toAcLowBtn"].forEach((id) => {
    const el = $(id);
    if (el) el.disabled = !hasTrack;
  });
}

function renderRecommendation() {
  const recBox = $("recommendation");
  const rec = state.recommendation;
  const track = currentTrack();

  if (isReviewMode()) {
    recBox.hidden = true;
    return;
  }
  recBox.hidden = false;

  if (!track) return;

  if (rec === null) {
    recBox.className = "recommendation loading";
    recBox.innerHTML = "Asking Gemini for a folder recommendation…";
    return;
  }

  if (rec.error) {
    recBox.className = "recommendation error";
    recBox.innerHTML = `<strong>Recommendation failed</strong><div class="rec-reason">${escapeHtml(
      rec.error
    )}</div>`;
    return;
  }

  const conf = Math.round((rec.confidence || 0) * 100);
  const tags = (rec.vibe_tags || [])
    .map((t) => `<span class="badge neutral">${escapeHtml(t)}</span>`)
    .join(" ");
  const alts = (rec.alternatives || [])
    .map(
      (a) =>
        `<button type="button" class="chip" data-lib="${escapeHtml(
          rec.library
        )}" data-path="${escapeHtml(a)}">${escapeHtml(rec.library)} / ${escapeHtml(a)}</button>`
    )
    .join("");

  recBox.className = "recommendation";
  recBox.innerHTML = `
    <div class="subtitle">Gemini suggestion ${rec.cached ? "(cached)" : ""} · ${conf}% · ${escapeHtml(
      rec.model || ""
    )}</div>
    <div class="rec-path">${escapeHtml(rec.library)} / ${escapeHtml(rec.relative_path)}</div>
    <div class="rec-reason">${escapeHtml(rec.reasoning || "")}</div>
    <div class="meta-row" style="margin-top:8px">${tags}</div>
    <div class="rec-alts">
      <button type="button" class="chip primary" data-lib="${escapeHtml(
        rec.library
      )}" data-path="${escapeHtml(rec.relative_path)}">Use recommendation</button>
      ${alts}
    </div>
  `;

  recBox.querySelectorAll(".chip").forEach((btn) => {
    btn.addEventListener("click", () => {
      applyFolderSelection(btn.dataset.lib, btn.dataset.path);
    });
  });
}

function pathModeLabel() {
  if (state.library === "Both") return "House + Zouk";
  return state.library;
}

function updatePathHint() {
  const el = $("pathHint");
  if (!el) return;
  if (state.library === "Both") {
    el.textContent =
      "Sort path: Both — places into House and Zouk at this folder, plus Cues Sorted";
  } else {
    el.textContent = `Sort path: ${state.library} only · also archives to Cues Sorted`;
  }
}

function applyFolderSelection(library, relativePath) {
  // Gemini may suggest House or Zouk; if user is in Both mode, keep Both.
  if (library && state.library !== "Both" && library !== state.library) {
    state.library = library;
    document.querySelectorAll("#libraryPathSeg button").forEach((b) => {
      b.classList.toggle("active", b.dataset.library === library);
    });
    updatePathHint();
    loadFolders().then(() => {
      selectFolder(relativePath, { expand: true });
    });
  } else {
    selectFolder(relativePath, { expand: true });
  }
}

function selectFolder(relativePath, { expand = false } = {}) {
  state.selectedPath = relativePath || "";
  if (expand && relativePath) {
    const parts = relativePath.split("/");
    let acc = [];
    for (const part of parts) {
      acc.push(part);
      state.expanded.add(acc.join("/"));
    }
  }
  $("selectedFolder").textContent = state.selectedPath
    ? `${pathModeLabel()} / ${state.selectedPath}`
    : "None selected";
  $("createParentHint").textContent = state.selectedPath
    ? `New folder will be created under: ${pathModeLabel()} / ${state.selectedPath}`
    : `New folder will be created at top level of ${pathModeLabel()}`;
  renderFolders();
  const track = currentTrack();
  $("sortBtn").disabled = !(track && track.is_cued && state.selectedPath);
}

function folderMatchesFilter(node, filter) {
  if (!filter) return true;
  const f = filter.toLowerCase();
  if (node.relative_path.toLowerCase().includes(f) || node.name.toLowerCase().includes(f)) {
    return true;
  }
  return (node.children || []).some((c) => folderMatchesFilter(c, filter));
}

function renderFolderNode(node, depth = 0) {
  if (!folderMatchesFilter(node, state.filter)) return "";
  const hasKids = (node.children || []).length > 0;
  const open = state.expanded.has(node.relative_path) || Boolean(state.filter);
  const selected = state.selectedPath === node.relative_path;
  const rec = state.recommendation;
  const isRec =
    rec &&
    !rec.error &&
    rec.library === state.library &&
    rec.relative_path === node.relative_path;

  const kids = hasKids && open
    ? `<div class="children">${node.children.map((c) => renderFolderNode(c, depth + 1)).join("")}</div>`
    : "";

  return `
    <div>
      <div class="folder-row" style="padding-left:${depth > 0 ? 0 : 0}px">
        ${
          hasKids
            ? `<button type="button" class="toggle" data-toggle="${escapeHtml(
                node.relative_path
              )}">${open ? "▾" : "▸"}</button>`
            : `<span class="toggle"></span>`
        }
        <button type="button" class="folder ${selected ? "selected" : ""} ${
          isRec ? "recommended" : ""
        }" data-path="${escapeHtml(node.relative_path)}">
          <span class="folder-name">${escapeHtml(node.name)}</span>
          <span class="folder-count">${node.track_count}</span>
        </button>
      </div>
      ${kids}
    </div>
  `;
}

function renderFolderSections(folders, title) {
  if (!folders || !folders.length) return "";
  const vibes = folders.filter((f) => f.group === "vibe");
  const artists = folders.filter((f) => f.group !== "vibe");
  let html = title
    ? `<div class="subtitle library-section-title">${escapeHtml(title)}</div>`
    : "";
  if (vibes.length) {
    html += `<div class="subtitle" style="padding:6px 8px">Vibes / emotions</div>`;
    html += vibes.map((n) => renderFolderNode(n)).join("");
  }
  if (artists.length) {
    html += `<div class="subtitle" style="padding:10px 8px 6px">Artists / collections</div>`;
    html += artists.map((n) => renderFolderNode(n)).join("");
  }
  return html;
}

function renderFolders() {
  const root = $("folderTree");
  let html = "";

  if (state.library === "Both" && state.folderTrees) {
    html += renderFolderSections(state.folderTrees.Zouk?.folders || [], "Zouk");
    html += renderFolderSections(state.folderTrees.House?.folders || [], "House");
    if (!html) html = `<div class="empty">No folders found.</div>`;
  } else if (!state.folders.length) {
    html = `<div class="empty">No folders found.</div>`;
  } else {
    html = renderFolderSections(state.folders, "");
  }

  root.innerHTML = html;

  root.querySelectorAll("[data-path]").forEach((btn) => {
    btn.addEventListener("click", () => selectFolder(btn.dataset.path));
  });
  root.querySelectorAll("[data-toggle]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const p = btn.dataset.toggle;
      if (state.expanded.has(p)) state.expanded.delete(p);
      else state.expanded.add(p);
      renderFolders();
    });
  });
}

async function loadHealth() {
  state.health = await api("/api/health");
  const vdj = state.health.virtualdj_running;
  $("vdjBadge").className = `badge ${vdj ? "warn" : "ok"}`;
  $("vdjBadge").textContent = vdj ? "VirtualDJ running" : "VirtualDJ closed";
  // Do not wipe countsBadge — a concurrent loadTracks owns that label.
}

async function loadTracks({ keepPath } = {}) {
  const listEl = $("trackList");
  const requestedMode = state.mode;
  const loadGen = ++state.tracksLoadGen;
  if (listEl) listEl.classList.add("list-loading");
  setStatus(requestedMode === "add_cues" ? "Loading Add Cues…" : "Loading Ready for Sort…");

  try {
    const data = await api(`/api/tracks?mode=${encodeURIComponent(requestedMode)}`);
    // Drop stale responses: mode switch or a newer refresh finished first.
    if (loadGen !== state.tracksLoadGen || state.mode !== requestedMode) {
      return;
    }
    if (data.mode && data.mode !== requestedMode) {
      return;
    }

    const prevPath = keepPath || currentTrack()?.path;
    state.tracks = data.tracks || [];
    const counts = data.counts || {};

    if (requestedMode === "add_cues") {
      $("countsBadge").textContent = `${counts.ready || 0} ready · ${
        counts.partial || 0
      } partial · ${counts.not_cued || 0} not cued`;
      $("countsBadge").className =
        (counts.ready || 0) > 0 ? "badge ok" : "badge warn";
    } else {
      $("countsBadge").textContent = `${counts.cued || 0} cued · ${counts.uncued || 0} not cued`;
      $("countsBadge").className = counts.uncued ? "badge uncued" : "badge ok";
    }

    let idx = state.tracks.findIndex((t) => t.path === prevPath);
    if (idx < 0) {
      const filtered = filteredTrackIndexes();
      idx = filtered.length ? filtered[0] : 0;
    }
    state.index = idx;
    state.trackGen += 1;
    renderTrackList();
    renderPlayer();
    if (currentTrack() && requestedMode === "sort") requestRecommendation(currentTrack());
    setStatus(
      requestedMode === "add_cues"
        ? `Add Cues · ${counts.total || state.tracks.length} tracks`
        : `Ready for Sort · ${counts.total || state.tracks.length} tracks`
    );
    updateBatchAddCuesButton();
  } finally {
    // Only clear loading style if this is still the latest load for this mode.
    if (listEl && loadGen === state.tracksLoadGen) {
      listEl.classList.remove("list-loading");
    }
  }
}

function applyModeUi() {
  const review = isReviewMode();
  $("listTitle").textContent = review ? "Add Cues" : "Ready for Sort";
  $("listSubtitle").textContent = review
    ? "Review cued tracks before Ready for Sort"
    : "Cued tracks only can be moved into House / Zouk";
  $("playerSubtitle").textContent = review
    ? "Listen to cues — promote only when they feel right"
    : "Listen, then confirm or override the AI pick";
  $("listToolbar").hidden = !review;
  const trackSearch = $("trackSearch");
  if (trackSearch) {
    trackSearch.placeholder = review
      ? "Search Add Cues…"
      : "Search Ready for Sort…";
  }
  $("foldersPanel").hidden = review;
  $("reviewPanel").hidden = !review;
  $("sortActions").hidden = review;
  $("reviewActions").hidden = !review;
  $("rerunRecBtn").hidden = review;
  $("recommendation").hidden = review;
  // AutoCue scope buttons live in Add Cues review, not Sort.
  const headerScopes = $("autocueScopeHeader");
  if (headerScopes) headerScopes.hidden = !review;
  const retryStatus = $("retryStatus");
  if (retryStatus && !review) {
    retryStatus.hidden = true;
  }
  if (review) syncAutocueUi();
  $("shortcutsHint").innerHTML = review
    ? `Shortcuts: <span class="kbd">Space</span> play/pause ·
       <span class="kbd">J</span>/<span class="kbd">K</span> tracks ·
       <span class="kbd">1</span>–<span class="kbd">9</span> jump cues ·
       <span class="kbd">L</span> loop play ·
       <span class="kbd">Z</span> zouk · <span class="kbd">H</span> ½ BPM ·
       <span class="kbd">A</span> approve · <span class="kbd">S</span> skip`
    : `Shortcuts: <span class="kbd">Space</span> play/pause ·
       <span class="kbd">J</span>/<span class="kbd">K</span> tracks ·
       <span class="kbd">1</span>–<span class="kbd">9</span> jump cues ·
       <span class="kbd">L</span> loop play ·
       <span class="kbd">Z</span> zouk · <span class="kbd">H</span> ½ BPM ·
       <span class="kbd">⌘</span>+<span class="kbd">Enter</span> sort`;

  document.querySelectorAll("#modeSeg button").forEach((b) => {
    b.classList.toggle("active", b.dataset.mode === state.mode);
  });
}

async function setMode(mode) {
  if (mode !== "sort" && mode !== "add_cues") return;
  if (state.mode === mode) return;
  state.mode = mode;
  state.recommendation = null;
  state.selectedPath = "";
  state.readinessFilter = "all";
  state.tracks = [];
  state.index = 0;
  state.trackGen += 1;
  // Invalidate any in-flight /api/tracks from the previous mode (Sort loads
  // can finish after Add Cues is selected and used to flood "Not cued").
  state.tracksLoadGen += 1;
  state.waveform = null;
  if (state.waveformAbort) state.waveformAbort.abort();
  if (state.waveformDebounce) clearTimeout(state.waveformDebounce);
  document.querySelectorAll("#readinessFilter button").forEach((b) => {
    b.classList.toggle("active", b.dataset.filter === "all");
  });
  $("countsBadge").textContent = "Loading…";
  $("countsBadge").className = "badge neutral";
  applyModeUi();
  resetWorkspaceScroll();
  renderTrackList();
  setWaveformStatus("Loading…");
  setPlayerLoading(true);
  await loadTracks();
  if (!isReviewMode()) await loadFolders();
  requestAnimationFrame(resetWorkspaceScroll);
}

async function loadFolders() {
  const data = await api(`/api/folders/${encodeURIComponent(state.library)}`);
  state.folders = data.folders || [];
  state.folderTrees = data.trees || null;
  renderFolders();
  updatePathHint();
}

async function selectTrack(index) {
  state.index = index;
  state.trackGen += 1;
  state.recommendation = null;
  state.trackMeta = null;
  state.activeLoopKey = null;
  stopLoopWatch();
  if (state.metaAbort) state.metaAbort.abort();
  // Immediate feedback before any async work
  setWaveformStatus("Loading waveform…");
  state.waveform = null;
  resetWaveZoom();
  resetWorkspaceScroll();
  drawWaveform();
  setPlayerLoading(true);
  renderTrackList();
  renderPlayer();
  // AutoCue busy state is per-track — refresh labels when switching.
  syncAutocueUi();
  const track = currentTrack();
  if (track && !isReviewMode()) requestRecommendation(track);
}

async function promoteTrack(destinationStage, { requireCued = null } = {}) {
  const track = currentTrack();
  if (!track) return;

  if (destinationStage === "ready_for_sort" && !track.is_cued) {
    setStatus("Cannot approve: track has no VDJ cue points yet.", "error");
    return;
  }

  const allowRunning = state.health?.virtualdj_running
    ? await showConfirmDialog({
        title: "VirtualDJ is still open",
        track: trackDisplayTitle(track),
        message:
          "This move may be overwritten when VirtualDJ quits. Close it before continuing whenever possible.",
        confirmLabel: "Move anyway",
        tone: "warning",
      })
    : false;
  if (state.health?.virtualdj_running && !allowRunning) {
    setStatus("Close VirtualDJ, then promote.", "error");
    return;
  }

  const labels = {
    ready_for_sort: "Ready for Sort",
    no_cues_found: "No Cues Found",
    low_quality_skip: "Low Quality Skip",
    ac_low_quality: "AC Low Quality",
  };
  setStatus(`Moving ${track.name} → ${labels[destinationStage] || destinationStage}…`);
  updateApproveButtons();
  ["approveBtn", "approveBtnSide"].forEach((id) => {
    const el = $(id);
    if (el) el.disabled = true;
  });

  try {
    const body = {
      path: track.path,
      destination_stage: destinationStage,
      allow_vdj_running: Boolean(allowRunning),
    };
    if (requireCued !== null) body.require_cued = requireCued;
    const data = await api("/api/promote", {
      method: "POST",
      body: JSON.stringify(body),
    });
    const r = data.result;
    setStatus(
      `Moved → ${r.dest_path}${r.database_updated ? " · VDJ cues retargeted" : ""}${
        r.stems_moved ? " · stems moved" : ""
      }`,
      "success"
    );
    await loadTracks();
  } catch (err) {
    setStatus(err.message, "error");
    updateApproveButtons();
  }
}

function skipToNextReviewTrack() {
  const indexes = filteredTrackIndexes();
  if (!indexes.length) return;
  const pos = indexes.indexOf(state.index);
  const next = indexes[pos + 1] ?? indexes[0];
  if (next !== state.index) selectTrack(next);
}

async function requestRecommendation(track) {
  if (state.recommendAbort) state.recommendAbort.abort();
  const controller = new AbortController();
  state.recommendAbort = controller;
  state.recommendation = null;
  renderRecommendation();

  try {
    const data = await api("/api/recommend", {
      method: "POST",
      body: JSON.stringify({
        path: track.path,
        preferred_library: state.library,
        force: false,
      }),
      signal: controller.signal,
    });
    if (currentTrack()?.path !== track.path) return;
    state.recommendation = data.recommendation;
    renderRecommendation();
    // Soft-highlight recommended folder if same library
    if (data.ok && data.recommendation?.library === state.library) {
      renderFolders();
    }
  } catch (err) {
    if (err.name === "AbortError") return;
    if (currentTrack()?.path !== track.path) return;
    state.recommendation = {
      error: err.message,
      library: state.library,
      relative_path: "",
      confidence: 0,
    };
    renderRecommendation();
  }
}

async function sortSelected() {
  const track = currentTrack();
  if (!track || !state.selectedPath) return;
  if (!track.is_cued) {
    setStatus("Cannot sort: track is not cued.", "error");
    return;
  }

  const allowRunning = state.health?.virtualdj_running
    ? await showConfirmDialog({
        title: "VirtualDJ is still open",
        track: trackDisplayTitle(track),
        message:
          "Sorting may be overwritten when VirtualDJ quits. Close it before continuing whenever possible.",
        confirmLabel: "Sort anyway",
        tone: "warning",
      })
    : false;

  if (state.health?.virtualdj_running && !allowRunning) {
    setStatus("Close VirtualDJ, then sort.", "error");
    return;
  }

  sortBtnBusy(true);
  setStatus(`Moving ${track.name} → ${pathModeLabel()} / ${state.selectedPath}…`);
  try {
    const data = await api("/api/sort", {
      method: "POST",
      body: JSON.stringify({
        path: track.path,
        library: state.library,
        relative_folder: state.selectedPath,
        allow_vdj_running: Boolean(allowRunning),
      }),
    });
    const r = data.result;
    const archiveBits = [];
    const libBits = (r.library_dests || [])
      .map((d) => `${d.library}`)
      .join("+");
    if (libBits) archiveBits.push(`libraries: ${libBits}`);
    if (r.cues_sorted_copied) archiveBits.push("copied to Cues Sorted");
    else if (r.cues_sorted_already_present) archiveBits.push("already in Cues Sorted");
    if (r.cues_sorted_db_cloned) archiveBits.push("Cues Sorted VDJ entry cloned");
    setStatus(
      `Sorted → ${r.dest_path}${r.database_updated ? " · VDJ cues retargeted" : " · no DB entry"}${
        r.stems_moved ? " · stems moved" : ""
      }${archiveBits.length ? " · " + archiveBits.join(" · ") : ""}`,
      "success"
    );
    state.selectedPath = "";
    await loadTracks();
    await loadFolders();
  } catch (err) {
    setStatus(err.message, "error");
  } finally {
    sortBtnBusy(false);
  }
}

function sortBtnBusy(busy) {
  $("sortBtn").disabled = busy || !currentTrack()?.is_cued || !state.selectedPath;
  $("sortBtn").textContent = busy ? "Sorting…" : "Sort into folder";
  const demoteBtn = $("demoteReadyBtn");
  if (demoteBtn) {
    demoteBtn.disabled = busy || !currentTrack() || isReviewMode();
    if (!busy) demoteBtn.textContent = "Back to Add Cues";
  }
  const removeBtn = $("removeReadyBtn");
  if (removeBtn) {
    removeBtn.disabled = busy || !currentTrack();
  }
}

async function removeFromReadyOnly() {
  const track = currentTrack();
  if (!track) return;
  if (isReviewMode()) {
    setStatus("Switch to Sort mode to remove from Ready for Sort.", "error");
    return;
  }
  const ok = await showConfirmDialog({
    title: "Remove from Ready?",
    track: trackDisplayTitle(track),
    message: "This track will not be placed into House, Zouk, or Cues Sorted.",
    note: "The file will be moved to Trash and remains recoverable.",
    confirmLabel: "Move to Trash",
    tone: "danger",
  });
  if (!ok) return;

  sortBtnBusy(true);
  setStatus(`Removing ${track.name} from Ready for Sort…`);
  try {
    const data = await api("/api/remove-ready", {
      method: "POST",
      body: JSON.stringify({ path: track.path, to_trash: true }),
    });
    setStatus(
      `Removed from Ready for Sort → Trash: ${data.result?.name || track.name}`,
      "success"
    );
    state.selectedPath = "";
    await loadTracks();
    if (!isReviewMode()) await loadFolders();
  } catch (err) {
    setStatus(err.message, "error");
  } finally {
    sortBtnBusy(false);
  }
}

async function demoteReadyToAddCues() {
  const track = currentTrack();
  if (!track) return;
  if (isReviewMode()) {
    setStatus("Switch to Sort mode to send tracks back to Add Cues.", "error");
    return;
  }

  const ok = await showConfirmDialog({
    title: "Send back to Add Cues?",
    track: trackDisplayTitle(track),
    message:
      "This track will leave Ready for Sort and return to Add Cues for re-review.",
    note:
      "File moves to Add Cues / Back from Ready. VirtualDJ cues and loops stay — only the FilePath is retargeted. Close VirtualDJ first if it is open.",
    confirmLabel: "Back to Add Cues",
    tone: "warning",
  });
  if (!ok) return;

  let allowRunning = false;
  if (state.health?.virtualdj_running) {
    allowRunning = await showConfirmDialog({
      title: "VirtualDJ is still open",
      track: trackDisplayTitle(track),
      message:
        "Path changes may be overwritten when VirtualDJ quits. Close it first when possible.",
      confirmLabel: "Continue anyway",
      tone: "warning",
    });
    if (!allowRunning) {
      setStatus("Close VirtualDJ, then send back to Add Cues.", "error");
      return;
    }
  }

  sortBtnBusy(true);
  const demoteBtn = $("demoteReadyBtn");
  if (demoteBtn) {
    demoteBtn.disabled = true;
    demoteBtn.textContent = "Sending…";
  }
  setStatus(`Sending ${track.name} back to Add Cues…`);
  try {
    const data = await api("/api/demote-ready", {
      method: "POST",
      body: JSON.stringify({
        path: track.path,
        allow_vdj_running: Boolean(allowRunning),
        subfolder: "Back from Ready",
      }),
    });
    const dest = data.result?.dest_path || "";
    const short = dest
      ? dest.split("/").slice(-3).join("/")
      : "Add Cues / Back from Ready";
    setStatus(`Back to Add Cues · ${short}`, "success");
    state.selectedPath = "";
    await loadTracks();
    if (!isReviewMode()) await loadFolders();
  } catch (err) {
    setStatus(err.message, "error");
  } finally {
    sortBtnBusy(false);
    if (demoteBtn) {
      demoteBtn.disabled = false;
      demoteBtn.textContent = "Back to Add Cues";
    }
  }
}

async function createFolder() {
  const name = $("newFolderName").value.trim();
  if (!name) {
    setStatus("Enter a folder name.", "error");
    return;
  }
  try {
    const data = await api("/api/folders", {
      method: "POST",
      body: JSON.stringify({
        library: state.library,
        name,
        parent_relative_path: state.selectedPath || "",
      }),
    });
    $("newFolderName").value = "";
    setStatus(`Created ${state.library}/${data.folder.relative_path}`, "success");
    // Expand parent and select new folder
    if (data.folder.parent_relative_path) {
      state.expanded.add(data.folder.parent_relative_path);
    }
    await loadFolders();
    selectFolder(data.folder.relative_path, { expand: true });
  } catch (err) {
    setStatus(err.message, "error");
  }
}

function bindUi() {
  // Cue/loop list tabs — bind early (must not depend on later listeners succeeding).
  const cueKindFilter = $("cueKindFilter");
  if (cueKindFilter) {
    cueKindFilter.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-cue-filter]");
      if (!btn || !cueKindFilter.contains(btn)) return;
      e.preventDefault();
      setCueListFilter(btn.getAttribute("data-cue-filter") || "all");
    });
    syncCueKindFilterUi();
  }

  const notesEl = $("vdjNotes");
  if (notesEl) {
    notesEl.addEventListener("input", () => scheduleNotesSave());
    notesEl.addEventListener("blur", () => {
      if (!state.notesDirty) return;
      if (state.notesSaveTimer) {
        clearTimeout(state.notesSaveTimer);
        state.notesSaveTimer = null;
      }
      const track = currentTrack();
      if (track && state.notesPath === track.path) {
        const gen = ++state.notesSaveGen;
        saveVdjNotes(track.path, notesEl.value, gen);
      }
    });
  }

  document.querySelectorAll("#accentPicker [data-accent-theme]").forEach((button) => {
    button.addEventListener("click", () => applyAccentTheme(button.dataset.accentTheme));
  });

  document.querySelectorAll("#modeSeg button").forEach((btn) => {
    btn.addEventListener("click", () => setMode(btn.dataset.mode));
  });

  document.querySelectorAll("#libraryPathSeg button[data-library]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      state.library = btn.dataset.library;
      document.querySelectorAll("#libraryPathSeg button[data-library]").forEach((b) =>
        b.classList.toggle("active", b === btn)
      );
      state.selectedPath = "";
      selectFolder("");
      updatePathHint();
      await loadFolders();
    });
  });

  document.querySelectorAll("#readinessFilter button").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.readinessFilter = btn.dataset.filter;
      document.querySelectorAll("#readinessFilter button").forEach((b) =>
        b.classList.toggle("active", b === btn)
      );
      const indexes = filteredTrackIndexes();
      if (indexes.length && !indexes.includes(state.index)) {
        state.index = indexes[0];
        renderPlayer();
      } else {
        // Filter alone changes Add cues vs Retry cues labels.
        syncAutocueUi();
      }
      renderTrackList();
      updateBatchAddCuesButton();
    });
  });

  $("batchAddCuesBtn")?.addEventListener("click", batchAddCuesForNotCued);

  $("folderFilter").addEventListener("input", (e) => {
    state.filter = e.target.value.trim();
    renderFolders();
  });

  $("trackSearch")?.addEventListener("input", (e) => {
    state.trackSearch = e.target.value || "";
    const indexes = filteredTrackIndexes();
    if (indexes.length && !indexes.includes(state.index)) {
      state.index = indexes[0];
      state.trackGen += 1;
      renderPlayer();
    }
    renderTrackList();
  });

  $("actionsLogBtn")?.addEventListener("click", async () => {
    const panel = $("actionsLogPanel");
    if (!panel) return;
    if (!panel.hidden) {
      panel.hidden = true;
      return;
    }
    panel.hidden = false;
    await loadActionsLogPanel();
  });
  $("actionsLogClose")?.addEventListener("click", () => {
    const panel = $("actionsLogPanel");
    if (panel) panel.hidden = true;
  });

  $("sortBtn").addEventListener("click", sortSelected);
  $("demoteReadyBtn")?.addEventListener("click", demoteReadyToAddCues);
  $("removeReadyBtn").addEventListener("click", removeFromReadyOnly);
  AUTO_CUE_SCOPE_BUTTONS.forEach((spec) => {
    const el = $(spec.id);
    if (!el) return;
    el.addEventListener("click", () => retryCuesForCurrentTrack(spec.scope));
  });
  $("createFolderBtn").addEventListener("click", createFolder);
  $("newFolderName").addEventListener("keydown", (e) => {
    if (e.key === "Enter") createFolder();
  });
  $("refreshBtn").addEventListener("click", async () => {
    await loadHealth();
    await loadTracks({ keepPath: currentTrack()?.path });
    if (!isReviewMode()) await loadFolders();
    setStatus("Refreshed.");
  });
  $("rerunRecBtn").addEventListener("click", () => {
    if (isReviewMode()) return;
    const t = currentTrack();
    if (!t) return;
    state.recommendation = null;
    renderRecommendation();
    api("/api/recommend", {
      method: "POST",
      body: JSON.stringify({
        path: t.path,
        preferred_library: state.library,
        force: true,
      }),
    })
      .then((data) => {
        if (currentTrack()?.path !== t.path) return;
        state.recommendation = data.recommendation;
        renderRecommendation();
        renderFolders();
      })
      .catch((err) => {
        state.recommendation = {
          error: err.message,
          library: state.library,
          relative_path: "",
          confidence: 0,
        };
        renderRecommendation();
      });
  });

  $("approveBtn").addEventListener("click", () =>
    promoteTrack("ready_for_sort", { requireCued: true })
  );
  $("approveBtnSide").addEventListener("click", () =>
    promoteTrack("ready_for_sort", { requireCued: true })
  );
  $("skipBtn").addEventListener("click", skipToNextReviewTrack);
  $("toNoCuesBtn").addEventListener("click", () =>
    promoteTrack("no_cues_found", { requireCued: false })
  );
  $("toLowSkipBtn").addEventListener("click", () =>
    promoteTrack("low_quality_skip", { requireCued: false })
  );
  $("toAcLowBtn").addEventListener("click", () =>
    promoteTrack("ac_low_quality", { requireCued: false })
  );

  const audio = $("audio");
  audio.addEventListener("timeupdate", () => {
    maybeLoopPlayback();
    updatePlayhead();
    updateTransportUi();
  });
  audio.addEventListener("loadedmetadata", () => {
    renderCues();
    updatePlayhead();
    drawWaveform();
    updateTransportUi();
  });
  audio.addEventListener("seeked", () => {
    updatePlayhead();
    updateTransportUi();
  });
  audio.addEventListener("durationchange", updateTransportUi);
  audio.addEventListener("pause", () => {
    stopLoopWatch();
    updateTransportUi();
  });
  audio.addEventListener("play", () => {
    if (state.loopPlaybackOn) startLoopWatch();
    updateTransportUi();
  });
  audio.addEventListener("ended", () => {
    // If looping a region near the end, wrap instead of stopping.
    if (state.loopPlaybackOn && state.activeLoopKey) {
      maybeLoopPlayback();
      if (!audio.paused) return;
    }
    stopLoopWatch();
    updateTransportUi();
  });
  audio.addEventListener("volumechange", () => {
    const volume = $("transportVolume");
    if (volume && Math.abs(Number(volume.value) - audio.volume) > 0.01) {
      volume.value = String(audio.volume);
    }
  });

  $("playPauseBtn")?.addEventListener("click", () => {
    if (!audio.src) return;
    if (audio.paused) audio.play().catch(() => {});
    else audio.pause();
  });
  $("previousTrackBtn")?.addEventListener("click", () => stepTrack(-1));
  $("nextTrackBtn")?.addEventListener("click", () => stepTrack(1));
  $("transportProgress")?.addEventListener("input", (e) => {
    const nextTime = Number(e.target.value);
    if (!Number.isFinite(nextTime)) return;
    audio.currentTime = nextTime;
    updateTransportUi();
  });
  $("transportVolume")?.addEventListener("input", (e) => {
    audio.volume = Math.min(1, Math.max(0, Number(e.target.value) || 0));
  });

  $("cueTimeline").addEventListener("click", (e) => {
    if (e.target.classList.contains("cue-marker")) return;
    const track = currentTrack();
    if (!track) return;
    const rect = $("cueTimeline").getBoundingClientRect();
    const x = e.clientX - rect.left;
    const usable = rect.width - 20;
    if (usable <= 0) return;
    const ratio = Math.min(1, Math.max(0, (x - 10) / usable));
    const duration = waveformDuration(track, audio) || trackDuration(track, audio);
    if (!duration) return;
    jumpToCue(ratio * duration);
  });

  $("waveformWrap").addEventListener("click", seekFromWaveformEvent);
  // passive:false so we can prevent page scroll while zooming the wave
  $("waveformWrap").addEventListener("wheel", onWaveformWheel, { passive: false });
  $("waveformWrap").addEventListener("dblclick", () => {
    resetWaveZoom();
    drawWaveform();
  });
  window.addEventListener("resize", () => drawWaveform());

  $("zoukSpeedBtn").addEventListener("click", enableZoukSpeed);
  $("normalSpeedBtn").addEventListener("click", enableNormalSpeed);
  $("halfBpmBtn")?.addEventListener("click", toggleHalfBpm);
  $("loopPlayBtn")?.addEventListener("click", toggleLoopPlayback);
  $("targetBpmInput").addEventListener("change", () => {
    state.targetBpm = Number($("targetBpmInput").value) || 75;
    if (state.zoukSpeedOn || state.playbackRate < 0.98) enableZoukSpeed();
  });
  $("speedSlider").addEventListener("input", (e) => {
    state.zoukSpeedOn = false;
    applyPlaybackRate(e.target.value);
  });
  // Some browsers reset playbackRate after play() — reassert.
  $("audio").addEventListener("play", () => {
    if ($("audio").playbackRate !== state.playbackRate) {
      $("audio").playbackRate = state.playbackRate;
    }
    updateTransportUi();
  });

  document.addEventListener("keydown", (e) => {
    if (e.target.matches("input, textarea")) return;
    if (e.code === "Space") {
      e.preventDefault();
      if (audio.paused) audio.play();
      else audio.pause();
    } else if (e.key === "j" || e.key === "ArrowDown") {
      e.preventDefault();
      stepTrack(1);
    } else if (e.key === "k" || e.key === "ArrowUp") {
      e.preventDefault();
      stepTrack(-1);
    } else if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      if (isReviewMode()) promoteTrack("ready_for_sort", { requireCued: true });
      else sortSelected();
    } else if (isReviewMode() && (e.key === "a" || e.key === "A")) {
      e.preventDefault();
      promoteTrack("ready_for_sort", { requireCued: true });
    } else if (isReviewMode() && (e.key === "s" || e.key === "S")) {
      e.preventDefault();
      skipToNextReviewTrack();
    } else if (e.key === "z" || e.key === "Z") {
      e.preventDefault();
      enableZoukSpeed();
    } else if (e.key === "n" || e.key === "N") {
      e.preventDefault();
      enableNormalSpeed();
    } else if (e.key === "h" || e.key === "H") {
      e.preventDefault();
      toggleHalfBpm();
    } else if (e.key === "l" || e.key === "L") {
      e.preventDefault();
      toggleLoopPlayback();
    } else if (/^[1-9]$/.test(e.key)) {
      const points = filteredCuePoints(currentTrack()?.cues?.points || []);
      const point = points[Number(e.key) - 1];
      if (point) {
        e.preventDefault();
        jumpToCue(point.pos, point);
      }
    }
  });
}

async function boot() {
  applyAccentTheme(storedAccentTheme(), { persist: false });
  bindUi();
  const params = new URLSearchParams(window.location.search);
  if (params.get("mode") === "add_cues" || params.get("add") === "1") {
    state.mode = "add_cues";
  }
  applyModeUi();
  try {
    await loadHealth();
    await loadTracks();
    if (!isReviewMode()) {
      await loadFolders();
      selectFolder("");
    }
    requestAnimationFrame(resetWorkspaceScroll);
    setStatus("Ready. Use Sort or Add Cues modes · Space / J/K / 1–9 cues");
  } catch (err) {
    setStatus(err.message, "error");
  }
}

boot();
