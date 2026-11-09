"""Offline versioned routing for the code-authored layered workflow."""
import copy
import sys
from types import ModuleType
import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from backend.app import vibe_cloud as cloud
from backend.app import vibe_layered as layered
from backend.app import vibe_layered_workflow as legacy_workflow
from backend.app.vibe_adoption import _require_quality_passed
import test_vibe_layered_v2_route as v2


class CodeLayeredRouteTests(unittest.TestCase):
    persist = v2.LayeredV2RouteTests.persist
    render = staticmethod(v2.LayeredV2RouteTests.render)
    material = staticmethod(v2.LayeredV2RouteTests.material)
    start_patch = v2.LayeredV2RouteTests.start_patch
    inventory_response = v2.LayeredV2RouteTests.inventory_response
    start_v2 = v2.LayeredV2RouteTests.start_v2
    execute = v2.LayeredV2RouteTests.execute
    finish_pipeline = v2.LayeredV2RouteTests.finish_pipeline
    prepare_failed_material = v2.LayeredV2RouteTests.prepare_failed_material
    recovered_asset = v2.LayeredV2RouteTests.recovered_asset

    def setUp(self):
        v2.LayeredV2RouteTests.setUp(self)
        self.start_patch('backend.app.vibe_layered.WORKFLOW_VERSION', layered.CODE_WORKFLOW_VERSION)
        self.quality['workflow_version'] = layered.CODE_WORKFLOW_VERSION
        self.code_preflight = MagicMock(return_value={'renderer': 'ocv-remotion-code-v1'})
        self.code_finish = MagicMock(side_effect=self.finish_code)
        # No renderer, model, or network is run by route contract tests. These
        # module stubs also keep tests independent of the browser installation.
        renderer = ModuleType('backend.app.vibe_code_renderer')
        renderer.preflight = self.code_preflight
        workflow = ModuleType('backend.app.vibe_code_workflow')
        workflow.finish = self.code_finish
        workflow.WORKFLOW_VERSION = layered.CODE_WORKFLOW_VERSION
        workflow.RENDERER = 'ocv-remotion-code-v1'
        workflow.VERSION = 'ocv-code-program-v1'
        modules = patch.dict(sys.modules, {
            renderer.__name__: renderer, workflow.__name__: workflow,
        })
        modules.start()
        self.addCleanup(modules.stop)

    def finish_code(self, *args, **kwargs):
        plan, assets, metrics, out, quality = self.finish_pipeline(*args, **kwargs)
        metrics['renderer'] = 'ocv-remotion-code-v1'
        return {'version': 'ocv-code-program-v1', 'source': 'offline-bound-program'}, assets, metrics, out, quality

    def test_default_layered_keeps_three_modes_and_uses_code_preflight_and_worker(self):
        before = (self.project / 'record.json').read_bytes()
        started = self.start_v2(instruction='保持素材，按字幕分组动画', pace='slow')
        self.assertEqual(started['template_mode'], 'layered')
        self.assertEqual(started['workflow_version'], layered.CODE_WORKFLOW_VERSION)
        self.assertEqual(started['renderer'], 'ocv-remotion-code-v1')
        self.code_preflight.assert_called_once_with(cancelled=None)
        cloud.preflight_remotion.assert_not_called()
        completed = self.execute()
        self.assertEqual(completed['status'], 'completed', completed)
        receipt = completed['preview']
        self.assertEqual(receipt['renderer'], 'ocv-remotion-code-v1')
        self.assertEqual(receipt['metrics']['renderer'], receipt['renderer'])
        self.assertEqual(receipt['workflow_version'], layered.CODE_WORKFLOW_VERSION)
        self.assertEqual(receipt['template_mode'], 'layered')
        self.assertEqual(receipt['quality_status'], 'passed')
        self.code_finish.assert_called_once()
        self.pipeline.assert_not_called()
        self.assertEqual(self.finishes[-1]['instruction'], started['instruction'])
        self.assertEqual(self.finishes[-1]['pace'], 'slow')
        self.assertIn('subject_0_original', self.finishes[-1]['assets'])
        self.assertEqual((self.project / 'record.json').read_bytes(), before)
        cloud.generate_asset.assert_not_called()

    def test_code_preflight_failure_cannot_start_paid_work_or_task(self):
        self.code_preflight.side_effect = RuntimeError('代码渲染组件不可用')
        with self.assertRaises(HTTPException) as failed:
            self.start_v2()
        self.assertEqual(failed.exception.status_code, 409)
        self.assertIn('代码渲染组件', failed.exception.detail)
        self.assertFalse(self.workers)
        self.assertFalse(cloud._active)
        self.model.assert_not_called()
        self.code_finish.assert_not_called()
        cloud.generate_asset.assert_not_called()

    def test_code_resume_preserves_version_and_does_not_create_another_image(self):
        started = self.prepare_failed_material()
        self.code_preflight.reset_mock()
        self.model.reset_mock()
        with patch.object(layered, 'resume_asset', side_effect=self.recovered_asset) as recover:
            queued = cloud.resume_materials(self.identity, 'shot1', started['task_id'], self.req,
                                           cloud.ResumeMaterialsRequest(confirm_model=True))
            self.assertEqual(queued['workflow_version'], layered.CODE_WORKFLOW_VERSION)
            completed = self.execute()
        self.assertEqual(completed['status'], 'completed', completed)
        self.code_preflight.assert_called_once_with(cancelled=None)
        self.code_finish.assert_called_once()
        self.pipeline.assert_not_called()
        self.model.assert_not_called()
        recover.assert_called_once()
        cloud.generate_asset.assert_not_called()
        self.assertEqual(completed['usage']['image_task_ids'], [self.studio_id])

    def test_saved_v2_resume_stays_on_v2_even_with_v3_default(self):
        started = self.prepare_failed_material()
        cloud._update(cloud._job(self.project, started['task_id']),
                      workflow_version=legacy_workflow.VERSION, renderer=layered.RENDERER)
        self.quality['workflow_version'] = legacy_workflow.VERSION
        self.code_preflight.reset_mock()
        self.model.reset_mock()
        with patch.object(layered, 'resume_asset', side_effect=self.recovered_asset):
            cloud.resume_materials(self.identity, 'shot1', started['task_id'], self.req,
                                   cloud.ResumeMaterialsRequest(confirm_model=True))
            completed = self.execute()
        self.assertEqual(completed['status'], 'completed', completed)
        self.assertEqual(completed['workflow_version'], legacy_workflow.VERSION)
        self.assertEqual(completed['preview']['workflow_version'], legacy_workflow.VERSION)
        self.assertEqual(completed['preview']['renderer'], layered.RENDERER)
        cloud.preflight_remotion.assert_called_once_with(
            required_renderer=layered.RENDERER, required_plan_version=layered.VERSION)
        self.code_preflight.assert_not_called()
        self.code_finish.assert_not_called()
        self.pipeline.assert_called_once()
        self.model.assert_not_called()

    def test_unknown_saved_version_fails_before_resume_mutation_or_paid_work(self):
        started = self.prepare_failed_material()
        path = cloud._job(self.project, started['task_id'])
        cloud._update(path, workflow_version='future-unknown-version')
        before = path.read_bytes()
        workers = len(self.workers)
        self.code_preflight.reset_mock()
        with self.assertRaises(HTTPException) as failed:
            cloud.resume_materials(self.identity, 'shot1', started['task_id'], self.req,
                                   cloud.ResumeMaterialsRequest(confirm_model=True))
        self.assertEqual(failed.exception.status_code, 409)
        self.assertIn('流程版本', failed.exception.detail)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(len(self.workers), workers)
        self.assertFalse(cloud._active)
        self.code_preflight.assert_not_called()
        self.code_finish.assert_not_called()

    def test_cancel_or_changed_inputs_cannot_publish_code_result(self):
        for mode in ('cancel', 'changed'):
            with self.subTest(mode=mode):
                self.cancel_in_finish = mode == 'cancel'
                self.mutate_in_finish = mode == 'changed'
                before = (self.project / 'record.json').read_bytes()
                self.start_v2()
                result = self.execute()
                self.assertEqual(result['status'], 'stopped' if mode == 'cancel' else 'failed', result)
                self.assertNotIn('preview', result)
                self.assertFalse(cloud.previews._pointer(self.project, 'shot1').exists())
                self.assertFalse(cloud._active)
                if mode == 'cancel':
                    self.assertEqual((self.project / 'record.json').read_bytes(), before)

    def test_wrong_renderer_or_quality_version_cannot_publish(self):
        for mode in ('renderer', 'version'):
            with self.subTest(mode=mode):
                def invalid(*args, **kwargs):
                    plan, assets, metrics, output, quality = self.finish_code(*args, **kwargs)
                    if mode == 'renderer':
                        metrics['renderer'] = layered.RENDERER
                    else:
                        quality['workflow_version'] = legacy_workflow.VERSION
                    return plan, assets, metrics, output, quality
                self.code_finish.side_effect = invalid
                self.start_v2()
                result = self.execute()
                self.assertEqual(result['status'], 'failed', result)
                self.assertIn('回执与任务流程版本不一致', result['message'])
                self.assertNotIn('preview', result)
                self.assertFalse(cloud.previews._pointer(self.project, 'shot1').exists())

    def test_missing_new_quality_stays_review_only_and_old_receipts_are_compatible(self):
        self.quality = {}
        # The routing fixture uses these keys when producing its fake video;
        # remove them from the result to simulate a missing workflow audit.
        def no_quality(*args, **kwargs):
            self.quality = dict(workflow_version=layered.CODE_WORKFLOW_VERSION,
                                quality_status='passed', visual_repairs=0)
            plan, assets, metrics, output, _ = self.finish_code(*args, **kwargs)
            metrics.pop('quality_status')
            return plan, assets, metrics, output, {}
        self.code_finish.side_effect = no_quality
        self.start_v2()
        result = self.execute()
        self.assertEqual(result['status'], 'completed', result)
        receipt = result['preview']
        self.assertEqual(receipt['workflow_version'], layered.CODE_WORKFLOW_VERSION)
        self.assertEqual(receipt['quality_status'], 'needs_review')
        with self.assertRaises(HTTPException):
            _require_quality_passed(receipt)
        _require_quality_passed({'renderer': layered.RENDERER})
        _require_quality_passed({'workflow_version': legacy_workflow.VERSION,
                                'quality_status': 'passed', 'metrics': {'quality_status': 'passed'}})


if __name__ == '__main__':
    unittest.main()
