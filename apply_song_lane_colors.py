#!/usr/bin/env python3
"""Apply or preview Zouk lane colors on VirtualDJ song names.

Default is a dry-run. Requires VirtualDJ to be closed for --apply.
Only cued songs are painted unless --all-in-scope is passed.
"""

from __future__ import annotations

import argparse
import shutil
from datetime import datetime
from pathlib import Path

from song_lane_color import (
    LANE_COLORS,
    PENDING_LANE,
    plan_color_updates,
    rewrite_database_user_colors,
    summarize_plan,
)
from vdj_database_safety import (
    VirtualDJRunningError,
    assert_safe_to_write_vdj_database,
    is_virtualdj_running,
    read_vdj_database_text,
)

DEFAULT_DB = (
    Path.home() / "Library" / "Application Support" / "VirtualDJ" / "database.xml"
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DB,
        help="VirtualDJ database.xml path",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write UserColor values (default is dry-run)",
    )
    parser.add_argument(
        "--all-in-scope",
        action="store_true",
        help="Color every in-scope song, not only cued ones",
    )
    args = parser.parse_args()
    cued_only = not args.all_in_scope

    content = read_vdj_database_text(args.database)
    plan = plan_color_updates(content, cued_only=cued_only)
    counts = summarize_plan(plan)
    print(f"Database: {args.database}")
    print(f"Cued only: {cued_only}")
    print(f"Songs to change: {len(plan)}")
    for lane in list(LANE_COLORS) + [PENDING_LANE, "clear"]:
        if lane in counts:
            print(f"  {lane:8} {counts[lane]}")

    if not args.apply:
        print("Dry-run only. Re-run with --apply to write.")
        return 0

    if is_virtualdj_running():
        print("VirtualDJ is open. Close it, then re-run --apply.")
        return 2

    try:
        assert_safe_to_write_vdj_database()
    except VirtualDJRunningError as exc:
        print(exc)
        return 2

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = args.database.with_name(f"{args.database.name}.backup.{stamp}.song-lane-color")
    shutil.copy2(args.database, backup)
    print(f"Backup: {backup}")
    stats = rewrite_database_user_colors(args.database, cued_only=cued_only)
    print(f"Updated {stats['updated']} songs (library still has {stats['song_count']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
