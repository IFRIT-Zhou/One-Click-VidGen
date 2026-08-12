import unittest
from unittest.mock import Mock, patch

from backend.app import main


class PreflightProbeTest(unittest.TestCase):
    def test_project_config_values_prefers_runtime_environment(self) -> None:
        with (
            patch.object(main, "_parse_env_lines", return_value={"GEMINI_API_KEY": "file-key"}),
            patch.dict(main.os.environ, {"GEMINI_API_KEY": "runtime-key"}),
        ):
            values = main._project_config_values()
        self.assertEqual(values["GEMINI_API_KEY"], "runtime-key")

    def test_ffmpeg_preflight_accepts_system_binaries(self) -> None:
        with (
            patch.object(main.Path, "is_file", return_value=False),
            patch.object(main.shutil, "which", side_effect=lambda name: f"/usr/bin/{name}"),
        ):
            ready, message = main._ffmpeg_preflight()
        self.assertTrue(ready)
        self.assertIn("/usr/bin/ffmpeg", message)

    def test_ffmpeg_preflight_reports_missing_binary(self) -> None:
        with (
            patch.object(main.Path, "is_file", return_value=False),
            patch.object(
                main.shutil,
                "which",
                side_effect=lambda name: None if name == "ffprobe" else "/usr/bin/ffmpeg",
            ),
        ):
            ready, message = main._ffmpeg_preflight()
        self.assertFalse(ready)
        self.assertIn("FFprobe", message)

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

    def test_full_poster_job_requires_image_key_before_start(self) -> None:
        status = {
            "language": {"configured": True},
            "image": {"configured": False},
        }
        with patch.object(main, "_api_key_status", return_value=status):
            error = main._required_job_config_error({"visual_backend": "poster"})
        self.assertIn("RUNNINGHUB_API_KEY", error)

    def test_module1_job_does_not_require_visual_keys(self) -> None:
        with patch.object(main, "_api_key_status") as api_key_status:
            error = main._required_job_config_error({"module1_only": True, "visual_backend": "poster"})
        self.assertIsNone(error)
        api_key_status.assert_not_called()

    def test_cloud_pool_job_uses_cloud_credentials_instead_of_local_keys(self) -> None:
        with patch.object(main, "_api_key_status") as api_key_status:
            error = main._required_job_config_error({"use_cloud_image_pool": True, "visual_backend": "poster"})
        self.assertIsNone(error)
        api_key_status.assert_not_called()


if __name__ == "__main__":
    unittest.main()
