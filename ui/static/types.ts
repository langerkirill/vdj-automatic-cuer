/**
 * Shared Music Sorter UI types.
 * Type-check only — the browser loads classic UMD scripts, not this file.
 */

export type CuePoint = {
  kind?: string;
  type?: string;
  num?: number;
  pos?: number;
  name?: string;
  size?: number;
  color_name?: string;
  comment?: string;
};

export type Placement = {
  path: string;
  relative_path?: string;
  root_name?: string;
  event?: string;
  is_current?: boolean;
  is_cued?: boolean;
  cue_count?: number;
  loop_count?: number;
  in_database?: boolean;
};

export type TrackCues = {
  title?: string;
  author?: string;
  cue_count?: number;
  loop_count?: number;
  bpm?: number;
  key?: string;
  camelot?: string;
  points?: CuePoint[];
  song_length?: number;
  in_database?: boolean;
  scan_phase?: number;
  beatgrid_pos?: number;
};

export type TrackPlacements = {
  library?: Placement[];
  sets?: Placement[];
  cues_sorted?: Placement[];
  in_library?: boolean;
  in_sets?: boolean;
  in_cues_sorted?: boolean;
  already_sorted?: boolean;
  any_library_cued?: boolean;
  any_archive_cued?: boolean;
  any_set_cued?: boolean;
};

export type Track = {
  path: string;
  name?: string;
  relative_path?: string;
  group?: string;
  section?: string;
  is_cued?: boolean;
  artist?: string;
  title?: string;
  fit?: number;
  duration?: number;
  bpm?: number;
  key?: string;
  camelot?: string;
  bitrate_kbps?: number;
  codec?: string;
  sample_rate?: number;
  cues?: TrackCues;
  placements?: TrackPlacements;
  placementsLoaded?: boolean;
  placementsLoading?: boolean;
  placementsError?: string;
  readiness?: { status?: string };
  retry_history?: {
    kind?: string;
    tried_cues?: boolean;
    tried_loops?: boolean;
    tried_both?: boolean;
  };
};

export type RetryJob = {
  id?: string;
  name?: string;
  message?: string;
  status?: string;
  writeScope?: string;
  write_scope?: string;
  pollTimer?: number | null;
};

export type WaveView = {
  start: number;
  end: number;
  span: number;
  offset: number;
  zoom: number;
};

export type AddCuesReadinessRank = (
  track: Track | null | undefined
) => number;

export type TrackRetryKind = (
  track: Track | null | undefined,
  job?: RetryJob | null
) => "both" | "cues" | "loops" | null;

export type ClassifyWaveMarkers = (
  points: Array<CuePoint> | null | undefined,
  view: { start: number; end: number },
  slack?: number
) => { inView: CuePoint[]; offLeft: CuePoint[]; offRight: CuePoint[] };

export type FormatOffscreenCueLabel = (
  points: Array<CuePoint> | null | undefined,
  side: string
) => string;

export type TrackDisplayTitle = (track: Track | null | undefined) => string;

export type SortAssemblePlaylist = (
  tracks: Array<Track> | null | undefined,
  mode?: string
) => Track[];

export type PlacementCardModel = (
  track: Track | null | undefined,
  options?: { review?: boolean }
) => {
  state: string;
  title: string;
  note: string;
  totalN: number;
  cuedN: number;
  inPajamathon: boolean;
  loading: boolean;
  loadError: string;
  libs: Placement[];
  sorted: Placement[];
  sets: Placement[];
  showAddButton: boolean;
};

export const SHARED_TYPE_NAMES = [
  "Track",
  "CuePoint",
  "Placement",
  "RetryJob",
] as const;
