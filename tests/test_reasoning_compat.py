import time
import unittest
from unittest.mock import Mock, patch
from backend.app.gemini_client import _reasoning_post, _conclusion_only, GeminiError

class ReasoningCompatibilityTests(unittest.TestCase):
    def test_explicit_blocks_only(self):
        self.assertEqual(_conclusion_only('<think>private</think> {"ok":true}'), '{"ok":true}')
        self.assertEqual(_conclusion_only('正文提到思考'), '正文提到思考')
        with self.assertRaises(GeminiError): _conclusion_only('<think>unfinished')

    def test_total_deadline_does_not_retry(self):
        response=Mock()
        def slow(*args,**kwargs):
            time.sleep(.08)
            return response
        with patch('backend.app.gemini_client.requests.post',side_effect=slow) as post:
            started=time.monotonic()
            with self.assertRaisesRegex(GeminiError,'未自动重试'):
                _reasoning_post('https://example.invalid',seconds=.02)
            self.assertLess(time.monotonic()-started,.07)
            time.sleep(.1)
            post.assert_called_once()
            response.close.assert_called_once()

    def test_response_keeps_content_separate(self):
        response=Mock()
        with patch('backend.app.gemini_client.requests.post',return_value=response):
            self.assertIs(_reasoning_post('https://example.invalid',seconds=1),response)
