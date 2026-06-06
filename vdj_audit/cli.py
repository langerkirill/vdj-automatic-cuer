"""Command-line entrypoint for cue visual audits."""

from .common import *
from .database import load_tracks
from .reports import write_reports

def expand_audio_paths(paths: list[str]) -> list[str]:
    audio_exts = {".flac", ".mp3", ".m4a", ".wav", ".aac", ".ogg", ".opus"}
    expanded = []
    for raw_path in paths:
        path = Path(raw_path).expanduser()
        if path.is_dir():
            expanded.extend(
                str(item)
                for item in sorted(path.iterdir())
                if item.suffix.lower() in audio_exts
            )
        elif path.suffix.lower() in audio_exts:
            expanded.append(str(path))
    return expanded


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", help="audio files or folders to audit")
    parser.add_argument("--database", default=str(DEFAULT_DATABASE))
    parser.add_argument("--output", default="cue_visual_audit")
    parser.add_argument("--bins", type=int, default=1200)
    args = parser.parse_args()

    audio_paths = expand_audio_paths(args.paths)
    tracks = load_tracks(Path(args.database).expanduser(), audio_paths)
    issues = write_reports(tracks, Path(args.output), args.bins)
    print(f"wrote {len(tracks)} track reports to {args.output}")
    print(f"found {len(issues)} potential issues")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
