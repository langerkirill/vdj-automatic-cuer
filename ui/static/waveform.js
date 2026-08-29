/**
 * Pure waveform window / playhead / off-screen cue helpers.
 * Classic script in the browser; CommonJS for Node tests.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module && module.exports) {
    module.exports = api;
  } else {
    /** @type {Record<string, unknown>} */ (root).MusicSorterWaveform = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const WAVE_PAD_X = 8;
  const WAVE_ZOOM_MIN = 1;
  const WAVE_ZOOM_MAX = 48;

  /**
   * @param {number} zoom
   */
  function clampWaveZoom(zoom) {
    return Math.min(WAVE_ZOOM_MAX, Math.max(WAVE_ZOOM_MIN, zoom));
  }

  /**
   * Visible time window over the full track duration (no playhead follow).
   * @param {number} duration
   * @param {number} zoom
   * @param {number} offset
   */
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

  /**
   * Page the zoom window so a moving playhead stays on-screen.
   * Paused / drag leave the user's view alone.
   * @param {number} duration
   * @param {number} timeSec
   * @param {{ zoom?: number, offset?: number, playing?: boolean, allowFollow?: boolean, lead?: number }} [opts]
   */
  function keepPlayheadInView(
    duration,
    timeSec,
    { zoom, offset, playing, allowFollow, lead } = {}
  ) {
    const view = visibleWaveWindow(duration, zoom || 1, offset || 0);
    if (!duration || !Number.isFinite(timeSec)) return view;
    if (!playing || allowFollow === false) return view;
    if (timeSec >= view.start && timeSec <= view.end) return view;
    const frac = lead != null && Number.isFinite(lead) ? lead : 0.08;
    return visibleWaveWindow(duration, view.zoom, timeSec - view.span * frac);
  }

  /**
   * Apply follow using an explicit playing flag so tests do not need DOM audio.
   * Mutates waveZoom / waveOffset / waveViewPinned on appState.
   * @param {{ waveZoom: number, waveOffset: number, waveViewPinned: boolean, gridAlignDragging?: boolean, loopDrag?: unknown, gridAlignMode?: boolean }} appState
   * @param {number} duration
   * @param {number} timeSec
   * @param {boolean} playing
   */
  function applyPlayheadFollow(appState, duration, timeSec, playing) {
    const view = visibleWaveWindow(duration, appState.waveZoom, appState.waveOffset);
    if (
      appState.waveViewPinned &&
      Number.isFinite(timeSec) &&
      view.span > 0 &&
      timeSec >= view.start &&
      timeSec <= view.end
    ) {
      // Needle is back on-screen — resume paging.
      appState.waveViewPinned = false;
    }
    const allowFollow =
      !appState.gridAlignDragging &&
      !appState.loopDrag &&
      !appState.gridAlignMode &&
      !appState.waveViewPinned;
    const next = keepPlayheadInView(duration, timeSec, {
      zoom: appState.waveZoom,
      offset: appState.waveOffset,
      playing,
      allowFollow,
    });
    appState.waveZoom = next.zoom;
    appState.waveOffset = next.start;
    return next;
  }

  /**
   * @param {number} timeSec
   * @param {number} padX
   * @param {number} plotW
   * @param {{ start: number, span: number }} view
   */
  function timeToWaveX(timeSec, padX, plotW, view) {
    if (!view.span) return padX;
    return padX + ((timeSec - view.start) / view.span) * plotW;
  }

  /**
   * @param {import('./types').CuePoint | { kind?: string, type?: string } | null | undefined} point
   */
  function pointKind(point) {
    const raw = String((point && (point.kind || point.type)) || "").toLowerCase();
    return raw === "loop" ? "loop" : "cue";
  }

  /**
   * @type {import('./types').ClassifyWaveMarkers}
   */
  function classifyWaveMarkers(points, view, slack) {
    const gap = slack == null ? 0.05 : slack;
    const inView = [];
    const offLeft = [];
    const offRight = [];
    for (const p of points || []) {
      const pos = Number(p.pos) || 0;
      if (pos < view.start - gap) offLeft.push(p);
      else if (pos > view.end + gap) offRight.push(p);
      else inView.push(p);
    }
    return { inView, offLeft, offRight };
  }

  /**
   * @type {import('./types').FormatOffscreenCueLabel}
   */
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

  /**
   * Pure pan: returns the next window without touching DOM.
   * @param {number} duration
   * @param {number} zoom
   * @param {number} timeSec
   * @param {number} [frac]
   */
  function panWaveToTime(duration, zoom, timeSec, frac) {
    const view = visibleWaveWindow(duration, zoom, 0);
    const t = Number(timeSec);
    if (!duration || !Number.isFinite(t)) return view;
    const lead = frac == null ? 0.22 : frac;
    return visibleWaveWindow(duration, view.zoom, t - view.span * lead);
  }

  /**
   * @param {number} zoom
   * @param {number} offset
   */
  function snapshotWaveView(zoom, offset) {
    return { zoom: zoom, offset: offset };
  }

  /**
   * @param {{ zoom?: number, offset?: number } | null | undefined} prev
   */
  function restoreWaveView(prev) {
    if (!prev) return null;
    return {
      zoom: clampWaveZoom(prev.zoom || 1),
      offset: Number(prev.offset) || 0,
    };
  }

  /**
   * X to paint. Moving needle is never dropped; paused off-screen may hide.
   * @param {number} timeSec
   * @param {number} padX
   * @param {number} plotW
   * @param {{ start: number, end: number, span: number }} view
   * @param {boolean} playing
   * @returns {number | null}
   */
  function playheadDrawX(timeSec, padX, plotW, view, playing) {
    const inView = view.span > 0 && timeSec >= view.start && timeSec <= view.end;
    if (!inView && !playing) return null;
    const x = timeToWaveX(timeSec, padX, plotW, view);
    return Math.max(padX, Math.min(padX + plotW, x));
  }

  /**
   * @param {{ x0: number, x1: number, y0: number, y1: number } | null | undefined} rect
   * @param {number} x
   * @param {number} y
   */
  function hitTestRect(rect, x, y) {
    if (!rect) return false;
    return x >= rect.x0 && x <= rect.x1 && y >= rect.y0 && y <= rect.y1;
  }

  return {
    WAVE_PAD_X,
    WAVE_ZOOM_MIN,
    WAVE_ZOOM_MAX,
    clampWaveZoom,
    visibleWaveWindow,
    keepPlayheadInView,
    applyPlayheadFollow,
    timeToWaveX,
    pointKind,
    classifyWaveMarkers,
    formatOffscreenCueLabel,
    panWaveToTime,
    snapshotWaveView,
    restoreWaveView,
    playheadDrawX,
    hitTestRect,
  };
});
