import copy
import io
import tempfile
import threading
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import video_studio


class VideoSceneGenerationTest(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(video_studio, 'ROOT', self.root))
        self.stack.enter_context(patch.object(video_studio, 'require_user', return_value={'id': 'scene-test'}))
        self.stack.enter_context(patch.object(video_studio, '_image_configs', return_value=[
            {'api_key': 'test-one'}, {'api_key': 'test-two'}, {'api_key': 'test-three'},
        ]))
        self.stack.enter_context(patch('module4_video_render.shared_runninghub_account_pool', return_value=object()))
        self.stack.enter_context(patch('module4_video_render._positive_env_int', return_value=1))
        self.plan = {'fingerprint': 'planned-scene-fixture', 'scenes': [{
            'scene_id': 'location_1', 'name': '会场', 'members': [0, 1],
            'reference_prompt': '简笔手绘无人会场，木质讲台，灰色观众席。',
            'reason': '两个核心图位于同一个会场，第三图已经转到医院。',
        }]}
        self.planner = self.stack.enter_context(patch.object(
            video_studio.scene_references, 'plan_references',
            side_effect=lambda *_: copy.deepcopy(self.plan),
        ))
        self.renderer = self.stack.enter_context(patch(
            'module4_video_render._render_poster_with_retry', side_effect=self._write_image,
        ))
        app = FastAPI()
        app.include_router(video_studio.router)
        self.client = self.stack.enter_context(TestClient(app))
        self.record = {
            'id': 'a' * 32, 'revision': 1, 'status': 'generation_ready', 'logs': [], 'error': '',
            'creation_parameters': {'director_strategy': 'enhanced_beta', 'scene_references_enabled': True},
            'settings': {'style': '简笔火柴人', 'world': '会场', 'ratio': '16:9'},
            'scenes': [],
            'references': [{'id': 'ref_host', 'file': 'assets/references/host.jpg'}],
            'shots': [
                {'id': 'shot0', 'image_prompt': '主讲人在会场提问', 'reference_ids': ['ref_host']},
                {'id': 'shot1', 'image_prompt': '会场的观众紧张回应', 'reference_ids': []},
                {'id': 'shot2', 'image_prompt': '医院病床上的老人', 'reference_ids': []},
            ],
        }
        self.path = video_studio.directory('scene-test', self.record['id'])
        reference = self.path / 'assets/references/host.jpg'
        reference.parent.mkdir(parents=True)
        reference.write_bytes(b'host-reference')
        video_studio.save(self.path, self.record)
        self.url = '/api/video-studio/' + self.record['id']

    def tearDown(self):
        self._wait_for_worker()

    @staticmethod
    def _write_image(macro, _pool):
        target = Path(macro['_output_path'])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(('image:' + macro['macro_scene_id']).encode())
        return target

    def _wait_for_worker(self):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with video_studio.LOCK:
                active = (str(self.path) in video_studio.ACTIVE or
                          self.record['id'] in video_studio.IMAGE_EDITS)
            if not active:
                return
            time.sleep(.01)
        self.fail('Mocked image worker did not finish')

    def _start(self, endpoint='/images/generate'):
        current = self.client.get(self.url).json()
        response = self.client.post(self.url + endpoint, json={'revision': current['revision']})
        self.assertEqual(response.status_code, 200, response.text)
        self._wait_for_worker()
        return self.client.get(self.url).json()

    def test_scene_precedes_parallel_core_images_and_only_related_shots_receive_it(self):
        submitted = []
        guard = threading.Lock()
        active = peak = 0

        def render(macro, pool):
            nonlocal active, peak
            is_scene = macro['macro_scene_id'].startswith('scene_ref_')
            with guard:
                submitted.append(copy.deepcopy(macro))
                if not is_scene:
                    active += 1
                    peak = max(peak, active)
            if not is_scene:
                time.sleep(.05)
            result = self._write_image(macro, pool)
            with guard:
                if not is_scene:
                    active -= 1
            return result

        self.renderer.side_effect = render
        record = self._start()
        self.assertEqual(record['status'], 'image_review')
        self.assertEqual(record['scene_references_status'], 'completed')
        self.assertEqual(len(record['scene_assets']), 1)
        scene = record['scene_assets'][0]
        self.assertEqual(scene['image_status'], 'completed')
        self.assertEqual(scene['used_by'], ['shot0', 'shot1'])
        self.assertEqual(submitted[0]['macro_scene_id'], scene['id'])
        self.assertGreaterEqual(peak, 2)
        self.assertEqual(len(submitted), 4)
        by_id = {macro['macro_scene_id']: macro for macro in submitted}
        scene_path = str((self.path / scene['image']).resolve())
        host_path = str((self.path / 'assets/references/host.jpg').resolve())
        self.assertEqual(by_id['shot0']['reference_image_paths'], [host_path, scene_path])
        self.assertIn('【场景参考】图2', by_id['shot0']['image_prompt'])
        self.assertEqual(by_id['shot1']['reference_image_paths'], [scene_path])
        self.assertIn('【场景参考】图1', by_id['shot1']['image_prompt'])
        self.assertEqual(by_id['shot2']['reference_image_paths'], [])
        self.assertNotIn('【场景参考】', by_id['shot2']['image_prompt'])
        self.assertNotIn('scene_reference_id', record['shots'][2])
        for shot in record['shots'][:2]:
            self.assertEqual(shot['scene_reference_id'], scene['id'])
            self.assertEqual(shot['scene_reference_used_version'], scene['image_version'])
        self.assertTrue(all(shot['image_status'] == 'completed' for shot in record['shots']))

    def test_retry_generates_only_failed_core_and_reuses_scene_and_successful_cores(self):
        fail_once = {'shot1'}

        def render(macro, pool):
            if macro['macro_scene_id'] in fail_once:
                fail_once.remove(macro['macro_scene_id'])
                raise ValueError('模拟单镜失败')
            return self._write_image(macro, pool)

        self.renderer.side_effect = render
        first = self._start()
        self.assertEqual([shot['image_status'] for shot in first['shots']],
                         ['completed', 'failed', 'completed'])
        scene = first['scene_assets'][0]
        preserved = {str(self.path / item['image']): (self.path / item['image']).read_bytes()
                     for item in [scene, first['shots'][0], first['shots'][2]]}
        self.renderer.reset_mock()
        retried = self._start()
        self.assertEqual(self.renderer.call_count, 1)
        self.assertEqual(self.renderer.call_args.args[0]['macro_scene_id'], 'shot1')
        self.assertEqual(retried['scene_assets'][0]['id'], scene['id'])
        self.assertEqual(retried['scene_assets'][0]['image_version'], scene['image_version'])
        self.assertTrue(all(shot['image_status'] == 'completed' for shot in retried['shots']))
        for filename, content in preserved.items():
            self.assertEqual(Path(filename).read_bytes(), content)

    def test_scene_only_endpoint_adds_assets_without_repainting_existing_core_images(self):
        self.record['status'] = 'image_review'
        originals = {}
        for shot in self.record['shots']:
            shot.update(image_status='completed', image=f'assets/storyboards/{shot["id"]}.jpg')
            target = self.path / shot['image']
            target.parent.mkdir(parents=True, exist_ok=True)
            originals[shot['id']] = ('original-' + shot['id']).encode()
            target.write_bytes(originals[shot['id']])
        video_studio.save(self.path, self.record)
        record = self._start('/scene-references/generate')
        self.assertEqual(record['status'], 'image_review')
        self.assertEqual(record['scene_references_status'], 'completed')
        self.assertEqual(len(record['scene_assets']), 1)
        self.assertEqual(self.renderer.call_count, 1)
        self.assertTrue(self.renderer.call_args.args[0]['macro_scene_id'].startswith('scene_ref_'))
        for shot in record['shots']:
            self.assertEqual((self.path / shot['image']).read_bytes(), originals[shot['id']])
            self.assertEqual(shot['image_status'], 'completed')
        self.assertEqual(record['shots'][0]['scene_reference_id'], record['scene_assets'][0]['id'])
        self.assertNotIn('scene_reference_id', record['shots'][2])
        self.renderer.reset_mock()
        repeated = self._start('/scene-references/generate')
        self.renderer.assert_not_called()
        self.assertEqual(repeated['scene_assets'][0]['id'], record['scene_assets'][0]['id'])

    def test_scene_failure_prevents_core_submission_and_remains_retryable(self):
        self.renderer.side_effect = ValueError('模拟场景接口失败')
        failed = self._start()
        self.assertEqual(failed['status'], 'image_review')
        self.assertEqual(failed['scene_references_status'], 'failed')
        self.assertEqual(failed['scene_assets'][0]['image_status'], 'failed')
        self.assertIn('模拟场景接口失败', failed['error'])
        self.assertEqual(self.renderer.call_count, 1)
        self.assertTrue(self.renderer.call_args.args[0]['macro_scene_id'].startswith('scene_ref_'))
        self.assertTrue(all(shot.get('image_status') != 'completed' for shot in failed['shots']))
        self.renderer.reset_mock()
        self.renderer.side_effect = self._write_image
        recovered = self._start()
        self.assertEqual(recovered['status'], 'image_review')
        self.assertEqual(recovered['scene_references_status'], 'completed')
        self.assertEqual(recovered['error'], '')
        self.assertEqual(self.renderer.call_count, 4)
        self.assertTrue(all(shot['image_status'] == 'completed' for shot in recovered['shots']))

    def test_disabled_scene_reference_skips_planner_and_only_generates_core_images(self):
        self.record['creation_parameters']['scene_references_enabled'] = False
        video_studio.save(self.path, self.record)
        record = self._start()
        self.planner.assert_not_called()
        self.assertEqual(record['scene_references_status'], 'disabled')
        self.assertEqual(self.renderer.call_count, 3)
        self.assertEqual({call.args[0]['macro_scene_id'] for call in self.renderer.call_args_list},
                         {'shot0', 'shot1', 'shot2'})
        self.assertFalse(record.get('scene_assets'))
        self.assertTrue(all(shot['image_status'] == 'completed' for shot in record['shots']))

    def test_scene_replacement_changes_future_redraw_references_and_undo_restores_asset(self):
        from PIL import Image

        record = self._start()
        scene = record['scene_assets'][0]
        scene_id = scene['id']
        scene_path = self.path / scene['image']
        original_scene = scene_path.read_bytes()
        original_version = scene['image_version']
        original_core_images = {shot['id']: (self.path / shot['image']).read_bytes()
                                for shot in record['shots']}
        replacement = io.BytesIO()
        Image.new('RGB', (6, 6), '#2277bb').save(replacement, 'PNG')
        response = self.client.post(self.url + f'/images/{scene_id}/upload', data={
            'revision': record['revision'], 'prompt': '调整后的无人会场布景',
        }, files={'file': ('new-scene.png', replacement.getvalue(), 'image/png')})
        self.assertEqual(response.status_code, 200, response.text)
        record = response.json()
        replaced_scene = record['scene_assets'][0]
        replacement_version = replaced_scene['image_version']
        self.assertEqual(replaced_scene['id'], scene_id)
        self.assertNotEqual(replacement_version, original_version)
        self.assertNotEqual(scene_path.read_bytes(), original_scene)
        self.assertEqual(len(replaced_scene['image_history']), 1)
        for shot in record['shots']:
            self.assertEqual((self.path / shot['image']).read_bytes(), original_core_images[shot['id']])
        for shot in record['shots'][:2]:
            self.assertEqual(shot['scene_reference_used_version'], original_version)

        captured = []

        def render(macro, pool):
            captured.append({**copy.deepcopy(macro), 'reference_contents': [
                Path(filename).read_bytes() for filename in macro['reference_image_paths']
            ]})
            return self._write_image(macro, pool)

        self.renderer.side_effect = render
        new_scene_bytes = scene_path.read_bytes()
        response = self.client.post(self.url + '/images/shot0/redraw', json={
            'revision': record['revision'], 'prompt': '主讲人在调整后的会场提问',
        })
        self.assertEqual(response.status_code, 200, response.text)
        self._wait_for_worker()
        record = self.client.get(self.url).json()
        self.assertEqual(record['shots'][0]['image_task']['status'], 'completed')
        self.assertEqual(record['shots'][0]['scene_reference_used_version'], replacement_version)
        self.assertEqual(captured[0]['reference_image_paths'], [
            str((self.path / 'assets/references/host.jpg').resolve()), str(scene_path.resolve()),
        ])
        self.assertEqual(captured[0]['reference_contents'][-1], new_scene_bytes)
        self.assertEqual(record['shots'][1]['scene_reference_used_version'], original_version)

        response = self.client.post(self.url + '/images/shot2/redraw', json={
            'revision': record['revision'], 'prompt': '调整医院病床构图',
        })
        self.assertEqual(response.status_code, 200, response.text)
        self._wait_for_worker()
        record = self.client.get(self.url).json()
        self.assertEqual(captured[1]['reference_image_paths'], [])
        self.assertEqual(record['shots'][2]['scene_reference_used_version'], '')
        response = self.client.post(self.url + f'/images/{scene_id}/undo', json={
            'revision': record['revision'],
        })
        self.assertEqual(response.status_code, 200, response.text)
        restored = response.json()
        self.assertEqual(restored['scene_assets'][0]['id'], scene_id)
        self.assertEqual(restored['scene_assets'][0]['image_version'], original_version)
        self.assertEqual(scene_path.read_bytes(), original_scene)
        self.assertEqual(restored['shots'][0]['scene_reference_used_version'], replacement_version)


if __name__ == '__main__':
    unittest.main()
