import unittest
from unittest.mock import patch

from backend.app import comfyui_bridge as bridge


class ResourceEstimateTests(unittest.TestCase):
    def setUp(self):
        self.profile = {"resolution_preset": "720p", "mappings": {"fps": 24}}
        self.available = {"vram_free_gb": 20.0, "ram_available_gb": 30.0}

    def test_heavier_workload_increases_estimate_and_warns(self):
        light = bridge._resource_estimate(self.profile, "16:9", "480p", 10, self.available)
        heavy = bridge._resource_estimate(self.profile, "16:9", "1080p", 13, self.available)
        self.assertGreater(heavy["vram_estimate_gb"][0], light["vram_estimate_gb"][0])
        self.assertGreater(heavy["ram_estimate_gb"][0], light["ram_estimate_gb"][0])
        self.assertEqual(heavy["risk"], "high")
        self.assertEqual(heavy["frames"], 312)

    def test_unknown_resources_do_not_claim_safety(self):
        result = bridge._resource_estimate(self.profile, "9:16", "720p", 10,
                                           {"vram_free_gb": None, "ram_available_gb": None})
        self.assertEqual(result["risk"], "unknown")
        self.assertEqual((result["width"], result["height"]), (720, 1280))

    def test_read_failure_is_nonfatal(self):
        with patch.object(bridge.subprocess, "run", side_effect=OSError("missing")):
            self.assertIsNone(bridge._available_resources()["vram_free_gb"])


if __name__ == "__main__":
    unittest.main()
