"""V4 routing, persistent authorization and legacy compatibility; no paid work."""
import copy
import sys
from types import ModuleType
import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from backend.app import vibe_cloud as cloud
from backend.app import vibe_layered as layered
from backend.app.vibe_agent_contract import VERSION
import test_vibe_layered_v2_route as fixture


class ToolAgentRouteTests(unittest.TestCase):
    persist = fixture.LayeredV2RouteTests.persist
    render = staticmethod(fixture.LayeredV2RouteTests.render)
    material = staticmethod(fixture.LayeredV2RouteTests.material)
    start_patch = fixture.LayeredV2RouteTests.start_patch
    inventory_response = fixture.LayeredV2RouteTests.inventory_response
    start_v2 = fixture.LayeredV2RouteTests.start_v2
    finish_pipeline = fixture.LayeredV2RouteTests.finish_pipeline

    def setUp(self):
        fixture.LayeredV2RouteTests.setUp(self)
        self.start_patch('backend.app.vibe_layered.WORKFLOW_VERSION', VERSION)
        self.agent_worker = MagicMock()
        self.agent_recoverable = MagicMock(return_value=False)
        self.code_preflight = MagicMock(return_value={'renderer': 'ocv-remotion-code-v1'})
        agent = ModuleType('backend.app.vibe_agent_workflow')
        agent.run_worker = self.agent_worker
        agent.recoverable_material = self.agent_recoverable
        renderer = ModuleType('backend.app.vibe_code_renderer')
        renderer.preflight = self.code_preflight
        modules = patch.dict(sys.modules, {agent.__name__: agent, renderer.__name__: renderer})
        modules.start()
        self.addCleanup(modules.stop)

    def test_new_layered_task_persists_budgets_and_dispatches_without_old_planning(self):
        before = (self.project/'record.json').read_bytes()
        queued = self.start_v2(budget=3, max_model_calls=24, instruction='自己看图并返工')
        self.assertEqual(queued['workflow_version'], VERSION)
        self.assertEqual(queued['max_image_requests'], 3)
        self.assertEqual(queued['max_model_calls'], 24)
        self.assertIs(queued['advanced_subject_separation'], False)
        self.assertIn('复杂主体保留底图', queued['message'])
        self.assertIs(cloud._read_job(self.project, queued['task_id'])['advanced_subject_separation'], False)
        self.assertEqual(queued['renderer'], 'ocv-remotion-code-v1')
        self.code_preflight.assert_called_once_with(cancelled=None)
        worker = self.workers[-1]
        worker.target(*worker.args)
        args = self.agent_worker.call_args.args
        self.assertEqual(args[:3], (self.project, 'shot1', queued['task_id']))
        self.assertEqual(args[4].max_model_calls, 24)
        self.assertEqual(args[4].max_image_requests, 3)
        self.assertIs(args[4].advanced_subject_separation, False)
        self.assertEqual(self.agent_worker.call_args.kwargs, {'resume': False})
        self.assertEqual((self.project/'record.json').read_bytes(), before)
        self.model.assert_not_called()
        self.pipeline.assert_not_called()
        cloud.generate_asset.assert_not_called()

    def test_advanced_subject_separation_is_explicit_and_persisted_for_worker(self):
        queued = self.start_v2(budget=0, advanced_subject_separation=True)
        self.assertIs(queued['advanced_subject_separation'], True)
        self.assertIn('进阶主体分离', queued['message'])
        self.assertIs(cloud._read_job(self.project, queued['task_id'])['advanced_subject_separation'], True)
        self.assertIs(self.workers[-1].args[4].advanced_subject_separation, True)
        self.model.assert_not_called()
        cloud.generate_asset.assert_not_called()

    def test_new_task_persists_default_model_budget_and_explicit_image_budget(self):
        queued = self.start_v2(budget=2)
        self.assertEqual(queued['max_model_calls'], 30)
        self.assertEqual(queued['max_image_requests'], 2)
        saved = cloud._read_job(self.project, queued['task_id'])
        self.assertEqual(saved['max_model_calls'], 30)
        self.assertEqual(saved['max_image_requests'], 2)
        self.assertEqual(self.workers[-1].args[4].max_model_calls, 30)
        self.assertEqual(self.workers[-1].args[4].max_image_requests, 2)
        self.model.assert_not_called()
        cloud.generate_asset.assert_not_called()

    def test_preflight_failure_starts_neither_task_nor_paid_work(self):
        self.code_preflight.side_effect = RuntimeError('代码渲染工具缺失')
        with self.assertRaises(HTTPException) as failed:
            self.start_v2(budget=1)
        self.assertEqual(failed.exception.status_code, 409)
        self.assertFalse(self.workers)
        self.assertFalse(cloud._active)
        self.agent_worker.assert_not_called()
        cloud.generate_asset.assert_not_called()

    def test_completed_dispatch_does_not_remove_a_replacement_active_task(self):
        queued = self.start_v2(budget=0)
        replacement = ('f'*32, object())
        key = (str(self.project), 'shot1')
        def replaced(*args, **kwargs):
            cloud._active[key] = replacement
        self.agent_worker.side_effect = replaced
        worker = self.workers[-1]
        worker.target(*worker.args)
        self.assertNotEqual(queued['task_id'], replacement[0])
        self.assertIs(cloud._active[key], replacement)

    def test_recoverability_is_version_dispatched(self):
        task = {'workflow_version': VERSION, 'status': 'failed', 'task_id': 'x'}
        self.agent_recoverable.return_value = True
        self.assertTrue(layered.recoverable_material(self.project, task, 1))
        self.agent_recoverable.assert_called_once_with(self.project, task, 1)
        self.agent_recoverable.reset_mock()
        for version in (None, layered.MATERIAL_WORKFLOW_VERSION, layered.CODE_WORKFLOW_VERSION):
            self.assertFalse(layered.recoverable_material(self.project, {'workflow_version': version}, 1))
        self.agent_recoverable.assert_not_called()

    def test_resume_preserves_job_studio_route_and_total_budgets(self):
        queued = self.start_v2(budget=4, max_model_calls=20, instruction='保留底图')
        cloud._active.clear()
        path = cloud._job(self.project, queued['task_id'])
        usage = {'model_calls': [{'status': 'completed'}]*5,
                 'image_requests': 2, 'image_task_ids': ['original-paid-image-task']}
        original_route = copy.deepcopy(queued['image_route'])
        cloud._update(path, status='failed', stage='materials', usage=usage)
        self.agent_recoverable.return_value = True
        with patch('backend.app.vibe_image_route.resolve', side_effect=AssertionError('must not switch image route')):
            resumed = cloud.resume_materials(self.identity, 'shot1', queued['task_id'], self.req,
                                            cloud.ResumeMaterialsRequest(confirm_model=True))
        self.assertEqual(resumed['task_id'], queued['task_id'])
        self.assertEqual(resumed['usage'], usage)
        self.assertEqual(resumed['image_route'], original_route)
        self.assertEqual(resumed['max_model_calls'], 20)
        self.assertEqual(resumed['max_image_requests'], 4)
        worker = self.workers[-1]
        self.assertEqual(worker.args[3]['layered_image_route'], original_route)
        self.assertIs(worker.args[3]['new_images_allowed'], False)
        self.assertEqual(worker.args[4].max_model_calls, 20)
        self.assertEqual(worker.args[4].max_image_requests, 4)
        worker.target(*worker.args)
        self.assertEqual(self.agent_worker.call_args.kwargs, {'resume': True})
        cloud.generate_asset.assert_not_called()
        self.model.assert_not_called()

    def test_resume_still_needs_explicit_model_consent(self):
        queued = self.start_v2(budget=1)
        cloud._active.clear()
        path = cloud._job(self.project, queued['task_id'])
        cloud._update(path, status='failed', stage='materials')
        self.agent_recoverable.return_value = True
        before = path.read_bytes()
        with self.assertRaises(HTTPException) as denied:
            cloud.resume_materials(self.identity, 'shot1', queued['task_id'], self.req)
        self.assertEqual(denied.exception.status_code, 409)
        self.assertEqual(path.read_bytes(), before)
        self.agent_worker.assert_not_called()

    def test_resume_missing_legacy_budgets_keeps_16_and_1_not_new_defaults(self):
        queued = self.start_v2(budget=1, max_model_calls=16)
        cloud._active.clear()
        path = cloud._job(self.project, queued['task_id'])
        old = cloud._read_job(self.project, queued['task_id'])
        old.update(status='failed', stage='materials')
        old.pop('max_model_calls')
        old.pop('max_image_requests')
        cloud._write(path, old)
        self.agent_recoverable.return_value = True
        resumed = cloud.resume_materials(self.identity, 'shot1', queued['task_id'], self.req,
                                        cloud.ResumeMaterialsRequest(confirm_model=True))
        self.assertEqual(resumed['task_id'], queued['task_id'])
        self.assertNotIn('max_model_calls', resumed)
        self.assertNotIn('max_image_requests', resumed)
        worker = self.workers[-1]
        self.assertEqual(worker.args[4].max_model_calls, 16)
        self.assertEqual(worker.args[4].max_image_requests, 1)
        self.assertIs(worker.args[3]['new_images_allowed'], False)
        self.model.assert_not_called()
        cloud.generate_asset.assert_not_called()

    def test_resume_keeps_previous_32_call_ceiling_without_expanding_authority(self):
        queued = self.start_v2(budget=2, max_model_calls=32)
        cloud._active.clear()
        path = cloud._job(self.project, queued['task_id'])
        cloud._update(path, status='failed', stage='materials')
        self.agent_recoverable.return_value = True
        resumed = cloud.resume_materials(self.identity, 'shot1', queued['task_id'], self.req,
                                        cloud.ResumeMaterialsRequest(confirm_model=True))
        self.assertEqual(resumed['max_model_calls'], 32)
        self.assertEqual(resumed['max_image_requests'], 2)
        self.assertEqual(self.workers[-1].args[4].max_model_calls, 32)
        self.assertEqual(self.workers[-1].args[4].max_image_requests, 2)
        self.assertEqual(cloud._read_job(self.project, queued['task_id'])['max_model_calls'], 32)
        self.model.assert_not_called()
        cloud.generate_asset.assert_not_called()

    def test_resume_preserves_stable_advanced_and_missing_legacy_policy(self):
        for original, expected in ((False, False), (True, True), ('legacy_missing', True)):
            with self.subTest(original=original):
                cloud._active.clear()
                self.agent_recoverable.return_value = False
                queued = self.start_v2(budget=0, advanced_subject_separation=original is True)
                cloud._active.clear()
                path = cloud._job(self.project, queued['task_id'])
                old = cloud._read_job(self.project, queued['task_id'])
                old.update(status='failed', stage='materials')
                if original == 'legacy_missing':
                    old.pop('advanced_subject_separation')
                cloud._write(path, old)
                self.agent_recoverable.return_value = True
                resumed = cloud.resume_materials(self.identity, 'shot1', queued['task_id'], self.req,
                                                cloud.ResumeMaterialsRequest(confirm_model=True))
                self.assertEqual(resumed['task_id'], queued['task_id'])
                self.assertIs(resumed['advanced_subject_separation'], expected)
                self.assertIn('进阶主体分离' if expected else '复杂主体保留底图', resumed['message'])
                self.assertIs(cloud._read_job(self.project, queued['task_id'])['advanced_subject_separation'], expected)
                worker = self.workers[-1]
                self.assertIs(worker.args[4].advanced_subject_separation, expected)
                self.assertIs(worker.args[3]['new_images_allowed'], False)
                self.assertEqual(resumed['usage'], old['usage'])
        self.model.assert_not_called()
        cloud.generate_asset.assert_not_called()

    def test_resume_request_cannot_override_persisted_subject_policy(self):
        for value in (False, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                cloud.ResumeMaterialsRequest(confirm_model=True, advanced_subject_separation=value)

    def test_agent_stage_failure_names_model_call_and_persists_usage(self):
        queued = self.start_v2(budget=0)
        path = cloud._job(self.project, queued['task_id'])
        for stage, label in (('agent_plan', '自主设计与工具选择'), ('agent_review', '对照预览检查与返工')):
            with self.subTest(stage=stage):
                cloud._update(path, stage=stage, usage={'model_calls': [{'status': 'completed'}]*3})
                with patch.object(cloud, 'chat_json', side_effect=cloud.CloudCallError('超时', {})):
                    with self.assertRaisesRegex(cloud.CloudCallError, label+' · 第 4 次模型调用'):
                        cloud._call(path, self.config, 'guide', {}, [], lambda: False)
                receipt = cloud._read_job(self.project, queued['task_id'])
                self.assertEqual(receipt['usage']['model_calls'][-1]['model_call_number'], 4)
                self.assertEqual(receipt['usage']['model_calls'][-1]['status'], 'unconfirmed')

    def reuse_source(self):
        queued = self.start_v2(budget=1, max_model_calls=16)
        cloud._active.clear()
        source = cloud._job(self.project, queued['task_id'])
        cloud._update(source, status='failed', usage={
            'model_calls': [{'status': 'completed'}]*16,
            'image_requests': 1, 'image_task_ids': ['old-paid-image']})
        state = dict(version=1, workflow_version=VERSION, pending_image=None,
                     workspace={'folder': source.parent.relative_to(self.project).as_posix()},
                     latest_code={'code': 'old code must not become a new accepted answer'},
                     previews={'old_preview': {'locally_valid': True}})
        cloud._write(source.parent/'agent-state.json', state)
        return queued, source, state

    def test_reuse_starts_new_authorized_budget_without_resetting_old_task_or_adopting(self):
        source_task, source, state = self.reuse_source()
        before_task = source.read_bytes()
        before_state = (source.parent/'agent-state.json').read_bytes()
        before_record = (self.project/'record.json').read_bytes()
        # Material pixels are reusable even after selecting a different future
        # image channel; no remote image is queried or resubmitted by this gate.
        cloud._update(source, image_route={'key': 'f'*64, 'label': 'old channel'})
        before_task = source.read_bytes()
        queued = self.start_v2(budget=0, max_model_calls=24,
                               reuse_task_id=source_task['task_id'])
        self.assertNotEqual(queued['task_id'], source_task['task_id'])
        self.assertEqual(queued['reuse_task_id'], source_task['task_id'])
        self.assertEqual(queued['max_model_calls'], 24)
        self.assertEqual(queued['usage'], {'model_calls': [], 'image_requests': 0, 'image_task_ids': []})
        self.assertEqual(source.read_bytes(), before_task)
        self.assertEqual((source.parent/'agent-state.json').read_bytes(), before_state)
        self.assertEqual((self.project/'record.json').read_bytes(), before_record)
        prior, read_state = cloud.validate_reuse_task(self.project, 'shot1', source_task['task_id'],
                                                     queued['cloud_input_stamp'])
        self.assertEqual(prior['task_id'], source_task['task_id'])
        self.assertEqual(read_state, state)
        worker = self.workers[-1]
        self.assertEqual(worker.args[4].reuse_task_id, source_task['task_id'])
        self.model.assert_not_called()
        cloud.generate_asset.assert_not_called()

    def test_reuse_new_policy_does_not_inherit_old_code_or_acceptance(self):
        for old_policy, requested, expected in ((True, None, False), (False, True, True)):
            with self.subTest(old_policy=old_policy, requested=requested):
                cloud._active.clear()
                source_task, source, state = self.reuse_source()
                cloud._update(source, advanced_subject_separation=old_policy,
                              quality_status='passed', preview={'preview_id': 'old-accepted'})
                before_task = source.read_bytes()
                before_state = (source.parent/'agent-state.json').read_bytes()
                extra = {} if requested is None else {'advanced_subject_separation': requested}
                queued = self.start_v2(budget=0, reuse_task_id=source_task['task_id'], **extra)
                self.assertIs(queued['advanced_subject_separation'], expected)
                self.assertIs(self.workers[-1].args[4].advanced_subject_separation, expected)
                self.assertNotIn('quality_status', queued)
                self.assertNotIn('preview', queued)
                self.assertNotIn('latest_code', queued)
                new_folder = cloud._job(self.project, queued['task_id']).parent
                self.assertFalse((new_folder/'agent-state.json').exists())
                self.assertEqual(source.read_bytes(), before_task)
                self.assertEqual((source.parent/'agent-state.json').read_bytes(), before_state)
        self.model.assert_not_called()
        cloud.generate_asset.assert_not_called()

    def test_reuse_rejects_other_shot_changed_inputs_running_or_old_workflow(self):
        original, path, state = self.reuse_source()
        good = cloud._read_job(self.project, original['task_id'])
        cases = [({'shot_id': 'another-shot'}, 404),
                 ({'cloud_input_stamp': 'changed'}, 409),
                 ({'status': 'running'}, 409),
                 ({'status': 'queued'}, 409),
                 ({'workflow_version': layered.CODE_WORKFLOW_VERSION}, 409),
                 ({'template_mode': 'shotcraft'}, 409)]
        count = len(self.workers)
        for change, status in cases:
            with self.subTest(change=change):
                cloud._write(path, {**good, **change})
                with self.assertRaises(HTTPException) as rejected:
                    self.start_v2(budget=0, reuse_task_id=original['task_id'])
                self.assertEqual(rejected.exception.status_code, status)
                self.assertEqual(len(self.workers), count)
        self.model.assert_not_called()
        cloud.generate_asset.assert_not_called()

    def test_reuse_rejects_pending_wrong_state_version_and_wrong_folder(self):
        original, path, state = self.reuse_source()
        cases = [dict(pending_image={'image_task_id': 'pending'}), dict(pending_image={}),
                 dict(version=0), dict(version=True), dict(workflow_version=layered.CODE_WORKFLOW_VERSION),
                 dict(workspace={'folder': '../elsewhere'}), dict(workspace=None)]
        count = len(self.workers)
        for change in cases:
            with self.subTest(change=change):
                cloud._write(path.parent/'agent-state.json', {**state, **change})
                with self.assertRaises(HTTPException) as rejected:
                    self.start_v2(budget=0, reuse_task_id=original['task_id'])
                self.assertEqual(rejected.exception.status_code, 409)
                self.assertEqual(len(self.workers), count)

    def test_reuse_never_searches_another_project_for_missing_id(self):
        original, path, state = self.reuse_source()
        other_project = self.project.parent/'other-project'
        other_project.mkdir()
        with self.assertRaises(HTTPException) as rejected:
            cloud.validate_reuse_task(other_project, 'shot1', original['task_id'],
                                      original['cloud_input_stamp'])
        self.assertEqual(rejected.exception.status_code, 404)

    def test_reuse_path_like_id_is_rejected_before_reading(self):
        for value in ('../task', 'a'*32+'/other', 'G:/outside', '', 42):
            with self.subTest(value=value), self.assertRaises(HTTPException) as rejected:
                cloud.validate_reuse_task(self.project, 'shot1', value, 'stamp')
            self.assertEqual(rejected.exception.status_code, 404)


class ToolAgentBudgetContractTests(unittest.TestCase):
    def test_stable_policy_defaults_and_strict_advanced_mode_gate(self):
        for mode in ('semantic', 'shotcraft', 'layered'):
            base = dict(confirm_cloud=True, template_mode=mode, max_image_requests=0)
            self.assertIs(cloud.CloudRequest(**base).advanced_subject_separation, False)
            self.assertIs(cloud.CloudRequest(**base, advanced_subject_separation=False).advanced_subject_separation, False)
            if mode == 'layered':
                self.assertIs(cloud.CloudRequest(**base, advanced_subject_separation=True).advanced_subject_separation, True)
            else:
                with self.assertRaisesRegex(ValueError, '仅支持原图分层'):
                    cloud.CloudRequest(**base, advanced_subject_separation=True)
            for value in (1, 0, 'true', 'false', None, [], {}):
                with self.subTest(mode=mode, value=value), self.assertRaises(ValueError):
                    cloud.CloudRequest(**base, advanced_subject_separation=value)
        schema = cloud.CloudRequest.model_json_schema()
        field = schema['properties']['advanced_subject_separation']
        self.assertEqual(field['type'], 'boolean')
        self.assertIs(field['default'], False)
        self.assertNotIn('advanced_subject_separation', schema['required'])

    def test_reuse_is_explicit_valid_task_id_only_for_layered_mode(self):
        valid = cloud.CloudRequest(confirm_cloud=True, template_mode='layered',
                                   max_image_requests=0, reuse_task_id='a'*32)
        self.assertEqual(valid.reuse_task_id, 'a'*32)
        for value in ('../task', 'a'*31, 'A'*32, True, ''):
            with self.subTest(value=value), self.assertRaises(ValueError):
                cloud.CloudRequest(confirm_cloud=True, template_mode='layered',
                                   max_image_requests=0, reuse_task_id=value)
        for mode in ('semantic', 'shotcraft'):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                cloud.CloudRequest(confirm_cloud=True, template_mode=mode,
                                   max_image_requests=0, reuse_task_id='a'*32)

    def test_default_and_explicit_call_budgets_are_strict(self):
        self.assertEqual(cloud.CloudRequest(confirm_cloud=True).max_model_calls, 30)
        self.assertEqual(cloud.CloudRequest.model_json_schema()['properties']['max_model_calls']['default'], 30)
        self.assertEqual(cloud.CloudRequest.model_json_schema()['properties']['max_model_calls']['maximum'], 50)
        for value in (6, 16, 30, 32, 33, 50):
            self.assertEqual(cloud.CloudRequest(confirm_cloud=True, max_model_calls=value).max_model_calls, value)
        for value in (True, '16', 3, 4, 5, 51, 16.5, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                cloud.CloudRequest(confirm_cloud=True, max_model_calls=value)

    def test_layered_image_budget_is_explicit_and_bounded(self):
        self.assertIsNone(cloud.CloudRequest(confirm_cloud=True).max_image_requests)
        self.assertIsNone(cloud.CloudRequest.model_json_schema()['properties']['max_image_requests']['default'])
        with self.assertRaises(ValueError):
            cloud.CloudRequest(confirm_cloud=True, template_mode='layered')
        for value in range(5):
            self.assertEqual(cloud.CloudRequest(confirm_cloud=True, template_mode='layered',
                                               max_image_requests=value).max_image_requests, value)
        for value in (None, True, '2', -1, 5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                cloud.CloudRequest(confirm_cloud=True, template_mode='layered', max_image_requests=value)
        # Existing non-layered clients keep their contract; the worker still
        # persists zero image requests for these modes.
        self.assertEqual(cloud.CloudRequest(confirm_cloud=True, max_image_requests=12).max_image_requests, 12)


if __name__ == '__main__':
    unittest.main()
