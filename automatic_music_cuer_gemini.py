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
        type=int,
        default=5,
        help="Number of songs to process in each batch (default: 5)",
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

    if not args.dry_run and cuer.is_virtualdj_running():
        print("❌ VirtualDJ appears to be running.")
        print("   Close VirtualDJ before making database changes, then run again.")
        print("   Dry-runs are safe while VirtualDJ is open: add --dry-run.")
        return

    # Create backup if requested (only once at the beginning)
    if args.backup and not args.dry_run:
        cuer.backup_database()

    # Process files in batches using efficient batch processing
    success_count = 0

    for batch_num in range(num_batches):
        start_idx = batch_num * batch_size
        end_idx = min(start_idx + batch_size, total_files)
        batch_files = audio_files[start_idx:end_idx]

        print(
            f"\n🔄 Batch {batch_num + 1}/{num_batches} - "
            f"Processing {len(batch_files)} files"
        )
        print(f"📊 Overall Progress: {start_idx}/{total_files} files completed")

        # Check if all batch files exist
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
            # Use async batch processing for concurrent uploads and retries
            batch_results = asyncio.run(
                cuer.process_audio_batch_async(valid_batch_files, args.dry_run)
            )

            # Count successes
            batch_success = sum(batch_results)
            success_count += batch_success

            print(
                f"\n✅ Batch {batch_num + 1} complete: {batch_success}/"
                f"{len(valid_batch_files)} files processed successfully"
            )

        except KeyboardInterrupt:
            print("\n⏹️  Processing interrupted by user")
            print(f"📊 Processed {success_count} files before interruption")
            return
        except Exception as e:
            print(f"❌ Error processing batch {batch_num + 1}: {e}")
            import traceback

            traceback.print_exc()
            continue

        # Add delay between batches if specified
        if args.batch_delay > 0 and batch_num < num_batches - 1:
            print(f"⏳ Waiting {args.batch_delay} seconds before next batch...")
            time.sleep(args.batch_delay)

    print(
        f"\n🎯 All batches complete: {success_count}/{total_files} files "
        f"processed successfully"
    )

    if args.dry_run:
        print("🔍 This was a dry run - no changes were made to the database")
        print("💡 Remove --dry-run flag to apply changes")


if __name__ == "__main__":
    main()
