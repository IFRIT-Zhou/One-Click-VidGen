import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from backend.app.qwen_tts import synthesize_to_file


class QwenTtsAdapterTest(unittest.TestCase):
    def test_downloads_audio_and_uses_instruct_model_when_description_exists(self) -> None:
        api_response = Mock()
        api_response.status_code = 200
        api_response.json.return_value = {
            "output": {"audio": {"url": "https://example.invalid/audio.wav"}}
        }
        api_response.raise_for_status.return_value = None
        download_response = Mock()
        download_response.raise_for_status.return_value = None
        fake_audio = b"RIFF" + b"test-audio" * 24
        download_response.iter_content.return_value = [fake_audio]

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "chunk.wav"
            with (
                patch.dict(os.environ, {"DASHSCOPE_API_KEY": "test-key"}, clear=False),
                patch("backend.app.qwen_tts.requests.post", return_value=api_response) as post,
                patch("backend.app.qwen_tts.requests.get", return_value=download_response),
            ):
                synthesize_to_file(
                    text="这是一次测试。",
                    destination=output,
                    instructions="沉稳的中年女性，语速偏慢。",
                )

            self.assertEqual(output.read_bytes(), fake_audio)
            payload = post.call_args.kwargs["json"]
            self.assertEqual(payload["model"], "qwen3-tts-instruct-flash")
            self.assertEqual(payload["input"]["instructions"], "沉稳的中年女性，语速偏慢。")
            self.assertEqual(payload["input"]["voice"], "Elias")
            self.assertNotIn("optimize_instructions", payload["input"])


if __name__ == "__main__":
    unittest.main()
