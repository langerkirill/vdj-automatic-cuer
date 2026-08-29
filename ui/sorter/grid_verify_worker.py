"""Beatgrid deep-verify in a process outside uvicorn --reload."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", required=True)
    parser.add_argument("--bpm", type=float, required=True)
    parser.add_argument("--mix-only", action="store_true")
    parser.add_argument("--database", required=True)
    parser.add_argument("--repo-root", required=True)
    args = parser.parse_args(argv)

    repo = Path(args.repo_root)
    sys.path.insert(0, str(repo))
    os.chdir(str(repo))

    try:
        from dotenv import load_dotenv

        load_dotenv(repo / ".env", override=False)
        load_dotenv(repo / "ui" / ".env", override=False)
    except Exception:
        pass

    from vdj_cuer import AutomaticMusicCuer

    cuer = AutomaticMusicCuer(
        gemini_api_key=None,
        vdj_database_path=args.database,
    )
    if args.mix_only:
        cuer._beatgrid_mix_only = True
    result = cuer._verify_beatgrid_alignment(args.path, args.bpm)
    try:
        cuer._release_track_resources(args.path)
    except Exception:
        pass
    print(
        json.dumps(
            {
                "verified": True,
                "offset": result.offset,
                "corrected": result.corrected,
                "shift_beats": result.shift_beats,
                "fine_shift_seconds": result.fine_shift_seconds,
                "confidence_ratio": result.confidence_ratio,
                "source": result.source,
                "beat_score": result.beat_score,
                "best_beat_score": result.best_beat_score,
                "error": None,
                "stems_skipped": bool(
                    getattr(result, "stems_skipped", False) or args.mix_only
                ),
            }
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            json.dumps(
                {
                    "verified": False,
                    "error": str(exc),
                    "stems_skipped": True,
                }
            )
        )
        raise SystemExit(2)
