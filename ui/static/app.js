const state = {
  mode: "add_cues", // sort | add_cues | practice
  accentTheme: "lime",
  tracks: [],
  index: 0,
  practiceMixes: [],
  practiceDetail: null,
  practiceDb: null,
  practiceMixPath: "",
  practiceTxSort: "order", // order | score | save
  practiceView: "mix", // mix | best
  practiceBestItems: [],
  practiceBestLoading: false,
  practiceAnalyzeJob: null,
  practiceAnalyzeTimer: null,
  practiceSummary: null,
  library: "Both", // House | Zouk | Both (tree filter)
  folders: [],
  folderTrees: null,
  // Multi-select destinations: { library, path, key }
  selectedDests: [],
  // Last clicked folder (parent for "create folder")
  selectedPath: "",
  selectedPathLibrary: "",
  expanded: new Set(),
  filter: "",
  trackSearch: "",
  readinessFilter: "all",
  crateFilter: "all",
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
  waveViewBeforeAlign: null, // { zoom, offset } restored if align is cancelled
  waveViewPinned: false, // user/align window — don't page to the playhead
  waveCueChromeHits: null, // { left, right, overview } click targets while zoomed
  showBeatOnes: true, // bar “1” markers on waveform (toggleable)
  // Beatgrid drag-align mode (optional panel)
  gridAlignMode: false,
  gridAlignAnchor: null, // working downbeat while aligning (seconds)
  gridAlignOriginal: null, // anchor when mode opened
  gridAlignDragging: false,
  gridAlignDragOriginTime: 0,
  gridAlignDragOriginAnchor: 0,
  gridAlignPlan: null, // last auto-align proposal {action, reason, halve, ...}
  // Marker drag on waveform: { kind, point, originPos, previewPos, pointerId, moved }
  loopDrag: null,
  placeCueMode: false,
  placeCuePreview: null, // seconds while hovering in place mode
  placeCueInFlight: false,
  placeLoopMode: false,
  placeLoopPreview: null,
  placeLoopInFlight: false,
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
  /** path -> true when user confirmed VDJ grid after weak-onset block */
  gridManualConfirmed: {},
  trackGen: 0, // bumped on each selection to ignore stale async results
  tracksLoadGen: 0, // bumped on each list load / mode switch to drop stale /api/tracks responses
  quietSession: false, // ?quiet=1 / ?mute=1 / webdriver — never start audible playback
  allowAutoplay: false, // only continue playback after the user hits Play
  waveformDebounce: null,
  lastDrawMs: 0,
  playheadRaf: null,
  waveSeekTime: null,
  trackMeta: null, // { bitrate_kbps, codec, sample_rate, ... } for current path
  metaAbort: null,
  sortInFlight: false,
  promoteInFlight: false,
  notesWarnedVdj: false,
  batchPollInFlight: false,
  gridFixPollTimer: null,
  gridFixPollInFlight: false,
  autocueJobChip: null, // { message, kind }
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

/** Palette choices for the cue/loop color dropdown (matches AutoCue VDJ ints). */
const CUE_COLOR_OPTIONS = [
  { id: "blue", label: "Blue" },
  { id: "green", label: "Green" },
  { id: "purple", label: "Purple" },
  { id: "yellow", label: "Yellow" },
  { id: "orange", label: "Orange" },
];

function sanitizeColorName(name) {
  const id = String(name || "unknown").toLowerCase().trim();
  if (CUE_COLORS[id]) return id;
  return "unknown";
}

function stillOnTrack(path, gen) {
  return Boolean(path) && currentTrack()?.path === path && state.trackGen === gen;
}

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

function isRecsMode() {
  return state.mode === "recs";
}

function isAssembleMode() {
  return state.mode === "assemble";
}

function isPracticeMode() {
  return state.mode === "practice";
}

function wantsQuietSession() {
  try {
    const params = new URLSearchParams(window.location.search);
    if (params.has("quiet") || params.has("mute")) {
      const flag = params.get("quiet") || params.get("mute");
      if (!flag || flag === "1" || flag === "true" || flag === "yes") return true;
    }
  } catch {
    /* ignore */
  }
  try {
    if (navigator.webdriver) return true;
  } catch {
    /* ignore */
  }
  return false;
}

function shouldAutoplayOnSelect() {
  if (state.quietSession) return false;
  if (isPracticeMode()) return false;
  return Boolean(state.allowAutoplay);
}

function playAudio(audio) {
  if (!audio) return Promise.resolve();
  if (state.quietSession) {
    try {
      audio.pause();
    } catch {
      /* ignore */
    }
    audio.muted = true;
    setStatus("Sound off — click Sound off in the top bar to hear playback");
    return Promise.resolve();
  }
  if (!audio.src) return Promise.resolve();
  const p = audio.play();
  const markStarted = () => {
    state.allowAutoplay = true;
  };
  if (p && typeof p.then === "function") {
    return p.then(markStarted);
  }
  markStarted();
  return Promise.resolve();
}

function syncQuietSessionUi() {
  const chip = $("quietSessionChip");
  if (chip) chip.hidden = !state.quietSession;
  document.body.classList.toggle("quiet-session", Boolean(state.quietSession));
}

function installQuietPlayGuard(audio) {
  if (!audio || audio.dataset.quietGuard === "1") return;
  audio.dataset.quietGuard = "1";
  const nativePlay = audio.play.bind(audio);
  audio.play = function quietGuardedPlay() {
    if (state.quietSession) {
      try {
        audio.pause();
      } catch {
        /* ignore */
      }
      audio.muted = true;
      return Promise.resolve();
    }
    return nativePlay();
  };
}

function applyQuietSession() {
  state.quietSession = wantsQuietSession();
  const audio = $("audio");
  if (audio) {
    installQuietPlayGuard(audio);
    audio.muted = Boolean(state.quietSession);
    if (state.quietSession) {
      try {
        audio.pause();
      } catch {
        /* ignore */
      }
    }
  }
  if (state.quietSession) state.allowAutoplay = false;
  syncQuietSessionUi();
}

function disableQuietSession() {
  state.quietSession = false;
  const audio = $("audio");
  if (audio) audio.muted = false;
  syncQuietSessionUi();
  try {
    const url = new URL(window.location.href);
    url.searchParams.delete("quiet");
    url.searchParams.delete("mute");
    window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
  } catch {
    /* ignore */
  }
}

function formatClock(sec) {
  const s = Math.max(0, Number(sec) || 0);
  const m = Math.floor(s / 60);
  const r = Math.floor(s % 60);
  return `${m}:${String(r).padStart(2, "0")}`;
}

function setPracticeWaveStatus(text, kind = "") {
  const el = $("practiceWaveformStatus");
  if (!el) return;
  el.textContent = text || "";
  const empty = !text;
  el.className = `waveform-status${kind ? ` ${kind}` : ""}${empty ? " hidden is-empty" : ""}`;
  el.hidden = empty;
}

function practiceTransitions() {
  return state.practiceDetail?.transitions || [];
}

function practiceDuration(track, audio) {
  // Prefer mix metadata (available before <audio> finishes loading).
  const fromDetail = Number(state.practiceDetail?.duration_sec) || 0;
  if (fromDetail > 0) return fromDetail;
  const fromMix = Number(track?.duration) || 0;
  if (fromMix > 0) return fromMix;
  const fromWave = Number(state.waveform?.duration) || 0;
  if (fromWave > 0) return fromWave;
  const fromAudio = Number(audio?.duration);
  if (Number.isFinite(fromAudio) && fromAudio > 0) return fromAudio;
  return trackDuration(track, audio) || 0;
}

/** Clamp helper for practice map layout math. */
function practiceClamp(n, lo, hi) {
  return Math.max(lo, Math.min(hi, n));
}

/**
 * Equal-width song slots for the practice transition map.
 * Slot i covers tracks[i].pos_sec → next (or duration); first slot starts at 0.
 * Returns null when tracks are missing (caller falls back to pure time mapping).
 */
function practiceSongSlots(duration) {
  const raw = state.practiceDetail?.tracks || [];
  if (!raw.length || !(duration > 0)) return null;
  const tracks = raw
    .slice()
    .sort((a, b) => (Number(a.pos_sec) || 0) - (Number(b.pos_sec) || 0));
  const n = tracks.length;
  return tracks.map((t, i) => {
    let t0 = Number(t.pos_sec) || 0;
    let t1 = i + 1 < n ? Number(tracks[i + 1].pos_sec) || duration : duration;
    if (i === 0) t0 = 0;
    if (i === n - 1) t1 = duration;
    if (!(t1 > t0)) t1 = t0 + 1e-3;
    const name = String(t.name || "").trim();
    return {
      index: i,
      name,
      label: name ? name.slice(0, 18) : String(i + 1),
      t0,
      t1,
    };
  });
}

/** Viewport + scrollable content width; px/song clamped so few songs fill, many scroll. */
function practiceMapLayout(wrap, duration) {
  const viewportW = Math.max(
    1,
    wrap?.clientWidth || wrap?.parentElement?.clientWidth || 600
  );
  const slots = practiceSongSlots(duration);
  const trackCount = slots?.length || 0;
  let contentWidth = viewportW;
  let pxPerSong = 0;
  if (trackCount > 0) {
    pxPerSong = practiceClamp(viewportW / trackCount, 120, 220);
    contentWidth = Math.max(viewportW, Math.round(trackCount * pxPerSong));
  }
  return { viewportW, contentWidth, slots, padX: 10, pxPerSong, trackCount };
}

function practiceTimeToX(t, slots, contentW, duration, padX = 10) {
  const plotW = Math.max(1, contentW - padX * 2);
  const time = Number(t) || 0;
  if (!slots?.length || !(duration > 0)) {
    return padX + practiceClamp(time / Math.max(duration, 1e-6), 0, 1) * plotW;
  }
  const n = slots.length;
  const slotW = plotW / n;
  if (time <= slots[0].t0) return padX;
  for (let i = 0; i < n; i++) {
    const s = slots[i];
    const span = Math.max(1e-6, s.t1 - s.t0);
    if (time < s.t1 || i === n - 1) {
      const frac = practiceClamp((time - s.t0) / span, 0, 1);
      return padX + i * slotW + frac * slotW;
    }
  }
  return padX + plotW;
}

function practiceXToTime(x, slots, contentW, duration, padX = 10) {
  const plotW = Math.max(1, contentW - padX * 2);
  const local = practiceClamp(x - padX, 0, plotW);
  if (!slots?.length || !(duration > 0)) {
    return (local / plotW) * duration;
  }
  const n = slots.length;
  const slotW = plotW / n;
  const i = Math.min(n - 1, Math.max(0, Math.floor(local / Math.max(slotW, 1e-6))));
  const frac = practiceClamp((local - i * slotW) / Math.max(slotW, 1e-6), 0, 1);
  const s = slots[i];
  return s.t0 + frac * (s.t1 - s.t0);
}

let _practiceWaveScrollMix = null;

function ensurePracticePlayheadVisible(wrap, px) {
  if (!wrap) return;
  const viewW = wrap.clientWidth || 0;
  if (viewW <= 0) return;
  const sl = wrap.scrollLeft || 0;
  const margin = 48;
  if (px >= sl + margin && px <= sl + viewW - margin) return;
  const target = Math.max(0, px - viewW * 0.35);
  if (Math.abs(target - sl) < 8) return;
  wrap.scrollLeft = target;
}

function updatePracticeWaveVisibility() {
  const panel = $("practiceWavePanel");
  if (!panel) return;
  if (!isPracticeMode()) {
    panel.hidden = true;
    panel.classList.remove("is-empty");
    return;
  }
  // Keep map visible whenever a practice mix is selected (even with 0 transitions).
  const hasMix = Boolean(state.practiceMixPath || state.practiceDetail);
  const txs = typeof practiceTransitions === "function" ? practiceTransitions() : [];
  panel.classList.toggle("is-empty", !txs.length);
  panel.hidden = !hasMix;
}

function schedulePracticeWaveRedraw() {
  if (!isPracticeMode()) return;
  // Layout must settle so wrap.clientWidth reflects full main column.
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      try {
        drawPracticeWaveform();
      } catch {
        /* ignore */
      }
    });
  });
  // Second pass after fonts/scrollbars
  setTimeout(() => {
    if (isPracticeMode()) {
      try {
        drawPracticeWaveform();
      } catch {
        /* ignore */
      }
    }
  }, 120);
}

/** Full-mix waveform with numbered transition markers (practice mode only). */
function drawPracticeWaveform() {
  const canvas = $("practiceWaveformCanvas");
  const wrap = $("practiceWaveformWrap");
  if (!canvas || !wrap || !isPracticeMode()) return;

  const track = currentTrack();
  const audio = $("audio");
  const duration = practiceDuration(track, audio);
  const layout = practiceMapLayout(wrap, duration);
  const { contentWidth, slots, padX } = layout;
  const dpr = window.devicePixelRatio || 1;
  const cssW = contentWidth;
  const cssH = wrap.clientHeight || 110;

  const mixKey = state.practiceMixPath || "";
  if (_practiceWaveScrollMix !== mixKey) {
    _practiceWaveScrollMix = mixKey;
    wrap.scrollLeft = 0;
  }

  if (
    canvas.width !== Math.floor(cssW * dpr) ||
    canvas.height !== Math.floor(cssH * dpr)
  ) {
    canvas.width = Math.floor(cssW * dpr);
    canvas.height = Math.floor(cssH * dpr);
  }
  canvas.style.width = `${cssW}px`;
  canvas.style.height = `${cssH}px`;

  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const w = cssW;
  const h = cssH;
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = "#0a0e16";
  ctx.fillRect(0, 0, w, h);

  const peaks = state.waveform?.peaks;
  const plotW = Math.max(1, w - padX * 2);
  const mid = h / 2;

  // Song slot backgrounds / dividers / labels
  if (slots?.length) {
    const slotW = plotW / slots.length;
    slots.forEach((s, i) => {
      const x0 = padX + i * slotW;
      if (i % 2 === 1) {
        ctx.fillStyle = "rgba(255,255,255,0.025)";
        ctx.fillRect(x0, 0, slotW, h);
      }
      ctx.strokeStyle = "rgba(255,255,255,0.10)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x0, 0);
      ctx.lineTo(x0, h);
      ctx.stroke();
      ctx.fillStyle = "rgba(255,255,255,0.38)";
      ctx.font = "600 10px ui-sans-serif, system-ui, sans-serif";
      ctx.textAlign = "left";
      ctx.textBaseline = "bottom";
      const label = s.label || String(i + 1);
      ctx.fillText(label, x0 + 4, h - 5, Math.max(24, slotW - 8));
    });
    // Closing edge
    ctx.strokeStyle = "rgba(255,255,255,0.10)";
    ctx.beginPath();
    ctx.moveTo(padX + plotW, 0);
    ctx.lineTo(padX + plotW, h);
    ctx.stroke();
  }

  // Center line
  ctx.strokeStyle = "rgba(42,51,68,0.9)";
  ctx.beginPath();
  ctx.moveTo(padX, mid);
  ctx.lineTo(w - padX, mid);
  ctx.stroke();

  // Normalize peaks so quiet-but-audible files still show shape;
  // truly silent files stay flat and get a banner.
  let drawPeaks = peaks;
  let peakMax = 0;
  if (peaks?.length) {
    for (const p of peaks) if (p > peakMax) peakMax = p;
  }
  const silent = peakMax > 0 && peakMax < 0.02;
  const banner = $("practiceSilentBanner");
  if (banner) banner.hidden = !silent;
  if (peaks?.length && peakMax > 0 && peakMax < 0.35) {
    // boost quiet mixes for display only
    const scale = 0.85 / peakMax;
    drawPeaks = peaks.map((p) => Math.min(1, p * scale));
  }

  if (drawPeaks?.length && duration > 0) {
    ctx.beginPath();
    const n = drawPeaks.length;
    for (let i = 0; i < n; i++) {
      const t = (i / Math.max(1, n - 1)) * duration;
      const x = practiceTimeToX(t, slots, w, duration, padX);
      const amp = Math.min(1, drawPeaks[i]) * (h * 0.4);
      if (i === 0) ctx.moveTo(x, mid - amp);
      else ctx.lineTo(x, mid - amp);
    }
    for (let i = n - 1; i >= 0; i--) {
      const t = (i / Math.max(1, n - 1)) * duration;
      const x = practiceTimeToX(t, slots, w, duration, padX);
      const amp = Math.min(1, drawPeaks[i]) * (h * 0.4);
      ctx.lineTo(x, mid + amp);
    }
    ctx.closePath();
    const grad = ctx.createLinearGradient(0, 0, 0, h);
    grad.addColorStop(0, accentRgba(0.55));
    grad.addColorStop(0.5, accentRgba(0.22));
    grad.addColorStop(1, accentRgba(0.5));
    ctx.fillStyle = grad;
    ctx.fill();
  }

  const txs = practiceTransitions();
  const countEl = $("practiceWaveTxCount");
  if (countEl) {
    countEl.textContent = `${txs.length} transition${txs.length === 1 ? "" : "s"}`;
  }

  // Transition markers (same song-slot coordinate system)
  if (duration > 0 && txs.length) {
    txs.forEach((tx, i) => {
      const t = Number(tx.at_sec) || 0;
      const x = practiceTimeToX(t, slots, w, duration, padX);
      const overall = tx.score?.overall;
      let color = accentRgba(0.95);
      if (overall != null) {
        if (Number(overall) >= 7.5) color = "rgba(34, 197, 94, 0.95)";
        else if (Number(overall) < 5.5) color = "rgba(249, 115, 22, 0.95)";
        else color = "rgba(234, 179, 8, 0.95)";
      }
      // Vertical line
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(x, 8);
      ctx.lineTo(x, h - 8);
      ctx.stroke();
      // Top disc + number
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.arc(x, 12, 9, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = "#0a0e16";
      ctx.font = "bold 10px ui-sans-serif, system-ui, sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(String(i + 1), x, 12);
    });
  }

  // Playhead
  let playheadX = null;
  if (duration > 0 && audio && Number.isFinite(audio.currentTime)) {
    playheadX = practiceTimeToX(audio.currentTime, slots, w, duration, padX);
    ctx.strokeStyle = "rgba(255,255,255,0.9)";
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(playheadX, 0);
    ctx.lineTo(playheadX, h);
    ctx.stroke();
    if (!audio.paused) {
      ensurePracticePlayheadVisible(wrap, playheadX);
    }
  }

  // Legend chips under wave
  renderPracticeWaveLegend(txs);
  updatePracticeWaveVisibility();
}

function renderPracticeWaveLegend(txs) {
  const el = $("practiceWaveLegend");
  if (!el) return;
  if (!txs?.length) {
    el.innerHTML = `<span class="subtitle">No transition cues on this mix yet — click the map to scrub & play.</span>`;
    return;
  }
  el.innerHTML = txs
    .map((tx, i) => {
      const score =
        tx.score?.overall != null
          ? `<span class="pw-score">${Number(tx.score.overall).toFixed(1)}</span>`
          : "";
      const save = tx.score?.save_for_set
        ? `<span class="badge ok">save</span>`
        : "";
      return `<button type="button" class="practice-wave-chip" data-at="${tx.at_sec}" data-index="${tx.index}" title="${escapeHtml(
        `${tx.from_track} → ${tx.to_track}`
      )}">
        <span class="pw-num">${i + 1}</span>
        <span class="pw-time">${formatClock(tx.at_sec)}</span>
        <span class="pw-to">${escapeHtml((tx.to_track || "").slice(0, 28))}</span>
        ${score}
        ${save}
      </button>`;
    })
    .join("");
  el.querySelectorAll(".practice-wave-chip").forEach((btn) => {
    btn.addEventListener("click", () => {
      seekPracticeTransition(Number(btn.dataset.at) || 0, {
        index: btn.dataset.index,
      });
    });
  });
}

/**
 * Scroll/highlight the matching transition card in the Practice Lab list.
 */
function focusPracticeTransitionCard(atSec, index) {
  const list = $("practiceTransitionList");
  if (!list) return null;
  let card = null;
  if (index != null && String(index) !== "") {
    const idx = String(index);
    card = [...list.querySelectorAll(".practice-tx[data-index]")].find(
      (el) => String(el.dataset.index) === idx
    ) || null;
  }
  if (!card && atSec != null && Number.isFinite(Number(atSec))) {
    const target = Number(atSec);
    let best = null;
    let bestD = Infinity;
    list.querySelectorAll(".practice-tx[data-at]").forEach((el) => {
      const d = Math.abs(Number(el.dataset.at) - target);
      if (d < bestD) {
        bestD = d;
        best = el;
      }
    });
    // Only accept a near match (same transition, not a random card).
    if (best && bestD <= 0.75) card = best;
  }
  list.querySelectorAll(".practice-tx.is-focused").forEach((el) => {
    el.classList.remove("is-focused");
  });
  if (!card) return null;
  card.classList.add("is-focused");
  // Scroll the analysis pane so the description is in view.
  try {
    card.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "nearest" });
  } catch {
    card.scrollIntoView(true);
  }
  const panel = $("practicePanel");
  if (panel && typeof panel.scrollTop === "number") {
    // If scrollIntoView didn't move a nested scroller enough, nudge panel.
    const panelRect = panel.getBoundingClientRect();
    const cardRect = card.getBoundingClientRect();
    if (cardRect.top < panelRect.top + 8 || cardRect.bottom > panelRect.bottom - 8) {
      const delta = cardRect.top - panelRect.top - panel.clientHeight * 0.12;
      panel.scrollBy({ top: delta, behavior: "smooth" });
    }
  }
  return card;
}

function seekPracticeTransition(atSec, { preRoll = 20, play = true, index = null } = {}) {
  const audio = $("audio");
  if (!audio) {
    setStatus("No audio element — refresh the page.", "error");
    return;
  }
  if (!audio.src && !audio.dataset.path) {
    setStatus("No mix loaded — click a practice mix first.", "error");
    return;
  }
  // Jump the bottom description list to this transition as well.
  focusPracticeTransitionCard(atSec, index);
  const target = Math.max(0, Number(atSec) - preRoll);
  const apply = () => {
    try {
      audio.currentTime = target;
    } catch {
      /* ignore until metadata ready */
    }
    if (play) {
      const p = playAudio(audio);
      if (p && typeof p.catch === "function") {
        p.catch((err) => {
          setStatus(
            `Playback blocked: ${err?.message || "click Play once, then try again"}`,
            "error"
          );
        });
      }
    }
    drawPracticeWaveform();
    // Re-focus after list may have re-rendered with waveform redraw side effects.
    focusPracticeTransitionCard(atSec, index);
    const silent = isPracticeMixNearlySilent();
    setStatus(
      silent
        ? `Seek ${formatClock(atSec)} (−${preRoll}s) — recording is nearly silent`
        : `Playing transition at ${formatClock(atSec)} (−${preRoll}s)`
    );
  };
  // Wait for media if needed (common right after switching mixes)
  if (audio.readyState >= 1) {
    apply();
  } else {
    let done = false;
    const once = () => {
      if (done) return;
      done = true;
      audio.removeEventListener("loadedmetadata", once);
      audio.removeEventListener("canplay", once);
      apply();
    };
    audio.addEventListener("loadedmetadata", once);
    audio.addEventListener("canplay", once);
    setTimeout(once, 500);
  }
}

function isPracticeMixNearlySilent() {
  const peaks = state.waveform?.peaks;
  if (!peaks?.length) return false;
  let mx = 0;
  for (const p of peaks) if (p > mx) mx = p;
  return mx < 0.02;
}

function handlePracticeWaveClick(e) {
  if (!isPracticeMode()) return;
  e.preventDefault();
  e.stopPropagation();
  const wrap = $("practiceWaveformWrap");
  const track = currentTrack();
  const audio = $("audio");
  if (!wrap || !audio) return;
  const duration = practiceDuration(track, audio);
  if (!duration || duration <= 0) {
    setStatus("Wave not ready yet — wait a moment and click again.", "error");
    return;
  }
  const layout = practiceMapLayout(wrap, duration);
  const { contentWidth, slots, padX } = layout;
  const rect = wrap.getBoundingClientRect();
  const x = e.clientX - rect.left + (wrap.scrollLeft || 0);
  const t = practiceXToTime(x, slots, contentWidth, duration, padX);
  const txs = practiceTransitions();

  // Snap in pixel space so equal song-slots stay intuitive
  const snapPx = Math.max(28, contentWidth * 0.02);
  let nearest = null;
  let best = Infinity;
  for (const tx of txs) {
    const txX = practiceTimeToX(Number(tx.at_sec) || 0, slots, contentWidth, duration, padX);
    const d = Math.abs(txX - x);
    if (d < best) {
      best = d;
      nearest = tx;
    }
  }
  if (nearest && best <= snapPx) {
    seekPracticeTransition(Number(nearest.at_sec) || 0, {
      index: nearest.index,
    });
    return;
  }

  // Free scrub — always start playback
  const apply = () => {
    try {
      audio.currentTime = t;
    } catch {
      /* ignore */
    }
    const p = playAudio(audio);
    if (p && typeof p.catch === "function") {
      p.catch((err) => {
        setStatus(
          `Playback blocked: ${err?.message || "click Play once, then try again"}`,
          "error"
        );
      });
    }
    drawPracticeWaveform();
    ensurePracticePlayheadVisible(
      wrap,
      practiceTimeToX(t, slots, contentWidth, duration, padX)
    );
    setStatus(`Playing ${formatClock(t)}`);
  };
  if (audio.readyState >= 1) apply();
  else {
    audio.addEventListener("loadedmetadata", apply, { once: true });
  }
}

