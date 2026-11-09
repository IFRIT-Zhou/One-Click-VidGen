"""Offline integration of measured review disputes and independent re-review.

Reuse only the existing tool-loop fixture helpers; do not inherit and re-run
all its tests. No model/image network, OCR worker, or browser process runs.
"""
import copy
import hashlib
from pathlib import Path
import unittest

from PIL import Image

import test_vibe_agent_workflow as fixtures
from backend.app.vibe_agent_contract import FinishArgs, ReviewEvidenceArgs
from backend.app.vibe_materials import MaterialError


class DeliveryEvidenceWorkflowTests(unittest.TestCase):
    mock = fixtures.AgentWorkflowTests.mock
    update_task = fixtures.AgentWorkflowTests.update_task
    model_call = fixtures.AgentWorkflowTests.model_call
    setup_commands = fixtures.AgentWorkflowTests.setup_commands
    preview = fixtures.AgentWorkflowTests.preview
    saved_candidate = fixtures.AgentWorkflowTests.saved_candidate
    layout_candidate = fixtures.AgentWorkflowTests.layout_candidate
    passed = fixtures.AgentWorkflowTests.passed

    def setUp(self):
        fixtures.AgentWorkflowTests.setUp(self)
        self.ocr_call.side_effect = self.local_ocr

    def local_ocr(self, path, *args, **kwargs):
        path = Path(path)
        with Image.open(path) as image:
            size = list(image.size)
        texts = ([dict(id='source_title',text='研究结果',box=[64,9,256,27],confidence=.99)]
                 if path == self.core else [])
        return dict(status='ready',backend='offline-fixture',version='test',
            source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),source_size=size,
            coordinate_space='source_pixels',texts=texts)

    def render(self, **kwargs):
        result=fixtures.AgentWorkflowTests.render(self,**kwargs)
        if kwargs['mode'] != 'measure':
            result['measurements'].update(poster=True,texts=[dict(id='title',text='研究结果',
                box=[132,20,380,36],visible=True,painted=True,font_size=32)])
        return result

    def rejected(self):
        return dict(passed=False,summary='需核实同位置底图与代码线条',
            issues=['poster 标题左侧疑似出现两条引导线，需核对实际背景'])

    def rejected_candidate(self):
        candidate=self.saved_candidate()
        self.reviews=[self.rejected()]
        result=self.session.finish(candidate['preview_id'])
        self.assertFalse(result['receipt']['ok'])
        self.assertIsNone(self.session.accepted)
        return candidate

    def evidence(self,candidate,**changes):
        values=dict(preview_id=candidate['preview_id'],explanation='请求核实当前线条是否来自代码，而非底图残留',
                    element_ids=['title'],text_queries=['研究结果'])
        values.update(changes)
        return self.session.execute('review_evidence',ReviewEvidenceArgs(**values),'本地同窗取证')

    def test_every_independent_review_receives_actual_background_with_bound_role(self):
        candidate=self.saved_candidate()
        self.reviews=[self.rejected(),self.passed()]
        for _ in range(2):
            self.session.finish(candidate['preview_id'])
            call=self.calls[-1]
            background_id,entry=self.session.workspace._resolve('background')
            self.assertEqual(Path(call['images']['background']),Path(entry['path']))
            self.assertNotEqual(Path(call['images']['background']),Path(call['images']['poster']))
            role=call['payload']['background_binding']
            self.assertEqual(role['role'],'prepared_background_plate')
            self.assertFalse(role['is_rendered_result'])
            self.assertEqual(role['asset_id'],background_id)
            self.assertEqual(role['source_sha256'],hashlib.sha256(Path(entry['path']).read_bytes()).hexdigest())
            self.assertEqual(role['preview_id'],candidate['preview_id'])
            self.assertEqual(call['payload']['image_roles']['background'],role)
        self.assertEqual(len(self.calls),2)
        self.image_call.assert_not_called()
        self.rmbg_call.assert_not_called()

    def test_free_measured_evidence_then_finish_requires_another_independent_model_review(self):
        candidate=self.rejected_candidate()
        before_calls=len(self.calls)
        before_renders=len(self.renders)
        candidate_before=copy.deepcopy(candidate)
        result=self.evidence(candidate)
        receipt=result['receipt']
        self.assertTrue(receipt['ok'])
        self.assertEqual(receipt['new_model_calls'],0)
        self.assertEqual(receipt['new_image_requests'],0)
        self.assertEqual(len(self.calls),before_calls)
        self.assertEqual(len(self.renders),before_renders)
        self.assertIsNone(self.session.accepted)
        self.assertEqual(candidate['turn'],candidate_before['turn'])
        self.assertTrue(result['images'])
        report=receipt['evidence']
        self.assertEqual(report['anchors'][0]['kind'],'browser_element')
        self.assertEqual(report['anchors'][0]['status'],'located')
        self.assertEqual(report['anchors'][1]['core']['status'],'located')
        self.assertFalse(report['automatic_quality_verdict'])
        evidence_id=receipt['review_evidence_id']
        self.reviews=[self.passed()]
        second=self.session.execute('finish',FinishArgs(preview_id=candidate['preview_id'],
            review_evidence_id=evidence_id),'依据本地证据申请独立复核')
        self.assertTrue(second['receipt']['ok'])
        self.assertEqual(len(self.calls),before_calls+1)
        self.assertEqual(len(self.task['usage']['model_calls']),before_calls+1)
        call=self.calls[-1]
        self.assertEqual(call['system'],fixtures.workflow.REVIEW_GUIDE)
        self.assertEqual(call['payload']['review_evidence']['evidence_id'],evidence_id)
        for name in result['images']:
            self.assertIn(name,call['images'])
            self.assertEqual(call['payload']['image_roles'][name]['role'],'measured_same_window_review_evidence')
            self.assertFalse(call['payload']['image_roles'][name]['region_is_exact_localization'])
        self.assertEqual(self.session.accepted['preview_id'],candidate['preview_id'])
        self.assertEqual(second['receipt']['review']['review_evidence_id'],evidence_id)
        self.image_call.assert_not_called()
        self.rmbg_call.assert_not_called()

    def test_evidence_cannot_override_a_second_failed_independent_review(self):
        candidate=self.rejected_candidate()
        result=self.evidence(candidate)
        self.reviews=[self.rejected()]
        second=self.session.finish(candidate['preview_id'],review_evidence_id=result['receipt']['review_evidence_id'])
        self.assertFalse(second['receipt']['ok'])
        self.assertEqual(len(self.calls),2)
        self.assertIsNone(self.session.accepted)
        self.assertEqual(len(self.session.state['review_history']),2)

    def test_stale_scope_background_or_review_hash_is_rejected_before_model_call(self):
        candidate=self.rejected_candidate()
        identity=self.evidence(candidate)['receipt']['review_evidence_id']
        original=copy.deepcopy(self.session.state['review_evidence'][identity])
        fields=['preview_id','code_sha256','material_fingerprint','measurement_stamp',
                'background_sha256','background_asset_id','review_sha256']
        for field in fields:
            with self.subTest(field=field):
                self.session.state['review_evidence'][identity]=copy.deepcopy(original)
                self.session.state['review_evidence'][identity][field]='different-version'
                count=len(self.calls)
                with self.assertRaisesRegex(ValueError,'审核证据.*不一致'):
                    self.session.finish(candidate['preview_id'],review_evidence_id=identity)
                self.assertEqual(len(self.calls),count)
                self.assertIsNone(self.session.accepted)
        self.session.state['review_evidence'][identity]=original

    def test_new_review_invalidates_evidence_even_for_the_same_preview(self):
        candidate=self.rejected_candidate()
        identity=self.evidence(candidate)['receipt']['review_evidence_id']
        self.session.state['delivery_reviews'][candidate['preview_id']]['issues']=['更新后的独立审核意见']
        with self.assertRaisesRegex(ValueError,'审核版本不一致'):
            self.session.finish(candidate['preview_id'],review_evidence_id=identity)
        self.assertEqual(len(self.calls),1)
        self.assertIsNone(self.session.accepted)

    def test_changed_background_blocks_collection_and_stale_delivery(self):
        candidate=self.rejected_candidate()
        identity=self.evidence(candidate)['receipt']['review_evidence_id']
        self.session.workspace.select_background(color='#654321')
        with self.assertRaisesRegex(ValueError,'当前素材与代码'):
            self.evidence(candidate)
        with self.assertRaisesRegex(ValueError,'素材/清单已变化'):
            self.session.finish(candidate['preview_id'],review_evidence_id=identity)
        self.assertEqual(len(self.calls),1)

    def test_evidence_image_tampering_cannot_bypass_registered_source_hash(self):
        candidate=self.rejected_candidate()
        result=self.evidence(candidate)
        name=next(iter(result['images']))
        first=self.session.workspace._resolve(name)[1]['path']
        with Image.new('RGB',(10,10),'red') as replacement:
            replacement.save(first)
        with self.assertRaises(MaterialError):
            self.session.finish(candidate['preview_id'],review_evidence_id=result['receipt']['review_evidence_id'])
        self.assertEqual(len(self.calls),1)
        self.assertIsNone(self.session.accepted)

    def test_creator_and_reviewer_receive_the_same_registered_evidence_snapshot(self):
        candidate=self.rejected_candidate()
        result=self.evidence(candidate)
        name=next(iter(result['images']))
        exported=result['images'][name]
        registered=self.session.workspace._resolve(name)[1]['path']
        original_sha=hashlib.sha256(Path(registered).read_bytes()).hexdigest()
        self.assertEqual(Path(exported),Path(registered))
        self.reviews=[self.passed()]
        second=self.session.finish(candidate['preview_id'],review_evidence_id=result['receipt']['review_evidence_id'])
        self.assertTrue(second['receipt']['ok'])
        self.assertEqual(Path(self.calls[-1]['images'][name]),Path(registered))
        self.assertEqual(hashlib.sha256(Path(registered).read_bytes()).hexdigest(),original_sha)

    def test_current_review_evidence_survives_a_later_font_measurement(self):
        candidate=self.rejected_candidate()
        evidence=self.evidence(candidate)
        identity=evidence['receipt']['review_evidence_id']
        self.session.record('review_evidence','本地证据',evidence['receipt'],evidence['images'])
        args=fixtures.workflow.ARGUMENT_MODELS['measure_text'].model_validate(
            dict(probes=[dict(id='separate_probe',text='参考字号',font_size=32)]))
        measured=self.session.execute('measure_text',args,'另一项免费排版测量')
        self.session.record('measure_text','另一项免费排版测量',measured['receipt'],measured.get('images'))
        payload=self.session.payload()
        self.assertEqual(payload['current_review_evidence']['evidence_id'],identity)
        self.assertEqual(self.session.current_review_evidence()['evidence_id'],identity)
        creator=self.session.creator_images()
        for name,path in evidence['images'].items():
            self.assertIn(name,creator)
            self.assertEqual(Path(creator[name]),Path(path))
        self.assertEqual(len(self.calls),1)
        self.assertIsNone(self.session.accepted)

    def test_stale_evidence_disappears_from_current_context_and_creator_images(self):
        candidate=self.rejected_candidate()
        evidence=self.evidence(candidate)
        identity=evidence['receipt']['review_evidence_id']
        self.session.record('review_evidence','本地证据',evidence['receipt'],evidence['images'])
        code=copy.deepcopy(self.session.state['latest_code'])
        self.session.state['latest_code']['code']+=' // changed current draft'
        self.assertIsNone(self.session.payload()['current_review_evidence'])
        self.assertIsNone(self.session.current_review_evidence())
        self.assertTrue(set(evidence['images']).isdisjoint(self.session.creator_images()))
        self.assertIn(identity,self.session.state['review_evidence'])
        self.session.state['latest_code']=code
        self.session.state['delivery_reviews'][candidate['preview_id']]['issues']=['该版本新增的审核发现']
        self.assertIsNone(self.session.payload()['current_review_evidence'])
        self.assertTrue(set(evidence['images']).isdisjoint(self.session.creator_images()))
        self.assertEqual(len(self.calls),1)

    def test_last_reserved_model_call_carries_current_measured_evidence_to_real_review(self):
        candidate=self.rejected_candidate()
        evidence=self.evidence(candidate)
        identity=evidence['receipt']['review_evidence_id']
        self.task['max_model_calls']=2
        self.assertEqual(self.session.budgets()['model_remaining'],1)
        self.reviews=[self.passed()]
        self.assertTrue(self.session.closing_review())
        self.assertEqual(len(self.calls),2)
        self.assertEqual(self.calls[-1]['system'],fixtures.workflow.REVIEW_GUIDE)
        self.assertEqual(self.calls[-1]['payload']['review_evidence']['evidence_id'],identity)
        self.assertEqual(self.session.accepted['preview_id'],candidate['preview_id'])
        self.assertTrue(self.session.state['events'][-1]['receipt']['reserved_review_call'])
        self.image_call.assert_not_called()

    def test_last_call_evidence_does_not_force_a_failed_review_to_pass(self):
        candidate=self.rejected_candidate()
        self.evidence(candidate)
        self.task['max_model_calls']=2
        self.reviews=[self.rejected()]
        self.assertTrue(self.session.closing_review())
        self.assertEqual(len(self.calls),2)
        self.assertEqual(self.session.budgets()['model_remaining'],0)
        self.assertIsNone(self.session.accepted)
        self.assertFalse(self.session.state['events'][-1]['receipt']['ok'])

    def test_no_anchor_or_unknown_anchor_cannot_create_a_bypass_evidence_id(self):
        candidate=self.rejected_candidate()
        with self.assertRaisesRegex(ValueError,'不能凭空猜'):
            ReviewEvidenceArgs(preview_id=candidate['preview_id'],explanation='不指定任何锚点')
        failed=self.evidence(candidate,element_ids=['does_not_exist'],text_queries=[])
        self.assertFalse(failed['receipt']['ok'])
        self.assertEqual(failed['images'],{})
        self.assertIn('No measured anchor',failed['receipt']['error'])
        with self.assertRaisesRegex(ValueError,'审核证据.*不一致'):
            self.session.finish(candidate['preview_id'],review_evidence_id=failed['receipt']['review_evidence_id'])
        self.assertEqual(len(self.calls),1)

    def test_layout_or_unreviewed_candidates_cannot_fabricate_a_rejected_review(self):
        candidate=self.layout_candidate()
        with self.assertRaisesRegex(ValueError,'完整有效预览'):
            self.evidence(candidate)
        with self.assertRaisesRegex(ValueError,'尚无完整审核证据'):
            self.session.finish(candidate['preview_id'],review_evidence_id='fabricated')
        self.assertFalse(self.calls)
        full=self.saved_candidate()
        with self.assertRaisesRegex(ValueError,'尚无对应的未通过审核'):
            self.evidence(full)
        with self.assertRaisesRegex(ValueError,'审核证据.*不一致'):
            self.session.finish(full['preview_id'],review_evidence_id='fabricated')
        self.assertFalse(self.calls)


if __name__ == '__main__':
    unittest.main()
