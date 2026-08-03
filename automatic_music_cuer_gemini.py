#!/usr/bin/env python3
"""CLI and compatibility wrapper for VirtualDJ automatic cue generation."""

import argparse
import asyncio
import os
import time

from vdj_cuer import (
    AutomaticMusicCuer,
    BatchMusicAnalysis,
    BeatgridAlignment,
    DEFAULT_ANALYSIS_RETRIES,
    DEFAULT_GEMINI_MODEL,
    DEFAULT_UPLOAD_RETRIES,
    LoopSegment,
    MeasureChange,
    MusicAnalysis,
    StemActivity,
)
from vdj_cuer.common import (
    WRITE_SCOPE_ALL,
    WRITE_SCOPE_CUES,
    WRITE_SCOPE_LOOPS,
)

# Default 1: large VirtualDJ libraries make concurrent analysis + DB work crash-prone.
MAX_BATCH_SIZE = 2
DEFAULT_BATCH_SIZE = 1


def parse_batch_size(value: str) -> int:
    """Limit concurrent audio work to a machine-safe range."""
    try:
        batch_size = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("batch size must be an integer") from error
    if not 1 <= batch_size <= MAX_BATCH_SIZE:
        raise argparse.ArgumentTypeError(
            f"batch size must be between 1 and {MAX_BATCH_SIZE}; "
            "larger track lists are split into multiple batches"
        )
    return batch_size


def expand_audio_files(paths):
    """Expand directories and file patterns into audio files"""
    import glob

    audio_extensions = {
        ".mp3",
        ".flac",
        ".wav",
        ".m4a",
        ".aac",
        ".ogg",
        ".opus",
        ".mpeg",
    }
    audio_files = []

    for path in paths:
        if os.path.isfile(path):
            # Single file
            if any(path.lower().endswith(ext) for ext in audio_extensions):
                audio_files.append(path)
            else:
                print(f"⚠️  Skipping non-audio file: {path}")
        elif os.path.isdir(path):
            # Directory - find all audio files recursively
            print(f"📁 Scanning directory: {path}")
            found_files = []
            for root, _, files in os.walk(path):
                for file in files:
                    if any(file.lower().endswith(ext) for ext in audio_extensions):
                        full_path = os.path.join(root, file)
                        found_files.append(full_path)

            found_files.sort()  # Sort for consistent processing order
            audio_files.extend(found_files)
            print(f"📁 Found {len(found_files)} audio files in {path}")
        else:
            # Try glob pattern
            matches = glob.glob(path)
            if matches:
                for match in matches:
                    if os.path.isfile(match) and any(
                        match.lower().endswith(ext) for ext in audio_extensions
                    ):
                        audio_files.append(match)
            else:
                print(f"❌ Path not found: {path}")

    return audio_files


