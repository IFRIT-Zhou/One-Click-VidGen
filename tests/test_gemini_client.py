import unittest
from unittest.mock import Mock, patch

from backend.app import gemini_client


class GeminiClientTest(unittest.TestCase):
    def test_timeout_uses_short_connect_and_long_read_windows(self) -> None:
        with patch.dict(
            gemini_client.os.environ,
            {"GEMINI_CONNECT_TIMEOUT_SECONDS": "7", "GEMINI_READ_TIMEOUT_SECONDS": "90"},
        ):
            self.assertEqual(gemini_client._gemini_timeout(), (7.0, 90.0))

    def test_deepseek_uses_shared_relay_key_and_selected_family_model(self) -> None:
        response = Mock()
        response.ok = True
        response.json.return_value = {
            "choices": [{"message": {"content": "[]"}, "finish_reason": "stop"}],
        }
        with (
            patch.object(gemini_client.requests, "post", return_value=response) as request_post,
            patch.dict("os.environ", {
                "LANGUAGE_PROVIDER": "deepseek",
                "GEMINI_API_KEY": "shared-relay-key",
                "GEMINI_API_BASE": "https://llm.runninghub.ai/v1",
                "DEEPSEEK_MODEL": "deepseek/deepseek-v4-pro",
            }, clear=False),
        ):
            text = gemini_client.generate_gemini_text(
                system_prompt="system",
                user_prompt="user",
                response_mime_type="application/json",
            )
        self.assertEqual(text, "[]")
        self.assertEqual(request_post.call_args.args[0], "https://llm.runninghub.ai/v1/chat/completions")
        self.assertEqual(request_post.call_args.kwargs["headers"]["Authorization"], "Bearer shared-relay-key")
        self.assertEqual(request_post.call_args.kwargs["json"]["model"], "deepseek/deepseek-v4-pro")

    def test_openai_compatible_length_finish_is_reported_as_truncation(self) -> None:
        response = Mock()
        response.ok = True
        response.json.return_value = {
            "choices": [{"message": {"role": "assistant"}, "finish_reason": "length"}],
            "usage": {"prompt_tokens": 15601, "completion_tokens": 1479},
        }
        with (
            patch.object(gemini_client.requests, "post", return_value=response),
            patch.dict("os.environ", {"GEMINI_MODEL": "test-model"}, clear=False),
        ):
            with self.assertRaises(gemini_client.GeminiOutputTruncated) as raised:
                gemini_client._generate_openai_compatible_text(
                    api_key="test-key",
                    system_prompt="system",
                    user_prompt="user",
                    temperature=0.2,
                    response_mime_type="application/json",
                    max_output_tokens=8192,
                )
        self.assertIn("max_tokens=8192", str(raised.exception))

    def test_anthropic_family_uses_same_openai_compatible_relay(self) -> None:
        response = Mock()
        response.ok = True
        response.json.return_value = {
            "choices": [{"message": {"content": "[]"}, "finish_reason": "stop"}],
        }
        with (
            patch.object(gemini_client.requests, "post", return_value=response) as request_post,
            patch.dict("os.environ", {
                "LANGUAGE_PROVIDER": "anthropic",
                "GEMINI_API_KEY": "shared-relay-key",
                "GEMINI_API_BASE": "https://llm.runninghub.ai/v1",
                "ANTHROPIC_MODEL": "anthropic/claude-sonnet-5",
            }, clear=False),
        ):
            text = gemini_client.generate_gemini_text(system_prompt="system", user_prompt="user")
        self.assertEqual(text, "[]")
        self.assertEqual(request_post.call_args.args[0], "https://llm.runninghub.ai/v1/chat/completions")
        self.assertEqual(request_post.call_args.kwargs["headers"]["Authorization"], "Bearer shared-relay-key")
        self.assertEqual(request_post.call_args.kwargs["json"]["model"], "anthropic/claude-sonnet-5")

    def test_provider_status_exposes_model_choices(self) -> None:
        with patch.dict("os.environ", {
            "LANGUAGE_PROVIDER": "qwen",
            "GEMINI_API_KEY": "shared-relay-key",
            "QWEN_MODEL": "qwen/qwen3.8-max",
        }, clear=False):
            status = gemini_client.language_provider_status()
        self.assertEqual(status["provider"], "qwen")
        self.assertEqual(status["model"], "qwen/qwen3.8-max")
        qwen = next(item for item in status["providers"] if item["value"] == "qwen")
        self.assertTrue(qwen["configured"])
        self.assertIn("qwen/qwen3.8-max", {item["value"] for item in qwen["models"]})
        self.assertNotIn("runninghub", {item["value"] for item in status["providers"]})

    def test_visible_model_families_share_one_relay_credential(self) -> None:
        for config in gemini_client.LANGUAGE_PROVIDER_OPTIONS.values():
            if config.get("hidden") or config.get("disabled") or config.get("optional_key"):
                continue
            self.assertEqual(config["key_env"], "GEMINI_API_KEY")
            self.assertEqual(config["base_env"], "GEMINI_API_BASE")

    def test_legacy_runninghub_selection_is_normalized_to_gemini(self) -> None:
        with patch.dict("os.environ", {"LANGUAGE_PROVIDER": "runninghub"}, clear=False):
            self.assertEqual(gemini_client._provider(), "gemini")

    def test_custom_local_provider_allows_an_endpoint_without_api_key(self) -> None:
        response = Mock()
        response.ok = True
        response.json.return_value = {
            "choices": [{"message": {"content": "[]"}, "finish_reason": "stop"}],
        }
        with (
            patch.object(gemini_client.requests, "post", return_value=response) as request_post,
            patch.dict("os.environ", {
                "LANGUAGE_PROVIDER": "custom",
                "CUSTOM_LLM_API_BASE": "http://127.0.0.1:1234/v1",
                "CUSTOM_LLM_MODEL": "local-test-model",
                "CUSTOM_LLM_API_KEY": "",
            }, clear=False),
        ):
            text = gemini_client.generate_gemini_text(system_prompt="system", user_prompt="user")
        self.assertEqual(text, "[]")
        self.assertNotIn("Authorization", request_post.call_args.kwargs["headers"])
        self.assertEqual(request_post.call_args.kwargs["json"]["model"], "local-test-model")


if __name__ == "__main__":
    unittest.main()
