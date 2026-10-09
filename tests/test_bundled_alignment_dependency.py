import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class BundledAlignmentDependencyTests(unittest.TestCase):
    def test_old_portable_without_installed_pypinyin(self):
        code = '''
import sys
sys.path = [p for p in sys.path if 'site-packages' not in p.lower()]
sys.path.insert(0, sys.argv[1])
from backend.app.tts_alignment import align_starts
words = [dict(word='巍溃杨', start=0., end=1.)]
assert align_starts(['胃溃疡'], words, 1.) == [0.]
import pypinyin
assert 'vendor' in pypinyin.__file__, pypinyin.__file__
'''
        result = subprocess.run([sys.executable, '-X', 'utf8', '-S', '-c', code, str(ROOT)],
                                capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_upstream_license_is_kept(self):
        licenses = list((ROOT / 'backend/vendor').rglob('LICENSE.txt'))
        self.assertTrue(licenses)
        self.assertIn('Permission is hereby granted', licenses[0].read_text(encoding='utf-8'))
