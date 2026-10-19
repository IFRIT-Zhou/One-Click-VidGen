import base64
import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock,patch

with patch.dict(sys.modules,{'fcntl':types.SimpleNamespace()}):
    spec=importlib.util.spec_from_file_location('node3_agent',Path(__file__).with_name('node3_agent.py'))
    agent=importlib.util.module_from_spec(spec);spec.loader.exec_module(agent)

class NodeSafetyTests(unittest.TestCase):
    def test_phase_lock_timeout_never_enters_operation_or_unlocks_other_owner(self):
        flock=Mock(side_effect=BlockingIOError())
        api=types.SimpleNamespace(flock=flock,LOCK_EX=2,LOCK_NB=4,LOCK_UN=8)
        with tempfile.TemporaryDirectory() as root,patch.object(agent,'fcntl',api),patch.object(agent.time,'monotonic',side_effect=[0,0,31]),patch.object(agent.time,'sleep'):
            with self.assertRaisesRegex(RuntimeError,'NODE_PHASE_BUSY'):
                with agent.phase_lock(str(Path(root)/'phase.lock')):
                    self.fail('busy operation must not run')
        self.assertEqual(flock.call_count,2)
        self.assertTrue(all(c.args[1] == 6 for c in flock.call_args_list))

    def test_phase_lock_releases_own_lock_even_when_dispatch_fails(self):
        flock=Mock()
        api=types.SimpleNamespace(flock=flock,LOCK_EX=2,LOCK_NB=4,LOCK_UN=8)
        with tempfile.TemporaryDirectory() as root,patch.object(agent,'fcntl',api):
            with self.assertRaisesRegex(ValueError,'dispatch failed'):
                with agent.phase_lock(str(Path(root)/'phase.lock')):
                    raise ValueError('dispatch failed')
        self.assertEqual([c.args[1] for c in flock.call_args_list],[6,8])

    def test_stopped_local_tts_does_not_require_global_drain(self):
        with patch.object(agent,'running',return_value=False),patch.object(agent,'drain_status') as drain,patch.object(agent,'docker') as docker:
            agent.stopped_tts()
        drain.assert_not_called();docker.assert_not_called()

    def test_running_local_tts_still_requires_drain(self):
        with patch.object(agent,'running',return_value=True),patch.object(agent,'drain_status',return_value={'draining':False,'drained':False,'active_jobs':1}),patch.object(agent,'docker') as docker:
            with self.assertRaisesRegex(RuntimeError,'TTS_NOT_DRAINED'):agent.stopped_tts()
        docker.assert_not_called()

    def test_stop_tts_preserves_h3(self):
        states={agent.TTS:True,agent.H3:True}
        def docker(*args):
            self.assertEqual(args,('stop','--time','60',agent.TTS));states[agent.TTS]=False
        with patch.object(agent,'running',side_effect=lambda name:states[name]),patch.object(agent,'drain_status',return_value={'draining':True,'drained':True,'active_jobs':0}),patch.object(agent,'docker',side_effect=docker):
            result=agent.dispatch({'action':'stop_tts'})
        self.assertFalse(result['tts_running']);self.assertTrue(states[agent.H3])

    def test_dedicated_h3_can_start_with_other_node_tts_active(self):
        states={agent.TTS:False,agent.H3:False}
        def docker(*args):
            self.assertEqual(args,('start',agent.H3));states[agent.H3]=True
        with patch.object(agent,'running',side_effect=lambda name:states[name]),patch.object(agent,'guard_windows_h3'),patch.object(agent,'docker',side_effect=docker),patch.object(agent,'drain_status') as drain,patch.object(agent,'api',return_value={'devices':[{'type':'cuda'}]}):
            agent.dispatch({'action':'prepare_dedicated_h3'})
        drain.assert_not_called();self.assertTrue(states[agent.H3])

    def test_windows_local_h3_blocks_container_switch_without_mutation(self):
        for action in ['start_h3','start_tts','prepare_dedicated_h3']:
            for status,error in [({'known':True,'busy':True,'blocked':True},'WINDOWS_H3_BUSY'),({'known':True,'busy':False,'blocked':True},'WINDOWS_H3_RESOURCE_BLOCKED'),({'known':False,'blocked':True},'WINDOWS_H3_PROBE_FAILED')]:
                with self.subTest(action=action,error=error),patch.object(agent,'windows_h3_status',return_value=status),patch.object(agent,'docker') as docker,patch.object(agent,'stop_h3') as h3,patch.object(agent,'stopped_tts') as tts:
                    with self.assertRaisesRegex(RuntimeError,error):agent.dispatch({'action':action})
                    docker.assert_not_called();h3.assert_not_called();tts.assert_not_called()

    def test_scheduler_role_keeps_fallback_restart_disabled(self):
        agent.SCHEDULER_ROLE.write_text(json.dumps({'schema':1,'owner':'ocv-gpu-scheduler',
            'node_ip':'10.250.0.3','primary_role':'h3','tts_container':agent.TTS}))
        with patch.object(agent,'guard_windows_h3'),patch.object(agent,'stop_h3'),patch.object(agent,'running',return_value=False),patch.object(agent,'docker') as docker:
            agent.dispatch({'action':'start_tts'})
        self.assertEqual(docker.call_args_list[0].args,('update','--restart=no',agent.TTS))
        self.assertEqual(docker.call_args_list[1].args,('start',agent.TTS))

    def test_scheduler_role_stop_does_not_contact_other_node_lifecycle(self):
        agent.SCHEDULER_ROLE.write_text(json.dumps({'schema':1,'owner':'ocv-gpu-scheduler',
            'node_ip':'10.250.0.3','primary_role':'h3','tts_container':agent.TTS}))
        with patch.object(agent,'running',return_value=False),patch.object(agent,'drain_status') as drain,patch.object(agent,'docker') as docker:
            agent.dispatch({'action':'stop_tts'})
        drain.assert_not_called()
        docker.assert_called_once_with('update','--restart=no',agent.TTS)

    def test_corrupt_role_blocks_before_any_lifecycle_mutation(self):
        agent.SCHEDULER_ROLE.write_text('{}')
        with patch.object(agent,'stop_h3') as h3,patch.object(agent,'stopped_tts') as tts,patch.object(agent,'docker') as docker:
            for action in ('start_h3','start_tts','stop_tts'):
                with self.assertRaisesRegex(RuntimeError,'SCHEDULER_ROLE_INVALID'):
                    agent.dispatch({'action':action})
        h3.assert_not_called();tts.assert_not_called();docker.assert_not_called()

    def test_malformed_role_blocks_before_any_lifecycle_mutation(self):
        agent.SCHEDULER_ROLE.write_text('{broken')
        with patch.object(agent,'stop_h3') as h3,patch.object(agent,'stopped_tts') as tts,patch.object(agent,'docker') as docker:
            with self.assertRaisesRegex(RuntimeError,'SCHEDULER_ROLE_INVALID'):
                agent.dispatch({'action':'stop_tts'})
        h3.assert_not_called();tts.assert_not_called();docker.assert_not_called()

    def test_stop_h3_requires_scheduler_role_and_keeps_tts_untouched(self):
        agent.SCHEDULER_ROLE.write_text(json.dumps({'schema':1,'owner':'ocv-gpu-scheduler',
            'node_ip':'10.250.0.3','primary_role':'h3','tts_container':agent.TTS}))
        states={agent.TTS:True,agent.H3:True}
        def docker(*args):
            self.assertEqual(args,('stop','--time','30',agent.H3));states[agent.H3]=False
        with patch.object(agent,'running',side_effect=lambda name:states[name]),patch.object(agent,'idle_h3'),patch.object(agent,'docker',side_effect=docker):
            result=agent.dispatch({'action':'stop_h3'})
        self.assertFalse(result['h3_running']);self.assertTrue(states[agent.TTS])

    def test_stop_h3_missing_scheduler_role_fails_before_idle_check(self):
        with patch.object(agent,'idle_h3') as idle,patch.object(agent,'docker') as docker:
            with self.assertRaisesRegex(RuntimeError,'SCHEDULER_ROLE_REQUIRED'):
                agent.dispatch({'action':'stop_h3'})
        idle.assert_not_called();docker.assert_not_called()

    def test_windows_probe_reports_idle_resident_model_blocker(self):
        result=types.SimpleNamespace(stdout=json.dumps({'known':True,'source':'windows_process_and_queue','processes':[{'pid':123,'queue_known':True,'memory_known':True,'busy':False,'dedicated_bytes':1024**3,'shared_bytes':0}]}).encode())
        with patch.object(agent,'WINDOWS_POWERSHELL') as exe,patch.object(agent.subprocess,'run',return_value=result):
            exe.exists.return_value=True
            status=agent.windows_h3_status()
        self.assertTrue(status['known']);self.assertFalse(status['busy']);self.assertTrue(status['blocked'])

    def test_windows_probe_fails_closed_when_queue_cannot_be_read(self):
        result=types.SimpleNamespace(stdout=json.dumps({'known':True,'processes':[{'pid':123,'queue_known':False}]}).encode())
        with patch.object(agent,'WINDOWS_POWERSHELL') as exe,patch.object(agent.subprocess,'run',return_value=result):
            exe.exists.return_value=True
            self.assertTrue(agent.windows_h3_status()['blocked'])

    def test_health_includes_windows_queue_without_container_mutation(self):
        local={'known':True,'busy':True,'blocked':True,'source':'windows_process_and_queue'}
        with patch.object(agent,'running',return_value=False),patch.object(agent,'windows_h3_status',return_value=local):
            status=agent.service_health()
        self.assertEqual(status['local_h3'],local)

    def test_windows_guard_blocks_fresh_submit_but_allows_existing_poll(self):
        with patch.object(agent,'guard_windows_h3',side_effect=RuntimeError('WINDOWS_H3_BUSY')) as guard,patch.object(agent,'poll',return_value={'status':'running'}):
            self.assertEqual(agent.dispatch({**self.req,'action':'submit'}),{'status':'running'})
            guard.assert_not_called()
            self.path.unlink()
            with self.assertRaisesRegex(RuntimeError,'WINDOWS_H3_BUSY'):
                agent.dispatch({**self.req,'action':'submit'})
        self.assertFalse(self.path.exists())

    def test_copy_marker_blocks_h3_start_without_touching_containers(self):
        (self.root/'model-cache-copy.active').write_text('123')
        with patch.object(agent,'docker') as docker:
            with self.assertRaisesRegex(RuntimeError,'MODEL_CACHE_COPY_IN_PROGRESS'):
                agent.dispatch({'action':'start_h3'})
        docker.assert_not_called()

    def test_oversize_submit_is_rejected_before_gpu_or_image_work(self):
        self.path.unlink()
        with patch.object(agent, 'guard_windows_h3'), patch.object(agent, 'idle_h3') as idle:
            with self.assertRaisesRegex(ValueError, 'H3_OUTPUT_PIXEL_LIMIT_EXCEEDED'):
                agent.dispatch({**self.req, 'action': 'submit',
                                'spec': {'width': 1664, 'height': 937}})
        idle.assert_not_called()

    def test_fresh_submit_duration_guard_precedes_gpu_start(self):
        self.path.unlink()
        with patch.object(agent,'guard_windows_h3'),patch.object(agent,'running',return_value=False),patch.object(agent,'idle_h3',side_effect=RuntimeError('GPU_START_REACHED')) as idle:
            for seconds,frames in [(15,362),(14.001,345),(14,362),(float('nan'),345),(-1,5)]:
                with self.subTest(seconds=seconds,frames=frames),self.assertRaisesRegex(ValueError,'H3_REQUEST_DURATION_LIMIT'):
                    agent.dispatch({**self.req,'action':'submit','spec':{'width':1536,'height':832,'duration_seconds':seconds,'num_frames':frames}})
            idle.assert_not_called()
            with self.assertRaisesRegex(RuntimeError,'GPU_START_REACHED'):
                agent.dispatch({**self.req,'action':'submit','spec':{'width':1536,'height':832,'duration_seconds':14,'num_frames':345}})

    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.patch=patch.object(agent,'ROOT',self.root);self.patch.start();self.addCleanup(self.patch.stop)
        role_patch=patch.object(agent,'SCHEDULER_ROLE',self.root/'gpu-scheduler-role.json')
        role_patch.start();self.addCleanup(role_patch.stop)
        (self.root/'jobs').mkdir();(self.root/'input').mkdir();(self.root/'output'/'jobs').mkdir(parents=True)
        self.req={'job_id':'h3job_'+'a'*32,'attempt_token':'b'*32}
        self.job={**self.req,'status':'submitting','prompt_id':'prompt-owned','submitted_at':0,
            'spec':{'width':640,'height':384,'num_frames':56,'fps':24}}
        self.path=agent.job_path(self.req)
        agent.atomic(self.path,self.job)

    def test_missing_history_remains_unknown_and_fenced(self):
        with patch.object(agent,'running',return_value=True),patch.object(agent,'api',side_effect=[{}, {'queue_running':[],'queue_pending':[]}]):
            result=agent.poll(self.req)
        self.assertEqual(result['status'],'unknown')
        self.assertEqual(json.loads(self.path.read_text())['status'],'submitting')
        with self.assertRaisesRegex(RuntimeError,'UNRESOLVED'):agent.idle_h3()

    def test_unknown_cancel_stops_process_before_terminal(self):
        states={agent.H3:True}
        def docker(*args):
            self.assertEqual(args,('stop','--time','30',agent.H3));states[agent.H3]=False
        with patch.object(agent,'running',side_effect=lambda name:states[name]),patch.object(agent,'api',return_value={'queue_running':[],'queue_pending':[]}),patch.object(agent,'docker',side_effect=docker) as stop:
            result=agent.reset_owned(self.req,self.path,self.job)
        self.assertEqual(result['status'],'cancelled');stop.assert_called_once()
        self.assertEqual(agent.unfinished(),[])

    def test_unknown_cancel_will_not_stop_foreign_task(self):
        with patch.object(agent,'running',return_value=True),patch.object(agent,'api',return_value={'queue_running':[[1,'other']],'queue_pending':[]}),patch.object(agent,'docker') as stop:
            with self.assertRaisesRegex(RuntimeError,'FOREIGN'):agent.reset_owned(self.req,self.path,self.job)
        stop.assert_not_called()

    def test_queue_cancel_only_interrupts_owned_running_prompt(self):
        calls=[]
        def api(path,data=None):
            calls.append((path,data))
            if path=='/queue':return {'queue_running':[[0,'prompt-owned']],'queue_pending':[]}
            return {}
        with patch.object(agent,'running',return_value=True),patch.object(agent,'api',side_effect=api):
            result=agent.dispatch({**self.req,'action':'cancel'})
        self.assertEqual(result['status'],'running')
        self.assertIn(('/interrupt',{'prompt_id':'prompt-owned'}),calls)
        self.assertNotIn(('/queue',{'delete':['prompt-owned']}),calls)

    def test_output_cannot_reference_other_job_or_traverse(self):
        wrong=self.root/'output'/'jobs'/'h3job_other_00001.mp4';wrong.write_bytes(b'mp4')
        with self.assertRaises(ValueError):agent.output_file(self.job,{'subfolder':'jobs','filename':wrong.name})
        good=self.root/'output'/'jobs'/(self.req['job_id']+'_00001_.mp4');good.write_bytes(b'mp4')
        self.assertEqual(agent.output_file(self.job,{'subfolder':'jobs','filename':good.name}),good)
        with self.assertRaises(ValueError):agent.output_file(self.job,{'subfolder':'jobs','filename':'../'+good.name})

    def test_invalid_media_becomes_definitive_failure_after_history(self):
        file=self.root/'output'/'jobs'/(self.req['job_id']+'_00001.mp4');file.write_bytes(b'mp4')
        history={'prompt-owned':{'status':{'status_str':'success'},'outputs':{'149':{'videos':[{'subfolder':'jobs','filename':file.name}]}}}}
        with patch.object(agent,'running',return_value=True),patch.object(agent,'api',return_value=history),patch.object(agent,'container_path',return_value='/container/output.mp4'),patch.object(agent,'docker',return_value=json.dumps({'streams':[{'codec_type':'video','width':1,'height':1}],'format':{'duration':'2'}})):
            result=agent.poll(self.req)
        self.assertEqual(result['status'],'failed');self.assertEqual(result['error_code'],'RESULT_VALIDATION_FAILED')

    def test_submit_persists_actual_response_id_and_frames(self):
        self.path.unlink()
        graph={node:{'inputs':{}} for node in ['137','157','131','181','129','149']}
        (self.root/'profiles-linux.json').write_text(json.dumps([{'workflow':graph}]))
        request={**self.req,'action':'submit','image_b64':base64.b64encode(b'image').decode(),
            'spec':{'prompt':'tree','num_frames':56,'duration_seconds':2,'seed':4,'width':640,'height':384}}
        def api(path,data=None):
            if path=='/queue':return {'queue_running':[],'queue_pending':[]}
            self.assertEqual(data['prompt']['131']['inputs'],{'expression':'round(a)','values.a':56})
            self.assertEqual(json.loads(self.path.read_text())['status'],'submitting')
            return {'prompt_id':'server-returned'}
        with patch.object(agent,'boot_id',return_value='fixture-boot'),patch.object(agent,'guard_windows_h3'),patch.object(agent,'running',side_effect=lambda name:name==agent.H3),patch.object(agent,'api',side_effect=api):
            agent.dispatch(request)
        self.assertEqual(json.loads(self.path.read_text())['prompt_id'],'server-returned')
        self.assertEqual(json.loads(self.path.read_text())['boot_id'],'fixture-boot')

    def test_missing_ledger_cancel_writes_tombstone_after_stop(self):
        self.path.unlink();states={agent.H3:True}
        def docker(*args):states[agent.H3]=False
        with patch.object(agent,'running',side_effect=lambda name:states[name]),patch.object(agent,'api',return_value={'queue_running':[],'queue_pending':[]}),patch.object(agent,'docker',side_effect=docker):
            agent.dispatch({**self.req,'action':'cancel'})
        self.assertEqual(json.loads(self.path.read_text())['status'],'cancelled')

    def test_reboot_marks_interrupted_without_resetting_container(self):
        agent.atomic(self.path,{**self.job,'boot_id':'previous-boot'})
        with patch.object(agent,'boot_id',return_value='current-boot'),patch.object(agent,'running',return_value=True),patch.object(agent,'api',side_effect=[{}, {'queue_running':[],'queue_pending':[]}]),patch.object(agent,'docker') as docker:
            result=agent.poll(self.req)
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['error_code'],'WORKER_RESTARTED')
        docker.assert_not_called()

    def test_same_boot_or_unreadable_boot_keeps_uncertain_submission_fenced(self):
        for current in ('previous-boot',None):
            agent.atomic(self.path,{**self.job,'boot_id':'previous-boot'})
            with patch.object(agent,'boot_id',return_value=current),patch.object(agent,'running',return_value=True),patch.object(agent,'api',side_effect=[{}, {'queue_running':[],'queue_pending':[]}]),patch.object(agent,'docker') as docker:
                self.assertEqual(agent.poll(self.req)['status'],'unknown')
            docker.assert_not_called()

if __name__=='__main__':unittest.main()
