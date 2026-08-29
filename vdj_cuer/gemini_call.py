"""Single Gemini JSON generate path: classify, retry, fallback, parse."""

from __future__ import annotations

import json
import re
import time
from typing import Any, Callable, Optional, Sequence, Type

from google.genai import types
from pydantic import BaseModel

NETWORK_ERROR_TERMS = (
    "ssl",
    "connection",
    "network",
    "broken pipe",
    "timeout",
    "reset",
    "errno 32",
)

RETRYABLE_TERMS = NETWORK_ERROR_TERMS + (
    "429",
    "500",
    "502",
    "503",
    "504",
    "internal error",
    "unavailable",
    "resource exhausted",
    "quota",
    "rate limit",
    "too many requests",
    "empty response",
    "high demand",
    "overloaded",
)

CAPACITY_TERMS = (
    "503",
    "unavailable",
    "high demand",
    "overloaded",
    "429",
    "too many requests",
    "rate limit",
)

MISSING_MODEL_TERMS = (
    "not_found",
    "not found",
    "no longer available",
    "is not found",
    "invalid model",
)

DEAD_MODELS = {
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
}

ACTION_RETRY = "retry"
ACTION_NEXT_MODEL = "next_model"
ACTION_FATAL = "fatal"
ACTION_DROP_THINKING = "drop_thinking"


def is_retryable_gemini_error(error: Exception) -> bool:
    text = str(error).lower()
    return any(term in text for term in RETRYABLE_TERMS)


def is_capacity_error(error: Exception) -> bool:
    text = str(error).lower()
    return any(term in text for term in CAPACITY_TERMS)


def is_missing_model_error(error: Exception) -> bool:
    text = str(error).lower()
    return any(term in text for term in MISSING_MODEL_TERMS)


def is_daily_quota_error(error: Exception) -> bool:
    text = str(error).lower()
    return "generate_requests_per_model_per_day" in text or (
        "resource_exhausted" in text and "per_day" in text
    )


def is_empty_response_error(error: Exception) -> bool:
    return "empty response" in str(error).lower()


def is_unsupported_thinking_error(error: Exception) -> bool:
    return "thinking level is not supported" in str(error).lower()


def model_supports_thinking_level(model: str) -> bool:
    name = (model or "").casefold()
    return "gemini-3" in name and "flash" not in name


def classify_gemini_error(
    error: Exception,
    *,
    attempt: int,
    use_thinking: bool,
) -> str:
    """What the caller should do after a failed generate_content."""
    if use_thinking and is_unsupported_thinking_error(error):
        return ACTION_DROP_THINKING
    if is_daily_quota_error(error) or is_missing_model_error(error):
        return ACTION_NEXT_MODEL
    if is_empty_response_error(error) or is_capacity_error(error):
        return ACTION_NEXT_MODEL if attempt >= 1 else ACTION_RETRY
    if is_retryable_gemini_error(error):
        return ACTION_RETRY
    return ACTION_FATAL


def empty_response_detail(response: object) -> str:
    if response is None:
        return ""
    parts: list[str] = []
    feedback = getattr(response, "prompt_feedback", None)
    block = getattr(feedback, "block_reason", None)
    if block:
        parts.append(f"block_reason={block}")
    candidates = getattr(response, "candidates", None)
    first = None
    if isinstance(candidates, (list, tuple)) and candidates:
        first = candidates[0]
    if first is not None:
        finish = getattr(first, "finish_reason", None)
        if finish:
            parts.append(f"finish_reason={finish}")
    return f" ({', '.join(parts)})" if parts else ""


def parse_gemini_json(response: object) -> dict[str, Any]:
    raw = getattr(response, "parsed", None)
    if raw is not None:
        if hasattr(raw, "model_dump"):
            dumped = raw.model_dump()
            if isinstance(dumped, dict):
                return dumped
        if isinstance(raw, dict):
            return raw
    text = getattr(response, "text", None) or ""
    if not text:
        raise ValueError("Empty response from Gemini" + empty_response_detail(response))
    cleaned = re.sub(
        r"(\d+\.\d{10,})",
        lambda match: f"{float(match.group(1)):.2f}",
        text,
    )
    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise ValueError("Gemini JSON was not an object")
    return parsed


