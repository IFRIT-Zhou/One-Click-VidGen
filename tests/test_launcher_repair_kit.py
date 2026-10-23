import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('kit', ROOT / 'tools/launcher_repair_kit.py')
kit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kit)


class RepairTests(unittest.TestCase):
    def test_install_restore_and_protect_new_update(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'runtime/temp') as folder:
            root = Path(folder)
            package = root / 'kit'
            manifest = {}
            for name in kit.FILES:
                dest = package / 'payload' / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text('new', 'utf-8')
                manifest[name] = kit.sha(dest)
            (package / 'manifest.json').write_text(json.dumps(manifest), 'utf-8')
            (root / 'start_windows.bat').write_text('old', 'utf-8')
            (root / '.env').write_text('KEEP', 'utf-8')
            with patch.object(kit, 'stopped'):
                kit.install(root, package)
            backup = Path((package / 'last_backup.txt').read_text('utf-8'))
            self.assertEqual((root / 'start_windows.bat').read_text(), 'new')
            self.assertEqual((root / '.env').read_text(), 'KEEP')
            (root / 'start_windows.bat').write_text('later-update', 'utf-8')
            with self.assertRaises(RuntimeError):
                kit.restore(root, backup)
            (root / 'start_windows.bat').write_text('new', 'utf-8')
            kit.restore(root, backup)
            self.assertEqual((root / 'start_windows.bat').read_text(), 'old')
            self.assertFalse((root / 'tools/launcher_service_probe.py').exists())

    def test_busy_port_rejected(self):
        with patch.object(kit.socket.socket, 'connect_ex', return_value=0):
            with self.assertRaises(RuntimeError):
                kit.stopped()

    def test_redaction(self):
        self.assertNotIn('abcdef', kit.redact('Authorization: Bearer abcdef'))


if __name__ == '__main__':
    unittest.main()

