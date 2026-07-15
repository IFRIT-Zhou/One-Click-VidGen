import unittest
from unittest.mock import Mock, patch

from backend.app import gemini_client


class GeminiClientTest(unittest.TestCase):
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
