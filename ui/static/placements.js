/**
 * Pure Already-in-library helpers.
 * Used by app.js for the Sort / Add Cues placement card.
 * Also required by Node unit tests (CommonJS) — keep free of DOM / fetch.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module && module.exports) {
    module.exports = api;
  } else {
    /** @type {Record<string, unknown>} */ (root).MusicSorterPlacements = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  /**
   * @param {import('./types').Placement | { event?: string, root_name?: string } | null | undefined} hit
   */
  function isPajamathonPlacement(hit) {
    return String((hit && (hit.event || hit.root_name)) || "")
      .toLowerCase()
      .startsWith("pajamathon");
  }

  /**
   * @param {import('./types').Track | null | undefined} track
   */
  function trackIsPajamathonSetFile(track) {
    const path = String((track && track.path) || "").replace(/\\/g, "/");
    if (/\/sets\/pajamathon/i.test(path)) return true;
    const group = String((track && (track.group || track.section)) || "");
    return /\/sets\//i.test(path) && /^pajamathon/i.test(group.trim());
  }

  /**
   * @param {import('./types').Track | null | undefined} track
   * @param {import('./types').Placement[] | null | undefined} sets
   */
  function withCurrentSetPlacement(track, sets) {
    const rows = Array.isArray(sets) ? sets.slice() : [];
    if (!trackIsPajamathonSetFile(track)) return rows;
    const src = String((track && track.path) || "");
    if (!src || rows.some((hit) => hit && hit.path === src)) return rows;
    const group = String((track && track.group) || "Pajamathon");
    const cues = (track && track.cues) || {};
    rows.unshift({
      path: src,
      relative_path: String((track && track.relative_path) || ""),
      root_name: group,
      event: group,
      is_current: true,
      is_cued: Boolean(track && track.is_cued),
      cue_count: cues.cue_count || 0,
      loop_count: cues.loop_count || 0,
      in_database: true,
    });
    return rows;
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

  /**
   * @param {import('./types').TrackPlacements | null | undefined} placements
   */
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

  /**
   * @param {import('./types').Track[] | null | undefined} prevTracks
   * @param {import('./types').Track[] | null | undefined} nextTracks
   */
  function mergeLoadedPlacements(prevTracks, nextTracks) {
    const prevByPath = new Map((prevTracks || []).map((/** @type {import('./types').Track} */ t) => [t.path, t]));
    return (nextTracks || []).map((/** @type {import('./types').Track} */ track) => {
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

  /**
   * @param {import('./types').Track | null | undefined} track
   * @param {{ dest_path?: string, existing?: import('./types').Placement, relative_path?: string, event?: string } | null | undefined} result
   */
  function applyExistingSetPlacement(track, result) {
    if (!track || !result) return track;
    const dest = result.dest_path || (result.existing && result.existing.path) || "";
    if (!dest) return track;
    const placements = {
      ...(track.placements || emptyPlacements()),
    };
    const sets = [...(placements.sets || [])];
    if (!sets.some((hit) => hit.path === dest)) {
      /** @type {import('./types').Placement | Record<string, string>} */
      const existing = result.existing || {};
      sets.push({
        ...existing,
        path: dest,
        relative_path:
          result.relative_path || existing.relative_path || "",
        root_name: result.event || existing.event || existing.root_name || "",
        event: result.event || existing.event || "",
      });
    }
    placements.sets = sets;
    placements.in_sets = sets.length > 0;
    track.placements = placements;
    track.placementsLoaded = true;
    track.placementsError = "";
    return track;
  }

  /**
   * @param {import('./types').Placement[] | null | undefined} hits
   */
  function _cuedCount(hits) {
    return (hits || []).filter((hit) => hit && hit.is_cued).length;
  }

  /**
   * @type {import('./types').PlacementCardModel}
   */
  function placementCardModel(track, options) {
    const review = Boolean(options && options.review);
    const libs = (track && track.placements && track.placements.library) || [];
    const sorted =
      (track && track.placements && track.placements.cues_sorted) || [];
    const sets = withCurrentSetPlacement(
      track,
      (track && track.placements && track.placements.sets) || []
    );
    const totalN = libs.length + sorted.length + sets.length;
    const cuedN = _cuedCount(libs) + _cuedCount(sorted) + _cuedCount(sets);
    const inPajamathon =
      sets.some(isPajamathonPlacement) || trackIsPajamathonSetFile(track);
    const loaded = Boolean(track && track.placementsLoaded);
    const explicitLoading = Boolean(track && track.placementsLoading);
    const loadError =
      track && track.placementsError ? String(track.placementsError) : "";
    const loading =
      totalN === 0 && (explicitLoading || (!loaded && !loadError));

    let state;
    let title;
    let note;
    if (totalN > 0) {
      state = "found";
      const titleExtra = ` · ${cuedN}/${totalN} cued`;
      title = review
        ? `Already sorted in main library${titleExtra}`
        : `Already in library${titleExtra}`;
      note = review
        ? cuedN > 0
          ? "This song already exists under House/Zouk, Cues Sorted, and/or Sets/Pajamathon with VDJ cues. Approving still moves this Add Cues copy to Ready — Copy cues pushes markers onto that copy; Delete from folder removes a library/archive file only."
          : "This song already exists under House/Zouk, Cues Sorted, and/or Sets/Pajamathon, but those copies are not cued in VirtualDJ yet. Copy cues writes this track's markers onto that file."
        : "Copy cues writes this Ready track's markers onto the existing House/Zouk/Cues Sorted/Pajamathon file without moving audio. Delete from folder Trashes a duplicate library copy. Add to Pajamathon copies this Ready file into Sets/Pajamathon 2026.";
    } else if (loading) {
      state = "loading";
      title = "Looking up library copies…";
      note = "Looking up House / Zouk / Pajamathon copies for this track.";
    } else if (loadError) {
      state = "error";
      title = "Couldn't load library copies";
      note = loadError;
    } else {
      state = "missing";
      title = "Not in Pajamathon";
      note =
        "No matching Sets/Pajamathon file. Add to Pajamathon copies this track into the event crate and clones its VirtualDJ cues.";
    }

    return {
      state,
      title,
      note,
      totalN,
      cuedN,
      inPajamathon,
      loading,
      loadError: state === "error" ? loadError : "",
      libs,
      sorted,
      sets,
      showAddButton: !inPajamathon && !loading && state !== "error",
    };
  }

  /**
   * Normalize a /api/copy-cues or /api/copy-cues-all payload into a receipt
   * the placement card can render after reload.
   * @param {Record<string, unknown> | null | undefined} payload
   * @param {string} [sourcePath]
   */
  function normalizeCueCopyReceipt(payload, sourcePath) {
    const body = payload || {};
    const dests = [];
    const rows = Array.isArray(body.results) ? body.results : null;
    if (rows) {
      for (const item of rows) {
        if (!item || !item.dest_path) continue;
        dests.push({
          path: String(item.dest_path),
          root: String(item.root_name || ""),
          relative: String(item.relative_path || ""),
          ok: Boolean(item.ok),
          overwrote: Boolean(item.overwrote),
          status: item.ok ? "copied" : String(item.status || "failed"),
        });
      }
    } else if (body.dest_path) {
      dests.push({
        path: String(body.dest_path),
        root: String(body.root_name || ""),
        relative: String(body.relative_path || ""),
        ok: body.ok !== false,
        overwrote: Boolean(body.overwrote),
        status: "copied",
      });
    }
    const copiedRows = dests.filter((row) => row.ok);
    return {
      sourcePath: String(sourcePath || body.source_path || ""),
      cues: Number(body.copied_cues) || 0,
      loops: Number(body.copied_loops) || 0,
      copied: Number(body.copied) || copiedRows.length,
      skipped: Number(body.skipped) || 0,
      failed: Number(body.failed) || 0,
      dests,
    };
  }

  /**
   * @param {{ root?: string, relative?: string } | null | undefined} dest
   */
  function cueCopyDestName(dest) {
    const root = String((dest && dest.root) || "");
    if (/cues sorted/i.test(root)) return "Archive";
    if (/pajamathon/i.test(root) || /^sets$/i.test(root)) return "Pajamathon";
    return root || (dest && dest.relative) || "copy";
  }

  /**
   * @param {ReturnType<typeof normalizeCueCopyReceipt> | null | undefined} receipt
   */
  function cueCopyReceiptLabel(receipt) {
    if (!receipt || !receipt.copied) return "";
    const cues = Number(receipt.cues) || 0;
    const loops = Number(receipt.loops) || 0;
    const countBit =
      `${cues} cue${cues === 1 ? "" : "s"}` +
      (loops ? ` · ${loops} loop${loops === 1 ? "" : "s"}` : "");
    const names = (receipt.dests || [])
      .filter((row) => row && row.ok)
      .map((row) => cueCopyDestName(row));
    const destBit = names.length
      ? names.join(", ")
      : `${receipt.copied} location${receipt.copied === 1 ? "" : "s"}`;
    let label = `Just copied ${countBit} onto ${destBit}`;
    if (receipt.skipped) {
      label += ` · skipped ${receipt.skipped}`;
    }
    if (receipt.failed) {
      label += ` · failed ${receipt.failed}`;
    }
    return label;
  }

  /**
   * @param {ReturnType<typeof normalizeCueCopyReceipt> | null | undefined} receipt
   * @param {string | null | undefined} path
   */
  function cueCopyDestForPath(receipt, path) {
    if (!receipt || !path) return null;
    const want = String(path);
    return (
      (receipt.dests || []).find((row) => row && row.ok && row.path === want) ||
      null
    );
  }

  return {
    isPajamathonPlacement,
    trackIsPajamathonSetFile,
    withCurrentSetPlacement,
    emptyPlacements,
    placementsArePopulated,
    mergeLoadedPlacements,
    applyExistingSetPlacement,
    placementCardModel,
    normalizeCueCopyReceipt,
    cueCopyDestName,
    cueCopyReceiptLabel,
    cueCopyDestForPath,
  };
});
