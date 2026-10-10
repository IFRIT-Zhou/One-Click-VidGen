import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('bridge_client', ROOT/'plugins/codex_bridge/ocv_bridge.py')
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)


class ClientTests(unittest.TestCase):
    def test_http_failure_overwrites_out_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)/'receipt.json'; out.write_text('{"ok":true}')
            failure = client.urllib.error.HTTPError('http://127.0.0.1:8010/api', 401, 'auth', {},
                io.BytesIO(b'{"detail":{"code":"IMAGE_AUTH_REQUIRED","message":"login required"}}'))
            with patch.object(client.urllib.request, 'build_opener') as opener, contextlib.redirect_stderr(io.StringIO()):
                opener.return_value.open.side_effect = failure
                code = client.main(['--out', str(out), 'info'])
            self.assertEqual(code, 2)
            receipt = json.loads(out.read_text())
            self.assertFalse(receipt['ok']); self.assertEqual(receipt['error']['code'], 'IMAGE_AUTH_REQUIRED')

    def test_network_failure_overwrites_out_without_leaking_url(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)/'receipt.json'
            with patch.object(client.urllib.request, 'build_opener') as opener, contextlib.redirect_stderr(io.StringIO()):
                opener.return_value.open.side_effect = OSError('secret-key signed-url')
                code = client.main(['--out', str(out), 'info'])
            self.assertEqual(code, 2)
            self.assertNotIn('secret-key', out.read_text())
            self.assertFalse(json.loads(out.read_text())['ok'])


    def run_client(self, command, valid=True):
        calls = []

        class Opener:
            def open(self, request, timeout):
                route = request.full_url.rsplit('/', 1)[-1]
                calls.append(route)
                response = {'ok': valid, 'revision': 3}
                if route == 'info':
                    response = {'capabilities': ['shot_patch']}
                return io.BytesIO(json.dumps(response).encode())

        with tempfile.TemporaryDirectory() as directory:
            payload = Path(directory)/'patch.json'
            payload.write_text('{}')
            with patch.object(client.urllib.request, 'build_opener', return_value=Opener()), \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                status = client.main([command, 'project', str(payload)])
        return calls, status, json.loads(output.getvalue())

    def test_patch_apply_validates_applies_and_reads_back(self):
        calls, status, result = self.run_client('patch-apply')
        self.assertEqual(calls, ['info', 'patch-validate', 'patch-apply', 'pack'])
        self.assertEqual(status, 0)
        self.assertTrue(result['readback_verified'])

    def test_failed_patch_never_applies(self):
        calls, status, result = self.run_client('patch-apply', valid=False)
        self.assertEqual(calls, ['info', 'patch-validate'])
        self.assertEqual(status, 2)

    def test_patch_validate_never_applies(self):
        self.assertEqual(self.run_client('patch-validate')[0], ['info', 'patch-validate'])
