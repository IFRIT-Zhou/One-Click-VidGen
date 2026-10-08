import ast
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from backend.app.cloud_client import CloudClient, CloudConfig, CloudSessionStore, CloudAuthSession, CloudApiError
from test_cloud_client import json_response


class DesktopRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.store = CloudSessionStore()
        self.client = CloudClient(7, config=CloudConfig(base_url='https://cluster.example/api/v1'), session_store=self.store)
    def test_send_works_without_cloud_login_and_never_sends_tokens(self):
        with patch('backend.app.cloud_client.requests.request', return_value=json_response({'expires_in':1800,'retry_after':60})) as request:
            self.assertEqual(self.client.request_password_reset('member@example.com')['expires_in'],1800)
        self.assertEqual(request.call_args.args[1],'https://cluster.example/api/v1/auth/password-reset/request')
        self.assertNotIn('Authorization',request.call_args.kwargs['headers'])
        self.assertEqual(request.call_args.kwargs['json'],{'email':'member@example.com'})
    def test_success_discards_matching_cached_session(self):
        self.store.set(7,CloudAuthSession('access','refresh',time.time()+900,{'email':'member@example.com'}))
        with patch('backend.app.cloud_client.requests.request',return_value=json_response({'ok':True})) as request:
            self.client.confirm_password_reset('MEMBER@example.com','123456','new-password')
        self.assertIsNone(self.store.get(7));self.assertNotIn('Authorization',request.call_args.kwargs['headers'])
    def test_failed_reset_preserves_session_and_is_not_retried(self):
        self.store.set(7,CloudAuthSession('access','refresh',time.time()+900,{'email':'member@example.com'}))
        response=json_response({'detail':'验证码已过期'},400)
        with patch('backend.app.cloud_client.requests.request',return_value=response) as request:
            with self.assertRaises(CloudApiError):self.client.confirm_password_reset('member@example.com','123456','new-password')
        self.assertEqual(request.call_count,1);self.assertIsNotNone(self.store.get(7))
    def test_rate_limit_preserves_retry_after_and_does_not_repeat_mail(self):
        response=json_response({'detail':'稍后重试'},429);response.headers={'Retry-After':'120'}
        with patch('backend.app.cloud_client.requests.request',return_value=response) as request:
            with self.assertRaises(CloudApiError) as result:self.client.request_password_reset('member@example.com')
        self.assertEqual(result.exception.retry_after,'120');self.assertEqual(request.call_count,1)
    def test_reset_of_other_account_preserves_current_session(self):
        self.store.set(7,CloudAuthSession('access','refresh',time.time()+900,{'email':'other@example.com'}))
        with patch('backend.app.cloud_client.requests.request',return_value=json_response({'ok':True})):
            self.client.confirm_password_reset('member@example.com','123456','new-password')
        self.assertIsNotNone(self.store.get(7))
    def test_local_endpoints_require_existing_local_user_and_forward_errors(self):
        # Compile only the two handlers, avoiding GPU/model startup side effects.
        module=ast.parse(Path('backend/app/main.py').read_text(encoding='utf-8'))
        handlers=[n for n in module.body if isinstance(n,ast.FunctionDef) and n.name in {'cloud_recovery_request','cloud_recovery_confirm'}]
        self.assertEqual(len(handlers),2)
        for node in handlers:
            self.assertIn('_cloud_for_request',ast.unparse(node))
            self.assertIn('raise _cloud_error(exc)',ast.unparse(node))

if __name__=='__main__':unittest.main()
