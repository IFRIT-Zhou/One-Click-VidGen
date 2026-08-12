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

    def test_deepseek_uses_its_own_key_model_and_openai_endpoint(self) -> None:
        response = Mock()
        response.ok = True
        response.json.return_value = {
            "choices": [{"message": {"content": "[]"}, "finish_reason": "stop"}],
        }
        with (
            patch.object(gemini_client.requests, "post", return_value=response) as request_post,
            patch.dict("os.environ", {
                "LANGUAGE_PROVIDER": "deepseek",
                "DEEPSEEK_API_KEY": "deepseek-key",
                "DEEPSEEK_MODEL": "deepseek-test",
            }, clear=False),
        ):
            text = gemini_client.generate_gemini_text(
                system_prompt="system",
                user_prompt="user",
                response_mime_type="application/json",
            )
        self.assertEqual(text, "[]")
        self.assertEqual(request_post.call_args.args[0], "https://api.deepseek.com/chat/completions")
        self.assertEqual(request_post.call_args.kwargs["headers"]["Authorization"], "Bearer deepseek-key")
        self.assertEqual(request_post.call_args.kwargs["json"]["model"], "deepseek-test")

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


if __name__ == "__main__":
    unittest.main()