function bindPracticeWaveInteractions() {
  const wrap = $("practiceWaveformWrap");
  const canvas = $("practiceWaveformCanvas");
  if (!wrap || wrap.dataset.bound === "1") return;
  wrap.dataset.bound = "1";
  wrap.addEventListener("click", handlePracticeWaveClick);
  // Explicit canvas bind (status overlay uses pointer-events:none / hidden)
  if (canvas && canvas.dataset.bound !== "1") {
    canvas.dataset.bound = "1";
    canvas.addEventListener("click", handlePracticeWaveClick);
  }
  window.addEventListener("resize", () => {
    if (isPracticeMode()) drawPracticeWaveform();
  });
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

  const indexes = filteredTrackIndexes();
  const position = indexes.indexOf(state.index);
  if (previous) previous.disabled = position <= 0;
  if (next) next.disabled = position < 0 || position >= indexes.length - 1;

  // Keep practice transition map playhead in sync.
  if (isPracticeMode()) drawPracticeWaveform();
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
  state.waveViewPinned = false;
  const t = Math.max(0, Number(pos) || 0);
  const seek = () => {
    try {
      audio.currentTime = t;
    } catch {
      /* ignore seek race before metadata */
    }
    playAudio(audio).catch(() => {});
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

function startPlayheadWatch() {
  if (state.playheadRaf) return;
  const tick = () => {
    const audio = $("audio");
    if (!audio || audio.paused || audio.ended) {
      state.playheadRaf = null;
      updatePlayhead();
      return;
    }
    updatePlayhead();
    state.playheadRaf = requestAnimationFrame(tick);
  };
  state.playheadRaf = requestAnimationFrame(tick);
}

function stopPlayheadWatch() {
  if (!state.playheadRaf) return;
  cancelAnimationFrame(state.playheadRaf);
  state.playheadRaf = null;
}

function updatePlayhead() {
  const audio = $("audio");
  const playhead = $("cuePlayhead");
  const track = currentTrack();
  if (!audio || !track) return;
  const duration = trackDuration(track, audio);
  if (playhead) {
    if (!duration) {
      playhead.style.left = "0%";
    } else {
      const pct = Math.min(100, Math.max(0, (audio.currentTime / duration) * 100));
      playhead.style.left = `calc(10px + (100% - 20px) * ${pct / 100})`;
    }
  }
  const playing = !audio.paused && !audio.ended;
  if (playing) startPlayheadWatch();
  else stopPlayheadWatch();
  const now = performance.now();
  if (playing) {
    if (now - (state.lastDrawMs || 0) > 16) {
      state.lastDrawMs = now;
      syncMovingPlayhead();
    }
  } else if (now - (state.lastDrawMs || 0) > 80) {
    state.lastDrawMs = now;
    drawWaveform();
  }
}

/** Move the overlay needle; full redraw only when the view pages. */
function syncMovingPlayhead() {
  const audio = $("audio");
  const track = currentTrack();
  if (!audio || !track) return;
  const duration = waveformDuration(track, audio) || trackDuration(track, audio);
  if (!duration || !Number.isFinite(audio.currentTime)) return;
  const prevOffset = state.waveOffset;
  const view = applyPlayheadFollow(duration, audio.currentTime);
  if (view.start !== prevOffset) {
    drawWaveform();
    return;
  }
  const wrap = $("waveformWrap");
  const cssW = wrap?.clientWidth || 600;
  const { padX, plotW } = wavePlotMetrics(cssW);
  positionWavePlayhead(null, audio, view, padX, plotW, 0);
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
  // Primary AutoCue controls live only in the Cue review side panel.
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

function isTrackCueing(track) {
  if (!track?.path) return false;
  if (isAutocueJobActive(retryJobForPath(track.path))) return true;
  return (state.batchCueingPaths || []).includes(track.path);
}

function cueingTrackIndexes() {
  return state.tracks
    .map((t, i) => i)
    .filter((i) => isTrackCueing(state.tracks[i]));
}

function cueingListSignature() {
  const jobs = activeRetryJobs()
    .map((j) => `${j.path}:${j.status}`)
    .sort();
  const batch = (state.batchCueingPaths || []).slice().sort();
  return `${jobs.join("|")}::${batch.join("|")}`;
}

function updateCueingFilterUi() {
  const btn = $("crateFilterCueing");
  if (!btn) return;
  const unique = new Set([
    ...activeRetryJobs().map((j) => j.path),
    ...(state.batchCueingPaths || []),
  ]);
  const count = unique.size;
  btn.textContent = count ? `Cueing · ${count}` : "Cueing";
  btn.classList.toggle("is-live", count > 0);
  btn.title = count
    ? `${count} track${count === 1 ? "" : "s"} AutoCueing now`
    : "Tracks AutoCue is working on";
}

/** True when the current track already has an AutoCue job in flight. */
function isAutocueBusyForCurrentTrack() {
  if (state.batchPollTimer) return true;
  const current = currentTrack()?.path;
  return isAutocueJobActive(retryJobForPath(current));
}

function startRetryPoll(pathKey, jobId) {
  const entry = state.retryJobs[pathKey];
  if (!entry || !jobId) return;
  if (entry.pollTimer) return;
  entry.id = jobId;
  entry.pollTimer = setInterval(async () => {
    const liveGate = state.retryJobs[pathKey];
    if (!liveGate || liveGate.id !== jobId) return;
    if (liveGate._pollInFlight) return;
    liveGate._pollInFlight = true;
    try {
      const res = await api(`/api/retry-cues/${jobId}`);
      const j = res.job;
      const live = state.retryJobs[pathKey];
      if (!live || live.id !== jobId) return;

      if (j.status === "running" || j.status === "queued") {
        live.status = j.status;
        live.message = j.message || "Running AutoCue…";
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
        scheduleLoadTracks({ keepPath: currentTrack()?.path, silent: true });
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
    } finally {
      const liveEnd = state.retryJobs[pathKey];
      if (liveEnd) liveEnd._pollInFlight = false;
    }
  }, 2000);
}

async function hydrateAutocueJobs() {
  const data = await api("/api/retry-cues", { timeoutMs: 5000 }).catch(() => null);
  const jobs = data?.jobs || [];
  let attached = 0;
  for (const job of jobs) {
    if (!isAutocueJobActive(job) || !job.path) continue;
    const existing = state.retryJobs[job.path];
    if (existing?.pollTimer && existing.id === job.id) continue;
    if (existing?.pollTimer) stopRetryPollForPath(job.path);
    state.retryJobs[job.path] = {
      id: job.id,
      path: job.path,
      name: job.name || existing?.name || job.path,
      message: job.message || "Running AutoCue…",
      status: job.status || "running",
      writeScope: job.write_scope || existing?.writeScope,
      pollTimer: null,
    };
    startRetryPoll(job.path, job.id);
    attached += 1;
  }
  const batchPaths = [];
  for (const batch of data?.batches || []) {
    if (batch.status !== "queued" && batch.status !== "running") continue;
    for (const item of batch.items || []) {
      if (item.path && isAutocueJobActive(item)) batchPaths.push(item.path);
    }
  }
  state.batchCueingPaths = batchPaths;
  if (attached || batchPaths.length) syncAutocueUi();
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

  // Sticky topbar chip for any in-flight AutoCue jobs.
  const chip = $("autocueJobChip");
  if (chip) {
    const active = activeRetryJobs();
    const batchOn = Boolean(state.batchPollTimer);
    if (active.length || batchOn) {
      chip.hidden = false;
      chip.classList.toggle("is-error", false);
      const names = active.map((j) => j.name || "track").slice(0, 2).join(", ");
      chip.textContent = batchOn
        ? `AutoCue batch running…`
        : `AutoCue ${active.length} running${names ? ` · ${names}` : ""}`;
      chip.title = active.map((j) => `${j.name}: ${j.message || j.status}`).join("\n");
    } else {
      chip.hidden = true;
      chip.textContent = "";
    }
  }

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
  updateCueingFilterUi();
  const cueSig = cueingListSignature();
  if (isReviewMode() && cueSig !== state.cueingListSig) {
    state.cueingListSig = cueSig;
    renderTrackList();
    if (state.crateFilter === "cueing") {
      const indexes = filteredTrackIndexes();
      if (indexes.length && !indexes.includes(state.index)) {
        state.index = indexes[0];
        renderPlayer();
      }
    }
  }
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

  // Deep grid preflight before confirming (skip deep if user already confirmed).
  let gridConfirmed = Boolean(
    state.gridManualConfirmed && state.gridManualConfirmed[pathKey]
  );
  setRetryStatus(
    gridConfirmed ? "Using confirmed beatgrid…" : "Checking beatgrid…",
    "running"
  );
  let preflight = null;
  try {
    const pf = await api(
      `/api/grid-preflight?path=${encodeURIComponent(track.path)}&deep=${
        gridConfirmed ? "false" : "true"
      }`
    );
    preflight = pf.preflight;
    if (gridConfirmed && preflight) {
      preflight = {
        ...preflight,
        can_autocue: preflight.bpm != null && preflight.grid_anchor != null
          ? true
          : preflight.can_autocue,
        manual_required: false,
        status: preflight.can_autocue === false ? preflight.status : "warn",
        label:
          preflight.can_autocue === false
            ? preflight.label
            : "Grid manually confirmed",
      };
    }
    state.gridPreflight = preflight;
    renderGridPreflightCard(track);
  } catch (err) {
    delete state.retryJobs[pathKey];
    syncAutocueUi();
    setRetryStatus(`Grid check failed: ${err.message}`, "error");
    return;
  }

  if (preflight && !preflight.can_autocue && !gridConfirmed) {
    const reasons = (preflight.issues || []).join("\n• ") || preflight.label;
    const confirmable =
      Boolean(preflight.manual_confirmable) ||
      // Structural grid present (BPM + anchor) but deep onset failed.
      (Boolean(preflight.bpm) &&
        preflight.grid_anchor != null &&
        preflight.manual_required &&
        /onset energy is too weak/i.test(reasons));

    if (confirmable) {
      setRetryStatus(preflight.label || "Grid not auto-verified", "error");
      const proceed = await showConfirmDialog({
        title: "Beatgrid needs attention",
        track: trackDisplayTitle(track),
        message: `• ${reasons}`,
        note:
          "If you already set the '1' in VirtualDJ and it sounds right, confirm the grid to run AutoCue anyway (skips automatic onset verification).",
        confirmLabel: "Grid is correct — AutoCue",
        tone: "warning",
        cancelOnly: false,
      });
      if (!proceed) {
        delete state.retryJobs[pathKey];
        syncAutocueUi();
        setRetryStatus("", "");
        return;
      }
      state.gridManualConfirmed[pathKey] = true;
      gridConfirmed = true;
      // Treat as OK for the rest of this flow.
      preflight = {
        ...preflight,
        can_autocue: true,
        manual_required: false,
        status: "warn",
        label: "Grid manually confirmed",
        warnings: [
          ...(preflight.warnings || []),
          "User confirmed the VirtualDJ beatgrid after weak onset verification.",
        ],
      };
      state.gridPreflight = preflight;
      renderGridPreflightCard(track);
    } else {
      delete state.retryJobs[pathKey];
      syncAutocueUi();
      setRetryStatus(preflight.label || "Blocked — fix grid in VDJ", "error");
      setStatus(`Cannot AutoCue: ${preflight.label}`, "error");
      await showConfirmDialog({
        title: "Beatgrid needs attention",
        track: trackDisplayTitle(track),
        message: `• ${reasons}`,
        note: "Align the grid in VirtualDJ first (BPM + '1'), then try AutoCue again.",
        confirmLabel: "Close",
        tone: "warning",
        cancelOnly: true,
      });
      return;
    }
  }

  // Keep buttons locked while the confirm dialog is open.
  const liveStart = state.retryJobs[pathKey];
  if (liveStart) {
    liveStart.message = `Waiting for confirm (${scopeWord})…`;
    liveStart.status = "starting";
  }
  syncAutocueUi();

  const gridNote = preflight?.needs_align
    ? " The beatgrid may be misaligned. Align grid first — AutoCue will not move the '1'."
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
  if (await isVdjRunningFresh()) {
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
        // Skip deep onset re-check when user confirmed the VDJ grid manually.
        deep_grid_check: !gridConfirmed,
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
    startRetryPoll(pathKey, job.id);
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

function pajamathonNotCuedCount() {
  return state.tracks.filter((t) => {
    const st = trackReadinessStatus(t);
    return (
      addCuesSection(t) === "pajamathon" &&
      (st === "not_cued" || st === "missing")
    );
  }).length;
}

async function batchAddCuesForNotCued(scope = "all") {
  if (!isReviewMode()) return;
  const pajOnly = scope === "pajamathon";
  const countHint = pajOnly
    ? pajamathonNotCuedCount()
    : state.tracks.filter((t) => {
        const st = trackReadinessStatus(t);
        return st === "not_cued" || st === "missing";
      }).length;

  if (!countHint) {
    setStatus(
      pajOnly
        ? "No not-cued Pajamathon tracks to queue."
        : "No not-cued tracks to queue.",
      "error"
    );
    return;
  }

  const ok = await showConfirmDialog({
    title: pajOnly ? "Batch Pajamathon cues?" : "Batch add cues?",
    track: pajOnly
      ? `${countHint} not-cued Pajamathon songs`
      : `About ${countHint} not-cued tracks`,
    message:
      "Each track gets a beatgrid preflight, then AutoCue runs (up to two at a time).",
    note: pajOnly
      ? "Only Add Cues / Pajamathon. Inbox songs stay put. Keep VirtualDJ closed during the batch."
      : "Tracks without a usable BPM or grid are skipped. Keep VirtualDJ closed during the batch.",
    confirmLabel: pajOnly ? "Cue Pajamathon" : "Start batch",
    tone: "accent",
  });
  if (!ok) return;

  let allowRunning = false;
  if (await isVdjRunningFresh()) {
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
  const pajBtn = $("batchPajamathonCuesBtn");
  if (batchBtn) batchBtn.disabled = true;
  if (pajBtn) pajBtn.disabled = true;
  stopBatchPoll();
  setRetryStatus(
    pajOnly ? "Starting Pajamathon AutoCue batch…" : "Starting batch Add cues…",
    "running"
  );
  setStatus(pajOnly ? "Pajamathon AutoCue: queuing…" : "Batch AutoCue: queuing…");

  try {
    const pajPaths = pajOnly
      ? state.tracks
          .filter((t) => {
            const st = trackReadinessStatus(t);
            return (
              addCuesSection(t) === "pajamathon" &&
              (st === "not_cued" || st === "missing") &&
              t.path
            );
          })
          .map((t) => t.path)
      : [];
    const data = await api("/api/retry-cues/batch", {
      method: "POST",
      body: JSON.stringify({
        ...(pajOnly
          ? { paths: pajPaths, filter: "pajamathon_not_cued" }
          : { filter: "not_cued" }),
        allow_vdj_running: Boolean(allowRunning),
        require_grid: true,
        deep_grid_check: false,
      }),
    });
    const batch = data.batch;
    state.batchId = batch.id;
    setRetryStatus(batch.message || `Batch ${batch.id}…`, "running");

    state.batchPollTimer = setInterval(async () => {
      if (state.batchPollInFlight) return;
      state.batchPollInFlight = true;
      try {
        const res = await api(`/api/retry-cues/batch/${batch.id}`);
        const b = res.batch;
        setRetryStatus(b.message || "Batch running…", "running");
        setStatus(b.message || "Batch AutoCue…");
        state.batchCueingPaths = (b.items || [])
          .filter((item) => item.path && isAutocueJobActive(item))
          .map((item) => item.path);
        syncAutocueUi();
        if (b.status === "queued" || b.status === "running") return;
        state.batchCueingPaths = [];
        stopBatchPoll();
        if (batchBtn) batchBtn.disabled = false;
        if (pajBtn) pajBtn.disabled = false;
        const kind = b.failed && !b.done ? "error" : "ok";
        setRetryStatus(b.message, kind);
        setStatus(b.message, b.failed && !b.done ? "error" : "success");
        scheduleLoadTracks({ keepPath: currentTrack()?.path, silent: true });
        updateBatchAddCuesButton();
      } catch (err) {
        stopBatchPoll();
        if (batchBtn) batchBtn.disabled = false;
        if (pajBtn) pajBtn.disabled = false;
        setRetryStatus(err.message, "error");
        setStatus(err.message, "error");
      } finally {
        state.batchPollInFlight = false;
      }
    }, 2500);
    if (pajOnly) setCrateFilter("cueing");
  } catch (err) {
    stopBatchPoll();
    if (batchBtn) batchBtn.disabled = false;
    if (pajBtn) pajBtn.disabled = false;
    setRetryStatus(err.message, "error");
    setStatus(err.message, "error");
  }
}

function updateBatchAddCuesButton() {
  updateBatchFixGridsButton();
  const btn = $("batchAddCuesBtn");
  const pajBtn = $("batchPajamathonCuesBtn");
  const pajN = pajamathonNotCuedCount();
  if (pajBtn) {
    const showPaj = isReviewMode() && pajN > 0;
    pajBtn.hidden = !showPaj;
    pajBtn.textContent = pajN
      ? `Batch Pajamathon cues (${pajN})`
      : "Batch Pajamathon cues";
    pajBtn.disabled = !pajN || Boolean(state.batchPollTimer);
  }
  if (!btn) return;
  const show = isReviewMode() && state.readinessFilter === "not_cued";
  btn.hidden = !show;
  if (!show) return;
  const n = state.tracks.filter((t) => {
    const st = trackReadinessStatus(t);
    return st === "not_cued" || st === "missing";
  }).length;
  btn.textContent = n ? `Batch add cues (${n})` : "Batch add cues";
  btn.disabled = !n || Boolean(state.batchPollTimer);
}

function stopGridFixPoll() {
  if (state.gridFixPollTimer) {
    clearInterval(state.gridFixPollTimer);
    state.gridFixPollTimer = null;
  }
}

function pajamathonTrackCount() {
  return state.tracks.filter((t) => addCuesSection(t) === "pajamathon").length;
}

function updateBatchFixGridsButton() {
  const btn = $("batchFixGridsBtn");
  if (!btn) return;
  const n = pajamathonTrackCount();
  const show = isReviewMode() && n > 0;
  btn.hidden = !show;
  btn.textContent = n ? `Fix Pajamathon grids (${n})` : "Fix Pajamathon grids";
  btn.disabled = !n || Boolean(state.gridFixPollTimer);
}

async function batchFixPajamathonGrids() {
  const n = pajamathonTrackCount();
  if (!n) {
    setStatus("No Pajamathon songs in Add Cues.", "error");
    return;
  }
  const ok = await showConfirmDialog({
    title: "Fix Pajamathon grids?",
    track: `${n} Pajamathon songs`,
    message:
      "Halve VDJ double-time BPM when the music is really ~60–80, and snap the beatgrid so the musical 1 lands on beat 1 of the bar (any bar is fine).",
    note: "Close VirtualDJ first or the writes will be refused. Already-good grids are left alone.",
    confirmLabel: "Fix grids",
    tone: "accent",
  });
  if (!ok) return;

  let allowRunning = false;
  if (await isVdjRunningFresh()) {
    allowRunning = await showConfirmDialog({
      title: "VirtualDJ is still open",
      message:
        "Grid/BPM writes will be overwritten when VirtualDJ quits. Close it before continuing whenever possible.",
      confirmLabel: "Continue anyway",
      tone: "warning",
    });
    if (!allowRunning) {
      setStatus("Close VirtualDJ, then fix grids.", "error");
      return;
    }
  }

  const btn = $("batchFixGridsBtn");
  if (btn) btn.disabled = true;
  stopGridFixPoll();
  setRetryStatus("Starting Pajamathon grid/BPM fix…", "running");
  setStatus("Pajamathon grids: analyzing…");

  try {
    const data = await api("/api/grid-fix/batch", {
      method: "POST",
      body: JSON.stringify({
        filter: "pajamathon",
        apply: true,
        allow_vdj_running: Boolean(allowRunning),
      }),
    });
    const batch = data.batch;
    setRetryStatus(batch.message || `Grid fix ${batch.id}…`, "running");
    state.gridFixPollTimer = setInterval(async () => {
      if (state.gridFixPollInFlight) return;
      state.gridFixPollInFlight = true;
      try {
        const res = await api(`/api/grid-fix/batch/${batch.id}`);
        const b = res.batch;
        setRetryStatus(b.message || "Fixing grids…", "running");
        setStatus(b.message || "Fixing grids…");
        if (b.status === "queued" || b.status === "running") return;
        stopGridFixPoll();
        if (btn) btn.disabled = false;
        const kind = b.failed && !b.done && !b.halved ? "error" : "ok";
        setRetryStatus(b.message, kind);
        setStatus(b.message, b.failed && !b.done ? "error" : "success");
        scheduleLoadTracks({ keepPath: currentTrack()?.path, silent: true });
        updateBatchFixGridsButton();
      } catch (err) {
        stopGridFixPoll();
        if (btn) btn.disabled = false;
        setRetryStatus(err.message, "error");
        setStatus(err.message, "error");
      } finally {
        state.gridFixPollInFlight = false;
      }
    }, 2500);
  } catch (err) {
    stopGridFixPoll();
    if (btn) btn.disabled = false;
    setRetryStatus(err.message, "error");
    setStatus(err.message, "error");
  }
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


/** Apply manual grid confirmation onto a preflight object. */
function withManualGridConfirmation(g = {}) {
  return {
    ...g,
    can_autocue:
      g.bpm != null && g.grid_anchor != null
        ? true
        : Boolean(g.can_autocue),
    manual_required: false,
    manual_confirmable: false,
    needs_align: false,
    status: "warn",
    label: "Grid manually confirmed",
    issues: [],
    warnings: [
      ...(g.warnings || []).filter((w) => !/manually confirmed/i.test(String(w))),
      "You confirmed the VirtualDJ beatgrid. AutoCue skips deep onset verification for this track.",
    ],
  };
}

function isGridManuallyConfirmed(path) {
  return Boolean(path && state.gridManualConfirmed && state.gridManualConfirmed[path]);
}

/** User confirms the VDJ beatgrid after weak onset / ambient block. */
function confirmGridManually(track) {
  if (!track?.path) return;
  state.gridManualConfirmed[track.path] = true;
  const g = state.gridPreflight || track.grid || {};
  state.gridPreflight = withManualGridConfirmation(g);
  // Keep list badge / structural grid in sync so the red banner cannot come back.
  const live = state.tracks.find((t) => t.path === track.path);
  if (live) live.grid = withManualGridConfirmation(live.grid || g);
  if (track.grid) track.grid = withManualGridConfirmation(track.grid);
  renderGridPreflightCard(track);
  renderTrackList();
  setStatus("Grid confirmed — you can run AutoCue now.", "success");
  setRetryStatus("Grid confirmed — ready for AutoCue", "success");
}

function renderGridPreflightCard(track) {
  const card = $("gridPreflightCard");
  if (!card) return;
  if (!isReviewMode() || !track) {
    card.hidden = true;
    card.innerHTML = "";
    return;
  }
  let g = state.gridPreflight || track.grid;
  if (!g) {
    card.hidden = true;
    card.innerHTML = "";
    return;
  }
  if (isGridManuallyConfirmed(track.path)) {
    g = withManualGridConfirmation(g);
    state.gridPreflight = g;
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
    ? "Fix the beatgrid in VirtualDJ before AutoCue — or use Align grid on the wave."
    : g.needs_align
      ? "Onset analysis disagrees with the current 1. If it already sounds right, leave it. If not, drag Align grid and Apply — do not trust Auto-align on syncopated tracks."
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
        g.bpm || g.grid_anchor != null
          ? `<button type="button" class="btn ${
              g.needs_align || g.status === "blocked" || g.status === "fixable"
                ? "primary"
                : "ghost"
            }" id="alignGridFromCardBtn"
               title="Drag the downbeat ones on the wave, then Apply to VirtualDJ">
               Align grid
             </button>
             <button type="button" class="btn ghost" id="autoAlignGridFromCardBtn"
               title="Preview a stem-based 1. Often wrong on syncopated tracks. Does not write until you Apply.">
               Auto-align (preview)
             </button>`
          : ""
      }
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
      ${
        isGridManuallyConfirmed(track.path)
          ? `<span class="badge ok">Grid confirmed</span>`
          : g.manual_confirmable ||
              (g.manual_required &&
                g.bpm != null &&
                g.grid_anchor != null &&
                !g.can_autocue)
            ? `<button type="button" class="btn primary" id="confirmGridBtn"
                 title="I set the grid in VirtualDJ — allow AutoCue without onset verification">
                 ✓ Grid is correct
               </button>`
            : ""
      }
    </div>
  `;

  $("confirmGridBtn")?.addEventListener("click", () => confirmGridManually(track));
  $("alignGridFromCardBtn")?.addEventListener("click", () => openGridAlignMode());
  $("autoAlignGridFromCardBtn")?.addEventListener("click", () => attemptAutoGridAlign());
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
  if (await isVdjRunningFresh()) {
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
  // User already confirmed this path — never re-show "Cannot verify grid".
  if (isGridManuallyConfirmed(track.path)) {
    const confirmed = withManualGridConfirmation(
      state.gridPreflight || track.grid || {}
    );
    state.gridPreflight = confirmed;
    if (track.grid) track.grid = confirmed;
    renderGridPreflightCard(track);
    return;
  }
  // Show fast list data immediately.
  state.gridPreflight = track.grid || null;
  renderGridPreflightCard(track);
  const st = trackReadinessStatus(track);
  const g = track.grid || {};
  const needsLook =
    st === "not_cued" ||
    st === "missing" ||
    st === "partial" ||
    g.needs_align ||
    g.status === "blocked" ||
    g.status === "fixable" ||
    g.status === "warn";
  if (!needsLook) return;
  try {
    const data = await api(
      `/api/grid-preflight?path=${encodeURIComponent(track.path)}&deep=true`
    );
    if (gen !== state.trackGen || currentTrack()?.path !== track.path) return;
    // Confirmation may have happened while deep request was in flight.
    if (isGridManuallyConfirmed(track.path)) {
      state.gridPreflight = withManualGridConfirmation(data.preflight || {});
    } else {
      state.gridPreflight = data.preflight;
    }
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
  if (await isVdjRunningFresh()) {
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
  state.waveViewPinned = false;
}

function snapshotWaveView() {
  state.waveViewBeforeAlign = {
    zoom: state.waveZoom,
    offset: state.waveOffset,
  };
}

function restoreWaveView() {
  const prev = state.waveViewBeforeAlign;
  state.waveViewBeforeAlign = null;
  if (!prev) return false;
  state.waveZoom = clampWaveZoom(prev.zoom);
  state.waveOffset = Number(prev.offset) || 0;
  return true;
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

  // Keep identity chrome light — at most a few chips (readiness, cues/bpm, bitrate).
  const statusChip = isReviewMode()
    ? `${readinessBadge(track)}${retryHistoryBadge(track)}`
    : track.is_cued
      ? `<span class="badge ok">${cues.cue_count || 0} cues${
          cues.loop_count ? ` · ${cues.loop_count} loops` : ""
        }</span>`
      : `<span class="badge uncued">Not cued</span>`;
  const bpmChip = cues.bpm
    ? state.halfBpm
      ? `<span class="badge ok" title="VDJ ${Number(cues.bpm).toFixed(0)} halved">${(
          Number(cues.bpm) / 2
        ).toFixed(0)} BPM (½)</span>`
      : `<span class="badge neutral">${Number(cues.bpm).toFixed(0)} BPM</span>`
    : "";
  const brChip = brLabel
    ? `<span class="badge ${brClass}" title="${codec || "audio"} ${
        sr ? sr + " Hz" : ""
      }">${escapeHtml(brLabel)}</span>`
    : "";
  const warnChip = !cues.in_database
    ? `<span class="badge bad">not in VDJ</span>`
    : "";
  return `${statusChip}${bpmChip}${brChip}${warnChip}`;
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
  if (isPracticeMode()) {
    setPracticeWaveStatus("Loading waveform…");
    drawPracticeWaveform();
  } else {
    setWaveformStatus("Loading waveform…");
    drawWaveform();
  }

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
    setPracticeWaveStatus("");
    if (isPracticeMode()) drawPracticeWaveform();
    else drawWaveform();
  } catch (err) {
    if (err.name === "AbortError") return;
    if (gen !== state.trackGen || currentTrack()?.path !== track.path) return;
    state.waveform = null;
    state.waveformLoading = false;
    state.waveformError = err.message;
    setWaveformStatus(err.message || "Waveform failed", "error");
    setPracticeWaveStatus(err.message || "Waveform failed", "error");
    if (isPracticeMode()) drawPracticeWaveform();
    else drawWaveform();
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

/** Visible time window over the full track duration (no playhead follow). */
function visibleWaveWindow(duration, zoom, offset) {
  const z = clampWaveZoom(zoom || 1);
  if (!duration || duration <= 0) {
    return { start: 0, end: 0, span: 0, offset: 0, zoom: z };
  }
  const span = duration / z;
  let start = Number(offset) || 0;
  start = Math.max(0, Math.min(start, Math.max(0, duration - span)));
  return { start, end: start + span, span, offset: start, zoom: z };
}

/** Visible time window over the full track duration. */
function waveViewWindow(duration) {
  const view = visibleWaveWindow(duration, state.waveZoom, state.waveOffset);
  state.waveZoom = view.zoom;
  state.waveOffset = view.start;
  return view;
}

/**
 * Page the zoom window so a moving playhead stays on-screen.
 * Paused / drag leave the user's view alone.
 */
function keepPlayheadInView(
  duration,
  timeSec,
  { zoom, offset, playing, allowFollow, lead } = {}
) {
  const view = visibleWaveWindow(
    duration,
    zoom ?? state.waveZoom,
    offset ?? state.waveOffset
  );
  if (!duration || !Number.isFinite(timeSec)) return view;
  if (!playing || allowFollow === false) return view;
  if (timeSec >= view.start && timeSec <= view.end) return view;
  const frac = Number.isFinite(lead) ? lead : 0.08;
  return visibleWaveWindow(duration, view.zoom, timeSec - view.span * frac);
}

function applyPlayheadFollow(duration, timeSec) {
  const audio = $("audio");
  const playing = Boolean(audio && !audio.paused && !audio.ended);
  const view = visibleWaveWindow(duration, state.waveZoom, state.waveOffset);
  if (
    state.waveViewPinned &&
    Number.isFinite(timeSec) &&
    view.span > 0 &&
    timeSec >= view.start &&
    timeSec <= view.end
  ) {
    // Needle is back on-screen — resume paging.
    state.waveViewPinned = false;
  }
  const allowFollow =
    !state.gridAlignDragging &&
    !state.loopDrag &&
    !state.gridAlignMode &&
    !state.waveViewPinned;
  const next = keepPlayheadInView(duration, timeSec, {
    zoom: state.waveZoom,
    offset: state.waveOffset,
    playing,
    allowFollow,
  });
  state.waveZoom = next.zoom;
  state.waveOffset = next.start;
  return next;
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

function classifyWaveMarkers(points, view, slack = 0.05) {
  const inView = [];
  const offLeft = [];
  const offRight = [];
  for (const p of points || []) {
    const pos = Number(p.pos) || 0;
    if (pos < view.start - slack) offLeft.push(p);
    else if (pos > view.end + slack) offRight.push(p);
    else inView.push(p);
  }
  return { inView, offLeft, offRight };
}

function formatOffscreenCueLabel(points, side) {
  let cues = 0;
  let loops = 0;
  for (const p of points || []) {
    if (pointKind(p) === "loop") loops += 1;
    else cues += 1;
  }
  const parts = [];
  if (cues) parts.push(`${cues} cue${cues === 1 ? "" : "s"}`);
  if (loops) parts.push(`${loops} loop${loops === 1 ? "" : "s"}`);
  if (!parts.length) return "";
  const body = parts.join(" · ");
  return side === "left" ? `← ${body}` : `${body} →`;
}

function panWaveToTime(timeSec, { frac = 0.22 } = {}) {
  const track = currentTrack();
  const audio = $("audio");
  const duration = waveformDuration(track, audio) || trackDuration(track, audio);
  const t = Number(timeSec);
  if (!duration || !Number.isFinite(t)) return;
  const view = waveViewWindow(duration);
  const next = visibleWaveWindow(duration, view.zoom, t - view.span * frac);
  state.waveZoom = next.zoom;
  state.waveOffset = next.start;
  state.waveViewPinned = true;
  drawWaveform();
}

function hitTestRect(rect, x, y) {
  if (!rect) return false;
  return x >= rect.x0 && x <= rect.x1 && y >= rect.y0 && y <= rect.y1;
}

function hitTestWaveCueChrome(clientX, clientY) {
  const hits = state.waveCueChromeHits;
  const wrap = $("waveformWrap");
  if (!hits || !wrap) return null;
  const box = wrap.getBoundingClientRect();
  const x = clientX - box.left;
  const y = clientY - box.top;
  if (hitTestRect(hits.left, x, y)) return { kind: "left", time: hits.left.time };
  if (hitTestRect(hits.right, x, y)) return { kind: "right", time: hits.right.time };
  // Loop/cue handles win over the full-width overview strip.
  if (
    hitTestRect(hits.overview, x, y) &&
    (hitTestCueAtClientX(clientX) || hitTestLoopAtClientX(clientX))
  ) {
    return null;
  }
  if (hitTestRect(hits.overview, x, y)) {
    const duration = waveformDuration(currentTrack(), $("audio"));
    const plotW = Number(hits.overview.plotW) || 0;
    if (!duration || plotW <= 0) return { kind: "overview", time: null };
    const ratio = Math.min(
      1,
      Math.max(0, (x - hits.overview.padX) / plotW)
    );
    return { kind: "overview", time: ratio * duration };
  }
  return null;
}

function drawWaveCueOverview(ctx, points, view, duration, padX, plotW, h) {
  if (!state.waveCueChromeHits) state.waveCueChromeHits = {};
  if (!duration || state.waveZoom <= 1.01) {
    state.waveCueChromeHits.overview = null;
    return;
  }
  const ovH = 7;
  const ovY = h - ovH - 2;
  ctx.save();
  ctx.fillStyle = "rgba(42, 51, 68, 0.95)";
  ctx.fillRect(padX, ovY, plotW, ovH);
  for (const p of points || []) {
    const t = Number(p.pos) || 0;
    const x = padX + (t / duration) * plotW;
    ctx.fillStyle = CUE_COLORS[p.color_name] || CUE_COLORS.unknown;
    ctx.fillRect(x - 1, ovY, 2, ovH);
  }
  const winX = padX + (view.start / duration) * plotW;
  const winW = Math.max(2, (view.span / duration) * plotW);
  ctx.fillStyle = accentRgba(0.22);
  ctx.fillRect(winX, ovY, winW, ovH);
  ctx.strokeStyle = accentRgba(0.95);
  ctx.lineWidth = 1;
  ctx.strokeRect(winX + 0.5, ovY + 0.5, Math.max(1, winW - 1), ovH - 1);
  ctx.restore();
  state.waveCueChromeHits.overview = {
    x0: padX,
    y0: ovY,
    x1: padX + plotW,
    y1: ovY + ovH,
    padX,
    plotW,
  };
}

function drawOffscreenCueHints(ctx, classified, padX, plotW, h) {
  if (!state.waveCueChromeHits) state.waveCueChromeHits = {};
  state.waveCueChromeHits.left = null;
  state.waveCueChromeHits.right = null;
  if (state.waveZoom <= 1.01 || !classified) return;

  const drawChip = (label, side, targetTime) => {
    if (!label) return;
    ctx.save();
    ctx.font = "11px SF Pro Text, system-ui, sans-serif";
    const tw = Math.ceil(ctx.measureText(label).width);
    const boxW = tw + 12;
    const boxH = 18;
    const x = side === "left" ? padX + 4 : padX + plotW - boxW - 4;
    const y = 20;
    ctx.fillStyle = "rgba(10, 14, 22, 0.88)";
    ctx.strokeStyle = "rgba(255, 214, 102, 0.85)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    const r = 4;
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + boxW, y, x + boxW, y + boxH, r);
    ctx.arcTo(x + boxW, y + boxH, x, y + boxH, r);
    ctx.arcTo(x, y + boxH, x, y, r);
    ctx.arcTo(x, y, x + boxW, y, r);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = "#ffd666";
    ctx.fillText(label, x + 6, y + 13);
    ctx.restore();
    const hit = { x0: x, y0: y, x1: x + boxW, y1: y + boxH, time: targetTime };
    if (side === "left") state.waveCueChromeHits.left = hit;
    else state.waveCueChromeHits.right = hit;
  };

  const leftPts = classified.offLeft || [];
  const rightPts = classified.offRight || [];
  const leftTime = leftPts.length
    ? Math.max(...leftPts.map((p) => Number(p.pos) || 0))
    : null;
  const rightTime = rightPts.length
    ? Math.min(...rightPts.map((p) => Number(p.pos) || 0))
    : null;
  drawChip(formatOffscreenCueLabel(leftPts, "left"), "left", leftTime);
  drawChip(formatOffscreenCueLabel(rightPts, "right"), "right", rightTime);
}

/** Canvas + overlay needle. Moving playhead is never dropped. */
function positionWavePlayhead(ctx, audio, view, padX, plotW, h) {
  const needle = $("wavePlayhead");
  if (!audio || !Number.isFinite(audio.currentTime)) {
    if (needle) needle.hidden = true;
    return;
  }
  const t = audio.currentTime;
  const playing = !audio.paused && !audio.ended;
  const inView = view.span > 0 && t >= view.start && t <= view.end;
  if (!inView && !playing) {
    if (needle) needle.hidden = true;
    return;
  }
  let x = timeToWaveX(t, padX, plotW, view);
  x = Math.max(padX, Math.min(padX + plotW, x));
  if (needle) {
    needle.hidden = false;
    needle.style.left = `${x}px`;
    return;
  }
  if (ctx && h > 0) {
    ctx.save();
    ctx.strokeStyle = "#ffffff";
    ctx.lineWidth = 2;
    ctx.globalAlpha = 0.95;
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, h);
    ctx.stroke();
    ctx.restore();
  }
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

/** Downbeat / grid anchor in seconds (VDJ beatgrid POI, Scan Phase, or preflight). */
function gridAnchorSeconds(track) {
  // Live drag / align mode overrides stored value for display.
  if (
    state.gridAlignMode &&
    state.gridAlignAnchor != null &&
    Number.isFinite(Number(state.gridAlignAnchor))
  ) {
    return Number(state.gridAlignAnchor);
  }
  const g = state.gridPreflight || track?.grid || {};
  const fromGrid = Number(g.grid_anchor);
  if (Number.isFinite(fromGrid)) return fromGrid;
  const cues = track?.cues || {};
  const phase = Number(cues.scan_phase);
  if (Number.isFinite(phase)) return phase;
  const bg = Number(cues.beatgrid_pos);
  if (Number.isFinite(bg)) return bg;
  return 0;
}

/**
 * Musical BPM for bar-1 spacing on the wave.
 * Honors ½ BPM toggle so double-time VDJ values still land on felt ones.
 */
function onesBpm(track) {
  return sourceBpm(track) || trackBpm(track);
}

function barPeriodSeconds(track) {
  const bpm = onesBpm(track);
  if (!bpm || bpm <= 0) return null;
  // 4/4 bars — beat 1 every 4 beats
  return (60 / bpm) * 4;
}

function syncBeatOnesBtn() {
  const btn = $("beatOnesBtn");
  if (!btn) return;
  btn.classList.toggle("active", state.showBeatOnes);
  btn.setAttribute("aria-pressed", state.showBeatOnes ? "true" : "false");
}

function toggleBeatOnes() {
  state.showBeatOnes = !state.showBeatOnes;
  try {
    localStorage.setItem("musicSorter.showBeatOnes", state.showBeatOnes ? "1" : "0");
  } catch {
    /* ignore */
  }
  syncBeatOnesBtn();
  drawWaveform();
}

/**
 * Draw beatgrid as a *ruler* (top/bottom ticks), not full-height cue-like lines.
 * Cues own the middle of the wave; ones stay out of their way.
 */
function drawBeatOnes(ctx, track, view, padX, plotW, h) {
  // Always show ones while aligning; otherwise honor Ones toggle.
  if (!state.showBeatOnes && !state.gridAlignMode) return;
  const barSec = barPeriodSeconds(track);
  if (!barSec || !view.span || barSec <= 0) return;

  const bpm = onesBpm(track) || trackBpm(track);
  const beatSec = bpm && bpm > 0 ? 60 / bpm : barSec / 4;
  const anchor = gridAnchorSeconds(track);
  // First one at or before view.start
  let t = anchor;
  if (t > view.start) {
    t -= Math.ceil((t - view.start) / barSec) * barSec;
  } else {
    t += Math.floor((view.start - t) / barSec) * barSec;
  }

  const pxPerBar = (barSec / view.span) * plotW;
  const align = state.gridAlignMode;
  if (!align && pxPerBar < 0.75) return;
  if (align && pxPerBar < 0.4) {
    ctx.save();
    ctx.fillStyle = accentRgba(0.18);
    ctx.fillRect(0, 0, padX + plotW + padX, 20);
    ctx.fillStyle = "rgba(200, 250, 255, 0.95)";
    ctx.font = "bold 11px SF Pro Text, system-ui, sans-serif";
    ctx.fillText("ALIGN · zoom in (scroll) to see grid ticks", padX + 6, 14);
    ctx.restore();
    return;
  }

  // Ruler lives in top/bottom gutters — cues keep the center.
  const rulerH = align ? 18 : 14;
  const botY = h - 1;
  const maxLines = 400;
  let count = 0;
  ctx.save();

  // Thin top/bottom rails (grid, not cue)
  if (align) {
    ctx.fillStyle = accentRgba(0.14);
    ctx.fillRect(0, 0, padX + plotW + padX, rulerH + 4);
    ctx.fillStyle = "rgba(200, 250, 255, 0.95)";
    ctx.font = "bold 11px SF Pro Text, system-ui, sans-serif";
    ctx.fillText(
      `ALIGN · drag to shift grid · 1 @ ${(Number(anchor) || 0).toFixed(3)}s`,
      padX + 6,
      13
    );
  } else {
    ctx.strokeStyle = "rgba(255, 214, 102, 0.12)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(padX, rulerH + 0.5);
    ctx.lineTo(padX + plotW, rulerH + 0.5);
    ctx.stroke();
  }

  // Weak beat ticks — top gutter only, very short
  if (beatSec > 0) {
    const pxPerBeat = (beatSec / view.span) * plotW;
    if (pxPerBeat >= (align ? 5 : 10)) {
      let bt = anchor;
      if (bt > view.start) {
        bt -= Math.ceil((bt - view.start) / beatSec) * beatSec;
      } else {
        bt += Math.floor((view.start - bt) / beatSec) * beatSec;
      }
      let bc = 0;
      for (; bt <= view.end + 1e-9 && bc < maxLines * 4; bt += beatSec, bc++) {
        const stepsFromAnchor = Math.round((bt - anchor) / beatSec);
        if (Math.abs(stepsFromAnchor % 4) < 1e-6) continue; // bars handled below
        if (bt < view.start - 1e-6) continue;
        const x = timeToWaveX(bt, padX, plotW, view);
        ctx.fillStyle = align
          ? accentRgba(0.35)
          : "rgba(255, 214, 102, 0.28)";
        ctx.fillRect(x, 2, 1, 5);
        ctx.fillRect(x, botY - 5, 1, 4);
      }
    }
  }

  for (; t <= view.end + 1e-9 && count < maxLines; t += barSec, count++) {
    if (t < view.start - 1e-6) continue;
    const x = timeToWaveX(t, padX, plotW, view);

    const barsFromAnchor = Math.round((t - anchor) / barSec);
    const isPhraseOne = Math.abs(barsFromAnchor % 4) < 1e-6;
    const isAnchor =
      Math.abs(t - anchor) < Math.max(barSec * 0.02, 0.002);

    if (isPhraseOne || isAnchor) {
      // Phrase / true 1 — tall ticks + label in gutters only (not full-height)
      const tickTop = isAnchor ? rulerH + 2 : rulerH - 2;
      const tickBot = isAnchor ? 12 : 8;
      ctx.strokeStyle = align
        ? accentRgba(0.85)
        : "rgba(255, 200, 90, 0.7)";
      ctx.lineWidth = isAnchor ? 2 : 1.5;
      // Top tick
      ctx.beginPath();
      ctx.moveTo(x + 0.5, 1);
      ctx.lineTo(x + 0.5, tickTop);
      ctx.stroke();
      // Bottom tick
      ctx.beginPath();
      ctx.moveTo(x + 0.5, botY - tickBot);
      ctx.lineTo(x + 0.5, botY);
      ctx.stroke();

      // Optional ultra-faint center guide only in align (so drag target is clear)
      if (align) {
        ctx.strokeStyle = accentRgba(0.12);
        ctx.lineWidth = 1;
        ctx.setLineDash([2, 6]);
        ctx.beginPath();
        ctx.moveTo(x + 0.5, tickTop + 2);
        ctx.lineTo(x + 0.5, botY - tickBot - 2);
        ctx.stroke();
        ctx.setLineDash([]);
      }

      // Badge: rounded chip with "1" — cue labels sit lower/elsewhere
      const label = "1";
      ctx.font = isAnchor
        ? "bold 10px SF Pro Text, system-ui, sans-serif"
        : "bold 9px SF Pro Text, system-ui, sans-serif";
      const tw = ctx.measureText(label).width;
      const bx = x - tw / 2 - 3;
      const by = align ? 22 : 2;
      const bw = tw + 6;
      const bh = 12;
      ctx.fillStyle = align
        ? "rgba(20, 40, 55, 0.92)"
        : "rgba(18, 16, 10, 0.88)";
      ctx.strokeStyle = align
        ? accentRgba(0.9)
        : "rgba(255, 200, 90, 0.85)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      const r = 3;
      ctx.moveTo(bx + r, by);
      ctx.arcTo(bx + bw, by, bx + bw, by + bh, r);
      ctx.arcTo(bx + bw, by + bh, bx, by + bh, r);
      ctx.arcTo(bx, by + bh, bx, by, r);
      ctx.arcTo(bx, by, bx + bw, by, r);
      ctx.closePath();
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = align ? "#9becff" : "#ffd666";
      ctx.fillText(label, x - tw / 2, by + 9);

      // Drag handle on true anchor only
      if (isAnchor) {
        ctx.fillStyle = align ? accentHex() : "#ffd666";
        ctx.beginPath();
        ctx.moveTo(x, by + bh + 5);
        ctx.lineTo(x - 4, by + bh + 1);
        ctx.lineTo(x + 4, by + bh + 1);
        ctx.closePath();
        ctx.fill();
      }
    } else {
      // Other bar downs (5, 9, 13…) — short gutter ticks only, no line through wave
      ctx.fillStyle = align
        ? accentRgba(0.4)
        : "rgba(255, 214, 102, 0.35)";
      ctx.fillRect(x, 2, 1, 7);
      ctx.fillRect(x, botY - 7, 1, 6);
    }
  }
  if (align) {
    ctx.fillStyle = "rgba(160, 220, 240, 0.85)";
    ctx.font = "10px SF Pro Text, system-ui, sans-serif";
    ctx.fillText(
      "Grid = top/bottom ticks · cues stay as full colored lines",
      padX + 6,
      h - 6
    );
  }
  ctx.restore();
}

function syncGridAlignUi() {
  const btn = $("gridAlignBtn");
  if (btn) {
    btn.classList.toggle("active", state.gridAlignMode);
    btn.setAttribute("aria-pressed", state.gridAlignMode ? "true" : "false");
    btn.textContent = state.gridAlignMode ? "Aligning…" : "Align grid";
  }
  const bar = $("gridAlignBar");
  if (bar) {
    if (state.gridAlignMode) {
      bar.hidden = false;
      bar.removeAttribute("hidden");
      bar.style.display = "flex";
    } else {
      bar.hidden = true;
      bar.setAttribute("hidden", "");
      bar.style.display = "";
    }
  }
  const wrap = $("waveformWrap");
  if (wrap) wrap.classList.toggle("grid-align-mode", state.gridAlignMode);
  const label = $("gridAlignAnchorLabel");
  if (label) {
    if (state.gridAlignMode) {
      const a = Number(state.gridAlignAnchor);
      const orig = Number(state.gridAlignOriginal);
      const delta = Number.isFinite(a) && Number.isFinite(orig) ? a - orig : 0;
      const plan = state.gridAlignPlan;
      const autoNote = plan?.reason
        ? ` · auto: ${plan.reason}`
        : " · drag wave to shift";
      label.textContent = Number.isFinite(a)
        ? `1 @ ${a.toFixed(3)}s${
            Math.abs(delta) >= 0.0005
              ? ` (${delta >= 0 ? "+" : ""}${delta.toFixed(3)}s)`
              : ""
          }${autoNote}`
        : "No anchor — drag to set 1";
    } else {
      label.textContent = "1 @ —";
    }
  }
  const applyBtn = $("gridAlignApplyBtn");
  if (applyBtn) {
    const dirty =
      state.gridAlignMode &&
      Math.abs(Number(state.gridAlignAnchor) - Number(state.gridAlignOriginal)) > 1e-4;
    applyBtn.disabled = !dirty;
  }
}

/** Zoom wave so ~12 bars are visible around the playhead (ones stay readable). */
function zoomWaveForGridAlign(track) {
  const audio = $("audio");
  const duration = waveformDuration(track, audio) || trackDuration(track, audio);
  const barSec = barPeriodSeconds(track);
  if (!duration || !barSec || barSec <= 0) return;
  const wantSpan = Math.min(duration, barSec * 12);
  const zoom = clampWaveZoom(duration / wantSpan);
  state.waveZoom = Math.max(zoom, 2);
  const span = duration / state.waveZoom;
  // Align the 1, not wherever the playhead happened to sit (that hid every cue).
  const center = gridAnchorSeconds(track);
  state.waveOffset = Math.max(0, Math.min(duration - span, center - span / 2));
  state.waveViewPinned = true;
}

function openGridAlignMode() {
  const track = currentTrack();
  if (!track) {
    setStatus("Select a track first.", "error");
    return;
  }
  if (!trackBpm(track) && !onesBpm(track)) {
    setStatus("Track needs a VDJ BPM before aligning the grid.", "error");
    return;
  }
  if (state.placeCueMode) cancelPlaceCueMode();
  if (state.placeLoopMode) cancelPlaceLoopMode();
  const anchor = gridAnchorSeconds(track);
  state.gridAlignMode = true;
  state.gridAlignOriginal = anchor;
  state.gridAlignAnchor = anchor;
  state.gridAlignDragging = false;
  // Ones must be visible while aligning
  state.showBeatOnes = true;
  syncBeatOnesBtn();
  // Zoom in so ones are clearly visible (full-track view can hide them)
  snapshotWaveView();
  zoomWaveForGridAlign(track);
  syncGridAlignUi();
  drawWaveform();
  // Ensure toolbar is on screen
  try {
    $("gridAlignBar")?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  } catch {
    /* ignore */
  }
  setStatus(
    "ALIGN MODE — drag the cyan ones on the wave (or use −1/+1 beat). Apply writes to VDJ.",
    "running"
  );
}

function exitGridAlignMode({ restoreView = false, status } = {}) {
  state.gridAlignMode = false;
  state.gridAlignPlan = null;
  state.gridAlignAnchor = null;
  state.gridAlignOriginal = null;
  state.gridAlignDragging = false;
  if (restoreView) {
    restoreWaveView();
    state.waveViewPinned = true;
  } else {
    state.waveViewBeforeAlign = null;
  }
  syncGridAlignUi();
  drawWaveform();
  if (status != null) setStatus(status);
}

function cancelGridAlignMode() {
  exitGridAlignMode({
    restoreView: true,
    status: "Grid align cancelled — no changes written.",
  });
}

function nudgeGridAlign(deltaSeconds) {
  if (!state.gridAlignMode) return;
  const cur = Number(state.gridAlignAnchor);
  if (!Number.isFinite(cur)) return;
  state.gridAlignAnchor = Math.max(0, cur + deltaSeconds);
  syncGridAlignUi();
  drawWaveform();
}

function nudgeGridAlignBeats(beats) {
  const track = currentTrack();
  const bpm = onesBpm(track) || trackBpm(track);
  if (!bpm) return;
  const beatSec = 60 / bpm;
  nudgeGridAlign(beats * beatSec);
}

async function applyGridAlign() {
  const track = currentTrack();
  if (!track || !state.gridAlignMode) return;
  const anchor = Number(state.gridAlignAnchor);
  if (!Number.isFinite(anchor)) {
    setStatus("No grid anchor to apply.", "error");
    return;
  }
  const orig = Number(state.gridAlignOriginal);
  const plan = state.gridAlignPlan;
  const wantHalve = Boolean(plan?.halve);
  if (Number.isFinite(orig) && Math.abs(anchor - orig) < 1e-4 && !wantHalve) {
    setStatus("Grid unchanged — nothing to write.");
    return;
  }

  let allowRunning = false;
  if (await isVdjRunningFresh()) {
    allowRunning = await showConfirmDialog({
      title: "VirtualDJ is still open",
      track: trackDisplayTitle(track),
      message:
        "Beatgrid changes may be overwritten when VirtualDJ quits. Close it first when possible.",
      confirmLabel: "Write grid anyway",
      tone: "warning",
    });
    if (!allowRunning) {
      setStatus("Close VirtualDJ, then apply the grid.", "error");
      return;
    }
  }

  try {
    if (wantHalve) {
      setStatus("Writing ½ BPM, then the new 1…");
      await api("/api/halve-bpm", {
        method: "POST",
        body: JSON.stringify({
          path: track.path,
          allow_vdj_running: Boolean(allowRunning),
          double_instead: false,
        }),
      });
      if (state.halfBpm) setHalfBpm(false);
    }
    setStatus(`Writing beatgrid 1 @ ${anchor.toFixed(3)}s…`);
    const data = await api("/api/set-beatgrid", {
      method: "POST",
      body: JSON.stringify({
        path: track.path,
        anchor_seconds: anchor,
        allow_vdj_running: Boolean(allowRunning),
      }),
    });
    const r = data.result || {};
    if (r.cues) {
      applyCueSummaryToTrack(track.path, r.cues);
    } else {
      const live = currentTrack();
      if (live?.cues) {
        applyCueSummaryToTrack(track.path, {
          ...live.cues,
          beatgrid_pos: anchor,
          scan_phase: anchor,
          has_beatgrid: true,
        });
      }
    }
    // Keep preflight in sync for ones display
    if (state.gridPreflight) {
      state.gridPreflight = {
        ...state.gridPreflight,
        grid_anchor: anchor,
        beatgrid_pos: anchor,
        scan_phase: anchor,
      };
    }
    if (track.grid) {
      track.grid = {
        ...track.grid,
        grid_anchor: anchor,
        beatgrid_pos: anchor,
        scan_phase: anchor,
      };
    }

    exitGridAlignMode({ restoreView: false });
    renderCues();
    drawWaveform();
    setStatus(
      (wantHalve ? "½ BPM + " : "") +
        `beatgrid 1 @ ${anchor.toFixed(3)}s` +
        (r.changes?.scan_phase_updated ? " · Scan Phase" : "") +
        (r.changes?.beatgrid_poi_updated || r.changes?.beatgrid_poi_created
          ? " · beatgrid POI"
          : ""),
      "success"
    );
    // Refresh deep preflight in background
    loadDeepGridPreflight(currentTrack(), state.trackGen).catch(() => {});
  } catch (err) {
    setStatus(err.message, "error");
  }
}

async function attemptAutoGridAlign() {
  const track = currentTrack();
  if (!track) {
    setStatus("Select a track first.", "error");
    return;
  }
  if (state.placeCueMode) cancelPlaceCueMode();
  if (state.placeLoopMode) cancelPlaceLoopMode();
  const btn = $("autoAlignGridBtn");
  if (btn) btn.disabled = true;
  try {
    setStatus("Attempting automatic beatgrid align (stems + onsets)…", "running");
    const data = await api("/api/grid-align/attempt", {
      method: "POST",
      body: JSON.stringify({ path: track.path, apply: false }),
    });
    const result = data.result || {};
    const plan = result.plan || {};
    const action = String(plan.action || "skip");
    if (action === "skip") {
      state.gridAlignPlan = null;
      setStatus(plan.reason || "Grid already looks aligned — no change.", "success");
      return;
    }
    const proposed = Number(plan.anchor_after);
    if (!Number.isFinite(proposed)) {
      setStatus("Auto-align did not return a usable 1.", "error");
      return;
    }
    state.gridAlignPlan = plan;
    if (!state.gridAlignMode) openGridAlignMode();
    state.gridAlignAnchor = proposed;
    state.showBeatOnes = true;
    syncBeatOnesBtn();
    zoomWaveForGridAlign(track);
    syncGridAlignUi();
    drawWaveform();
    const halfNote = plan.halve
      ? ` · will also write ½ BPM (${Number(plan.bpm_before).toFixed(0)}→${Number(plan.bpm_after).toFixed(0)})`
      : "";
    setStatus(
      `Auto-align preview · 1 @ ${proposed.toFixed(3)}s` +
        (plan.shift_beats ? ` · +${plan.shift_beats} beat` : "") +
        halfNote +
        ". Listen, then Apply to VDJ.",
      "running"
    );
  } catch (err) {
    setStatus(err.message, "error");
  } finally {
    if (btn) btn.disabled = false;
  }
}

function onGridAlignPointerDown(e) {
  if (!state.gridAlignMode) return false;
  if (e.button != null && e.button !== 0) return false;
  const wrap = $("waveformWrap");
  const track = currentTrack();
  const audio = $("audio");
  if (!wrap || !track) return false;
  const duration = waveformDuration(track, audio);
  if (!duration) return false;
  const rect = wrap.getBoundingClientRect();
  const t = clientXToTime(e.clientX, rect, duration);
  state.gridAlignDragging = true;
  state.gridAlignDragOriginTime = t;
  state.gridAlignDragOriginAnchor = Number(state.gridAlignAnchor) || 0;
  wrap.setPointerCapture?.(e.pointerId);
  e.preventDefault();
  return true;
}

function onGridAlignPointerMove(e) {
  if (!state.gridAlignMode || !state.gridAlignDragging) return;
  const wrap = $("waveformWrap");
  const track = currentTrack();
  const audio = $("audio");
  if (!wrap || !track) return;
  const duration = waveformDuration(track, audio);
  if (!duration) return;
  const rect = wrap.getBoundingClientRect();
  const t = clientXToTime(e.clientX, rect, duration);
  const delta = t - state.gridAlignDragOriginTime;
  state.gridAlignAnchor = Math.max(
    0,
    state.gridAlignDragOriginAnchor + delta
  );
  syncGridAlignUi();
  drawWaveform();
  e.preventDefault();
}

function onGridAlignPointerUp(e) {
  if (!state.gridAlignDragging) return;
  state.gridAlignDragging = false;
  try {
    $("waveformWrap")?.releasePointerCapture?.(e.pointerId);
  } catch {
    /* ignore */
  }
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

  const track = currentTrack();
  const audio = $("audio");
  const duration = waveformDuration(track, audio);
  const t = audio && Number.isFinite(audio.currentTime) ? audio.currentTime : NaN;
  const view = duration
    ? applyPlayheadFollow(duration, t)
    : waveViewWindow(duration || 1);
  const { padX, plotW } = wavePlotMetrics(w);

  const peaks = state.waveform?.peaks;
  if (!peaks || !peaks.length) {
    ctx.strokeStyle = "rgba(42,51,68,0.8)";
    ctx.beginPath();
    ctx.moveTo(0, h / 2);
    ctx.lineTo(w, h / 2);
    ctx.stroke();
    positionWavePlayhead(ctx, audio, view, padX, plotW, h);
    return;
  }
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

  if (!duration) {
    positionWavePlayhead(ctx, audio, view, padX, plotW, h);
    return;
  }

  // Bar “1” grid (under cues so markers stay readable)
  drawBeatOnes(ctx, track, view, padX, plotW, h);

  // Honor Both / Cues / Loops tabs on the waveform too.
  const points = filteredCuePoints(track?.cues?.points || []);
  const bpm = trackBpm(track);

  // Loop bands first (full duration translucent fill)
  // Apply live preview position while dragging a loop.
  const drag = state.loopDrag;
  for (const p of points) {
    if (pointKind(p) !== "loop") continue;
    let start = Number(p.pos) || 0;
    if (
      drag &&
      (drag.kind || "loop") === "loop" &&
      drag.previewPos != null &&
      Math.abs(Number(drag.originPos) - start) < 0.02 &&
      (drag.point?.name === p.name || drag.point?.slot === p.slot)
    ) {
      start = Number(drag.previewPos);
    }
    const len = loopDurationSeconds(p, bpm);
    if (len <= 0) continue;
    const end = start + len;
    // Skip if entirely outside the visible window
    if (end < view.start || start > view.end) continue;

    const x0 = timeToWaveX(Math.max(start, view.start), padX, plotW, view);
    const x1 = timeToWaveX(Math.min(end, view.end), padX, plotW, view);
    const width = Math.max(2, x1 - x0);
    const draggingThis =
      drag &&
      (drag.kind || "loop") === "loop" &&
      Math.abs(Number(drag.originPos) - (Number(p.pos) || 0)) < 0.02;
    ctx.save();
    ctx.fillStyle = cueRgba(p.color_name, draggingThis ? 0.38 : 0.22);
    ctx.fillRect(x0, 4, width, h - 8);
    // Soft edges
    ctx.strokeStyle = cueRgba(p.color_name, draggingThis ? 0.85 : 0.45);
    ctx.lineWidth = draggingThis ? 2 : 1;
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
    let t = Number(p.pos) || 0;
    if (
      drag &&
      drag.previewPos != null &&
      (drag.kind || "loop") === kind &&
      Math.abs(Number(drag.originPos) - t) < 0.02
    ) {
      t = Number(drag.previewPos);
    }
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
    const draggingCue =
      kind === "cue" &&
      drag &&
      drag.kind === "cue" &&
      Math.abs(Number(drag.originPos) - (Number(p.pos) || 0)) < 0.02;
    ctx.lineWidth = kind === "loop" ? 1.5 : draggingCue ? 3 : 2;
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

  // Ghost marker while placing a cue
  const ghost = Number(state.placeCuePreview);
  if (
    state.placeCueMode &&
    Number.isFinite(ghost) &&
    ghost >= view.start - 0.05 &&
    ghost <= view.end + 0.05
  ) {
    const gx = timeToWaveX(ghost, padX, plotW, view);
    ctx.save();
    ctx.strokeStyle = "rgba(52, 211, 153, 0.9)";
    ctx.lineWidth = 2;
    ctx.setLineDash([4, 3]);
    ctx.beginPath();
    ctx.moveTo(gx, 10);
    ctx.lineTo(gx, h - 10);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "rgba(10, 14, 22, 0.78)";
    ctx.fillRect(gx + 4, 8, 86, 14);
    ctx.fillStyle = "#6ee7b7";
    ctx.font = "10px SF Pro Text, system-ui, sans-serif";
    ctx.fillText(`Cue @ ${fmtTime(ghost)}`, gx + 6, 18);
    ctx.restore();
  }

  const loopGhost = Number(state.placeLoopPreview);
  if (
    state.placeLoopMode &&
    Number.isFinite(loopGhost) &&
    loopGhost >= view.start - 0.05 &&
    loopGhost <= view.end + 0.05
  ) {
    const bpm = onesBpm(track) || trackBpm(track);
    const len = bpm && bpm > 0 ? (60 / bpm) * 8 : 0;
    const gx = timeToWaveX(loopGhost, padX, plotW, view);
    ctx.save();
    if (len > 0) {
      const x1 = timeToWaveX(loopGhost + len, padX, plotW, view);
      ctx.fillStyle = "rgba(168, 85, 247, 0.22)";
      ctx.fillRect(gx, 4, Math.max(2, x1 - gx), h - 8);
    }
    ctx.strokeStyle = "rgba(168, 85, 247, 0.95)";
    ctx.lineWidth = 2;
    ctx.setLineDash([4, 3]);
    ctx.beginPath();
    ctx.moveTo(gx, 10);
    ctx.lineTo(gx, h - 10);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "rgba(10, 14, 22, 0.78)";
    ctx.fillRect(gx + 4, 8, 92, 14);
    ctx.fillStyle = "#d8b4fe";
    ctx.font = "10px SF Pro Text, system-ui, sans-serif";
    ctx.fillText(`Loop 8b @ ${fmtTime(loopGhost)}`, gx + 6, 18);
    ctx.restore();
  }

  // Playhead — follow while moving so it never vanishes at a zoom width
  positionWavePlayhead(ctx, audio, view, padX, plotW, h);

  // Zoom / window chrome — cues outside this slice stay as chips + overview ticks
  if (state.waveZoom > 1.01) {
    ctx.fillStyle = "rgba(10, 14, 22, 0.72)";
    ctx.fillRect(padX, h - 34, 168, 16);
    ctx.fillStyle = "rgba(232, 237, 247, 0.9)";
    ctx.font = "11px SF Pro Text, system-ui, sans-serif";
    ctx.fillText(
      `${state.waveZoom.toFixed(1)}×  ${fmtTime(view.start)}–${fmtTime(view.end)}`,
      padX + 6,
      h - 22
    );
  }
  const classified = classifyWaveMarkers(points, view);
  drawWaveCueOverview(ctx, points, view, duration, padX, plotW, h);
  drawOffscreenCueHints(ctx, classified, padX, plotW, h);
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

function snapshotWaveSeekTime(clientX) {
  const wrap = $("waveformWrap");
  const track = currentTrack();
  const audio = $("audio");
  if (!wrap || !track) {
    state.waveSeekTime = null;
    return;
  }
  const duration = waveformDuration(track, audio);
  if (!duration) {
    state.waveSeekTime = null;
    return;
  }
  const rect = wrap.getBoundingClientRect();
  state.waveSeekTime = clientXToTime(clientX, rect, duration);
}

function seekFromWaveformEvent(e) {
  const chrome = hitTestWaveCueChrome(e.clientX, e.clientY);
  if (chrome) {
    state.waveSeekTime = null;
    if (chrome.time != null && Number.isFinite(Number(chrome.time))) {
      panWaveToTime(chrome.time, {
        frac: chrome.kind === "overview" ? 0.5 : 0.22,
      });
    }
    return;
  }
  // In align mode, drag moves the grid — don't seek.
  if (state.gridAlignMode) {
    state.waveSeekTime = null;
    return;
  }
  // After a loop/cue drag, suppress seek (click fires after pointerup).
  if (state.loopDrag?.moved || state._suppressWaveSeek) {
    state._suppressWaveSeek = false;
    state.waveSeekTime = null;
    return;
  }
  const wrap = $("waveformWrap");
  const track = currentTrack();
  const audio = $("audio");
  if (!wrap || !track) return;
  const duration = waveformDuration(track, audio);
  if (!duration) return;
  const snapped = Number(state.waveSeekTime);
  state.waveSeekTime = null;
  const t = Number.isFinite(snapped)
    ? snapped
    : clientXToTime(e.clientX, wrap.getBoundingClientRect(), duration);
  if (state.placeLoopMode) {
    if (e.detail > 1 || state.placeLoopInFlight) return;
    const pos = snapCueDragTime(t, { free: Boolean(e.shiftKey) });
    const existing = existingLoopNear(pos);
    if (existing) {
      jumpToCue(Number(existing.pos) || pos, existing);
      return;
    }
    placeLoopAtTime(pos, { free: Boolean(e.shiftKey), alreadySnapped: true });
    return;
  }
  if (state.placeCueMode || e.altKey) {
    if (e.detail > 1 || state.placeCueInFlight) return;
    const pos = snapCueDragTime(t, { free: Boolean(e.shiftKey) });
    const existing = existingCueNear(pos);
    if (existing) {
      jumpToCue(Number(existing.pos) || pos, existing);
      return;
    }
    placeCueAtTime(pos, { free: Boolean(e.shiftKey), alreadySnapped: true });
    return;
  }
  jumpToCue(t);
}

/** Snap time to nearest beat (or free if no BPM / Shift held). */
function snapLoopDragTime(t, { free = false } = {}) {
  if (free) return Math.max(0, t);
  const track = currentTrack();
  const bpm = onesBpm(track) || trackBpm(track);
  if (!bpm || bpm <= 0) return Math.max(0, t);
  const beatSec = 60 / bpm;
  const anchor = gridAnchorSeconds(track);
  const steps = Math.round((t - anchor) / beatSec);
  return Math.max(0, anchor + steps * beatSec);
}

/** Snap time to the nearest bar 1 (downbeat). Shift / free skips snap. */
function snapCueDragTime(t, { free = false } = {}) {
  if (free) return Math.max(0, t);
  const track = currentTrack();
  const barSec = barPeriodSeconds(track);
  if (!barSec || barSec <= 0) {
    return snapLoopDragTime(t, { free: false });
  }
  const anchor = gridAnchorSeconds(track);
  const steps = Math.round((t - anchor) / barSec);
  return Math.max(0, anchor + steps * barSec);
}

function snapMarkerDragTime(t, { kind = "loop", free = false } = {}) {
  return kind === "cue"
    ? snapCueDragTime(t, { free })
    : snapLoopDragTime(t, { free });
}

/** Unfiltered cue sitting on this time (any tab). */
function existingCueNear(pos, tol = 0.03) {
  const points = currentTrack()?.cues?.points || [];
  const target = Number(pos);
  if (!Number.isFinite(target)) return null;
  return (
    points.find(
      (p) =>
        pointKind(p) === "cue" &&
        Math.abs((Number(p.pos) || 0) - target) <= tol
    ) || null
  );
}

function existingLoopNear(pos, tol = 0.03) {
  const points = currentTrack()?.cues?.points || [];
  const target = Number(pos);
  if (!Number.isFinite(target)) return null;
  return (
    points.find(
      (p) =>
        pointKind(p) === "loop" &&
        Math.abs((Number(p.pos) || 0) - target) <= tol
    ) || null
  );
}

/**
 * Hit-test cue start lines under the cursor (~10px).
 * Returns { point, hit: 'start' } or null.
 */
function hitTestCueAtClientX(clientX) {
  const wrap = $("waveformWrap");
  const track = currentTrack();
  const audio = $("audio");
  if (!wrap || !track) return null;
  const duration = waveformDuration(track, audio);
  if (!duration) return null;
  const rect = wrap.getBoundingClientRect();
  const { padX, plotW } = wavePlotMetrics(rect.width);
  const view = waveViewWindow(duration);
  const x = clientX - rect.left;

  const cues = filteredCuePoints(track.cues?.points || []).filter(
    (p) => pointKind(p) === "cue"
  );
  let best = null;
  let bestDist = 10;
  for (const p of cues) {
    const start = Number(p.pos) || 0;
    const sx = timeToWaveX(start, padX, plotW, view);
    const d = Math.abs(x - sx);
    if (d < bestDist) {
      bestDist = d;
      best = p;
    }
  }
  if (best) return { point: best, hit: "start", kind: "cue" };
  return null;
}

/**
 * Hit-test loops under the cursor. Prefers start handle, then body of loop band.
 * Returns { point, hit: 'start'|'body' } or null.
 */
function hitTestLoopAtClientX(clientX) {
  const wrap = $("waveformWrap");
  const track = currentTrack();
  const audio = $("audio");
  if (!wrap || !track) return null;
  const duration = waveformDuration(track, audio);
  if (!duration) return null;
  const rect = wrap.getBoundingClientRect();
  const { padX, plotW } = wavePlotMetrics(rect.width);
  const view = waveViewWindow(duration);
  const t = clientXToTime(clientX, rect, duration);
  const bpm = trackBpm(track);
  const x = clientX - rect.left;

  const loops = filteredCuePoints(track.cues?.points || []).filter(
    (p) => pointKind(p) === "loop"
  );
  // Prefer start handle within ~10px
  let bestStart = null;
  let bestStartDist = 12;
  for (const p of loops) {
    const start = Number(p.pos) || 0;
    const sx = timeToWaveX(start, padX, plotW, view);
    const d = Math.abs(x - sx);
    if (d < bestStartDist) {
      bestStartDist = d;
      bestStart = p;
    }
  }
  if (bestStart) return { point: bestStart, hit: "start", kind: "loop" };

  // Else body of loop region
  for (const p of loops) {
    const start = Number(p.pos) || 0;
    const len = loopDurationSeconds(p, bpm);
    if (len <= 0) continue;
    if (t >= start - 0.02 && t <= start + len + 0.02) {
      return { point: p, hit: "body", kind: "loop" };
    }
  }
  return null;
}

function onLoopDragPointerDown(e) {
  if (state.gridAlignMode) return false;
  if (e.button != null && e.button !== 0) return false;
  // Don't steal events from buttons/selects
  if (e.target?.closest?.("button, select, a, input, label")) return false;
  // Alt+click places a cue — don't start a drag.
  if (e.altKey) return false;

  const hit = hitTestCueAtClientX(e.clientX) || hitTestLoopAtClientX(e.clientX);
  if (!hit) return false;

  const wrap = $("waveformWrap");
  const track = currentTrack();
  const audio = $("audio");
  const duration = waveformDuration(track, audio);
  if (!wrap || !duration) return false;
  const rect = wrap.getBoundingClientRect();
  const t = clientXToTime(e.clientX, rect, duration);
  const originPos = Number(hit.point.pos) || 0;
  const kind = hit.kind || pointKind(hit.point);

  state.loopDrag = {
    kind,
    point: { ...hit.point },
    originPos,
    previewPos: originPos,
    grabOffset: t - originPos, // keep relative grab within band
    pointerId: e.pointerId,
    moved: false,
    free: Boolean(e.shiftKey),
  };
  wrap.classList.add(kind === "cue" ? "cue-dragging" : "loop-dragging");
  try {
    wrap.setPointerCapture?.(e.pointerId);
  } catch {
    /* ignore */
  }
  e.preventDefault();
  return true;
}

function onLoopDragPointerMove(e) {
  const drag = state.loopDrag;
  if (!drag) return;
  const wrap = $("waveformWrap");
  const track = currentTrack();
  const audio = $("audio");
  if (!wrap || !track) return;
  const duration = waveformDuration(track, audio) || 0;
  const rect = wrap.getBoundingClientRect();
  const t = clientXToTime(e.clientX, rect, duration || 1);
  let next = t - (Number(drag.grabOffset) || 0);
  next = snapMarkerDragTime(next, {
    kind: drag.kind || "loop",
    free: drag.free || e.shiftKey,
  });
  if (duration > 0) next = Math.min(next, Math.max(0, duration - 0.05));
  if (Math.abs(next - drag.originPos) > 0.01) drag.moved = true;
  drag.previewPos = next;
  drawWaveform();
  e.preventDefault();
}

async function onLoopDragPointerUp(e) {
  const drag = state.loopDrag;
  if (!drag) return;
  const wrap = $("waveformWrap");
  wrap?.classList.remove("loop-dragging", "cue-dragging");
  try {
    wrap?.releasePointerCapture?.(e.pointerId);
  } catch {
    /* ignore */
  }

  const origin = Number(drag.originPos) || 0;
  const next = Number(drag.previewPos);
  const kind = drag.kind || "loop";
  state.loopDrag = null;

  if (!drag.moved || !Number.isFinite(next) || Math.abs(next - origin) < 0.015) {
    drawWaveform();
    return;
  }

  state._suppressWaveSeek = true;
  if (kind === "cue") {
    await commitCueMove(drag.point, origin, next);
  } else {
    await commitLoopMove(drag.point, origin, next);
  }
}

async function commitLoopMove(point, originPos, newPos) {
  const track = currentTrack();
  if (!track || !point) return;
  const path = track.path;
  const gen = state.trackGen;

  let allowRunning = false;
  if (await isVdjRunningFresh()) {
    allowRunning = await showConfirmDialog({
      title: "VirtualDJ is still open",
      track: trackDisplayTitle(track),
      message:
        "Moving a loop may be overwritten when VirtualDJ quits. Close it first when possible.",
      confirmLabel: "Move anyway",
      tone: "warning",
    });
    if (!allowRunning) {
      setStatus("Close VirtualDJ, then move the loop.", "error");
      if (stillOnTrack(path, gen)) drawWaveform();
      return;
    }
  }

  try {
    setStatus(
      `Moving loop “${point.name || "Loop"}” ${fmtTime(originPos)} → ${fmtTime(newPos)}…`
    );
    const data = await api("/api/move-poi", {
      method: "POST",
      body: JSON.stringify({
        path,
        kind: "loop",
        pos: originPos,
        new_pos: newPos,
        num: point.num != null ? String(point.num) : null,
        name: point.name || null,
        slot: point.slot != null ? String(point.slot) : null,
        allow_vdj_running: Boolean(allowRunning),
      }),
    });
    const r = data.result || {};
    if (r.cues) {
      applyCueSummaryToTrack(path, r.cues);
    } else {
      const idx = state.tracks.findIndex((t) => t.path === path);
      const snap = idx >= 0 ? state.tracks[idx] : null;
      if (snap?.cues?.points) {
        const points = snap.cues.points.map((p) => {
          if (
            pointKind(p) === "loop" &&
            Math.abs(Number(p.pos) - originPos) < 0.02
          ) {
            return { ...p, pos: newPos };
          }
          return p;
        });
        applyCueSummaryToTrack(path, { ...snap.cues, points });
      }
    }

    if (!stillOnTrack(path, gen)) {
      setStatus(`Loop moved on ${track.name} (switched tracks)`, "success");
      return;
    }

    const updated =
      (currentTrack()?.cues?.points || []).find(
        (p) =>
          pointKind(p) === "loop" && Math.abs(Number(p.pos) - newPos) < 0.02
      ) || { ...point, pos: newPos };

    if (state.activeLoopKey) {
      state.activeLoopKey = cueKey(updated);
      state.activeCueKey = cueKey(updated);
    }

    renderCues();
    drawWaveform();
    setStatus(
      `Loop “${updated.name || "Loop"}” → ${fmtTime(newPos)}` +
        (updated.size ? ` · ${updated.size}b` : ""),
      "success"
    );
    auditionLoopPoint(updated);
  } catch (err) {
    setStatus(err.message, "error");
    if (stillOnTrack(path, gen)) drawWaveform();
  }
}

async function commitCueMove(point, originPos, newPos) {
  const track = currentTrack();
  if (!track || !point) return;
  const path = track.path;
  const gen = state.trackGen;

  let allowRunning = false;
  if (await isVdjRunningFresh()) {
    allowRunning = await showConfirmDialog({
      title: "VirtualDJ is still open",
      track: trackDisplayTitle(track),
      message:
        "Moving a cue may be overwritten when VirtualDJ quits. Close it first when possible.",
      confirmLabel: "Move anyway",
      tone: "warning",
    });
    if (!allowRunning) {
      setStatus("Close VirtualDJ, then move the cue.", "error");
      if (stillOnTrack(path, gen)) drawWaveform();
      return;
    }
  }

  try {
    setStatus(
      `Moving cue “${point.name || "Cue"}” ${fmtTime(originPos)} → ${fmtTime(newPos)}…`
    );
    const data = await api("/api/move-poi", {
      method: "POST",
      body: JSON.stringify({
        path,
        kind: "cue",
        pos: originPos,
        new_pos: newPos,
        num: point.num != null ? String(point.num) : null,
        name: point.name || null,
        allow_vdj_running: Boolean(allowRunning),
      }),
    });
    const r = data.result || {};
    if (r.cues) {
      applyCueSummaryToTrack(path, r.cues);
    } else {
      const idx = state.tracks.findIndex((t) => t.path === path);
      const snap = idx >= 0 ? state.tracks[idx] : null;
      if (snap?.cues?.points) {
        const points = snap.cues.points.map((p) => {
          if (
            pointKind(p) === "cue" &&
            Math.abs(Number(p.pos) - originPos) < 0.02
          ) {
            return { ...p, pos: newPos };
          }
          return p;
        });
        applyCueSummaryToTrack(path, { ...snap.cues, points });
      }
    }

    if (!stillOnTrack(path, gen)) {
      setStatus(`Cue moved on ${track.name} (switched tracks)`, "success");
      return;
    }

    const updated =
      (currentTrack()?.cues?.points || []).find(
        (p) =>
          pointKind(p) === "cue" && Math.abs(Number(p.pos) - newPos) < 0.02
      ) || { ...point, pos: newPos };

    state.activeCueKey = cueKey(updated);
    renderCues();
    drawWaveform();
    setStatus(
      `Cue “${updated.name || "Cue"}” → ${fmtTime(newPos)} (on the 1)`,
      "success"
    );
    jumpToCue(newPos, updated);
  } catch (err) {
    setStatus(err.message, "error");
    if (stillOnTrack(path, gen)) drawWaveform();
  }
}

function syncPlaceCueUi() {
  const btn = $("placeCueBtn");
  if (btn) {
    btn.classList.toggle("active", state.placeCueMode);
    btn.setAttribute("aria-pressed", state.placeCueMode ? "true" : "false");
    btn.textContent = state.placeCueMode ? "Placing…" : "Place cue";
  }
  const bar = $("placeCueBar");
  if (bar) {
    if (state.placeCueMode) {
      bar.hidden = false;
      bar.removeAttribute("hidden");
    } else {
      bar.hidden = true;
      bar.setAttribute("hidden", "");
    }
  }
  const wrap = $("waveformWrap");
  if (wrap) wrap.classList.toggle("place-cue-mode", state.placeCueMode);
  if (!state.placeCueMode) {
    state.placeCuePreview = null;
    wrap?.classList.remove("place-cue-mode");
  }
}

function togglePlaceCueMode() {
  if (state.placeCueMode) {
    cancelPlaceCueMode();
    return;
  }
  if (state.placeLoopMode) cancelPlaceLoopMode();
  if (state.gridAlignMode) exitGridAlignMode({ restoreView: false });
  const track = currentTrack();
  if (!track) {
    setStatus("Select a track first.", "error");
    return;
  }
  state.placeCueMode = true;
  state.placeCuePreview = null;
  syncPlaceCueUi();
  drawWaveform();
  setStatus("Click the wave to place a cue on the 1. Shift = free. Esc cancels.");
}

function cancelPlaceCueMode() {
  if (!state.placeCueMode) return;
  state.placeCueMode = false;
  state.placeCuePreview = null;
  syncPlaceCueUi();
  drawWaveform();
}

function updatePlaceCuePreview(clientX, free = false) {
  if (!state.placeCueMode) return;
  const wrap = $("waveformWrap");
  const track = currentTrack();
  const audio = $("audio");
  if (!wrap || !track) return;
  const duration = waveformDuration(track, audio);
  if (!duration) return;
  const rect = wrap.getBoundingClientRect();
  const t = clientXToTime(clientX, rect, duration);
  state.placeCuePreview = snapCueDragTime(t, { free: Boolean(free) });
  drawWaveform();
}

async function placeCueAtTime(rawTime, { free = false, alreadySnapped = false } = {}) {
  const track = currentTrack();
  if (!track || state.placeCueInFlight) return;
  const path = track.path;
  const gen = state.trackGen;
  const audio = $("audio");
  const duration = waveformDuration(track, audio) || trackDuration(track, audio);
  let pos = alreadySnapped
    ? Math.max(0, Number(rawTime) || 0)
    : snapCueDragTime(Number(rawTime) || 0, { free });
  if (duration > 0) pos = Math.min(pos, Math.max(0, duration - 0.05));
  const existing = existingCueNear(pos);
  if (existing) {
    jumpToCue(Number(existing.pos) || pos, existing);
    return;
  }

  let allowRunning = false;
  if (await isVdjRunningFresh()) {
    allowRunning = await showConfirmDialog({
      title: "VirtualDJ is still open",
      track: trackDisplayTitle(track),
      message:
        "Adding a cue may be overwritten when VirtualDJ quits. Close it first when possible.",
      confirmLabel: "Place anyway",
      tone: "warning",
    });
    if (!allowRunning) {
      setStatus("Close VirtualDJ, then place the cue.", "error");
      return;
    }
  }

  state.placeCueInFlight = true;
  try {
    setStatus(`Placing cue at ${fmtTime(pos)}…`);
    const data = await api("/api/add-cue", {
      method: "POST",
      body: JSON.stringify({
        path,
        pos,
        color: "green",
        allow_vdj_running: Boolean(allowRunning),
      }),
    });
    const r = data.result || {};
    if (r.cues) {
      applyCueSummaryToTrack(path, r.cues);
    }

    if (!stillOnTrack(path, gen)) {
      setStatus(`Cue placed on ${track.name} (switched tracks)`, "success");
      return;
    }

    const placed =
      (currentTrack()?.cues?.points || []).find(
        (p) =>
          pointKind(p) === "cue" && Math.abs(Number(p.pos) - pos) < 0.03
      ) || { name: r.change?.name, pos, kind: "cue", num: r.change?.num };

    state.activeCueKey = cueKey(placed);
    renderCues();
    drawWaveform();
    setStatus(
      `Placed “${placed.name || "Cue"}” at ${fmtTime(pos)}` +
        (free ? " (free)" : " (on the 1)"),
      "success"
    );
    jumpToCue(pos, placed);
  } catch (err) {
    setStatus(err.message, "error");
  } finally {
    state.placeCueInFlight = false;
  }
}

function syncPlaceLoopUi() {
  const btn = $("placeLoopBtn");
  if (btn) {
    btn.classList.toggle("active", state.placeLoopMode);
    btn.setAttribute("aria-pressed", state.placeLoopMode ? "true" : "false");
    btn.textContent = state.placeLoopMode ? "Placing loop…" : "Place loop";
  }
  const bar = $("placeLoopBar");
  if (bar) {
    if (state.placeLoopMode) {
      bar.hidden = false;
      bar.removeAttribute("hidden");
    } else {
      bar.hidden = true;
      bar.setAttribute("hidden", "");
    }
  }
  const wrap = $("waveformWrap");
  if (wrap) wrap.classList.toggle("place-loop-mode", state.placeLoopMode);
  if (!state.placeLoopMode) {
    state.placeLoopPreview = null;
    wrap?.classList.remove("place-loop-mode");
  }
}

function togglePlaceLoopMode() {
  if (state.placeLoopMode) {
    cancelPlaceLoopMode();
    return;
  }
  if (state.placeCueMode) cancelPlaceCueMode();
  if (state.gridAlignMode) exitGridAlignMode({ restoreView: false });
  const track = currentTrack();
  if (!track) {
    setStatus("Select a track first.", "error");
    return;
  }
  state.placeLoopMode = true;
  state.placeLoopPreview = null;
  syncPlaceLoopUi();
  drawWaveform();
  setStatus("Click the wave to place an 8-beat loop on the 1. Shift = free. Esc cancels.");
}

function cancelPlaceLoopMode() {
  if (!state.placeLoopMode) return;
  state.placeLoopMode = false;
  state.placeLoopPreview = null;
  syncPlaceLoopUi();
  drawWaveform();
}

function updatePlaceLoopPreview(clientX, free = false) {
  if (!state.placeLoopMode) return;
  const wrap = $("waveformWrap");
  const track = currentTrack();
  const audio = $("audio");
  if (!wrap || !track) return;
  const duration = waveformDuration(track, audio);
  if (!duration) return;
  const rect = wrap.getBoundingClientRect();
  const t = clientXToTime(clientX, rect, duration);
  state.placeLoopPreview = snapCueDragTime(t, { free: Boolean(free) });
  drawWaveform();
}

async function placeLoopAtTime(rawTime, { free = false, alreadySnapped = false } = {}) {
  const track = currentTrack();
  if (!track || state.placeLoopInFlight) return;
  const path = track.path;
  const gen = state.trackGen;
  const audio = $("audio");
  const duration = waveformDuration(track, audio) || trackDuration(track, audio);
  let pos = alreadySnapped
    ? Math.max(0, Number(rawTime) || 0)
    : snapCueDragTime(Number(rawTime) || 0, { free });
  if (duration > 0) pos = Math.min(pos, Math.max(0, duration - 0.05));
  const existing = existingLoopNear(pos);
  if (existing) {
    jumpToCue(Number(existing.pos) || pos, existing);
    return;
  }

  let allowRunning = false;
  if (await isVdjRunningFresh()) {
    allowRunning = await showConfirmDialog({
      title: "VirtualDJ is still open",
      track: trackDisplayTitle(track),
      message:
        "Adding a loop may be overwritten when VirtualDJ quits. Close it first when possible.",
      confirmLabel: "Place anyway",
      tone: "warning",
    });
    if (!allowRunning) {
      setStatus("Close VirtualDJ, then place the loop.", "error");
      return;
    }
  }

  state.placeLoopInFlight = true;
  try {
    setStatus(`Placing 8-beat loop at ${fmtTime(pos)}…`);
    const data = await api("/api/add-loop", {
      method: "POST",
      body: JSON.stringify({
        path,
        pos,
        color: "green",
        beats: 8,
        allow_vdj_running: Boolean(allowRunning),
      }),
    });
    const r = data.result || {};
    if (r.cues) {
      applyCueSummaryToTrack(path, r.cues);
    }

    if (!stillOnTrack(path, gen)) {
      setStatus(`Loop placed on ${track.name} (switched tracks)`, "success");
      return;
    }

    const placed =
      (currentTrack()?.cues?.points || []).find(
        (p) =>
          pointKind(p) === "loop" && Math.abs(Number(p.pos) - pos) < 0.03
      ) || {
        name: r.change?.name,
        pos,
        kind: "loop",
        size: r.change?.beats,
        slot: r.change?.slot,
      };

    state.activeLoopKey = cueKey(placed);
    state.activeCueKey = cueKey(placed);
    renderCues();
    drawWaveform();
    setStatus(
      `Placed loop “${placed.name || "Loop"}” at ${fmtTime(pos)} · 8b` +
        (free ? " (free)" : " (on the 1)"),
      "success"
    );
    jumpToCue(pos, placed);
  } catch (err) {
    setStatus(err.message, "error");
  } finally {
    state.placeLoopInFlight = false;
  }
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
    state.waveViewPinned = true;
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
  // Keep the cursor-centered slice; follow resumes once the needle is in view.
  state.waveViewPinned = true;
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
      const sizeBeats = Number(p.size);
      const canHalve = kind === "loop" && Number.isFinite(sizeBeats) && sizeBeats > 1.01;
      const canDouble =
        kind === "loop" && Number.isFinite(sizeBeats) && sizeBeats < 255;
      const kindLabel =
        kind === "loop"
          ? `loop${p.size ? ` ${p.size}b` : ""}${isLooping ? " · ON" : ""}`
          : `cue ${p.num || ""}`.trim();
      const loopScaleBtns =
        kind === "loop"
          ? `
          <button
            type="button"
            class="btn ghost cue-loop-scale-btn"
            data-index="${i}"
            data-factor="0.5"
            ${canHalve ? "" : "disabled"}
            title="Halve loop length in VirtualDJ and audition"
            aria-label="Halve loop ${escapeHtml(p.name || "")}"
          >½</button>
          <button
            type="button"
            class="btn ghost cue-loop-scale-btn"
            data-index="${i}"
            data-factor="2"
            ${canDouble ? "" : "disabled"}
            title="Double loop length in VirtualDJ and audition"
            aria-label="Double loop ${escapeHtml(p.name || "")}"
          >×2</button>`
          : "";
      const currentColor = sanitizeColorName(p.color_name);
      const colorOpts = CUE_COLOR_OPTIONS.map(
        (c) =>
          `<option value="${c.id}" ${
            currentColor === c.id ? "selected" : ""
          }>${c.label}</option>`
      ).join("");
      const unknownOpt =
        currentColor && !CUE_COLOR_OPTIONS.some((c) => c.id === currentColor)
          ? `<option value="${escapeHtml(currentColor)}" selected>${escapeHtml(
              currentColor
            )}</option>`
          : "";
      return `
        <div class="cue-row ${
          state.activeCueKey === key ? "active" : ""
        } ${isLooping ? "looping" : ""}" data-key="${escapeHtml(key)}" data-pos="${p.pos}" data-index="${i}" data-kind="${kind}">
          <button type="button" class="cue-row-main" data-index="${i}" title="Jump to marker">
            <span class="cue-dot ${kind} color-${sanitizeColorName(p.color_name)}"></span>
            <span class="cue-time">${fmtTime(p.pos)}</span>
            <span
              class="cue-name"
              data-index="${i}"
              role="button"
              tabindex="0"
              title="Click to rename"
            >${escapeHtml(p.name || (kind === "loop" ? "Loop" : "Cue"))}</span>
            <span class="cue-kind">${escapeHtml(kindLabel)} ${hotkey}</span>
          </button>
          <div class="cue-row-actions">
            <label class="cue-color-label" title="Change marker color in VirtualDJ">
              <span class="visually-hidden">Color</span>
              <select
                class="cue-color-select"
                data-index="${i}"
                aria-label="Color for ${escapeHtml(p.name || kind)}"
              >
                ${unknownOpt}
                ${colorOpts}
              </select>
            </label>
            ${loopScaleBtns}
            <button
              type="button"
              class="btn ghost danger cue-delete-btn"
              data-index="${i}"
              title="Delete this ${kind === "loop" ? "loop" : "cue"} from VirtualDJ"
              aria-label="Delete ${escapeHtml(p.name || kind)}"
            >✕</button>
          </div>
        </div>`;
    })
    .join("");

  list.querySelectorAll(".cue-row-main").forEach((row) => {
    row.addEventListener("click", (e) => {
      // Name text has its own rename handler.
      if (e.target.closest(".cue-name")) return;
      const idx = Number(row.dataset.index);
      const point = points[idx];
      jumpToCue(point?.pos ?? points[idx]?.pos, point);
    });
  });
  list.querySelectorAll(".cue-name").forEach((el) => {
    el.addEventListener("click", (e) => {
      e.stopPropagation();
      e.preventDefault();
      const idx = Number(el.dataset.index);
      const point = points[idx];
      if (point) beginRenamePoi(point, el);
    });
    el.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        e.stopPropagation();
        const idx = Number(el.dataset.index);
        const point = points[idx];
        if (point) beginRenamePoi(point, el);
      }
    });
  });
  list.querySelectorAll(".cue-color-select").forEach((sel) => {
    sel.addEventListener("click", (e) => e.stopPropagation());
    sel.addEventListener("mousedown", (e) => e.stopPropagation());
    sel.addEventListener("change", (e) => {
      e.stopPropagation();
      const idx = Number(sel.dataset.index);
      const point = points[idx];
      const color = sel.value;
      if (point && color) setCueColor(point, color, sel);
    });
  });
  list.querySelectorAll(".cue-loop-scale-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const idx = Number(btn.dataset.index);
      const factor = Number(btn.dataset.factor);
      const point = points[idx];
      if (point && (factor === 0.5 || factor === 2)) scaleLoopPoint(point, factor);
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
 * Inline rename: click cue/loop name text → input → Enter/blur saves, Esc cancels.
 */
function beginRenamePoi(point, nameEl) {
  const track = currentTrack();
  if (!track || !point || !nameEl || nameEl.dataset.editing === "1") return;

  const kind = pointKind(point);
  const prevName = String(point.name || "").trim();
  nameEl.dataset.editing = "1";

  const input = document.createElement("input");
  input.type = "text";
  input.className = "cue-name-input";
  input.value = prevName;
  input.maxLength = 120;
  input.setAttribute(
    "aria-label",
    `Rename ${kind === "loop" ? "loop" : "cue"}`
  );
  input.title = "Enter to save · Esc to cancel";

  const parent = nameEl.parentNode;
  parent.replaceChild(input, nameEl);
  input.focus();
  input.select();

  let finished = false;
  const restore = (text) => {
    if (finished) return;
    finished = true;
    // Full list re-render is safest after save; for cancel rebuild the span.
    if (text == null) {
      renderCues();
      return;
    }
    renderCues();
  };

  const commit = async () => {
    if (finished) return;
    const next = String(input.value || "").trim();
    if (!next) {
      setStatus("Name cannot be empty", "error");
      input.focus();
      return;
    }
    if (next === prevName) {
      finished = true;
      restore(null);
      return;
    }
    finished = true;
    input.disabled = true;
    await renamePoiPoint(point, next, prevName);
  };

  const cancel = () => {
    if (finished) return;
    finished = true;
    restore(null);
  };

  input.addEventListener("keydown", (e) => {
    e.stopPropagation();
    if (e.key === "Enter") {
      e.preventDefault();
      commit();
    } else if (e.key === "Escape") {
      e.preventDefault();
      cancel();
    }
  });
  input.addEventListener("click", (e) => e.stopPropagation());
  input.addEventListener("mousedown", (e) => e.stopPropagation());
  input.addEventListener("blur", () => {
    // Defer so Enter can mark finished first.
    setTimeout(() => {
      if (!finished) commit();
    }, 0);
  });
}

async function renamePoiPoint(point, newName, prevName) {
  const track = currentTrack();
  if (!track || !point) return;
  const path = track.path;
  const gen = state.trackGen;
  const kind = pointKind(point);
  const label = prevName || kind;

  let allowRunning = false;
  if (await isVdjRunningFresh()) {
    allowRunning = await showConfirmDialog({
      title: "VirtualDJ is still open",
      track: trackDisplayTitle(track),
      message:
        "Renames may be overwritten when VirtualDJ quits. Close it first when possible.",
      confirmLabel: "Rename anyway",
      tone: "warning",
    });
    if (!allowRunning) {
      if (stillOnTrack(path, gen)) renderCues();
      setStatus("Close VirtualDJ, then rename the marker.", "error");
      return;
    }
  }

  try {
    setStatus(`Renaming ${kind}: “${label}” → “${newName}”…`);
    const data = await api("/api/rename-poi", {
      method: "POST",
      body: JSON.stringify({
        path,
        kind,
        pos: Number(point.pos) || 0,
        new_name: newName,
        num: point.num != null ? String(point.num) : null,
        name: point.name || null,
        slot: point.slot != null ? String(point.slot) : null,
        allow_vdj_running: Boolean(allowRunning),
      }),
    });
    const r = data.result || {};
    if (r.cues) {
      applyCueSummaryToTrack(path, r.cues);
    } else {
      const idx = state.tracks.findIndex((t) => t.path === path);
      const snap = idx >= 0 ? state.tracks[idx] : null;
      if (snap?.cues?.points) {
        const key = cueKey(point);
        const points = snap.cues.points.map((p) =>
          cueKey(p) === key ? { ...p, name: newName } : p
        );
        applyCueSummaryToTrack(path, { ...snap.cues, points });
      }
    }
    if (!stillOnTrack(path, gen)) {
      setStatus(`Renamed ${kind} on other track`, "success");
      return;
    }
    renderCues();
    drawWaveform();
    setStatus(
      `Renamed ${kind}: “${label}” → “${newName}”`,
      "success"
    );
  } catch (err) {
    if (stillOnTrack(path, gen)) renderCues();
    setStatus(err.message, "error");
  }
}

async function setCueColor(point, color, selectEl) {
  const track = currentTrack();
  if (!track || !point || !color) return;
  const path = track.path;
  const gen = state.trackGen;
  const kind = pointKind(point);
  const safeColor = sanitizeColorName(color);
  const prev = sanitizeColorName(point.color_name);
  if (prev === safeColor) return;

  let allowRunning = false;
  if (await isVdjRunningFresh()) {
    allowRunning = await showConfirmDialog({
      title: "VirtualDJ is still open",
      track: trackDisplayTitle(track),
      message:
        "Color changes may be overwritten when VirtualDJ quits. Close it first when possible.",
      confirmLabel: "Change color anyway",
      tone: "warning",
    });
    if (!allowRunning) {
      if (selectEl) selectEl.value = prev;
      setStatus("Close VirtualDJ, then change the color.", "error");
      return;
    }
  }

  try {
    setStatus(`Setting ${kind} color → ${safeColor}…`);
    if (selectEl) selectEl.disabled = true;
    const data = await api("/api/set-cue-color", {
      method: "POST",
      body: JSON.stringify({
        path,
        kind,
        pos: Number(point.pos) || 0,
        color: safeColor,
        num: point.num != null ? String(point.num) : null,
        name: point.name || null,
        slot: point.slot != null ? String(point.slot) : null,
        allow_vdj_running: Boolean(allowRunning),
      }),
    });
    const r = data.result || {};
    if (r.cues) {
      applyCueSummaryToTrack(path, r.cues);
    } else {
      const idx = state.tracks.findIndex((t) => t.path === path);
      const snap = idx >= 0 ? state.tracks[idx] : null;
      if (snap?.cues?.points) {
        const key = cueKey(point);
        const points = snap.cues.points.map((p) =>
          cueKey(p) === key
            ? {
                ...p,
                color_name: safeColor,
                color: r.change?.color_after || p.color,
              }
            : p
        );
        applyCueSummaryToTrack(path, { ...snap.cues, points });
      }
    }
    if (!stillOnTrack(path, gen)) {
      setStatus(`Color updated on other track`, "success");
      return;
    }
    renderCues();
    drawWaveform();
    setStatus(
      `${kind === "loop" ? "Loop" : "Cue"} “${point.name || kind}” → ${safeColor}`,
      "success"
    );
  } catch (err) {
    if (selectEl) selectEl.value = prev;
    setStatus(err.message, "error");
  } finally {
    if (selectEl) selectEl.disabled = false;
  }
}

/**
 * Audition a loop after resize: enable loop play, jump to start, play.
 */
function auditionLoopPoint(point) {
  if (!point || pointKind(point) !== "loop") return;
  const track = currentTrack();
  const bpm = trackBpm(track);
  const start = Number(point.pos) || 0;
  const end = start + loopDurationSeconds(point, bpm);
  state.loopPlaybackOn = true;
  state.activeLoopKey = cueKey(point);
  state.activeCueKey = cueKey(point);
  syncLoopPlayBtn();
  jumpToCue(start, point);
  const audio = $("audio");
  if (audio) {
    playAudio(audio).catch(() => {});
    startLoopWatch();
  }
  setStatus(
    `Auditioning loop · ${point.name || "loop"} ${point.size || "?"}b ` +
      `(${fmtTime(start)}–${fmtTime(end)})`
  );
  renderCues();
  drawWaveform();
}

async function scaleLoopPoint(point, factor) {
  const track = currentTrack();
  if (!track || !point || pointKind(point) !== "loop") return;
  const path = track.path;
  const gen = state.trackGen;
  const label = point.name || "Loop";
  const oldSize = point.size || "?";
  const verb = factor < 1 ? "Halve" : "Double";

  let allowRunning = false;
  if (await isVdjRunningFresh()) {
    allowRunning = await showConfirmDialog({
      title: "VirtualDJ is still open",
      track: trackDisplayTitle(track),
      message:
        "Loop size changes may be overwritten when VirtualDJ quits. Close it first when possible.",
      confirmLabel: `${verb} anyway`,
      tone: "warning",
    });
    if (!allowRunning) {
      setStatus("Close VirtualDJ, then resize the loop.", "error");
      return;
    }
  }

  try {
    setStatus(`${verb} loop “${label}” (${oldSize}b)…`);
    const data = await api("/api/scale-loop", {
      method: "POST",
      body: JSON.stringify({
        path,
        pos: Number(point.pos) || 0,
        factor,
        num: point.num != null ? String(point.num) : null,
        name: point.name || null,
        slot: point.slot != null ? String(point.slot) : null,
        allow_vdj_running: Boolean(allowRunning),
      }),
    });
    const r = data.result || {};
    const ch = r.change || {};
    if (r.cues) {
      applyCueSummaryToTrack(path, r.cues);
    } else {
      const idx = state.tracks.findIndex((t) => t.path === path);
      const snap = idx >= 0 ? state.tracks[idx] : null;
      if (snap?.cues?.points) {
        const key = cueKey(point);
        const points = snap.cues.points.map((p) =>
          cueKey(p) === key
            ? { ...p, size: ch.size_after != null ? String(ch.size_after) : p.size }
            : p
        );
        applyCueSummaryToTrack(path, { ...snap.cues, points });
      }
    }

    if (!stillOnTrack(path, gen)) {
      setStatus(`${verb}d loop on other track`, "success");
      return;
    }

    const updated =
      (currentTrack()?.cues?.points || []).find(
        (p) =>
          pointKind(p) === "loop" &&
          Math.abs(Number(p.pos) - Number(point.pos)) < 0.02
      ) || null;

    setStatus(
      `${verb}d “${label}” · ${ch.size_before || oldSize}b → ${
        ch.size_after || updated?.size || "?"
      }b in VDJ`,
      "success"
    );
    renderCues();
    drawWaveform();
    if (updated) {
      auditionLoopPoint(updated);
    } else if (point) {
      const optimistic = {
        ...point,
        size: ch.size_after != null ? String(ch.size_after) : point.size,
      };
      auditionLoopPoint(optimistic);
    }
  } catch (err) {
    setStatus(err.message, "error");
  }
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
    if (cueN >= 2 && loopN >= 2 && hasGrid) status = "ready";
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
  if (await isVdjRunningFresh()) {
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

function setStatus(msg, kind = "", action = null) {
  const el = $("status");
  if (!el) return;
  el.className = `status-bar ${kind || ""}`.trim();
  el.replaceChildren();
  const text = document.createElement("span");
  text.className = "status-text";
  text.textContent = msg || "";
  el.appendChild(text);
  if (action && action.label && action.onClick) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn primary empty-cta";
    btn.textContent = action.label;
    if (action.gotoMode) btn.dataset.gotoMode = action.gotoMode;
    btn.addEventListener("click", action.onClick);
    el.appendChild(btn);
  }
}

function updatePipelineStrip() {
  const kicker = $("pipelineKicker");
  const title = $("pipelineTitle");
  const hint = $("pipelineHint");
  const next = $("pipelineNextAction");
  if (!kicker || !title || !hint || !next) return;

  const track = currentTrack();
  const n = state.tracks.length;
  const readyN = state.tracks.filter((t) => trackReadinessStatus(t) === "ready").length;
  const notCuedN = state.tracks.filter((t) => {
    const s = trackReadinessStatus(t);
    return s === "not_cued" || s === "missing";
  }).length;
  const destN = (state.selectedDests || []).length;

  if (isPracticeMode()) {
    kicker.textContent = "Practice";
    title.textContent = "Score transitions";
    hint.textContent = n > 0 ? `${n} mixes` : "Add mixes to begin";
    next.textContent = track ? "Analyze below" : "Select a mix";
    return;
  }
  if (isAssembleMode()) {
    kicker.textContent = "Assemble";
    title.textContent = "Pajamathon crate";
    const job = state.assembleJob;
    const n = job?.result?.playlist?.length;
    hint.textContent = job
      ? job.message || `${n || 0} in playlist`
      : "Gemini scores Zouk in chunks · newest first";
    next.textContent = assembleJobBusy(job) ? "Scoring chunks…" : "Build 300–500";
    return;
  }
  if (isRecsMode()) {
    kicker.textContent = "Recs";
    title.textContent = "Next-track recommendations";
    const np = state.recsNow;
    const n = state.recsResult?.candidates_considered;
    hint.textContent = np
      ? `${np.artist ? np.artist + " — " : ""}${np.title || np.name || "Track"}${
          n != null ? ` · ${n} in-key ±5 BPM` : " · auto"
        }`
      : "Waiting for VirtualDJ · auto-poll + auto-recs";
    next.textContent = np
      ? state.recsJobRunning
        ? "Ranking energy…"
        : "Higher · same · lower"
      : "Play a track in VDJ";
    return;
  }
  if (isReviewMode()) {
    kicker.textContent = "Step 1 · Add Cues";
    title.textContent = "Listen, then promote when markers feel right";
    hint.textContent =
      n > 0
        ? `${n} in queue · ${readyN} ready · ${notCuedN} need cues`
        : "Empty queue — use Open Sort if Ready already has tracks.";
    const pajNeed = state.tracks.filter(
      (t) =>
        addCuesSection(t) === "pajamathon" &&
        ["not_cued", "missing"].includes(trackReadinessStatus(t))
    ).length;
    const pajN = state.tracks.filter((t) => addCuesSection(t) === "pajamathon").length;
    if (pajN) {
      hint.textContent =
        n > 0
          ? `${n} in queue · Pajamathon ${pajNeed}/${pajN} need cues · ${readyN} ready`
          : "Empty queue — use Open Sort if Ready already has tracks.";
    }
    if (!track) next.textContent = n ? "Select a track" : "Queue empty";
    else if (!track.is_cued) next.textContent = "Right: AutoCue";
    else next.textContent = "Right: Move to Ready";
    return;
  }
  // Sort
  kicker.textContent = "Step 2 · Sort";
  title.textContent = "Place cued tracks into House / Zouk";
  hint.textContent =
    n > 0
      ? `${n} ready · choose a folder on the right`
      : "Nothing in Ready — promote from Add Cues first.";
  if (!track) next.textContent = n ? "Select a track" : "Queue empty";
  else if (!track.is_cued) next.textContent = "Send back to Add Cues";
  else if (destN) next.textContent = "Right: Sort";
  else next.textContent = "Right: pick a folder";
}

function emptyStateHtml({ icon = "◎", title, copy, ctaLabel, ctaMode }) {
  const cta = ctaLabel
    ? `<button type="button" class="btn primary empty-cta" data-goto-mode="${escapeHtml(
        ctaMode || ""
      )}">${escapeHtml(ctaLabel)}</button>`
    : "";
  return `<div class="empty empty-state">
    <div class="empty-state-icon" aria-hidden="true">${icon}</div>
    <p class="empty-state-title">${escapeHtml(title)}</p>
    <p class="empty-state-copy">${escapeHtml(copy)}</p>
    ${cta}
  </div>`;
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
  const target = Number($("targetBpmInput")?.value) || state.targetBpm || 75;
  document.querySelectorAll(".speed-preset[data-target-bpm]").forEach((btn) => {
    btn.classList.toggle("active", Number(btn.dataset.targetBpm) === target);
  });
}

async function api(path, options = {}) {
  const timeoutMs = Number(options.timeoutMs || 0);
  const extra = { ...options };
  delete extra.timeoutMs;
  const controller = timeoutMs > 0 ? new AbortController() : null;
  const timer =
    controller && timeoutMs > 0
      ? setTimeout(() => controller.abort(), timeoutMs)
      : null;
  try {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json", ...(extra.headers || {}) },
      ...extra,
      signal: extra.signal || controller?.signal,
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
  } catch (err) {
    if (err?.name === "AbortError") {
      throw new Error("Request timed out — is Music Sorter still running?");
    }
    throw err;
  } finally {
    if (timer) clearTimeout(timer);
  }
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

function addCuesReadinessRank(track) {
  const status = trackReadinessStatus(track);
  if (status === "ready") return 0;
  if (status === "partial") return 1;
  if (status === "not_cued" || status === "missing") return 2;
  return 3;
}

function sortAddCuesIndexes(indexes) {
  return indexes.slice().sort((a, b) => {
    const rank =
      addCuesReadinessRank(state.tracks[a]) - addCuesReadinessRank(state.tracks[b]);
    if (rank !== 0) return rank;
    return a - b;
  });
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

function persistCrateFilter(value) {
  const next =
    value === "pajamathon" || value === "inbox" || value === "cueing" ? value : "all";
  state.crateFilter = next;
  try {
    localStorage.setItem("addCuesCrateFilter", next);
  } catch {
    /* ignore */
  }
}

function loadCrateFilter() {
  try {
    const stored = localStorage.getItem("addCuesCrateFilter");
    if (
      stored === "pajamathon" ||
      stored === "inbox" ||
      stored === "cueing" ||
      stored === "all"
    ) {
      state.crateFilter = stored;
    }
  } catch {
    /* ignore */
  }
}

function syncCrateFilterUi() {
  document.querySelectorAll("#crateFilter button").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.crate === (state.crateFilter || "all"));
  });
}

function setCrateFilter(value) {
  persistCrateFilter(value);
  syncCrateFilterUi();
  applyModeUi();
  const indexes = filteredTrackIndexes();
  if (indexes.length && !indexes.includes(state.index)) {
    state.index = indexes[0];
    renderPlayer();
  }
  renderTrackList();
  updateBatchAddCuesButton();
  updatePipelineStrip();
}

function addCuesSection(track) {
  if (track?.section === "pajamathon" || track?.section === "inbox") {
    return track.section;
  }
  const group = String(track?.group || "").toLowerCase();
  const rel = String(track?.relative_path || "")
    .replace(/\\/g, "/")
    .toLowerCase();
  if (group.startsWith("pajamathon") || rel.startsWith("pajamathon/") || rel.startsWith("pajamathon ")) {
    return "pajamathon";
  }
  return "inbox";
}

function filteredTrackIndexes() {
  const q = (state.trackSearch || "").trim();
  const indexes = state.tracks
    .map((t, i) => i)
    .filter((i) => {
      const track = state.tracks[i];
      if (!trackMatchesSearch(track, q)) return false;
      if (isReviewMode() && state.crateFilter && state.crateFilter !== "all") {
        if (state.crateFilter === "cueing") {
          if (!isTrackCueing(track)) return false;
        } else if (addCuesSection(track) !== state.crateFilter) {
          return false;
        }
      }
      if (!isReviewMode() || state.readinessFilter === "all") return true;
      if (
        state.readinessFilter === "retried_cues" ||
        state.readinessFilter === "retried_loops" ||
        state.readinessFilter === "retried_both"
      ) {
        const want = state.readinessFilter.replace("retried_", "");
        return trackRetryKind(track) === want;
      }
      const status = trackReadinessStatus(track);
      if (!status) return false;
      if (state.readinessFilter === "ready") return status === "ready";
      if (state.readinessFilter === "partial") return status === "partial";
      if (state.readinessFilter === "not_cued") {
        return status === "not_cued" || status === "missing";
      }
      return true;
    });
  return isReviewMode() ? sortAddCuesIndexes(indexes) : indexes;
}

function trackRetryKind(track) {
  const hist = track?.retry_history || {};
  let cues = Boolean(hist.tried_cues);
  let loops = Boolean(hist.tried_loops);
  if (hist.kind === "both" || hist.tried_both) {
    cues = true;
    loops = true;
  } else if (hist.kind === "cues") {
    cues = true;
  } else if (hist.kind === "loops") {
    loops = true;
  }
  const job = retryJobForPath(track?.path);
  const scope = String(job?.writeScope || job?.write_scope || "").toLowerCase();
  const finished = ["ok", "error", "skipped"].includes(String(job?.status || ""));
  if (job && finished && scope) {
    if (scope === "cues" || scope === "all" || scope === "both") cues = true;
    if (scope === "loops" || scope === "all" || scope === "both") loops = true;
  }
  if (cues && loops) return "both";
  if (cues) return "cues";
  if (loops) return "loops";
  return null;
}

function retryHistoryBadge(track) {
  const kind = trackRetryKind(track);
  if (kind === "both") {
    return `<span class="badge retry-hist" title="AutoCue already ran cues and loops">Tried both</span>`;
  }
  if (kind === "cues") {
    return `<span class="badge retry-hist" title="AutoCue already ran cues only">Retried cues</span>`;
  }
  if (kind === "loops") {
    return `<span class="badge retry-hist" title="AutoCue already ran loops only">Retried loops</span>`;
  }
  return "";
}

function updateRetriedFilterUi() {
  const counts = state.tracks.reduce(
    (acc, track) => {
      const kind = trackRetryKind(track);
      if (kind === "cues") acc.cues += 1;
      else if (kind === "loops") acc.loops += 1;
      else if (kind === "both") acc.both += 1;
      return acc;
    },
    { cues: 0, loops: 0, both: 0 }
  );
  const cuesBtn = $("filterRetriedCues");
  const loopsBtn = $("filterRetriedLoops");
  const bothBtn = $("filterRetriedBoth");
  if (cuesBtn) {
    cuesBtn.textContent = counts.cues ? `Retried cues · ${counts.cues}` : "Retried cues";
  }
  if (loopsBtn) {
    loopsBtn.textContent = counts.loops
      ? `Retried loops · ${counts.loops}`
      : "Retried loops";
  }
  if (bothBtn) {
    bothBtn.textContent = counts.both ? `Tried both · ${counts.both}` : "Tried both";
  }
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

function renderRecsRail() {
  const root = $("trackList");
  if (!root) return;
  const np = state.recsNow;
  if (np?.path) {
    root.innerHTML = `<button type="button" class="track recs-now-rail active" disabled>
      <div class="track-title">${escapeHtml(np.title || np.name || "Now playing")}</div>
      <div class="track-sub">${escapeHtml(np.artist || "VirtualDJ")}</div>
      <div class="track-badges">
        ${np.bpm != null ? `<span class="badge ok">${Number(np.bpm).toFixed(0)} BPM</span>` : ""}
        ${np.key ? `<span class="badge neutral">${escapeHtml(np.key)}</span>` : ""}
        ${np.genre ? `<span class="badge genre">${escapeHtml(np.genre)}</span>` : ""}
      </div>
    </button>`;
  } else {
    root.innerHTML = emptyStateHtml({
      icon: "↻",
      title: "Watching VirtualDJ",
      copy: "Play a track — recs appear here and in VDJ Sideview (Next Recs).",
      ctaLabel: "",
      ctaMode: "",
    });
  }
  updatePipelineStrip();
}

function renderTrackList() {
  updateRetriedFilterUi();
  const root = $("trackList");
  if (isPracticeMode()) {
    renderPracticeMixList();
    return;
  }
  if (isRecsMode()) {
    renderRecsRail();
    return;
  }
  if (isAssembleMode()) {
    renderAssembleRail();
    return;
  }
  const indexes = filteredTrackIndexes();
  if (!state.tracks.length) {
    if (root.classList.contains("list-loading") || state.tracksLoadTimer) {
      root.innerHTML = `<div class="empty">Loading tracks…</div>`;
      return;
    }
    if (isReviewMode()) {
      root.innerHTML = emptyStateHtml({
        icon: "1",
        title: "Add Cues is empty",
        copy:
          state.crateFilter === "pajamathon"
            ? "No Pajamathon event-crate tracks loaded. Refresh — this tab lists Sets/Pajamathon, not only Add Cues."
            : "Drop audio into the Add Cues folder, or jump to Sort if Ready already has tracks.",
        ctaLabel: "Open Sort",
        ctaMode: "sort",
      });
    } else {
      root.innerHTML = emptyStateHtml({
        icon: "2",
        title: "Ready for Sort is empty",
        copy: "Approve cued tracks from Add Cues to fill this queue, then place them into House / Zouk.",
        ctaLabel: "Open Add Cues",
        ctaMode: "add_cues",
      });
    }
    root.querySelectorAll("[data-goto-mode]").forEach((btn) => {
      btn.addEventListener("click", () => setMode(btn.dataset.gotoMode));
    });
    updatePipelineStrip();
    return;
  }
  if (!indexes.length) {
    if (isReviewMode() && state.crateFilter === "cueing" && !state.trackSearch.trim()) {
      root.innerHTML = emptyStateHtml({
        icon: "↻",
        title: "Nothing cueing",
        copy: "Start AutoCue on a track and it will list here until it finishes.",
        ctaLabel: "",
        ctaMode: "",
      });
      return;
    }
    root.innerHTML = `<div class="empty">${
      state.trackSearch.trim()
        ? "No tracks match this search."
        : "No tracks match this filter."
    }</div>`;
    return;
  }

  root.innerHTML = isReviewMode()
    ? renderAddCuesTrackSections(indexes)
    : indexes.map((i) => renderQueueTrackRow(i)).join("");

  root.querySelectorAll(".track").forEach((btn) => {
    btn.addEventListener("click", () => selectTrack(Number(btn.dataset.index)));
  });
}

function renderQueueTrackRow(i) {
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
  const cueingBadge = isTrackCueing(t) ? `<span class="badge warn">Cueing</span>` : "";
  const section = addCuesSection(t);
  const group =
    isReviewMode() && t.group && section !== "pajamathon"
      ? `<span class="badge neutral">${escapeHtml(t.group)}</span>`
      : "";
  const br = formatBitrate(t.bitrate_kbps);
  const brBadge = br
    ? `<span class="badge ${bitrateBadgeClass(t.bitrate_kbps)}">${escapeHtml(br)}</span>`
    : "";
  const placements = t.placements || {};
  const libCued = (placements.library || []).some((p) => p.is_cued);
  const archCued = (placements.cues_sorted || []).some((p) => p.is_cued);
  const setCued = (placements.sets || []).some((p) => p.is_cued);
  const libHits = placements.library || [];
  const archHits = placements.cues_sorted || [];
  const setHits = placements.sets || [];
  const zoukLibHits = libHits.filter((p) => p.root_name === "Zouk");
  const houseLibHits = libHits.filter((p) => p.root_name === "House");
  const zoukLibCued = zoukLibHits.some((p) => p.is_cued);
  const houseLibCued = houseLibHits.some((p) => p.is_cued);
  const placementBadges = [
    zoukLibHits.length
      ? `<span class="badge ${zoukLibCued ? "ok" : "warn"}" title="${escapeHtml(
          zoukLibHits
            .map(
              (p) =>
                `${p.root_name}/${p.relative_path}` +
                (p.is_cued ? ` (${p.cue_count} cues)` : " (no cues)")
            )
            .join(", ")
        )}">${zoukLibCued ? "Zouk cued" : "In Zouk"}</span>`
      : "",
    houseLibHits.length
      ? `<span class="badge ${houseLibCued ? "ok" : "warn"}" title="${escapeHtml(
          houseLibHits
            .map(
              (p) =>
                `${p.root_name}/${p.relative_path}` +
                (p.is_cued ? ` (${p.cue_count} cues)` : " (no cues)")
            )
            .join(", ")
        )}">${houseLibCued ? "House cued" : "In House"}</span>`
      : "",
    placements.in_library && !zoukLibHits.length && !houseLibHits.length
      ? `<span class="badge ${libCued ? "ok" : "warn"}" title="${escapeHtml(
          libHits
            .map(
              (p) =>
                `${p.root_name}/${p.relative_path}` +
                (p.is_cued ? ` (${p.cue_count} cues)` : " (no cues)")
            )
            .join(", ")
        )}">${libCued ? "Lib cued" : "In library"}</span>`
      : "",
    placements.in_cues_sorted
      ? `<span class="badge ${archCued ? "ok" : "warn"}" title="${escapeHtml(
          archHits
            .map(
              (p) =>
                `Cues Sorted/${p.relative_path}` +
                (p.is_cued ? ` (${p.cue_count} cues)` : " (no cues)")
            )
            .join(", ")
        )}">${archCued ? "Archive cued" : "In archive"}</span>`
      : "",
    placements.in_sets
      ? `<span class="badge ${setCued ? "ok" : "warn"}" title="${escapeHtml(
          setHits
            .map(
              (p) =>
                `Sets/${p.relative_path}` +
                (p.is_cued ? ` (${p.cue_count} cues)` : " (no cues)")
            )
            .join(", ")
        )}">${setCued ? "Pajamathon cued" : "In Pajamathon"}</span>`
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
            ${cueingBadge}
            ${isReviewMode() ? retryHistoryBadge(t) : ""}
            ${grid}
            ${brBadge}
            ${placementBadges}
            ${group}
            ${loops}
            ${stems}
            <span class="badge neutral">${fmtBytes(t.size_bytes)}</span>
          </div>
        </button>`;
}

function renderAddCuesTrackSections(indexes) {
  const cueing = indexes.filter((i) => isTrackCueing(state.tracks[i]));
  const rest = indexes.filter((i) => !isTrackCueing(state.tracks[i]));
  const paj = sortAddCuesIndexes(
    rest.filter((i) => addCuesSection(state.tracks[i]) === "pajamathon")
  );
  const inbox = sortAddCuesIndexes(
    rest.filter((i) => addCuesSection(state.tracks[i]) !== "pajamathon")
  );
  const parts = [];
  const sectionBlock = (id, label, rows) => {
    if (!rows.length) return;
    const need = rows.filter((i) => {
      const status = trackReadinessStatus(state.tracks[i]);
      return status === "not_cued" || status === "missing";
    }).length;
    const sub =
      id === "cueing"
        ? `${rows.length} running`
        : `${need} not cued · ${rows.length}`;
    parts.push(
      `<div class="track-section-head" data-section="${id}">
        <strong>${escapeHtml(label)}</strong>
        <span class="subtitle">${escapeHtml(sub)}</span>
      </div>${rows.map((i) => renderQueueTrackRow(i)).join("")}`
    );
  };
  if (state.crateFilter === "cueing") {
    sectionBlock("cueing", "Currently cueing", cueing);
    return parts.join("");
  }
  sectionBlock("cueing", "Currently cueing", cueing);
  sectionBlock("pajamathon", "Pajamathon", paj);
  sectionBlock("inbox", "Inbox", inbox);
  return parts.join("");
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
  // Flush dirty notes for the previous track before rebinding.
  const prevPath = state.notesPath;
  const prevDirty = state.notesDirty;
  const prevText = ta.value;
  if (state.notesSaveTimer) {
    clearTimeout(state.notesSaveTimer);
    state.notesSaveTimer = null;
  }
  if (prevDirty && prevPath && (!track || track.path !== prevPath)) {
    const gen = ++state.notesSaveGen;
    // Fire-and-forget flush; do not require currentTrack match.
    saveVdjNotes(prevPath, prevText, gen, { force: true });
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

async function saveVdjNotes(path, comment, gen, opts = {}) {
  const force = Boolean(opts.force);
  if (gen != null && gen !== state.notesSaveGen && !force) return;
  if (!force && currentTrack()?.path !== path) return;

  if (!force) setNotesStatus("saving…", "warn");
  // Warn once per session if VDJ is open (notes can be overwritten on quit).
  if (!state.notesWarnedVdj && (await isVdjRunningFresh())) {
    state.notesWarnedVdj = true;
    setStatus(
      "VirtualDJ is open — notes still save, but VDJ may overwrite them on quit.",
      "warn"
    );
  }
  try {
    const data = await api("/api/notes", {
      method: "POST",
      body: JSON.stringify({
        path,
        comment,
        allow_vdj_running: true,
        // One backup on first notes write of the session.
        create_backup: !state._notesBackupDone,
      }),
    });
    state._notesBackupDone = true;
    if (gen != null && gen !== state.notesSaveGen && !force) return;
    if (!force && currentTrack()?.path !== path) return;

    const saved = data.result?.comment ?? comment;
    const idx = state.tracks.findIndex((t) => t.path === path);
    if (idx >= 0) {
      if (!state.tracks[idx].cues) state.tracks[idx].cues = {};
      state.tracks[idx].cues.comment = saved;
    }
    if (!force || currentTrack()?.path === path) {
      state.notesDirty = false;
    }
    const ta = $("vdjNotes");
    if (!force && ta && ta.value === comment) {
      setNotesStatus(
        data.result?.unchanged ? "saved" : "saved to VDJ",
        "ok"
      );
    } else if (!force) {
      setNotesStatus("saved · editing…", "ok");
    }
  } catch (err) {
    if (gen != null && gen !== state.notesSaveGen && !force) return;
    if (!force) setNotesStatus(err.message || "save failed", "error");
    else setStatus(`Notes save failed: ${err.message}`, "error");
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
    document.body.classList.remove("track-is-cued", "has-track");
    title.textContent = isPracticeMode()
      ? "Select a practice mix"
      : isReviewMode()
        ? "Select a track from the queue"
        : "Select a track from the queue";
    title.removeAttribute("title");
    title.removeAttribute("aria-label");
    meta.innerHTML = "";
    if (!isPracticeMode()) bindNotesToTrack(null);
    audio.pause();
    audio.removeAttribute("src");
    try {
      audio.load();
    } catch {
      /* ignore */
    }
    if (!isPracticeMode()) {
      if (recBox) {
        recBox.hidden = true;
        recBox.className = "recommendation";
        recBox.innerHTML = "";
      }
    }
    if (block) block.hidden = true;
    if ($("placementCard")) {
      $("placementCard").hidden = true;
      $("placementCard").innerHTML = "";
    }
    if (sortBtn) sortBtn.disabled = true;
    if ($("removeReadyBtn")) $("removeReadyBtn").disabled = true;
    if ($("demoteReadyBtn")) {
      $("demoteReadyBtn").disabled = true;
      $("demoteReadyBtn").hidden = isReviewMode() || isPracticeMode();
    }
    state.activeCueKey = null;
    state.waveform = null;
    resetWaveZoom();
    setPlayerLoading(false);
    if (!isPracticeMode()) {
      renderCues();
      drawWaveform();
      setWaveformStatus("No track selected");
      renderReviewPanel();
      syncAutocueUi();
      state.gridPreflight = null;
      renderGridPreflightCard(null);
    }
    updateTransportUi();
    return;
  }

  renderNowPlayingTitle(track);
  document.body.classList.toggle("track-is-cued", Boolean(track.is_cued));
  document.body.classList.toggle("has-track", true);
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
  meta.innerHTML = isPracticeMode()
    ? buildPracticePlayerMetaHtml(track)
    : buildPlayerMetaHtml(track);
  if (!isPracticeMode()) loadTrackMeta(track, gen);

  if (!isPracticeMode()) renderPlacementCard(track);

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
    // Always load waveform (practice uses it for transition map).
    scheduleWaveformLoad(track, gen);
    applyPlaybackRate(isPracticeMode() ? 1 : state.playbackRate);
    // Defer play slightly so aborted switches don't start audio.
    // Opening / selecting a track stays silent until the user hits Play
    // (or a previous Play in this tab unlocked autoplay). Quiet sessions never play.
    setTimeout(() => {
      if (gen !== state.trackGen || currentTrack()?.path !== track.path) return;
      if (shouldAutoplayOnSelect()) {
        playAudio(audio).catch(() => {});
      }
      setPlayerLoading(false);
    }, 160);
  } else {
    if (!state.waveform && !state.waveformLoading) {
      scheduleWaveformLoad(track, gen);
    }
    setPlayerLoading(false);
  }
  if (!isPracticeMode()) updateSpeedUi();
  updateTransportUi();
  if (!isPracticeMode()) bindNotesToTrack(track);

  if ($("removeReadyBtn")) {
    $("removeReadyBtn").disabled = isReviewMode() || isPracticeMode();
    $("removeReadyBtn").hidden = isReviewMode() || isPracticeMode();
  }
  if ($("demoteReadyBtn")) {
    $("demoteReadyBtn").disabled = isReviewMode() || isPracticeMode() || !track;
    $("demoteReadyBtn").hidden = isReviewMode() || isPracticeMode();
  }

  if (isPracticeMode()) {
    if (block) block.hidden = true;
    if (sortBtn) sortBtn.disabled = true;
    if (recBox) recBox.hidden = true;
    drawPracticeWaveform();
    return;
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
      "No VirtualDJ cue points yet. Sort is locked — you can still Trash from Ready.";
    sortBtn.disabled = true;
  } else {
    block.hidden = true;
    syncSortButtonState();
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

function buildPracticePlayerMetaHtml(track) {
  const d = state.practiceDetail;
  const bits = [];
  if (d?.duration_sec != null) bits.push(formatClock(d.duration_sec));
  else if (track.duration != null) bits.push(formatClock(track.duration));
  if (d?.track_count != null) bits.push(`${d.track_count} tracks`);
  if (d?.transition_count != null) bits.push(`${d.transition_count} transitions`);
  return bits.length
    ? bits.map((b) => `<span class="badge neutral">${escapeHtml(b)}</span>`).join(" ")
    : `<span class="badge neutral">practice mix</span>`;
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

function placementPathRow(labelPath, hit, options = {}) {
  const bpm = hit.bpm ? `<span class="badge neutral">${Number(hit.bpm).toFixed(0)} BPM</span>` : "";
  const grid = hit.has_beatgrid ? `<span class="badge neutral">grid</span>` : "";
  const pathAttr = escapeHtml(hit.path || "");
  const allowDelete = options.allowDelete !== false;
  const allowCopyCues = options.allowCopyCues !== false;
  const copyLabel = hit.is_cued ? "Replace cues" : "Copy cues";
  const copyTitle = hit.is_cued
    ? "Replace this copy's VirtualDJ cues with the Ready / Add Cues markers"
    : "Copy this track's VirtualDJ cues onto this existing file";
  const actions = [];
  if (allowCopyCues) {
    actions.push(`
      <button
        type="button"
        class="btn ghost placement-copy-cues-btn"
        data-placement-path="${pathAttr}"
        title="${escapeHtml(copyTitle)}"
        aria-label="${escapeHtml(copyLabel)} ${escapeHtml(labelPath)}"
      >${copyLabel}</button>`);
  }
  if (allowDelete) {
    actions.push(`
      <button
        type="button"
        class="btn ghost danger placement-delete-btn"
        data-placement-path="${pathAttr}"
        title="Remove this file from its folder (Trash) and delete its VirtualDJ cues for that path"
        aria-label="Delete from folder ${escapeHtml(labelPath)}"
      >Delete from folder</button>`);
  }
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
      <div class="placement-path-actions">${actions.join("")}</div>
    </div>`;
}

function isPajamathonPlacement(hit) {
  return String(hit?.event || hit?.root_name || "")
    .toLowerCase()
    .startsWith("pajamathon");
}

function emptyPlacements() {
  return {
    in_cues_sorted: false,
    cues_sorted: [],
    in_library: false,
    library: [],
    in_sets: false,
    sets: [],
    already_sorted: false,
    any_library_cued: false,
    any_archive_cued: false,
    any_set_cued: false,
  };
}

function placementsArePopulated(placements) {
  const p = placements || {};
  return Boolean(
    p.already_sorted ||
      p.in_sets ||
      p.in_library ||
      p.in_cues_sorted ||
      (p.library || []).length ||
      (p.sets || []).length ||
      (p.cues_sorted || []).length
  );
}

function mergeLoadedPlacements(prevTracks, nextTracks) {
  const prevByPath = new Map((prevTracks || []).map((t) => [t.path, t]));
  return (nextTracks || []).map((track) => {
    const prev = prevByPath.get(track.path);
    if (!prev) return track;
    const incomingEmpty = !placementsArePopulated(track.placements);
    if (incomingEmpty && prev.placementsLoaded && prev.placements) {
      return {
        ...track,
        placements: prev.placements,
        placementsLoaded: true,
        placementsError: prev.placementsError || "",
      };
    }
    return {
      ...track,
      placementsLoaded: Boolean(prev.placementsLoaded && incomingEmpty),
      placementsError: incomingEmpty ? prev.placementsError || "" : "",
    };
  });
}

function applyExistingSetPlacement(track, result) {
  if (!track || !result) return track;
  const dest = result.dest_path || result.existing?.path || "";
  if (!dest) return track;
  const placements = {
    ...(track.placements || emptyPlacements()),
  };
  const sets = [...(placements.sets || [])];
  if (!sets.some((hit) => hit.path === dest)) {
    sets.push({
      path: dest,
      relative_path: result.relative_path || result.existing?.relative_path || "",
      root_name: result.event || result.existing?.event || result.existing?.root_name || "",
      event: result.event || result.existing?.event || "",
      ...(result.existing || {}),
    });
  }
  placements.sets = sets;
  placements.in_sets = sets.length > 0;
  track.placements = placements;
  return track;
}

function renderPlacementCard(track) {
  const card = $("placementCard");
  if (!card) return;

  if (!track || isPracticeMode()) {
    card.hidden = true;
    card.innerHTML = "";
    return;
  }

  const rows = [];
  const libs = track.placements?.library || [];
  const sorted = track.placements?.cues_sorted || [];
  const sets = track.placements?.sets || [];

  const canCopyCues = Boolean(track.is_cued);
  const libraryGroups = [];
  const zoukHits = libs.filter((p) => p.root_name === "Zouk");
  const houseHits = libs.filter((p) => p.root_name === "House");
  const otherLibs = libs.filter(
    (p) => p.root_name !== "Zouk" && p.root_name !== "House"
  );
  if (zoukHits.length) libraryGroups.push(["Zouk", zoukHits]);
  if (houseHits.length) libraryGroups.push(["House", houseHits]);
  for (const p of otherLibs) {
    libraryGroups.push([p.root_name || "Library", [p]]);
  }
  for (const [label, hits] of libraryGroups) {
    const paths = hits
      .map((p) =>
        placementPathRow(`${p.root_name}/${p.relative_path}`, p, {
          allowCopyCues: canCopyCues,
          allowDelete: true,
        })
      )
      .join("");
    rows.push(`
      <div class="placement-row">
        <div class="placement-label">${escapeHtml(label)}</div>
        <div class="placement-paths">${paths}</div>
      </div>`);
  }
  if (sorted.length) {
    const paths = sorted
      .map((p) =>
        placementPathRow(`Cues Sorted/${p.relative_path}`, p, {
          allowCopyCues: canCopyCues,
          allowDelete: true,
        })
      )
      .join("");
    rows.push(`
      <div class="placement-row">
        <div class="placement-label">Archive</div>
        <div class="placement-paths">${paths}</div>
      </div>`);
  }
  if (sets.length) {
    const allPaj = sets.every((p) => isPajamathonPlacement(p));
    const paths = sets
      .map((p) =>
        placementPathRow(`Sets/${p.relative_path}`, p, {
          allowCopyCues: canCopyCues,
          allowDelete: true,
        })
      )
      .join("");
    rows.push(`
      <div class="placement-row">
        <div class="placement-label">${allPaj ? "Pajamathon" : "Sets"}</div>
        <div class="placement-paths">${paths}</div>
      </div>`);
  }

  const cuedN =
    libs.filter((h) => h.is_cued).length +
    sorted.filter((h) => h.is_cued).length +
    sets.filter((h) => h.is_cued).length;
  const totalN = libs.length + sorted.length + sets.length;
  const titleExtra =
    totalN > 0
      ? ` · ${cuedN}/${totalN} cued`
      : "";

  const review = isReviewMode();
  const inPajamathon = sets.some((p) => isPajamathonPlacement(p));
  const loading = Boolean(track.placementsLoading) && totalN === 0;
  const loadError = !loading && totalN === 0 ? track.placementsError || "" : "";
  const title = totalN > 0
    ? review
      ? `Already sorted in main library${titleExtra}`
      : `Already in library${titleExtra}`
    : loading
      ? "Looking up library copies…"
      : loadError
        ? "Couldn't load library copies"
        : "Not in Pajamathon";
  const note = totalN > 0
    ? review
      ? cuedN > 0
        ? "This song already exists under House/Zouk, Cues Sorted, and/or Sets/Pajamathon with VDJ cues. Approving still moves this Add Cues copy to Ready — Copy cues pushes markers onto that copy; Delete from folder removes a library/archive file only."
        : "This song already exists under House/Zouk, Cues Sorted, and/or Sets/Pajamathon, but those copies are not cued in VirtualDJ yet. Copy cues writes this track's markers onto that file."
      : "Copy cues writes this Ready track's markers onto the existing House/Zouk/Cues Sorted/Pajamathon file without moving audio. Delete from folder Trashes a duplicate library copy. Add to Pajamathon copies this Ready file into Sets/Pajamathon 2026."
    : loading
      ? "Looking up House / Zouk / Pajamathon copies for this track."
      : loadError
        ? loadError
        : "No matching Sets/Pajamathon file. Add to Pajamathon copies this track into the event crate and clones its VirtualDJ cues.";

  const actionBtns = [];
  if (loadError) {
    actionBtns.push(`
      <button
        type="button"
        class="btn ghost placement-retry-btn"
        title="Look up House, Zouk, Cues Sorted, and Sets/Pajamathon again"
      >Retry library lookup</button>`);
  }
  if (!inPajamathon && !loading) {
    actionBtns.push(`
      <button
        type="button"
        class="btn primary placement-add-set-btn"
        title="Copy this track into Sets/Pajamathon 2026 and clone its VirtualDJ cues"
      >Add to Pajamathon</button>`);
  }
  if (canCopyCues && totalN > 1) {
    actionBtns.push(`
      <button
        type="button"
        class="btn ghost placement-copy-cues-all-btn"
        title="Write this track's VirtualDJ cues onto every House/Zouk, Cues Sorted, and Sets copy listed above"
      >Copy cues to all ${totalN} locations</button>`);
  }
  const allAction = actionBtns.length
    ? `<div class="placement-card-actions">${actionBtns.join("")}</div>`
    : "";

  card.hidden = false;
  card.classList.toggle("placement-card-review", review);
  card.classList.toggle("placement-card-has-cued", cuedN > 0);
  card.innerHTML = `
    <div class="placement-card-title">${title}</div>
    <div class="placement-rows">${rows.join("")}</div>
    ${allAction}
    <div class="placement-card-note">${note}</div>
  `;

  card.querySelectorAll(".placement-delete-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const path = btn.getAttribute("data-placement-path");
      if (path) deleteLibraryPlacement(path);
    });
  });
  card.querySelectorAll(".placement-copy-cues-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const path = btn.getAttribute("data-placement-path");
      if (path) copyCuesToPlacement(path);
    });
  });
  const allBtn = card.querySelector(".placement-copy-cues-all-btn");
  if (allBtn) {
    allBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      copyCuesToAllPlacements();
    });
  }
  const addSetBtn = card.querySelector(".placement-add-set-btn");
  if (addSetBtn) {
    addSetBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      addTrackToPajamathon();
    });
  }
  const retryBtn = card.querySelector(".placement-retry-btn");
  if (retryBtn) {
    retryBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      loadTrackPlacements(currentTrack(), { force: true });
    });
  }
}

