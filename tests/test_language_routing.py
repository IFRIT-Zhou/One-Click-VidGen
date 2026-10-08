import copy
import os
import threading
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from backend.app import gemini_client as client
from backend.app import language_routing as routing


class LanguageRoutingTests(unittest.TestCase):
    def test_scene_and_parallel_image_workers_keep_pool_language_route(self):
        from backend.app import video_studio as studio
        import module4_video_render as visual
        observed = []
        def observe(*args, **kwargs):
            observed.append((client.gemini_configured(), client.language_model(), client.language_base_url()))
        def render(*args):
            observe()
            return SimpleNamespace(is_file=lambda: True,
                                   stat=lambda: SimpleNamespace(st_size=1), name='new.jpg')
        record = dict(id='test', revision=1, logs=[], settings={},
                      creation_parameters={'use_cloud_image_pool': True},
                      shots=[dict(id='shot1', image_prompt='a', image_status='pending')])
        with tempfile.TemporaryDirectory() as folder, \
             patch.dict(os.environ, {}, clear=True), \
             patch.object(routing, 'cloud_client_for', side_effect=self.runtime), \
             patch.object(studio, 'save'), patch.object(studio, 'ACTIVE', set()), \
             patch.object(studio, '_prepare_scene_assets', side_effect=observe), \
             patch.object(studio, '_bind_material_numbers', side_effect=lambda r, s, p: p), \
             patch.object(studio, '_image_inputs', return_value=('a', [], None)), \
             patch.object(studio, '_normalize_rgb_image'), patch.object(studio, '_image_version'), \
             patch.object(visual, 'shared_runninghub_account_pool'), \
             patch.object(visual, '_render_poster_with_retry', side_effect=render):
            workers = []
            with patch.object(studio.threading, 'Thread', side_effect=lambda target, **kw: SimpleNamespace(start=lambda: workers.append(target))):
                studio._start_storyboard_images(Path(folder) / '7' / 'test', record, [{'cloud_pool': '1'}])
            workers[0]()
            self.assertFalse(client.gemini_configured())
        self.assertEqual(observed, [(True, 'auto', 'https://pool.example/model-pool/v1')] * 2)
        self.assertEqual(record['shots'][0]['image_status'], 'completed')

    def runtime(self, user_id):
        return SimpleNamespace(image_pool_runtime=lambda: {
            'base_url': 'https://pool.example', 'access_token': f'pool-token-{user_id}'})

    def test_pool_without_local_api_sends_pool_token_and_auto_model(self):
        response = Mock(ok=True)
        response.json.return_value = {'choices': [{'message': {'content': 'OK'}, 'finish_reason': 'stop'}]}
        with patch.dict(os.environ, {}, clear=True), \
             patch.object(routing, 'cloud_client_for', side_effect=self.runtime), \
             patch.object(client.requests, 'post', return_value=response) as post:
            self.assertFalse(client.gemini_configured())
            with routing.project_language_scope(3, {'use_cloud_image_pool': True}):
                self.assertTrue(client.gemini_configured())
                self.assertEqual(client.generate_gemini_text(system_prompt='s', user_prompt='u'), 'OK')
                self.assertNotIn('GEMINI_API_KEY', os.environ)
            self.assertFalse(client.gemini_configured())
        self.assertEqual(post.call_args.args[0], 'https://pool.example/model-pool/v1/chat/completions')
        self.assertEqual(post.call_args.kwargs['headers']['Authorization'], 'Bearer pool-token-3')
        self.assertEqual(post.call_args.kwargs['json']['model'], 'auto')

    def test_pool_overrides_personal_api_and_restores_after_exception(self):
        env = {'LANGUAGE_PROVIDER': 'custom', 'CUSTOM_LLM_BASE_URL': 'https://personal.example/v1',
               'CUSTOM_LLM_MODEL': 'personal', 'GEMINI_FALLBACK_MODELS': 'paid-personal-model'}
        with patch.dict(os.environ, env, clear=True), patch.object(routing, 'cloud_client_for', side_effect=self.runtime):
            before = dict(os.environ)
            with self.assertRaisesRegex(ValueError, 'test'):
                with routing.project_language_scope(1, {'use_cloud_image_pool': True}):
                    self.assertEqual(client._gemini_models(), ['auto'])
                    self.assertEqual(client.language_base_url(), 'https://pool.example/model-pool/v1')
                    raise ValueError('test')
            self.assertEqual(dict(os.environ), before)
            self.assertEqual(client._provider(), 'custom')

    def test_login_failure_never_calls_personal_api(self):
        with patch.object(routing, 'cloud_client_for', side_effect=RuntimeError('private error')), \
             patch.object(client.requests, 'post') as post:
            with self.assertRaisesRegex(RuntimeError, '未改用个人 API'):
                with routing.project_language_scope(1, {'use_cloud_image_pool': True}):
                    client.generate_gemini_text(system_prompt='s', user_prompt='u')
            post.assert_not_called()

    def test_personal_route_does_not_request_cloud_session(self):
        with patch.object(routing, 'cloud_client_for') as cloud:
            with routing.project_language_scope(1, {'use_cloud_image_pool': False}):
                pass
            cloud.assert_not_called()

    def test_concurrent_users_do_not_share_tokens_or_environment(self):
        barrier = threading.Barrier(2)
        def worker(user_id):
            with routing.project_language_scope(user_id, {'use_cloud_image_pool': True}):
                barrier.wait(timeout=5)
                return client._language_getenv('GEMINI_API_KEY')
        with patch.dict(os.environ, {}, clear=True), patch.object(routing, 'cloud_client_for', side_effect=self.runtime):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(worker, [1, 2]))
            self.assertEqual(results, ['pool-token-1', 'pool-token-2'])
            self.assertFalse(client.gemini_configured())

    def test_dynamic_plan_worker_enters_pool_route(self):
        from backend.app import video_studio as studio
        record = dict(id='test', revision=1, status='draft', shots=[], scenes=[], references=[],
                      settings=dict(style='', characters='', world=''), narration_groups=[],
                      creation_parameters={'use_cloud_image_pool': True}, logs=[])
        observed = []
        def planner(*args, **kwargs):
            observed.append((client.gemini_configured(), client.language_model(), client.language_base_url()))
            return {}, []
        with patch.dict(os.environ, {}, clear=True), \
             patch.object(routing, 'cloud_client_for', side_effect=self.runtime), \
             patch.object(studio, 'require_user', return_value={'id': 1}), \
             patch.object(studio, 'directory', return_value=Path('test-pool-project')), \
             patch.object(studio, 'read', side_effect=lambda _: copy.deepcopy(record)), \
             patch.object(studio, 'save'), patch.object(studio, 'ACTIVE', set()), \
             patch.object(studio, 'plan_storyboard', side_effect=planner), \
             patch.object(studio.threading, 'Thread', side_effect=lambda target, **kw: SimpleNamespace(start=target)):
            result = studio.plan('test', None)
        self.assertEqual(observed, [(True, 'auto', 'https://pool.example/model-pool/v1')])
        self.assertEqual(result['status'], 'storyboard_review')
        self.assertTrue(any('语言模型路由：云端号池' in message for message in result['logs']))

    def test_empty_fresh_replan_keeps_completed_steps_on_late_failure(self):
        from backend.app import video_studio as studio
        record = dict(id='test', revision=1, status='draft', shots=[], scenes=[], references=[],
                      settings=dict(style='', characters='', world=''), narration_groups=[],
                      creation_parameters={'use_cloud_image_pool': True}, logs=[])
        def planner(scenes, style, characters, world, references, progress, parameters, **kw):
            state = {'fingerprint': studio.planning_fingerprint(scenes, style, characters, world, references, parameters),
                     'context': {'ready': True}, 'stage': '核心画面设计 13～15', 'completed': {'core': ['a']}}
            kw['save_state'](state)
            raise ValueError('镜头末尾素材选择无效')
        with patch.dict(os.environ, {}, clear=True), \
             patch.object(routing, 'cloud_client_for', side_effect=self.runtime), \
             patch.object(studio, 'require_user', return_value={'id': 1}), \
             patch.object(studio, 'directory', return_value=Path('test-pool-project')), \
             patch.object(studio, 'read', side_effect=lambda _: copy.deepcopy(record)), \
             patch.object(studio, 'save'), patch.object(studio, 'ACTIVE', set()), \
             patch.object(studio, 'plan_storyboard', side_effect=planner), \
             patch.object(studio.threading, 'Thread', side_effect=lambda target, **kw: SimpleNamespace(start=target)):
            result = studio.plan('test', None, fresh=True, revision=1)
        self.assertTrue(result['planning_resume_available'])
        self.assertEqual(result['planning_state']['completed']['core'], ['a'])
        self.assertEqual(result['planning_state']['stage'], '核心画面设计 13～15')

    def test_image_pipeline_scope_also_leaves_environment_untouched(self):
        from backend.app.pipeline import cloud_model_pool_environment
        with patch.dict(os.environ, {}, clear=True), patch.object(routing, 'cloud_client_for', side_effect=self.runtime):
            with cloud_model_pool_environment(SimpleNamespace(user_id=1), Mock(), {'use_cloud_image_pool': True}):
                self.assertTrue(client.gemini_configured())
                self.assertNotIn('GEMINI_API_KEY', os.environ)
            self.assertFalse(client.gemini_configured())

    def test_dynamic_images_use_pool_not_saved_personal_image_profile(self):
        from backend.app import video_studio as studio
        import module4_video_render as visual
        record = {'settings': {'ratio': '9:16'}, 'creation_parameters': {
            'use_cloud_image_pool': True, 'image_resolution': '720p',
            'image_profile_snapshot': {'name': 'must not use personal image API'}}}
        original = copy.deepcopy(record)
        with patch('backend.app.cloud_client.cloud_client_for', side_effect=self.runtime), \
             patch.object(visual, '_provider_configs') as local, \
             patch('backend.app.image_profiles.profile_provider_configs') as profile:
            config = studio._image_configs(record, 2)[0]
        local.assert_not_called()
        profile.assert_not_called()
        self.assertEqual(config['endpoint'], 'https://pool.example/image-pool/generate')
        self.assertEqual(config['api_key'], 'pool-token-2')
        self.assertEqual((config['ratio'], config['resolution'], config['cloud_pool']), ('9:16', '720p', '1'))
        self.assertEqual(record, original)

    def test_image_pool_failure_does_not_fall_back(self):
        from backend.app import video_studio as studio
        import module4_video_render as visual
        with patch('backend.app.cloud_client.cloud_client_for', side_effect=RuntimeError('offline')), \
             patch.object(visual, '_provider_configs') as local:
            with self.assertRaisesRegex(ValueError, '未改用个人 API'):
                studio._image_configs({'creation_parameters': {'use_cloud_image_pool': True}}, 1)
            local.assert_not_called()
