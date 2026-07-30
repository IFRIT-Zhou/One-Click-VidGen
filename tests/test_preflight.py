import unittest
from unittest.mock import Mock, patch

from backend.app import main


class PreflightProbeTest(unittest.TestCase):
    @patch.object(main.requests, "get")
    def test_language_probe_rejects_invalid_key(self, request_get: Mock) -> None:
        request_get.return_value.status_code = 401
        status, message = main._probe_language_api({
            "GEMINI_API_KEY": "invalid",
            "GEMINI_PROVIDER": "runninghub",
        })
        self.assertEqual(status, "error")
        self.assertIn("401", message)

    @patch.object(main.requests, "get")
    def test_language_probe_treats_network_failure_as_warning(self, request_get: Mock) -> None:
        request_get.side_effect = main.requests.ConnectionError("offline")
        status, message = main._probe_language_api({
            "GEMINI_API_KEY": "configured",
            "GEMINI_PROVIDER": "runninghub",
        })
        self.assertEqual(status, "warning")
        self.assertIn("仍可尝试启动", message)

    def test_image_pool_keeps_valid_account_and_reports_invalid_one(self) -> None:
        def probe(key: str) -> tuple[str, str]:
            return ("valid", "0") if key == "good" else ("invalid", "HTTP 401")

        with patch.object(main, "_probe_one_image_key", side_effect=probe):
            status, message = main._probe_image_api_pool(["good", "bad"])
        self.assertEqual(status, "warning")
        self.assertIn("1/2", message)
        self.assertIn("1 个无效账号", message)


if __name__ == "__main__":
    unittest.main()