def build_json_config(
    schema: Type[BaseModel],
    timeout_seconds: int,
    *,
    thinking: bool,
    temperature: Optional[float] = None,
    use_response_schema: bool = False,
) -> types.GenerateContentConfig:
    kwargs: dict[str, Any] = {
        "response_mime_type": "application/json",
        "http_options": types.HttpOptions(timeout=int(timeout_seconds) * 1000),
    }
    if temperature is not None:
        kwargs["temperature"] = temperature
    if use_response_schema:
        kwargs["response_schema"] = schema
    else:
        kwargs["response_json_schema"] = schema.model_json_schema()
    if thinking:
        kwargs["thinking_config"] = types.ThinkingConfig(thinking_level="high")
    return types.GenerateContentConfig(**kwargs)


def generate_json(
    client: Any,
    contents: Any,
    schema: Type[BaseModel],
    *,
    models: Sequence[str],
    timeout_seconds: int = 180,
    max_retries: int = 3,
    temperature: Optional[float] = None,
    thinking: Optional[bool] = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    use_response_schema: bool = False,
) -> tuple[dict[str, Any], str]:
    """
    Call Gemini generate_content until JSON parses.

    Returns (payload, model_used).
    """
    last_error: Optional[Exception] = None
    names = [name.strip() for name in models if (name or "").strip()]
    if not names:
        raise RuntimeError("No Gemini models to try")

    for model in names:
        use_thinking = (
            model_supports_thinking_level(model) if thinking is None else thinking
        )
        for attempt in range(max(1, int(max_retries))):
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=build_json_config(
                        schema,
                        timeout_seconds,
                        thinking=use_thinking,
                        temperature=temperature,
                        use_response_schema=use_response_schema,
                    ),
                )
                return parse_gemini_json(response), model
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                action = classify_gemini_error(
                    exc, attempt=attempt, use_thinking=use_thinking
                )
                if action == ACTION_DROP_THINKING:
                    use_thinking = False
                    continue
                if action == ACTION_NEXT_MODEL:
                    break
                if action == ACTION_RETRY and attempt < max_retries - 1:
                    sleep_fn(min((attempt + 1) * 3, 30))
                    continue
                if action == ACTION_RETRY:
                    break
                raise

    if last_error is not None:
        raise last_error
    raise RuntimeError("Failed to get Gemini JSON after retries")


async def generate_json_async(
    client: Any,
    contents: Any,
    schema: Type[BaseModel],
    *,
    models: Sequence[str],
    timeout_seconds: int = 180,
    max_retries: int = 3,
    temperature: Optional[float] = None,
    thinking: Optional[bool] = None,
    use_response_schema: bool = False,
) -> tuple[dict[str, Any], str]:
    """Async twin of generate_json (same classify / retry / fallback rules)."""
    import asyncio

    last_error: Optional[Exception] = None
    names = [name.strip() for name in models if (name or "").strip()]
    if not names:
        raise RuntimeError("No Gemini models to try")

    for model in names:
        use_thinking = (
            model_supports_thinking_level(model) if thinking is None else thinking
        )
        for attempt in range(max(1, int(max_retries))):
            try:
                response = await client.aio.models.generate_content(
                    model=model,
                    contents=contents,
                    config=build_json_config(
                        schema,
                        timeout_seconds,
                        thinking=use_thinking,
                        temperature=temperature,
                        use_response_schema=use_response_schema,
                    ),
                )
                return parse_gemini_json(response), model
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                action = classify_gemini_error(
                    exc, attempt=attempt, use_thinking=use_thinking
                )
                if action == ACTION_DROP_THINKING:
                    use_thinking = False
                    continue
                if action == ACTION_NEXT_MODEL:
                    break
                if action == ACTION_RETRY and attempt < max_retries - 1:
                    await asyncio.sleep(min((attempt + 1) * 3, 30))
                    continue
                if action == ACTION_RETRY:
                    break
                raise

    if last_error is not None:
        raise last_error
    raise RuntimeError("Failed to get Gemini JSON after retries")
