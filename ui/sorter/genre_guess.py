"""Gemini genre guess when VDJ tag + folder path do not make the family clear."""

from __future__ import annotations

import json
import os
import re
import threading
import time
import unicodedata
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from .config import DJ_NOTES_ROOT
from .musical_key import (
    _UNCLEAR_GENRE_TAGS,
    genre_family,
    path_genre_is_clear,
)

CACHE_PATH = DJ_NOTES_ROOT / "genre_guesses.json"
DEFAULT_MODEL = os.getenv("MUSIC_SORTER_GEMINI_MODEL") or os.getenv(
    "GEMINI_MODEL", "gemini-3.5-flash"
)
MODEL_FALLBACKS = [
    DEFAULT_MODEL,
    "gemini-3.5-flash",
    "gemini-flash-latest",
    "gemini-3-flash-preview",
]
VALID_FAMILIES = {
    "rnb_soul_zouk",
    "hiphop",
    "house_dance",
    "psy_tribal_world",
    "rock_indie",
    "other",
}

_cache_lock = threading.Lock()
_mem_cache: dict[str, dict[str, Any]] | None = None
_mem_cache_path: str | None = None


class GenreGuessSchema(BaseModel):
    genre: str = Field(
        description="Specific mixable genre, e.g. alternative R&B, organic tribal, deep house"
    )
    family: str = Field(
        description="One of: rnb_soul_zouk, hiphop, house_dance, psy_tribal_world, rock_indie, other"
    )
    confidence: float = Field(ge=0.0, le=1.0, default=0.7)
    reason: str = Field(default="", description="One short clause")


def _load_api_key() -> str:
    ui_root = Path(__file__).resolve().parents[1]
    repo_root = Path(__file__).resolve().parents[2]
    load_dotenv(ui_root / ".env")
    load_dotenv(repo_root / ".env")
    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or ""
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    return key


def _fold_label(raw: str) -> str:
    nfkd = unicodedata.normalize("NFKD", raw or "")
    folded = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", folded).strip().lower()


def guess_cache_key(
    *,
    path: str = "",
    artist: str = "",
    title: str = "",
    name: str = "",
) -> str:
    artist_f = _fold_label(artist)
    title_f = _fold_label(title)
    if artist_f and title_f:
        return f"label:{artist_f} {title_f}"
    base = _fold_label(name or Path(path).name or "")
    base = re.sub(r"^\d+\.\s*", "", base)
    base = re.sub(r"\.(m4a|mp3|flac|wav|aiff?)$", "", base, flags=re.I)
    if base:
        return f"name:{re.sub(r'[^a-z0-9]+', ' ', base).strip()}"
    if path:
        return f"path:{path.lower()}"
    return ""


def normalize_family(raw: str | None) -> str:
    """Map Gemini family/genre text onto a known family id."""
    text = (raw or "").strip().lower()
    if text in VALID_FAMILIES:
        return text
    fam = genre_family(text, text)
    return fam or "other"


def _read_cache() -> dict[str, dict[str, Any]]:
    global _mem_cache, _mem_cache_path
    with _cache_lock:
        current = str(CACHE_PATH)
        if _mem_cache is not None and _mem_cache_path == current:
            return _mem_cache
        data: dict[str, dict[str, Any]] = {}
        if CACHE_PATH.is_file():
            try:
                raw = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    data = {str(k): v for k, v in raw.items() if isinstance(v, dict)}
            except (OSError, json.JSONDecodeError):
                data = {}
        _mem_cache = data
        _mem_cache_path = current
        return data


def _write_cache(data: dict[str, dict[str, Any]]) -> None:
    global _mem_cache, _mem_cache_path
    with _cache_lock:
        _mem_cache = data
        _mem_cache_path = str(CACHE_PATH)
        try:
            CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = CACHE_PATH.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(CACHE_PATH)
        except OSError:
            pass


