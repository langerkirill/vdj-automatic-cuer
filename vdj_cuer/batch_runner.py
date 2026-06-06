"""BatchRunnerMixin for AutomaticMusicCuer."""

from .common import *


class BatchRunnerMixin:
    def process_audio_batch(
        self, audio_file_paths: List[str], dry_run: bool = False
    ) -> List[bool]:
        """Process multiple audio files in a single API call for efficiency"""
        print(f"\n🎶 Processing batch of {len(audio_file_paths)} songs:")
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

        if dry_run:
            # For dry run, just analyze and show what would be done
            try:
                print(f"📤 Uploading {len(valid_files)} audio files for dry run...")
                uploaded_files = []
                total_size = 0

                for audio_file_path in valid_files:
                    file_size = os.path.getsize(audio_file_path) / (1024 * 1024)  # MB
                    total_size += file_size
                    print(
                        f"📤 Uploading {os.path.basename(audio_file_path)} "
                        f"({file_size:.1f} MB)..."
                    )

                    uploaded_file = self._upload_audio_file_with_retry(audio_file_path)
                    uploaded_files.append((audio_file_path, uploaded_file))

                print(f"✅ Upload complete ({total_size:.1f} MB total)")

                # Analyze all files in one API call
                print(f"🤖 Analyzing batch of {len(valid_files)} songs with Gemini...")
                analysis_results = self._analyze_audio_batch(uploaded_files)

                if not analysis_results:
                    print("❌ Failed to analyze audio batch")
                    return [False] * len(audio_file_paths)

                # Process each song's results (dry run)
                batch_success = []
                for i, (audio_file_path, _) in enumerate(uploaded_files):
                    if i < len(analysis_results):
                        song_analysis = analysis_results[i]
                        success = self._apply_cues_to_database(
                            audio_file_path, song_analysis, dry_run=True
                        )
                        batch_success.append(success)
                    else:
                        batch_success.append(False)

                # Update results for valid files
                valid_idx = 0
                for i, success in enumerate(results):
                    if success:  # This was a valid file
                        results[i] = batch_success[valid_idx]
                        valid_idx += 1

                return results

            except Exception as e:
                print(f"❌ Error processing batch (dry run): {e}")
                return [False] * len(audio_file_paths)

        # For actual processing, we need to modify the database
        try:
            print(f"📤 Uploading {len(valid_files)} audio files...")
            uploaded_files = []
            total_size = 0

            for audio_file_path in valid_files:
                file_size = os.path.getsize(audio_file_path) / (1024 * 1024)  # MB
                total_size += file_size
                print(
                    f"📤 Uploading {os.path.basename(audio_file_path)} "
                    f"({file_size:.1f} MB)..."
                )

                uploaded_file = self._upload_audio_file_with_retry(audio_file_path)
                uploaded_files.append((audio_file_path, uploaded_file))

            print(f"✅ Upload complete ({total_size:.1f} MB total)")

            # Analyze all files in one API call
            print(f"🤖 Analyzing batch of {len(valid_files)} songs with Gemini...")
            analysis_results = self._analyze_audio_batch(uploaded_files)

            if not analysis_results:
                print("❌ Failed to analyze audio batch")
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

            for i, (audio_file_path, _) in enumerate(uploaded_files):
                if i < len(analysis_results):
                    song_analysis = analysis_results[i]
                    success = self._apply_cues_to_batch_database(
                        root, audio_file_path, song_analysis
                    )
                    batch_success.append(success)
                    if success:
                        songs_processed += 1
                else:
                    batch_success.append(False)

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
                    results[i] = batch_success[valid_idx]
                    valid_idx += 1

            successful_count = sum(batch_success)
            print(
                f"🎯 Batch complete: {successful_count}/"
                f"{len(valid_files)} songs processed successfully"
            )
            return results

        except Exception as e:
            print(f"❌ Error processing batch: {e}")
            import traceback

            traceback.print_exc()
            return [False] * len(audio_file_paths)

