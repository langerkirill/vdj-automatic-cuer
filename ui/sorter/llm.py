"""Shared Gemini JSON helper for Music Sorter."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional, Type

from dotenv import load_dotenv
from google import genai
from pydantic import BaseModel

from .autocue_path import ensure_autocue_on_path

ensure_autocue_on_path()

from vdj_cuer.common import load_gemini_api_key  # noqa: E402
from vdj_cuer.gemini_call import (  # noqa: E402
    generate_json,
    is_capacity_error,
    is_missing_model_error,
)

# Pin: gemini-3.7-flash everywhere in the sorter.
PREFERRED_SORTER_MODEL = "gemini-3.7-flash"
DEAD_MODELS = {
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
}
MODEL_FALLBACKS = (
    PREFERRED_SORTER_MODEL,
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
    "gemini-2.5-pro",
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
    payload, _used = generate_json(
        client,
        prompt,
        schema,
        models=models_to_try(model),
        timeout_seconds=180,
        temperature=temperature,
        thinking=False,
    )
    return payload