async function deleteLibraryPlacement(placementPath) {
  const track = currentTrack();
  if (!placementPath) return;

  // Resolve label from current placements if possible.
  const allHits = [
    ...(track?.placements?.library || []),
    ...(track?.placements?.cues_sorted || []),
    ...(track?.placements?.sets || []),
  ];
  const hit = allHits.find((h) => h.path === placementPath);
  const label = hit ? placementHitLabel(hit) : placementPath.split("/").slice(-3).join("/");
  const cueNote =
    hit?.is_cued
      ? ` This copy has ${hit.cue_count || 0} cues` +
        (hit.loop_count ? ` and ${hit.loop_count} loops` : "") +
        " in VirtualDJ — they will be removed for this path only."
      : hit?.in_database
        ? " The VirtualDJ Song entry for this path will be removed (no manual cues found)."
        : " No VirtualDJ entry was found for this path (file still goes to Trash).";

  const ok = await showConfirmDialog({
    title: "Delete from this folder?",
    track: track ? trackDisplayTitle(track) : label,
    message: `Remove “${label}” from that folder and delete its VirtualDJ database entry (cues/loops for that path only).`,
    note:
      `File moves to Trash (recoverable). Ready for Sort / Add Cues is not touched.${cueNote} Use this to remove a House, Zouk, Cues Sorted, or Pajamathon copy. Close VirtualDJ first if it is open.`,
    confirmLabel: "Delete from folder",
    tone: "danger",
  });
  if (!ok) return;

  let allowRunning = false;
  if (await isVdjRunningFresh()) {
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
    const missingBit = r.missing_file ? " · file was already gone" : "";
    setStatus(
      `Deleted ${r.root_name || ""}/${r.relative_path || label}${missingBit}${dbBit}`,
      "success"
    );
    // Refresh placements for the Ready track (source stays).
    await loadTracks({ keepPath: track?.path });
    if (!isReviewMode()) await loadFolders();
  } catch (err) {
    setStatus(err.message, "error");
  }
}

