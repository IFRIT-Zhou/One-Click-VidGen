import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from backend.app import gemini_client, html_generator


class GeminiClientTest(unittest.TestCase):
    def test_explicit_legacy_relay_configuration_remains_compatible(self) -> None:
        response = Mock()
        response.ok = True
        response.json.return_value = {
            "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
        }
        with (
            patch.object(gemini_client.requests, "post", return_value=response) as request_post,
            patch.dict("os.environ", {
                "LANGUAGE_PROVIDER": "gemini",
                "GEMINI_API_KEY": "runninghub-key",
                "GEMINI_API_BASE": "https://llm.runninghub.ai/v1",
            }, clear=True),
        ):
            text = gemini_client.generate_gemini_text(system_prompt="system", user_prompt="user")

        self.assertEqual(text, "OK")
        self.assertEqual(
            request_post.call_args.args[0],
            "https://llm.runninghub.ai/v1/chat/completions",
        )
        self.assertEqual(
            request_post.call_args.kwargs["json"]["model"],
            "google/gemini-3.1-flash-lite-preview",
        )

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

    def test_deepseek_official_uses_independent_key_base_and_model(self) -> None:
        response = Mock()
        response.ok = True
        response.json.return_value = {
            "choices": [{"message": {"content": "[]"}, "finish_reason": "stop"}],
        }
        with (
            patch.object(gemini_client.requests, "post", return_value=response) as request_post,
            patch.dict("os.environ", {
                "LANGUAGE_PROVIDER": "deepseek_official",
                "GEMINI_API_KEY": "relay-key-must-not-be-used",
                "DEEPSEEK_API_KEY": "official-deepseek-key",
                "DEEPSEEK_API_BASE": "https://api.deepseek.com",
                "DEEPSEEK_OFFICIAL_MODEL": "deepseek-v4-pro",
            }, clear=False),
        ):
            text = gemini_client.generate_gemini_text(
                system_prompt="system",
                user_prompt="user",
                response_mime_type="application/json",
            )

        self.assertEqual(text, "[]")
        self.assertEqual(request_post.call_args.args[0], "https://api.deepseek.com/chat/completions")
        self.assertEqual(request_post.call_args.kwargs["headers"]["Authorization"], "Bearer official-deepseek-key")
        self.assertEqual(request_post.call_args.kwargs["json"]["model"], "deepseek-v4-pro")

    def test_gemini_official_uses_native_google_endpoint_and_key(self) -> None:
        response = Mock()
        response.ok = True
        response.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "[]"}]}}],
        }
        with (
            patch.object(gemini_client.requests, "post", return_value=response) as request_post,
            patch.dict("os.environ", {
                "LANGUAGE_PROVIDER": "gemini_official",
                "GEMINI_API_KEY": "relay-key-must-not-be-used",
                "GOOGLE_API_KEY": "official-google-key",
                "GOOGLE_GEMINI_MODEL": "gemini-3.7-flash",
            }, clear=True),
        ):
            text = gemini_client.generate_gemini_text(
                system_prompt="system",
                user_prompt="user",
                response_mime_type="application/json",
            )
        self.assertEqual(text, "[]")
        self.assertEqual(
            request_post.call_args.args[0],
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.7-flash:generateContent",
        )
        self.assertEqual(request_post.call_args.kwargs["headers"]["x-goog-api-key"], "official-google-key")

    def test_anthropic_official_uses_messages_protocol(self) -> None:
        response = Mock()
        response.ok = True
        response.json.return_value = {
            "content": [{"type": "text", "text": "[]"}],
            "stop_reason": "end_turn",
        }
        with (
            patch.object(gemini_client.requests, "post", return_value=response) as request_post,
            patch.dict("os.environ", {
                "LANGUAGE_PROVIDER": "anthropic_official",
                "ANTHROPIC_API_KEY": "official-anthropic-key",
                "ANTHROPIC_OFFICIAL_MODEL": "claude-sonnet-5",
            }, clear=True),
        ):
            text = gemini_client.generate_gemini_text(system_prompt="system", user_prompt="user")
        self.assertEqual(text, "[]")
        self.assertEqual(request_post.call_args.args[0], "https://api.anthropic.com/v1/messages")
        self.assertEqual(request_post.call_args.kwargs["headers"]["x-api-key"], "official-anthropic-key")
        self.assertEqual(request_post.call_args.kwargs["json"]["system"], "system")

    def test_openai_official_does_not_reuse_relay_key(self) -> None:
        response = Mock()
        response.ok = True
        response.json.return_value = {
            "choices": [{"message": {"content": "[]"}, "finish_reason": "stop"}],
        }
        with (
            patch.object(gemini_client.requests, "post", return_value=response) as request_post,
            patch.dict("os.environ", {
                "LANGUAGE_PROVIDER": "openai_official",
                "GEMINI_API_KEY": "relay-key-must-not-be-used",
                "OPENAI_API_KEY": "official-openai-key",
                "OPENAI_OFFICIAL_MODEL": "gpt-5.6-terra",
            }, clear=True),
        ):
            text = gemini_client.generate_gemini_text(system_prompt="system", user_prompt="user")
        self.assertEqual(text, "[]")
        self.assertEqual(request_post.call_args.args[0], "https://api.openai.com/v1/chat/completions")
        self.assertEqual(request_post.call_args.kwargs["headers"]["Authorization"], "Bearer official-openai-key")

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

    def test_openai_array_request_does_not_force_json_object_mode(self) -> None:
        response = Mock()
        response.ok = True
        response.json.return_value = {
            "choices": [{"message": {"content": "[]"}, "finish_reason": "stop"}],
        }
        with (
            patch.object(gemini_client.requests, "post", return_value=response) as request_post,
            patch.dict("os.environ", {
                "LANGUAGE_PROVIDER": "openai",
                "GEMINI_API_KEY": "shared-relay-key",
                "GEMINI_API_BASE": "https://llm.runninghub.ai/v1",
                "OPENAI_MODEL": "openai/gpt-5.6-terra",
            }, clear=False),
        ):
            text = gemini_client.generate_gemini_text(
                system_prompt="strict JSON array",
                user_prompt="[]",
                response_mime_type="application/json",
                json_root="array",
            )
        self.assertEqual(text, "[]")
        payload = request_post.call_args.kwargs["json"]
        self.assertNotIn("response_format", payload)
        self.assertNotIn("extra_body", payload)

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
        relay_providers = {"gemini", "anthropic", "deepseek", "openai", "qwen", "kimi", "glm"}
        for name, config in gemini_client.LANGUAGE_PROVIDER_OPTIONS.items():
            if name not in relay_providers:
                continue
            if config.get("hidden") or config.get("disabled") or config.get("optional_key"):
                continue
            self.assertEqual(config["key_env"], "GEMINI_API_KEY")
            self.assertEqual(config["base_env"], "GEMINI_API_BASE")

    def test_deepseek_official_is_exposed_as_an_independent_provider(self) -> None:
        config = gemini_client.LANGUAGE_PROVIDER_OPTIONS["deepseek_official"]
        self.assertEqual(config["key_env"], "DEEPSEEK_API_KEY")
        self.assertEqual(config["base_env"], "DEEPSEEK_API_BASE")
        self.assertEqual(config["model_env"], "DEEPSEEK_OFFICIAL_MODEL")

    def test_official_providers_have_isolated_credentials_and_fixed_sources(self) -> None:
        official = {
            "gemini_official", "anthropic_official", "deepseek_official",
            "openai_official", "qwen_official", "kimi_official", "glm_official",
        }
        for name in official:
            config = gemini_client.LANGUAGE_PROVIDER_OPTIONS[name]
            self.assertEqual(config["source"], "official")
            self.assertNotEqual(config["key_env"], "GEMINI_API_KEY")
            self.assertNotEqual(config["base_env"], "GEMINI_API_BASE")

    def test_official_endpoint_cannot_be_redirected_by_stale_env(self) -> None:
        with patch.dict("os.environ", {
            "GOOGLE_GEMINI_API_BASE": "https://relay.example/v1",
            "GEMINI_API_BASE": "https://another-relay.example/v1",
        }, clear=False):
            self.assertEqual(
                gemini_client.language_base_url("gemini_official"),
                "https://generativelanguage.googleapis.com/v1beta",
            )
            self.assertEqual(
                gemini_client.language_base_url("gemini"),
                "https://another-relay.example/v1",
            )

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

    def test_html_generation_reuses_runninghub_gemini_settings(self) -> None:
        with TemporaryDirectory() as directory:
            output_dir = Path(directory)
            with (
                patch.object(html_generator, "VISUAL_DIR", output_dir),
                patch.object(html_generator, "load_scenes", return_value=[{
                    "scene_id": "scene_001",
                    "start_time": 0.0,
                    "end_time": 1.0,
                    "text_content": "test",
                }]),
                patch.object(html_generator, "audio_duration_seconds", return_value=1.0),
                patch.object(
                    html_generator,
                    "call_openai_compatible_html",
                    return_value="<html>generated</html>",
                ) as generate,
                patch.dict("os.environ", {
                    "GEMINI_API_KEY": "runninghub-key",
                    "GEMINI_API_BASE": "https://llm.runninghub.ai/v1",
                    "GEMINI_MODEL": "google/gemini-3.1-flash-lite-preview",
                }, clear=True),
            ):
                path, provider = html_generator.generate_visual_html()

        self.assertEqual(provider, "runninghub_gemini")
        self.assertEqual(path, output_dir / "index.html")
        self.assertEqual(generate.call_args.kwargs["api_key"], "runninghub-key")
        self.assertEqual(generate.call_args.kwargs["base_url"], "https://llm.runninghub.ai/v1")
        self.assertEqual(
            generate.call_args.kwargs["model"],
            "google/gemini-3.1-flash-lite-preview",
        )

    def test_fresh_install_defaults_to_official_gemini(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(gemini_client._provider(), "gemini_official")
            self.assertEqual(
                gemini_client.language_base_url("gemini_official"),
                "https://generativelanguage.googleapis.com/v1beta",
            )

    def test_relay_requires_an_explicit_base_url(self) -> None:
        with patch.dict("os.environ", {
            "LANGUAGE_PROVIDER": "gemini",
            "GEMINI_API_KEY": "legacy-key",
            "GEMINI_API_BASE": "",
        }, clear=True):
            self.assertFalse(gemini_client.language_provider_configured())

    def test_html_generation_keeps_explicit_model_overrides(self) -> None:
        with TemporaryDirectory() as directory:
            with (
                patch.object(html_generator, "VISUAL_DIR", Path(directory)),
                patch.object(html_generator, "load_scenes", return_value=[{
                    "scene_id": "scene_001",
                    "start_time": 0.0,
                    "end_time": 1.0,
                    "text_content": "test",
                }]),
                patch.object(html_generator, "audio_duration_seconds", return_value=1.0),
                patch.object(
                    html_generator,
                    "call_openai_compatible_html",
                    return_value="<html>generated</html>",
                ) as generate,
                patch.dict("os.environ", {
                    "GEMINI_API_KEY": "environment-key",
                    "GEMINI_API_BASE": "https://llm.runninghub.ai/v1",
                    "GEMINI_MODEL": "google/gemini-3.1-flash-lite-preview",
                }, clear=True),
            ):
                html_generator.generate_visual_html(
                    api_key="explicit-key",
                    base_url="https://relay.example/v1",
                    model="explicit-model",
                )

        self.assertEqual(generate.call_args.kwargs["api_key"], "explicit-key")
        self.assertEqual(generate.call_args.kwargs["base_url"], "https://relay.example/v1")
        self.assertEqual(generate.call_args.kwargs["model"], "explicit-model")


if __name__ == "__main__":
    unittest.main()
