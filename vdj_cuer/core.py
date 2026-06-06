"""AutomaticMusicCuer public class assembled from focused mixins."""

from .common import *
from .analysis_postprocess import AnalysisPostprocessMixin
from .async_batch import AsyncBatchMixin
from .batch_analysis import BatchAnalysisMixin
from .batch_runner import BatchRunnerMixin
from .beatgrid_alignment import BeatgridAlignmentMixin
from .beatgrid_sources import BeatgridSourceMixin
from .cue_writer import CueWriterMixin
from .file_processor import FileProcessorMixin
from .file_preview import FilePreviewMixin
from .gemini_analysis import GeminiAnalysisMixin
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
    FilePreviewMixin,
    FileProcessorMixin,
):
    """A class to automatically cue music files for VirtualDJ."""

    @staticmethod
    def sanitize_xml_content(text: str) -> str:
        """Sanitize text content for safe XML inclusion"""
        if not text:
            return ""

        # Remove or replace problematic characters
        # Keep only printable ASCII and common Unicode characters
        sanitized = "".join(
            char for char in text if ord(char) >= 32 or char in "\t\n\r"
        )

        # HTML escape for XML safety
        sanitized = html.escape(sanitized, quote=False)

        # Remove any null bytes or other control characters
        sanitized = (
            sanitized.replace("\x00", "").replace("\x01", "").replace("\x02", "")
        )

        return sanitized.strip()

    def __init__(
        self,
        gemini_api_key: Optional[str] = None,
        vdj_database_path: Optional[str] = None,
        model_name: Optional[str] = None,
    ):
        """Initialize the automatic music cuer with Gemini Pro API"""
        # Load API key from .env file if not provided
        if gemini_api_key is None:
            load_dotenv()
            gemini_api_key = os.getenv("GEMINI_API_KEY")
            if not gemini_api_key:
                raise ValueError("GEMINI_API_KEY not found in environment or .env file")

        self.gemini_api_key = gemini_api_key
        self.model_name = model_name or os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
        self.client = genai.Client(api_key=gemini_api_key)
        self._beatgrid_alignment_cache: Dict[Tuple[str, float], BeatgridAlignment] = {}

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
            result = subprocess.run(
                ["pgrep", "-fl", "VirtualDJ|virtualdj"],
                capture_output=True,
                text=True,
                check=False,
            )
        except Exception:
            return False

        if result.returncode != 0:
            return False

        return any("virtualdj" in line.lower() for line in result.stdout.splitlines())

    @staticmethod
    def _is_retryable_error(error: Exception, terms=RETRYABLE_API_ERROR_TERMS) -> bool:
        """Return True for temporary network/server failures worth retrying."""
        error_text = str(error).lower()
        return any(term in error_text for term in terms)

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
