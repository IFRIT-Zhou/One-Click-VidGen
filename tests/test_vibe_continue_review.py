"""Offline saved-preview continuation gates; never contacts a model or renderer."""
import copy
import hashlib
import shutil
import unittest
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from pydantic import ValidationError
from starlette.requests import Request

from backend.app import vibe_cloud as cloud
from backend.app.vibe_agent_contract import VERSION
from backend.app.vibe_agent_materials import MaterialWorkspace
import test_vibe_cloud as fixture


class ContinueReviewTests(unittest.TestCase):
    persist=fixture.VibeCloudTests.persist
    render=staticmethod(fixture.VibeCloudTests.render)
    material=staticmethod(fixture.VibeCloudTests.material)

    def setUp(self):
        fixture.VibeCloudTests.setUp(self)
        self.req=Request({'type':'http','headers':[]})
        self.task_id='a'*32
        self.folder=cloud._root(self.project)/self.task_id
        self.folder.mkdir()
        parts=cloud._context(self.project,'shot1')
        self.context=parts[2]
        self.workspace=MaterialWorkspace(self.project,self.folder,parts[3],self.context['canvas'],
            advanced_subject_separation=False)
        self.workspace.set_inventory(dict(background='solid',background_color='#154060',background_is_uniform=True,
            foreground_summary=dict(has_nontext_subjects=False,subject_count=0,description='测试文字',
                text_only_reason='测试只含文字'),subjects=[],texts=[dict(text='结果',source_box=[.1,.1,.4,.3])]))
        self.workspace.select_background(color='#154060')
        self.output=self.folder/'saved-preview'
        self.output.mkdir()
        shutil.copyfile(self.project/'core.png',self.output/'poster.png')
        code='return <Text id="result" text="结果" />;'
        self.candidate=dict(preview_id='preview_saved',turn=dict(code=code,summary='已有代码',embedded_text=[],recovered_text=[]),
            code_sha256=hashlib.sha256(code.encode()).hexdigest(),fingerprint=self.workspace.fingerprint(),
            output=self.output.relative_to(self.project).as_posix(),locally_valid=True)
        self.state=dict(version=1,workflow_version=VERSION,workspace=self.workspace.snapshot(),pending_image=None,
            latest_code=copy.deepcopy(self.candidate['turn']),previews={'preview_saved':self.candidate},events=[],
            observations={},measurements={},delivery_reviews={},review_history=[])
        self.usage=dict(model_calls=[dict(status='completed') for _ in range(30)],image_requests=1,image_task_ids=['old-image'])
        self.task=dict(task_id=self.task_id,shot_id='shot1',status='failed',stage='agent_plan',
            template_mode='layered',workflow_version=VERSION,max_model_calls=30,max_image_requests=2,
            advanced_subject_separation=False,usage=copy.deepcopy(self.usage),instruction='保留版式',pace='slow',
            cloud_input_stamp=cloud._cloud_stamp(*parts),image_route={'key':'original-route','label':'原渠道'})
        cloud._write(self.folder/'agent-state.json',self.state)
        cloud._write(self.folder/'task.json',self.task)
        cloud._write(cloud._pointer(self.project,'shot1'),{'task_id':self.task_id})
        p=patch('backend.app.vibe_layered.preflight_workflow',return_value={})
        self.preflight=p.start();self.addCleanup(p.stop)

    def body(self,**values):
        return cloud.ContinueReviewRequest.model_validate(dict(confirm_model=True,expected_model_calls=30,
            expected_model_limit=30,request_id='continue-test-0001',**values))

    def submit(self,body=None):
        return cloud.continue_review(self.identity,'shot1',self.task_id,body or self.body(),self.req)

    def test_continue_retains_state_and_usage_without_new_images_or_formal_edits(self):
        before=(self.project/'record.json').read_bytes()
        state_before=(self.folder/'agent-state.json').read_bytes()
        with patch('backend.app.vibe_image_route.resolve',side_effect=AssertionError('must not change image route')):
            result=self.submit(self.body(additional_model_calls=12))
        self.assertEqual(result['max_model_calls'],42)
        self.assertEqual(result['usage'],self.usage)
        self.assertFalse(result['new_images_allowed'])
        worker=self.workers[-1]
        self.assertIs(worker.target,cloud._continue_review_worker)
        self.assertFalse(worker.args[3]['new_images_allowed'])
        self.assertEqual(worker.args[3]['layered_image_route'],self.task['image_route'])
        self.assertEqual(worker.args[4].max_image_requests,0)
        self.assertEqual(worker.args[4].max_model_calls,42)
        self.assertEqual(worker.args[4].instruction,'保留版式')
        self.assertEqual(worker.args[4].pace,'slow')
        with patch('backend.app.vibe_agent_workflow.run_worker') as run:
            worker.target(*worker.args)
        self.assertEqual(run.call_args.kwargs,{'continue_preview':True})
        self.assertEqual((self.project/'record.json').read_bytes(),before)
        self.assertEqual((self.folder/'agent-state.json').read_bytes(),state_before)
        cloud.generate_asset.assert_not_called()

    def test_identical_request_replay_does_not_add_budget_or_queue(self):
        first=self.submit()
        replay=self.submit()
        self.assertTrue(replay['continuation_replayed'])
        self.assertEqual(first['max_model_calls'],replay['max_model_calls'])
        self.assertEqual(len(self.workers),1)
        self.assertEqual(len(replay['review_continuations']),1)
        cloud._active.clear()
        cloud._update(self.folder/'task.json',status='failed')
        again=self.submit()
        self.assertTrue(again['continuation_replayed'])
        self.assertEqual(len(self.workers),1,'replay after a terminal run must not automatically run again')

    def test_request_id_cannot_be_reused_for_different_allowance(self):
        self.submit()
        with self.assertRaises(HTTPException) as raised:
            self.submit(self.body(additional_model_calls=2))
        self.assertEqual(raised.exception.status_code,409)
        self.assertEqual(len(self.workers),1)

    def test_stale_usage_and_budget_overflow_are_rejected_before_worker(self):
        for body in (cloud.ContinueReviewRequest(confirm_model=True,expected_model_calls=29,expected_model_limit=30,request_id='stale-count'),
                     self.body(additional_model_calls=21),self.body(additional_model_calls=0)):
            with self.subTest(body=body),self.assertRaises(HTTPException):self.submit(body)
        self.assertFalse(self.workers)
        self.assertEqual(cloud._read_job(self.project,self.task_id)['usage'],self.usage)
        self.assertEqual(cloud._read_job(self.project,self.task_id)['max_model_calls'],30)

    def test_zero_addition_uses_only_remaining_authorization(self):
        cloud._update(self.folder/'task.json',max_model_calls=50)
        body=self.body(additional_model_calls=0).model_copy(update={'expected_model_limit':50})
        result=self.submit(body)
        self.assertEqual(result['max_model_calls'],50)
        self.assertEqual(result['review_continuations'][0]['additional_model_calls'],0)

    def test_stale_allowance_cannot_extend_a_concurrent_authorization(self):
        self.submit()
        cloud._active.clear()
        cloud._update(self.folder/'task.json',status='failed')
        stale=self.body().model_copy(update={'request_id':'another-window-0001'})
        with self.assertRaises(HTTPException) as failed:self.submit(stale)
        self.assertIn('授权上限已改变',failed.exception.detail)
        self.assertEqual(cloud._read_job(self.project,self.task_id)['max_model_calls'],40)
        self.assertEqual(len(self.workers),1)

    def test_no_confirmation_does_not_queue(self):
        body=self.body().model_copy(update={'confirm_model':False})
        with self.assertRaises(HTTPException):self.submit(body)
        self.assertFalse(self.workers)

    def test_locally_invalid_preview_can_be_rerendered_but_not_marked_passed(self):
        self.state['previews']['preview_saved']['locally_valid']=False
        self.state['previews']['preview_saved']['validation_error']='old metric overlap'
        cloud._write(self.folder/'agent-state.json',self.state)
        self.submit()
        retained=cloud.previews._read_json(self.folder/'agent-state.json',8*1024**2)
        self.assertFalse(retained['previews']['preview_saved']['locally_valid'])
        self.assertEqual(retained['delivery_reviews'],{})

    def test_changed_inputs_code_or_materials_and_pending_images_are_blocked(self):
        baseline=copy.deepcopy(self.state)
        for name in ('latest_code','fingerprint','pending_image','output'):
            state=copy.deepcopy(baseline)
            if name=='latest_code':state['latest_code']['code']='changed code'
            elif name=='fingerprint':state['previews']['preview_saved']['fingerprint']='x'
            elif name=='pending_image':state['pending_image']={'image_task_id':'unknown'}
            else:state['previews']['preview_saved']['output']='../outside'
            cloud._write(self.folder/'agent-state.json',state)
            with self.subTest(name=name),self.assertRaises(HTTPException):self.submit()
        cloud._write(self.folder/'agent-state.json',baseline)
        self.shot['intent']='已改意图';self.persist()
        with self.assertRaises(HTTPException):self.submit()
        self.assertFalse(self.workers)

    def test_latest_offers_only_verified_saved_candidate_and_remaining_capacity(self):
        result=cloud.latest(self.identity,'shot1',self.req)
        self.assertTrue(result['can_continue_review'])
        self.assertEqual(result['continuation_additional_limit'],20)
        self.state['latest_code']['code']='unrendered'
        cloud._write(self.folder/'agent-state.json',self.state)
        result=cloud.latest(self.identity,'shot1',self.req)
        self.assertFalse(result['can_continue_review'])
        self.assertIn('不一致',result['continue_review_unavailable_reason'])

    def test_corrupt_material_is_rejected_without_budget_change(self):
        background=self.workspace.assets()['background']
        background.write_bytes(b'invalid image')
        with self.assertRaises(HTTPException):self.submit()
        self.assertFalse(self.workers)
        self.assertEqual(cloud._read_job(self.project,self.task_id)['max_model_calls'],30)

    def test_strict_continuation_schema(self):
        for field,value in [('confirm_model','true'),('expected_model_calls',True),('expected_model_limit',True),
                            ('additional_model_calls',-1),('additional_model_calls',51),('request_id','../x')]:
            body=self.body().model_dump();body[field]=value
            with self.subTest(field=field,value=value),self.assertRaises(ValidationError):
                cloud.ContinueReviewRequest.model_validate(body)


