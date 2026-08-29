/**
 * Pure practice-map layout helpers.
 * Classic script in the browser; CommonJS for Node tests.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module && module.exports) {
    module.exports = api;
  } else {
    /** @type {Record<string, unknown>} */ (root).MusicSorterPractice = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  /**
   * @param {{ practiceDetail?: { transitions?: unknown[] } | null } | null | undefined} appState
   */
  function practiceTransitions(appState) {
    const detail = appState && appState.practiceDetail;
    return (detail && detail.transitions) || [];
  }

  /**
   * @param {import('./types').Track | { duration?: number } | null | undefined} track
   * @param {{ duration?: number } | null | undefined} audio
   * @param {{ practiceDetail?: { duration_sec?: number } | null, waveform?: { duration?: number } | null } | null | undefined} appState
   * @param {(track: unknown, audio: unknown) => number} [fallbackDuration]
   */
  function practiceDuration(track, audio, appState, fallbackDuration) {
    const fromDetail = Number(appState && appState.practiceDetail && appState.practiceDetail.duration_sec) || 0;
    if (fromDetail > 0) return fromDetail;
    const fromMix = Number(track && track.duration) || 0;
    if (fromMix > 0) return fromMix;
    const fromWave = Number(appState && appState.waveform && appState.waveform.duration) || 0;
    if (fromWave > 0) return fromWave;
    const fromAudio = Number(audio && audio.duration);
    if (Number.isFinite(fromAudio) && fromAudio > 0) return fromAudio;
    if (typeof fallbackDuration === "function") return fallbackDuration(track, audio) || 0;
    return 0;
  }

  /**
   * @param {number} n
   * @param {number} lo
   * @param {number} hi
   */
  function practiceClamp(n, lo, hi) {
    return Math.max(lo, Math.min(hi, n));
  }

  /**
   * @param {number} duration
   * @param {{ tracks?: Array<{ pos_sec?: number, name?: string }> } | null | undefined} detail
   */
  function practiceSongSlots(duration, detail) {
    const raw = (detail && detail.tracks) || [];
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

  /**
   * @param {{ clientWidth?: number, parentElement?: { clientWidth?: number } } | null | undefined} wrap
   * @param {number} duration
   * @param {{ tracks?: Array<{ pos_sec?: number, name?: string }> } | null | undefined} detail
   */
  function practiceMapLayout(wrap, duration, detail) {
    const viewportW = Math.max(
      1,
      (wrap && wrap.clientWidth) ||
        (wrap && wrap.parentElement && wrap.parentElement.clientWidth) ||
        600
    );
    const slots = practiceSongSlots(duration, detail);
    const trackCount = (slots && slots.length) || 0;
    let contentWidth = viewportW;
    let pxPerSong = 0;
    if (trackCount > 0) {
      pxPerSong = practiceClamp(viewportW / trackCount, 120, 220);
      contentWidth = Math.max(viewportW, Math.round(trackCount * pxPerSong));
    }
    return { viewportW, contentWidth, slots, padX: 10, pxPerSong, trackCount };
  }

  /**
   * @param {number} t
   * @param {Array<{ t0: number, t1: number }> | null | undefined} slots
   * @param {number} contentW
   * @param {number} duration
   * @param {number} [padX]
   */
  function practiceTimeToX(t, slots, contentW, duration, padX) {
    const pad = padX == null ? 10 : padX;
    const plotW = Math.max(1, contentW - pad * 2);
    const time = Number(t) || 0;
    if (!slots || !slots.length || !(duration > 0)) {
      return pad + practiceClamp(time / Math.max(duration, 1e-6), 0, 1) * plotW;
    }
    const n = slots.length;
    const slotW = plotW / n;
    if (time <= slots[0].t0) return pad;
    for (let i = 0; i < n; i++) {
      const s = slots[i];
      const span = Math.max(1e-6, s.t1 - s.t0);
      if (time < s.t1 || i === n - 1) {
        const frac = practiceClamp((time - s.t0) / span, 0, 1);
        return pad + i * slotW + frac * slotW;
      }
    }
    return pad + plotW;
  }

  /**
   * @param {number} x
   * @param {Array<{ t0: number, t1: number }> | null | undefined} slots
   * @param {number} contentW
   * @param {number} duration
   * @param {number} [padX]
   */
  function practiceXToTime(x, slots, contentW, duration, padX) {
    const pad = padX == null ? 10 : padX;
    const plotW = Math.max(1, contentW - pad * 2);
    const local = practiceClamp(x - pad, 0, plotW);
    if (!slots || !slots.length || !(duration > 0)) {
      return (local / plotW) * duration;
    }
    const n = slots.length;
    const slotW = plotW / n;
    const i = Math.min(n - 1, Math.max(0, Math.floor(local / Math.max(slotW, 1e-6))));
    const frac = practiceClamp((local - i * slotW) / Math.max(slotW, 1e-6), 0, 1);
    const s = slots[i];
    return s.t0 + frac * (s.t1 - s.t0);
  }

  return {
    practiceTransitions,
    practiceDuration,
    practiceClamp,
    practiceSongSlots,
    practiceMapLayout,
    practiceTimeToX,
    practiceXToTime,
  };
});