def _ask_gemini(
    *,
    artist: str,
    title: str,
    name: str,
    path: str,
    genre: str,
    vibe: str,
) -> dict[str, Any]:
    api_key = _load_api_key()
    client = genai.Client(api_key=api_key)
    prompt = f"""You are helping a DJ classify one track for mix continuity.

Guess the genre so we do not mix contemporary R&B / soul / urban kiz with
tribal / psy / organic world (or house vs rock) just because BPM and key match.

Use artist + title + filename. Folder/tag hints may be inbox staging
(Add Cues, Ready for Sort, AC Low Quality, Cues Sorted/Energy) and then
MUST be ignored.

ARTIST: {artist or "—"}
TITLE: {title or "—"}
FILENAME: {name or Path(path).name or "—"}
EXISTING TAG: {genre or "—"}
FOLDER VIBE: {vibe or "—"}

Return:
- genre: a specific mixable label (e.g. alternative R&B, organic tribal, deep house)
- family: exactly one of rnb_soul_zouk, hiphop, house_dance, psy_tribal_world, rock_indie, other
- confidence: 0-1
- reason: one short clause
"""
    last_err: Optional[Exception] = None
    for model in MODEL_FALLBACKS:
        if not model:
            continue
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.2,
                    response_mime_type="application/json",
                    response_schema=GenreGuessSchema,
                ),
            )
            raw = getattr(response, "parsed", None)
            if raw is None:
                text = getattr(response, "text", None) or ""
                data = json.loads(text)
            elif hasattr(raw, "model_dump"):
                data = raw.model_dump()
            else:
                data = dict(raw)
            genre_label = str(data.get("genre") or "").strip()
            if not genre_label:
                raise RuntimeError("empty genre guess")
            family = normalize_family(str(data.get("family") or "") or genre_label)
            try:
                confidence = float(data.get("confidence") or 0.7)
            except (TypeError, ValueError):
                confidence = 0.7
            return {
                "genre": genre_label,
                "family": family,
                "confidence": max(0.0, min(1.0, confidence)),
                "reason": str(data.get("reason") or "").strip(),
                "model": model,
            }
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            continue
    raise RuntimeError(f"Gemini genre guess failed: {last_err}")


def guess_genre(
    *,
    artist: str = "",
    title: str = "",
    path: str = "",
    name: str = "",
    genre: str = "",
    vibe: str = "",
    allow_network: bool = True,
) -> Optional[dict[str, Any]]:
    """Return a cached or fresh Gemini genre guess, or None on failure."""
    key = guess_cache_key(path=path, artist=artist, title=title, name=name)
    if not key:
        return None
    cache = _read_cache()
    hit = cache.get(key)
    if hit and hit.get("genre"):
        try:
            hit_conf = float(hit.get("confidence") or 0.0)
        except (TypeError, ValueError):
            hit_conf = 0.0
        if hit_conf >= 0.5:
            return {
                "genre": str(hit.get("genre") or ""),
                "family": normalize_family(
                    str(hit.get("family") or hit.get("genre") or "")
                ),
                "confidence": hit_conf,
                "reason": str(hit.get("reason") or ""),
                "cached": True,
            }
    if not allow_network:
        return None
    try:
        fresh = _ask_gemini(
            artist=artist,
            title=title,
            name=name or Path(path).name,
            path=path,
            genre=genre,
            vibe=vibe,
        )
    except Exception:
        return None
    record = {
        "genre": fresh["genre"],
        "family": fresh["family"],
        "confidence": fresh["confidence"],
        "reason": fresh.get("reason") or "",
        "artist": artist,
        "title": title,
        "updated_at": time.time(),
    }
    next_cache = dict(cache)
    next_cache[key] = record
    _write_cache(next_cache)
    return {**fresh, "cached": False}


def resolve_source_genre(
    *,
    genre: str = "",
    vibe: str = "",
    artist: str = "",
    title: str = "",
    path: str = "",
    name: str = "",
    use_gemini: bool = True,
) -> dict[str, Any]:
    """
    Fill genre / family for the playing track.

    Uses VDJ tag or folder when those already imply a family. Otherwise asks
    Gemini (cached by artist+title) so transition ranking has a working genre.
    """
    out: dict[str, Any] = {
        "genre": (genre or "").strip(),
        "vibe": (vibe or "").strip(),
        "genre_source": "",
        "genre_family": "",
        "genre_guess": None,
    }
    if path_genre_is_clear(out["genre"], out["vibe"]):
        if genre_family(out["genre"], None):
            out["genre_source"] = "tag"
        else:
            out["genre_source"] = "path"
        out["genre_family"] = genre_family(out["genre"], out["vibe"])
        return out

    tag_for_gemini = "" if out["genre"].lower() in _UNCLEAR_GENRE_TAGS else out["genre"]
    guess = guess_genre(
        artist=artist,
        title=title,
        path=path,
        name=name,
        genre=tag_for_gemini,
        vibe=out["vibe"],
        allow_network=use_gemini,
    )
    if guess and guess.get("genre"):
        out["genre"] = str(guess["genre"])
        out["genre_source"] = "gemini"
        out["genre_guess"] = guess
        out["genre_family"] = normalize_family(
            str(guess.get("family") or guess["genre"])
        )
        return out

    out["genre_family"] = genre_family(
        out["genre"], out["vibe"], artist=artist, title=title
    )
    return out
