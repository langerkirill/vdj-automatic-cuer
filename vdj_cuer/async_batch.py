"""Resource-bounded asynchronous batch processing with surgical DB writes."""

from .common import *


# Keep concurrent Gemini/ffmpeg work low to protect host RAM.
MAX_CONCURRENT_AUDIO_TASKS = 1


class AsyncBatchMixin:
    async def upload_file_with_retry(
        self, audio_file_path: str, max_retries: int = DEFAULT_UPLOAD_RETRIES
    ) -> Optional[object]:
        """Upload one file asynchronously with cancellable retry delays."""
        file_size = os.path.getsize(audio_file_path) / (1024 * 1024)
        print(
            f"📤 Uploading {os.path.basename(audio_file_path)} "
            f"({file_size:.1f} MB)..."
        )

        for retry in range(max_retries):
            try:
                uploaded_file = await self._upload_audio_file_async(audio_file_path)
                print(f"✅ {os.path.basename(audio_file_path)} upload complete")
                return uploaded_file
            except asyncio.CancelledError:
                raise
            except Exception as error:
                if self._is_retryable_error(error, NETWORK_ERROR_TERMS) and (
                    retry < max_retries - 1
                ):
                    wait_time = min((retry + 1) ** 2, 30)
                    print(
                        f"⚠️  {os.path.basename(audio_file_path)} upload failed "
                        f"(attempt {retry + 1}/{max_retries}): {error}"
                    )
                    print(f"🔄 Retrying in {wait_time} seconds...")
                    await asyncio.sleep(wait_time)
                    continue
                print(
                    f"❌ Failed to upload {os.path.basename(audio_file_path)} "
                    f"after {max_retries} attempts: {error}"
                )
                return None
        return None

    async def _run_bounded(self, entries, operation):
        """Run an async per-entry operation with a hard concurrency ceiling."""
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_AUDIO_TASKS)

        async def run_one(entry):
            async with semaphore:
                return await operation(entry)

        return await asyncio.gather(
            *(run_one(entry) for entry in entries), return_exceptions=True
        )

    async def process_audio_batch_async(
        self, audio_file_paths: List[str], dry_run: bool = False
    ) -> List[bool]:
        """
        Process tracks with bounded analysis and surgical per-song DB writes.

        Never materializes the full VirtualDJ XML tree for multi-song batches.
        """
        print(f"\n🎶 Processing batch of {len(audio_file_paths)} songs:")
        for path in audio_file_paths:
            print(f"   - {os.path.basename(path)}")

        results = [False] * len(audio_file_paths)
        valid_entries = [
            (index, path)
            for index, path in enumerate(audio_file_paths)
            if self._validate_file_in_database(path)
        ]
        if not valid_entries:
            print("❌ No valid files found in VDJ database")
            return results
        print(f"✅ {len(valid_entries)} files validated in VDJ database")

        uploaded_entries = []
        try:
            print(f"📤 Uploading {len(valid_entries)} audio files...")

            async def upload(entry):
                index, file_path = entry
                return index, file_path, await self.upload_file_with_retry(file_path)

            upload_results = await self._run_bounded(valid_entries, upload)
            for entry, upload_result in zip(valid_entries, upload_results):
                index, file_path = entry
                if isinstance(upload_result, Exception):
                    print(
                        f"❌ Failed to upload {os.path.basename(file_path)}: "
                        f"{upload_result}"
                    )
                    continue
                _, _, uploaded_file = upload_result
                if uploaded_file is None:
                    print(f"❌ Upload failed for {os.path.basename(file_path)}")
                    continue
                uploaded_entries.append((index, file_path, uploaded_file))

            if not uploaded_entries:
                print("❌ No files uploaded successfully")
                return results
            print(
                f"✅ Successfully uploaded {len(uploaded_entries)}/"
                f"{len(valid_entries)} files"
            )

            print(f"🤖 Analyzing {len(uploaded_entries)} songs with Gemini...")

            async def analyze(entry):
                index, file_path, uploaded_file = entry
                analysis = await self.analyze_audio_with_gemini_async(
                    file_path, uploaded_file
                )
                return index, file_path, analysis

            analysis_results = await self._run_bounded(uploaded_entries, analyze)
            analyzed_entries = []
            for uploaded_entry, analysis_result in zip(
                uploaded_entries, analysis_results
            ):
                index, file_path, _ = uploaded_entry
                if isinstance(analysis_result, Exception):
                    print(
                        f"❌ Analysis failed for {os.path.basename(file_path)}: "
                        f"{analysis_result}"
                    )
                    self._release_track_resources(file_path)
                    continue
                _, _, analysis = analysis_result
                if not analysis:
                    print(f"❌ No analysis result for {os.path.basename(file_path)}")
                    self._release_track_resources(file_path)
                    continue
                analyzed_entries.append((index, file_path, analysis))

            if not analyzed_entries:
                print("❌ Failed to analyze any songs")
                return results

            # Surgical per-song write: only one Song block is rewritten at a time.
            for index, file_path, analysis in analyzed_entries:
                try:
                    wrote = self._apply_cues_to_database(
                        file_path, analysis, dry_run=dry_run
                    )
                    results[index] = wrote
                    if wrote and not dry_run:
                        # Customary: render waveform + compare cues to the grid.
                        self.audit_track_after_cue(file_path, dry_run=False)
                finally:
                    self._release_track_resources(file_path)

            print(
                f"🎯 Async batch complete: {sum(results)}/"
                f"{len(audio_file_paths)} songs processed successfully"
            )
            return results
        except asyncio.CancelledError:
            print("⏹️  Batch cancelled; cleaning up remote files...")
            raise
        except Exception as error:
            print(f"❌ Error processing async batch: {error}")
            import traceback

            traceback.print_exc()
            return results
        finally:
            remote_files = [uploaded for _, _, uploaded in uploaded_entries]
            if remote_files:
                await asyncio.shield(self._delete_uploaded_files_async(remote_files))
