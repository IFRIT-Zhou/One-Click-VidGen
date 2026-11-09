"""Fresh starts isolate generated work without deleting history or bypassing consent."""
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError

from backend.app import vibe_cloud as cloud, vibe_layered as layered
import test_vibe_agent_route as route_fixture
import test_vibe_layered_cache as cache_fixture


class FreshStartRouteTests(unittest.TestCase):
    persist = route_fixture.ToolAgentRouteTests.persist
    render = staticmethod(route_fixture.ToolAgentRouteTests.render)
    material = staticmethod(route_fixture.ToolAgentRouteTests.material)
    start_patch = route_fixture.ToolAgentRouteTests.start_patch
    inventory_response = route_fixture.ToolAgentRouteTests.inventory_response
    start_v2 = route_fixture.ToolAgentRouteTests.start_v2
    finish_pipeline = route_fixture.ToolAgentRouteTests.finish_pipeline
    setUp = route_fixture.ToolAgentRouteTests.setUp

    def prior_task(self, *, pending=False):
        task = self.start_v2(budget=2, max_model_calls=30)
        cloud._active.clear()
        path = cloud._job(self.project, task['task_id'])
        cloud._update(path, status='failed', stage='agent_review',
            usage={'model_calls': [{'status': 'completed'}] * 30,
                   'image_requests': 2, 'image_task_ids': ['c' * 32]},
            preview={'preview_id': 'old-preview'}, message='old review')
        cloud._write(path.parent / 'agent-state.json', {
            'latest_code': 'old DS code', 'delivery_reviews': {'old-preview': {'passed': False}},
            'workspace': {'background': 'old generated material'},
        })
        self.agent_recoverable.return_value = pending
        return cloud._read_job(self.project, task['task_id'])

    def assert_no_generation(self):
        self.model.assert_not_called()
        self.agent_worker.assert_not_called()
        cloud.generate_asset.assert_not_called()

    def test_standard_default_preserves_legacy_request_contract(self):
        data = cloud.CloudRequest(confirm_cloud=True, template_mode='layered', max_image_requests=0)
        self.assertEqual(data.start_mode, 'standard')
        queued = self.start_v2()
        self.assertEqual(queued['start_mode'], 'standard')
        self.assertEqual(queued['usage']['model_calls'], [])
        self.assertNotIn('彻底重新开始', queued['message'])

    def test_fresh_is_incompatible_with_explicit_old_material_reuse(self):
        with self.assertRaisesRegex(ValidationError, '不能同时指定 reuse_task_id'):
            cloud.CloudRequest(confirm_cloud=True, template_mode='layered',
                max_image_requests=0, start_mode='fresh', reuse_task_id='a' * 32)
        with self.assertRaises(ValidationError):
            cloud.CloudRequest(confirm_cloud=True, start_mode='restart')
        self.assert_no_generation()

    def test_fresh_persists_new_identity_zero_usage_and_only_original_inputs(self):
        previous = self.prior_task()
        old_path = cloud._job(self.project, previous['task_id'])
        old_files = {p.name: p.read_bytes() for p in old_path.parent.iterdir() if p.is_file()}
        record = (self.project / 'record.json').read_bytes()
        ocr = self.project / 'assets' / 'vibe_measurement_cache' / 'saved-ocr.json'
        ocr.parent.mkdir(parents=True, exist_ok=True)
        cloud._write(ocr, {'source_sha256': 'immutable', 'text': 'cached original OCR'})
        ocr_bytes = ocr.read_bytes()

        queued = self.start_v2(budget=2, max_model_calls=20, start_mode='fresh')
        self.assertNotEqual(queued['task_id'], previous['task_id'])
        self.assertEqual(queued['start_mode'], 'fresh')
        self.assertEqual(queued['previous_task_id'], previous['task_id'])
        self.assertEqual(queued['cloud_input_stamp'], previous['cloud_input_stamp'])
        self.assertEqual(queued['max_model_calls'], 20)
        self.assertEqual(queued['max_image_requests'], 2)
        self.assertEqual(queued['usage'], {'model_calls': [], 'image_requests': 0, 'image_task_ids': []})
        self.assertIsNone(queued['reuse_task_id'])
        self.assertNotIn('preview', queued)
        self.assertIn('彻底重新开始', queued['message'])
        self.assertEqual(self.workers[-1].args[4].start_mode, 'fresh')
        new_folder = cloud._job(self.project, queued['task_id']).parent
        self.assertEqual([p.name for p in new_folder.iterdir()], ['task.json'])
        self.assertEqual(old_files, {p.name: p.read_bytes() for p in old_path.parent.iterdir() if p.is_file()})
        self.assertEqual(record, (self.project / 'record.json').read_bytes())
        self.assertEqual(ocr_bytes, ocr.read_bytes())
        self.assert_no_generation()

    def test_fresh_without_prior_task_can_start_with_zero_image_budget(self):
        queued = self.start_v2(start_mode='fresh', budget=0)
        self.assertIsNone(queued['previous_task_id'])
        self.assertEqual(queued['max_image_requests'], 0)
        self.assertEqual(queued['usage']['image_requests'], 0)
        self.assert_no_generation()

    def test_fresh_pending_same_route_requires_confirmation_even_with_zero_budget(self):
        previous = self.prior_task(pending=True)
        old = cloud._job(self.project, previous['task_id']).read_bytes()
        pointer = cloud._pointer(self.project, 'shot1').read_bytes()
        for budget in (0, 2):
            with self.subTest(budget=budget), patch('backend.app.vibe_image_route.same_pending_route', return_value=True):
                with self.assertRaises(HTTPException) as error:
                    self.start_v2(start_mode='fresh', budget=budget)
                self.assertEqual(error.exception.status_code, 409)
                self.assertIn('不会取消旧任务', error.exception.detail)
                self.assertEqual(pointer, cloud._pointer(self.project, 'shot1').read_bytes())
                self.assertEqual(old, cloud._job(self.project, previous['task_id']).read_bytes())
        self.assert_no_generation()

    def test_confirmed_fresh_can_bypass_pending_same_route_without_cancelling_old_task(self):
        previous = self.prior_task(pending=True)
        old = cloud._job(self.project, previous['task_id']).read_bytes()
        with patch('backend.app.vibe_image_route.same_pending_route', return_value=True):
            queued = self.start_v2(start_mode='fresh', budget=0, confirm_new_image_task=True)
        self.assertEqual(queued['previous_task_id'], previous['task_id'])
        self.assertEqual(queued['max_image_requests'], 0)
        self.assertEqual(queued['usage']['image_requests'], 0)
        self.assertEqual(old, cloud._job(self.project, previous['task_id']).read_bytes())
        self.assert_no_generation()

    def test_fresh_pending_old_inputs_still_requires_explicit_confirmation(self):
        previous = self.prior_task(pending=True)
        cloud._update(cloud._job(self.project, previous['task_id']), cloud_input_stamp='old-input-stamp')
        with self.assertRaises(HTTPException) as error:
            self.start_v2(start_mode='fresh', budget=0)
        self.assertEqual(error.exception.status_code, 409)
        self.assertIn('明确确认', error.exception.detail)
        self.assert_no_generation()

    def test_standard_pending_same_route_stays_blocked_even_with_confirmation(self):
        self.prior_task(pending=True)
        with patch('backend.app.vibe_image_route.same_pending_route', return_value=True):
            with self.assertRaises(HTTPException) as error:
                self.start_v2(budget=2, confirm_new_image_task=True)
        self.assertEqual(error.exception.status_code, 409)
        self.assertIn('继续原素材任务', error.exception.detail)
        self.assert_no_generation()

    def test_standard_changed_route_still_requires_confirmation_and_can_then_start(self):
        self.prior_task(pending=True)
        with patch('backend.app.vibe_image_route.same_pending_route', return_value=False):
            with self.assertRaises(HTTPException) as error:
                self.start_v2(budget=2)
            self.assertEqual(error.exception.status_code, 409)
            queued = self.start_v2(budget=2, confirm_new_image_task=True)
        self.assertEqual(queued['start_mode'], 'standard')
        self.assert_no_generation()

    def test_active_task_blocks_confirmed_fresh_and_is_not_cancelled(self):
        first = self.start_v2(budget=0)
        current = cloud._active[(str(self.project), 'shot1')]
        old_pointer = cloud._pointer(self.project, 'shot1').read_bytes()
        with self.assertRaises(HTTPException) as error:
            self.start_v2(start_mode='fresh', budget=0, confirm_new_image_task=True)
        self.assertEqual(error.exception.status_code, 409)
        self.assertIn('正在运行', error.exception.detail)
        self.assertIs(cloud._active[(str(self.project), 'shot1')], current)
        self.assertFalse(current[1].is_set())
        self.assertEqual(old_pointer, cloud._pointer(self.project, 'shot1').read_bytes())
        self.assertEqual(current[0], first['task_id'])
        self.assert_no_generation()


