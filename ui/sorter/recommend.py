"""Gemini folder recommendations from audio + the user's House/Zouk tree."""

from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from .autocue_path import ensure_autocue_on_path
from .config import LIBRARIES
from .library import list_library_tree

ensure_autocue_on_path()

# Prefer a current Flash model with audio + JSON support.
# gemini-2.5-flash is blocked for many new API keys ("no longer available to new users").
DEFAULT_SORTER_MODEL = os.getenv("MUSIC_SORTER_GEMINI_MODEL") or os.getenv(
    "GEMINI_MODEL", "gemini-3.5-flash"
)

# Tried in order when the primary model is missing/blocked for the API key.
MODEL_FALLBACKS = [
    DEFAULT_SORTER_MODEL,
    "gemini-3.5-flash",
    "gemini-3.6-flash",
    "gemini-3-flash-preview",
    "gemini-flash-latest",
    "gemini-3.1-pro-preview",
]


class FolderRecommendationSchema(BaseModel):
    library: str = Field(description="House or Zouk")
    relative_path: str = Field(
        description="Folder path under the library, e.g. Chill/Mystical or Party"
    )
    confidence: float = Field(ge=0.0, le=1.0, description="0-1 confidence")
    reasoning: str = Field(description="Short why this folder fits")
    vibe_tags: list[str] = Field(default_factory=list, description="Mood/energy tags")
    alternatives: list[str] = Field(
        default_factory=list,
        description="Up to 3 other valid relative_path options from the tree",
    )


@dataclass
class RecommendationResult:
    library: str
    relative_path: str
    confidence: float
    reasoning: str
    vibe_tags: list[str] = field(default_factory=list)
    alternatives: list[str] = field(default_factory=list)
    model: str = ""
    cached: bool = False
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _load_api_key() -> str:
    # Prefer UI .env, then AutoCue repo root .env, then process env.
    ui_root = Path(__file__).resolve().parents[1]  # ui/
    repo_root = Path(__file__).resolve().parents[2]  # vdj-automatic-cuer/
    load_dotenv(ui_root / ".env")
    load_dotenv(repo_root / ".env")
    load_dotenv(Path.home() / "Desktop" / "vdj-automatic-cuer" / ".env")
    load_dotenv()
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise RuntimeError(
            "GEMINI_API_KEY not found. Set it in vdj-automatic-cuer/.env "
            "(or ui/.env)."
        )
    return key


def _flatten_folder_paths(nodes: list[dict[str, Any]]) -> list[str]:
    paths: list[str] = []
    for node in nodes:
        paths.append(node["relative_path"])
        paths.extend(_flatten_folder_paths(node.get("children") or []))
    return paths


def build_folder_catalog(max_paths: int = 400) -> dict[str, list[str]]:
    """Compact folder lists for the Gemini prompt."""
    catalog: dict[str, list[str]] = {}
    for name in LIBRARIES:
        try:
            tree = list_library_tree(name, max_depth=4)
            paths = _flatten_folder_paths(tree["folders"])
            if len(paths) > max_paths:
                # Prefer shorter (top-level + one nest) paths when truncating.
                paths = sorted(paths, key=lambda p: (p.count("/"), p))[:max_paths]
            catalog[name] = paths
        except (KeyError, FileNotFoundError, OSError):
            catalog[name] = []
    return catalog


def _guess_mime(path: Path) -> str:
    ext = path.suffix.lower()
    return {
        ".mp3": "audio/mpeg",
        ".flac": "audio/flac",
        ".m4a": "audio/mp4",
        ".wav": "audio/wav",
        ".aiff": "audio/aiff",
        ".aif": "audio/aiff",
        ".ogg": "audio/ogg",
        ".opus": "audio/ogg",
    }.get(ext, "application/octet-stream")


def _parse_json_response(text: str) -> dict[str, Any]:
    cleaned = re.sub(
        r"(\d+\.\d{10,})",
        lambda m: f"{float(m.group(1)):.2f}",
        text,
    )
    cleaned = cleaned.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return json.loads(cleaned)


class FolderRecommender:
    """Uploads a track to Gemini and ranks destination folders."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: Optional[str] = None,
    ):
        self.api_key = api_key or _load_api_key()
        self.model_name = model_name or DEFAULT_SORTER_MODEL
        self.client = genai.Client(api_key=self.api_key)
        self._cache: dict[str, RecommendationResult] = {}
        self._lock = threading.Lock()
        self._catalog_cache: Optional[dict[str, list[str]]] = None
        self._catalog_mtime: float = 0.0

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()
            self._catalog_cache = None

    def _get_catalog(self) -> dict[str, list[str]]:
        # Refresh if library roots changed recently.
        mtimes = []
        for path in LIBRARIES.values():
            try:
                mtimes.append(path.stat().st_mtime)
            except OSError:
                pass
        stamp = max(mtimes) if mtimes else 0.0
        if self._catalog_cache is None or stamp != self._catalog_mtime:
            self._catalog_cache = build_folder_catalog()
            self._catalog_mtime = stamp
        return self._catalog_cache

    def recommend(
        self,
        audio_path: str | Path,
        *,
        force: bool = False,
        preferred_library: Optional[str] = None,
    ) -> RecommendationResult:
        path = Path(audio_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Audio not found: {path}")

        cache_key = str(path)
        if not force:
            with self._lock:
                cached = self._cache.get(cache_key)
            if cached is not None and cached.error is None:
                return RecommendationResult(**{**cached.to_dict(), "cached": True})

        catalog = self._get_catalog()
        catalog_text = json.dumps(catalog, indent=2)
        library_hint = (
            f"The user is currently browsing the **{preferred_library}** library; "
            "prefer that library unless the song clearly belongs elsewhere.\n"
            if preferred_library
            else ""
        )

        prompt = f"""You are helping a DJ sort a cued track into their music library.

