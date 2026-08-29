"""Drive the shipped Gemini JSON helper — retries, fallback, fatal errors."""

from __future__ import annotations

import unittest
from unittest.mock import Mock

from pydantic import BaseModel

from vdj_cuer.gemini_call import (
    ACTION_DROP_THINKING,
    ACTION_FATAL,
    ACTION_NEXT_MODEL,
    ACTION_RETRY,
    classify_gemini_error,
    generate_json,
    is_capacity_error,
    is_daily_quota_error,
    is_empty_response_error,
    is_missing_model_error,
)


class DummySchema(BaseModel):
    ok: bool


CAPACITY = Exception(
    "503 UNAVAILABLE. This model is currently experiencing high demand."
)
MISSING = Exception("404 NOT_FOUND. This model is no longer available")
DAILY = Exception(
    "429 RESOURCE_EXHAUSTED generate_requests_per_model_per_day limit: 250"
)
BAD_ARG = Exception("400 INVALID_ARGUMENT: bad schema")


class ClassifyTests(unittest.TestCase):
    def test_capacity_retries_then_switches(self) -> None:
        self.assertEqual(
            classify_gemini_error(CAPACITY, attempt=0, use_thinking=False),
            ACTION_RETRY,
        )
        self.assertEqual(
            classify_gemini_error(CAPACITY, attempt=1, use_thinking=False),
            ACTION_NEXT_MODEL,
        )

    def test_quota_and_missing_switch_immediately(self) -> None:
        self.assertTrue(is_daily_quota_error(DAILY))
        self.assertTrue(is_missing_model_error(MISSING))
        self.assertEqual(
            classify_gemini_error(DAILY, attempt=0, use_thinking=False),
            ACTION_NEXT_MODEL,
        )
        self.assertEqual(
            classify_gemini_error(MISSING, attempt=0, use_thinking=False),
            ACTION_NEXT_MODEL,
        )

    def test_400_is_fatal(self) -> None:
        self.assertFalse(is_capacity_error(BAD_ARG))
        self.assertEqual(
            classify_gemini_error(BAD_ARG, attempt=0, use_thinking=False),
            ACTION_FATAL,
        )

    def test_thinking_drop(self) -> None:
        err = Exception("Thinking level is not supported for this model")
        self.assertEqual(
            classify_gemini_error(err, attempt=0, use_thinking=True),
            ACTION_DROP_THINKING,
        )


class GenerateJsonTests(unittest.TestCase):
    def test_retries_503_then_succeeds_same_model(self) -> None:
        ok = Mock()
        ok.parsed = None
        ok.text = '{"ok": true}'
        client = Mock()
        client.models.generate_content.side_effect = [CAPACITY, ok]
        sleeps: list[float] = []

        payload, model = generate_json(
            client,
            "ping",
            DummySchema,
            models=["gemini-3.6-flash"],
            max_retries=3,
            sleep_fn=sleeps.append,
        )

        self.assertEqual(payload, {"ok": True})
        self.assertEqual(model, "gemini-3.6-flash")
        self.assertEqual(len(client.models.generate_content.call_args_list), 2)
        self.assertEqual(sleeps, [3])

    def test_empty_then_switches_model(self) -> None:
        empty = Mock()
        empty.text = ""
        empty.parsed = None
        ok = Mock()
        ok.parsed = None
        ok.text = '{"ok": true}'
        client = Mock()
        client.models.generate_content.side_effect = [empty, empty, ok]

        payload, model = generate_json(
            client,
            "ping",
            DummySchema,
            models=["gemini-3.5-flash-lite", "gemini-3.6-flash"],
            max_retries=3,
            sleep_fn=lambda _n: None,
        )

        self.assertEqual(payload, {"ok": True})
        self.assertEqual(model, "gemini-3.6-flash")
        models = [
            call.kwargs["model"]
            for call in client.models.generate_content.call_args_list
        ]
        self.assertEqual(
            models, ["gemini-3.5-flash-lite", "gemini-3.5-flash-lite", "gemini-3.6-flash"]
        )
        self.assertTrue(is_empty_response_error(ValueError("Empty response from Gemini")))

    def test_daily_quota_switches_without_retry_loop(self) -> None:
        ok = Mock()
        ok.text = '{"ok": true}'
        ok.parsed = None
        client = Mock()
        client.models.generate_content.side_effect = [DAILY, ok]
        payload, model = generate_json(
            client,
            "ping",
            DummySchema,
            models=["gemini-3.1-pro-preview", "gemini-3.6-flash"],
            sleep_fn=lambda _n: None,
        )
        self.assertEqual(payload["ok"], True)
        self.assertEqual(model, "gemini-3.6-flash")
        self.assertEqual(len(client.models.generate_content.call_args_list), 2)

    def test_invalid_argument_does_not_loop(self) -> None:
        client = Mock()
        client.models.generate_content.side_effect = BAD_ARG
        with self.assertRaisesRegex(Exception, "INVALID_ARGUMENT"):
            generate_json(
                client,
                "ping",
                DummySchema,
                models=["gemini-3.6-flash", "gemini-2.5-pro"],
                sleep_fn=lambda _n: None,
            )
        self.assertEqual(client.models.generate_content.call_count, 1)


if __name__ == "__main__":
    unittest.main()
