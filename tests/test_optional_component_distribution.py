"""Publication guards must not mutate independently installed components."""
import itertools
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from tools.create_portable_archive import is_excluded
from tools.release_integrity import restricted_optional_file

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import publish_launcher_release as release


class OptionalComponentDistributionTests(unittest.TestCase):
    def test_all_portable_variants_exclude_pending_sampler(self):
        for name in ('comfyui/ComfyUI/custom_nodes/comfyui-SelfLift/nodes.py',
                     'components/vendor/COMFYUI-SELFLIFT/node.py',
                     'downloads/comfyui-selflift.zip'):
            for tts, video, models in itertools.product((False, True), repeat=3):
                with self.subTest(name=name, options=(tts, video, models)):
                    self.assertTrue(is_excluded(Path(name), without_local_tts=tts,
                        without_local_video=video, without_models=models))

    def test_other_components_are_not_blanket_blocked(self):
        for name in ('tools/rmbg/worker.py', 'tools/vibe_remotion/package.json',
                     'components/comfyui/h3-v0382/README.md'):
            self.assertFalse(restricted_optional_file(name))

    def test_force_tracked_sampler_is_rejected(self):
        with patch.object(release, 'run', return_value='vendor/COMFYUI-SELFLIFT/node.py'):
            with self.assertRaisesRegex(RuntimeError, '拒绝公开发布'):
                release.assert_no_private_plugins('HEAD')

    def test_injected_archive_is_rejected_before_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / 'update.zip'
            with zipfile.ZipFile(archive, 'w') as package:
                package.writestr('OCV/vendor/comfyui-SelfLift/node.py', b'local component')
            before = archive.read_bytes()
            with patch.object(zipfile.ZipFile, 'extractall') as extract:
                with self.assertRaisesRegex(RuntimeError, 'SelfLift'):
                    release.verify_archive(archive, 'unused')
                extract.assert_not_called()
            self.assertEqual(archive.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
