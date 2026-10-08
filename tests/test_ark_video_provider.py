import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import requests

from backend.app.ark_video_provider import ArkVideoProvider
from module6_dynamic_video import VideoGenerationRequest, DynamicVideoTaskFailed


class ArkVideoTests(unittest.TestCase):
    def test_long_api_duration_is_submitted_without_clamping(self):
        provider, session = self.provider({'id': 'long-task'})
        with tempfile.TemporaryDirectory() as root:
            image = Path(root) / 'image.png'
            image.write_bytes(b'image')
            request = VideoGenerationRequest('reviewed prompt', (image,), Path(root)/'out.mp4',
                                             30, '16:9', '1080p')
            provider.submit(request)
            self.assertEqual(session.post.call_args.kwargs['json']['duration'], 30)

    def test_proxy_failure_retries_download_directly_without_submission(self):
        provider, session = self.provider({})
        original = provider.session
        with patch('backend.app.ark_video_provider.RunningHubVideoProvider.download',
                   side_effect=[requests.exceptions.ProxyError('proxy 502'), Path('out.mp4')]) as download:
            with patch('backend.app.ark_video_provider.requests.Session') as factory:
                direct = factory.return_value.__enter__.return_value
                self.assertEqual(provider.download('https://example.com/signed.mp4', Path('out.mp4')), Path('out.mp4'))
                self.assertFalse(direct.trust_env)
                self.assertEqual(download.call_count, 2)
        self.assertIs(provider.session, original)
        session.post.assert_not_called()

    def provider(self, body, status=200):
        session = Mock()
        response = Mock(status_code=status, ok=status < 400)
        response.json.return_value = body
        session.post.return_value = response
        session.get.return_value = response
        return ArkVideoProvider('test-key', base_url='https://ark.cn-beijing.volces.com',
            submit_path='/api/v3/contents/generations/tasks',
            query_path='/api/v3/contents/generations/tasks', session=session), session

    def test_submit_reference_image_and_persist_identity(self):
        provider, session = self.provider({'id': 'cgt-test'})
        with tempfile.TemporaryDirectory() as root:
            image = Path(root) / 'image.png'
            image.write_bytes(b'image')
            request = VideoGenerationRequest('reviewed prompt', (image,), Path(root)/'out.mp4',
                                             10, '16:9', '1080p')
            before = Mock()
            self.assertEqual(provider.submit(request, before_submit=before)['task_id'], 'cgt-test')
            before.assert_called_once()
            payload = session.post.call_args.kwargs['json']
            self.assertEqual(payload['model'], 'doubao-seedance-2-0-260128')
            self.assertEqual(payload['content'][1]['role'], 'reference_image')
            self.assertTrue(payload['content'][1]['image_url']['url'].startswith('data:image/png;base64,'))
            self.assertFalse(payload['generate_audio'])
            self.assertNotIn('videoUrls', payload)

    def test_query_maps_download_and_terminal_failure(self):
        provider, session = self.provider({'status':'succeeded', 'content':{'video_url':'https://example.com/clip.mp4'}})
        result = provider.query('cgt-test')
        self.assertEqual(result['status'], 'SUCCESS')
        self.assertEqual(result['result_url'], 'https://example.com/clip.mp4')
        self.assertTrue(session.get.call_args.args[0].endswith('/cgt-test'))
        session.get.return_value.json.return_value = {'status':'failed', 'error':{'message':'model failure'}}
        self.assertEqual(provider.query('cgt-test')['raw']['errorMessage'], 'model failure')
