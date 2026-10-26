"""Isolated updater tests: no real installation or service is modified."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PS = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
HELPER = ROOT / 'launcher/safe_update_helper.ps1'


@unittest.skipUnless(os.name == 'nt', 'Windows updater')
class LauncherArchiveTests(unittest.TestCase):
    def extract(self, package, destination):
        quote = lambda p: str(p).replace("'", "''")
        command = (
            "$ErrorActionPreference='Stop';"
            f"$a=[System.Management.Automation.Language.Parser]::ParseFile('{quote(HELPER)}',[ref]$null,[ref]$null);"
            "$a.FindAll({param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] "
            "-and $n.Name -in @('Assert-UnderRoot','Expand-OcvUpdateArchive')},$true) | "
            "ForEach-Object {. ([scriptblock]::Create($_.Extent.Text))};"
            "function Write-UpdateLog {param($Message)};"
            f"Expand-OcvUpdateArchive -Package '{quote(package)}' -Destination '{quote(destination)}'"
        )
        return subprocess.run([str(PS), '-NoProfile', '-NonInteractive', '-Command', command],
                              env=dict(os.environ, PATH=''), capture_output=True, timeout=45)

    def test_dotfiles_spaces_chinese_and_content(self):
        with tempfile.TemporaryDirectory(prefix='OCV 空格 test ') as folder:
            root = Path(folder)
            package = root / 'update.zip'
            with zipfile.ZipFile(package, 'w') as z:
                z.writestr('OCV/.env.example', b'EXAMPLE=yes\n')
                z.writestr('OCV/目录/file.txt', b'payload')
            result = self.extract(package, root / 'stage')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((root / 'stage/OCV/.env.example').read_bytes(), b'EXAMPLE=yes\n')
            self.assertEqual((root / 'stage/OCV/目录/file.txt').read_bytes(), b'payload')

    def test_reject_unsafe_names_before_writing(self):
        for name in ('../escape', '/absolute', 'C:/absolute', 'OCV/file:stream',
                     'OCV/../escape', 'OCV/NUL.txt', 'OCV/trailing.', 'OCV/a?/b'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                with zipfile.ZipFile(root / 'update.zip', 'w') as z:
                    z.writestr('OCV/safe.txt', 'safe')
                    z.writestr(name, 'bad')
                result = self.extract(root / 'update.zip', root / 'stage')
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((root / 'stage').exists())

    def test_reject_duplicate_case_and_symlink(self):
        for symlink in (False, True):
            with self.subTest(symlink=symlink), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                with zipfile.ZipFile(root / 'update.zip', 'w') as z:
                    z.writestr('OCV/a', 'first')
                    if symlink:
                        info = zipfile.ZipInfo('OCV/link')
                        info.create_system = 3
                        info.external_attr = 0o120777 << 16
                        z.writestr(info, '../outside')
                    else:
                        z.writestr('ocv/A', 'duplicate')
                self.assertNotEqual(self.extract(root / 'update.zip', root / 'stage').returncode, 0)
                self.assertFalse((root / 'stage').exists())

    def test_existing_stage_and_invalid_zip_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'bad.zip').write_bytes(b'not a zip')
            self.assertNotEqual(self.extract(root / 'bad.zip', root / 'stage').returncode, 0)
            (root / 'stage').mkdir()
            (root / 'stage/keep').write_text('keep')
            self.assertNotEqual(self.extract(root / 'bad.zip', root / 'stage').returncode, 0)
            self.assertEqual((root / 'stage/keep').read_text(), 'keep')

    def test_full_overlay_and_missing_template_no_mutation(self):
        for missing_template in (False, True):
            with self.subTest(missing_template=missing_template), tempfile.TemporaryDirectory(prefix='OCV test ') as folder:
                root = Path(folder)
                temp = root / 'runtime/temp/ocv-updates'
                temp.mkdir(parents=True)
                (root / '.env').write_text('KEEP_KEY')
                (root / 'start_windows.bat').write_text('old startup')
                (root / 'workspace').mkdir()
                (root / 'workspace/keep.txt').write_text('KEEP_PROJECT')
                channel = dict(release_id='test', release_order=1, portable_overlay_safe=True)
                with zipfile.ZipFile(temp / 'update.zip', 'w') as z:
                    z.writestr('OCV/start_windows.bat', 'new startup')
                    z.writestr('OCV/launcher/update-channel.json', json.dumps(channel))
                    z.writestr('OCV/.env', 'DO_NOT_COPY')
                    z.writestr('OCV/workspace/keep.txt', 'DO_NOT_COPY')
                    if not missing_template:
                        z.writestr('OCV/.env.example', 'example')
                # Only this isolated test copy checks port 0 instead of real OCV ports.
                script = root / 'helper.ps1'
                script.write_text(HELPER.read_text().replace('@(8010, 5173, 8030)', '@(0)'), encoding='utf-8-sig')
                result = subprocess.run([str(PS), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script),
                    '-Mode', 'portable', '-ProjectRoot', str(root), '-LauncherPid', '2147483647',
                    '-PackagePath', str(temp / 'update.zip'), '-ExpectedReleaseId', 'test', '-NoRestart'],
                    env=dict(os.environ, PATH=''), capture_output=True, timeout=45)
                log = (root / 'runtime_logs/launcher_update.log').read_text(encoding='utf-8-sig')
                self.assertEqual(result.returncode, 1 if missing_template else 0, log)
                self.assertEqual((root / '.env').read_text(), 'KEEP_KEY')
                self.assertEqual((root / 'workspace/keep.txt').read_text(), 'KEEP_PROJECT')
                self.assertEqual((root / 'start_windows.bat').read_text(), 'old startup' if missing_template else 'new startup')
                if missing_template:
                    self.assertIn('Phase=validate-package', log)
                    self.assertIn('Location:', log)


if __name__ == '__main__':
    unittest.main()