class FreshStartBackgroundCacheTests(unittest.TestCase):
    setUp = cache_fixture.LayeredBackgroundCacheTests.setUp
    job = cache_fixture.LayeredBackgroundCacheTests.job
    generated_file = cache_fixture.LayeredBackgroundCacheTests.generated_file
    generate = cache_fixture.LayeredBackgroundCacheTests.generate
    resume = cache_fixture.LayeredBackgroundCacheTests.resume
    cache = cache_fixture.LayeredBackgroundCacheTests.cache
    background = cache_fixture.LayeredBackgroundCacheTests.background
    ready = cache_fixture.LayeredBackgroundCacheTests.ready

    def make_fresh(self):
        cloud._update(cloud._job(self.project, self.current), start_mode='fresh')

    def test_fresh_does_not_reuse_matching_previous_task_background(self):
        previous, source = self.cache()
        old_bytes = source.read_bytes()
        self.make_fresh()
        task = cloud._read_job(self.project, self.current)
        result = layered._cached_background(self.project, task, self.sources,
            self.route, self.event, recipe=layered._background_recipe(self.design))
        self.assertIsNone(result)
        self.assertEqual(source.read_bytes(), old_bytes)
        self.assertEqual(self.ready(previous)['task_id'], previous)
        self.generated.assert_not_called()

    def test_fresh_zero_budget_cannot_use_old_background_or_submit_new_image(self):
        self.cache()
        self.make_fresh()
        with self.assertRaises(ValueError):
            self.background(budget=0)
        self.generated.assert_not_called()
        self.resumed.assert_not_called()

    def test_fresh_requests_own_background_even_when_old_recipe_matches(self):
        previous, _ = self.cache()
        self.make_fresh()
        result = self.background(budget=1)
        self.assertTrue(result.is_file())
        self.assertEqual(self.generated.call_count, 1)
        self.assertEqual(self.ready()['origin_task_id'], self.current)
        self.assertNotEqual(self.ready()['origin_task_id'], previous)
        self.assertEqual(cloud._read_job(self.project, self.current)['usage']['image_requests'], 1)

    def test_fresh_can_reuse_own_completed_edit_without_new_request_or_copy_collision(self):
        self.make_fresh()
        result = self.background(budget=1)
        first_bytes = result.read_bytes()
        recipe = layered._background_recipe(self.design)
        current = cloud._read_job(self.project, self.current)
        reused = layered._cached_background(self.project, current, self.sources,
            self.route, self.event, recipe=recipe)
        self.assertEqual(reused[2], self.current)
        self.assertEqual(reused[0], result)
        again = self.background(budget=0)
        self.assertEqual(again, result)
        self.assertEqual(again.read_bytes(), first_bytes)
        self.assertEqual(self.generated.call_count, 1)
        self.resumed.assert_not_called()

    def test_fresh_own_cache_still_requires_recipe_and_hash_binding(self):
        self.make_fresh()
        result = self.background()
        task = cloud._read_job(self.project, self.current)
        changed = self.design.model_copy(update={'background_notes': '保留不同装饰'})
        self.assertIsNone(layered._cached_background(self.project, task, self.sources,
            self.route, self.event, recipe=layered._background_recipe(changed)))
        result.write_bytes(b'corrupt owned image')
        self.assertIsNone(layered._cached_background(self.project, task, self.sources,
            self.route, self.event, recipe=layered._background_recipe(self.design)))
        self.assertEqual(self.generated.call_count, 1)

    def test_standard_and_legacy_tasks_keep_cross_task_cache(self):
        previous, source = self.cache()
        for mode in (None, 'standard'):
            task = cloud._read_job(self.project, self.current)
            if mode is not None:
                task['start_mode'] = mode
            with self.subTest(mode=mode):
                result = layered._cached_background(self.project, task, self.sources,
                    self.route, self.event, recipe=layered._background_recipe(self.design))
                self.assertEqual(result[2], previous)
                self.assertEqual(result[0], source)
        self.generated.assert_not_called()


if __name__ == '__main__':
    unittest.main()
