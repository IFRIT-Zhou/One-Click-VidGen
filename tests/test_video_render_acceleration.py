import os
import unittest
from pathlib import Path
from unittest.mock import patch

import module5_video_render


class VideoRenderAccelerationTest(unittest.TestCase):
    def build(self) -> list[str]:
        return module5_video_render.build_render_command(
            "node",
            Path("hyperframes-cli.js"),
            Path("index.raw.html"),
            Path("output.mp4"),
        )

    def test_default_uses_auto_workers_gpu_encoding_and_auto_browser_gpu(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            command = self.build()
        self.assertEqual(command[command.index("--workers") + 1], "auto")
        self.assertIn("--gpu", command)
        self.assertNotIn("--browser-gpu", command)
        self.assertNotIn("--no-browser-gpu", command)

    def test_render_acceleration_can_be_overridden_for_compatibility(self) -> None:
        env = {
            "VIDEO_RENDER_WORKERS": "4",
            "VIDEO_RENDER_GPU_ENCODING": "0",
            "VIDEO_RENDER_BROWSER_GPU": "software",
        }
        with patch.dict(os.environ, env, clear=True):
            command = self.build()
        self.assertEqual(command[command.index("--workers") + 1], "4")
        self.assertNotIn("--gpu", command)
        self.assertIn("--no-browser-gpu", command)

    def test_worker_count_is_bounded(self) -> None:
        with patch.dict(os.environ, {"VIDEO_RENDER_WORKERS": "99"}, clear=True):
            self.assertEqual(module5_video_render.render_workers(), "16")


if __name__ == "__main__":
    unittest.main()
