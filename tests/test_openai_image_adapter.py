import base64
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import requests
from PIL import Image
from backend.app.openai_image_adapter import render
from backend.app import image_profiles
import module4_video_render as visual


class SyncImagesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.output = self.root / 'result.jpg'
        data = io.BytesIO(); Image.new('RGB', (16, 16), 'blue').save(data, 'PNG')
        self.content = data.getvalue()
        self.config = {'protocol': 'openai_sync', 'model': 'custom-image', 'api_key': 'secret',
            'endpoint': 'https://relay.test/v1/images/generations',
            'reference_endpoint': 'https://relay.test/v1/images/edits', 'size': '1536x1024'}
        self.macro = {'macro_scene_id': 'test', 'image_prompt': 'a cat', 'reference_binding_version': 1}
        self.session = Mock()
        self.session.post.return_value = Mock(ok=True, status_code=200,
            json=lambda: {'data': [{'b64_json': base64.b64encode(self.content).decode()}]})

    def test_text_sync_and_no_query(self):
        render(self.macro, self.config, self.session, self.output)
        self.assertTrue(self.output.is_file())
        call = self.session.post.call_args
        self.assertEqual(call.args[0], self.config['endpoint'])
        self.assertEqual(call.kwargs['json'], {'model': 'custom-image', 'prompt': 'a cat', 'n': 1, 'size': '1536x1024'})
        self.session.get.assert_not_called()

    def test_reference_multipart(self):
        reference = self.root / 'input.png'; reference.write_bytes(self.content)
        self.macro['reference_image_paths'] = [str(reference), str(reference)]
        render(self.macro, self.config, self.session, self.output)
        call = self.session.post.call_args
        self.assertEqual(call.args[0], self.config['reference_endpoint'])
        self.assertEqual([r[0] for r in call.kwargs['files']], ['image[]', 'image[]'])
        self.assertNotIn('Content-Type', call.kwargs['headers'])
        self.assertTrue(call.kwargs['files'][0][1][1].closed)

    def test_url_output_never_forwards_key(self):
        self.session.post.return_value.json = lambda: {'data': [{'url': 'https://storage.test/image'}]}
        self.session.get.return_value = Mock(content=self.content)
        render(self.macro, self.config, self.session, self.output)
        self.assertNotIn('headers', self.session.get.call_args.kwargs)

    def test_http_errors_and_timeout_never_resubmit(self):
        for code in (400, 401, 403, 404, 429, 500):
            self.session.post.reset_mock()
            self.session.post.return_value = Mock(ok=False, status_code=code)
            with self.assertRaisesRegex(RuntimeError, str(code)):
                render(self.macro, self.config, self.session, self.output)
            self.assertEqual(self.session.post.call_count, 1)
        self.session.post.side_effect = requests.Timeout('timeout')
        with self.assertRaisesRegex(RuntimeError, '未自动重复提交'):
            render(self.macro, self.config, self.session, self.output)

    def test_invalid_output_does_not_replace_old_image(self):
        self.output.write_bytes(b'old')
        self.session.post.return_value.json = lambda: {'data': [{'b64_json': 'invalid'}]}
        with self.assertRaises(RuntimeError):
            render(self.macro, self.config, self.session, self.output)
        self.assertEqual(self.output.read_bytes(), b'old')

    def test_async_plain_404_stops(self):
        response = Mock(status_code=404, ok=False)
        with patch.object(visual, '_request_with_cloud_refresh', return_value=response):
            with self.assertRaisesRegex(RuntimeError, 'HTTP 404'):
                visual._submit_poster_request(self.macro, {**self.config, 'ratio': '16:9', 'resolution': '1k'}, self.session)
        response.json.assert_not_called()

    def test_subprocess_protocol_and_paths(self):
        profile = {**self.config, 'id': 'custom', 'base_url': 'https://relay.test/v1', 'model_id': 'custom-image',
                   'text_endpoint': '/v1/images/generations', 'reference_endpoint': '/v1/images/edits',
                   'query_endpoint': '/unused', 'api_keys': ['secret']}
        with patch.object(image_profiles, 'profile_by_id', return_value=profile):
            env = image_profiles.profile_environment(profile)
        self.assertEqual(env['OCV_IMAGE_PROTOCOL'], 'openai_sync')
        self.assertEqual(env['RUNNINGHUB_ENDPOINT'], 'https://relay.test/v1/images/generations')

    def test_native_submit_sync_bypasses_async(self):
        with patch.object(visual, '_poster_output_path', return_value=self.output), \
             patch.object(visual, '_new_session', return_value=self.session), \
             patch.object(visual, '_submit_poster_request') as asynchronous:
            task = visual._submit_poster(self.macro, self.config)
        asynchronous.assert_not_called()
        self.assertIsNone(task.task_id)
        self.assertEqual(visual._wait_for_poster(task, self.config), self.output)