function allPlacementHits(track) {
  return [
    ...(track?.placements?.library || []),
    ...(track?.placements?.cues_sorted || []),
    ...(track?.placements?.sets || []),
  ].filter((h) => h && h.path);
}

function placementHitLabel(hit) {
  if (!hit) return "";
  if (hit.root_name === "Cues Sorted" || (hit.root || "").includes("Cues Sorted")) {
    return `Cues Sorted/${hit.relative_path}`;
  }
  if (hit.event || (hit.root || "").includes("/Sets") || (hit.root || "").endsWith("/Sets")) {
    return `Sets/${hit.relative_path}`;
  }
  return `${hit.root_name}/${hit.relative_path}`;
}

async function copyCuesToPlacement(placementPath) {
  const track = currentTrack();
  if (!placementPath || !track) return;
  if (!track.is_cued) {
    setStatus("This track has no cue points to copy.", "error");
    return;
  }

  const allHits = [
    ...(track.placements?.library || []),
    ...(track.placements?.cues_sorted || []),
    ...(track.placements?.sets || []),
  ];
  const hit = allHits.find((h) => h.path === placementPath);
  const label = hit
    ? placementHitLabel(hit)
    : placementPath.split("/").slice(-3).join("/");
  const destCued = Boolean(hit?.is_cued) || Number(hit?.loop_count || 0) > 0;
  const cueNote = destCued
    ? ` This copy already has ${hit.cue_count || 0} cues` +
      (hit.loop_count ? ` and ${hit.loop_count} loops` : "") +
      " — they will be replaced. Beatgrid, BPM, and comments on that file stay."
    : " The Ready / Add Cues markers will be written onto this file. Audio stays put.";

  const ok = await showConfirmDialog({
    title: destCued ? "Replace cues on this copy?" : "Copy cues onto this copy?",
    track: trackDisplayTitle(track),
    message: `Write cues from “${trackDisplayTitle(track)}” onto “${label}”.`,
    note: `VirtualDJ database only — files are not moved.${cueNote} Close VirtualDJ first if it is open.`,
    confirmLabel: destCued ? "Replace cues" : "Copy cues",
    tone: destCued ? "warning" : "accent",
  });
  if (!ok) return;

  let allowRunning = false;
  if (await isVdjRunningFresh()) {
    allowRunning = await showConfirmDialog({
      title: "VirtualDJ is still open",
      track: trackDisplayTitle(track),
      message:
        "Database edits may be overwritten when VirtualDJ quits. Close it first when possible.",
      confirmLabel: "Copy anyway",
      tone: "warning",
    });
    if (!allowRunning) {
      setStatus("Close VirtualDJ, then copy cues.", "error");
      return;
    }
  }

  try {
    setStatus(`Copying cues onto ${label}…`);
    const data = await api("/api/copy-cues", {
      method: "POST",
      body: JSON.stringify({
        source: track.path,
        dest: placementPath,
        overwrite: destCued,
        allow_vdj_running: Boolean(allowRunning),
      }),
    });
    const r = data.result || {};
    const destName = r.root_name
      ? `${r.root_name}/${r.relative_path || label}`
      : label;
    setStatus(
      `Copied ${r.copied_cues || 0} cues` +
        (r.copied_loops ? ` · ${r.copied_loops} loops` : "") +
        ` → ${destName}`,
      "success"
    );
    await loadTracks({ keepPath: track.path, skipStatus: true });
  } catch (err) {
    setStatus(err.message, "error");
  }
}