The DJ has two libraries with nested emotion/vibe folders (and some artist crates).
You must recommend ONE existing folder path from the catalog below, or a sensible
new nested path only if nothing fits (e.g. "Chill/NewVibe") — prefer existing folders.

{library_hint}
FOLDER CATALOG (library → relative_path list):
{catalog_text}

Listen to the full audio. Infer energy, mood, genre lean (house vs zouk/world/electronic
dance), darkness/lightness, groove, and how a DJ would file it by *feeling*.

Rules:
- relative_path must use forward slashes, no leading slash
- For nested vibe crates like Zouk Chill/* and Zouk Energy/*, go as deep as the mood
  supports (e.g. Chill/Mystical, Energy/Bouncy) rather than stopping at Chill/Energy
- House is mostly flat emotion folders (Party, Dark, Journey, …)
- confidence is 0-1
- alternatives must be real relative_path values from the same library when possible
- Keep reasoning to 1-3 short sentences

Track filename: {path.name}
"""

        uploaded = None
        upload_path = path
        temp_upload_path: Optional[Path] = None
        try:
            # Mirror AutoCue: non-ASCII filenames need a temp ASCII path for upload.
            try:
                path.name.encode("ascii")
            except UnicodeEncodeError:
                import shutil
                import tempfile

                suffix = path.suffix or ".audio"
                fd, tmp = tempfile.mkstemp(prefix="sorter_upload_", suffix=suffix)
                os.close(fd)
                temp_upload_path = Path(tmp)
                shutil.copy2(path, temp_upload_path)
                upload_path = temp_upload_path

            uploaded = self.client.files.upload(file=str(upload_path))
            # Wait until ACTIVE when the API returns a processing state.
            for _ in range(60):
                state = getattr(getattr(uploaded, "state", None), "name", None) or str(
                    getattr(uploaded, "state", "")
                )
                if not state or state in {"ACTIVE", "FileState.ACTIVE", "STATE_ACTIVE"}:
                    break
                if "FAILED" in state.upper():
                    raise RuntimeError(f"Gemini file processing failed: {state}")
                time.sleep(1)
                if getattr(uploaded, "name", None):
                    uploaded = self.client.files.get(name=uploaded.name)

            # Same contents shape as AutoCue: [prompt, uploaded_file]
            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                response_json_schema=FolderRecommendationSchema.model_json_schema(),
                http_options=types.HttpOptions(timeout=180_000),
            )

            models_to_try: list[str] = []
            for name in [self.model_name, *MODEL_FALLBACKS]:
                if name and name not in models_to_try:
                    models_to_try.append(name)

            response = None
            last_error: Optional[Exception] = None
            used_model = self.model_name
            for model_name in models_to_try:
                try:
                    response = self.client.models.generate_content(
                        model=model_name,
                        contents=[prompt, uploaded],
                        config=config,
                    )
                    used_model = model_name
                    self.model_name = model_name  # stick with a working model
                    break
                except Exception as model_exc:
                    last_error = model_exc
                    err_text = str(model_exc).lower()
                    # Only fall through for missing/blocked model IDs.
                    if any(
                        token in err_text
                        for token in (
                            "not_found",
                            "not found",
                            "no longer available",
                            "is not found",
                            "invalid model",
                        )
                    ):
                        continue
                    raise

            if response is None:
                raise last_error or RuntimeError("No Gemini model available")

            if not response or not response.text:
                raise RuntimeError("Empty response from Gemini")

            data = _parse_json_response(response.text)
            parsed = FolderRecommendationSchema.model_validate(data)

            # Normalize library name casing.
            library = parsed.library.strip()
            if library.lower() == "house":
                library = "House"
            elif library.lower() in {"zouk", "zook"}:
                library = "Zouk"

            result = RecommendationResult(
                library=library,
                relative_path=parsed.relative_path.strip().strip("/"),
                confidence=float(parsed.confidence),
                reasoning=parsed.reasoning.strip(),
                vibe_tags=list(parsed.vibe_tags or []),
                alternatives=[a.strip().strip("/") for a in (parsed.alternatives or [])][
                    :3
                ],
                model=used_model,
                cached=False,
            )
            with self._lock:
                self._cache[cache_key] = result
            return result
        except Exception as exc:
            result = RecommendationResult(
                library=preferred_library or "House",
                relative_path="",
                confidence=0.0,
                reasoning="",
                model=self.model_name,
                error=str(exc),
            )
            return result
        finally:
            if temp_upload_path is not None and temp_upload_path.exists():
                try:
                    temp_upload_path.unlink()
                except OSError:
                    pass
            if uploaded is not None and getattr(uploaded, "name", None):
                try:
                    self.client.files.delete(name=uploaded.name)
                except Exception:
                    pass


# Process-wide singleton for the web app.
_recommender: Optional[FolderRecommender] = None
_recommender_lock = threading.Lock()


def get_recommender() -> FolderRecommender:
    global _recommender
    with _recommender_lock:
        if _recommender is None:
            _recommender = FolderRecommender()
        return _recommender
