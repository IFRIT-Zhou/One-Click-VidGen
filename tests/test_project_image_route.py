import copy
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import HTTPException
from backend.app import video_studio as studio
from backend.app.cloud_image_channel import generation_payload


class ProjectImageRouteTests(unittest.TestCase):
    def record(self):
        return dict(id='route-test', revision=3, status='image_review', logs=[],
                    creation_parameters=dict(use_cloud_image_pool=True, method='ican', size='2560x1440',
                                             image_resolution='2k', image_profile_snapshot={'id': 'old'}),
                    settings={'ratio': '16:9'}, shots=[{'id': 'done', 'image': 'keep.jpg', 'video': 'keep.mp4'},
                    {'id': 'failed', 'image_error': 'timeout'}], scenes=[{'id': 'subtitle', 'text': '保留字幕'}],
                    audio='keep.wav', planning_state={'completed': {'core': ['done']}}, error='timeout')

    def invoke(self, record, *, channel='running', revision=3, login_failure=False, active=False):
        with ExitStack() as stack:
            stack.enter_context(patch.object(studio, 'require_user', return_value={'id': 7}))
            stack.enter_context(patch.object(studio, 'directory', return_value=Path('route-test')))
            stack.enter_context(patch.object(studio, 'read', return_value=copy.deepcopy(record)))
            saved = stack.enter_context(patch.object(studio, 'save'))
            stack.enter_context(patch.object(studio, 'ACTIVE', {'route-test'} if active else set()))
            client = Mock()
            client.image_pool_runtime.return_value = {'base_url': 'https://pool.invalid', 'access_token': 'SECRET'}
            if login_failure:
                client.image_pool_runtime.side_effect = ValueError('expired')
            stack.enter_context(patch('backend.app.cloud_client.cloud_client_for', return_value=client))
            snapshot = {'id': 'new', 'name': '我的 API', 'model_id': 'fresh-image-model', 'resolution': '2k'}
            fresh = stack.enter_context(patch('backend.app.image_profiles.profile_snapshot', return_value=snapshot))
            personal = stack.enter_context(patch('backend.app.image_profiles.profile_provider_configs',
                return_value=[{'api_key': 'SECRET', 'model': 'fresh-image-model', 'resolution': '2k'}]))
            submit = stack.enter_context(patch('module4_video_render._render_poster_with_retry'))
            try:
                result = studio.set_image_route('route-test', studio.ImageRouteEdit(
                    revision=revision, channel=channel, image_profile_id='new', image_resolution='2k'), None)
                configs = studio._image_configs(result, 7)
            except Exception:
                saved.assert_not_called()
                raise
            finally:
                submit.assert_not_called()
            self.assertEqual(saved.call_count, 1)
            return result, configs, fresh.call_args, personal.call_count

    def test_switch_ican_to_stable_used_by_next_request_preserves_everything(self):
        original = self.record()
        result, configs, _, personal_calls = self.invoke(original)
        payload = generation_payload({}, configs[0])
        self.assertEqual((payload['method'], payload['provider']), ('running', 'runninghub'))
        self.assertNotIn('model', payload)
        self.assertNotIn('size', payload)
        self.assertEqual(personal_calls, 0)
        for key in ('shots', 'scenes', 'audio', 'planning_state', 'status', 'error'):
            self.assertEqual(result[key], original[key])
        self.assertEqual(result['revision'], 4)
        self.assertEqual(result['creation_parameters']['language_route'], 'cloud')
        self.assertNotIn('image_profile_snapshot', result['creation_parameters'])
        self.assertFalse(result['creation_parameters']['dynamic_auto_advance'])
        self.assertIn('稳定版号池', result['logs'][-1])

    def test_switch_to_personal_uses_fresh_profile_and_keeps_language_pool(self):
        result, configs, fresh, _ = self.invoke(self.record(), channel='personal')
        self.assertFalse(result['creation_parameters']['use_cloud_image_pool'])
        self.assertEqual(result['creation_parameters']['language_route'], 'cloud')
        self.assertEqual(fresh.args, ('new', '2k'))
        self.assertEqual(configs[0]['model'], 'fresh-image-model')
        self.assertNotIn('cloud_pool', configs[0])
        self.assertIn('个人 API · fresh-image-model · 2K', result['logs'][-1])

    def test_personal_language_stays_personal_after_enabling_cloud_images(self):
        record = self.record()
        record['creation_parameters'] = {'use_cloud_image_pool': False, 'language_route': 'project'}
        result, configs, _, _ = self.invoke(record, channel='ican')
        self.assertEqual(result['creation_parameters']['language_route'], 'personal')
        self.assertEqual(generation_payload({}, configs[0])['model'], 'gpt-image-2.5')

    def test_busy_stale_and_login_failure_do_not_commit(self):
        for record, args, status in [
            (self.record(), {'revision': 2}, 409),
            ({**self.record(), 'status': 'image_generating'}, {}, 409),
            (self.record(), {'active': True}, 409),
            (self.record(), {'login_failure': True}, 401),
        ]:
            with self.subTest(args=args), self.assertRaises(HTTPException) as raised:
                self.invoke(record, **args)
            self.assertEqual(raised.exception.status_code, status)

    def test_missing_personal_profile_does_not_fall_back(self):
        with patch('backend.app.image_profiles.profile_by_id', side_effect=ValueError('配置不存在')), \
             patch.object(studio, 'require_user', return_value={'id': 7}), \
             patch.object(studio, 'directory', return_value=Path('route-test')), \
             patch.object(studio, 'read', return_value=self.record()), \
             patch.object(studio, 'save') as save, \
             patch.object(studio, '_image_configs') as configs:
            with self.assertRaises(HTTPException) as raised:
                studio.set_image_route('route-test', studio.ImageRouteEdit(revision=3, channel='personal', image_profile_id='missing'), None)
            self.assertEqual(raised.exception.status_code, 400)
            save.assert_not_called(); configs.assert_not_called()

    def test_resolved_route_logs_safe_model_and_request_size(self):
        label = studio._image_route_label([
            {'cloud_pool': '1', 'method': 'ican', 'size': '2048x1152', 'api_key': 'SECRET'},
            {'cloud_pool': '1', 'method': 'running', 'resolution': '4k', 'ratio': '9:16'},
            {'protocol': 'openai_sync', 'model': 'my-image', 'size': '1536x1024', 'endpoint': 'https://SECRET.invalid'},
        ])
        for text in ['ICAN 号池 · GPT Image 2.5 · 2048x1152', '稳定版号池', '4K · 9:16', '个人 API · my-image · 1536x1024']:
            self.assertIn(text, label)
        self.assertNotIn('SECRET', label)

    def test_redraw_log_has_actual_route(self):
        record = self.record(); shot = record['shots'][0]
        with patch.object(studio, 'save'), patch.object(studio, 'IMAGE_EDITS', set()), \
             patch.object(studio.threading, 'Thread'):
            studio._start_storyboard_redraw(Path('route-test'), record, shot, SimpleNamespace(prompt='图'),
                [{'cloud_pool': '1', 'method': 'ican', 'size': '2048x1152'}], [])
        self.assertIn('ICAN 号池 · GPT Image 2.5 · 2048x1152', record['logs'][-1])
        self.assertEqual(shot['image_task']['route'], record['last_image_route'])


if __name__ == '__main__':
    unittest.main()
