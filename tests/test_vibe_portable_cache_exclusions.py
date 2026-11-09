import itertools
import unittest
from pathlib import Path

from tools.create_portable_archive import is_excluded


class VibePortableCacheExclusionsTest(unittest.TestCase):
    def test_private_render_and_download_caches_are_always_excluded(self) -> None:
        private_paths = (
            "tools/vibe_remotion/.cache",
            "tools/vibe_remotion/.cache/shotcraft-smoke-private/input.json",
            "tools/vibe_remotion/.cache/shotcraft-smoke-private/render/silent.mp4",
            "tools/vibe_remotion/.cache/bundle-v2-test/bundle.js",
            "tools/vibe_shotcraft_library/.downloads",
            "tools/vibe_shotcraft_library/.downloads/upstream/archive.zip",
            "tools/vibe_shotcraft_library/.downloads/media/preview.mp4",
            "runtime/vibe_ocr",
            "runtime/vibe_ocr/Scripts/python.exe",
            "runtime/vibe_ocr/Lib/site-packages/rapidocr/__init__.py",
        )
        for tts, video, models in itertools.product((False, True), repeat=3):
            for relative in private_paths:
                with self.subTest(path=relative, tts=tts, video=video, models=models):
                    self.assertTrue(is_excluded(
                        Path(relative), without_local_tts=tts,
                        without_local_video=video, without_models=models,
                    ))

    def test_distributable_runtime_catalog_and_licenses_are_retained(self) -> None:
        for relative in (
            "tools/vibe_remotion/render.mjs",
            "tools/vibe_remotion/package-lock.json",
            "tools/vibe_remotion/README.md",
            "tools/vibe_remotion/gallery/manifest.json",
            "tools/vibe_remotion/gallery/spotlight-hero-card.gif",
            "tools/vibe_remotion/gallery/spotlight-hero-card.jpg",
            "tools/vibe_remotion/src/ShotcraftScene.tsx",
            "tools/vibe_remotion/src/lib/ShotcraftMotion.tsx",
            "tools/vibe_remotion/lib/shotcraft-contract.mjs",
            "tools/vibe_remotion/lib/video-shotcraft/LICENSE",
            "tools/vibe_remotion/lib/video-shotcraft/NOTICE.txt",
            "tools/vibe_shotcraft_library/catalog.json",
            "tools/vibe_shotcraft_library/manifest.json",
            "tools/vibe_shotcraft_library/runtime_supported.json",
            "tools/vibe_shotcraft_library/categories/opening.json",
            "tools/vibe_shotcraft_library/LICENSE",
            "tools/vibe_ocr/worker.py",
            "tools/vibe_ocr/setup_runtime.py",
            "tools/vibe_ocr/runtime-spec.json",
            "tools/vibe_ocr/README.txt",
        ):
            with self.subTest(path=relative):
                self.assertFalse(is_excluded(Path(relative)))

    def test_new_exclusions_are_scoped_to_the_two_exact_cache_roots(self) -> None:
        for relative in (
            "tools/vibe_remotion/.cache-public/example.json",
            "tools/vibe_remotion/.downloads/example.json",
            "tools/vibe_shotcraft_library/.downloads-public/example.json",
            "tools/vibe_shotcraft_library/.cache/example.json",
            "tools/another_runtime/.cache/example.json",
            "tools/another_runtime/.downloads/example.json",
        ):
            with self.subTest(path=relative):
                self.assertFalse(is_excluded(Path(relative)))


if __name__ == "__main__":
    unittest.main()
