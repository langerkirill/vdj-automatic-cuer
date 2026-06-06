"""AsyncBatchMixin for AutomaticMusicCuer."""

from .common import *


class AsyncBatchMixin:
    async def upload_file_with_retry(
        self, audio_file_path: str, max_retries: int = 5
    ) -> Optional[object]:
        """Upload a single file with exponential backoff retry logic"""
        file_size = os.path.getsize(audio_file_path) / (1024 * 1024)  # MB
        print(
            f"📤 Uploading {os.path.basename(audio_file_path)} "
            f"({file_size:.1f} MB)..."
        )

        for retry in range(max_retries):
            try:
                uploaded_file = await asyncio.get_running_loop().run_in_executor(
                    None, self._upload_audio_file, audio_file_path
                )
                print(f"✅ {os.path.basename(audio_file_path)} upload complete")
                return uploaded_file
            except Exception as e:
                if self._is_retryable_error(e, NETWORK_ERROR_TERMS) and (
                    retry < max_retries - 1
                ):
                    wait_time = min(
                        (retry + 1) ** 2, 30
                    )  # Exponential backoff: 1s, 4s, 9s...
                    print(
                        f"⚠️  {os.path.basename(audio_file_path)} upload "
                        f"failed (attempt {retry + 1}/{max_retries}): {e}"
                    )
                    print(f"🔄 Retrying in {wait_time} seconds...")
                    await asyncio.sleep(wait_time)
                else:
                    print(
                        f"❌ Failed to upload {os.path.basename(audio_file_path)} "
                        f"after {max_retries} attempts: {e}"
                    )
                    return None

        return None

    async def process_audio_batch_async(
        self, audio_file_paths: List[str], dry_run: bool = False
    ) -> List[bool]:
        """Process multiple audio files concurrently using asyncio"""
        print(f"\n🎶 Processing batch of {len(audio_file_paths)} songs concurrently:")
        for path in audio_file_paths:
            print(f"   - {os.path.basename(path)}")

        results = []
        valid_files = []

        # First, validate all files exist in VDJ database
        for audio_file_path in audio_file_paths:
            if self._validate_file_in_database(audio_file_path):
                valid_files.append(audio_file_path)
                results.append(True)  # Placeholder, will be updated
            else:
                results.append(False)

        if not valid_files:
            print("❌ No valid files found in VDJ database")
            return results

        print(f"✅ {len(valid_files)} files validated in VDJ database")

        try:
            # Upload all files concurrently
            print(f"📤 Uploading {len(valid_files)} audio files concurrently...")
            upload_tasks = [
                self.upload_file_with_retry(file_path) for file_path in valid_files
            ]
            uploaded_results = await asyncio.gather(
                *upload_tasks, return_exceptions=True
            )

            # Filter successful uploads
            uploaded_files = []
            successful_uploads = 0
            for i, (file_path, result) in enumerate(zip(valid_files, uploaded_results)):
                if isinstance(result, Exception):
                    print(
                        f"❌ Failed to upload "
                        f"{os.path.basename(file_path)}: {result}"
                    )
                elif result is not None:
                    uploaded_files.append((file_path, result))
                    successful_uploads += 1
                else:
                    print(f"❌ Upload failed for {os.path.basename(file_path)}")

            if not uploaded_files:
                print("❌ No files uploaded successfully")
                return [False] * len(audio_file_paths)

            print(
                f"✅ Successfully uploaded "
                f"{successful_uploads}/{len(valid_files)} files"
            )

            if dry_run:
                # For dry run, analyze each song individually
                print(
                    f"🤖 Analyzing {len(uploaded_files)} songs with Gemini "
                    f"(concurrent individual calls)..."
                )

                # Create concurrent analysis tasks
                analysis_tasks = []
                for audio_file_path, uploaded_file in uploaded_files:
                    task = asyncio.get_running_loop().run_in_executor(
                        None,
                        self.analyze_audio_with_gemini,
                        audio_file_path,
                        uploaded_file,
                    )
                    analysis_tasks.append(task)

                # Run all analyses concurrently
                analysis_results = await asyncio.gather(
                    *analysis_tasks, return_exceptions=True
                )

                # Process each song's results (dry run)
                batch_success = []
                for i, (audio_file_path, _) in enumerate(uploaded_files):
                    if (
                        i < len(analysis_results)
                        and not isinstance(analysis_results[i], Exception)
                        and analysis_results[i]
                    ):
                        song_analysis = analysis_results[i]
                        success = self._apply_cues_to_database(
                            audio_file_path, song_analysis, dry_run=True
                        )
                        batch_success.append(success)
                    else:
                        if isinstance(analysis_results[i], Exception):
                            print(
                                f"❌ Analysis failed for "
                                f"{os.path.basename(audio_file_path)}: "
                                f"{analysis_results[i]}"
                            )
                        else:
                            print(
                                f"❌ No analysis result for "
                                f"{os.path.basename(audio_file_path)}"
                            )
                        batch_success.append(False)

                # Update results for valid files
                valid_idx = 0
                for i, success in enumerate(results):
                    if success:  # This was a valid file
                        if valid_idx < len(batch_success):
                            results[i] = batch_success[valid_idx]
                        else:
                            results[i] = False
                        valid_idx += 1

                return results

            # For actual processing, analyze each song individually
            print(
                f"🤖 Analyzing {len(uploaded_files)} songs with Gemini "
                f"(concurrent individual calls)..."
            )

            # Create concurrent analysis tasks
            analysis_tasks = []
            for audio_file_path, uploaded_file in uploaded_files:
                task = asyncio.get_running_loop().run_in_executor(
                    None,
                    self.analyze_audio_with_gemini,
                    audio_file_path,
                    uploaded_file,
                )
                analysis_tasks.append(task)

            # Run all analyses concurrently
            analysis_results = await asyncio.gather(
                *analysis_tasks, return_exceptions=True
            )

            # Filter successful analyses
            valid_analyses = []
            valid_file_paths = []
            for i, (audio_file_path, _) in enumerate(uploaded_files):
                if (
                    i < len(analysis_results)
                    and not isinstance(analysis_results[i], Exception)
                    and analysis_results[i]
                ):
                    valid_analyses.append(analysis_results[i])
                    valid_file_paths.append(audio_file_path)
                else:
                    if isinstance(analysis_results[i], Exception):
                        print(
                            f"❌ Analysis failed for "
                            f"{os.path.basename(audio_file_path)}: "
                            f"{analysis_results[i]}"
                        )
                    else:
                        print(
                            f"❌ No analysis result for "
                            f"{os.path.basename(audio_file_path)}"
                        )

            if not valid_analyses:
                print("❌ Failed to analyze any songs")
                return [False] * len(audio_file_paths)

            # Load the VDJ database once for the entire batch
            print("📂 Loading VDJ database for batch processing...")
            root = self.parse_vdj_database()
            if root is None:
                print("❌ Could not parse VDJ database for batch modification")
                return [False] * len(audio_file_paths)

            # Process each song's results and modify the XML tree
            batch_success = []
            songs_processed = 0

            # Process valid analyses
            for audio_file_path, song_analysis in zip(valid_file_paths, valid_analyses):
                success = self._apply_cues_to_batch_database(
                    root, audio_file_path, song_analysis
                )
                batch_success.append(success)
                if success:
                    songs_processed += 1

            # Add failures for songs that couldn't be analyzed
            failed_songs = len(uploaded_files) - len(valid_analyses)
            batch_success.extend([False] * failed_songs)

            # Save the database once after processing all songs
            if songs_processed > 0:
                try:
                    print(
                        f"💾 Saving database with changes for "
                        f"{songs_processed} songs..."
                    )
                    original_stats = self._database_integrity_stats(
                        self.vdj_database_path
                    )
                    xml_str = ET.tostring(root, encoding="unicode")

                    # Ensure CRLF line endings for VDJ compatibility
                    if "\r\n" not in xml_str and "\n" in xml_str:
                        xml_str = xml_str.replace("\n", "\r\n")

                    # Validate XML is well-formed
                    try:
                        ET.fromstring(xml_str)
                    except ET.ParseError as e:
                        raise ValueError(f"Generated XML is malformed: {e}")

                    # Atomic write
                    temp_path = f"{self.vdj_database_path}.tmp"
                    with open(temp_path, "w", encoding="utf-8", newline="") as f:
                        f.write(xml_str)

                    # Verify before replacing
                    try:
                        ET.parse(temp_path)
                        self._validate_database_replacement(temp_path, original_stats)
                        shutil.move(temp_path, self.vdj_database_path)
                        print("✅ Batch database update completed successfully")
                    except Exception as e:
                        if os.path.exists(temp_path):
                            os.remove(temp_path)
                        raise ValueError(f"Generated XML file failed verification: {e}")

                except Exception as e:
                    print(f"❌ Error saving database after batch processing: {e}")
                    # Set all successes to False since database save failed
                    batch_success = [False] * len(batch_success)

            # Update results for valid files
            valid_idx = 0
            for i, success in enumerate(results):
                if success:  # This was a valid file
                    if valid_idx < len(batch_success):
                        results[i] = batch_success[valid_idx]
                    else:
                        results[i] = False
                    valid_idx += 1

            successful_count = sum(batch_success)
            print(
                f"🎯 Async batch complete: {successful_count}/"
                f"{len(uploaded_files)} songs processed successfully"
            )
            return results

        except Exception as e:
            print(f"❌ Error processing async batch: {e}")
            import traceback

            traceback.print_exc()
            return [False] * len(audio_file_paths)

