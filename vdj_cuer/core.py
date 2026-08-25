"""AutomaticMusicCuer public class assembled from focused mixins."""

import gc
import resource

from .common import *
from .analysis_postprocess import AnalysisPostprocessMixin
from .async_batch import AsyncBatchMixin
from .audio_cache import TrackAudioCache
from .batch_analysis import BatchAnalysisMixin
from .batch_runner import BatchRunnerMixin
from .beatgrid_alignment import BeatgridAlignmentMixin
from .beatgrid_sources import BeatgridSourceMixin
from .cue_writer import CueWriterMixin
from .file_processor import FileProcessorMixin
from .file_preview import FilePreviewMixin
from .gemini_analysis import GeminiAnalysisMixin
from .post_cue_audit import PostCueAuditMixin
from .stems import StemMixin
from .vdj_database import VdjDatabaseMixin


class AutomaticMusicCuer(
    StemMixin,
    GeminiAnalysisMixin,
    AnalysisPostprocessMixin,
    VdjDatabaseMixin,
    BeatgridSourceMixin,
    BeatgridAlignmentMixin,
    AsyncBatchMixin,
    BatchRunnerMixin,
    BatchAnalysisMixin,
    CueWriterMixin,
    PostCueAuditMixin,
    FilePreviewMixin,
    FileProcessorMixin,
):
    """A class to automatically cue music files for VirtualDJ."""

    @staticmethod
    def sanitize_marker_name(text: str) -> str:
        """Sanitize cue/loop names for VirtualDJ.

        Never keep ``&`` (or pre-escaped ``&amp;``) in marker names — VDJ shows
        the escaped form literally after double-encoding, and ampersands make
        names awkward (e.g. ``Bass &amp;amp; Snaps In``). Use ``and`` instead.
        Do not HTML-escape here: attribute escaping happens when writing POIs.
        """
        if not text:
            return ""

        sanitized = "".join(
            char for char in text if ord(char) >= 32 or char in "\t\n\r"
        )
        sanitized = (
            sanitized.replace("\x00", "").replace("\x01", "").replace("\x02", "")
        )
        # Fully decode any nested entity chains first (&amp;amp; → &).
        for _ in range(6):
            decoded = html.unescape(sanitized)
            if decoded == sanitized:
                break
            sanitized = decoded
        # Ampersands → "and" (with spacing cleanup).
        sanitized = re.sub(r"\s*&\s*", " and ", sanitized)
        sanitized = re.sub(r"\s+and\s+and\s+", " and ", sanitized, flags=re.I)
        sanitized = re.sub(r"\s+", " ", sanitized)
        # Strip remaining markup-sensitive characters from display names.
        sanitized = (
            sanitized.replace("<", "")
            .replace(">", "")
            .replace('"', "'")
            .replace("'", "'")
        )
        return sanitized.strip()

    @staticmethod
    def sanitize_xml_content(text: str) -> str:
        """Sanitize general text (comments, etc.) for safe XML inclusion.

        Marker names should use :meth:`sanitize_marker_name` instead so they
        are not HTML-escaped before attribute escaping (which double-encodes).
        """
        if not text:
            return ""

        sanitized = "".join(
            char for char in text if ord(char) >= 32 or char in "\t\n\r"
        )
        sanitized = (
            sanitized.replace("\x00", "").replace("\x01", "").replace("\x02", "")
        )
        # Escape once for element text / free-form fields.
        sanitized = html.escape(sanitized, quote=False)
        return sanitized.strip()

    def __init__(
        self,
        gemini_api_key: Optional[str] = None,
        vdj_database_path: Optional[str] = None,
        model_name: Optional[str] = None,
    ):
        """Initialize the automatic music cuer with Gemini Pro API"""
        # Load API key from env / known .env paths (not just process CWD).
        if gemini_api_key is None:
            gemini_api_key = load_gemini_api_key()

        self.gemini_api_key = gemini_api_key
        self.model_name = resolve_gemini_model(model_name)
        self.client = genai.Client(api_key=gemini_api_key)
        self.loop_seam_client = self.client
        self._beatgrid_alignment_cache: Dict[
            Tuple[str, float, bool], BeatgridAlignment
        ] = {}
        # Set when a VDJ stem stream fails to decode (EPIPE / ffmpeg); beatgrid
        # then uses the mix only so AutoCue can still cue the track.
        self._beatgrid_mix_only = False
        self._vdj_metadata_cache = None
        self._vdj_metadata_fingerprint = None
        self._vdj_metadata_lock = threading.Lock()
        self._track_audio_cache = TrackAudioCache()
        # After each successful write: waveform SVG + beatgrid comparison.
        self.post_cue_audit_enabled = True
        self.post_cue_audit_dir = None
        self._post_cue_run_dir = None
        self._post_cue_audit_entries = []
        # Rewrite scope: "all" | "cues" (keep loops) | "loops" (keep cues).
        self.write_scope = WRITE_SCOPE_ALL

        # Default VDJ database path
        if vdj_database_path is None:
            self.vdj_database_path = os.path.expanduser(
                "~/Library/Application Support/VirtualDJ/database.xml"
            )
        else:
            self.vdj_database_path = vdj_database_path

        # Color mappings for VDJ cues
        # (CORRECTED - based on actual VDJ database analysis)
        self.color_mappings = {
            "blue": "4278190335",  # Blue - melodic only (0xff0000ff) - FIXED
            "green": "4278255360",  # Green - melodic+drums (0xff00ff00)
            "purple": "4288020735",  # Purple - drums only (0xff9600ff)
            "yellow": "4294967040",  # Yellow - full mix (0xffffff00)
            "orange": "4294934272",  # Orange - vocal only (0xffff7f00)
        }

        print(f"🎵 Automatic Music Cuer initialized with Gemini model: {self.model_name}")
        print(f"📁 VDJ Database: {self.vdj_database_path}")

    @staticmethod
    def process_rss_mb() -> float:
        """Return current process RSS in megabytes (best-effort)."""
        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # macOS reports bytes; Linux reports kilobytes.
        if sys.platform == "darwin":
            return usage / (1024 * 1024)
        return usage / 1024

    def _release_track_resources(self, audio_file_path: Optional[str] = None) -> None:
        """Drop per-track caches so multi-song runs stay memory-bounded."""
        self._track_audio_cache.clear()
        if audio_file_path:
            stale_keys = [
                key
                for key in self._beatgrid_alignment_cache
                if key[0] == audio_file_path
            ]
            for key in stale_keys:
                self._beatgrid_alignment_cache.pop(key, None)
        else:
            self._beatgrid_alignment_cache.clear()
        # Mix-only is per-track: a broken stem on one song must not disable
        # stems for the rest of a library / batch run.
        self._beatgrid_mix_only = False
        gc.collect()
        print(f"🧹 Track resources released (RSS peak ~{self.process_rss_mb():.0f} MB)")

    def backup_database(self) -> str:
        """Create a timestamped backup of the VDJ database"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = f"{self.vdj_database_path}.backup.{timestamp}"
        shutil.copy2(self.vdj_database_path, backup_path)
        print(f"✅ Database backed up to: {backup_path}")
        return backup_path

    @staticmethod
    def is_virtualdj_running() -> bool:
        """Return True when a VirtualDJ process appears to be active."""
        try:
            from vdj_database_safety import is_virtualdj_running as _shared

            return _shared()
        except Exception:
            try:
                result = subprocess.run(
                    ["pgrep", "-x", "VirtualDJ"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
            except Exception:
                return False
            return result.returncode == 0 and bool(result.stdout.strip())

    @staticmethod
    def _is_retryable_error(error: Exception, terms=RETRYABLE_API_ERROR_TERMS) -> bool:
        """Return True for temporary network/server failures worth retrying."""
        error_text = str(error).lower()
        return any(term in error_text for term in terms)

    @staticmethod
    def _is_daily_quota_error(error: Exception) -> bool:
        """True when this model is out of daily requests — try another Pro."""
        text = str(error).lower()
        return "generate_requests_per_model_per_day" in text or (
            "resource_exhausted" in text and "per_day" in text
        )

    @staticmethod
    def _is_empty_response_error(error: Exception) -> bool:
        return "empty response" in str(error).lower()

    @staticmethod
    def _is_capacity_error(error: Exception) -> bool:
        text = str(error).lower()
        return any(
            term in text
            for term in (
                "503",
                "unavailable",
                "high demand",
                "overloaded",
                "429",
                "too many requests",
                "rate limit",
            )
        )

    def _should_switch_model(self, error: Exception, analysis_retry: int) -> bool:
        """True when this model is done — quota, or empty/503 after one retry."""
        if self._is_daily_quota_error(error):
            return True
        if self._is_empty_response_error(error) or self._is_capacity_error(error):
            return analysis_retry >= 1
        return False

    def _model_candidates(self) -> list[str]:
        names: list[str] = []
        for candidate in (self.model_name, *GEMINI_PRO_FALLBACKS):
            name = (candidate or "").strip()
            if name and name not in names:
                names.append(name)
        return names

    def _upload_audio_file(self, audio_file_path: str):
        """Upload an audio file once using the current Gemini client."""
        upload_path = audio_file_path
        temp_upload_path = None

        try:
            os.path.basename(audio_file_path).encode("ascii")
        except UnicodeEncodeError:
            suffix = os.path.splitext(audio_file_path)[1] or ".audio"
            with tempfile.NamedTemporaryFile(
                prefix="vdj_upload_", suffix=suffix, delete=False
            ) as temp_file:
                temp_upload_path = temp_file.name
            shutil.copy2(audio_file_path, temp_upload_path)
            upload_path = temp_upload_path

        try:
            return self.client.files.upload(file=upload_path)
        finally:
            if temp_upload_path and os.path.exists(temp_upload_path):
                os.remove(temp_upload_path)

    async def _upload_audio_file_async(self, audio_file_path: str):
        """Upload through Gemini's cancellable async client."""
        upload_path = audio_file_path
        temp_upload_path = None

        try:
            os.path.basename(audio_file_path).encode("ascii")
        except UnicodeEncodeError:
            suffix = os.path.splitext(audio_file_path)[1] or ".audio"
            with tempfile.NamedTemporaryFile(
                prefix="vdj_upload_", suffix=suffix, delete=False
            ) as temp_file:
                temp_upload_path = temp_file.name
            shutil.copy2(audio_file_path, temp_upload_path)
            upload_path = temp_upload_path

        try:
            return await self.client.aio.files.upload(file=upload_path)
        finally:
            if temp_upload_path and os.path.exists(temp_upload_path):
                os.remove(temp_upload_path)

    @staticmethod
    def _uploaded_file_name(uploaded_file) -> Optional[str]:
        if uploaded_file is None:
            return None
        if isinstance(uploaded_file, dict):
            return uploaded_file.get("name")
        return getattr(uploaded_file, "name", None)

    def _delete_uploaded_files(self, uploaded_files) -> None:
        """Best-effort cleanup for Gemini files created by synchronous paths."""
        for uploaded_file in uploaded_files:
            name = self._uploaded_file_name(uploaded_file)
            if not name:
                continue
            try:
                self.client.files.delete(name=name)
            except Exception as error:
                print(f"⚠️  Could not delete Gemini file {name}: {error}")

    async def _delete_uploaded_files_async(self, uploaded_files) -> None:
        """Best-effort cleanup for Gemini files created by async paths."""
        for uploaded_file in uploaded_files:
            name = self._uploaded_file_name(uploaded_file)
            if not name:
                continue
            try:
                await self.client.aio.files.delete(name=name)
            except Exception as error:
                print(f"⚠️  Could not delete Gemini file {name}: {error}")

    def _upload_audio_file_with_retry(
        self, audio_file_path: str, max_retries: int = DEFAULT_UPLOAD_RETRIES
    ):
        """Upload a single audio file with retry handling."""
        audio_file = None
        for upload_retry in range(max_retries):
            try:
                audio_file = self._upload_audio_file(audio_file_path)
                print("✅ Upload complete")
                return audio_file
            except Exception as upload_e:
                if self._is_retryable_error(upload_e, NETWORK_ERROR_TERMS) and (
                    upload_retry < max_retries - 1
                ):
                    wait_time = min((upload_retry + 1) * 2, 30)
                    print(
                        f"⚠️  Upload failed (attempt "
                        f"{upload_retry + 1}/{max_retries}): {upload_e}"
                    )
                    print(f"🔄 Retrying upload in {wait_time} seconds...")
                    time.sleep(wait_time)
                    continue
                raise

        return audio_file
