"""Public bridge files are updateable; local/private plugin files stay private."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

from tools.create_portable_archive import is_excluded
from tools.release_integrity import INTEGRITY_FILES, release_file_bytes

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = [
    'plugins/codex_bridge/plugin.json', 'plugins/codex_bridge/ocv_bridge.py',
    'plugins/codex_bridge/README.md',
    'plugins/codex_bridge/skills/ocv-production-bridge/SKILL.md',
    'plugins/codex_bridge/skills/ocv-production-bridge/agents/openai.yaml',
]
PRIVATE = ['plugins/codex_bridge/disabled', 'plugins/codex_bridge/cookies.txt',
           'plugins/codex_bridge/plan.json', 'plugins/private_medical/plugin.json',
           'workspace/codex_bridge/draft.json']


class DistributionTests(unittest.TestCase):
    def test_package_and_fingerprint_include_public_files_only(self):
        for path in PUBLIC:
            self.assertTrue((ROOT/path).is_file(), path)
            self.assertFalse(is_excluded(Path(path)), path)
            self.assertIn(path, INTEGRITY_FILES)
        for path in PRIVATE:
            self.assertTrue(is_excluded(Path(path)), path)
            self.assertNotIn(path, INTEGRITY_FILES)

    def test_git_does_not_hide_public_skill(self):
        if not shutil.which('git'):
            self.skipTest('git unavailable')
        for path in PUBLIC:
            result = subprocess.run(['git', 'check-ignore', '--no-index', '-q', path], cwd=ROOT)
            self.assertEqual(result.returncode, 1, path)

    def test_update_whitelist_preserves_disable_choice_and_private_files(self):
        shell = shutil.which('pwsh') or shutil.which('powershell')
        if not shell:
            self.skipTest('PowerShell required for updater function test')
        # Execute only this pure predicate, never the actual update script.
        source = (ROOT/'launcher/safe_update_helper.ps1').read_text(encoding='utf-8-sig')
        predicate = source.split('function Test-ProtectedRelativePath {', 1)[1].split('\nfunction ', 1)[0]
        paths = PUBLIC + PRIVATE + ['plugins/user_plugin/plugin.json']
        code = 'function Test-ProtectedRelativePath {' + predicate
        code += '\n@(' + ','.join("'" + p + "'" for p in paths) + ') | ForEach-Object { Test-ProtectedRelativePath $_ } | ConvertTo-Json -Compress'
        result = subprocess.run([shell, '-NoProfile', '-NonInteractive', '-Command', code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [False]*len(PUBLIC) + [True]*(len(PRIVATE)+1))

    def test_document_hash_normalizes_line_endings(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            for extension in ('.md', '.yaml'):
                path = Path(directory)/('test' + extension)
                path.write_bytes(b'\xef\xbb\xbfhello\r\nworld\r\n')
                self.assertEqual(release_file_bytes(path), b'hello\nworld\n')
