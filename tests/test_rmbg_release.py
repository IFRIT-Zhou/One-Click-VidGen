"""The optional model must not be a Launcher download or integrity prerequisite."""
import itertools
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from tools.create_portable_archive import is_excluded
from tools.release_integrity import INTEGRITY_FILES, RMBG_MANAGED_FILES
from tools import release_integrity


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import publish_launcher_release as release


class RmbgReleaseTests(unittest.TestCase):
    private_files = (
        'tools/rmbg/models/BiRefNet/BiRefNet-general.safetensors',
        'TOOLS/RMBG/MODELS/BiRefNet/local-note.json',
        'tools/rmbg/python/python.exe',
        'tools/rmbg/vendor/birefnet/__pycache__/config.pyc',
        'tools/rmbg/unrelated.json',
    )

    def test_archive_editions_and_launcher_integrity(self):
        for relative in (*RMBG_MANAGED_FILES, *self.private_files):
            managed = relative in RMBG_MANAGED_FILES
            with self.subTest(relative=relative):
                self.assertEqual(relative in INTEGRITY_FILES, managed)
                for tts, video, models in itertools.product((False, True), repeat=3):
                    self.assertEqual(is_excluded(Path(relative), without_local_tts=tts,
                        without_local_video=video, without_models=models), not managed)
        self.assertTrue(is_excluded(Path('runtime/rmbg/env/Scripts/python.exe')))

    def test_git_delivery_rules(self):
        for path in (*RMBG_MANAGED_FILES, *self.private_files):
            with self.subTest(path=path):
                # Ignore case semantics for filesystem-specific git settings.
                if path.startswith('TOOLS/'):
                    path = path.lower()
                proc = subprocess.run(['git', 'check-ignore', '--no-index', '-q', '--', path], cwd=ROOT)
                self.assertEqual(proc.returncode, 0 if path not in RMBG_MANAGED_FILES else 1)

    def test_real_plugin_sources_are_sufficient_without_weights(self):
        # Verify the real deployable payload, not fixtures that could go stale.
        with tempfile.TemporaryDirectory() as folder, patch.object(release_integrity, 'INTEGRITY_FILES', RMBG_MANAGED_FILES):
            root = Path(folder)
            for name in RMBG_MANAGED_FILES:
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((ROOT / name).read_bytes())
            before,missing = release_integrity.fingerprint(root)
            self.assertEqual(missing, [])
            weight = root / self.private_files[0]
            weight.parent.mkdir(parents=True)
            weight.write_bytes(b'optional user weights')
            self.assertEqual(release_integrity.fingerprint(root), (before,[]))
            (root / 'tools/rmbg/worker.py').write_text('# deliberately damaged')
            self.assertNotEqual(release_integrity.fingerprint(root)[0], before)

    def test_even_force_tracked_or_injected_package_payload_is_refused_before_extraction(self):
        for path in self.private_files:
            with self.subTest(path=path), patch.object(release, 'run', return_value=path):
                with self.assertRaisesRegex(RuntimeError, '拒绝公开发布'):
                    release.assert_no_private_plugins('HEAD')
                with tempfile.TemporaryDirectory() as folder:
                    archive=Path(folder) / 'update.zip'
                    with zipfile.ZipFile(archive,'w') as package:
                        package.writestr('OCV/' + path, b'not a real weight')
                    with patch.object(zipfile.ZipFile, 'extractall') as extraction:
                        with self.assertRaisesRegex(RuntimeError, 'RMBG'):
                            release.verify_archive(archive, 'unused')
                        extraction.assert_not_called()
