"""Gemini folder recommendations from audio + the user's House/Zouk tree.

Always suggests a Zouk folder. House is only suggested when musical BPM is
above HOUSE_BPM_MIN (default 100) — slow/zouk-tempo tracks skip House.
"""

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
from .relocate import summarize_cues

ensure_autocue_on_path()

# Prefer a current Flash model with audio + JSON support.
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

# House crates are for higher-tempo material; at/under this BPM skip House.
HOUSE_BPM_MIN = float(os.getenv("MUSIC_SORTER_HOUSE_BPM_MIN", "100"))


class LibraryFolderPickSchema(BaseModel):
    """One library destination pick."""

    relative_path: str = Field(
        description="Folder path under the library, e.g. Chill/Mystical or Party"
    )
    confidence: float = Field(ge=0.0, le=1.0, description="0-1 confidence")
    reasoning: str = Field(description="Short why this folder fits")
    alternatives: list[str] = Field(
        default_factory=list,
        description="Up to 3 other valid relative_path options from the same library",
    )


class DualFolderRecommendationSchema(BaseModel):
    """House + Zouk picks (House may be omitted by the prompt when BPM is low)."""

    zouk: LibraryFolderPickSchema = Field(description="Best Zouk library folder")
    house: Optional[LibraryFolderPickSchema] = Field(
        default=None,
        description="Best House library folder (omit when track is too slow for House)",
    )
    vibe_tags: list[str] = Field(
        default_factory=list, description="Shared mood/energy tags"
    )


class ZoukOnlyRecommendationSchema(BaseModel):
    zouk: LibraryFolderPickSchema = Field(description="Best Zouk library folder")
    vibe_tags: list[str] = Field(
        default_factory=list, description="Shared mood/energy tags"
    )


@dataclass
class LibraryPick:
    relative_path: str
    confidence: float
    reasoning: str
    alternatives: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RecommendationResult:
    """Dual-library recommendation with backward-compatible primary fields."""

    library: str
    relative_path: str
    confidence: float
    reasoning: str
    vibe_tags: list[str] = field(default_factory=list)
    alternatives: list[str] = field(default_factory=list)
    house: Optional[dict[str, Any]] = None
    zouk: Optional[dict[str, Any]] = None
    bpm: Optional[float] = None
    house_eligible: bool = False
    house_skip_reason: Optional[str] = None
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


def house_eligible_for_bpm(bpm: Optional[float], *, min_bpm: float = HOUSE_BPM_MIN) -> bool:
    """True when we should ask for / return a House folder recommendation."""
    if bpm is None:
        return False
    return float(bpm) > float(min_bpm)


def _pick_from_schema(pick: LibraryFolderPickSchema) -> LibraryPick:
    return LibraryPick(
        relative_path=pick.relative_path.strip().strip("/"),
        confidence=float(pick.confidence),
        reasoning=(pick.reasoning or "").strip(),
        alternatives=[a.strip().strip("/") for a in (pick.alternatives or [])][:3],
    )


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


def _primary_from_picks(
    *,
    house: Optional[LibraryPick],
    zouk: Optional[LibraryPick],
    preferred_library: Optional[str],
) -> tuple[str, LibraryPick]:
    """Choose primary library/path for legacy single-field consumers."""
    pref = (preferred_library or "").strip().lower()
    if pref in {"house", "zouk"} and pref == "house" and house is not None:
        return "House", house
    if pref in {"zouk", "zook"} and zouk is not None:
        return "Zouk", zouk
    if house is not None and zouk is not None:
        if house.confidence >= zouk.confidence:
            return "House", house
        return "Zouk", zouk
    if zouk is not None:
        return "Zouk", zouk
    if house is not None:
        return "House", house
    return "Zouk", LibraryPick(relative_path="", confidence=0.0, reasoning="")


