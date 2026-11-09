"""Offline manual continuation after an empty completed model response.

Every worker is captured, never run; no API, image generation or rendering is
performed. Fixtures are temporary projects, not a user's formal shot.
"""
import copy
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from pydantic import ValidationError
from starlette.requests import Request

from backend.app import vibe_cloud as cloud
from backend.app.vibe_agent_contract import VERSION
from backend.app.vibe_agent_materials import MaterialWorkspace
import test_vibe_cloud as fixture


class ContinueStepsTests(unittest.TestCase):
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
        self.workspace=MaterialWorkspace(self.project,self.folder,parts[3],parts[2]['canvas'],
            advanced_subject_separation=False)
        self.state=dict(version=1,workflow_version=VERSION,workspace=self.workspace.snapshot(),
            pending_image=None,latest_code=None,previews={},events=[dict(tool='inspect_source',receipt={'ok':True})],
            sequence=1,observations={},measurements={},delivery_reviews={},review_history=[],new_images_allowed=True)
        self.usage=dict(model_calls=[dict(status='completed'),dict(status='completed'),
            dict(status='unconfirmed',error_code='model_empty_completed',answer_chars=119,empty_after_strip=True)],
            image_requests=0,image_task_ids=[])
        self.route=dict(key='c'*64,label='原个人 API',provider='custom')
        self.task=dict(task_id=self.task_id,shot_id='shot1',status='failed',stage='agent_plan',
            template_mode='layered',workflow_version=VERSION,max_model_calls=30,max_image_requests=2,
            advanced_subject_separation=False,usage=copy.deepcopy(self.usage),instruction='保持原图信息',pace='slow',
            cloud_input_stamp=cloud._cloud_stamp(*parts),image_route=copy.deepcopy(self.route),
            start_mode='fresh',previous_task_id='d'*32)
        self.save()
        cloud._write(cloud._pointer(self.project,'shot1'),{'task_id':self.task_id})
        p=patch('backend.app.vibe_layered.preflight_workflow',return_value={})
        self.preflight=p.start();self.addCleanup(p.stop)

    def save(self):
        cloud._write(self.folder/'agent-state.json',self.state)
        cloud._write(self.folder/'task.json',self.task)

    def body(self,**values):
        body=dict(confirm_model=True,expected_model_calls=3,expected_model_limit=30,
            request_id='continue-steps-0001')
        body.update(values)
        return cloud.ContinueStepsRequest.model_validate(body)

    def submit(self,body=None):
        return cloud.continue_steps(self.identity,'shot1',self.task_id,body or self.body(),self.req)

    def image_body(self,**values):
        body=dict(confirm_remaining_images=True,expected_image_requests=0,expected_image_limit=2)
        body.update(values)
        return self.body(**body)

    def test_early_checkpoint_without_plan_background_code_or_preview_can_continue(self):
        before=(self.project/'record.json').read_bytes()
        checkpoint=(self.folder/'agent-state.json').read_bytes()
        with patch('backend.app.vibe_image_route.resolve',side_effect=AssertionError('must not resolve image route')):
            result=self.submit()
        self.assertEqual(result['task_id'],self.task_id)
        self.assertEqual(result['usage'],self.usage)
        self.assertEqual(result['max_model_calls'],30)
        self.assertEqual(result['max_image_requests'],2,'persist the original authorization, not a fresh budget')
        self.assertFalse(result['new_images_allowed'])
        self.assertEqual(result['start_mode'],'fresh')
        self.assertEqual(result['previous_task_id'],'d'*32)
        self.assertIn('剩余 27 次',result['message'])
        self.assertIn('不新增图片',result['message'])
        self.assertEqual(len(result['steps_continuations']),1)
        worker=self.workers[-1]
        self.assertIs(worker.target,cloud._continue_steps_worker)
        self.assertFalse(worker.args[3]['new_images_allowed'])
        self.assertEqual(worker.args[3]['layered_image_route'],self.route)
        data=worker.args[4]
        self.assertEqual(data.max_model_calls,30)
        self.assertEqual(data.max_image_requests,0)
        self.assertEqual(data.instruction,'保持原图信息')
        self.assertEqual(data.pace,'slow')
        self.assertEqual(data.start_mode,'fresh')
        self.assertFalse(data.advanced_subject_separation)
        with patch('backend.app.vibe_agent_workflow.run_worker') as run:
            worker.target(*worker.args)
        self.assertEqual(run.call_args.kwargs,{'continue_steps':True})
        self.assertEqual((self.project/'record.json').read_bytes(),before)
        self.assertEqual((self.folder/'agent-state.json').read_bytes(),checkpoint)
        cloud.generate_asset.assert_not_called()

    def test_latest_advertises_empty_checkpoint_without_polling_a_worker(self):
        state_before=(self.folder/'agent-state.json').read_bytes()
        task_before=(self.folder/'task.json').read_bytes()
        for _ in range(2):
            result=cloud.latest(self.identity,'shot1',self.req)
            self.assertTrue(result['can_continue_steps'])
            self.assertFalse(result['can_continue_review'])
            self.assertEqual(result['continue_steps_model_remaining'],27)
            self.assertEqual(result['continue_steps_image_remaining'],2)
            self.assertTrue(result['continue_steps_images_allowed'])
            self.assertEqual(result['continuation_additional_limit'],20)
        self.assertFalse(self.workers)
        self.assertEqual((self.folder/'agent-state.json').read_bytes(),state_before)
        self.assertEqual((self.folder/'task.json').read_bytes(),task_before)

    def test_empty_first_response_can_resume_before_any_tool_has_run(self):
        self.state['events']=[];self.state['sequence']=0
        self.task['usage']['model_calls']=[copy.deepcopy(self.usage['model_calls'][-1])]
        self.save()
        result=self.submit(self.body(expected_model_calls=1))
        self.assertEqual(result['steps_continuations'][0]['saved_sequence'],0)
        self.assertEqual(len(result['usage']['model_calls']),1)
        self.assertEqual(self.workers[0].args[4].max_model_calls,30)

    def test_completed_materials_and_unrendered_code_are_preserved(self):
        self.workspace.set_inventory(dict(background='solid',background_color='#154060',background_is_uniform=True,
            foreground_summary=dict(has_nontext_subjects=False,subject_count=0,description='纯文字图',
                text_only_reason='仅有可编辑文字'),subjects=[],texts=[dict(text='结果',source_box=[.1,.1,.4,.3])]))
        self.workspace.select_background(color='#154060')
        self.state['workspace']=self.workspace.snapshot()
        self.state['latest_code']={'code':'return <Text id="result" text="结果" />;','summary':'已写完但尚未渲染'}
        self.state['observations']['measure_text']={'width':120,'height':40}
        self.save()
        checkpoint=(self.folder/'agent-state.json').read_bytes()
        self.submit()
        self.assertEqual((self.folder/'agent-state.json').read_bytes(),checkpoint)
        self.assertEqual(len(self.workers),1)
        cloud.render_scene.assert_not_called()

    def test_explicit_remaining_image_consent_keeps_original_route_and_budget(self):
        self.task['usage']['image_requests']=1
        self.task['usage']['image_task_ids']=['completed-original-image']
        self.save()
        with patch('backend.app.vibe_image_route.resolve',return_value=({'api_key':'ephemeral'},self.route)):
            result=self.submit(self.image_body(expected_image_requests=1))
        self.assertTrue(result['new_images_allowed'])
        self.assertEqual(result['max_image_requests'],2)
        self.assertEqual(result['usage']['image_requests'],1)
        self.assertEqual(result['steps_continuations'][0]['authorized_image_remaining'],1)
        self.assertTrue(self.workers[0].args[3]['new_images_allowed'])
        self.assertEqual(self.workers[0].args[3]['layered_image_config'],{'api_key':'ephemeral'})
        self.assertEqual(self.workers[0].args[4].max_image_requests,2)
        self.assertIn('剩余 1 次图片',result['message'])
        self.assertNotIn('ephemeral',(self.folder/'task.json').read_text(encoding='utf-8'))
        cloud.generate_asset.assert_not_called()

    def test_image_permission_cannot_be_reenabled_after_revocation(self):
        original_task=copy.deepcopy(self.task)
        original_state=copy.deepcopy(self.state)
        for location in ('task','state'):
            self.task=copy.deepcopy(original_task);self.state=copy.deepcopy(original_state)
            (self.task if location=='task' else self.state)['new_images_allowed']=False
            self.save()
            with self.subTest(location=location):
                result=cloud.latest(self.identity,'shot1',self.req)
                self.assertTrue(result['can_continue_steps'])
                self.assertEqual(result['continue_steps_image_remaining'],2)
                self.assertFalse(result['continue_steps_images_allowed'])
                with self.assertRaises(HTTPException) as error:self.submit(self.image_body())
                self.assertIn('原图片授权已关闭',error.exception.detail)
        self.assertFalse(self.workers)

    def test_zero_image_budget_needs_no_image_config(self):
        self.task['max_image_requests']=0;self.save()
        latest=cloud.latest(self.identity,'shot1',self.req)
        self.assertEqual(latest['continue_steps_image_remaining'],0)
        self.assertFalse(latest['continue_steps_images_allowed'])
        with patch('backend.app.vibe_image_route.resolve',side_effect=AssertionError('must not resolve')):
            result=self.submit()
        self.assertEqual(result['max_image_requests'],0)
        self.assertFalse(result['new_images_allowed'])

    def test_changed_image_route_blocks_only_image_authorized_continuation(self):
        with patch('backend.app.vibe_image_route.resolve',return_value=({'api_key':'unused'},{**self.route,'key':'e'*64})):
            with self.assertRaises(HTTPException) as failed:self.submit(self.image_body())
        self.assertIn('原任务渠道不一致',failed.exception.detail)
        self.assertFalse(self.workers)
        self.assertEqual(cloud._read_job(self.project,self.task_id)['status'],'failed')
        self.submit()
        self.assertEqual(len(self.workers),1)

    def test_stale_image_usage_or_limit_rejected_without_enabling_images(self):
        for body in (self.image_body(expected_image_requests=1),self.image_body(expected_image_limit=3),
                     self.body(expected_image_requests=1,expected_image_limit=2)):
            with self.subTest(body=body),self.assertRaises(HTTPException):self.submit(body)
        self.assertFalse(self.workers)
        self.assertNotIn('new_images_allowed',cloud._read_job(self.project,self.task_id))

    def test_identical_manual_request_is_idempotent_even_after_terminal_state(self):
        first=self.submit()
        second=self.submit()
        self.assertTrue(second['steps_continuation_replayed'])
        self.assertEqual(first['usage'],second['usage'])
        self.assertEqual(len(self.workers),1)
        cloud._active.clear();cloud._update(self.folder/'task.json',status='failed')
        third=self.submit()
        self.assertTrue(third['steps_continuation_replayed'])
        self.assertEqual(len(self.workers),1)
        self.assertEqual(len(third['steps_continuations']),1)

    def test_replay_with_changed_authorization_is_rejected(self):
        self.submit()
        for change in (dict(additional_model_calls=1),dict(expected_model_calls=4),
                       dict(expected_model_limit=31),dict(confirm_remaining_images=True,
                           expected_image_requests=0,expected_image_limit=2)):
            with self.subTest(change=change),self.assertRaises(HTTPException):self.submit(self.body(**change))
        self.assertEqual(len(self.workers),1)

    def test_confirm_false_and_stale_counts_never_start(self):
        for change in (dict(confirm_model=False),dict(expected_model_calls=2),dict(expected_model_limit=31),
                       dict(additional_model_calls=21)):
            with self.subTest(change=change),self.assertRaises(HTTPException):self.submit(self.body(**change))
        self.assertFalse(self.workers)
        self.assertEqual(cloud._read_job(self.project,self.task_id)['usage'],self.usage)
        self.assertEqual(cloud._read_job(self.project,self.task_id)['max_model_calls'],30)

    def test_exhausted_original_allowance_needs_explicit_addition(self):
        self.task['usage']['model_calls']=[dict(status='completed') for _ in range(29)]+[copy.deepcopy(self.usage['model_calls'][-1])]
        self.save()
        self.assertTrue(cloud.latest(self.identity,'shot1',self.req)['can_continue_steps'])
        with self.assertRaises(HTTPException):self.submit(self.body(expected_model_calls=30))
        result=self.submit(self.body(expected_model_calls=30,additional_model_calls=2))
        self.assertEqual(result['max_model_calls'],32)
        self.assertEqual(len(result['usage']['model_calls']),30)
        self.assertIn('剩余 2 次',result['message'])

    def test_total_cap_fifty_never_resets_paid_usage(self):
        self.task['max_model_calls']=50
        self.task['usage']['model_calls']=[dict(status='completed') for _ in range(49)]+[copy.deepcopy(self.usage['model_calls'][-1])]
        self.save()
        latest=cloud.latest(self.identity,'shot1',self.req)
        self.assertFalse(latest['can_continue_steps'])
        self.assertIn('50',latest['continue_steps_unavailable_reason'])
        for extra in (0,1):
            with self.subTest(extra=extra),self.assertRaises(HTTPException):
                self.submit(self.body(expected_model_calls=50,expected_model_limit=50,additional_model_calls=extra))
        self.assertFalse(self.workers)

    def test_only_last_empty_completed_response_is_eligible(self):
        for code in ('model_truncated','model_connection_error',None):
            self.task['usage']['model_calls'][-1]['error_code']=code;self.save()
            with self.subTest(code=code):
                self.assertFalse(cloud.latest(self.identity,'shot1',self.req)['can_continue_steps'])
                with self.assertRaises(HTTPException):self.submit()
        self.assertFalse(self.workers)

    def test_completed_running_and_legacy_jobs_are_not_resumed(self):
        baseline=copy.deepcopy(self.task)
        for change in (dict(status='completed'),dict(status='running'),dict(workflow_version='old-v2'),
                       dict(template_mode='shotcraft'),dict(shot_id='other-shot')):
            self.task={**copy.deepcopy(baseline),**change};self.save()
            with self.subTest(change=change),self.assertRaises(HTTPException):self.submit()
        self.assertFalse(self.workers)

    def test_pending_image_blocks_even_model_only_continuation(self):
        self.state['pending_image']={'image_task_id':'uncertain-existing-charge'};self.save()
        with self.assertRaises(HTTPException) as failed:self.submit()
        self.assertIn('待核对的图片',failed.exception.detail)
        self.assertFalse(self.workers)

    def test_changed_inputs_or_corrupt_sources_block_without_writing(self):
        before=(self.folder/'task.json').read_bytes()
        self.shot['intent']='改变镜头内容';self.persist()
        with self.assertRaises(HTTPException):self.submit()
        self.assertEqual((self.folder/'task.json').read_bytes(),before)
        self.shot['intent']='解释两组结果';self.persist()
        self.state['workspace']['entries']['core']['sha256']='0'*64;self.save()
        with self.assertRaises(HTTPException):self.submit()
        self.assertFalse(self.workers)

    def test_missing_or_malformed_checkpoint_is_unavailable(self):
        baseline=copy.deepcopy(self.state)
        for name,value in (('events',{}),('observations',[]),('sequence',True),
                           ('new_images_allowed','true'),('workspace',{}),('latest_code','bad')):
            self.state={**copy.deepcopy(baseline),name:value};self.save()
            with self.subTest(name=name),self.assertRaises(HTTPException):self.submit()
        (self.folder/'agent-state.json').unlink()
        with self.assertRaises(HTTPException):self.submit()
        self.assertFalse(self.workers)

    def test_bad_budget_receipt_is_not_silently_coerced(self):
        baseline=copy.deepcopy(self.task)
        for name,value in (('max_model_calls',True),('max_image_requests',None),('image_requests',True),
                           ('image_requests',3),('model_calls','invalid')):
            self.task=copy.deepcopy(baseline)
            if name in ('image_requests','model_calls'):self.task['usage'][name]=value
            else:self.task[name]=value
            self.save()
            with self.subTest(name=name,value=value),self.assertRaises(HTTPException):self.submit()
        self.assertFalse(self.workers)

    def test_active_task_and_parallel_limit_block(self):
        cloud._active[(str(self.project),'shot1')]=('different-task',threading.Event())
        with self.assertRaises(HTTPException) as failed:self.submit()
        self.assertEqual(failed.exception.status_code,409)
        self.assertFalse(cloud.latest(self.identity,'shot1',self.req)['can_continue_steps'])
        cloud._active.clear()
        cloud._active[('project-a','shot-a')]=('a',threading.Event())
        cloud._active[('project-b','shot-b')]=('b',threading.Event())
        with self.assertRaises(HTTPException) as failed:self.submit()
        self.assertEqual(failed.exception.status_code,429)
        self.assertFalse(self.workers)

    def test_preflight_or_api_key_failure_preserves_allowance(self):
        self.config['api_key']=''
        with self.assertRaises(HTTPException):self.submit()
        self.config['api_key']='unused-offline'
        self.preflight.side_effect=RuntimeError('runtime missing')
        with self.assertRaises(HTTPException):self.submit()
        self.assertFalse(self.workers)
        self.assertNotIn('steps_continuations',cloud._read_job(self.project,self.task_id))

    def test_worker_start_failure_keeps_checkpoint_and_replay_never_retries(self):
        checkpoint=(self.folder/'agent-state.json').read_bytes()
        with patch.object(cloud.threading.Thread,'start',side_effect=RuntimeError('thread unavailable')):
            with self.assertRaisesRegex(RuntimeError,'thread unavailable'):self.submit()
        self.assertNotIn((str(self.project),'shot1'),cloud._active)
        saved=cloud._read_job(self.project,self.task_id)
        self.assertEqual(saved['status'],'failed')
        self.assertEqual(saved['usage'],self.usage)
        self.assertEqual(len(saved['steps_continuations']),1)
        self.assertEqual((self.folder/'agent-state.json').read_bytes(),checkpoint)
        replay=self.submit()
        self.assertTrue(replay['steps_continuation_replayed'])
        self.assertEqual(len(self.workers),1)

    def test_http_route_ownership_and_strict_confirmation(self):
        url=self.base+f'/vibe-cloud-jobs/{self.task_id}/continue-steps'
        body=self.body().model_dump()
        missing=copy.deepcopy(body);missing.pop('confirm_model')
        self.assertEqual(self.http.post(url,json=missing).status_code,422)
        self.assertEqual(self.http.post(url,json=body,headers={'user':'2'}).status_code,404)
        result=self.http.post(url,json=body)
        self.assertEqual(result.status_code,202,result.text)
        self.assertEqual(result.json()['task_id'],self.task_id)
        self.assertEqual(len(self.workers),1)

    def test_strict_schema_has_no_silent_allowance_or_permission_upgrade(self):
        self.assertEqual(self.body().additional_model_calls,0)
        self.assertFalse(self.body().confirm_remaining_images)
        for field,value in (('confirm_model','true'),('confirm_remaining_images',1),('expected_model_calls',True),
                            ('expected_model_limit',True),('additional_model_calls',51),('additional_model_calls',-1),
                            ('expected_image_limit',True),('expected_image_requests',-1),('request_id','../bad')):
            raw=self.body().model_dump();raw[field]=value
            with self.subTest(field=field,value=value),self.assertRaises(ValidationError):
                cloud.ContinueStepsRequest.model_validate(raw)
        for missing in ('expected_image_requests','expected_image_limit'):
            raw=self.image_body().model_dump();raw.pop(missing)
            with self.subTest(missing=missing),self.assertRaises(ValidationError):
                cloud.ContinueStepsRequest.model_validate(raw)


class ContinueStepsMcpContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import sys
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'plugins'/'codex_bridge'))
        app=FastAPI();app.include_router(cloud.router)
        cls.document=app.openapi()
        cls.name='ocv_video_vibe_cloud_continue_steps'

    def arguments(self,**values):
        body=dict(confirm_model=True,additional_model_calls=0,expected_model_calls=3,expected_model_limit=30,
            request_id='manual-steps-0001',confirm_remaining_images=False,expected_image_requests=0,expected_image_limit=2)
        body.update(values)
        return dict(path=dict(identity='a'*32,shot_id='generic',task_id='b'*32),body=body)

    def contract(self):
        from ocv_mcp.contracts import bind
        bound,_=bind(self.document)
        self.assertIn(self.name,bound)
        return bound[self.name]

    def test_live_schema_requires_explicit_allowance_and_image_permission_choice(self):
        from jsonschema import Draft202012Validator
        op,schema=self.contract()
        self.assertEqual(op.access,'generate')
        validator=Draft202012Validator(schema)
        args=self.arguments()
        self.assertFalse(list(validator.iter_errors(args)))
        for key in args['body']:
            missing=copy.deepcopy(args);missing['body'].pop(key)
            with self.subTest(key=key):self.assertTrue(list(validator.iter_errors(missing)))
        for key,value in (('confirm_model',False),('confirm_remaining_images','true'),
                          ('expected_image_requests',None),('expected_image_limit',None),
                          ('additional_model_calls',51),('expected_model_calls',True)):
            invalid=self.arguments(**{key:value})
            with self.subTest(key=key,value=value):self.assertTrue(list(validator.iter_errors(invalid)))
        self.assertFalse(list(validator.iter_errors(self.arguments(confirm_remaining_images=True))))

    def test_incomplete_older_backend_contract_is_not_exposed(self):
        from ocv_mcp.contracts import bind
        for key in self.arguments()['body']:
            old=copy.deepcopy(self.document)
            old['components']['schemas']['ContinueStepsRequest']['properties'].pop(key)
            bound,unavailable=bind(old)
            with self.subTest(key=key):
                self.assertNotIn(self.name,bound)
                self.assertIn(self.name,unavailable)

    def test_cost_explains_manual_charge_and_defaults_to_no_images(self):
        from ocv_mcp.adapter import operation_cost
        op,schema=self.contract()
        cost=operation_cost(op,self.arguments(),schema)
        for expected in ('可能计费','追加 0 次','原用量保留','不新增图片请求','不覆盖正式镜头',
                         '不自动重试','不重放旧工具','同一request_id','不能重新开启'):
            self.assertIn(expected,cost)
        image_cost=operation_cost(op,self.arguments(confirm_remaining_images=True,expected_image_requests=1),schema)
        self.assertIn('已单独确认',image_cost)
        self.assertIn('原剩余 1 次',image_cost)
        self.assertIn('不增加图片额度',image_cost)
        self.assertIn('不切换渠道',image_cost)

    def test_mcp_generate_permission_and_commit_confirmation_are_still_required(self):
        from ocv_mcp.adapter import Adapter
        from test_ocv_mcp import FakeBackend
        backend=FakeBackend(self.document)
        with tempfile.TemporaryDirectory() as temporary:
            adapter=Adapter('http://127.0.0.1:8010',Path(temporary)/'state',
                transport_factory=lambda:backend,allow_generation=False)
            with self.assertRaisesRegex(ValueError,'未获 MCP 启动配置授权'):
                adapter.prepare(self.name,self.arguments(),'prepare-steps-0001')
            adapter.allow_generation=True
            plan=adapter.prepare(self.name,self.arguments(),'prepare-steps-0001')
            self.assertTrue(plan['requires_confirmation'])
            self.assertFalse(plan['arguments']['body']['confirm_remaining_images'])
            self.assertEqual(plan['arguments']['body']['additional_model_calls'],0)
            self.assertFalse(backend.calls)
            with self.assertRaisesRegex(ValueError,'明确授权'):
                adapter.commit(plan['plan_id'],confirmed=False)
            self.assertFalse(backend.calls,'prepare and unconfirmed commit must never dispatch generation')


if __name__=='__main__':unittest.main()
