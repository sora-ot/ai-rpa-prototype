"""Image payloads, screenshot policy, and failures without text fallback."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import _bootstrap
from agent import _screenshots_allowed
from llm import DEFAULT_MODELS, ask_json


OPENAI_RESULT = {"choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}]}
GEMINI_RESULT = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": '{"ok":true}'}]}}]}


class MultimodalTests(unittest.TestCase):
    def test_deepseek_and_gpt_include_png_in_user_content(self):
        for provider in ("deepseek", "gpt"):
            with self.subTest(provider=provider), patch("llm._post", return_value=OPENAI_RESULT) as api:
                self.assertEqual(ask_json(provider, "test-key", "", "system", "page", "png-base64"), {"ok": True})
                payload = api.call_args.args[1]
                self.assertEqual(payload["model"], DEFAULT_MODELS[provider])
                content = payload["messages"][1]["content"]
                self.assertEqual(content[0], {"type": "text", "text": "page"})
                self.assertEqual(content[1], {"type": "image_url", "image_url": {
                    "url": "data:image/png;base64,png-base64", "detail": "low"}})

    def test_gemini_includes_png_inline_data(self):
        with patch("llm._post", return_value=GEMINI_RESULT) as api:
            ask_json("gemini", "test-key", "", "system", "page", "png-base64")
        self.assertEqual(api.call_args.args[1]["contents"][0]["parts"][1],
                         {"inline_data": {"mime_type": "image/png", "data": "png-base64"}})

    def test_text_only_requests_remain_strings_for_chat_api(self):
        for provider in ("deepseek", "gpt"):
            with self.subTest(provider=provider), patch("llm._post", return_value=OPENAI_RESULT) as api:
                ask_json(provider, "test-key", "", "system", "plan")
                self.assertEqual(api.call_args.args[1]["messages"][1]["content"], "plan")

    def test_image_api_failure_is_raised_without_retry_or_text_fallback(self):
        for provider in DEFAULT_MODELS:
            with self.subTest(provider=provider), patch("llm._post", side_effect=RuntimeError("API rejected image")) as api:
                with self.assertRaisesRegex(RuntimeError, "API rejected image"):
                    ask_json(provider, "test-key", "", "system", "page", "png-base64")
                self.assertEqual(api.call_count, 1)

    def test_json_retry_keeps_the_same_screenshot(self):
        invalid = {"choices": [{"message": {"content": "not JSON"}}]}
        with patch("llm._post", side_effect=[invalid, OPENAI_RESULT]) as api:
            ask_json("deepseek", "test-key", "", "system", "page", "png-base64")
        first = api.call_args_list[0].args[1]["messages"][1]["content"]
        second = api.call_args_list[1].args[1]["messages"][1]["content"]
        self.assertEqual(first[1], second[1])
        self.assertNotEqual(first[0]["text"], second[0]["text"])

    def test_screenshot_policy_covers_public_and_authenticated_pages(self):
        self.assertTrue(_screenshots_allowed({}))
        self.assertTrue(_screenshots_allowed({}, SimpleNamespace(used=False)))
        self.assertFalse(_screenshots_allowed({"requires_login": True}))
        self.assertFalse(_screenshots_allowed({}, SimpleNamespace(used=True)))


if __name__ == "__main__":
    unittest.main()