async function copyCuesToAllPlacements() {
  const track = currentTrack();
  if (!track) return;
  if (!track.is_cued) {
    setStatus("This track has no cue points to copy.", "error");
    return;
  }

  const hits = allPlacementHits(track);
  if (!hits.length) {
    setStatus("No existing library copies to write cues onto.", "error");
    return;
  }

  const marked = hits.filter(
    (h) => h.is_cued || Number(h.loop_count || 0) > 0
  );
  const labels = hits.map((h) => placementHitLabel(h));
  const list =
    labels.length <= 6
      ? labels.join(", ")
      : `${labels.slice(0, 5).join(", ")} +${labels.length - 5} more`;
  const cueNote = marked.length
    ? ` ${marked.length} of ${hits.length} already have cues/loops and will be replaced.`
    : " Audio files stay put.";

  const ok = await showConfirmDialog({
    title: marked.length
      ? `Replace cues on all ${hits.length} copies?`
      : `Copy cues to all ${hits.length} locations?`,
    track: trackDisplayTitle(track),
    message: `Write cues from “${trackDisplayTitle(track)}” onto: ${list}.`,
    note: `VirtualDJ database only — files are not moved.${cueNote} Close VirtualDJ first if it is open.`,
    confirmLabel: marked.length
      ? `Replace cues on ${hits.length}`
      : `Copy cues to ${hits.length}`,
    tone: marked.length ? "warning" : "accent",
  });
  if (!ok) return;

  let allowRunning = false;
  if (await isVdjRunningFresh()) {
    allowRunning = await showConfirmDialog({
      title: "VirtualDJ is still open",
      track: trackDisplayTitle(track),
      message:
        "Database edits may be overwritten when VirtualDJ quits. Close it first when possible.",
      confirmLabel: "Copy anyway",
      tone: "warning",
    });
    if (!allowRunning) {
      setStatus("Close VirtualDJ, then copy cues.", "error");
      return;
    }
  }

  try {
    setStatus(`Copying cues onto ${hits.length} locations…`);
    const data = await api("/api/copy-cues-all", {
      method: "POST",
      body: JSON.stringify({
        source: track.path,
        dests: hits.map((h) => h.path),
        overwrite: marked.length > 0,
        allow_vdj_running: Boolean(allowRunning),
      }),
    });
    const r = data.result || {};
    const bits = [`Copied to ${r.copied || 0}`];
    if (r.skipped) bits.push(`skipped ${r.skipped}`);
    if (r.failed) bits.push(`failed ${r.failed}`);
    setStatus(
      `${bits.join(" · ")}` +
        (r.copied_cues != null ? ` · ${r.copied_cues} cues` : "") +
        (r.copied_loops ? ` · ${r.copied_loops} loops` : ""),
      r.failed ? "error" : "success"
    );
    await loadTracks({ keepPath: track.path, skipStatus: true });
  } catch (err) {
    setStatus(err.message, "error");
  }
}

async function addTrackToPajamathon() {
  const track = currentTrack();
  if (!track) return;
  if (isPracticeMode()) return;

  const existing = (track.placements?.sets || []).filter((p) =>
    isPajamathonPlacement(p)
  );
  if (existing.length) {
    renderPlacementCard(track);
    setStatus(
      `Already in Pajamathon: ${existing.map((p) => p.relative_path).join(", ")}`
    );
    return;
  }

  const cueBit = track.is_cued
    ? ` Its ${track.cues?.cue_count || 0} cues` +
      (track.cues?.loop_count ? ` and ${track.cues.loop_count} loops` : "") +
      " will be cloned onto the new set file."
    : " The audio is copied even without cues.";

  const ok = await showConfirmDialog({
    title: "Add to Pajamathon?",
    track: trackDisplayTitle(track),
    message:
      "Copy this track into Sets/Pajamathon 2026. Ready for Sort / Add Cues stays put.",
    note: `Next numbered file in the event crate.${cueBit} Close VirtualDJ first if it is open.`,
    confirmLabel: "Add to Pajamathon",
    tone: "accent",
  });
  if (!ok) return;

  let allowRunning = false;
  if (await isVdjRunningFresh()) {
    allowRunning = await showConfirmDialog({
      title: "VirtualDJ is still open",
      track: trackDisplayTitle(track),
      message:
        "Database edits may be overwritten when VirtualDJ quits. Close it first when possible.",
      confirmLabel: "Add anyway",
      tone: "warning",
    });
    if (!allowRunning) {
      setStatus("Close VirtualDJ, then add to Pajamathon.", "error");
      return;
    }
  }

  try {
    setStatus("Adding to Pajamathon…");
    const data = await api("/api/add-to-set", {
      method: "POST",
      body: JSON.stringify({
        path: track.path,
        allow_vdj_running: Boolean(allowRunning),
      }),
    });
    const r = data.result || {};
    if (r.already_exists) {
      applyExistingSetPlacement(track, r);
      renderPlacementCard(currentTrack());
      setStatus(`Already in ${r.event || "Pajamathon"}: ${r.relative_path || r.dest_path || ""}`);
      await loadTrackPlacements(currentTrack(), { force: true });
      return;
    }
    setStatus(
      `Added to ${r.relative_path || r.dest_path || "Pajamathon"}` +
        (r.copied_cues ? ` · ${r.copied_cues} cues` : "") +
        (r.copied_loops ? ` · ${r.copied_loops} loops` : ""),
      "success"
    );
    await loadTracks({ keepPath: track.path, skipStatus: true });
    await loadTrackPlacements(currentTrack(), { force: true });
  } catch (err) {
    setStatus(err.message, "error");
    await loadTrackPlacements(currentTrack(), { force: true });
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
  ["toNoCuesBtn", "toLowSkipBtn", "toAcLowBtn", "deleteAddCuesBtn"].forEach((id) => {
    const el = $(id);
    if (!el) return;
    el.disabled = !hasTrack;
    // Ensure danger delete is never left pointer-events:none after enable.
    if (id === "deleteAddCuesBtn" && hasTrack) {
      el.removeAttribute("disabled");
      el.setAttribute("aria-disabled", "false");
    }
  });
}

function _recLibCardHtml(libName, pick) {
  if (!pick || !pick.relative_path) return "";
  const conf = Math.round((pick.confidence || 0) * 100);
  const alts = (pick.alternatives || [])
    .map(
      (a) =>
        `<button type="button" class="chip" data-action="use-one" data-lib="${escapeHtml(
          libName
        )}" data-path="${escapeHtml(a)}">${escapeHtml(libName)} / ${escapeHtml(a)}</button>`
    )
    .join("");
  return `
    <div class="rec-lib-card" data-lib="${escapeHtml(libName)}">
      <div class="rec-lib-head">
        <strong>${escapeHtml(libName)}</strong>
        <span class="badge neutral">${conf}%</span>
      </div>
      <div class="rec-path">${escapeHtml(libName)} / ${escapeHtml(pick.relative_path)}</div>
      <div class="rec-reason">${escapeHtml(pick.reasoning || "")}</div>
      <div class="rec-alts">
        <button type="button" class="chip primary" data-action="use-one" data-lib="${escapeHtml(
          libName
        )}" data-path="${escapeHtml(pick.relative_path)}">Use ${escapeHtml(libName)}</button>
        ${alts}
      </div>
    </div>
  `;
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
    recBox.innerHTML = "Asking Gemini for House + Zouk recommendations…";
    return;
  }

  if (rec.error) {
    recBox.className = "recommendation error";
    recBox.innerHTML = `<strong>Recommendation failed</strong><div class="rec-reason">${escapeHtml(
      rec.error
    )}</div>`;
    return;
  }

  // Dual picks (new API). Fall back to legacy single-library shape.
  const zoukPick = rec.zouk || null;
  const housePick = rec.house || null;
  const hasDual = Boolean(zoukPick || housePick);

  const tags = (rec.vibe_tags || [])
    .map((t) => `<span class="badge neutral">${escapeHtml(t)}</span>`)
    .join(" ");

  const bpmLabel =
    rec.bpm != null && Number.isFinite(Number(rec.bpm))
      ? ` · ${Number(rec.bpm).toFixed(1)} BPM`
      : "";
  const cacheLabel = rec.cached ? " (cached)" : "";
  const modelLabel = rec.model ? ` · ${escapeHtml(rec.model)}` : "";

  if (!hasDual) {
    // Legacy single recommendation payload
    const conf = Math.round((rec.confidence || 0) * 100);
    const alts = (rec.alternatives || [])
      .map(
        (a) =>
          `<button type="button" class="chip" data-action="use-one" data-lib="${escapeHtml(
            rec.library
          )}" data-path="${escapeHtml(a)}">${escapeHtml(rec.library)} / ${escapeHtml(a)}</button>`
      )
      .join("");
    recBox.className = "recommendation";
    recBox.innerHTML = `
      <div class="subtitle">Gemini suggestion${cacheLabel}${bpmLabel}${modelLabel}</div>
      <div class="rec-path">${escapeHtml(rec.library)} / ${escapeHtml(rec.relative_path)}</div>
      <div class="rec-reason">${escapeHtml(rec.reasoning || "")}</div>
      <div class="meta-row" style="margin-top:8px">${tags}</div>
      <div class="rec-alts">
        <button type="button" class="chip primary" data-action="use-one" data-lib="${escapeHtml(
          rec.library
        )}" data-path="${escapeHtml(rec.relative_path)}">Use recommendation</button>
        ${alts}
      </div>
    `;
  } else {
    const houseCard = housePick
      ? _recLibCardHtml("House", housePick)
      : `<div class="rec-lib-card rec-lib-skipped">
          <div class="rec-lib-head"><strong>House</strong><span class="badge neutral">skipped</span></div>
          <div class="rec-reason">${escapeHtml(
            rec.house_skip_reason ||
              "House only recommended above 100 BPM"
          )}</div>
        </div>`;
    const zoukCard = zoukPick
      ? _recLibCardHtml("Zouk", zoukPick)
      : `<div class="rec-lib-card rec-lib-skipped">
          <div class="rec-lib-head"><strong>Zouk</strong></div>
          <div class="rec-reason">No Zouk suggestion</div>
        </div>`;

    const bothBtn =
      housePick?.relative_path && zoukPick?.relative_path
        ? `<button type="button" class="chip primary" data-action="use-both">Use House + Zouk</button>`
        : "";

    recBox.className = "recommendation dual";
    recBox.innerHTML = `
      <div class="subtitle">Gemini · House + Zouk${cacheLabel}${bpmLabel}${modelLabel}</div>
      <div class="rec-dual-grid">
        ${houseCard}
        ${zoukCard}
      </div>
      <div class="meta-row" style="margin-top:8px">${tags}</div>
      <div class="rec-alts rec-dual-actions">
        ${bothBtn}
      </div>
    `;
  }

  recBox.querySelectorAll("[data-action='use-one']").forEach((btn) => {
    btn.addEventListener("click", () => {
      applyFolderSelection(btn.dataset.lib, btn.dataset.path);
    });
  });
  const both = recBox.querySelector("[data-action='use-both']");
  if (both) {
    both.addEventListener("click", () => {
      applyBothLibraryRecommendations(rec);
    });
  }
}

/** Select House + Zouk recommended folders together (multi-dest sort). */
function applyBothLibraryRecommendations(rec) {
  const house = rec?.house;
  const zouk = rec?.zouk;
  if (!house?.relative_path && !zouk?.relative_path) return;

  state.library = "Both";
  document.querySelectorAll("#libraryPathSeg button").forEach((b) => {
    b.classList.toggle("active", b.dataset.library === "Both");
  });
  updatePathHint();

  const dests = [];
  if (zouk?.relative_path) {
    dests.push({
      library: "Zouk",
      path: zouk.relative_path,
      key: destKey("Zouk", zouk.relative_path),
    });
    _expandFolderPath(zouk.relative_path);
  }
  if (house?.relative_path) {
    dests.push({
      library: "House",
      path: house.relative_path,
      key: destKey("House", house.relative_path),
    });
    _expandFolderPath(house.relative_path);
  }
  state.selectedDests = dests;
  state.selectedPath = dests[0]?.path || "";
  state.selectedPathLibrary = dests[0]?.library || "";
  updateSelectionLabels();
  loadFolders().then(() => {
    renderFolders();
  });
}

function pathModeLabel() {
  if (state.library === "Both") return "House + Zouk";
  return state.library;
}

function destKey(library, relativePath) {
  return `${library}::${relativePath}`;
}

function hasDest(library, relativePath) {
  const key = destKey(library, relativePath);
  return state.selectedDests.some((d) => d.key === key);
}

function selectedDestCount() {
  return (state.selectedDests || []).length;
}

function formatSelectedDestsLabel() {
  const dests = state.selectedDests || [];
  if (!dests.length) return "None selected";
  if (dests.length === 1) {
    return `${dests[0].library} / ${dests[0].path}`;
  }
  return dests.map((d) => `${d.library}/${d.path}`).join(" · ");
}

function updatePathHint() {
  const el = $("pathHint");
  if (!el) return;
  if (state.library === "Both") {
    el.textContent =
      "Both: click a folder to copy into House and Zouk at that path. Click again to deselect. Hold Alt/Option to pick one library only.";
  } else {
    el.textContent = `Showing ${state.library} — click to multi-select folders (switch to Both to place into House + Zouk together). Also archives to Cues Sorted.`;
  }
}

function syncSortButtonState() {
  const track = currentTrack();
  const n = selectedDestCount();
  const sortBtn = $("sortBtn");
  if (!sortBtn) return;
  const canSort = Boolean(track && track.is_cued && n > 0);
  sortBtn.disabled = !canSort;
  sortBtn.classList.toggle("is-waiting", !canSort && !sortBtn.dataset.busy);
  sortBtn.classList.toggle("btn-cta", canSort || Boolean(sortBtn.dataset.busy));
  if (!sortBtn.dataset.busy) {
    const dests = state.selectedDests || [];
    const paths = new Set(dests.map((d) => d.path));
    const libs = new Set(dests.map((d) => d.library));
    if (!track) {
      sortBtn.textContent = "Select a track first";
    } else if (!track.is_cued) {
      sortBtn.textContent = "Track not cued";
    } else if (n === 0) {
      sortBtn.textContent = "Select a folder →";
    } else if (n === 2 && paths.size === 1 && libs.has("House") && libs.has("Zouk")) {
      sortBtn.textContent = `Sort · House + Zouk / ${dests[0].path}`;
    } else if (n > 1) {
      sortBtn.textContent = `Sort · ${n} folders`;
    } else {
      sortBtn.textContent = `Sort · ${dests[0].library}/${dests[0].path}`;
    }
  }
  const step = $("sortRailStepLabel");
  if (step) step.textContent = canSort ? "Primary · ready" : "Primary";
  const railTitle = $("foldersRailTitle");
  if (railTitle) railTitle.textContent = canSort ? "Sort destination" : "Choose a folder";
  const railSub = $("foldersRailSubtitle");
  if (railSub) {
    railSub.textContent = canSort
      ? "One click places the track"
      : "Pick House / Zouk path, then sort";
  }
}

function updateSelectionLabels() {
  const label = formatSelectedDestsLabel();
  const sel = $("selectedFolder");
  if (sel) sel.textContent = label;
  const parentLib =
    state.selectedPathLibrary ||
    (state.library === "Both" ? "Zouk" : state.library);
  const hint = $("createParentHint");
  if (hint) {
    hint.textContent = state.selectedPath
      ? `New folder will be created under: ${parentLib} / ${state.selectedPath}`
      : `New folder will be created at top level of ${pathModeLabel()}`;
  }
  syncSortButtonState();
}

function applyFolderSelection(library, relativePath) {
  // Gemini suggestion: jump to that library tree and select that dest.
  if (library && state.library !== "Both" && library !== state.library) {
    state.library = library;
    document.querySelectorAll("#libraryPathSeg button").forEach((b) => {
      b.classList.toggle("active", b.dataset.library === library);
    });
    updatePathHint();
    loadFolders().then(() => {
      setSingleDest(library, relativePath, { expand: true });
    });
  } else {
    setSingleDest(library || state.library, relativePath, { expand: true });
  }
}

/** Replace selection with one destination (used by Gemini recommend). */
function setSingleDest(library, relativePath, { expand = false } = {}) {
  const lib =
    library === "Both" ? "Zouk" : library || state.library || "Zouk";
  const path = relativePath || "";
  state.selectedDests = path
    ? [{ library: lib, path, key: destKey(lib, path) }]
    : [];
  state.selectedPath = path;
  state.selectedPathLibrary = lib;
  if (expand && path) {
    const parts = path.split("/");
    let acc = [];
    for (const part of parts) {
      acc.push(part);
      state.expanded.add(acc.join("/"));
    }
  }
  updateSelectionLabels();
  renderFolders();
}

function _expandFolderPath(relativePath) {
  if (!relativePath) return;
  const parts = relativePath.split("/");
  let acc = [];
  for (const part of parts) {
    acc.push(part);
    state.expanded.add(acc.join("/"));
  }
}

/** Toggle a (library, folder) destination in the multi-select set. */
function toggleDest(library, relativePath, { expand = false } = {}) {
  const lib = library || (state.library === "Both" ? "Zouk" : state.library);
  const path = relativePath || "";
  if (!path) return;
  const key = destKey(lib, path);
  const exists = state.selectedDests.some((d) => d.key === key);
  if (exists) {
    state.selectedDests = state.selectedDests.filter((d) => d.key !== key);
  } else {
    state.selectedDests = [
      ...state.selectedDests,
      { library: lib, path, key },
    ];
  }
  state.selectedPath = path;
  state.selectedPathLibrary = lib;
  if (expand) _expandFolderPath(path);
  updateSelectionLabels();
  renderFolders();
}

/**
 * Toggle the same relative path under House and Zouk together.
 * If both are already selected, remove both; otherwise ensure both are selected.
 */
function toggleDestBothLibraries(relativePath, { expand = false } = {}) {
  const path = relativePath || "";
  if (!path) return;
  const houseKey = destKey("House", path);
  const zoukKey = destKey("Zouk", path);
  const hasHouse = state.selectedDests.some((d) => d.key === houseKey);
  const hasZouk = state.selectedDests.some((d) => d.key === zoukKey);
  if (hasHouse && hasZouk) {
    state.selectedDests = state.selectedDests.filter(
      (d) => d.key !== houseKey && d.key !== zoukKey
    );
  } else {
    const next = state.selectedDests.filter(
      (d) => d.key !== houseKey && d.key !== zoukKey
    );
    next.push(
      { library: "Zouk", path, key: zoukKey },
      { library: "House", path, key: houseKey }
    );
    state.selectedDests = next;
  }
  state.selectedPath = path;
  state.selectedPathLibrary = "Zouk";
  if (expand) _expandFolderPath(path);
  updateSelectionLabels();
  renderFolders();
}

/** Ensure both House and Zouk are selected for this path (no toggle-off). */
function addDestBothLibraries(relativePath, { expand = true } = {}) {
  const path = relativePath || "";
  if (!path) return;
  for (const lib of ["Zouk", "House"]) {
    const key = destKey(lib, path);
    if (!state.selectedDests.some((d) => d.key === key)) {
      state.selectedDests = [
        ...state.selectedDests,
        { library: lib, path, key },
      ];
    }
  }
  state.selectedPath = path;
  state.selectedPathLibrary = "Zouk";
  if (expand) _expandFolderPath(path);
  updateSelectionLabels();
  renderFolders();
}

function clearSelectedDests() {
  state.selectedDests = [];
  state.selectedPath = "";
  state.selectedPathLibrary = "";
  updateSelectionLabels();
  renderFolders();
}

/** @deprecated use toggleDest / setSingleDest — kept for call sites */
function selectFolder(relativePath, { expand = false } = {}) {
  const lib =
    state.selectedPathLibrary ||
    (state.library === "Both" ? "Zouk" : state.library);
  setSingleDest(lib, relativePath, { expand });
}

function folderMatchesFilter(node, filter) {
  if (!filter) return true;
  const f = filter.toLowerCase();
  if (node.relative_path.toLowerCase().includes(f) || node.name.toLowerCase().includes(f)) {
    return true;
  }
  return (node.children || []).some((c) => folderMatchesFilter(c, filter));
}

