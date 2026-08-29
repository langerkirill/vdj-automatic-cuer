#!/usr/bin/env python3
"""Copy cued sibling markers onto Sets/Pajamathon files. Skip matches."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from sorter.action_log import append_action
from sorter.config import ADD_CUES
from sorter.library import (
    cached_placement_indexes,
    find_cues_sorted_matches,
    find_library_matches,
    is_pajamathon_set_audio,
    list_add_cues_tracks,
    list_all_set_tracks,
)
from sorter.relocate import copy_cues_to_placement, summarize_cues


def _times(points, kind):
    out = []
    for p in points or []:
        if getattr(p, "kind", None) != kind:
            continue
        try:
            out.append(round(float(p.pos), 2))
        except (TypeError, ValueError):
            continue
    return sorted(out)


def _same(a, b) -> bool:
    return _times(a.points, "cue") == _times(b.points, "cue") and _times(
        a.points, "loop"
    ) == _times(b.points, "loop")


def _pick_sibling(set_track, add_by_name, placement_index) -> Path | None:
    src = Path(set_track.path).resolve()
    candidates: list[Path] = []
    for t in add_by_name.get(set_track.name.lower(), []):
        p = Path(t.path).resolve()
        if p != src:
            candidates.append(p)
    for hit in find_cues_sorted_matches(set_track.name, index=placement_index):
        p = Path(hit["path"]).resolve()
        if p != src:
            candidates.append(p)
    for hit in find_library_matches(set_track.name, index=placement_index):
        p = Path(hit["path"]).resolve()
        if p != src:
            candidates.append(p)
    best = None
    best_score = -1
    for path in candidates:
        cues = summarize_cues(path)
        if cues.cue_count < 1 and cues.loop_count < 1:
            continue
        score = cues.cue_count + cues.loop_count
        try:
            path.relative_to(ADD_CUES.resolve())
            score += 20
        except ValueError:
            pass
        if "cues sorted" in str(path).lower():
            score += 10
        if score > best_score:
            best = path
            best_score = score
    return best


def main() -> int:
    apply = "--apply" in sys.argv
    set_tracks = [t for t in list_all_set_tracks() if is_pajamathon_set_audio(t.path)]
    add_by_name: dict[str, list] = {}
    for t in list_add_cues_tracks():
        add_by_name.setdefault(t.name.lower(), []).append(t)
    placement_index, _ = cached_placement_indexes()

    planned = []
    skipped_match = 0
    skipped_no_sib = 0
    for t in set_tracks:
        sib = _pick_sibling(t, add_by_name, placement_index)
        if sib is None:
            skipped_no_sib += 1
            continue
        dest_cues = summarize_cues(t.path)
        src_cues = summarize_cues(sib)
        if dest_cues.cue_count or dest_cues.loop_count:
            if _same(dest_cues, src_cues):
                skipped_match += 1
                continue
        planned.append((sib, Path(t.path), src_cues, dest_cues))

    print(
        json.dumps(
            {
                "set_tracks": len(set_tracks),
                "planned": len(planned),
                "skipped_match": skipped_match,
                "skipped_no_sibling": skipped_no_sib,
                "apply": apply,
            }
        )
    )
    for src, dest, src_cues, dest_cues in planned[:25]:
        print(
            f"  {src.name} -> {dest.name}  "
            f"src {src_cues.cue_count}c/{src_cues.loop_count}l  "
            f"dest {dest_cues.cue_count}c/{dest_cues.loop_count}l"
        )
    if len(planned) > 25:
        print(f"  ... +{len(planned) - 25} more")
    if not apply:
        return 0

    copied = 0
    failed = 0
    backup_once = True
    for src, dest, src_cues, dest_cues in planned:
        try:
            result = copy_cues_to_placement(
                src,
                dest,
                overwrite=True,
                create_backup=backup_once,
                allow_vdj_running=False,
            )
            backup_once = False
            copied += 1
            append_action(
                "copy_cues",
                source_path=str(src),
                dest_path=str(dest),
                name=dest.name,
                details={
                    "batch": "set_pajamathon_siblings",
                    "copied_cues": result.get("copied_cues"),
                    "copied_loops": result.get("copied_loops"),
                },
            )
        except Exception as exc:
            failed += 1
            print(f"FAIL {dest.name}: {exc}")
    print(json.dumps({"copied": copied, "failed": failed}))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