class ContinueMcpContractTests(unittest.TestCase):
    def test_live_contract_requires_explicit_allowance_and_has_correct_cost(self):
        import sys
        from pathlib import Path
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'plugins'/'codex_bridge'))
        from ocv_mcp.contracts import bind
        from ocv_mcp.adapter import operation_cost
        from jsonschema import Draft202012Validator
        app=FastAPI();app.include_router(cloud.router)
        bound,_=bind(app.openapi())
        name='ocv_video_vibe_cloud_continue_review'
        self.assertIn(name,bound)
        op,schema=bound[name]
        args=dict(path=dict(identity='a'*32,shot_id='generic',task_id='b'*32),
            body=dict(confirm_model=True,additional_model_calls=12,expected_model_calls=30,expected_model_limit=30,request_id='test-review-12'))
        validator=Draft202012Validator(schema)
        self.assertFalse(list(validator.iter_errors(args)))
        for field in ('confirm_model','additional_model_calls','expected_model_calls','expected_model_limit','request_id'):
            missing=copy.deepcopy(args);missing['body'].pop(field)
            with self.subTest(field=field):self.assertTrue(list(validator.iter_errors(missing)))
        false=copy.deepcopy(args);false['body']['confirm_model']=False
        self.assertTrue(list(validator.iter_errors(false)))
        cost=operation_cost(op,args,schema)
        for expected in ('12','可能计费','不新增图片请求','不覆盖正式镜头','同一request_id'):
            self.assertIn(expected,cost)
        outdated=app.openapi()
        request_schema=outdated['components']['schemas']['ContinueReviewRequest']
        request_schema['properties'].pop('expected_model_limit')
        old_bound,unavailable=bind(outdated)
        self.assertNotIn(name,old_bound)
        self.assertIn(name,unavailable)


if __name__=='__main__':unittest.main()