function renderFolderNode(node, depth = 0, library = "Zouk") {
  if (!folderMatchesFilter(node, state.filter)) return "";
  const hasKids = (node.children || []).length > 0;
  const open = state.expanded.has(node.relative_path) || Boolean(state.filter);
  const selected = hasDest(library, node.relative_path);
  const rec = state.recommendation;
  const isRec =
    rec &&
    !rec.error &&
    rec.library === library &&
    rec.relative_path === node.relative_path;

  const kids =
    hasKids && open
      ? `<div class="children">${node.children
          .map((c) => renderFolderNode(c, depth + 1, library))
          .join("")}</div>`
      : "";

  // In Both mode a folder is "selected" if either/both libraries have it.
  const selectedBoth =
    state.library === "Both" &&
    (hasDest("House", node.relative_path) || hasDest("Zouk", node.relative_path));
  const isSelected = state.library === "Both" ? selectedBoth : selected;
  const bothComplete =
    state.library === "Both" &&
    hasDest("House", node.relative_path) &&
    hasDest("Zouk", node.relative_path);

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
        <button type="button" class="folder ${isSelected ? "selected" : ""} ${
          bothComplete ? "selected-both-libs" : ""
        } ${isRec ? "recommended" : ""}" data-path="${escapeHtml(
          node.relative_path
        )}" data-lib="${escapeHtml(library)}" title="${
          state.library === "Both"
            ? "Copy into House + Zouk at this folder (Alt-click for this library only)"
            : "Toggle destination"
        }">
          <span class="folder-name">${escapeHtml(node.name)}</span>
          ${
            bothComplete
              ? `<span class="folder-hz-badge" title="House + Zouk">H+Z</span>`
              : ""
          }
          <span class="folder-count">${node.track_count}</span>
        </button>
      </div>
      ${kids}
    </div>
  `;
}

function renderFolderSections(folders, title, library) {
  if (!folders || !folders.length) return "";
  const lib = library || title || state.library;
  const vibes = folders.filter((f) => f.group === "vibe");
  const artists = folders.filter((f) => f.group !== "vibe");
  let html = title
    ? `<div class="subtitle library-section-title">${escapeHtml(title)}</div>`
    : "";
  if (vibes.length) {
    html += `<div class="subtitle" style="padding:6px 8px">Vibes / emotions</div>`;
    html += vibes.map((n) => renderFolderNode(n, 0, lib)).join("");
  }
  if (artists.length) {
    html += `<div class="subtitle" style="padding:10px 8px 6px">Artists / collections</div>`;
    html += artists.map((n) => renderFolderNode(n, 0, lib)).join("");
  }
  return html;
}

function renderSelectedDestChips() {
  const host = $("selectedDestChips");
  if (!host) return;
  const dests = state.selectedDests || [];
  if (!dests.length) {
    host.innerHTML = "";
    host.hidden = true;
    return;
  }
  host.hidden = false;
  host.innerHTML =
    dests
      .map(
        (d) => `
      <button type="button" class="dest-chip" data-chip-key="${escapeHtml(
        d.key
      )}" title="Remove ${escapeHtml(d.library)} / ${escapeHtml(d.path)}">
        <span>${escapeHtml(d.library)} / ${escapeHtml(d.path)}</span>
        <span class="dest-chip-x" aria-hidden="true">×</span>
      </button>`
      )
      .join("") +
    `<button type="button" class="btn ghost dest-clear-btn" id="clearDestsBtn">Clear</button>`;

  host.querySelectorAll("[data-chip-key]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const key = btn.getAttribute("data-chip-key");
      state.selectedDests = state.selectedDests.filter((d) => d.key !== key);
      updateSelectionLabels();
      renderFolders();
    });
  });
  host.querySelector("#clearDestsBtn")?.addEventListener("click", () => {
    clearSelectedDests();
  });
}

function renderFolders() {
  const root = $("folderTree");
  let html = "";

  if (state.library === "Both" && state.folderTrees) {
    html += renderFolderSections(
      state.folderTrees.Zouk?.folders || [],
      "Zouk",
      "Zouk"
    );
    html += renderFolderSections(
      state.folderTrees.House?.folders || [],
      "House",
      "House"
    );
    if (!html) html = `<div class="empty">No folders found.</div>`;
  } else if (!state.folders.length) {
    html = `<div class="empty">No folders found.</div>`;
  } else {
    html = renderFolderSections(state.folders, "", state.library);
  }

  root.innerHTML = html;
  renderSelectedDestChips();

  root.querySelectorAll("button.folder[data-path]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      const lib = btn.dataset.lib || state.library;
      const path = btn.dataset.path;
      // Both mode: one click = House + Zouk at this path.
      // Alt/Option = single library only (fine-grained multi-select).
      if (state.library === "Both" && !e.altKey) {
        toggleDestBothLibraries(path, { expand: true });
      } else {
        toggleDest(lib, path, { expand: true });
      }
    });
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

/** Re-check VDJ process right before a DB write (badge/health can be stale). */
async function isVdjRunningFresh() {
  try {
    await loadHealth();
    updatePipelineStrip();
  } catch {
    /* keep last known health */
  }
  return Boolean(state.health?.virtualdj_running);
}

function scheduleLoadTracks(opts = {}) {
  state.tracksLoadQueued = { ...(state.tracksLoadQueued || {}), ...opts, silent: true };
  if (state.tracksLoadTimer) return;
  state.tracksLoadTimer = setTimeout(() => {
    const next = state.tracksLoadQueued || { silent: true };
    state.tracksLoadQueued = null;
    state.tracksLoadTimer = null;
    loadTracks(next);
  }, 280);
}

async function loadTracks({ keepPath, skipStatus = false, silent = false } = {}) {
  const listEl = $("trackList");
  const requestedMode = state.mode;
  const loadGen = ++state.tracksLoadGen;
  const haveTracks = Array.isArray(state.tracks) && state.tracks.length > 0;
  const soft = Boolean(silent || (haveTracks && requestedMode === "add_cues"));
  if (listEl && !soft) listEl.classList.add("list-loading");
  if (!skipStatus && !soft) {
    setStatus(
      requestedMode === "add_cues" ? "Loading Add Cues…" : "Loading Ready for Sort…"
    );
  } else if (!skipStatus && soft && requestedMode === "add_cues" && !isAutocueJobRunning()) {
    setStatus("Updating cue list…");
  }

  try {
    const data = await api(`/api/tracks?mode=${encodeURIComponent(requestedMode)}`, {
      timeoutMs: 120000,
    });
    // Drop stale responses: mode switch or a newer refresh finished first.
    if (loadGen !== state.tracksLoadGen || state.mode !== requestedMode) {
      return;
    }
    if (data.mode && data.mode !== requestedMode) {
      return;
    }

    const prevPath = keepPath || currentTrack()?.path;
    state.tracks = mergeLoadedPlacements(state.tracks, data.tracks || []);
    const counts = data.counts || {};

    if (requestedMode === "add_cues") {
      const paj = counts.pajamathon || 0;
      $("countsBadge").textContent = paj
        ? `Pajamathon ${counts.pajamathon_not_cued || 0}/${paj} need cues · ${
            counts.not_cued || 0
          } not cued`
        : `${counts.ready || 0} ready · ${counts.partial || 0} partial · ${
            counts.not_cued || 0
          } not cued`;
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
    if (currentTrack() && !isPracticeMode() && !isRecsMode() && !isAssembleMode()) {
      loadTrackPlacements(currentTrack());
    }
    if (currentTrack() && requestedMode === "sort") requestRecommendation(currentTrack());
    // Callers that just finished promote/sort pass skipStatus and set their own
    // success handoff *after* this returns so the CTA is not wiped.
    if (!skipStatus) {
      setStatus(
        requestedMode === "add_cues"
          ? counts.pajamathon
            ? `Add Cues · ${counts.pajamathon} Pajamathon · ${counts.inbox || 0} inbox`
            : `Add Cues · ${counts.total || state.tracks.length} tracks · primary action on the right`
          : `Ready for Sort · ${counts.total || state.tracks.length} tracks · pick a folder, then Sort`
      );
    }
    updateBatchAddCuesButton();
    updatePipelineStrip();
  } catch (err) {
    if (loadGen !== state.tracksLoadGen || state.mode !== requestedMode) {
      return;
    }
    if (!skipStatus) {
      setStatus(err.message || "Failed to load tracks", "error");
    } else {
      throw err;
    }
    if (listEl && !state.tracks.length) {
      listEl.innerHTML = emptyStateHtml({
        icon: "!",
        title: "Could not load tracks",
        copy: err.message || "Network error",
        ctaLabel: "Retry",
        ctaMode: "",
      }).replace(
        'data-goto-mode=""',
        'id="retryLoadTracksBtn" data-goto-mode=""'
      );
      const retry = $("retryLoadTracksBtn");
      if (retry) {
        retry.removeAttribute("data-goto-mode");
        retry.addEventListener("click", () => loadTracks({ keepPath }));
      }
    }
  } finally {
    // Only clear loading style if this is still the latest load for this mode.
    if (listEl && loadGen === state.tracksLoadGen) {
      listEl.classList.remove("list-loading");
    }
  }
}

function applyModeUi() {
  const review = isReviewMode();
  const practice = isPracticeMode();
  const recs = isRecsMode();
  const assemble = isAssembleMode();
  document.body.classList.toggle("mode-practice", practice);
  document.body.classList.toggle("mode-recs", recs);
  document.body.classList.toggle("mode-assemble", assemble);
  document.body.classList.toggle("mode-review", review);
  document.body.classList.toggle("mode-sort", !review && !practice && !recs && !assemble);
  document.body.classList.toggle("practice-stack-layout", practice);

  $("listTitle").textContent = practice
    ? "Practice mixes"
    : isAssembleMode()
      ? "Pajamathon"
      : isRecsMode()
        ? "Live from VDJ"
        : review
          ? state.crateFilter === "pajamathon"
            ? "Pajamathon cues"
            : state.crateFilter === "cueing"
              ? "Currently cueing"
              : "Add Cues"
          : "Ready for Sort";
  $("listSubtitle").textContent = practice
    ? "Select a mix to analyze"
    : isAssembleMode()
      ? "Newest Zouk first · vibe crate"
      : isRecsMode()
        ? "Auto-checks now-playing every 5s"
        : review
          ? state.crateFilter === "pajamathon"
            ? "Event crate · cue these separately from the inbox"
            : state.crateFilter === "cueing"
              ? "Tracks AutoCue is working on right now"
              : "Step 1 · cue, listen, promote"
          : "Step 2 · place into House / Zouk";
  const playerHeading = $("playerHeading");
  if (playerHeading) {
    playerHeading.textContent = practice ? "Mix playback" : "Now playing";
  }

  // Stage subtitle is redundant with pipeline — hide in all modes.
  const subEl = $("playerSubtitle");
  if (subEl) {
    subEl.textContent = "";
    subEl.hidden = true;
  }
  $("listToolbar").hidden = !review || isRecsMode() || isAssembleMode();
  const trackSearch = $("trackSearch");
  if (trackSearch) {
    trackSearch.placeholder = practice
      ? "Search practice mixes…"
      : review
        ? "Search Add Cues…"
        : "Search Ready for Sort…";
  }
  const recsMode = isRecsMode();
  const assembleMode = isAssembleMode();
  $("foldersPanel").hidden = review || practice || recsMode || assembleMode;
  $("reviewPanel").hidden = !review;
  const practicePanel = $("practicePanel");
  if (practicePanel) practicePanel.hidden = !practice;
  const recsPanel = $("recsPanel");
  if (recsPanel) recsPanel.hidden = !recsMode;
  const assemblePanel = $("assemblePanel");
  if (assemblePanel) assemblePanel.hidden = !assembleMode;

  // Practice: transport + transition waveform; hide sort/cue chrome.
  const hideInPractice = [
    "speedPanel",
    "notesPanel",
    "cuesPanel",
    "blockBanner",
    "placementCard",
    "recommendation",
    "sortActions",
    "reviewActions",
  ];
  hideInPractice.forEach((id) => {
    const el = $(id);
    if (!el) return;
    if (practice || recsMode || assembleMode) {
      el.hidden = true;
    } else if (id === "sortActions") {
      el.hidden = review;
    } else if (id === "reviewActions") {
      el.hidden = !review;
    } else if (id === "recommendation") {
      el.hidden = review;
    } else if (id === "blockBanner" || id === "placementCard") {
      // leave to their own renderers when leaving practice
    } else {
      el.hidden = false;
    }
  });
  if (practice) {
    updatePracticeWaveVisibility();
  } else {
    const practiceWave = $("practiceWavePanel");
    if (practiceWave) {
      practiceWave.hidden = true;
      practiceWave.classList.remove("is-empty");
    }
  }
  $("rerunRecBtn").hidden = review || practice || isRecsMode() || isAssembleMode();

  // AutoCue scope buttons live in Add Cues review, not Sort.
  const headerScopes = $("autocueScopeHeader");
  if (headerScopes) headerScopes.hidden = !review;
  const retryStatus = $("retryStatus");
  if (retryStatus && !review) {
    retryStatus.hidden = true;
  }
  if (review) syncAutocueUi();
  if (practice) {
    $("shortcutsHint").innerHTML = `Shortcuts: <span class="kbd">Space</span> play/pause ·
       <span class="kbd">J</span>/<span class="kbd">K</span> mixes ·
       Seek on a transition to jump −20s`;
  } else if (review) {
    $("shortcutsHint").innerHTML = `Shortcuts: <span class="kbd">Space</span> play/pause ·
       <span class="kbd">J</span>/<span class="kbd">K</span> tracks ·
       <span class="kbd">1</span>–<span class="kbd">9</span> jump cues ·
       <span class="kbd">L</span> loop play ·
       <span class="kbd">Z</span> zouk · <span class="kbd">H</span> ½ BPM ·
       <span class="kbd">G</span> ones ·
       <span class="kbd">A</span> approve · <span class="kbd">S</span> skip`;
  } else {
    $("shortcutsHint").innerHTML = `Shortcuts: <span class="kbd">Space</span> play/pause ·
       <span class="kbd">J</span>/<span class="kbd">K</span> tracks ·
       <span class="kbd">1</span>–<span class="kbd">9</span> jump cues ·
       <span class="kbd">L</span> loop play ·
       <span class="kbd">Z</span> zouk · <span class="kbd">H</span> ½ BPM ·
       <span class="kbd">G</span> ones ·
       <span class="kbd">⌘</span>+<span class="kbd">Enter</span> sort`;
  }

  document.querySelectorAll("#modeSeg button").forEach((b) => {
    const on = b.dataset.mode === state.mode;
    b.classList.toggle("active", on);
    b.setAttribute("aria-pressed", on ? "true" : "false");
  });
  const t = currentTrack();
  document.body.classList.toggle("track-is-cued", Boolean(t && t.is_cued));
  document.body.classList.toggle("has-track", Boolean(t));
  updatePipelineStrip();
  syncSortButtonState();
}

async function setMode(mode) {
  if (
    mode !== "sort" &&
    mode !== "add_cues" &&
    mode !== "practice" &&
    mode !== "recs" &&
    mode !== "assemble"
  )
    return;
  if (state.mode === mode) {
    if (!state.tracks.length) {
      loadTracks();
    }
    return;
  }
  // Leave recs → stop live polls
  stopRecsNowPlayingPoll();
  stopRecsPoll();
  stopAssemblePoll();
  state.mode = mode;
  state.recommendation = null;
  state.selectedPath = "";
  state.selectedPathLibrary = "";
  state.readinessFilter = "all";
  state.tracks = [];
  state.index = 0;
  state.practiceDetail = null;
  state.practiceMixPath = "";
  state.practiceMixes = [];
  state.trackMeta = null;
  state.gridPreflight = null;
  state.trackGen += 1;
  // Invalidate any in-flight /api/tracks from the previous mode (Sort loads
  // can finish after Add Cues is selected and used to flood "Not cued").
  state.tracksLoadGen += 1;
  state.waveform = null;
  if (state.waveformAbort) state.waveformAbort.abort();
  if (state.recommendAbort) state.recommendAbort.abort();
  if (state.metaAbort) state.metaAbort.abort();
  if (state.waveformDebounce) clearTimeout(state.waveformDebounce);
  document.querySelectorAll("#readinessFilter button").forEach((b) => {
    b.classList.toggle("active", b.dataset.filter === "all");
  });
  $("countsBadge").textContent = "Loading…";
  $("countsBadge").className = "badge neutral";
  $("trackList")?.classList.add("list-loading");
  applyModeUi();
  document.body.classList.add("is-mode-loading");
  // Clear stage immediately so mode switches never show the previous mode's track.
  try {
    clearSelectedDests();
  } catch {
    state.selectedDests = [];
  }
  renderTrackList();
  renderPlayer();
  if (typeof renderReviewPanel === "function") {
    try {
      renderReviewPanel();
    } catch {
      /* ignore during boot */
    }
  }
  resetWorkspaceScroll();
  setStatus(
    isPracticeMode()
      ? "Loading practice mixes…"
      : isReviewMode()
        ? "Loading Add Cues…"
        : "Loading Ready for Sort…"
  );
  try {
    if (isPracticeMode()) {
      renderPracticePanel();
      setPlayerLoading(false);
      await loadPracticeMixes();
    } else if (isRecsMode()) {
      setPlayerLoading(false);
      state.tracks = [];
      state.index = 0;
      renderTrackList();
      setStatus("Watching VirtualDJ · recs refresh automatically");
      showRecsSkeletons("Looking up now-playing…");
      await refreshRecsNowPlaying({ loadAudio: false, forceAuto: true });
      startRecsNowPlayingPoll();
    } else if (isAssembleMode()) {
      setPlayerLoading(false);
      state.tracks = [];
      state.index = 0;
      renderTrackList();
      setStatus("Assemble a Zouk crate for the event");
      renderAssembleMixTuners();
      await loadAssemblePreview();
    } else {
      setWaveformStatus("Select a track");
      setPlayerLoading(false);
      await loadTracks();
      if (isReviewMode()) await hydrateAutocueJobs();
      if (!isReviewMode()) await loadFolders();
    }
  } finally {
    document.body.classList.remove("is-mode-loading");
    updatePipelineStrip();
    if (isPracticeMode()) {
      document.body.classList.add("practice-stack-layout");
      schedulePracticeWaveRedraw();
    } else {
      document.body.classList.remove("practice-stack-layout");
    }
  }
  requestAnimationFrame(resetWorkspaceScroll);
  if (isPracticeMode()) schedulePracticeWaveRedraw();
}

async function loadFolders() {
  const data = await api(`/api/folders/${encodeURIComponent(state.library)}`);
  state.folders = data.folders || [];
  state.folderTrees = data.trees || null;
  renderFolders();
  updatePathHint();
}

async function loadTrackPlacements(track, { force = false } = {}) {
  if (!track?.path) return;
  const path = track.path;
  const liveStart = state.tracks.find((t) => t.path === path) || track;
  if (!force && (liveStart.placementsLoaded || liveStart.placementsLoading)) {
    if (currentTrack()?.path === path) renderPlacementCard(currentTrack());
    return;
  }
  liveStart.placementsLoading = true;
  liveStart.placementsError = "";
  if (currentTrack()?.path === path) renderPlacementCard(currentTrack());
  try {
    const data = await api(
      `/api/track-placements?path=${encodeURIComponent(path)}`,
      { timeoutMs: 45000 }
    );
    const live = state.tracks.find((t) => t.path === path);
    if (!live) return;
    if (data.placements) live.placements = data.placements;
    live.placementsLoaded = true;
    live.placementsLoading = false;
    live.placementsError = "";
    if (currentTrack()?.path === path) {
      renderPlacementCard(currentTrack());
      renderTrackList();
    }
  } catch (err) {
    const live = state.tracks.find((t) => t.path === path);
    if (live) {
      live.placementsLoading = false;
      live.placementsError = err.message || "Could not look up library copies";
    }
    if (currentTrack()?.path === path) {
      renderPlacementCard(currentTrack());
    }
  }
}

async function selectTrack(index) {
  state.index = index;
  state.trackGen += 1;
  const selected = currentTrack();
  if (selected) {
    loadTrackPlacements(selected);
  }
  updatePipelineStrip();
  state.recommendation = null;
  state.trackMeta = null;
  state.activeLoopKey = null;
  stopLoopWatch();
  // Leave grid-align / place-cue without writing when switching tracks.
  if (state.gridAlignMode) {
    state.gridAlignMode = false;
    state.gridAlignPlan = null;
    state.gridAlignAnchor = null;
    state.gridAlignOriginal = null;
    state.gridAlignDragging = false;
    syncGridAlignUi();
  }
  if (state.placeCueMode) cancelPlaceCueMode();
  if (state.placeLoopMode) cancelPlaceLoopMode();
  if (state.metaAbort) state.metaAbort.abort();
  // Immediate feedback before any async work
  state.waveform = null;
  resetWaveZoom();
  resetWorkspaceScroll();
  // Stop previous mix so seeks don't hit the wrong file mid-switch
  const audio = $("audio");
  if (audio) {
    try {
      audio.pause();
    } catch {
      /* ignore */
    }
  }
  if (isPracticeMode()) {
    // Clear detail until the new mix loads (prevents wrong-mix seek targets)
    if (state.practiceMixPath !== currentTrack()?.path) {
      state.practiceDetail = null;
    }
    setPracticeWaveStatus("Loading waveform…");
    drawPracticeWaveform();
  } else {
    setWaveformStatus("Loading waveform…");
    drawWaveform();
  }
  setPlayerLoading(true);
  renderTrackList();
  renderPlayer();
  // AutoCue busy state is per-track — refresh labels when switching.
  if (!isPracticeMode()) {
    syncAutocueUi();
    updateApproveButtons();
  }
  const track = currentTrack();
  if (isPracticeMode() && track) {
    await loadPracticeDetail(track.path);
  } else if (track && !isReviewMode()) {
    requestRecommendation(track);
  }
}

function practiceMixAsTrack(mix) {
  return {
    path: mix.path,
    name: mix.name,
    is_cued: true,
    duration: mix.duration_sec,
    is_practice_mix: true,
    cues: { points: [], cue_count: 0, loop_count: 0 },
  };
}

async function loadPracticeMixes() {
  const listEl = $("trackList");
  const loadGen = ++state.tracksLoadGen;
  if (listEl) listEl.classList.add("list-loading");
  setStatus("Loading practice mixes…");
  try {
    const data = await api("/api/practice/sets");
    if (loadGen !== state.tracksLoadGen || !isPracticeMode()) return;
    state.practiceMixes = data.mixes || [];
    state.practiceDb = data.transitions_db || null;
    state.tracks = state.practiceMixes.map(practiceMixAsTrack);
    state.index = 0;
    $("countsBadge").textContent = `${state.tracks.length} mixes`;
    $("countsBadge").className = "badge ok";
    renderPracticeDbBadge();
    renderTrackList();
    if (state.tracks.length) {
      await selectTrack(0);
    } else {
      state.practiceDetail = null;
      renderPracticePanel();
      setPlayerLoading(false);
      setStatus("No practice mixes found in Music/Mixes");
    }
    setStatus(`Practice · ${state.tracks.length} mixes`);
  } catch (err) {
    setStatus(err.message || String(err), "error");
  } finally {
    if (listEl && loadGen === state.tracksLoadGen) {
      listEl.classList.remove("list-loading");
    }
  }
}

function renderPracticeMixList() {
  const root = $("trackList");
  if (!root) return;
  const q = (state.trackSearch || "").trim().toLowerCase();
  const indexes = state.tracks
    .map((t, i) => i)
    .filter((i) => {
      if (!q) return true;
      return (state.tracks[i].name || "").toLowerCase().includes(q);
    });
  if (!state.tracks.length) {
    root.innerHTML = `<div class="empty">No practice mixes in ~/Music/Mixes.</div>`;
    return;
  }
  if (!indexes.length) {
    root.innerHTML = `<div class="empty">No mixes match this search.</div>`;
    return;
  }
  root.innerHTML = indexes
    .map((i) => {
      const t = state.tracks[i];
      const mix = state.practiceMixes[i] || {};
      const active = i === state.index ? "active" : "";
      const dur =
        mix.duration_sec != null
          ? `<span class="mix-dur">${formatClock(mix.duration_sec)}</span>`
          : "";
      const flag = mix.is_practice
        ? `<span class="badge ok">practice</span>`
        : `<span class="badge neutral">mix</span>`;
      return `<button type="button" class="practice-mix-row ${active}" data-index="${i}">
        <strong>${escapeHtml(t.name)}</strong>
        <div class="track-row-meta">${flag} ${dur}</div>
      </button>`;
    })
    .join("");
  root.querySelectorAll("button.practice-mix-row[data-index]").forEach((btn) => {
    btn.addEventListener("click", () => selectTrack(Number(btn.dataset.index)));
  });
}

function renderPracticeDbBadge() {
  const el = $("practiceDbBadge");
  if (!el) return;
  const db = state.practiceDb;
  if (!db || db.error) {
    el.textContent = db?.error
      ? `Transitions DB error: ${db.error}`
      : "Transitions DB —";
    el.className = "badge warn practice-db-badge";
    return;
  }
  el.textContent = `Notes ${db.note_edges ?? 0} · History ${db.history_edges ?? 0}`;
  el.className = "badge ok practice-db-badge";
  el.title = db.db_path || "";
}

function scoreTone(n) {
  const v = Number(n);
  if (!Number.isFinite(v)) return "";
  if (v >= 7.5) return "good";
  if (v >= 5.5) return "mid";
  return "bad";
}

function scorePill(label, value, { overall = false } = {}) {
  if (value == null || value === "") {
    return `<span class="score-pill">${escapeHtml(label)} —</span>`;
  }
  const n = Number(value);
  const tone = scoreTone(n);
  const cls = `score-pill ${overall ? "overall" : ""} ${tone}`.trim();
  return `<span class="${cls}">${escapeHtml(label)} ${n.toFixed(1)}</span>`;
}

function mergeScoresIntoDetail(detail, results) {
  if (!detail?.transitions || !results?.length) return detail;
  const byIdx = new Map(
    results
      .filter((r) => r.transition_index != null)
      .map((r) => [Number(r.transition_index), r])
  );
  const transitions = detail.transitions.map((tx) => {
    const s = byIdx.get(Number(tx.index));
    if (!s || s.error) return tx;
    return {
      ...tx,
      score: {
        overall: s.overall,
        smoothness: s.smoothness,
        creativity: s.creativity,
        flow: s.flow,
        energy_match: s.energy_match,
        comments: s.comments,
        save_for_set: s.save_for_set,
        model: s.model,
        strengths: s.strengths || [],
        improvements: s.improvements || [],
        better_option_track: s.better_option_track || "",
        better_option_reason: s.better_option_reason || "",
        better_option_source: s.better_option_source || "",
        better_option_confidence: s.better_option_confidence,
        clip_start_sec: s.clip_start_sec,
        clip_duration_sec: s.clip_duration_sec,
        cached: Boolean(s.cached),
      },
    };
  });
  return { ...detail, transitions };
}

function sortedPracticeTransitions(txs) {
  const list = [...(txs || [])];
  const sort = state.practiceTxSort || "order";
  if (sort === "score") {
    list.sort(
      (a, b) =>
        (Number(b.score?.overall) || -1) - (Number(a.score?.overall) || -1) ||
        a.index - b.index
    );
  } else if (sort === "save") {
    list.sort((a, b) => {
      const as = a.score?.save_for_set ? 1 : 0;
      const bs = b.score?.save_for_set ? 1 : 0;
      if (bs !== as) return bs - as;
      return (Number(b.score?.overall) || -1) - (Number(a.score?.overall) || -1);
    });
  } else {
    list.sort((a, b) => a.index - b.index);
  }
  return list;
}

function renderPracticeSummary() {
  const el = $("practiceSummary");
  if (!el) return;
  const d = state.practiceDetail;
  const summary = state.practiceSummary;
  const scored = (d?.transitions || []).filter((t) => t.score?.overall != null);
  if (!scored.length && !summary) {
    el.hidden = true;
    el.innerHTML = "";
    return;
  }
  el.hidden = false;
  const avg =
    summary?.avg_overall != null
      ? summary.avg_overall
      : scored.length
        ? (
            scored.reduce((s, t) => s + Number(t.score.overall), 0) / scored.length
          ).toFixed(1)
        : "—";
  const saveN =
    summary?.save_for_set?.length ??
    scored.filter((t) => t.score?.save_for_set).length;
  const top = summary?.top?.[0] || scored.sort(
    (a, b) => Number(b.score?.overall) - Number(a.score?.overall)
  )[0];
  const topLabel = top
    ? `${top.from_track || top.from} → ${top.to_track || top.to}`.replace(
        /undefined/g,
        ""
      )
    : "—";
  const topScore = top?.overall ?? top?.score?.overall;
  el.innerHTML = `
    <div class="practice-summary-card">
      <div class="label">Avg score</div>
      <div class="value">${escapeHtml(String(avg))}</div>
      <div class="hint">${scored.length} scored transitions</div>
    </div>
    <div class="practice-summary-card">
      <div class="label">Save for set</div>
      <div class="value">${saveN}</div>
      <div class="hint">Gemini keepers</div>
    </div>
    <div class="practice-summary-card">
      <div class="label">Best blend</div>
      <div class="value" style="font-size:1rem;line-height:1.3">${
        topScore != null ? Number(topScore).toFixed(1) : "—"
      }</div>
      <div class="hint">${escapeHtml(
        typeof topLabel === "string" ? topLabel.slice(0, 64) : "—"
      )}</div>
    </div>`;
}

function renderPracticeAnalyzeStatus() {
  const el = $("practiceAnalyzeStatus");
  const btn = $("practiceAnalyzeBtn");
  const job = state.practiceAnalyzeJob;
  if (!el) return;
  if (!job) {
    el.hidden = true;
    if (btn) {
      btn.disabled = !state.practiceDetail?.transitions?.length;
      btn.textContent = "Analyze with Gemini";
    }
    return;
  }
  el.hidden = false;
  if (job.status === "running" || job.status === "queued") {
    const pct = job.total ? Math.round((100 * (job.done || 0)) / job.total) : 0;
    el.className = "badge warn";
    el.innerHTML = `${escapeHtml(String(job.current || "Listening"))} · ${
      job.done || 0
    }/${job.total || "?"} (${pct}%)` +
      `<div class="practice-progress"><i style="width:${pct}%"></i></div>`;
    if (btn) {
      btn.disabled = true;
      btn.textContent = "Listening…";
    }
  } else if (job.status === "done") {
    el.className = "badge ok";
    const cached = job.summary?.cached ?? 0;
    const scored = job.summary?.scored ?? job.done ?? 0;
    el.textContent =
      cached && cached === scored
        ? `Loaded ${scored} saved scores`
        : `Scored ${scored}` + (cached ? ` · ${cached} from save` : "");
    if (btn) {
      btn.disabled = !state.practiceDetail?.transitions?.length;
      btn.textContent = "Re-analyze all";
    }
  } else if (job.status === "error") {
    el.className = "badge bad";
    el.textContent = `Analysis failed: ${job.error || "unknown"}`;
    if (btn) {
      btn.disabled = !state.practiceDetail?.transitions?.length;
      btn.textContent = "Retry analysis";
    }
  }
}


function syncPracticeViewToggle() {
  document.querySelectorAll("#practiceViewToggle button").forEach((b) => {
    b.classList.toggle("active", b.dataset.practiceView === state.practiceView);
  });
  const mixView = $("practiceMixView");
  const bestView = $("practiceBestView");
  const isBest = state.practiceView === "best";
  if (mixView) mixView.hidden = isBest;
  if (bestView) bestView.hidden = !isBest;
  const analyzeBtn = $("practiceAnalyzeBtn");
  if (analyzeBtn) {
    analyzeBtn.hidden = isBest;
  }
  // Per-mix sort only applies to This mix view (lives inside #practiceMixView).
  const txSort = $("practiceTxSort");
  if (txSort) txSort.hidden = isBest;
  const hint = $("practiceViewBarHint");
  if (hint) {
    hint.textContent = isBest
      ? "Across all pj mixes"
      : "Per-mix transitions";
  }
  const bar = $("practiceViewBar");
  if (bar) bar.classList.toggle("is-best", isBest);
}

async function setPracticeView(view) {
  const next = view === "best" ? "best" : "mix";
  if (state.practiceView === next) {
    syncPracticeViewToggle();
    if (next === "best") await loadBestPracticeScores();
    return;
  }
  state.practiceView = next;
  syncPracticeViewToggle();
  if (next === "best") {
    await loadBestPracticeScores();
  } else {
    renderPracticePanel();
  }
}

async function loadBestPracticeScores() {
  state.practiceBestLoading = true;
  renderPracticeBestList();
  try {
    const params = new URLSearchParams({
      prefix: "pj",
      min_overall: "7.0",
      saved_only: "false",
      min_priority: "0",
    });
    const data = await api(`/api/practice/best?${params}`);
    state.practiceBestItems = data.items || [];
    renderPracticeBestList();
    setStatus(
      `Best for set · ${state.practiceBestItems.length} transitions (Gemini + priority)`
    );
  } catch (err) {
    setStatus(err.message || String(err), "error");
    state.practiceBestItems = [];
    renderPracticeBestList();
  } finally {
    state.practiceBestLoading = false;
    renderPracticeBestList();
  }
}

function renderPracticeBestList() {
  syncPracticeViewToggle();
  const el = $("practiceBestList");
  const countEl = $("practiceBestCount");
  if (!el) return;
  const items = state.practiceBestItems || [];
  if (countEl) countEl.textContent = String(items.length);
  if (state.practiceBestLoading && !items.length) {
    el.innerHTML = `<div class="empty">Loading best transitions…</div>`;
    return;
  }
  if (!items.length) {
    el.innerHTML = `<div class="empty">No keepers yet for pj mixes (save for set, overall ≥ 7, or priority ≥ 1).</div>`;
    return;
  }
  el.innerHTML = items
    .map((item) => {
      const overall = item.overall;
      const isSave = Boolean(item.save_for_set);
      const priority = Number(item.priority) || 0;
      const pills = `
        <div class="practice-best-score">
          ${scorePill("Overall", overall, { overall: true })}
          <div class="practice-tx-scores">
            ${scorePill("Smooth", item.smoothness)}
            ${scorePill("Flow", item.flow)}
            ${
              isSave
                ? `<span class="badge ok">Save</span>`
                : `<span class="badge neutral">Scout</span>`
            }
          </div>
        </div>`;
      const priBtns = [1, 2, 3, 4, 5]
        .map(
          (n) =>
            `<button type="button" class="practice-priority-btn ${
              priority === n ? "active" : ""
            }" data-id="${item.id}" data-priority="${n}" title="Priority ${n}${
              priority === n ? " (click again to clear)" : ""
            }">${n}</button>`
        )
        .join("");
      return `<article class="practice-best-row ${isSave ? "is-save" : ""}" data-id="${item.id}">
        ${pills}
        <div class="practice-best-main">
          <div class="practice-best-pair">
            <span>${escapeHtml(item.from_track || "")}</span>
            <span class="arrow">→</span>
            <span>${escapeHtml(item.to_track || "")}</span>
          </div>
          <div class="practice-best-mix">${escapeHtml(item.mix_name || "")}${
            item.at_sec != null ? ` · @ ${formatClock(item.at_sec)}` : ""
          }</div>
          ${
            item.comments
              ? `<div class="practice-tx-comments">${escapeHtml(item.comments)}</div>`
              : ""
          }
        </div>
        <div class="practice-best-actions">
          <div class="practice-priority" title="Priority tier (5 = must remember)">${priBtns}</div>
          <button type="button" class="btn primary practice-best-play" data-id="${item.id}">▶ Play</button>
        </div>
      </article>`;
    })
    .join("");

  el.querySelectorAll(".practice-priority-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = Number(btn.dataset.id);
      const n = Number(btn.dataset.priority);
      const cur = Number(
        (state.practiceBestItems || []).find((x) => Number(x.id) === id)
          ?.priority || 0
      );
      // Toggle off if clicking the active tier
      updateBestPriority(id, cur === n ? 0 : n);
    });
  });
  el.querySelectorAll(".practice-best-play").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = Number(btn.dataset.id);
      const row = (state.practiceBestItems || []).find(
        (x) => Number(x.id) === id
      );
      if (row) playBestPracticeItem(row);
    });
  });
}

async function updateBestPriority(id, priority) {
  try {
    const data = await api("/api/practice/score", {
      method: "POST",
      body: JSON.stringify({ id, priority }),
    });
    const updated = data.score;
    // Optimistic local reorder using server list rules
    state.practiceBestItems = (state.practiceBestItems || []).map((it) =>
      Number(it.id) === Number(id) ? { ...it, ...updated } : it
    );
    state.practiceBestItems.sort((a, b) => {
      const pr = (Number(b.priority) || 0) - (Number(a.priority) || 0);
      if (pr) return pr;
      const ov =
        (Number(b.overall) || -1) - (Number(a.overall) || -1);
      if (ov) return ov;
      const sv = (b.save_for_set ? 1 : 0) - (a.save_for_set ? 1 : 0);
      if (sv) return sv;
      const an = String(b.analyzed_at || "").localeCompare(
        String(a.analyzed_at || "")
      );
      if (an) return an;
      return String(a.mix_name || "").localeCompare(String(b.mix_name || ""));
    });
    // Drop rows that no longer match default inclusion if priority cleared
    // and they wouldn't otherwise qualify — reload to stay truthful.
    await loadBestPracticeScores();
  } catch (err) {
    setStatus(err.message || String(err), "error");
  }
}

async function playBestPracticeItem(item) {
  const mixPath = item.mix_path;
  if (!mixPath) {
    setStatus("Missing mix path for this transition.", "error");
    return;
  }
  let idx = state.tracks.findIndex((t) => t.path === mixPath);
  if (idx < 0) {
    // Mix not in current sidebar list — inject a synthetic row so player can load it.
    const name = mixPath.split("/").pop() || mixPath;
    state.practiceMixes = [
      ...(state.practiceMixes || []),
      { path: mixPath, name, is_practice: true },
    ];
    state.tracks = state.practiceMixes.map(practiceMixAsTrack);
    idx = state.tracks.length - 1;
    renderTrackList();
  }
  // Stay on Best view; selectTrack loads audio + detail for seeking.
  const prevView = state.practiceView;
  state.practiceView = "best";
  await selectTrack(idx);
  state.practiceView = prevView;
  syncPracticeViewToggle();
  // Ensure best list still visible after selectTrack → renderPracticePanel
  if (state.practiceView === "best") {
    renderPracticeBestList();
  }
  seekPracticeTransition(Number(item.at_sec) || 0, {
    play: true,
    index: item.transition_index,
  });
}

function renderPracticePanel() {
  const meta = $("practiceSetMeta");
  const tracksEl = $("practiceTrackList");
  const txEl = $("practiceTransitionList");
  const analyzeBtn = $("practiceAnalyzeBtn");
  if (!meta || !tracksEl || !txEl) return;
  renderPracticeDbBadge();
  renderPracticeAnalyzeStatus();
  syncPracticeViewToggle();
  if (state.practiceView === "best") {
    // Keep mix-detail state warm for playback, but render the Best shortlist.
    renderPracticeBestList();
    return;
  }

  const d = state.practiceDetail;
  if (!d) {
    meta.className = "practice-set-meta empty";
    meta.textContent = "Select a practice mix on the left to review transitions.";
    tracksEl.innerHTML = "";
    txEl.innerHTML = "";
    if ($("practiceTrackCount")) $("practiceTrackCount").textContent = "0";
    if ($("practiceSummary")) {
      $("practiceSummary").hidden = true;
      $("practiceSummary").innerHTML = "";
    }
    if (analyzeBtn) analyzeBtn.disabled = true;
    return;
  }

  if (analyzeBtn) {
    const busy =
      state.practiceAnalyzeJob &&
      ["running", "queued"].includes(state.practiceAnalyzeJob.status);
    analyzeBtn.disabled = busy || !(d.transitions || []).length;
  }

  const dur = d.duration_sec != null ? formatClock(d.duration_sec) : "—";
  const scoredN = (d.transitions || []).filter((t) => t.score?.overall != null).length;
  const txN = d.transition_count || (d.transitions || []).length || 0;
  meta.className = "practice-set-meta compact";
  // Stage already shows the mix name — keep a single stats row here.
  meta.innerHTML = `
    <div class="set-stats">
      <span class="badge ok">${d.track_count || 0} tracks</span>
      <span class="badge ${txN ? "ok" : "neutral"}">${txN} transitions</span>
      <span class="badge neutral">${escapeHtml(dur)}</span>
      <span class="badge ${scoredN ? "ok" : "neutral"}">${scoredN} scored</span>
    </div>
    ${
      txN
        ? ""
        : `<p class="hint practice-empty-hint">Need at least 2 named VDJ cues on this mix to build transitions.</p>`
    }`;

  renderPracticeSummary();

  const tracks = d.tracks || [];
  if ($("practiceTrackCount")) {
    $("practiceTrackCount").textContent = String(tracks.length);
  }
  tracksEl.innerHTML = tracks.length
    ? tracks
        .map(
          (t) => `<div class="practice-track-row">
            <span class="idx">${(t.index ?? 0) + 1}</span>
            <span class="time">${formatClock(t.pos_sec)}</span>
            <span>${escapeHtml(t.name)}</span>
          </div>`
        )
        .join("")
    : `<div class="empty">No named cues on this mix in VirtualDJ. Name hotcues on the recording (track titles) so we can build a tracklist.</div>`;

  const txs = sortedPracticeTransitions(d.transitions || []);
  if (!txs.length) {
    txEl.innerHTML = `<div class="empty">Need at least 2 named cues to show transitions.</div>`;
    return;
  }

  txEl.innerHTML = txs
    .map((tx, i) => {
      const s = tx.score || {};
      const overall = s.overall;
      const isSave = Boolean(s.save_for_set);
      const isWeak = overall != null && Number(overall) < 5.5;
      const cardCls = `practice-tx ${isSave ? "is-save" : ""} ${isWeak ? "is-weak" : ""}`.trim();

      const scoreHtml =
        overall != null
          ? `<div class="practice-tx-scores">
              ${scorePill("Overall", overall, { overall: true })}
              ${scorePill("Smooth", s.smoothness)}
              ${scorePill("Creative", s.creativity)}
              ${scorePill("Flow", s.flow)}
              ${scorePill("Energy", s.energy_match)}
              ${
                isSave
                  ? `<span class="badge ok">Save for set</span>`
                  : `<span class="badge neutral">Practice more</span>`
              }
            </div>`
          : `<div class="practice-tx-scores"><span class="score-pill">Not scored yet</span></div>`;

      const comments = s.comments
        ? `<div class="practice-tx-comments">${escapeHtml(s.comments)}</div>`
        : "";

      const better =
        s.better_option_track
          ? `<div class="practice-better-option">
              <div class="practice-better-kicker">Better option from your history/notes</div>
              <div class="practice-better-track">${escapeHtml(s.better_option_track)}</div>
              <div class="practice-better-reason">${escapeHtml(
                s.better_option_reason || ""
              )}</div>
              <div class="practice-better-meta">
                ${
                  s.better_option_source
                    ? `<span class="badge neutral">${escapeHtml(
                        s.better_option_source
                      )}</span>`
                    : ""
                }
                ${
                  s.better_option_confidence != null
                    ? `<span class="badge neutral">${Math.round(
                        Number(s.better_option_confidence) * 100
                      )}% conf.</span>`
                    : ""
                }
              </div>
            </div>`
          : "";

      const strengths = s.strengths || [];
      const improvements = s.improvements || [];
      const bullets =
        strengths.length || improvements.length
          ? `<div class="practice-tx-bullets">
              <div>
                <div class="col-title">Strengths</div>
                <ul>${
                  strengths.length
                    ? strengths.map((x) => `<li>${escapeHtml(x)}</li>`).join("")
                    : "<li>—</li>"
                }</ul>
              </div>
              <div>
                <div class="col-title">Improve</div>
                <ul>${
                  improvements.length
                    ? improvements.map((x) => `<li>${escapeHtml(x)}</li>`).join("")
                    : "<li>—</li>"
                }</ul>
              </div>
            </div>`
          : "";

      const alts = tx.alternatives || [];
      const altHtml = alts.length
        ? `<div class="practice-alts-title">Other options (notes + history)</div>
           <div class="practice-alts">${alts
             .map((a) => {
               const cnt =
                 a.count > 0
                   ? `<span class="practice-alt-count">×${a.count}</span>`
                   : a.vibe
                     ? `<span class="practice-alt-count">${escapeHtml(a.vibe)}</span>`
                     : "";
               const note =
                 a.note || a.vibe
                   ? `<span class="practice-alt-note">${escapeHtml(
                       [a.vibe && `Vibe: ${a.vibe}`, a.note]
                         .filter(Boolean)
                         .join(" · ")
                     )}</span>`
                   : "";
               return `<div class="practice-alt ${a.is_actual ? "is-actual" : ""}">
                <span class="practice-alt-source">${escapeHtml(a.source || "")}</span>
                <span class="practice-alt-label">${escapeHtml(a.to_label || "")}${note}</span>
                ${cnt}
              </div>`;
             })
             .join("")}</div>`
        : `<div class="practice-alt-empty">No note/history options matched the outgoing track.</div>`;

      return `<article class="${cardCls}" id="practice-tx-${escapeHtml(String(tx.index ?? i))}" data-at="${tx.at_sec}" data-index="${tx.index}">
        <div class="practice-tx-top">
          <div class="practice-tx-pair">
            <div class="practice-tx-from">${escapeHtml(tx.from_track)}</div>
            <div class="practice-tx-arrow-row">↓ transition · #${(tx.index ?? 0) + 1}</div>
            <div class="practice-tx-to">${escapeHtml(tx.to_track)}</div>
          </div>
          ${scoreHtml}
        </div>
        <div class="practice-tx-meta">
          <span>@ ${formatClock(tx.at_sec)}</span>
          <span>gap ~${formatClock(tx.duration_est_sec)}</span>
          <button type="button" class="btn primary practice-seek-btn" data-at="${tx.at_sec}" data-index="${tx.index}">▶ Play blend</button>
        </div>
        ${comments}
        ${better}
        ${bullets}
        ${altHtml}
      </article>`;
    })
    .join("");

  txEl.querySelectorAll(".practice-seek-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      seekPracticeTransition(Number(btn.dataset.at) || 0, {
        index: btn.dataset.index,
      });
    });
  });
  // Clicking a card body focuses it (without always restarting audio).
  txEl.querySelectorAll("article.practice-tx").forEach((card) => {
    card.addEventListener("click", (e) => {
      if (e.target.closest("button, a, input")) return;
      focusPracticeTransitionCard(
        Number(card.dataset.at) || 0,
        card.dataset.index
      );
    });
  });

  document.querySelectorAll("#practiceTxSort button").forEach((b) => {
    b.classList.toggle("active", b.dataset.txSort === state.practiceTxSort);
  });

  // Markers depend on transition list + scores
  drawPracticeWaveform();
}

