/**
 * Pure status handoff composers for promote / sort success.
 * Used by app.js after loadTracks so success CTAs are not wiped by list refresh.
 * Also required by Node unit tests (CommonJS) — keep free of DOM / fetch.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.MusicSorterStatusHandoff = factory();
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  /**
   * @param {{ database_updated?: boolean, stems_moved?: boolean } | null | undefined} result
   * @param {string} destinationStage
   * @returns {{ message: string, kind: string, action: { label: string, gotoMode: string } | null }}
   */
  function composePromoteSuccessHandoff(result, destinationStage) {
    const destLabel =
      destinationStage === "ready_for_sort"
        ? "Ready for Sort"
        : String(destinationStage || "").replaceAll("_", " ");
    const bits = [];
    if (result && result.database_updated) bits.push("VDJ cues retargeted");
    if (result && result.stems_moved) bits.push("stems moved");
    const message =
      `Moved → ${destLabel}` + (bits.length ? " · " + bits.join(" · ") : "");
    const action =
      destinationStage === "ready_for_sort"
        ? { label: "Open Sort", gotoMode: "sort" }
        : null;
    return { message, kind: "success", action };
  }

  /**
   * @param {{ database_updated?: boolean } | null | undefined} result
   * @param {number} remainingCount tracks still in Ready after refresh
   * @param {string[]} [archiveBits]
   * @returns {{ message: string, kind: string, action: { label: string, gotoMode: string } | null }}
   */
  function composeSortSuccessHandoff(result, remainingCount, archiveBits) {
    const remaining = Number(remainingCount);
    const left = Number.isFinite(remaining) ? Math.max(0, remaining) : 0;
    const bits = Array.isArray(archiveBits) ? archiveBits.slice() : [];
    if (result && result.database_updated) bits.push("cues kept");

    if (left <= 0) {
      return {
        message: "Ready queue is empty — great session.",
        kind: "success",
        action: { label: "Back to Add Cues", gotoMode: "add_cues" },
      };
    }

    const suffix = bits.length ? " · " + bits.join(" · ") : "";
    return {
      message: `Sorted · ${left} left in Ready${suffix}`,
      kind: "success",
      // Keep working the queue — no mode switch required when items remain.
      action: null,
    };
  }

  /**
   * Simulate loadTracks default status vs success handoff ordering.
   * Proves that applying handoff *after* the load status wins.
   *
   * @param {{ message: string, kind?: string, action?: object | null }} loadStatus
   * @param {{ message: string, kind?: string, action?: object | null }} handoff
   * @param {{ skipStatus?: boolean }} [loadOpts]
   * @returns {{ message: string, kind: string, action: object | null }}
   */
  function applyStatusAfterLoad(loadStatus, handoff, loadOpts) {
    const skip = Boolean(loadOpts && loadOpts.skipStatus);
    let current = skip
      ? { message: "", kind: "", action: null }
      : {
          message: loadStatus.message,
          kind: loadStatus.kind || "",
          action: loadStatus.action || null,
        };
    // Success handoff always applied after load when provided (skipStatus path).
    if (handoff && handoff.message) {
      current = {
        message: handoff.message,
        kind: handoff.kind || "success",
        action: handoff.action || null,
      };
    }
    return current;
  }

  return {
    composePromoteSuccessHandoff,
    composeSortSuccessHandoff,
    applyStatusAfterLoad,
  };
});
