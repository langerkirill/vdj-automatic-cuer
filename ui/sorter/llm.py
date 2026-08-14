"""Shared Gemini JSON helper for Music Sorter."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional, Type

from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel

from .autocue_path import ensure_autocue_on_path

ensure_autocue_on_path()

from vdj_cuer.common import load_gemini_api_key  # noqa: E402

# 3.5 Flash-Lite is the cheap high-throughput SKU and 503s under load.
# 3.6 Flash answered on this key and has a separate capacity pool.
PREFERRED_SORTER_MODEL = "gemini-3.6-flash"
DEAD_MODELS = {
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
}
MODEL_FALLBACKS = (
    PREFERRED_SORTER_MODEL,
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
    "gemini-2.5-pro",
)
_MISSING_MODEL_TERMS = (
    "not_found",
    "not found",
    "no longer available",
    "is not found",
    "invalid model",
)
_CAPACITY_TERMS = (
    "503",
    "unavailable",
    "high demand",
    "overloaded",
    "resource exhausted",
    "429",
    "too many requests",
    "rate limit",
)


def resolve_sorter_model(explicit: Optional[str] = None) -> str:
    """Pick sorter/recs model: call arg, MUSIC_SORTER_GEMINI_MODEL, GEMINI_MODEL."""
    for candidate in (
        explicit,
        os.getenv("MUSIC_SORTER_GEMINI_MODEL"),
        os.getenv("GEMINI_MODEL"),
        PREFERRED_SORTER_MODEL,
    ):
        name = (candidate or "").strip()
        if not name or name.lower().startswith("grok"):
            continue
        if name.casefold() in DEAD_MODELS:
            continue
        return name
    return PREFERRED_SORTER_MODEL


def models_to_try(primary: Optional[str] = None) -> list[str]:
    """Primary first, then live fallbacks. Skip grok leftovers and retired Flash ids."""
    names: list[str] = []
    for name in (resolve_sorter_model(primary), *MODEL_FALLBACKS):
        cleaned = (name or "").strip()
        if (
            cleaned
            and cleaned not in names
            and not cleaned.lower().startswith("grok")
            and cleaned.casefold() not in DEAD_MODELS
        ):
            names.append(cleaned)
    return names


def is_missing_model_error(error: Exception) -> bool:
    err = str(error).lower()
    return any(term in err for term in _MISSING_MODEL_TERMS)


def is_capacity_error(error: Exception) -> bool:
    err = str(error).lower()
    return any(term in err for term in _CAPACITY_TERMS)


def should_try_next_model(error: Exception) -> bool:
    """True when this model is gone or overloaded — try the next id."""
    return is_missing_model_error(error) or is_capacity_error(error)


DEFAULT_MODEL = resolve_sorter_model()


def load_api_key() -> str:
    ui_root = Path(__file__).resolve().parents[1]
    repo_root = ui_root.parent
    load_dotenv(ui_root / ".env")
    load_dotenv(repo_root / ".env")
    return load_gemini_api_key()


def ask_json(
    prompt: Any,
    schema: Type[BaseModel],
    *,
    model: Optional[str] = None,
    temperature: float = 0.2,
) -> dict[str, Any]:
    """Call Gemini and parse a Pydantic JSON schema. Prompt may include audio parts."""
    client = genai.Client(api_key=load_api_key())
    last_error: Optional[Exception] = None

    contents = prompt
    for mid in models_to_try(model):
        try:
            response = client.models.generate_content(
                model=mid,
                contents=contents,
                config=types.GenerateContentConfig(
                    temperature=temperature,
                    response_mime_type="application/json",
                    response_json_schema=schema.model_json_schema(),
                    http_options=types.HttpOptions(timeout=180_000),
                ),
            )
            raw = getattr(response, "parsed", None)
            if raw is not None:
                if hasattr(raw, "model_dump"):
                    return raw.model_dump()
                if isinstance(raw, dict):
                    return raw
            text = getattr(response, "text", None) or ""
            if not text:
                raise RuntimeError("Empty Gemini response")
            return json.loads(text)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if should_try_next_model(exc):
                continue
            raise
    raise RuntimeError(f"Gemini JSON call failed: {last_error}")
