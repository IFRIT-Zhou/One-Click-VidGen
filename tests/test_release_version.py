import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from backend.app import release_version as version


class ReleaseVersionTests(unittest.TestCase):
    def test_portable_and_modified_repository_share_release_id(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'launcher').mkdir()
            (root / 'launcher/update-channel.json').write_text(json.dumps({'release_id': '2026.11.13.1'}))
            with patch.object(version, '__file__', str(root / 'backend/app/release_version.py')):
                version._cache = (0, {})
                self.assertEqual(version.release_info()['label'], 'OCV 2026.11.13.1')
                (root / '.git').write_text('gitdir: fixture')
                version._cache = (0, {})
                with patch.object(version.subprocess, 'run', side_effect=[
                    SimpleNamespace(returncode=0, stdout='abc123\n'),
                    SimpleNamespace(returncode=0, stdout=' M file\n'),
                ]) as run:
                    info = version.release_info()
                    self.assertEqual(info['label'], 'OCV 2026.11.13.1 · abc123 · 本地修改版')
                    self.assertEqual(version.release_info(), info)
                    self.assertEqual(run.call_count, 2)
        version._cache = (0, {})
