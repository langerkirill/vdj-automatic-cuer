/**
 * Playback / quiet-session / clock helpers.
 * Classic script in the browser; CommonJS for Node tests.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module && module.exports) {
    module.exports = api;
  } else {
    /** @type {Record<string, unknown>} */ (root).MusicSorterTransport = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  /**
   * @param {{ location?: { search?: string }, navigator?: { webdriver?: boolean } } | null | undefined} env
   */
  function wantsQuietSession(env) {
    const win = env || (typeof window !== "undefined" ? window : null);
    try {
      const search = win && win.location ? String(win.location.search || "") : "";
      const params = new URLSearchParams(search);
      if (params.has("quiet") || params.has("mute")) {
        const flag = params.get("quiet") || params.get("mute");
        if (!flag || flag === "1" || flag === "true" || flag === "yes") return true;
      }
    } catch {
      /* ignore */
    }
    try {
      const nav = (env && env.navigator) || (typeof navigator !== "undefined" ? navigator : null);
      if (nav && nav.webdriver) return true;
    } catch {
      /* ignore */
    }
    return false;
  }

  /**
   * @param {{ quietSession?: boolean, allowAutoplay?: boolean } | null | undefined} appState
   * @param {boolean} practiceMode
   */
  function shouldAutoplayOnSelect(appState, practiceMode) {
    if (appState && appState.quietSession) return false;
    if (practiceMode) return false;
    return Boolean(appState && appState.allowAutoplay);
  }

  /**
   * @param {number} sec
   */
  function formatClock(sec) {
    const s = Math.max(0, Number(sec) || 0);
    const m = Math.floor(s / 60);
    const r = Math.floor(s % 60);
    return `${m}:${String(r).padStart(2, "0")}`;
  }

  /**
   * Cue/list clock. Milliseconds so Cue 1 at 0.030522s is 0:00.031, not 0:00.0.
   * @param {number} seconds
   */
  function fmtTime(seconds) {
    // Milliseconds — 0.1s rounding painted Cue 1 at 0:00.0 while the 1 sat at 0.031s.
    const totalMs = Math.round(Math.max(0, Number(seconds) || 0) * 1000);
    const m = Math.floor(totalMs / 60000);
    const remMs = totalMs % 60000;
    const whole = Math.floor(remMs / 1000);
    const frac = remMs % 1000;
    return `${m}:${String(whole).padStart(2, "0")}.${String(frac).padStart(3, "0")}`;
  }

  /**
   * @param {number} seconds
   */
  function fmtTransportTime(seconds) {
    const s = Math.max(0, Math.floor(Number(seconds) || 0));
    const m = Math.floor(s / 60);
    return `${m}:${String(s % 60).padStart(2, "0")}`;
  }

  /**
   * @param {number} n
   */
  function fmtBytes(n) {
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
    return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  }

  /**
   * @param {import('./types').CuePoint | null | undefined} point
   */
  function cueKey(point) {
    return `${point && point.kind}:${point && point.num}:${point && point.pos}`;
  }

  /**
   * @param {import('./types').Track | null | undefined} track
   * @param {{ duration?: number } | null | undefined} audio
   */
  function trackDuration(track, audio) {
    const fromDb = Number(track && track.cues && track.cues.song_length) || 0;
    const fromAudio = Number(audio && audio.duration);
    if (fromAudio && Number.isFinite(fromAudio) && fromAudio > 0) return fromAudio;
    if (fromDb > 0) return fromDb;
    return 0;
  }

  /**
   * @param {import('./types').CuePoint | null | undefined} point
   * @param {number} [bpm]
   */
  function loopDurationSeconds(point, bpm) {
    const beats = Number(point && point.size);
    if (!Number.isFinite(beats) || beats <= 0) return 0;
    if (bpm && bpm > 0) return (beats / bpm) * 60;
    return (beats / 120) * 60;
  }

  /**
   * @type {import('./types').TrackDisplayTitle}
   */
  function trackDisplayTitle(track) {
    return (
      String((track && track.cues && track.cues.title) || "").trim() ||
      String((track && track.name) || "Untitled track")
        .replace(/\.[^.]+$/, "")
        .replace(/^\d+[\s._-]+/, "")
    );
  }

  /**
   * @param {import('./types').Track | null | undefined} track
   */
  function trackDisplayArtist(track) {
    return String((track && track.cues && track.cues.author) || "").trim();
  }

  const FALLBACK_BEAT_SEC = 2;
  const PREROLL_BEATS = 4;
  const BAR_BEATS = 4;

  /**
   * Lead-in before a cue/loop so the reviewer hears the approach.
   * 4 beats (1 bar in 4/4) when BPM is known, else ~2s.
   * @param {number | null | undefined} bpm
   */
  function cuePrerollSeconds(bpm) {
    const n = Number(bpm);
    if (n && Number.isFinite(n) && n > 0) return (PREROLL_BEATS / n) * 60;
    return FALLBACK_BEAT_SEC;
  }

  /**
   * Seek time for cue review: approach the marker, never before 0.
   * @param {number} cuePos
   * @param {number | null | undefined} bpm
   */
  function cuePrerollTime(cuePos, bpm) {
    const pos = Number(cuePos);
    const t = Number.isFinite(pos) ? pos : 0;
    return Math.max(0, t - cuePrerollSeconds(bpm));
  }

  /**
   * @param {number | null | undefined} bpm
   * @param {boolean} [bar]
   */
  function beatSeekStep(bpm, bar) {
    const n = Number(bpm);
    if (n && Number.isFinite(n) && n > 0) {
      const beat = 60 / n;
      return bar ? beat * BAR_BEATS : beat;
    }
    return bar ? FALLBACK_BEAT_SEC * BAR_BEATS : FALLBACK_BEAT_SEC;
  }

  /**
   * @param {number} current
   * @param {number | null | undefined} bpm
   * @param {{ direction?: number, bar?: boolean, shift?: boolean, duration?: number } | null | undefined} opts
   */
  function beatSeekTime(current, bpm, opts) {
    const options = opts || {};
    const dir = Number(options.direction) < 0 ? -1 : 1;
    const bar = Boolean(options.bar || options.shift);
    const cur = Number(current);
    const from = Number.isFinite(cur) ? cur : 0;
    const next = from + dir * beatSeekStep(bpm, bar);
    if (next < 0) return 0;
    const duration = Number(options.duration);
    if (Number.isFinite(duration) && duration > 0 && next > duration) return duration;
    return next;
  }

  const CAMELOT_MAJOR = {
    b: 1,
    "f#": 2,
    gb: 2,
    db: 3,
    "c#": 3,
    ab: 4,
    "g#": 4,
    eb: 5,
    "d#": 5,
    bb: 6,
    "a#": 6,
    f: 7,
    c: 8,
    g: 9,
    d: 10,
    a: 11,
    e: 12,
  };

  const CAMELOT_MINOR = {
    "g#m": 1,
    abm: 1,
    ebm: 2,
    "d#m": 2,
    bbm: 3,
    "a#m": 3,
    fm: 4,
    cm: 5,
    gm: 6,
    dm: 7,
    am: 8,
    em: 9,
    bm: 10,
    "f#m": 11,
    gbm: 11,
    "c#m": 12,
    dbm: 12,
  };

  /**
   * @param {string | null | undefined} raw
   * @returns {string}
   */
  function keyToCamelot(raw) {
    if (!raw) return "";
    let s = String(raw).trim().replace(/\s+/g, "");
    if (!s) return "";
    s = s.replace(/maj/gi, "").replace(/min/gi, "m");
    const low = s.toLowerCase().replace(/♯/g, "#").replace(/♭/g, "b");
    const already = low.match(/^(\d{1,2})\s*([ab])$/);
    if (already) {
      const n = Number(already[1]);
      if (n >= 1 && n <= 12) return `${n}${already[2].toUpperCase()}`;
    }
    if (Object.prototype.hasOwnProperty.call(CAMELOT_MINOR, low)) {
      return `${/** @type {Record<string, number>} */ (CAMELOT_MINOR)[low]}A`;
    }
    if (!low.endsWith("m") && Object.prototype.hasOwnProperty.call(CAMELOT_MAJOR, low)) {
      return `${/** @type {Record<string, number>} */ (CAMELOT_MAJOR)[low]}B`;
    }
    return "";
  }

  /**
   * Scannable BPM + key/Camelot chips for queue rows.
   * Missing values are an em dash — never a guessed key.
   * @param {import('./types').Track | null | undefined} track
   */
  function crateBpmKeyLabels(track) {
    const cues = (track && track.cues) || {};
    const bpmRaw = Number(
      track && track.bpm != null ? track.bpm : cues.bpm
    );
    const bpm =
      Number.isFinite(bpmRaw) && bpmRaw > 0 ? `${Math.round(bpmRaw)} BPM` : "—";
    const keyRaw = String((track && track.key) || cues.key || "").trim();
    const camelotRaw = String((track && track.camelot) || cues.camelot || "").trim();
    const camelot = camelotRaw || keyToCamelot(keyRaw);
    let key = "—";
    if (keyRaw && camelot && camelot.toUpperCase() !== keyRaw.toUpperCase()) {
      key = `${keyRaw} · ${camelot}`;
    } else if (keyRaw) {
      key = keyRaw;
    } else if (camelot) {
      key = camelot;
    }
    return { bpm, key };
  }

  const CUE_COLOR_MEANINGS = {
    blue: "Melodic — no drums or vocals",
    green: "Melodic + drums — no vocals",
    purple: "Drums / percussion only",
    yellow: "Drums + vocals",
    orange: "Vocals with no drums",
  };

  /**
   * Musical meaning for this app's cue color language.
   * @param {string | null | undefined} colorName
   */
  function cueColorMeaning(colorName) {
    const id = String(colorName || "").toLowerCase().trim();
    return /** @type {Record<string, string>} */ (CUE_COLOR_MEANINGS)[id] || "Unlabeled color";
  }

  return {
    wantsQuietSession,
    shouldAutoplayOnSelect,
    formatClock,
    fmtTime,
    fmtTransportTime,
    fmtBytes,
    cueKey,
    trackDuration,
    loopDurationSeconds,
    trackDisplayTitle,
    trackDisplayArtist,
    cuePrerollSeconds,
    cuePrerollTime,
    beatSeekStep,
    beatSeekTime,
    keyToCamelot,
    crateBpmKeyLabels,
    cueColorMeaning,
  };
});
