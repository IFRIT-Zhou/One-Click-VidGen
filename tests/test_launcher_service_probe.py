import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('probe', Path(__file__).parents[1] / 'tools/launcher_service_probe.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
TEMP = Path(__file__).parents[1] / 'runtime/temp'
TEMP.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(TEMP)


class ProbeTests(unittest.TestCase):
    def test_closed_starts_backend(self):
        with patch.object(probe, 'listening', return_value=False):
            self.assertEqual(probe.backend(Path('.')), 1)

    def test_unknown_listener_is_not_stopped(self):
        with patch.object(probe, 'listening', return_value=True), patch.object(probe, 'fetch', return_value=b'{}'):
            with self.assertRaises(KeyError):
                probe.backend(Path('.'))

    def test_stale_active_and_idle(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'app.py').touch()
            for count, expected in [(0, 2), (1, 3), (None, 3)]:
                data = probe.json.dumps(dict(server_started_at=1, active_task_count=count)).encode()
                with patch.object(probe, 'listening', return_value=True), patch.object(probe, 'fetch', return_value=data):
                    self.assertEqual(probe.backend(root), expected)

    def test_current(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(probe, 'listening', return_value=True), patch.object(probe, 'fetch', return_value=b'{"server_started_at":9999999999}'):
                self.assertEqual(probe.backend(Path(folder)), 0)

    def test_external_url_refused(self):
        with self.assertRaises(ValueError):
            probe.fetch('https://example.com')

    def test_http_failure_not_success(self):
        with patch.object(probe, 'fetch', side_effect=OSError('refused')):
            self.assertEqual(probe.main(['url', 'http://127.0.0.1:8010']), 1)


if __name__ == '__main__':
    unittest.main()

