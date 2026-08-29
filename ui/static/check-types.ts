/**
 * Boundary types that the shipped domain JS must satisfy.
 * Imported via JSDoc from state/transport/waveform/assemble/placements.
 * Not loaded by the browser.
 */
import type {
  AddCuesReadinessRank,
  ClassifyWaveMarkers,
  CuePoint,
  FormatOffscreenCueLabel,
  Placement,
  PlacementCardModel,
  RetryJob,
  SortAssemblePlaylist,
  Track,
  TrackDisplayTitle,
  TrackRetryKind,
} from "./types";
import { SHARED_TYPE_NAMES } from "./types";

export type {
  AddCuesReadinessRank,
  ClassifyWaveMarkers,
  CuePoint,
  FormatOffscreenCueLabel,
  Placement,
  PlacementCardModel,
  RetryJob,
  SortAssemblePlaylist,
  Track,
  TrackDisplayTitle,
  TrackRetryKind,
};

export function assertSharedTypes(
  track: Track,
  cue: CuePoint,
  placement: Placement,
  job: RetryJob
): string {
  return [track.path, cue.kind || "cue", placement.path, job.status || ""].join(
    ":"
  );
}

export function sharedTypeNames(): readonly string[] {
  return SHARED_TYPE_NAMES;
}

const _names: readonly ["Track", "CuePoint", "Placement", "RetryJob"] =
  SHARED_TYPE_NAMES;
void _names;
