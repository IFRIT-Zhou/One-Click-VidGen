"""Offline upload-timeout regressions; no socket is opened and no API is called."""
import json
import unittest
from unittest.mock import MagicMock, patch

import requests
from urllib3.connection import HTTPConnection
from urllib3.exceptions import ProtocolError, ReadTimeoutError

from backend.app import vibe_cloud_client as client


class VibeTransportUploadTests(unittest.TestCase):
    API_KEY = 'private-api-key-never-in-diagnostics'
    URL_MARKER = 'private-endpoint-never-in-diagnostics'
    SYSTEM = 'private-system-never-in-diagnostics'
    PAYLOAD = 'private-payload-never-in-diagnostics'
    RAW_ERROR = 'https://private-user:private-password@example.invalid/?key=private-error-secret'

    def config(self):
        return {
            'model': 'offline-vision',
            'base_url': 'http://127.0.0.1:9/' + self.URL_MARKER,
            'api_key': self.API_KEY,
        }

    def assert_safe_diagnostics(self, error):
        serialized = str(error) + json.dumps(error.usage, ensure_ascii=False)
        for private_value in (
            self.API_KEY, self.URL_MARKER, self.SYSTEM, self.PAYLOAD,
            self.RAW_ERROR, 'private-user', 'private-password', 'private-error-secret',
        ):
            self.assertNotIn(private_value, serialized)
        self.assertIsInstance(error.usage['exception_types'], list)
        for name in error.usage['exception_types']:
            self.assertIsInstance(name, str)
            self.assertRegex(name, r'^[A-Za-z_][A-Za-z0-9_.]*$')
        self.assertIsInstance(error.usage['network_errnos'], list)
        for number in error.usage['network_errnos']:
            self.assertIs(type(number), int)

    def exception_names(self, error):
        return {name.rsplit('.', 1)[-1] for name in error.usage['exception_types']}

    def test_real_requests_upload_uses_60_seconds_and_wrapped_timeout_is_not_stream_failure(self):
        """Exercise requests/urllib3 wrapping, stopping before any socket I/O."""
        sends = []
        session = requests.Session()
        session.trust_env = False
        self.addCleanup(session.close)

        def offline_send(connection, data):
            sends.append({'timeout': connection.timeout, 'bytes': len(data)})
            if len(sends) == 2:
                raise TimeoutError(self.RAW_ERROR)

        image = {
            'type': 'image_url',
            'image_url': {
                'url': 'data:image/jpeg;base64,' + 'A' * 131072,
                'detail': 'high',
            },
        }
        with patch.object(HTTPConnection, 'send', offline_send), \
             patch.object(HTTPConnection, 'connect', side_effect=AssertionError('Network access forbidden')) as connect, \
             patch.object(client.requests, 'post', side_effect=session.post) as post, \
             patch.object(client, 'image_block', return_value=image) as image_block:
            with self.assertRaises(client.CloudCallError) as caught:
                client.chat_json(
                    self.config(), self.SYSTEM, {'text': self.PAYLOAD + '中文'},
                    {'reference': 'not-opened.png'}, lambda: False,
                )

        error = caught.exception
        self.assertEqual(client.CONNECT_TIMEOUT, 60)
        self.assertEqual(client.READ_TIMEOUT, 45)
        self.assertEqual(len(sends), 2)
        self.assertEqual([item['timeout'] for item in sends], [60, 60])
        self.assertEqual(post.call_args.kwargs['timeout'], (60, 45))
        self.assertEqual(error.usage['request_bytes'], sends[1]['bytes'])
        self.assertGreater(error.usage['request_bytes'], 131072)
        self.assertEqual(error.usage['image_count'], 1)
        self.assertEqual(error.usage['connect_send_timeout_seconds'], 60)
        self.assertEqual(error.usage['read_timeout_seconds'], 45)
        self.assertEqual(error.usage['total_budget_seconds'], 180)
        self.assertEqual(error.usage['error_code'], 'network_timeout')
        self.assertEqual(error.usage['transport_phase'], 'waiting_response')
        self.assertEqual(error.usage['answer_chars'], 0)
        self.assertEqual(error.usage['received_events'], 0)
        self.assertNotIn('http_status', error.usage)
        self.assertTrue({'ConnectionError', 'ProtocolError', 'TimeoutError'} <= self.exception_names(error))
        self.assertIn('未自动重试', str(error))
        self.assertIn('未切换渠道', str(error))
        self.assert_safe_diagnostics(error)
        post.assert_called_once()
        image_block.assert_called_once_with('not-opened.png')
        connect.assert_not_called()

    def test_connect_and_read_timeouts_are_both_clipped_to_remaining_budget(self):
        for budget, expected in ((7.25, (7.25, 7.25)), (50, (50, 45)), (180, (60, 45))):
            with self.subTest(budget=budget), \
                 patch.object(client.requests, 'post', side_effect=requests.Timeout(self.RAW_ERROR)) as post:
                with self.assertRaises(client.CloudCallError) as caught:
                    client.chat_json(
                        self.config(), self.SYSTEM, {'text': self.PAYLOAD}, {},
                        lambda: False, timeout=budget,
                    )
                usage = caught.exception.usage
                self.assertEqual(post.call_args.kwargs['timeout'], expected)
                self.assertEqual(usage['connect_send_timeout_seconds'], expected[0])
                self.assertEqual(usage['read_timeout_seconds'], expected[1])
                self.assertEqual(usage['total_budget_seconds'], budget)
                self.assertEqual(usage['image_count'], 0)
                self.assertGreater(usage['request_bytes'], 0)
                self.assertEqual(usage['error_code'], 'network_timeout')
                self.assert_safe_diagnostics(caught.exception)
                post.assert_called_once()

    def test_connection_reset_remains_protocol_failure_and_only_numeric_errno_is_exposed(self):
        reset = ConnectionResetError(10054, self.RAW_ERROR)
        error = requests.ConnectionError(ProtocolError(self.RAW_ERROR, reset))
        error.errno = self.RAW_ERROR
        with patch.object(client.requests, 'post', side_effect=error) as post:
            with self.assertRaises(client.CloudCallError) as caught:
                client.chat_json(self.config(), self.SYSTEM, {'text': self.PAYLOAD}, {}, lambda: False)
        result = caught.exception
        self.assertEqual(result.usage['error_code'], 'stream_interrupted')
        self.assertEqual(result.usage['network_errnos'], [10054])
        self.assertTrue({'ConnectionError', 'ProtocolError', 'ConnectionResetError'} <= self.exception_names(result))
        self.assert_safe_diagnostics(result)
        post.assert_called_once()

    def test_wrapped_os_timeout_keeps_numeric_errno_without_exposing_original_text(self):
        inner = TimeoutError(10060, self.RAW_ERROR)
        error = requests.ConnectionError(ProtocolError(self.RAW_ERROR, inner))
        with patch.object(client.requests, 'post', side_effect=error) as post:
            with self.assertRaises(client.CloudCallError) as caught:
                client.chat_json(self.config(), self.SYSTEM, {'text': self.PAYLOAD}, {}, lambda: False)
        result = caught.exception
        self.assertEqual(result.usage['error_code'], 'network_timeout')
        self.assertEqual(result.usage['network_errnos'], [10060])
        self.assertIn('TimeoutError', self.exception_names(result))
        self.assert_safe_diagnostics(result)
        post.assert_called_once()

    def test_specific_connect_and_read_timeout_classification_takes_precedence(self):
        cases = (
            (requests.ConnectTimeout(TimeoutError(self.RAW_ERROR)), 'connect_timeout'),
            (requests.ReadTimeout(TimeoutError(self.RAW_ERROR)), 'read_timeout'),
            (requests.ConnectionError(ReadTimeoutError(None, '/private', self.RAW_ERROR)), 'read_timeout'),
        )
        for error, expected in cases:
            with self.subTest(code=expected), patch.object(client.requests, 'post', side_effect=error) as post:
                with self.assertRaises(client.CloudCallError) as caught:
                    client.chat_json(self.config(), self.SYSTEM, {'text': self.PAYLOAD}, {}, lambda: False)
                self.assertEqual(caught.exception.usage['error_code'], expected)
                self.assert_safe_diagnostics(caught.exception)
                post.assert_called_once()

    def test_successful_call_retains_payload_size_and_budget_diagnostics(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.status_code = 200
        response.iter_lines.return_value = [
            b'data: {"choices":[{"delta":{"content":"{}"},"finish_reason":"stop"}]}',
            b'data: [DONE]',
        ]
        with patch.object(client.requests, 'post', return_value=response) as post:
            result, usage = client.chat_json(
                self.config(), self.SYSTEM, {'text': self.PAYLOAD + '中文'}, {}, lambda: False,
            )
        prepared = requests.Request('POST', 'http://example.invalid', json=post.call_args.kwargs['json']).prepare()
        self.assertEqual(result, {})
        self.assertEqual(usage['request_bytes'], len(prepared.body))
        self.assertEqual(usage['image_count'], 0)
        self.assertEqual(usage['connect_send_timeout_seconds'], 60)
        self.assertEqual(usage['read_timeout_seconds'], 45)
        self.assertEqual(usage['total_budget_seconds'], 180)
        self.assertEqual(usage['http_status'], 200)
        self.assertNotIn(self.PAYLOAD, json.dumps(usage))
        post.assert_called_once()


if __name__ == '__main__':
    unittest.main()
