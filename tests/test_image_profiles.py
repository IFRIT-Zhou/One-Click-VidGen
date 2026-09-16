import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app import image_profiles


class ImageProfilesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = self.root / ".env"
        self.profiles = self.root / "image_profiles.json"
        self.env.write_text(
            "IMAGE_API_BASE_URL=https://images.example.test\n"
            "IMAGE_MODEL_ID=image-v2\n"
            "RUNNINGHUB_API_KEY=secret-1234\n",
            encoding="utf-8",
        )
        self.patch_env = patch.object(image_profiles, "ENV_PATH", self.env)
        self.patch_profiles = patch.object(image_profiles, "PROFILE_PATH", self.profiles)
        self.patch_env.start()
        self.patch_profiles.start()

    def tearDown(self):
        self.patch_profiles.stop()
        self.patch_env.stop()
        self.temp.cleanup()

    def test_legacy_configuration_is_exposed_without_branding_or_secret(self):
        profiles = image_profiles.list_profiles()
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0]["name"], "已有图像接口")
        self.assertTrue(profiles[0]["configured"])
        self.assertNotIn("api_keys", profiles[0])
        self.assertEqual(profiles[0]["key_hints"], ["••••1234"])

    def test_profile_snapshot_rejects_unsupported_resolution(self):
        self.profiles.write_text(json.dumps({"profiles": [{
            "id": "model-a", "name": "模型 A", "protocol": "async_task",
            "base_url": "https://relay.example.test", "model_id": "image-2.5",
            "text_endpoint": "/{model}/text-to-image",
            "reference_endpoint": "/{model}/image-to-image",
            "query_endpoint": "/openapi/v2/query", "resolutions": ["2k"],
            "reference_images": True,
        }]}), encoding="utf-8")
        self.env.write_text("OCV_IMAGE_PROFILE_KEYS_MODEL_A=key-a\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "不支持 4K"):
            image_profiles.profile_snapshot("model-a", "4k")

    def test_task_environment_uses_snapshotted_model_and_paths(self):
        self.profiles.write_text(json.dumps({"profiles": [{
            "id": "model-a", "name": "模型 A", "protocol": "async_task",
            "base_url": "https://relay.example.test", "model_id": "image-2.5",
            "text_endpoint": "/{model}/text-to-image",
            "reference_endpoint": "/{model}/image-to-image",
            "query_endpoint": "/tasks/query", "resolutions": ["2k"],
            "reference_images": True,
        }]}), encoding="utf-8")
        self.env.write_text("OCV_IMAGE_PROFILE_KEYS_MODEL_A=key-a\n", encoding="utf-8")
        snapshot = image_profiles.profile_snapshot("model-a", "2k")
        environment = image_profiles.profile_environment(snapshot)
        self.assertEqual(environment["IMAGE_MODEL_ID"], "image-2.5")
        self.assertEqual(environment["IMAGE_RESOLUTION"], "2k")
        self.assertEqual(environment["RUNNINGHUB_ENDPOINT"], "https://relay.example.test/image-2.5/text-to-image")
        self.assertEqual(environment["OCV_IMAGE_QUERY_URL"], "https://relay.example.test/tasks/query")


if __name__ == "__main__":
    unittest.main()