class FolderRecommender:
    """Uploads a track to Gemini and ranks destination folders (Zouk + optional House)."""

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

    def _track_bpm(self, path: Path) -> Optional[float]:
        try:
            cues = summarize_cues(path)
            return cues.bpm
        except Exception:
            return None

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

        bpm = self._track_bpm(path)
        want_house = house_eligible_for_bpm(bpm)
        house_skip_reason: Optional[str] = None
        if not want_house:
            if bpm is None:
                house_skip_reason = "No BPM in VirtualDJ — House recommendation skipped"
            else:
                house_skip_reason = (
                    f"BPM {bpm:.1f} ≤ {HOUSE_BPM_MIN:g} — House recommendation skipped "
                    f"(only above {HOUSE_BPM_MIN:g} BPM)"
                )

        # Cache key includes eligibility so a BPM edit can re-fetch House later.
        cache_key = f"{path}|house={want_house}|v2"
        if not force:
            with self._lock:
                cached = self._cache.get(cache_key)
            if cached is not None and cached.error is None:
                return RecommendationResult(**{**cached.to_dict(), "cached": True})

        catalog = self._get_catalog()
        if want_house:
            catalog_for_prompt = catalog
        else:
            catalog_for_prompt = {"Zouk": catalog.get("Zouk") or []}
        catalog_text = json.dumps(catalog_for_prompt, indent=2)

        pref = (preferred_library or "").strip()
        if pref.lower() in {"both", ""}:
            library_hint = ""
        else:
            library_hint = (
                f"The user is currently browsing the **{pref}** library UI; "
                "still fill every library field the schema requires, but lean "
                "confidence toward that library when both fit.\n"
            )

        if want_house:
            prompt = f"""You are helping a DJ sort a cued track into House and Zouk libraries.

Recommend the best folder in EACH library from the catalog (or a sensible new nested
path only if nothing fits — prefer existing folders).

Musical BPM from VirtualDJ: {bpm:.1f} (House is allowed for this tempo).

{library_hint}
FOLDER CATALOG (library → relative_path list):
{catalog_text}

Listen to the full audio. Infer energy, mood, groove, darkness/lightness, and how a DJ
would file it by *feeling* for:
1) Zouk (often nested: Chill/*, Energy/*, …)
2) House (often flatter emotion folders: Party, Dark, Journey, …)

Rules:
- Always provide both `zouk` and `house` picks
- relative_path uses forward slashes, no leading slash
- For Zouk Chill/* and Energy/*, go as deep as the mood supports
- confidence is 0-1 per library
- alternatives: up to 3 real relative_path values from the SAME library
- vibe_tags: shared mood tags for the track
- Keep each reasoning to 1-2 short sentences

Track filename: {path.name}
"""
            schema = DualFolderRecommendationSchema
        else:
            prompt = f"""You are helping a DJ sort a cued track into their Zouk library.

Recommend ONE best Zouk folder from the catalog (or a sensible new nested path only if
nothing fits — prefer existing folders).

Musical BPM from VirtualDJ: {bpm if bpm is not None else "unknown"}.
Do NOT recommend House — this track is at or under {HOUSE_BPM_MIN:g} BPM (or BPM unknown).

{library_hint}
FOLDER CATALOG (Zouk only):
{catalog_text}

Listen to the full audio. Infer energy, mood, groove, and the best Zouk vibe crate
(e.g. Chill/Mystical, Energy/Bouncy, Lounge, …).

Rules:
- relative_path uses forward slashes, no leading slash
- Go as deep as the mood supports under Chill/Energy when nested crates fit
- confidence is 0-1
- alternatives: up to 3 real relative_path values from Zouk
- vibe_tags: mood tags for the track
- Keep reasoning to 1-2 short sentences

Track filename: {path.name}
"""
            schema = ZoukOnlyRecommendationSchema

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

            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                response_json_schema=schema.model_json_schema(),
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
                    self.model_name = model_name
                    break
                except Exception as model_exc:
                    last_error = model_exc
                    err_text = str(model_exc).lower()
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
            parsed = schema.model_validate(data)

            zouk_pick = _pick_from_schema(parsed.zouk)
            house_pick: Optional[LibraryPick] = None
            if want_house and isinstance(parsed, DualFolderRecommendationSchema):
                if parsed.house is not None:
                    house_pick = _pick_from_schema(parsed.house)

            vibe_tags = list(getattr(parsed, "vibe_tags", None) or [])

            primary_lib, primary = _primary_from_picks(
                house=house_pick,
                zouk=zouk_pick,
                preferred_library=preferred_library,
            )

            result = RecommendationResult(
                library=primary_lib,
                relative_path=primary.relative_path,
                confidence=primary.confidence,
                reasoning=primary.reasoning,
                vibe_tags=vibe_tags,
                alternatives=list(primary.alternatives),
                house=house_pick.to_dict() if house_pick else None,
                zouk=zouk_pick.to_dict(),
                bpm=bpm,
                house_eligible=want_house,
                house_skip_reason=house_skip_reason,
                model=used_model,
                cached=False,
            )
            with self._lock:
                self._cache[cache_key] = result
            return result
        except Exception as exc:
            result = RecommendationResult(
                library="Zouk",
                relative_path="",
                confidence=0.0,
                reasoning="",
                bpm=bpm,
                house_eligible=want_house,
                house_skip_reason=house_skip_reason,
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