def main():
    """Main function to run the music cuer."""
    parser = argparse.ArgumentParser(
        description="Automatic Music Cueing for VirtualDJ (Gemini)"
    )
    parser.add_argument(
        "paths", nargs="+", help="Audio files or directories to process"
    )
    parser.add_argument("--api-key", help="Gemini API key (optional if in .env file)")
    parser.add_argument(
        "--model",
        default=None,
        help=(
            f"Gemini model to use (default: GEMINI_MODEL or {DEFAULT_GEMINI_MODEL})"
        ),
    )
    parser.add_argument("--database", help="Path to VDJ database.xml")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without modifying database",
    )
    parser.add_argument(
        "--backup",
        action="store_true",
        default=True,
        help="Create database backup (default: True)",
    )
    parser.add_argument(
        "--recursive",
        "-r",
        action="store_true",
        default=True,
        help="Process directories recursively (default: True)",
    )
    parser.add_argument(
        "--batch-size",
        "-b",
        type=parse_batch_size,
        default=DEFAULT_BATCH_SIZE,
        help=(
            f"Songs processed per batch (default: {DEFAULT_BATCH_SIZE}; "
            f"max {MAX_BATCH_SIZE}). Use 1 on large libraries to avoid RAM spikes."
        ),
    )
    parser.add_argument(
        "--batch-delay",
        type=int,
        default=0,
        help="Delay in seconds between batches (default: 0)",
    )
    parser.add_argument(
        "--max-songs",
        "-m",
        type=int,
        default=None,
        help="Maximum number of songs to process (default: all songs)",
    )
    parser.add_argument(
        "--audit",
        dest="audit",
        action="store_true",
        default=True,
        help=(
            "After each successful write, generate a waveform image and compare "
            "cues against the beatgrid (default: on)"
        ),
    )
    parser.add_argument(
        "--no-audit",
        dest="audit",
        action="store_false",
        help="Skip post-cue visual/grid audits",
    )
    parser.add_argument(
        "--audit-dir",
        default=None,
        help="Directory for post-cue audits (default: audit_reports/auto/<timestamp>)",
    )
    retry_group = parser.add_mutually_exclusive_group()
    retry_group.add_argument(
        "--cues-only",
        action="store_true",
        help=(
            "Retry cues only: rewrite cue points from analysis, leave existing "
            "loops unchanged in the database"
        ),
    )
    retry_group.add_argument(
        "--loops-only",
        action="store_true",
        help=(
            "Retry loops only: rewrite loops from analysis, leave existing "
            "cue points unchanged in the database"
        ),
    )

    args = parser.parse_args()

    # Expand directories and patterns into audio files
    audio_files = expand_audio_files(args.paths)

    if not audio_files:
        print("❌ No audio files found to process")
        return

    # Limit number of songs if max-songs is specified
    original_count = len(audio_files)
    if args.max_songs and args.max_songs < len(audio_files):
        audio_files = audio_files[: args.max_songs]
        print(
            f"🎯 Limited to first {args.max_songs} songs out of "
            f"{original_count} found"
        )

    # Split into batches
    total_files = len(audio_files)
    batch_size = args.batch_size
    num_batches = (total_files + batch_size - 1) // batch_size
    print(f"🎵 Processing {total_files} audio files")
    print(f"📦 Processing in {num_batches} batches of {batch_size} songs each")

    # Initialize cuer (will auto-load from .env if api_key not provided)
    cuer = AutomaticMusicCuer(args.api_key, args.database, args.model)
    if args.cues_only:
        cuer.write_scope = WRITE_SCOPE_CUES
        print("🎯 Mode: cues only (existing loops will be kept)")
    elif args.loops_only:
        cuer.write_scope = WRITE_SCOPE_LOOPS
        print("🎯 Mode: loops only (existing cues will be kept)")
    else:
        cuer.write_scope = WRITE_SCOPE_ALL
    cuer.post_cue_audit_enabled = bool(args.audit) and not args.dry_run
    if args.audit_dir:
        cuer.post_cue_audit_dir = args.audit_dir
    if cuer.post_cue_audit_enabled:
        print(
            "🖼️  Post-cue audits enabled "
            "(waveform + beatgrid comparison after each write)"
        )

    if not args.dry_run and cuer.is_virtualdj_running():
        print("❌ VirtualDJ appears to be running.")
        print("   Close VirtualDJ before making database changes, then run again.")
        print("   Dry-runs are safe while VirtualDJ is open: add --dry-run.")
        return

    # Create backup if requested (only once at the beginning)
    if args.backup and not args.dry_run:
        cuer.backup_database()

    # One event loop for the whole run. Recreating asyncio.run() per batch leaves
    # the Gemini async client bound to a closed loop ("Event loop is closed").
    success_count = 0

    async def process_all_batches() -> int:
        completed = 0
        for batch_num in range(num_batches):
            start_idx = batch_num * batch_size
            end_idx = min(start_idx + batch_size, total_files)
            batch_files = audio_files[start_idx:end_idx]

            print(
                f"\n🔄 Batch {batch_num + 1}/{num_batches} - "
                f"Processing {len(batch_files)} files"
            )
            print(f"📊 Overall Progress: {start_idx}/{total_files} files completed")

            valid_batch_files = []
            for audio_file in batch_files:
                if os.path.exists(audio_file):
                    valid_batch_files.append(audio_file)
                else:
                    print(f"❌ File not found: {audio_file}")

            if not valid_batch_files:
                print(f"❌ No valid files in batch {batch_num + 1}")
                continue

            try:
                batch_results = await cuer.process_audio_batch_async(
                    valid_batch_files, args.dry_run
                )
                batch_success = sum(batch_results)
                completed += batch_success
                print(
                    f"\n✅ Batch {batch_num + 1} complete: {batch_success}/"
                    f"{len(valid_batch_files)} files processed successfully"
                )
            except Exception as e:
                print(f"❌ Error processing batch {batch_num + 1}: {e}")
                import traceback

                traceback.print_exc()

            if args.batch_delay > 0 and batch_num < num_batches - 1:
                print(
                    f"⏳ Waiting {args.batch_delay} seconds before next batch..."
                )
                await asyncio.sleep(args.batch_delay)

        return completed

    try:
        success_count = asyncio.run(process_all_batches())
    except KeyboardInterrupt:
        print("\n⏹️  Processing interrupted by user")
        print(f"📊 Processed {success_count} files before interruption")
        return

    print(
        f"\n🎯 All batches complete: {success_count}/{total_files} files "
        f"processed successfully"
    )

    if args.dry_run:
        print("🔍 This was a dry run - no changes were made to the database")
        print("💡 Remove --dry-run flag to apply changes")


if __name__ == "__main__":
    main()