async function loadPracticeDetail(path) {
  if (!path) return;
  // Don't interrupt an in-flight job for the same mix
  const sameJobRunning =
    state.practiceAnalyzeJob &&
    state.practiceAnalyzeJob.mix_path === path &&
    ["running", "queued"].includes(state.practiceAnalyzeJob.status);

  state.practiceMixPath = path;
  if (!sameJobRunning) state.practiceSummary = null;
  setStatus("Loading set analysis…");
  try {
    const data = await api(
      `/api/practice/set?path=${encodeURIComponent(path)}`
    );
    if (state.practiceMixPath !== path || !isPracticeMode()) return;
    state.practiceDetail = data;
    // Merge live job results if any
    if (
      state.practiceAnalyzeJob?.mix_path === path &&
      state.practiceAnalyzeJob.results
    ) {
      state.practiceDetail = mergeScoresIntoDetail(
        data,
        state.practiceAnalyzeJob.results
      );
      if (state.practiceAnalyzeJob.summary) {
        state.practiceSummary = state.practiceAnalyzeJob.summary;
      }
    }
    renderPracticePanel();
    drawPracticeWaveform();
    const meta = $("playerMeta");
    const track = currentTrack();
    if (meta && track) meta.innerHTML = buildPracticePlayerMetaHtml(track);

    const txs = state.practiceDetail.transitions || [];
    const scored = txs.filter((t) => t.score?.overall != null).length;
    const pending = txs.length - scored;
    setStatus(
      `${data.name}: ${data.track_count} tracks · ${data.transition_count} transitions` +
        (scored ? ` · ${scored} saved scores` : "") +
        (pending ? ` · ${pending} to analyze` : "")
    );

    // Auto-score any transitions not yet saved (never re-runs completed ones).
    if (!sameJobRunning && pending > 0) {
      await startPracticeAnalyze({ force: false });
    } else if (!sameJobRunning && scored > 0 && pending === 0) {
      // Build summary from saved scores for the header cards
      const list = txs
        .filter((t) => t.score?.overall != null)
        .map((t) => ({
          ...t.score,
          from_track: t.from_track,
          to_track: t.to_track,
          transition_index: t.index,
        }));
      const avg =
        list.reduce((s, r) => s + Number(r.overall), 0) / (list.length || 1);
      state.practiceSummary = {
        scored: list.length,
        cached: list.length,
        avg_overall: Math.round(avg * 10) / 10,
        top: [...list].sort((a, b) => Number(b.overall) - Number(a.overall)).slice(0, 5),
        save_for_set: list.filter((r) => r.save_for_set),
        better_options: list.filter((r) => r.better_option_track),
      };
      renderPracticePanel();
    }
  } catch (err) {
    state.practiceDetail = null;
    renderPracticePanel();
    setStatus(err.message || String(err), "error");
  } finally {
    setPlayerLoading(false);
  }
}

async function rebuildTransitionsDb() {
  setStatus("Rebuilding transitions database…");
  try {
    const stats = await api("/api/transitions/rebuild", { method: "POST" });
    state.practiceDb = stats;
    renderPracticeDbBadge();
    if (state.practiceMixPath) {
      await loadPracticeDetail(state.practiceMixPath);
    }
    setStatus(
      `Transitions DB rebuilt · notes ${stats.note_edges ?? stats.imported_notes ?? 0} · history ${stats.history_edges ?? stats.imported_history ?? 0}`
    );
  } catch (err) {
    setStatus(err.message || String(err), "error");
  }
}

function stopPracticeAnalyzePoll() {
  if (state.practiceAnalyzeTimer) {
    clearInterval(state.practiceAnalyzeTimer);
    state.practiceAnalyzeTimer = null;
  }
}

async function pollPracticeAnalyzeJob() {
  const job = state.practiceAnalyzeJob;
  if (!job?.id) return;
  try {
    const data = await api(`/api/practice/analyze/${encodeURIComponent(job.id)}`);
    const j = data.job;
    if (!j) return;
    state.practiceAnalyzeJob = j;
    if (j.results && state.practiceDetail && j.mix_path === state.practiceMixPath) {
      state.practiceDetail = mergeScoresIntoDetail(state.practiceDetail, j.results);
    }
    if (j.summary) state.practiceSummary = j.summary;
    renderPracticePanel();
    if (j.status === "done" || j.status === "error") {
      stopPracticeAnalyzePoll();
      if (j.status === "done") {
        setStatus(
          `Gemini analysis done · avg ${j.summary?.avg_overall ?? "—"} · ${
            j.summary?.save_for_set?.length ?? 0
          } save for set`
        );
      } else {
        setStatus(j.error || "Analysis failed", "error");
      }
    }
  } catch (err) {
    stopPracticeAnalyzePoll();
    setStatus(err.message || String(err), "error");
  }
}

async function startPracticeAnalyze({ force = false } = {}) {
  const path = state.practiceMixPath || currentTrack()?.path;
  if (!path) {
    setStatus("Select a practice mix first.", "error");
    return;
  }
  const txs = state.practiceDetail?.transitions || [];
  const n = txs.length;
  if (!n) {
    setStatus("No transitions to analyze on this mix.", "error");
    return;
  }
  // Already fully scored and not forcing — nothing to do
  const pending = txs.filter((t) => t.score?.overall == null).length;
  if (!force && pending === 0) {
    setStatus(`All ${n} transitions already scored (saved).`);
    return;
  }
  // Don't stack jobs for the same mix
  if (
    state.practiceAnalyzeJob &&
    state.practiceAnalyzeJob.mix_path === path &&
    ["running", "queued"].includes(state.practiceAnalyzeJob.status)
  ) {
    return;
  }
  stopPracticeAnalyzePoll();
  setStatus(
    force
      ? `Re-analyzing all ${n} transitions with Gemini…`
      : `Gemini listening to ${pending} new transition${pending === 1 ? "" : "s"} (${n - pending} already saved)…`
  );
  try {
    const data = await api("/api/practice/analyze", {
      method: "POST",
      body: JSON.stringify({ path, force }),
    });
    state.practiceAnalyzeJob = data.job;
    renderPracticeAnalyzeStatus();
    state.practiceAnalyzeTimer = setInterval(pollPracticeAnalyzeJob, 1500);
    await pollPracticeAnalyzeJob();
  } catch (err) {
    setStatus(err.message || String(err), "error");
    renderPracticeAnalyzeStatus();
  }
}

async function promoteTrack(destinationStage, { requireCued = null } = {}) {
  const track = currentTrack();
  if (!track) return;
  if (state.promoteInFlight) return;

  if (destinationStage === "ready_for_sort" && !track.is_cued) {
    setStatus("Cannot approve: track has no VDJ cue points yet.", "error");
    return;
  }

  state.promoteInFlight = true;
  try {
  const allowRunning = (await isVdjRunningFresh())
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
    no_cues_found: "Couldn't cue — parked",
    low_quality_skip: "Low quality — skipped",
    ac_low_quality: "AutoCue quality bad — parked",
  };
  setStatus(`Moving ${track.name} → ${labels[destinationStage] || destinationStage}…`);
  updateApproveButtons();
  ["approveBtn", "approveBtnSide"].forEach((id) => {
    const el = $(id);
    if (el) el.disabled = true;
  });

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
    // Refresh list without clobbering status; apply handoff after load.
    await loadTracks({ skipStatus: true });
    updatePipelineStrip();
    const handoff = (
      globalThis.MusicSorterStatusHandoff || window.MusicSorterStatusHandoff
    ).composePromoteSuccessHandoff(r, destinationStage);
    setStatus(
      handoff.message,
      handoff.kind,
      handoff.action
        ? {
            label: handoff.action.label,
            gotoMode: handoff.action.gotoMode,
            onClick: () => setMode(handoff.action.gotoMode),
          }
        : null
    );
  } catch (err) {
    setStatus(err.message, "error");
    updateApproveButtons();
  } finally {
    state.promoteInFlight = false;
  }
}

function skipToNextReviewTrack() {
  const indexes = filteredTrackIndexes();
  if (!indexes.length) return;
  const pos = indexes.indexOf(state.index);
  const next = indexes[pos + 1] ?? indexes[0];
  if (next !== state.index) selectTrack(next);
}

async function requestRecommendation(track, { force = false } = {}) {
  if (!track) return;
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
        force: Boolean(force),
      }),
      signal: controller.signal,
    });
    if (currentTrack()?.path !== track.path) return;
    state.recommendation = data.recommendation;
    renderRecommendation();
    // Soft-highlight folders after dual or single rec
    if (data.ok && data.recommendation) {
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
  const dests = state.selectedDests || [];
  if (!track || !dests.length) return;
  if (state.sortInFlight) return;
  if (!track.is_cued) {
    setStatus("Cannot sort: track is not cued.", "error");
    return;
  }

  if (dests.length > 1) {
    const destLabelConfirm = dests.map((d) => `${d.library}/${d.path}`).join("\n• ");
    const okMulti = await showConfirmDialog({
      title: "Sort into multiple destinations?",
      track: trackDisplayTitle(track),
      message: `• ${destLabelConfirm}\n• Cues Sorted archive (primary folder)`,
      note: "Primary library gets the move + VDJ retarget; others are copies with cloned cues.",
      confirmLabel: "Sort to all",
      tone: "accent",
    });
    if (!okMulti) return;
  }

  state.sortInFlight = true;
  const destLabel = dests.map((d) => `${d.library}/${d.path}`).join(", ");
  sortBtnBusy(true);
  try {
  const allowRunning = (await isVdjRunningFresh())
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

  setStatus(`Moving ${track.name} → ${destLabel}…`);

    const data = await api("/api/sort", {
      method: "POST",
      body: JSON.stringify({
        path: track.path,
        library: dests[0].library,
        relative_folder: dests[0].path,
        destinations: dests.map((d) => ({
          library: d.library,
          relative_folder: d.path,
        })),
        allow_vdj_running: Boolean(allowRunning),
      }),
    });
    const r = data.result;
    const archiveBits = [];
    const libBits = (r.library_dests || [])
      .map((d) => `${d.library}/${d.relative_folder || ""}`.replace(/\/$/, ""))
      .join(" + ");
    if (libBits) archiveBits.push(libBits);
    if (r.cues_sorted_copied) archiveBits.push("copied to Cues Sorted");
    else if (r.cues_sorted_already_present) archiveBits.push("already in Cues Sorted");
    if (r.cues_sorted_db_cloned) archiveBits.push("Cues Sorted VDJ entry cloned");
    if (r.sets_cues_copied) archiveBits.push(`cues copied to Pajamathon (${r.sets_cues_copied})`);
    else if ((r.sets_cues_skipped || 0) > 0) {
      archiveBits.push("Pajamathon already cued");
    }
    clearSelectedDests();
    // Refresh without clobbering status; remaining count comes from post-load list.
    await loadTracks({ skipStatus: true });
    await loadFolders();
    updatePipelineStrip();
    const handoff = (
      globalThis.MusicSorterStatusHandoff || window.MusicSorterStatusHandoff
    ).composeSortSuccessHandoff(r, state.tracks.length, archiveBits);
    setStatus(
      handoff.message,
      handoff.kind,
      handoff.action
        ? {
            label: handoff.action.label,
            gotoMode: handoff.action.gotoMode,
            onClick: () => setMode(handoff.action.gotoMode),
          }
        : null
    );
  } catch (err) {
    setStatus(err.message, "error");
  } finally {
    state.sortInFlight = false;
    sortBtnBusy(false);
  }
}

function sortBtnBusy(busy) {
  const sortBtn = $("sortBtn");
  if (sortBtn) {
    if (busy) sortBtn.dataset.busy = "1";
    else delete sortBtn.dataset.busy;
  }
  syncSortButtonState();
  if (sortBtn && busy) {
    sortBtn.disabled = true;
    sortBtn.textContent = "Sorting…";
  }
  const demoteBtn = $("demoteReadyBtn");
  if (demoteBtn) {
    demoteBtn.disabled = busy || !currentTrack() || isReviewMode();
    if (!busy) demoteBtn.textContent = "Back to Add Cues";
  }
  const removeBtn = $("removeReadyBtn");
  if (removeBtn) {
    removeBtn.disabled = busy || !currentTrack();
    if (!busy) removeBtn.textContent = "Trash from Ready";
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
    title: "Trash from Ready?",
    track: trackDisplayTitle(track),
    message:
      "This track will not be placed into House, Zouk, or Cues Sorted. Audio goes to Trash and its VirtualDJ Song entry is removed.",
    note: "Recoverable from Trash only until emptied. Close VirtualDJ first when possible.",
    confirmLabel: "Trash file + VDJ entry",
    tone: "danger",
  });
  if (!ok) return;

  let allowRunning = false;
  if (await isVdjRunningFresh()) {
    allowRunning = await showConfirmDialog({
      title: "VirtualDJ is still open",
      track: trackDisplayTitle(track),
      message:
        "Removing the VirtualDJ Song while VDJ is open may be overwritten on quit.",
      confirmLabel: "Trash anyway",
      tone: "warning",
    });
    if (!allowRunning) {
      setStatus("Close VirtualDJ, then trash from Ready.", "error");
      return;
    }
  }

  sortBtnBusy(true);
  setStatus(`Trashing ${track.name} from Ready for Sort…`);
  try {
    const data = await api("/api/remove-ready", {
      method: "POST",
      body: JSON.stringify({
        path: track.path,
        to_trash: true,
        allow_vdj_running: Boolean(allowRunning),
        remove_from_database: true,
      }),
    });
    setStatus(
      `Trashed from Ready: ${data.result?.name || track.name}`,
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

/** Delete Add Cues audio + stems and wipe VirtualDJ Song (cues/loops) for that path. */
async function deleteAddCuesTrack() {
  const track = currentTrack();
  if (!track) return;
  if (!isReviewMode()) {
    setStatus("Switch to Add Cues to delete a track from the cue queue.", "error");
    return;
  }

  const cueN = track.cues?.cue_count ?? 0;
  const loopN = track.cues?.loop_count ?? 0;
  const ok = await showConfirmDialog({
    title: "Delete from Add Cues?",
    track: trackDisplayTitle(track),
    message:
      "This permanently removes the track from Add Cues and deletes its VirtualDJ entry (cues and loops for this path).",
    note: `Audio${track.stems_path ? " + stems" : ""} → Trash (${cueN} cues, ${loopN} loops). Close VirtualDJ first if it is open.`,
    confirmLabel: "Delete to Trash",
    tone: "danger",
  });
  if (!ok) return;

  let allowRunning = false;
  if (await isVdjRunningFresh()) {
    allowRunning = await showConfirmDialog({
      title: "VirtualDJ is still open",
      track: trackDisplayTitle(track),
      message:
        "Deleting the database entry while VirtualDJ is open can be overwritten on quit. Close it first when possible.",
      confirmLabel: "Delete anyway",
      tone: "warning",
    });
    if (!allowRunning) {
      setStatus("Close VirtualDJ, then delete the track.", "error");
      return;
    }
  }

  sortBtnBusy(true);
  setStatus(`Deleting ${track.name}…`);
  try {
    const data = await api("/api/delete-add-cues", {
      method: "POST",
      body: JSON.stringify({
        path: track.path,
        to_trash: true,
        allow_vdj_running: allowRunning,
      }),
    });
    const r = data.result || {};
    const dbPart = r.database?.removed_from_db
      ? ` · VDJ entry removed (${r.had_cues || 0} cues, ${r.had_loops || 0} loops)`
      : r.in_database === false
        ? " · not in VDJ database"
        : "";
    setStatus(
      `Deleted → Trash: ${r.name || track.name}${dbPart}`,
      "success"
    );
    state.recommendation = null;
    await loadTracks();
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
  if (await isVdjRunningFresh()) {
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
    // Create under the library of the last-clicked folder, or all (Both).
    const createLib =
      state.library === "Both"
        ? state.selectedPathLibrary || "Both"
        : state.library;
    const data = await api("/api/folders", {
      method: "POST",
      body: JSON.stringify({
        library: createLib,
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
      // House / Zouk / Both only filters which tree is shown — never clear
      // multi-select destinations (chips still list House + Zouk picks).
      updatePathHint();
      updateSelectionLabels();
      await loadFolders();
    });
  });

  loadCrateFilter();
  syncCrateFilterUi();
  updateCueingFilterUi();
  document.querySelectorAll("#crateFilter button").forEach((btn) => {
    btn.addEventListener("click", () => {
      setCrateFilter(btn.dataset.crate || "all");
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

  $("batchAddCuesBtn")?.addEventListener("click", () => batchAddCuesForNotCued("all"));
  $("batchPajamathonCuesBtn")?.addEventListener("click", () =>
    batchAddCuesForNotCued("pajamathon")
  );
  $("batchFixGridsBtn")?.addEventListener("click", () => batchFixPajamathonGrids());
  $("practiceRebuildBtn")?.addEventListener("click", rebuildTransitionsDb);
  // Manual button forces a full re-score; auto-load uses force:false (saved scores kept).
  $("practiceAnalyzeBtn")?.addEventListener("click", () =>
    startPracticeAnalyze({ force: true })
  );
  bindPracticeWaveInteractions();
  document.querySelectorAll("#practiceTxSort button").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.practiceTxSort = btn.dataset.txSort || "order";
      document.querySelectorAll("#practiceTxSort button").forEach((b) =>
        b.classList.toggle("active", b === btn)
      );
      renderPracticePanel();
    });
  });
  document.querySelectorAll("#practiceViewToggle button").forEach((btn) => {
    btn.addEventListener("click", () => {
      setPracticeView(btn.dataset.practiceView || "mix");
    });
  });

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
    if (isRecsMode()) {
      await forceRefreshRecs();
      return;
    }
    if (isPracticeMode()) {
      await loadPracticeMixes();
      setStatus("Practice mixes refreshed.");
      return;
    }
    await loadTracks({ keepPath: currentTrack()?.path });
    if (!isReviewMode()) await loadFolders();
    setStatus("Refreshed.");
  });
  $("rerunRecBtn").addEventListener("click", () => {
    if (isReviewMode() || isPracticeMode()) return;
    const t = currentTrack();
    if (!t) return;
    requestRecommendation(t, { force: true });
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
  // Event delegation so Delete stays wired even if the panel re-renders.
  document.addEventListener("click", (ev) => {
    const t = ev.target;
    if (!(t instanceof Element)) return;
    const btn = t.closest("#deleteAddCuesBtn");
    if (!btn || btn.hasAttribute("disabled") || btn.disabled) return;
    ev.preventDefault();
    deleteAddCuesTrack();
  });

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
    stopPlayheadWatch();
    updatePlayhead();
    updateTransportUi();
  });
  audio.addEventListener("play", () => {
    if (state.loopPlaybackOn) startLoopWatch();
    startPlayheadWatch();
    updatePlayhead();
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
    if (audio.paused) playAudio(audio).catch(() => {});
    else audio.pause();
  });
  $("quietSessionChip")?.addEventListener("click", () => disableQuietSession());
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
    if (state.gridAlignMode || state.placeCueMode || state.placeLoopMode) return;
    resetWaveZoom();
    drawWaveform();
  });
  // Beatgrid align: drag ones on the wave
  // Loop move: drag a loop band / start handle
  $("waveformWrap").addEventListener("pointerdown", (e) => {
    if (hitTestWaveCueChrome(e.clientX, e.clientY)) {
      state.waveSeekTime = null;
      return;
    }
    if (onGridAlignPointerDown(e)) {
      state.waveSeekTime = null;
      e.stopPropagation();
      return;
    }
    if (!state.placeCueMode && !state.placeLoopMode && onLoopDragPointerDown(e)) {
      state.waveSeekTime = null;
      e.stopPropagation();
      return;
    }
    snapshotWaveSeekTime(e.clientX);
  });
  $("waveformWrap").addEventListener("pointermove", (e) => {
    onGridAlignPointerMove(e);
    onLoopDragPointerMove(e);
    if (state.placeCueMode && !state.loopDrag) {
      updatePlaceCuePreview(e.clientX, e.shiftKey);
    }
    if (state.placeLoopMode && !state.loopDrag) {
      updatePlaceLoopPreview(e.clientX, e.shiftKey);
    }
  });
  $("waveformWrap").addEventListener("pointerup", (e) => {
    onGridAlignPointerUp(e);
    onLoopDragPointerUp(e);
  });
  $("waveformWrap").addEventListener("pointercancel", (e) => {
    onGridAlignPointerUp(e);
    onLoopDragPointerUp(e);
  });
  // Cursor hint when hovering a draggable cue or loop
  $("waveformWrap").addEventListener("pointermove", (e) => {
    if (state.gridAlignMode || state.loopDrag || state.placeCueMode || state.placeLoopMode) return;
    const wrap = $("waveformWrap");
    if (!wrap) return;
    const chrome = hitTestWaveCueChrome(e.clientX, e.clientY);
    const cueHit = chrome ? null : hitTestCueAtClientX(e.clientX);
    const loopHit = chrome || cueHit ? null : hitTestLoopAtClientX(e.clientX);
    wrap.classList.toggle("cue-chrome-hover", Boolean(chrome));
    wrap.classList.toggle("cue-hover", Boolean(cueHit));
    wrap.classList.toggle("loop-hover", Boolean(loopHit));
  });
  $("waveformWrap").addEventListener("pointerleave", () => {
    $("waveformWrap")?.classList.remove("loop-hover", "cue-hover", "cue-chrome-hover");
    if (state.placeCueMode) {
      state.placeCuePreview = null;
      drawWaveform();
    }
    if (state.placeLoopMode) {
      state.placeLoopPreview = null;
      drawWaveform();
    }
  });
  window.addEventListener("resize", () => drawWaveform());

  $("zoukSpeedBtn").addEventListener("click", enableZoukSpeed);
  $("normalSpeedBtn").addEventListener("click", enableNormalSpeed);
  $("halfBpmBtn")?.addEventListener("click", toggleHalfBpm);
  $("beatOnesBtn")?.addEventListener("click", toggleBeatOnes);
  $("gridAlignBtn")?.addEventListener("click", () => {
    if (state.gridAlignMode) cancelGridAlignMode();
    else openGridAlignMode();
  });
  $("autoAlignGridBtn")?.addEventListener("click", () => attemptAutoGridAlign());
  $("gridAlignCancelBtn")?.addEventListener("click", cancelGridAlignMode);
  $("gridAlignApplyBtn")?.addEventListener("click", applyGridAlign);
  $("placeCueBtn")?.addEventListener("click", togglePlaceCueMode);
  $("placeCueDoneBtn")?.addEventListener("click", cancelPlaceCueMode);
  $("placeLoopBtn")?.addEventListener("click", togglePlaceLoopMode);
  $("placeLoopDoneBtn")?.addEventListener("click", cancelPlaceLoopMode);
  $("gridNudgeBarLeft")?.addEventListener("click", () => nudgeGridAlignBeats(-4));
  $("gridNudgeBeatLeft")?.addEventListener("click", () => nudgeGridAlignBeats(-1));
  $("gridNudgeBeatRight")?.addEventListener("click", () => nudgeGridAlignBeats(1));
  $("gridNudgeBarRight")?.addEventListener("click", () => nudgeGridAlignBeats(4));
  $("loopPlayBtn")?.addEventListener("click", toggleLoopPlayback);
  // Restore ones overlay preference (default on)
  try {
    const saved = localStorage.getItem("musicSorter.showBeatOnes");
    if (saved === "0") state.showBeatOnes = false;
    else if (saved === "1") state.showBeatOnes = true;
  } catch {
    /* ignore */
  }
  syncBeatOnesBtn();
  $("targetBpmInput").addEventListener("change", () => {
    state.targetBpm = Number($("targetBpmInput").value) || 75;
    if (state.zoukSpeedOn || state.playbackRate < 0.98) enableZoukSpeed();
    else updateSpeedUi();
  });
  document.querySelectorAll(".speed-preset[data-target-bpm]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const n = Number(btn.dataset.targetBpm);
      if (!Number.isFinite(n)) return;
      const input = $("targetBpmInput");
      if (input) input.value = String(n);
      state.targetBpm = n;
      enableZoukSpeed();
      updateSpeedUi();
    });
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
      if (!audio.src) return;
      if (audio.paused) playAudio(audio).catch(() => {});
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
    } else if (e.key === "g" || e.key === "G") {
      e.preventDefault();
      toggleBeatOnes();
    } else if (e.key === "c" || e.key === "C") {
      e.preventDefault();
      togglePlaceCueMode();
    } else if (e.key === "o" || e.key === "O") {
      e.preventDefault();
      togglePlaceLoopMode();
    } else if (e.key === "Escape") {
      if (state.placeCueMode) {
        e.preventDefault();
        cancelPlaceCueMode();
      } else if (state.placeLoopMode) {
        e.preventDefault();
        cancelPlaceLoopMode();
      } else if (state.gridAlignMode) {
        e.preventDefault();
        cancelGridAlignMode();
      }
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
  applyQuietSession();
  try {
    bindUi();
  } catch {
    /* keep booting */
  }
  try {
    bindAssembleUi();
  } catch {
    document.body.addEventListener("click", onAssembleChromeClick);
  }
  try {
    bindRecsUi();
  } catch {
    /* keep booting */
  }
  const params = new URLSearchParams(window.location.search);
  if (params.get("mode") === "add_cues" || params.get("add") === "1") {
    state.mode = "add_cues";
  }
  window.addEventListener("resize", () => {
    if (isPracticeMode()) schedulePracticeWaveRedraw();
  });
  applyModeUi();
  $("trackList")?.classList.add("list-loading");
  renderTrackList();
  try {
    await loadHealth();
    await loadTracks();
    if (isReviewMode()) await hydrateAutocueJobs();
    if (!isReviewMode()) {
      await loadFolders();
      selectFolder("");
    }
    requestAnimationFrame(resetWorkspaceScroll);
    setStatus("Ready. Use Sort, Add Cues, or Practice modes · Space / J/K");
  } catch (err) {
    setStatus(err.message, "error");
  }
}

boot();



/* ── Transition recommendations tab ─────────────────────────────────────── */

state.recsNow = null;
state.recsJobId = null;
state.recsPollTimer = null; // job status poll while Gemini runs
state.recsNowPollTimer = null; // VDJ now-playing poll (every 5s)
state.recsResult = null;
state.recsNowPollInFlight = false;
state.recsNowSeq = 0; // ignore stale now-playing responses
state.recsRetryTimer = null;
state.recsJobRunning = false;
state.recsAutoForPath = ""; // last path we auto-fetched recs for
state.recsPendingPath = ""; // path change while a job is running

const RECS_NOW_POLL_MS = 5_000;

function stopRecsPoll() {
  if (state.recsPollTimer) {
    clearInterval(state.recsPollTimer);
    state.recsPollTimer = null;
  }
}

function stopRecsNowPlayingPoll() {
  if (state.recsNowPollTimer) {
    clearInterval(state.recsNowPollTimer);
    state.recsNowPollTimer = null;
  }
  state.recsNowPollDueAt = 0;
  renderRecsPollCountdown();
}

function recsPollSecondsLeft() {
  const due = Number(state.recsNowPollDueAt || 0);
  if (!due) return 0;
  return Math.max(0, Math.ceil((due - Date.now()) / 1000));
}

function renderRecsPollCountdown() {
  const el = $("recsPollCountdown");
  if (!el) return;
  if (!isRecsMode() || !state.recsNowPollTimer) {
    el.textContent = "Next check in 5s";
    return;
  }
  const sec = recsPollSecondsLeft();
  el.textContent = sec <= 0 ? "Checking now…" : `Next check in ${sec}s`;
}

function armRecsNowPlayingPoll() {
  state.recsNowPollDueAt = Date.now() + RECS_NOW_POLL_MS;
  renderRecsPollCountdown();
}

function startRecsNowPlayingPoll() {
  stopRecsNowPlayingPoll();
  if (!isRecsMode()) return;
  armRecsNowPlayingPoll();
  state.recsNowPollTimer = setInterval(() => {
    if (!isRecsMode()) {
      stopRecsNowPlayingPoll();
      return;
    }
    renderRecsPollCountdown();
    if (recsPollSecondsLeft() > 0) return;
    armRecsNowPlayingPoll();
    if (state.recsNowPollInFlight) return;
    refreshRecsNowPlaying({ quiet: true, loadAudio: false });
  }, 250);
}

/** Auto-run Gemini energy buckets when now-playing is new or changed. */
function maybeAutoGenerateRecs(np, { force = false } = {}) {
  if (!isRecsMode()) return;
  const path = np?.path || "";
  if (!path) return;
  if (!force && path === state.recsAutoForPath && state.recsResult) return;
  if (state.recsJobRunning && !force) {
    // Queue this path; finish handler will re-run if still needed
    state.recsPendingPath = path;
    return;
  }
  if (force && state.recsJobRunning) {
    stopRecsPoll();
    state.recsJobRunning = false;
  }
  generateTransitionRecs({ auto: !force });
}

function showRecsSkeletons(label) {
  const buckets = $("recsBuckets");
  if (buckets) buckets.hidden = false;
  const msg = escapeHtml(label || "Ranking…");
  ["recsHigher", "recsSame", "recsLower"].forEach((id) => {
    const el = $(id);
    if (el) el.innerHTML = `<div class="recs-skel">${msg}</div><div class="recs-skel"></div>`;
  });
}

function setRecsStatus(msg, kind = "") {
  const el = $("recsStatus");
  if (!el) return;
  if (!msg) {
    el.hidden = true;
    el.textContent = "";
    el.className = "recs-status";
    return;
  }
  el.hidden = false;
  el.textContent = msg;
  el.className = `recs-status ${kind}`.trim();
}

function recsLastPlayLabel(np) {
  const raw = np?.lastplay_unix || np?.lastplay;
  if (!raw) return "";
  const ts = Number(raw);
  if (!Number.isFinite(ts) || ts <= 0) return "";
  const ageSec = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (ageSec < 15) return "just played";
  if (ageSec < 60) return `played ${ageSec}s ago`;
  if (ageSec < 3600) return `played ${Math.round(ageSec / 60)}m ago`;
  return `played ${Math.round(ageSec / 3600)}h ago`;
}

function renderRecsNowCard(np) {
  const title = $("recsNowTitle");
  const meta = $("recsNowMeta");
  const hint = $("recsNowHint");
  const timingEl = $("recsNowTiming");
  if (!title || !meta) return;
  if (!np) {
    title.textContent = "No recent VDJ play";
    meta.innerHTML = "";
    if (timingEl) {
      timingEl.hidden = true;
      timingEl.innerHTML = "";
    }
    if (hint) {
      hint.hidden = false;
      hint.textContent =
        "Play a track in VirtualDJ — this view auto-checks every 5s.";
    }
    return;
  }
  const label =
    np.artist && np.title
      ? `${np.artist} — ${np.title}`
      : np.title || np.name || "Unknown track";
  title.textContent = label;
  const chips = [];
  if (np.bpm) chips.push(`<span class="badge ok">${Number(np.bpm).toFixed(0)} BPM</span>`);
  if (np.key) chips.push(`<span class="badge neutral">${escapeHtml(np.key)}${np.camelot ? ` · ${escapeHtml(np.camelot)}` : ""}</span>`);
  if (np.genre) {
    const guessed = np.genre_source === "gemini";
    const genreTitle = guessed
      ? "Gemini genre guess (path/tag were unclear)"
      : np.genre_source === "path"
        ? "Genre from library folder"
        : "VDJ Genre tag";
    chips.push(
      `<span class="badge genre${guessed ? " guessed" : ""}" title="${genreTitle}">${escapeHtml(np.genre)}${guessed ? " · guessed" : ""}</span>`
    );
  }
  if (np.vibe) chips.push(`<span class="badge vibe" title="Folder vibe">${escapeHtml(np.vibe)}</span>`);
  if (np.is_cued) chips.push(`<span class="badge ok">${np.cue_count || 0} cues</span>`);
  else chips.push(`<span class="badge warn">not cued</span>`);
  const played = recsLastPlayLabel(np);
  if (played) chips.push(`<span class="badge neutral" title="VDJ last play">${escapeHtml(played)}</span>`);
  meta.innerHTML = chips.join("");
  if (timingEl) {
    const windows = Array.isArray(np.mix_windows) ? np.mix_windows : [];
    if (windows.length) {
      timingEl.hidden = false;
      timingEl.innerHTML = windows
        .slice(0, 2)
        .map((w) => {
          const missing = (w.missing || []).join(" + ");
          const present = (w.present || []).join(" + ");
          const hole = missing
            ? `needs ${escapeHtml(missing)}`
            : present
              ? escapeHtml(present)
              : "mix window";
          return `<div class="recs-timing-line" title="Outgoing frequency hole">
            <span class="recs-timing-clock">${escapeHtml(w.time || "")}</span>
            <span class="recs-timing-pair">${escapeHtml(w.label || "section")}</span>
            <span class="badge timing">${hole}</span>
          </div>`;
        })
        .join("");
    } else {
      timingEl.hidden = true;
      timingEl.innerHTML = "";
    }
  }
  if (hint) {
    hint.hidden = true;
  }
}

function renderRecsBucket(el, picks) {
  if (!el) return;
  if (!picks?.length) {
    el.innerHTML = `<div class="empty recs-empty">No picks in this bucket.</div>`;
    return;
  }
  el.innerHTML = picks
    .map((p) => {
      const conf = p.confidence != null ? Math.round(Number(p.confidence) * 100) : null;
      const genreLabel = p.genre || "";
      const vibeLabel = p.vibe || "";
      const timing = p.timing || null;
      const fills = (timing?.fills || []).join(" + ");
      const timingHtml = timing
        ? `<div class="recs-card-timing" title="${escapeHtml(timing.summary || "")}">
            <span class="recs-timing-clock">${escapeHtml(timing.out_time || "")} → ${escapeHtml(timing.in_time || "")}</span>
            <span class="recs-timing-pair">${escapeHtml(timing.out_label || "out")} → ${escapeHtml(timing.in_label || "in")}</span>
            ${fills ? `<span class="badge timing">fills ${escapeHtml(fills)}</span>` : ""}
          </div>`
        : "";
      return `<article class="recs-card" data-path="${escapeHtml(p.path || "")}">
        <div class="recs-card-title">${escapeHtml(p.artist || "")}${p.artist && p.title ? " — " : ""}${escapeHtml(p.title || p.name || "Track")}</div>
        <div class="recs-card-meta">
          ${p.bpm != null ? `<span class="badge ok">${Number(p.bpm).toFixed(0)} BPM</span>` : ""}
          ${p.key ? `<span class="badge neutral">${escapeHtml(p.key)}${p.camelot ? ` · ${escapeHtml(p.camelot)}` : ""}</span>` : ""}
          ${genreLabel ? `<span class="badge genre" title="Genre">${escapeHtml(genreLabel)}</span>` : ""}
          ${vibeLabel ? `<span class="badge vibe" title="Folder vibe">${escapeHtml(vibeLabel)}</span>` : !genreLabel ? `<span class="badge warn">genre ?</span>` : ""}
          ${p.library ? `<span class="badge neutral">${escapeHtml(p.library)}</span>` : ""}
          ${p.history_count ? `<span class="badge ok">history ×${p.history_count}</span>` : ""}
          ${conf != null ? `<span class="badge neutral">${conf}%</span>` : ""}
        </div>
        ${timingHtml}
        <p class="recs-card-reason">${escapeHtml(p.reason || "")}</p>
        <div class="recs-card-path" title="${escapeHtml(p.path || "")}">${escapeHtml(p.relative_path || p.name || "")}</div>
      </article>`;
    })
    .join("");
}

function renderRecsFilterBar(result) {
  const bar = $("recsFilterBar");
  const label = $("recsFilterLabel");
  const count = $("recsFilterCount");
  const detail = $("recsFilterDetail");
  if (!bar) return;
  const n = result?.candidates_considered ?? 0;
  const filters = result?.filters || {};
  const src = result?.source || state.recsNow || {};
  const bpmTol = filters.bpm_tolerance ?? 5;
  const filterLabel =
    filters.label || `In-key · ±${bpmTol} BPM · cued`;
  bar.hidden = false;
  if (label) label.textContent = filterLabel;
  if (count) count.textContent = `${n} match${n === 1 ? "" : "es"}`;
  if (detail) {
    const bits = [];
    if (src.bpm != null) bits.push(`${Number(src.bpm).toFixed(0)} BPM`);
    if (src.key) bits.push(`${src.key}${src.camelot ? ` · ${src.camelot}` : ""}`);
    if (src.genre) {
      bits.push(
        src.genre_source === "gemini" ? `${src.genre} (guessed)` : src.genre
      );
    } else if (src.vibe) bits.push(src.vibe);
    bits.push("→ genre-aware higher / same / lower");
    detail.textContent = bits.join(" · ");
  }
}

