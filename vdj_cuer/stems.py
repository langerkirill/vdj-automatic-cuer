"""StemMixin for AutomaticMusicCuer."""

from .common import *


class StemMixin:
    def _find_vdj_stems_file(audio_file_path: str) -> Optional[str]:
        """Return the adjacent VDJ stems file path when it exists."""
        stems_path = f"{audio_file_path}.vdjstems"
        return stems_path if os.path.exists(stems_path) else None

    @staticmethod
    def _probe_vdj_stem_streams(vdj_stems_path: str) -> List[Tuple[str, int]]:
        """Read named audio streams from a VDJ stems Matroska file."""
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_streams",
                "-select_streams",
                "a",
                vdj_stems_path,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        probe_data = json.loads(result.stdout)
        streams = []

        for stream in probe_data.get("streams", []):
            title = stream.get("tags", {}).get("title", "").lower()
            index = stream.get("index")
            if title in VDJ_STEM_NAMES and index is not None:
                streams.append((title, index))

        return streams

    def _extract_vdj_stems(
        self, vdj_stems_path: str, output_dir: str
    ) -> List[Tuple[str, str]]:
        """Extract VDJ stem streams into small AAC files for model upload."""
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            print("⚠️  ffmpeg/ffprobe not found; skipping VDJ stem upload")
            return []

        stem_streams = self._probe_vdj_stem_streams(vdj_stems_path)
        extracted_files = []

        for stem_name, stream_index in stem_streams:
            output_path = os.path.join(output_dir, f"{stem_name}.m4a")
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    vdj_stems_path,
                    "-map",
                    f"0:{stream_index}",
                    "-c:a",
                    "copy",
                    output_path,
                ],
                check=True,
            )
            extracted_files.append((stem_name, output_path))

        return extracted_files

    def _prepare_vdj_stems_with_retry(
        self, audio_file_path: str
    ) -> Tuple[List[Tuple[str, object]], List[Tuple[str, str]], Optional[str]]:
        """Extract and upload adjacent VDJ stem files when available."""
        vdj_stems_path = self._find_vdj_stems_file(audio_file_path)
        if not vdj_stems_path:
            return [], [], None

        print(f"🧬 Found VDJ stems: {os.path.basename(vdj_stems_path)}")
        temp_dir = tempfile.mkdtemp(prefix="vdj-stems-")

        try:
            extracted_stems = self._extract_vdj_stems(vdj_stems_path, temp_dir)
            uploaded_stems = []

            for stem_name, stem_path in extracted_stems:
                file_size = os.path.getsize(stem_path) / (1024 * 1024)
                print(f"📤 Uploading {stem_name} stem ({file_size:.1f} MB)...")
                uploaded_stems.append(
                    (stem_name, self._upload_audio_file_with_retry(stem_path))
                )

            if uploaded_stems:
                print(f"✅ Uploaded {len(uploaded_stems)} VDJ stem files")
            return uploaded_stems, extracted_stems, temp_dir
        except Exception as e:
            print(f"⚠️  Could not use VDJ stems for {os.path.basename(audio_file_path)}: {e}")
            shutil.rmtree(temp_dir, ignore_errors=True)
            return [], [], None

    def _upload_vdj_stems_with_retry(self, audio_file_path: str) -> List[Tuple[str, object]]:
        """Upload adjacent VDJ stem files, then clean local temporary files."""
        stem_uploads, _, temp_dir = self._prepare_vdj_stems_with_retry(audio_file_path)
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
        return stem_uploads

    @staticmethod
    def _stem_upload_prompt(stem_uploads: List[Tuple[str, object]]) -> str:
        """Describe uploaded stem files and their order for Gemini."""
        if not stem_uploads:
            return (
                "Only the original full mix is uploaded. Infer elements from the "
                "full mix, then follow the strict label/color rules."
            )

        stem_lines = [
            f"- Uploaded file {index + 2}: isolated {stem_name} stem"
            for index, (stem_name, _) in enumerate(stem_uploads)
        ]
        return "\n".join(
            [
                "Uploaded audio files:",
                "- Uploaded file 1: original full mix",
                *stem_lines,
                "",
                "Use the isolated stems as evidence for element presence. For every",
                "cue and loop, set stem_activity for vocal, hihat, bass, instruments,",
                "and kick to one of: none, low, medium, high.",
            ]
        )

    @staticmethod
    def _volume_to_activity(mean_volume_db: Optional[float]) -> str:
        """Map ffmpeg volumedetect mean volume to a coarse activity level."""
        if mean_volume_db is None:
            return "none"
        if mean_volume_db > -25:
            return "high"
        if mean_volume_db > -38:
            return "medium"
        if mean_volume_db > -50:
            return "low"
        return "none"

    @staticmethod
    def _measure_mean_volume(
        audio_file_path: str, timestamp: float, duration_seconds: float = 4.0
    ) -> Optional[float]:
        """Measure mean volume around a timestamp using ffmpeg volumedetect."""
        start = max(float(timestamp) - (duration_seconds / 2), 0.0)
        result = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostats",
                "-ss",
                f"{start:.3f}",
                "-t",
                f"{duration_seconds:.3f}",
                "-i",
                audio_file_path,
                "-af",
                "volumedetect",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        match = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?) dB", result.stderr)
        return float(match.group(1)) if match else None

    def _measure_stem_activity(
        self, stem_files: List[Tuple[str, str]], timestamp: float
    ) -> Dict:
        """Measure activity for every extracted stem near a timestamp."""
        activity = {}
        for stem_name, stem_path in stem_files:
            mean_volume = self._measure_mean_volume(stem_path, timestamp)
            activity[stem_name] = self._volume_to_activity(mean_volume)
        return activity

    def _apply_measured_stem_activity(
        self, analysis_data: Dict, stem_files: List[Tuple[str, str]]
    ) -> Dict:
        """Replace model-reported stem activity with measured stem activity."""
        if not stem_files:
            return analysis_data

        for cue_data in analysis_data.get("measure_changes", []):
            timestamp = cue_data.get("timestamp")
            if timestamp is not None:
                cue_data["stem_activity"] = self._measure_stem_activity(
                    stem_files, float(timestamp)
                )

        for loop_data in analysis_data.get("loop_segments", []):
            timestamp = loop_data.get("start")
            if timestamp is not None:
                loop_data["stem_activity"] = self._measure_stem_activity(
                    stem_files, float(timestamp)
                )

        return analysis_data
