import unittest
from unittest.mock import Mock, patch

from backend.app import main


class PreflightProbeTest(unittest.TestCase):
    def test_legacy_relay_is_presented_as_the_unified_custom_interface(self) -> None:
        values = {
            "LANGUAGE_PROVIDER": "openai",
            "GEMINI_API_KEY": "legacy-secret",
            "GEMINI_API_BASE": "https://relay.example/v1",
            "OPENAI_MODEL": "vendor/gpt-model",
        }
        with patch.object(main, "_project_config_values", return_value=values):
            status = main._api_key_status()["language"]

        self.assertEqual(status["provider"], "custom")
        self.assertEqual(status["source"], "custom")
        self.assertEqual(status["base_url"], "")
        self.assertEqual(status["model"], "")
        self.assertTrue(status["configured"])
        self.assertEqual({item["source"] for item in status["providers"]}, {"official", "custom"})
        custom_status = next(item for item in status["providers"] if item["value"] == "custom")
        self.assertEqual(custom_status["base_url"], "")
        self.assertEqual(custom_status["selected_model"], "")

    def test_pre_provider_env_relay_is_also_presented_as_custom(self) -> None:
        values = {
            "GEMINI_PROVIDER": "runninghub",
            "GEMINI_API_KEY": "legacy-secret",
            "GEMINI_API_BASE": "https://relay.example/v1",
            "GEMINI_MODEL": "google/gemini-model",
        }
        with patch.object(main, "_project_config_values", return_value=values):
            status = main._api_key_status()["language"]

        self.assertEqual(status["provider"], "custom")
        self.assertEqual(status["source"], "custom")
        self.assertEqual(status["base_url"], "")
        self.assertTrue(status["configured"])

    def test_saving_mapped_legacy_relay_migrates_hidden_key_to_custom_fields(self) -> None:
        payload = main.ApiKeySettingsRequest(
            language_provider="custom",
        )
        existing = {
            "LANGUAGE_PROVIDER": "openai",
            "GEMINI_API_KEY": "legacy-secret",
            "GEMINI_API_BASE": "https://relay.example/v1",
            "OPENAI_MODEL": "vendor/gpt-model",
        }
        with (
            patch.object(main, "require_user"),
            patch.object(main, "_parse_env_lines", return_value=existing),
            patch.object(main, "save_project_env_values") as save_values,
            patch.object(main, "_api_key_status", return_value={}),
        ):
            main.save_api_key_settings(payload, Mock())

        updates = save_values.call_args.args[0]
        self.assertEqual(updates["LANGUAGE_PROVIDER"], "custom")
        self.assertEqual(updates["CUSTOM_LLM_API_KEY"], "legacy-secret")
        self.assertEqual(updates["CUSTOM_LLM_API_BASE"], "https://relay.example/v1")
        self.assertEqual(updates["CUSTOM_LLM_MODEL"], "vendor/gpt-model")

    def test_custom_language_settings_save_base_url_model_and_optional_key(self) -> None:
        payload = main.ApiKeySettingsRequest(
            language_provider="custom",
            language_model="local-model-v2",
            language_api_base_url="http://127.0.0.1:1234/v1/",
            language_api_key="optional-secret",
        )
        with (
            patch.object(main, "require_user"),
            patch.object(main, "_parse_env_lines", return_value={}),
            patch.object(main, "save_project_env_values") as save_values,
            patch.object(main, "_api_key_status", return_value={}),
        ):
            main.save_api_key_settings(payload, Mock())

        updates = save_values.call_args.args[0]
        self.assertEqual(updates["LANGUAGE_PROVIDER"], "custom")
        self.assertEqual(updates["CUSTOM_LLM_API_BASE"], "http://127.0.0.1:1234/v1")
        self.assertEqual(updates["CUSTOM_LLM_MODEL"], "local-model-v2")
        self.assertEqual(updates["CUSTOM_LLM_API_KEY"], "optional-secret")

    def test_non_custom_provider_cannot_override_base_url(self) -> None:
        payload = main.ApiKeySettingsRequest(
            language_provider="openai_official",
            language_model="gpt-5.6-terra",
            language_api_base_url="https://untrusted.example/v1",
        )
        with patch.object(main, "require_user"):
            with self.assertRaises(main.HTTPException) as raised:
                main.save_api_key_settings(payload, Mock())
        self.assertEqual(raised.exception.status_code, 400)

    def test_custom_base_url_rejects_embedded_credentials(self) -> None:
        payload = main.ApiKeySettingsRequest(
            language_provider="custom",
            language_model="local-model",
            language_api_base_url="https://user:password@example.test/v1",
        )
        with patch.object(main, "require_user"):
            with self.assertRaises(main.HTTPException) as raised:
                main.save_api_key_settings(payload, Mock())
        self.assertEqual(raised.exception.status_code, 400)

    def test_api_key_status_reports_automatic_image_capacity(self) -> None:
        values = {
            "RUNNINGHUB_API_KEY": "image-key-1",
            "RUNNINGHUB_API_KEYS": "image-key-2,image-key-3",
            "RUNNINGHUB_BASE_URL": "https://images.example.test",
            "RUNNINGHUB_IMAGE_MODEL": "rhart-image-g-2",
            "RUNNINGHUB_RESOLUTION": "2k",
            "RUNNINGHUB_CONCURRENCY_MODE": "auto",
            "RUNNINGHUB_PER_KEY_CONCURRENCY": "2",
            "RUNNINGHUB_ACTIVE_TASK_CONCURRENCY": "3",
        }
        with patch.object(main, "_project_config_values", return_value=values):
            status = main._api_key_status()
        self.assertEqual(status["image"]["count"], 3)
        self.assertEqual(status["image"]["base_url"], "")
        self.assertEqual(status["image"]["model"], "")
        self.assertEqual(status["image"]["resolution"], "2k")
        self.assertEqual(status["image"]["concurrency"]["effective"], 6)

    def test_fresh_image_settings_do_not_advertise_a_third_party_url(self) -> None:
        with patch.object(main, "_project_config_values", return_value={}):
            status = main._api_key_status()["image"]
        self.assertEqual(status["base_url"], "")
        self.assertEqual(status["model"], "rhart-image-g-2")

    def test_image_settings_save_base_url_model_and_legacy_runtime_aliases(self) -> None:
        payload = main.ApiKeySettingsRequest(
            image_api_base_url="https://images.example.test/api/",
            image_model="vendor/image-2",
            image_resolution="4k",
            image_api_key="image-secret",
        )
        with (
            patch.object(main, "require_user"),
            patch.object(main, "_parse_env_lines", return_value={}),
            patch.object(main, "save_project_env_values") as save_values,
            patch.object(main, "_api_key_status", return_value={}),
        ):
            main.save_api_key_settings(payload, Mock())

        updates = save_values.call_args.args[0]
        self.assertEqual(updates["IMAGE_API_BASE_URL"], "https://images.example.test/api")
        self.assertEqual(updates["RUNNINGHUB_BASE_URL"], "https://images.example.test/api")
        self.assertEqual(updates["IMAGE_MODEL_ID"], "vendor/image-2")
        self.assertEqual(updates["RUNNINGHUB_IMAGE_MODEL"], "vendor/image-2")
        self.assertEqual(updates["RUNNINGHUB_ENDPOINT"], "/vendor/image-2/text-to-image")
        self.assertEqual(updates["IMAGE_RESOLUTION"], "4k")
        self.assertEqual(updates["RUNNINGHUB_RESOLUTION"], "4k")
        self.assertEqual(updates["RUNNINGHUB_API_KEY"], "image-secret")

    def test_new_image_key_requires_an_explicit_base_url(self) -> None:
        payload = main.ApiKeySettingsRequest(
            image_model="rhart-image-g-2",
            image_api_key="image-secret",
        )
        with (
            patch.object(main, "require_user"),
            patch.object(main, "_parse_env_lines", return_value={}),
        ):
            with self.assertRaises(main.HTTPException) as raised:
                main.save_api_key_settings(payload, Mock())
        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("Base URL", raised.exception.detail)

    def test_cluster_health_error_blocks_only_unhealthy_service(self) -> None:
        self.assertIsNone(main._cluster_health_error({"ok": True}))
        message = main._cluster_health_error({"ok": False, "ray_error": "timed out"})
        self.assertIn("timed out", message)
        self.assertIn("不会预扣积分", message)

    def test_cluster_health_error_rejects_unhealthy_nested_dispatcher(self) -> None:
        message = main._cluster_health_error({
            "ok": True,
            "ray": {
                "ok": True,
                "dispatcher": {"ready": False, "consumer_alive": True, "redis_ready": True},
            },
        })
        self.assertIn("Dispatcher 未就绪", message)

    def test_cluster_health_error_accepts_legacy_top_level_health(self) -> None:
        self.assertIsNone(main._cluster_health_error({"ok": True, "ray": {"ok": True}}))

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

    @patch.object(main.requests, "get")
    def test_language_probe_rejects_model_missing_from_runninghub(self, request_get: Mock) -> None:
        request_get.return_value.status_code = 200
        request_get.return_value.json.return_value = {
            "data": [{"id": "google/gemini-2.5-flash"}],
        }
        status, message = main._probe_language_api({
            "LANGUAGE_PROVIDER": "gemini",
            "GEMINI_API_KEY": "configured",
            "GEMINI_MODEL": "google/gemini-3.1-flash-lite-preview",
        })
        self.assertEqual(status, "error")
        self.assertIn("google/gemini-3.1-flash-lite-preview", message)

    @patch.object(main.requests, "post")
    def test_image_probe_uses_global_low_price_endpoint_without_creating_task(
        self, request_post: Mock
    ) -> None:
        request_post.return_value.status_code = 200
        request_post.return_value.json.return_value = {
            "taskId": "",
            "errorCode": "1007",
            "errorMessage": "field 'prompt' is required, can not be empty",
        }
        with patch.dict(main.os.environ, {}, clear=False):
            main.os.environ.pop("RUNNINGHUB_BASE_URL", None)
            status, detail = main._probe_one_image_key("global-key")
        self.assertEqual((status, detail), ("valid", "global"))
        self.assertEqual(
            request_post.call_args.args[0],
            "https://www.runninghub.ai/openapi/v2/rhart-image-g-2/text-to-image",
        )
        self.assertNotIn("prompt", request_post.call_args.kwargs["json"])

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
        self.assertIn("第三方图像 API Key", error)
        self.assertNotIn("RunningHub", error)

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
