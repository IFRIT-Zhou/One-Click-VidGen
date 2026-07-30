import unittest

from backend.app.diagnostics import redact_text, sanitize_request


class DiagnosticsPrivacyTest(unittest.TestCase):
    def test_redacts_keys_and_user_paths(self):
        value = "api_key=sk-1234567890abcdefghijkl C:\\Users\\Alice\\secret.txt"
        cleaned = redact_text(value)
        self.assertNotIn("sk-1234567890abcdefghijkl", cleaned)
        self.assertNotIn("C:\\Users\\Alice", cleaned)
        self.assertIn("[REDACTED]", cleaned)
        self.assertIn("<user-home>", cleaned)

    def test_request_omits_user_content_but_keeps_switches(self):
        cleaned = sanitize_request({
            "script": "private story",
            "api_key": "sk-private",
            "tts_engine": "qwen",
            "step_mode": True,
        })
        self.assertEqual(cleaned["script"], "[REDACTED]")
        self.assertEqual(cleaned["api_key"], "[REDACTED]")
        self.assertEqual(cleaned["tts_engine"], "qwen")
        self.assertTrue(cleaned["step_mode"])


if __name__ == "__main__":
    unittest.main()
