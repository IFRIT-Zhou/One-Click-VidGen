import json
import os
import unittest
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.app import video_model_config as config


class VideoModelConfigTests(unittest.TestCase):
    def setUp(self):
        self.values = {}
        self.profiles = []
        self.patches = [
            patch.dict(os.environ, {}, clear=True),
            patch.object(config, "_parse_env_lines", side_effect=lambda _: dict(self.values)),
            patch.object(config, "list_profiles", side_effect=lambda **_: list(self.profiles)),
            patch.object(config, "require_user", return_value={"id": 1}),
            patch.object(config, "save_project_env_values", side_effect=self.values.update),
        ]
        for item in self.patches:
            item.start()
        self.app = FastAPI()
        self.app.include_router(config.router)
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        for item in reversed(self.patches):
            item.stop()

    def image_profile(self, base="https://www.runninghub.ai"):
        return {"protocol": "async_task", "base_url": base, "api_keys": ["image-secret"],
                "text_endpoint": "/openapi/v2/{model}/text-to-image",
                "query_endpoint": "/openapi/v2/query"}

    def test_missing_and_compatible_credentials_never_auto_use_image_key(self):
        self.assertEqual(config.load_config()["source"], "missing")
        self.profiles.append(self.image_profile())
        runtime = config.load_config()
        self.assertEqual(runtime["source"], "image_compatible")
        self.assertEqual(runtime["api_key"], "")
        result = self.client.get("/api/video-model").json()
        self.assertFalse(result["has_api_key"])
        self.assertNotIn("api_key", result)
        self.assertNotIn("image-secret", json.dumps(result))
        config.save_project_env_values.assert_not_called()

    def test_explicit_compatible_copy_saves_independent_config(self):
        self.profiles.append({**self.image_profile(), "api_keys": ["image-secret", "parallel-secret"]})
        response = self.client.put("/api/video-model", json={
            "base_url": "https://www.runninghub.ai", "use_image_credentials": True, "resolution": "480p",
            "concurrency_mode": "manual", "per_key_concurrency": 2, "total_concurrency": 3})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.values["VIDEO_API_KEY"], "image-secret")
        self.assertEqual(self.values["VIDEO_API_KEYS"], "parallel-secret")
        self.assertEqual(response.json()["source"], "dedicated")
        self.assertEqual(response.json()["key_count"], 2)
        self.assertEqual(response.json()["effective_concurrency"], 3)
        self.assertEqual(config.load_config()["resolution"], "480p")
        self.assertNotIn("image-secret", response.text)
        self.assertNotIn("parallel-secret", response.text)

    def test_dedicated_config_wins_and_empty_key_preserves_same_origin(self):
        self.values.update(VIDEO_API_BASE_URL="https://relay.example.test", VIDEO_API_KEY="own-secret")
        self.profiles.append(self.image_profile())
        self.assertEqual(config.load_config()["api_key"], "own-secret")
        response = self.client.put("/api/video-model", json={
            "base_url": "https://relay.example.test/", "api_key": "", "resolution": "480p"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.values["VIDEO_API_KEY"], "own-secret")
        self.assertNotIn("own-secret", response.text)

    def test_additional_keys_append_and_can_be_deleted_without_echoing_secrets(self):
        self.values.update(VIDEO_API_BASE_URL="https://relay.example.test", VIDEO_API_KEY="first-secret")
        response = self.client.put("/api/video-model", json={
            "base_url": "https://relay.example.test", "api_keys": ["second-secret"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["key_count"], 2)
        self.assertEqual(self.values["VIDEO_API_KEY"], "first-secret")
        self.assertEqual(self.values["VIDEO_API_KEYS"], "second-secret")
        self.assertNotIn("secret", response.text)
        deleted = self.client.delete("/api/video-model/keys/0")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(deleted.json()["key_count"], 1)
        self.assertEqual(self.values["VIDEO_API_KEY"], "second-secret")

    def test_cross_origin_requires_new_key_and_copy_rejects_other_host(self):
        self.values.update(VIDEO_API_BASE_URL="https://relay.example.test", VIDEO_API_KEY="own-secret")
        self.profiles.append(self.image_profile())
        for data in [
            {"base_url": "https://other.example.test", "api_key": ""},
            {"base_url": "https://other.example.test", "use_image_credentials": True},
            {"base_url": "http://www.runninghub.ai", "use_image_credentials": True},
            {"base_url": "https://www.runninghub.cn", "use_image_credentials": True},
        ]:
            with self.subTest(data=data):
                self.assertEqual(self.client.put("/api/video-model", json=data).status_code, 400)
        config.save_project_env_values.assert_not_called()

    def test_known_host_alone_does_not_prove_v2_compatibility(self):
        for override in [{"protocol": "openai"}, {"query_endpoint": "/v1/query"},
                         {"text_endpoint": "https://other.example.test/openapi/v2/submit"},
                         {"base_url": "https://runninghub.ai.evil.test"},
                         {"base_url": "https://www.runninghub.ai/prefix"}]:
            with self.subTest(override=override):
                self.profiles[:] = [{**self.image_profile(), **override}]
                self.assertEqual(config.load_config()["source"], "missing")

    def test_legacy_image_settings_can_be_explicitly_reused(self):
        self.values.update(IMAGE_API_BASE_URL="https://www.runninghub.cn", IMAGE_MODEL_ID="image-2",
                           RUNNINGHUB_API_KEY="legacy-secret")
        self.assertEqual(config.load_config()["source"], "image_compatible")
        self.assertEqual(config.load_config()["api_key"], "")
        response = self.client.put("/api/video-model", json={
            "base_url": "https://www.runninghub.cn", "use_image_credentials": True})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.values["VIDEO_API_KEY"], "legacy-secret")

    def test_invalid_bases_and_foreign_paths_are_rejected_without_saving(self):
        for base in ["ftp://example.test", "https://user:pass@example.test", "https://example.test?q=1",
                     "https://example.test#fragment", "https://example.test\\evil", "https://example.test\n/x"]:
            with self.subTest(base=base):
                response = self.client.put("/api/video-model", json={"base_url": base, "api_key": "new-secret"})
                self.assertEqual(response.status_code, 400)
        for path in ["https://evil.test/submit", "//evil.test/submit", "/%2fexample.test", "/foo/../bar",
                     "/submit?key=x", "/submit#x", "/submit%0aheader", "/submit\\evil"]:
            with self.subTest(path=path):
                response = self.client.put("/api/video-model", json={
                    "base_url": "https://example.test", "api_key": "new-secret", "submit_path": path})
                self.assertEqual(response.status_code, 400)
        config.save_project_env_values.assert_not_called()

    def test_newline_key_invalid_resolution_and_missing_key_rejected(self):
        for extra, status in [({"api_key": "key\nheader"}, 400), ({"api_key": "x", "resolution": "4k"}, 422),
                              ({"api_key": ""}, 400)]:
            response = self.client.put("/api/video-model", json={"base_url": "https://example.test", **extra})
            self.assertEqual(response.status_code, status, response.text)
        config.save_project_env_values.assert_not_called()

    def test_manually_injected_key_without_host_cannot_execute(self):
        self.values["VIDEO_API_KEY"] = "unbound-secret"
        self.assertEqual(config.load_config()["api_key"], "")
        self.assertEqual(config.load_config()["source"], "missing")

    def test_api_requires_login(self):
        with patch.object(config, "require_user", side_effect=HTTPException(status_code=401, detail="请先登录")):
            self.assertEqual(self.client.get("/api/video-model").status_code, 401)
            self.assertEqual(self.client.put("/api/video-model", json={
                "base_url": "https://example.test", "api_key": "new-secret"}).status_code, 401)
        config.save_project_env_values.assert_not_called()


if __name__ == "__main__":
    unittest.main()