function renderRecsResult(result) {
  state.recsResult = result;
  renderRecsFilterBar(result);
  const recs = result?.recommendations || {};
  const buckets = $("recsBuckets");
  if (buckets) buckets.hidden = false;
  renderRecsBucket($("recsHigher"), recs.higher_energy || []);
  renderRecsBucket($("recsSame"), recs.same_energy || []);
  renderRecsBucket($("recsLower"), recs.lower_energy || []);
  const notes = $("recsNotes");
  if (notes) {
    const n = recs.notes || "";
    notes.hidden = !n;
    notes.textContent = n;
  }
  const hist = result?.history_options || [];
  const drawer = $("recsHistoryDrawer");
  const body = $("recsHistoryBody");
  const count = $("recsHistoryCount");
  if (drawer && body) {
    drawer.hidden = !hist.length;
    if (count) count.textContent = String(hist.length);
    body.innerHTML = hist
      .map(
        (h) =>
          `<div class="recs-hist-row"><span class="badge neutral">${escapeHtml(
            h.source || ""
          )}</span> <strong>${escapeHtml(h.to_label || "")}</strong> ${
            h.count ? `<span class="badge ok">×${h.count}</span>` : ""
          } ${h.note ? `<span class="subtitle">${escapeHtml(h.note)}</span>` : ""}</div>`
      )
      .join("");
  }
}

async function refreshRecsNowPlaying({
  loadAudio = false,
  quiet = false,
  skipAuto = false,
  forceAuto = false,
} = {}) {
  // Quiet polls yield to an in-flight read. Manual/force always starts a new one.
  if (state.recsNowPollInFlight && quiet && !forceAuto) return state.recsNow;
  state.recsNowPollInFlight = true;
  const seq = ++state.recsNowSeq;
  const prevPath = state.recsNow?.path || "";
  if (!quiet) setRecsStatus("Reading VirtualDJ…");
  try {
    const qs = forceAuto ? "fast=1&refresh=1" : "fast=1";
    const data = await api(`/api/recs/now-playing?${qs}`, { timeoutMs: 4000 });
    if (seq !== state.recsNowSeq) return state.recsNow;
    const np = data.now_playing;
    const changed = (np?.path || "") !== prevPath;
    state.recsNow = np;
    renderRecsNowCard(np);
    // Fill BPM/key/genre without blocking Refresh
    if (np?.path) {
      api("/api/recs/now-playing", { timeoutMs: 12000 })
        .then((full) => {
          if (seq !== state.recsNowSeq) return;
          const rich = full.now_playing;
          if (!rich?.path || rich.path !== state.recsNow?.path) return;
          state.recsNow = rich;
          renderRecsNowCard(rich);
          if (isRecsMode()) renderTrackList();
          updatePipelineStrip();
        })
        .catch(() => {});
    }

    if (!quiet || changed || !np) {
      setRecsStatus(
        np
          ? `Now playing · ${np.artist ? np.artist + " — " : ""}${np.title || np.name}${
              np.key || np.bpm != null
                ? ` · ${np.bpm != null ? Number(np.bpm).toFixed(0) + " BPM" : ""}${
                    np.key ? (np.bpm != null ? " · " : "") + np.key : ""
                  }`
                : ""
            }`
          : "No VDJ history yet — play something in VirtualDJ (auto-checks every 5s).",
        np ? "ok" : "warn"
      );
    }

    // Recs rail is driven by recsNow — don't fake a Sort-queue track (NaN MB).
    if (isRecsMode()) {
      renderTrackList();
    }
    updatePipelineStrip();

    // Auto-fetch higher/same/lower when track appears, changes, or user forced
    if (
      !skipAuto &&
      np?.path &&
      (forceAuto ||
        changed ||
        !state.recsResult ||
        state.recsAutoForPath !== np.path)
    ) {
      maybeAutoGenerateRecs(np, { force: forceAuto });
    }
    if (state.recsRetryTimer) {
      clearTimeout(state.recsRetryTimer);
      state.recsRetryTimer = null;
    }
    return np;
  } catch (err) {
    if (!quiet) setRecsStatus(err.message || String(err), "error");
    if (isRecsMode() && !state.recsRetryTimer) {
      state.recsRetryTimer = setTimeout(() => {
        state.recsRetryTimer = null;
        if (isRecsMode()) refreshRecsNowPlaying({ quiet: true, loadAudio: false });
      }, 2000);
    }
    return null;
  } finally {
    if (seq === state.recsNowSeq) state.recsNowPollInFlight = false;
  }
}

function _setRecsGenerateBtnIdle() {
  const btn = $("recsGenerateBtn");
  if (!btn) return;
  btn.disabled = false;
  btn.textContent = "Refresh";
}

async function generateTransitionRecs({ auto = false } = {}) {
  if (state.recsJobRunning && auto) {
    if (state.recsNow?.path) state.recsPendingPath = state.recsNow.path;
    return;
  }
  // Manual click always cancels in-flight poll and starts a new job
  stopRecsPoll();
  state.recsJobRunning = true;
  const pathForJob = state.recsNow?.path || null;
  const btn = $("recsGenerateBtn");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Refreshing…";
  }
  showRecsSkeletons(auto ? "Ranking energy…" : "Refreshing recs…");
  setRecsStatus(
    auto
      ? "Filtering in-key ±5 BPM, ranking higher / same / lower…"
      : "Refreshing now-playing and ranking recs…",
    "running"
  );
  if (isRecsMode()) {
    setStatus("Ranking next tracks…");
  }
  try {
    // Always re-read VDJ history. Do not rank a stale recsNow after a failed fetch.
    const np = await refreshRecsNowPlaying({
      loadAudio: false,
      quiet: true,
      skipAuto: true,
      forceAuto: true,
    });
    const body = {
      path: np?.path || state.recsNow?.path || null,
      use_gemini: true,
      force_rescan: false,
      sync: false,
    };
    if (!body.path) {
      throw new Error("No now-playing track — play something in VirtualDJ.");
    }
    const data = await api("/api/recs/transitions", {
      method: "POST",
      body: JSON.stringify(body),
    });
    if (data.sync && data.result) {
      state.recsAutoForPath = body.path;
      state.recsJobRunning = false;
      renderRecsResult(data.result);
      setRecsStatus(
        `Ready · ${data.result.candidates_considered || 0} in-key ±5 BPM · ${
          data.result.recommendations?.model || ""
        }`,
        "ok"
      );
      _setRecsGenerateBtnIdle();
      return;
    }
    const job = data.job;
    if (!job?.id) throw new Error("No job returned");
    state.recsJobId = job.id;
    setRecsStatus(job.message || "Running…", "running");
    state.recsPollTimer = setInterval(async () => {
      try {
        const res = await api(`/api/recs/transitions/${job.id}`);
        const j = res.job;
        if (!j) return;
        // Ignore stale job polls if a newer job superseded this one
        if (state.recsJobId && state.recsJobId !== job.id) return;
        setRecsStatus(
          j.message || j.status,
          j.status === "error" ? "error" : "running"
        );
        if (j.status === "ok" || j.status === "error") {
          stopRecsPoll();
          state.recsJobRunning = false;
          _setRecsGenerateBtnIdle();
          if (j.status === "ok" && j.result) {
            state.recsAutoForPath = body.path || j.result?.source?.path || "";
            const src = j.result.source;
            const jobPath = src?.path || body.path || "";
            const nowTs = Number(state.recsNow?.lastplay_unix || 0);
            const srcTs = Number(src?.lastplay_unix || 0);
            const recsIsNewer =
              state.recsNow?.path &&
              state.recsNow.path !== jobPath &&
              (nowTs === 0 || srcTs === 0 || nowTs >= srcTs);
            // Don't clobber a newer now-playing the user just started
            if (src && !recsIsNewer && (!state.recsNow?.path || state.recsNow.path === jobPath)) {
              state.recsNow = src;
              renderRecsNowCard(src);
            }
            renderRecsResult(j.result);
            const n = j.result.candidates_considered || 0;
            setRecsStatus(
              `Ready · ${n} in-key ±5 BPM matches · higher / same / lower ranked · ${
                j.result.recommendations?.model || ""
              }`,
              "ok"
            );
          } else {
            setRecsStatus(j.error || j.message || "Failed", "error");
          }
          // If now-playing changed during the job, auto-run again
          const pending = state.recsPendingPath;
          state.recsPendingPath = "";
          if (
            pending &&
            pending !== state.recsAutoForPath &&
            isRecsMode()
          ) {
            maybeAutoGenerateRecs({ path: pending }, { force: true });
          }
        }
      } catch (err) {
        if (state.recsJobId && state.recsJobId !== job.id) return;
        stopRecsPoll();
        state.recsJobRunning = false;
        _setRecsGenerateBtnIdle();
        setRecsStatus(err.message || String(err), "error");
      }
    }, 900);
  } catch (err) {
    state.recsJobRunning = false;
    _setRecsGenerateBtnIdle();
    setRecsStatus(err.message || String(err), "error");
  }
}

async function forceRefreshRecs() {
  stopRecsPoll();
  state.recsJobRunning = false;
  state.recsAutoForPath = "";
  state.recsPendingPath = "";
  state.recsNowPollInFlight = false;
  if (isRecsMode()) {
    if (!state.recsNowPollTimer) startRecsNowPlayingPoll();
    else armRecsNowPlayingPoll();
  }
  setRecsStatus("Picking up now-playing…", "running");
  showRecsSkeletons("Picking up track…");
  _setRecsGenerateBtnIdle();
  const np = await refreshRecsNowPlaying({
    loadAudio: false,
    quiet: false,
    forceAuto: true,
  });
  if (!np) {
    setRecsStatus("No VDJ history play found — load a track in VirtualDJ.", "warn");
    _setRecsGenerateBtnIdle();
  }
}

function bindRecsUi() {
  const gen = $("recsGenerateBtn");
  if (gen && !gen.dataset.bound) {
    gen.dataset.bound = "1";
    gen.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      forceRefreshRecs();
    });
  }
  const ref = $("recsRefreshNowBtn");
  if (ref && !ref.dataset.bound) {
    ref.dataset.bound = "1";
    ref.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      forceRefreshRecs();
    });
  }
}

state.assembleJob = null;
state.assemblePollTimer = null;
state.assemblePollSeq = 0;
state.assemblePreview = null;
state.assemblePlaylistSort = "crate";
state.assembleLaneShares = null;
state.assembleMinFit = null;
state.assembleMixTimer = null;
state.assembleMixPrefsTimer = null;

function renderAssembleRail() {
  const root = $("trackList");
  if (!root) return;
  const newest = state.assemblePreview?.newest || [];
  const total = state.assemblePreview?.total;
  if (!newest.length) {
    root.innerHTML = emptyStateHtml({
      icon: "☰",
      title: "Zouk crate",
      copy: "Newest Zouk tracks will list here. Assemble scores them for Pajamathon.",
      ctaLabel: "",
      ctaMode: "",
    });
    return;
  }
  root.innerHTML = `<div class="assemble-rail-head">Newest · ${total || newest.length}</div>${newest
    .map(
      (t) => `<div class="track assemble-rail-track">
        <div class="track-title">${escapeHtml(t.title || t.name || "")}</div>
        <div class="track-sub">${escapeHtml(t.artist || t.relative_path || "")}</div>
      </div>`
    )
    .join("")}`;
}

function setAssembleStatus(msg, kind = "") {
  const el = $("assembleStatus");
  if (!el) return;
  if (!msg) {
    el.hidden = true;
    el.textContent = "";
    return;
  }
  el.hidden = false;
  el.textContent = msg;
  el.className = `recs-status ${kind}`.trim();
}

function assembleSongKey(t) {
  const artist = (t.artist || "")
    .toLowerCase()
    .split(/[,&+/]| feat\.? | ft\.? | featuring | and /i)[0]
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
  let title = (t.title || t.name || "").toLowerCase();
  title = title.replace(/^\d+[\s.\-]+/, "");
  title = title.replace(/\([^)]*\)/g, " ");
  title = title.replace(
    /\b(original mix|extended mix|radio edit|club mix|remix|mix|edit|version)\b/g,
    " "
  );
  title = title.replace(/[^a-z0-9]+/g, " ").replace(/\s+/g, " ").trim();
  return `${artist}|${title}`;
}

function uniqueAssembleTracks(tracks) {
  const seen = new Set();
  return (tracks || []).filter((t) => {
    const key = assembleSongKey(t);
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function assembleFitPercent(track) {
  return Math.round(Number(track?.fit || 0) * 100);
}

function sortAssemblePlaylist(tracks, mode) {
  const rows = Array.isArray(tracks) ? tracks.slice() : [];
  if ((mode || "crate") !== "fit") return rows;
  return rows.sort((a, b) => {
    const fitDelta = assembleFitPercent(b) - assembleFitPercent(a);
    if (fitDelta) return fitDelta;
    const titleA = `${a?.artist || ""} ${a?.title || a?.name || ""}`.toLowerCase();
    const titleB = `${b?.artist || ""} ${b?.title || b?.name || ""}`.toLowerCase();
    return titleA.localeCompare(titleB);
  });
}

function syncAssemblePlaylistSortUi() {
  const mode = state.assemblePlaylistSort || "crate";
  document.querySelectorAll("#assemblePlaylistSort button").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.plSort === mode);
  });
}

const ASSEMBLE_LANES = [
  ["chill", "Chill"],
  ["energy", "Energy"],
  ["rnb", "R&B"],
  ["kizouk", "Kizouk"],
  ["lamba", "Lamba"],
  ["trancy", "Trancy"],
  ["hiphop", "Hip-hop"],
  ["remixes", "Remixes"],
  ["tribal", "Tribal"],
  ["bassy", "Bassy"],
  ["experimental", "Experimental"],
  ["intense", "Intense"],
  ["beautiful", "Beautiful"],
  ["classics", "Classics"],
  ["neo_zouk", "Neo Zouk"],
  ["pop", "Pop"],
  ["nostalgia", "Nostalgia"],
  ["reggaeton", "Reggaeton"],
  ["trippy", "Trippy"],
  ["world", "World"],
  ["other", "Other"],
];
const ASSEMBLE_SHARE_STORE = "assembleLaneShares.v3";
const ASSEMBLE_MIN_FIT_STORE = "assembleMinFit.v3";
const ASSEMBLE_DEFAULT_MIN_FIT = 0.6;

function defaultAssembleShares() {
  return {
    chill: 0.24,
    energy: 0.14,
    rnb: 0.12,
    kizouk: 0.1,
    lamba: 0.08,
    trancy: 0.05,
    hiphop: 0.04,
    remixes: 0.06,
    classics: 0.05,
    nostalgia: 0.04,
    tribal: 0,
    bassy: 0,
    experimental: 0,
    intense: 0,
    beautiful: 0,
    neo_zouk: 0,
    pop: 0,
    reggaeton: 0,
    trippy: 0,
    world: 0,
    other: 0.08,
  };
}

function normalizeClientShares(raw) {
  const out = {};
  let any = false;
  ASSEMBLE_LANES.forEach(([lane]) => {
    let num = Number(raw?.[lane]);
    if (!Number.isFinite(num) || num < 0) num = 0;
    if (num > 1.5) num /= 100;
    out[lane] = Math.max(0, Math.min(1, num));
    if (out[lane] > 0) any = true;
  });
  if (!any) return defaultAssembleShares();
  const total = Object.values(out).reduce((a, b) => a + b, 0);
  if (total > 1 + 1e-6) {
    ASSEMBLE_LANES.forEach(([lane]) => {
      out[lane] = Math.round((out[lane] / total) * 10000) / 10000;
    });
  }
  return out;
}

function sharesToPercents(shares) {
  const norm = normalizeClientShares(shares);
  const raw = {};
  ASSEMBLE_LANES.forEach(([lane]) => {
    raw[lane] = Math.round((norm[lane] || 0) * 100);
  });
  return raw;
}

function readAssembleMixSharesFromDom() {
  const root = $("assembleMixLanes");
  if (!root) return null;
  const raw = {};
  let any = false;
  root.querySelectorAll("input.assemble-mix-pct").forEach((el) => {
    const n = Number(el.value || 0);
    raw[el.dataset.lane] = n;
    if (n > 0) any = true;
  });
  return any ? normalizeClientShares(raw) : null;
}

function loadAssembleShares() {
  try {
    const stored = JSON.parse(localStorage.getItem(ASSEMBLE_SHARE_STORE) || "null");
    if (stored && typeof stored === "object") return normalizeClientShares(stored);
  } catch {
    /* ignore */
  }
  const saved = state.assemblePreview?.mix_prefs;
  if (saved?.saved && saved.lane_shares) return normalizeClientShares(saved.lane_shares);
  const fromDom = readAssembleMixSharesFromDom();
  if (fromDom) return fromDom;
  const preview = state.assemblePreview?.defaults?.lane_shares;
  if (preview) return normalizeClientShares(preview);
  return defaultAssembleShares();
}

function persistAssembleShares(shares, opts) {
  const syncServer = !opts || opts.syncServer !== false;
  state.assembleLaneShares = normalizeClientShares(shares);
  try {
    localStorage.setItem(ASSEMBLE_SHARE_STORE, JSON.stringify(state.assembleLaneShares));
  } catch {
    /* ignore */
  }
  if (syncServer) scheduleSaveAssembleMixPrefs();
}

function scheduleSaveAssembleMixPrefs() {
  if (state.assembleMixPrefsTimer) clearTimeout(state.assembleMixPrefsTimer);
  state.assembleMixPrefsTimer = setTimeout(() => {
    state.assembleMixPrefsTimer = null;
    saveAssembleMixPrefs();
  }, 280);
}

async function saveAssembleMixPrefs() {
  const shares = state.assembleLaneShares || readAssembleMixShares();
  const minFit = state.assembleMinFit ?? readAssembleMinFit();
  try {
    await api("/api/assemble/mix-prefs", {
      method: "POST",
      body: JSON.stringify({
        lane_shares: sharesToPercents(shares),
        min_fit: minFit,
      }),
      timeoutMs: 8000,
    });
  } catch {
    /* keep local copy even if Notes write fails */
  }
}

function applySavedMixPrefs(prefs) {
  if (!prefs?.saved || !prefs.lane_shares) return;
  if (document.activeElement?.closest?.("#assembleMix")) return;
  try {
    if (localStorage.getItem(ASSEMBLE_SHARE_STORE)) return;
  } catch {
    /* ignore */
  }
  persistAssembleShares(prefs.lane_shares, { syncServer: false });
  if (prefs.min_fit != null) persistAssembleMinFit(prefs.min_fit, { syncServer: false });
  writeAssembleMixTuners(state.assembleLaneShares);
  writeAssembleMinFit(state.assembleMinFit);
}

function normalizeClientMinFit(raw) {
  let num = Number(raw);
  if (!Number.isFinite(num)) return ASSEMBLE_DEFAULT_MIN_FIT;
  if (num > 1.5) num /= 100;
  return Math.max(0, Math.min(1, num));
}

function loadAssembleMinFit() {
  try {
    const stored = localStorage.getItem(ASSEMBLE_MIN_FIT_STORE);
    if (stored != null && stored !== "") return normalizeClientMinFit(stored);
  } catch {
    /* ignore */
  }
  const saved = state.assemblePreview?.mix_prefs;
  if (saved?.saved && saved.min_fit != null) return normalizeClientMinFit(saved.min_fit);
  const preview = state.assemblePreview?.defaults?.min_fit;
  if (preview != null) return normalizeClientMinFit(preview);
  return ASSEMBLE_DEFAULT_MIN_FIT;
}

function persistAssembleMinFit(value, opts) {
  const syncServer = !opts || opts.syncServer !== false;
  state.assembleMinFit = normalizeClientMinFit(value);
  try {
    localStorage.setItem(ASSEMBLE_MIN_FIT_STORE, String(state.assembleMinFit));
  } catch {
    /* ignore */
  }
  if (syncServer) scheduleSaveAssembleMixPrefs();
}

function readAssembleMinFit() {
  const el = $("assembleMinFitNum") || $("assembleMinFit");
  if (!el) return state.assembleMinFit ?? loadAssembleMinFit();
  return normalizeClientMinFit(el.value);
}

function writeAssembleMinFit(value) {
  const frac = normalizeClientMinFit(value);
  const pct = Math.round(frac * 100);
  const range = $("assembleMinFit");
  const num = $("assembleMinFitNum");
  if (range) range.value = String(pct);
  if (num) num.value = String(pct);
}

function readAssembleMixShares() {
  const root = $("assembleMixLanes");
  if (!root) return state.assembleLaneShares || loadAssembleShares();
  const raw = {};
  root.querySelectorAll("input.assemble-mix-pct").forEach((el) => {
    raw[el.dataset.lane] = Number(el.value || 0);
  });
  return normalizeClientShares(raw);
}

function syncAssembleMixSum() {
  const root = $("assembleMixLanes");
  const sumEl = $("assembleMixSum");
  if (!root || !sumEl) return;
  let sum = 0;
  root.querySelectorAll("input.assemble-mix-pct").forEach((el) => {
    sum += Number(el.value || 0);
  });
  const leftover = 100 - sum;
  sumEl.textContent =
    leftover === 0 ? "100%" : leftover > 0 ? `${sum}% · ${leftover}% leftover` : `${sum}%`;
  sumEl.classList.toggle("assemble-mix-sum-warn", leftover < 0);
}

function writeAssembleMixTuners(shares) {
  const pcts = sharesToPercents(shares);
  document.querySelectorAll("#assembleMixLanes input.assemble-mix-pct").forEach((el) => {
    el.value = String(pcts[el.dataset.lane] || 0);
  });
  syncAssembleMixSum();
}

function renderAssembleMixTuners() {
  const root = $("assembleMixLanes");
  if (!root) return;
  if (!state.assembleLaneShares) state.assembleLaneShares = loadAssembleShares();
  if (!root.querySelector("input.assemble-mix-pct")) {
    const pcts = sharesToPercents(state.assembleLaneShares);
    root.innerHTML = ASSEMBLE_LANES.map(([id, label]) => {
      const pct = pcts[id] || 0;
      return `<label class="assemble-mix-lane">
        <span class="assemble-mix-name">${label}</span>
        <input type="number" class="assemble-mix-pct" min="0" max="100" step="1" value="${pct}" data-lane="${id}" aria-label="${label} target percent" />
        <span class="subtitle">%</span>
        <span class="assemble-mix-actual" data-lane="${id}">—</span>
      </label>`;
    }).join("");
  }
  writeAssembleMixTuners(state.assembleLaneShares);
}

function updateAssembleMixActuals(mix, playlistLen) {
  const total =
    playlistLen ||
    Object.values(mix || {}).reduce((a, b) => a + Number(b || 0), 0);
  document.querySelectorAll(".assemble-mix-actual").forEach((el) => {
    const n = Number((mix || {})[el.dataset.lane] || 0);
    const pct = total ? Math.round((n / total) * 100) : 0;
    el.textContent = total ? `${pct}% now` : "—";
  });
}

function renderAssembleLists(result) {
  const playlist = result?.playlist || [];
  const ranked = result?.ranked || [];
  const mix = result?.mix || {};
  updateAssembleMixActuals(mix, playlist.length);
  if (
    state.assembleMinFit == null &&
    result?.min_fit != null &&
    !document.activeElement?.closest?.(".assemble-min-fit")
  ) {
    persistAssembleMinFit(result.min_fit, { syncServer: false });
    writeAssembleMinFit(result.min_fit);
  }
  const pl = $("assemblePlaylist");
  const rk = $("assembleRanked");
  const pc = $("assemblePlaylistCount");
  const rc = $("assembleRankedCount");
  if (pc) pc.textContent = String(playlist.length);
  if (rc) rc.textContent = `${result?.scored_total || ranked.length} scored`;
  if (pl) {
    const uniquePl = sortAssemblePlaylist(
      uniqueAssembleTracks(playlist),
      state.assemblePlaylistSort || "crate"
    );
    if (pc) pc.textContent = String(uniquePl.length);
    syncAssemblePlaylistSortUi();
    pl.innerHTML = uniquePl
      .map((t, i) => {
        const newest = t.newest ? `<span class="badge ok">new</span>` : "";
        return `<article class="assemble-card">
          <div class="assemble-card-idx">${i + 1}</div>
          <div>
            <div class="recs-card-title">${escapeHtml(t.artist || "")}${t.artist && t.title ? " — " : ""}${escapeHtml(t.title || t.name || "")}</div>
            <div class="recs-card-meta">
              ${t.bpm != null ? `<span class="badge ok">${Number(t.bpm).toFixed(0)} BPM</span>` : ""}
              ${t.vibe ? `<span class="badge vibe">${escapeHtml(t.vibe)}</span>` : ""}
              ${t.lane ? `<span class="badge genre">${escapeHtml(t.lane)}</span>` : ""}
              ${newest}
              <span class="badge timing">${assembleFitPercent(t)}% fit</span>
            </div>
            <p class="recs-card-reason">${escapeHtml(t.reason || "")}</p>
          </div>
        </article>`;
      })
      .join("");
  }
  if (rk) {
    const uniqueRanked = sortAssemblePlaylist(
      uniqueAssembleTracks(ranked),
      state.assemblePlaylistSort === "fit" ? "fit" : "crate"
    );
    rk.innerHTML = uniqueRanked
      .map((t) => {
        const verdict = t.verdict || "";
        return `<article class="assemble-card assemble-card-rank">
          <div>
            <div class="recs-card-title">${escapeHtml(t.artist || "")}${t.artist && t.title ? " — " : ""}${escapeHtml(t.title || t.name || "")}</div>
            <div class="recs-card-meta">
              <span class="badge ${verdict === "keep" ? "ok" : verdict === "skip" ? "warn" : "neutral"}">${escapeHtml(verdict || "—")}</span>
              <span class="badge timing">${assembleFitPercent(t)}%</span>
              ${t.vibe ? `<span class="badge vibe">${escapeHtml(t.vibe)}</span>` : ""}
            </div>
            <p class="recs-card-reason">${escapeHtml(t.reason || "")}</p>
          </div>
        </article>`;
      })
      .join("");
  }
  const files = result?.files;
  const fileEl = $("assembleFiles");
  if (fileEl) {
    if (files?.folder || files?.cues) {
      fileEl.hidden = false;
      const cueMsg = files.cues?.message ? ` · ${files.cues.message}` : "";
      fileEl.textContent = files.folder
        ? `Set folder · ${files.count || playlist.length} songs → ${files.folder}${cueMsg}`
        : `Wrote ${files.count || playlist.length} songs → ${files.cues}`;
    } else {
      fileEl.hidden = true;
    }
  }
}

function assembleJobBusy(job) {
  return Boolean(job?.id && (job.status === "running" || job.status === "queued"));
}

function unstickAssembleJob(job, message) {
  stopAssemblePoll();
  const next = job && typeof job === "object" ? { ...job } : { ...(state.assembleJob || {}) };
  if (assembleJobBusy(next)) next.status = "ok";
  next.message =
    message ||
    next.message ||
    "Scoring stopped. Click Assemble to continue — saved evals stay.";
  renderAssembleJob(next);
  setAssembleStatus(next.message, "");
}

function renderAssembleJob(job) {
  state.assembleJob = job;
  const prog = $("assembleProgress");
  const label = $("assembleProgressLabel");
  const count = $("assembleProgressCount");
  const fill = $("assembleBarFill");
  const stopBtn = $("assembleStopBtn");
  const startBtn = $("assembleStartBtn");
  if (prog) prog.hidden = !job;
  if (label) label.textContent = job?.message || "Idle";
  const total = job?.total || 0;
  const scored = job?.scored || 0;
  if (count) count.textContent = total ? `${scored} / ${total}` : "—";
  if (fill) {
    const pct = total ? Math.min(100, Math.round((scored / total) * 100)) : 0;
    fill.style.width = `${pct}%`;
  }
  const busy = assembleJobBusy(job);
  if (stopBtn) stopBtn.hidden = !busy;
  if (startBtn) {
    startBtn.disabled = busy;
    startBtn.textContent = busy ? "Scoring…" : "Assemble Pajamathon";
  }
  if (job?.result) {
    renderAssembleLists(job.result);
  } else if (state.assemblePreview?.result) {
    renderAssembleLists(state.assemblePreview.result);
  }
  if (job?.status === "error") setAssembleStatus(job.error || job.message, "error");
  else if (job?.status === "ok") setAssembleStatus(job.message, "ok");
  else if (job?.status === "running") setAssembleStatus(job.message, "running");
  else setAssembleStatus(job?.message || "", "");
  if (isAssembleMode()) {
    renderTrackList();
    updatePipelineStrip();
  }
}

function stopAssemblePoll() {
  state.assemblePollSeq += 1;
  if (state.assemblePollTimer) {
    clearInterval(state.assemblePollTimer);
    state.assemblePollTimer = null;
  }
}

async function recoverAssembleJob(seq) {
  const latest = await api("/api/assemble/latest", { timeoutMs: 4000 }).catch(() => null);
  if (seq !== state.assemblePollSeq) return true;
  const job = latest?.job;
  if (assembleJobBusy(job)) {
    if (!job.result && (state.assembleJob?.result || state.assemblePreview?.result)) {
      job.result = state.assembleJob?.result || state.assemblePreview?.result;
    }
    renderAssembleJob(job);
    startAssemblePoll(job.id);
    return true;
  }
  return false;
}

function startAssemblePoll(jobId) {
  stopAssemblePoll();
  if (!jobId) return;
  const seq = state.assemblePollSeq;
  state.assemblePollTimer = setInterval(async () => {
    if (seq !== state.assemblePollSeq) return;
    try {
      const data = await api(`/api/assemble/status/${jobId}`, { timeoutMs: 8000 });
      if (seq !== state.assemblePollSeq) return;
      renderAssembleJob(data.job);
      if (data.job && !assembleJobBusy(data.job)) {
        if (seq === state.assemblePollSeq) stopAssemblePoll();
      }
    } catch (err) {
      if (seq !== state.assemblePollSeq) return;
      const msg = err.message || String(err);
      if (/404|Unknown assemble job/i.test(msg)) {
        const recovered = await recoverAssembleJob(seq);
        if (seq !== state.assemblePollSeq) return;
        if (recovered) return;
        unstickAssembleJob(
          state.assembleJob,
          "That assemble run ended — lists still show saved evals. Click Assemble to continue."
        );
        return;
      }
      if (seq !== state.assemblePollSeq) return;
      setAssembleStatus(msg, "error");
    }
  }, 1200);
}

async function loadAssemblePreview() {
  try {
    const data = await api("/api/assemble/preview?library=Zouk", { timeoutMs: 60000 });
    state.assemblePreview = data;
    applySavedMixPrefs(data.mix_prefs);
    const brief = $("assembleBrief");
    if (brief && !brief.value.trim() && data.event?.brief) brief.value = data.event.brief;
    const name = $("assembleEventName");
    if (name && !name.value.trim() && data.event?.name) name.value = data.event.name;
    renderTrackList();
    const latest = await api("/api/assemble/latest", { timeoutMs: 4000 }).catch(() => null);
    const liveJob = assembleJobBusy(state.assembleJob);
    if (latest?.job) {
      if (!latest.job.result && data.result) latest.job.result = data.result;
      const keepNewer =
        liveJob &&
        state.assembleJob?.id &&
        latest.job.id !== state.assembleJob.id &&
        (latest.job.created_at || 0) < (state.assembleJob.created_at || 0);
      if (!keepNewer) {
        renderAssembleJob(latest.job);
        if (assembleJobBusy(latest.job)) startAssemblePoll(latest.job.id);
      }
    } else if (liveJob) {
      unstickAssembleJob(
        state.assembleJob,
        "Scoring stopped. Click Assemble to continue — saved evals stay."
      );
    }
    $("countsBadge").textContent =
      data.cached_evals != null
        ? `${data.cached_evals} cached · ${data.unique_songs || data.total || 0} songs`
        : `${data.total || 0} Zouk`;
    if (
      data.result &&
      !(latest?.job && latest.job.result) &&
      !assembleJobBusy(state.assembleJob)
    ) {
      renderAssembleLists(data.result);
      state.assembleJob = {
        status: "ok",
        message: `${data.result.scored_total} saved evals loaded`,
        result: data.result,
        event_name: data.result.event_name,
      };
    }
    const jobBusy = assembleJobBusy(latest?.job);
    if (data.cached_evals && !jobBusy) {
      setAssembleStatus(
        `${data.cached_evals} saved evals in the lists — next run skips those LLM calls`,
        "ok"
      );
    }
  } catch (err) {
    setAssembleStatus(err.message || String(err), "error");
  }
}

async function startAssemble() {
  try {
    const eventName = $("assembleEventName")?.value?.trim() || "Pajamathon";
    const brief = $("assembleBrief")?.value?.trim() || "";
    const target = Number($("assembleTarget")?.value || 400);
    const chunk = Number($("assembleChunk")?.value || 16);
    const scanAll = Boolean($("assembleScanAll")?.checked);
    setAssembleStatus("Starting Zouk scan…", "running");
    const previousResult = state.assembleJob?.result || state.assemblePreview?.result;
    const laneShares = readAssembleMixShares();
    persistAssembleShares(laneShares);
    const data = await api("/api/assemble/start", {
      method: "POST",
      body: JSON.stringify({
        event_name: eventName,
        brief,
        library: "Zouk",
        chunk_size: chunk,
        target,
        use_gemini: true,
        scan_all: scanAll,
        lane_shares: sharesToPercents(laneShares),
        min_fit: readAssembleMinFit(),
      }),
      timeoutMs: 15000,
    });
    if (data.job && !data.job.result && previousResult) {
      data.job.result = previousResult;
    }
    renderAssembleJob(data.job);
    if (data.job?.id) startAssemblePoll(data.job.id);
  } catch (err) {
    setAssembleStatus(err.message || String(err), "error");
  }
}

async function stopAssemble() {
  const id = state.assembleJob?.id;
  if (!id) return;
  try {
    const data = await api(`/api/assemble/stop/${id}`, { method: "POST", timeoutMs: 8000 });
    renderAssembleJob(data.job);
  } catch (err) {
    setAssembleStatus(err.message || String(err), "error");
  }
}

async function exportAssembleFolder() {
  setAssembleStatus("Writing Sets/Pajamathon 2026…", "running");
  try {
    const data = await api("/api/assemble/export", { method: "POST", timeoutMs: 120000 });
    if (state.assembleJob?.result) {
      state.assembleJob.result.files = data.files;
      renderAssembleJob(state.assembleJob);
    }
    setAssembleStatus(
      `Set folder ready · ${data.files?.count || 0} songs → ${data.files?.folder || ""}`,
      "ok"
    );
  } catch (err) {
    setAssembleStatus(err.message || String(err), "error");
  }
}

async function applyAssembleMix() {
  const shares = readAssembleMixShares();
  const minFit = readAssembleMinFit();
  persistAssembleShares(shares);
  persistAssembleMinFit(minFit);
  writeAssembleMixTuners(shares);
  writeAssembleMinFit(minFit);
  const eventName = $("assembleEventName")?.value?.trim() || "Pajamathon";
  const target = Number($("assembleTarget")?.value || 400);
  try {
    const data = await api("/api/assemble/rebalance", {
      method: "POST",
      body: JSON.stringify({
        event_name: eventName,
        target,
        lane_shares: sharesToPercents(shares),
        min_fit: minFit,
      }),
      timeoutMs: 20000,
    });
    if (data.result) {
      if (state.assembleJob) {
        state.assembleJob.result = data.result;
        if (data.job?.lane_shares) state.assembleJob.lane_shares = data.job.lane_shares;
        if (data.job?.min_fit != null) state.assembleJob.min_fit = data.job.min_fit;
      }
      renderAssembleLists(data.result);
    }
    const pct = Math.round(minFit * 100);
    setAssembleStatus(`Playlist rebuilt · min ${pct}% fit`, "ok");
  } catch (err) {
    const msg = err.message || String(err);
    if (/No assembled playlist/i.test(msg)) return;
    setAssembleStatus(msg, "error");
  }
}

function scheduleAssembleMixApply() {
  if (state.assembleMixTimer) clearTimeout(state.assembleMixTimer);
  state.assembleMixTimer = setTimeout(() => {
    state.assembleMixTimer = null;
    applyAssembleMix();
  }, 450);
}

function onAssembleChromeClick(e) {
  const t = e.target;
  if (!t || typeof t.closest !== "function") return;
  if (t.closest("#assembleStartBtn")) {
    e.preventDefault();
    startAssemble();
    return;
  }
  if (t.closest("#assembleStopBtn")) {
    e.preventDefault();
    stopAssemble();
    return;
  }
  if (t.closest("#assembleExportBtn")) {
    e.preventDefault();
    exportAssembleFolder();
    return;
  }
  if (t.closest("#assembleMixApply")) {
    e.preventDefault();
    if (state.assembleMixTimer) {
      clearTimeout(state.assembleMixTimer);
      state.assembleMixTimer = null;
    }
    applyAssembleMix();
    return;
  }
  if (t.closest("#assembleMixReset")) {
    e.preventDefault();
    persistAssembleShares(defaultAssembleShares());
    persistAssembleMinFit(ASSEMBLE_DEFAULT_MIN_FIT);
    writeAssembleMixTuners(state.assembleLaneShares);
    writeAssembleMinFit(state.assembleMinFit);
    applyAssembleMix();
    return;
  }
  const sortBtn = t.closest("#assemblePlaylistSort button");
  if (sortBtn) {
    e.preventDefault();
    const mode = sortBtn.getAttribute("data-pl-sort") || "crate";
    state.assemblePlaylistSort = mode;
    try {
      localStorage.setItem("assemblePlaylistSort", mode);
    } catch {
      /* ignore */
    }
    syncAssemblePlaylistSortUi();
    const result = state.assembleJob?.result || state.assemblePreview?.result;
    if (result) renderAssembleLists(result);
  }
}

function bindAssembleUi() {
  if (!document.body.dataset.assembleChromeBound) {
    document.body.dataset.assembleChromeBound = "1";
    document.body.addEventListener("click", onAssembleChromeClick);
  }
  if (!state.assemblePlaylistSort) {
    try {
      state.assemblePlaylistSort = localStorage.getItem("assemblePlaylistSort") || "crate";
    } catch {
      state.assemblePlaylistSort = "crate";
    }
  }
  renderAssembleMixTuners();
  if (state.assembleMinFit == null) state.assembleMinFit = loadAssembleMinFit();
  writeAssembleMinFit(state.assembleMinFit);
  const minFit = $("assembleMinFit");
  const minFitNum = $("assembleMinFitNum");
  const bindMinFit = (el) => {
    if (!el || el.dataset.bound) return;
    el.dataset.bound = "1";
    el.addEventListener("input", () => {
      writeAssembleMinFit(el.value);
      persistAssembleMinFit(el.value);
      persistAssembleShares(readAssembleMixShares());
      scheduleAssembleMixApply();
    });
  };
  bindMinFit(minFit);
  bindMinFit(minFitNum);
  const lanes = $("assembleMixLanes");
  if (lanes && !lanes.dataset.bound) {
    lanes.dataset.bound = "1";
    const onMixEdit = (e) => {
      const el = e.target;
      if (!(el instanceof HTMLInputElement) || !el.classList.contains("assemble-mix-pct")) return;
      persistAssembleShares(readAssembleMixShares());
      persistAssembleMinFit(readAssembleMinFit());
      syncAssembleMixSum();
      if (e.type === "change") scheduleAssembleMixApply();
    };
    lanes.addEventListener("input", onMixEdit);
    lanes.addEventListener("change", onMixEdit);
  }
}

try {
  bindAssembleUi();
} catch {
  document.body.addEventListener("click", onAssembleChromeClick);
}
