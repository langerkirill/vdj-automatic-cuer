/**
 * Pure Assemble playlist / mix-share helpers.
 * Classic script in the browser; CommonJS for Node tests.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module && module.exports) {
    module.exports = api;
  } else {
    /** @type {Record<string, unknown>} */ (root).MusicSorterAssemble = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

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

  /**
   * @param {import('./types').Track | { artist?: string, title?: string, name?: string } | null | undefined} t
   */
  function assembleSongKey(t) {
    const artist = String((t && t.artist) || "")
      .toLowerCase()
      .split(/[,&+/]| feat\.? | ft\.? | featuring | and /i)[0]
      .replace(/[^a-z0-9]+/g, " ")
      .trim();
    let title = String((t && (t.title || t.name)) || "").toLowerCase();
    title = title.replace(/^\d+[\s.\-]+/, "");
    title = title.replace(/\([^)]*\)/g, " ");
    title = title.replace(
      /\b(original mix|extended mix|radio edit|club mix|remix|mix|edit|version)\b/g,
      " "
    );
    title = title.replace(/[^a-z0-9]+/g, " ").replace(/\s+/g, " ").trim();
    return `${artist}|${title}`;
  }

  /**
   * @param {Array<{ artist?: string, title?: string, name?: string }> | null | undefined} tracks
   */
  function uniqueAssembleTracks(tracks) {
    const seen = new Set();
    return (tracks || []).filter((t) => {
      const key = assembleSongKey(t);
      if (!key || seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }

  /**
   * @param {import('./types').Track | { fit?: number } | null | undefined} track
   */
  function assembleFitPercent(track) {
    return Math.round(Number((track && track.fit) || 0) * 100);
  }

  /**
   * @type {import('./types').SortAssemblePlaylist}
   */
  function sortAssemblePlaylist(tracks, mode) {
    const rows = Array.isArray(tracks) ? tracks.slice() : [];
    if ((mode || "crate") !== "fit") return rows;
    return rows.sort((a, b) => {
      const fitDelta = assembleFitPercent(b) - assembleFitPercent(a);
      if (fitDelta) return fitDelta;
      const titleA = `${(a && a.artist) || ""} ${(a && (a.title || a.name)) || ""}`.toLowerCase();
      const titleB = `${(b && b.artist) || ""} ${(b && (b.title || b.name)) || ""}`.toLowerCase();
      return titleA.localeCompare(titleB);
    });
  }

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

  /**
   * @param {Record<string, number> | null | undefined} raw
   */
  function normalizeClientShares(raw) {
    /** @type {Record<string, number>} */
    const out = {};
    let any = false;
    ASSEMBLE_LANES.forEach(([lane]) => {
      let num = Number(raw && raw[lane]);
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

  /**
   * @param {Record<string, number> | null | undefined} shares
   */
  function sharesToPercents(shares) {
    const norm = /** @type {Record<string, number>} */ (normalizeClientShares(shares));
    /** @type {Record<string, number>} */
    const raw = {};
    ASSEMBLE_LANES.forEach(([lane]) => {
      raw[lane] = Math.round((norm[lane] || 0) * 100);
    });
    return raw;
  }

  /**
   * @param {unknown} raw
   */
  function normalizeClientMinFit(raw) {
    let num = Number(raw);
    if (!Number.isFinite(num)) return ASSEMBLE_DEFAULT_MIN_FIT;
    if (num > 1.5) num /= 100;
    return Math.max(0, Math.min(1, num));
  }

  /**
   * @param {{ id?: string, status?: string } | null | undefined} job
   */
  function assembleJobBusy(job) {
    return Boolean(job && job.id && (job.status === "running" || job.status === "queued"));
  }

  return {
    ASSEMBLE_LANES,
    ASSEMBLE_SHARE_STORE,
    ASSEMBLE_MIN_FIT_STORE,
    ASSEMBLE_DEFAULT_MIN_FIT,
    assembleSongKey,
    uniqueAssembleTracks,
    assembleFitPercent,
    sortAssemblePlaylist,
    defaultAssembleShares,
    normalizeClientShares,
    sharesToPercents,
    normalizeClientMinFit,
    assembleJobBusy,
  };
});
