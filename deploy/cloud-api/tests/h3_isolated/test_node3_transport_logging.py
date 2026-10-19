import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('node3_transport_test', Path(__file__).parents[2] / 'node3_client.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class TransportLoggingTests(unittest.TestCase):
    def test_native_transport_never_builds_windows_or_user_shell_commands(self):
        payload={'prompt':'$(touch /tmp/unwanted); `whoami`'}
        completed=subprocess.CompletedProcess([],0,stdout=b'{"ok":true,"result":{"status":"running"}}')
        with patch.object(module.subprocess,'run',return_value=completed) as run:
            result=module.Node3Client().call('submit',spec=payload)
        args,kwargs=run.call_args
        self.assertEqual(args[0][-1],'sudo -n -- /usr/bin/python3 -I /opt/ocv-h3/node3_agent.py')
        self.assertEqual(json.loads(kwargs['input']),{'action':'submit','spec':payload})
        self.assertNotIn('wsl.exe',' '.join(args[0]))
        self.assertNotIn(payload['prompt'],' '.join(args[0]))
        self.assertEqual(result,{'status':'running'})

    def test_timeout_logged_without_payload_or_subprocess_output(self):
        secret = 'private-prompt-and-key'
        error = subprocess.TimeoutExpired(['ssh', secret], 240, output=secret, stderr=secret)
        with patch.object(module.Node3Client, '_call', side_effect=error), self.assertLogs(module.LOG, 'INFO') as logs:
            with self.assertRaises(subprocess.TimeoutExpired):
                module.Node3Client().call('submit', spec={'prompt': secret})
        report = '\n'.join(logs.output)
        self.assertIn('NODE_TRANSPORT_TIMEOUT', report)
        self.assertIn('action=submit', report)
        self.assertNotIn(secret, report)

    def test_remote_error_allowlist_and_unknown_error_redaction(self):
        for error, code in [('WINDOWS_H3_BUSY','WINDOWS_H3_BUSY'),
                            ('token=private-value','NODE_RPC_FAILED')]:
            with patch.object(module.Node3Client, '_call', side_effect=RuntimeError(error)), self.assertLogs(module.LOG, 'INFO') as logs:
                with self.assertRaises(RuntimeError):module.Node3Client().call('start_h3')
            report = '\n'.join(logs.output)
            self.assertIn('code='+code, report)
            self.assertNotIn('private-value', report)

    def test_success_retains_result_without_logging_result_payload(self):
        result = {'data_b64':'private-result'}
        with patch.object(module.Node3Client, '_call', return_value=result), self.assertLogs(module.LOG, 'INFO') as logs:
            self.assertIs(module.Node3Client().call('result'), result)
        self.assertIn('node3_done', '\n'.join(logs.output))
        self.assertNotIn('private-result', '\n'.join(logs.output))

    def test_unknown_action_never_reaches_transport(self):
        with patch.object(module.Node3Client, '_call') as transport:
            with self.assertRaises(ValueError):module.Node3Client().call('arbitrary-private-input')
        transport.assert_not_called()


if __name__ == '__main__':unittest.main()
