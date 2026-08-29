"""Sorter Gemini model selection and 503/capacity fallbacks."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from pydantic import BaseModel
from sorter.llm import (
    PREFERRED_SORTER_MODEL,
    ask_json,
    is_capacity_error,
    is_missing_model_error,
    models_to_try,
)
from sorter.recommend import FolderRecommender


class DummySchema(BaseModel):
    ok: bool


CAPACITY_503 = Exception(
    "503 UNAVAILABLE. {'error': {'code': 503, 'message': "
    "'This model is currently experiencing high demand. Spikes in demand "
    "are usually temporary. Please try again later.', 'status': 'UNAVAILABLE'}}"
)


class CapacityErrorTests(unittest.TestCase):
    def test_detects_high_demand_503(self):
        self.assertTrue(is_capacity_error(CAPACITY_503))
        self.assertTrue(is_capacity_error(Exception("UNAVAILABLE: model overloaded")))
        self.assertFalse(is_capacity_error(Exception("400 INVALID_ARGUMENT")))

    def test_detects_missing_model(self):
        self.assertTrue(
            is_missing_model_error(
                Exception("404 NOT_FOUND. This model is no longer available")
            )
        )
        self.assertFalse(is_missing_model_error(CAPACITY_503))


class SorterModelTests(unittest.TestCase):
    def test_default_is_not_flash_lite(self):
        self.assertEqual(PREFERRED_SORTER_MODEL, "gemini-3.7-flash")
        self.assertNotIn("lite", PREFERRED_SORTER_MODEL)

    def test_models_to_try_skips_dead_and_grok(self):
        names = models_to_try("gemini-3.5-flash-lite")
        self.assertEqual(names[0], "gemini-3.5-flash-lite")
        self.assertIn("gemini-3.7-flash", names)
        self.assertIn("gemini-2.5-pro", names)
        self.assertNotIn("gemini-2.5-flash", names)
        self.assertNotIn("gemini-2.0-flash", names)
        self.assertFalse(any(n.lower().startswith("grok") for n in names))


class AskJsonFallbackTests(unittest.TestCase):
    def test_falls_back_on_503(self):
        ok = Mock()
        ok.parsed = None
        ok.text = '{"ok": true}'
        client = Mock()
        client.models.generate_content.side_effect = [CAPACITY_503, CAPACITY_503, ok]

        with (
            patch("sorter.llm.genai.Client", return_value=client),
            patch("sorter.llm.load_api_key", return_value="test-key"),
            patch("vdj_cuer.gemini_call.time.sleep"),
        ):
            result = ask_json("ping", DummySchema, model="gemini-3.5-flash-lite")

        self.assertEqual(result, {"ok": True})
        models = [
            call.kwargs["model"]
            for call in client.models.generate_content.call_args_list
        ]
        self.assertEqual(models[0], "gemini-3.5-flash-lite")
        self.assertEqual(models[1], "gemini-3.5-flash-lite")
        self.assertEqual(models[2], "gemini-3.7-flash")


class RecommendFallbackTests(unittest.TestCase):
    def test_falls_back_on_503_to_next_model(self):
        uploaded = Mock()
        uploaded.name = "files/abc"
        uploaded.state = Mock()
        uploaded.state.name = "ACTIVE"

        ok = Mock()
        ok.text = json.dumps(
            {
                "zouk": {
                    "relative_path": "Chill",
                    "confidence": 0.8,
                    "reasoning": "soft and spacious",
                    "alternatives": ["Lounge"],
                },
                "vibe_tags": ["chill"],
            }
        )
        client = Mock()
        client.files.upload.return_value = uploaded
        client.files.get.return_value = uploaded
        client.models.generate_content.side_effect = [CAPACITY_503, CAPACITY_503, ok]

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "track.mp3"
            audio.write_bytes(b"fake-audio")
            rec = FolderRecommender(api_key="test-key", model_name="gemini-3.5-flash-lite")
            rec.client = client
            rec._catalog_cache = {"Zouk": ["Chill", "Lounge"], "House": ["Party"]}
            rec._catalog_mtime = 1.0

            with patch.object(rec, "_track_bpm", return_value=90.0), patch(
                "vdj_cuer.gemini_call.time.sleep"
            ):
                result = rec.recommend(audio)

        self.assertIsNone(result.error)
        self.assertEqual(result.zouk["relative_path"], "Chill")
        self.assertEqual(result.model, "gemini-3.7-flash")
        models = [
            call.kwargs["model"]
            for call in client.models.generate_content.call_args_list
        ]
        self.assertEqual(models[0], "gemini-3.5-flash-lite")
        self.assertEqual(models[1], "gemini-3.5-flash-lite")
        self.assertEqual(models[2], "gemini-3.7-flash")


if __name__ == "__main__":
    unittest.main()
