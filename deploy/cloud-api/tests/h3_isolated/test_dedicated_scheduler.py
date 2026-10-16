import importlib.util
import tempfile
import unittest
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

spec=importlib.util.spec_from_file_location('dedicated_scheduler',Path(__file__).resolve().parents[2]/'dedicated_scheduler.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
P,F=module.PRIMARY,module.FALLBACK

class Executor:
    def __init__(self): self.jobs=[]
    def submit(self,fn):
        future=Future();self.jobs.append((future,fn));return future
    def finish(self):
        future,fn=self.jobs.pop(0)
        try:future.set_result(fn())
        except Exception as exc:future.set_exception(exc)

class ImmediateExecutor(Executor):
    def submit(self,fn):
        future=super().submit(fn);self.finish();return future

class DedicatedTests(unittest.TestCase):
    def setUp(self):
        self.now=0;self.calls=[];self.executor=Executor()
        self.probe={'ok':True,'target_node':P,'lifecycle_target':P,'ready':True,'primary_tts':{'state':'ready'},'nodes':{P:{'online':True}}}
        self.status={'queue_depth':0,'active_jobs':0,'redis_ready':True}
        self.tts=SimpleNamespace(snapshot=lambda:self.status,drain=Mock(side_effect=self.drain),resume=Mock(return_value={'resumed':True}))
        self.phases=SimpleNamespace(stop_tts=Mock(side_effect=lambda:self.calls.append('stop_tts')),prepare_fallback=Mock(side_effect=lambda:self.calls.append('fallback')),ensure_h3=Mock(side_effect=lambda:self.calls.append('h3')),h3_ready=Mock(return_value=True))
        self.phases.ensure_idle=Mock(side_effect=lambda:self.calls.append('idle'))
        self.h3=SimpleNamespace(has_pending=Mock(return_value=False),has_inflight=Mock(return_value=False),run_one=Mock())
        self.placement=SimpleNamespace(snapshot=Mock(side_effect=lambda:dict(self.probe)),select=Mock(side_effect=self.select))
        self.scheduler=module.DedicatedScheduler(self.tts,self.phases,self.h3,self.placement,clock=lambda:self.now,executor=self.executor,probe_interval=10)
    def drain(self):
        self.calls.append('drain');return {'draining':True,'drained':self.status['active_jobs']==0,'active_jobs':self.status['active_jobs']}
    def select(self,target):
        self.calls.append(('select',target));self.probe.update(target_node=target,transaction_pending=False);return dict(self.probe)
    def tick(self,seconds=10):
        self.now+=seconds;self.scheduler.tick()
    def stabilize(self,count=3):
        for _ in range(count):self.tick()
    def finish_h3(self):
        if self.executor.jobs:self.executor.finish()
        self.tick()
    def test_primary_resident_tts_and_h3_independent(self):
        self.stabilize(2)
        self.assertEqual(self.scheduler.mode,'dedicated');self.assertTrue(self.tts.resume.called)
        self.assertEqual(len(self.executor.jobs),1);self.executor.finish();self.tick()
        self.assertEqual(self.scheduler.h3_state,'ready');self.phases.stop_tts.assert_not_called()
        self.phases.prepare_fallback.assert_not_called();self.placement.select.assert_not_called()
        self.h3.has_pending.return_value=True;self.tick();self.executor.finish();self.tick()
        self.h3.run_one.assert_called_once()
    def test_offline_without_pending_never_starts_fallback(self):
        self.probe['nodes'][P]['online']=False;self.stabilize(5)
        self.assertEqual(self.scheduler.primary_state,'offline');self.phases.prepare_fallback.assert_not_called();self.placement.select.assert_not_called()
    def test_no_failover_until_stable_offline(self):
        self.probe['nodes'][P]['online']=False;self.status['queue_depth']=1
        self.stabilize(2);self.placement.select.assert_not_called()
        self.tick()
        self.assertEqual(self.scheduler.target_node,F)
    def test_probe_failure_is_unknown_not_offline(self):
        self.placement.snapshot.side_effect=OSError('network');self.status['queue_depth']=1
        for _ in range(4):
            with self.assertRaises(OSError):self.tick()
        self.assertEqual(self.scheduler.primary_state,'unknown');self.placement.select.assert_not_called()
    def test_explicit_unknown_online_is_not_offline(self):
        self.probe['nodes'][P]['online']=None;self.status['queue_depth']=1
        for _ in range(4):
            try:self.tick()
            except RuntimeError:pass
            if self.executor.jobs:self.executor.finish()
        self.assertNotEqual(self.scheduler.primary_state,'offline');self.placement.select.assert_not_called()
    def test_recovery_drains_running_fallback_before_stop_and_select(self):
        self.probe['target_node']=F;self.status.update(queue_depth=1,active_jobs=1)
        self.stabilize(2);self.phases.stop_tts.assert_not_called();self.placement.select.assert_not_called()
        self.status['active_jobs']=0;self.tick()
        self.assertEqual(self.calls[-3:],['drain','stop_tts',('select',P)])
    def test_restart_pending_transaction_reconciles_even_if_target_primary(self):
        self.probe.update(transaction_pending=True,transaction_target=P);self.stabilize(2)
        self.assertEqual(self.calls[-2:],['drain',('select',P)])
    def test_restart_transaction_stops_fallback_before_primary_selection(self):
        self.probe.update(transaction_pending=True,transaction_target=P,lifecycle_target=F)
        self.tick()
        self.assertEqual(self.calls,['drain','stop_tts',('select',P)])
    def test_checking_primary_does_not_reactivate_old_fallback(self):
        self.probe['target_node']=F;self.status['queue_depth']=1
        self.tick()
        self.phases.prepare_fallback.assert_not_called();self.phases.stop_tts.assert_not_called()
        self.assertFalse(self.executor.jobs)
    def test_immediate_executor_completed_future_is_reaped_next_tick(self):
        self.scheduler.executor=ImmediateExecutor()
        self.stabilize(2);self.assertTrue(self.scheduler.h3_future.done())
        self.tick();self.assertIsNone(self.scheduler.h3_future);self.assertTrue(self.scheduler.h3_resident)
    def test_busy_h3_future_prevents_fallback(self):
        self.stabilize(2)
        self.probe['nodes'][P]['online']=False;self.status['queue_depth']=1;self.stabilize(4)
        self.placement.select.assert_not_called();self.phases.prepare_fallback.assert_not_called()
    def test_inflight_h3_reconciled_before_fallback(self):
        self.probe['nodes'][P]['online']=False;self.status['queue_depth']=1
        self.h3.has_inflight.return_value=True;self.stabilize(4)
        self.placement.select.assert_not_called()
    def test_local_windows_h3_blocker_never_changes_target(self):
        self.probe['nodes'][P]['online']=False;self.status['queue_depth']=1
        self.stabilize(2)
        self.phases.prepare_fallback.side_effect=RuntimeError('WINDOWS_H3_BUSY')
        with self.assertRaisesRegex(RuntimeError,'WINDOWS_H3_BUSY'):self.tick()
        self.placement.select.assert_not_called()
    def test_fallback_yields_to_h3_only_after_active_finishes(self):
        self.probe['target_node']=F;self.probe['nodes'][P]['online']=False;self.status.update(queue_depth=1,active_jobs=1)
        self.scheduler.tts_started=-500;self.h3.has_pending.return_value=True
        self.stabilize(3);self.phases.stop_tts.assert_not_called();self.assertFalse(self.executor.jobs)
        self.status['active_jobs']=0;self.tick()
        self.phases.stop_tts.assert_called_once();self.assertEqual(len(self.executor.jobs),1)
    def test_fallback_idle_releases_both_models_without_tts_start(self):
        self.probe['target_node']=F;self.probe['nodes'][P]['online']=False
        self.stabilize(3);self.tick()
        self.phases.prepare_fallback.assert_not_called();self.assertFalse(self.scheduler.h3_resident)
        self.phases.ensure_idle.assert_called_once();self.assertFalse(self.executor.jobs)
        self.assertEqual(self.scheduler.mode,'idle')
    def test_h3_failure_does_not_stop_primary_tts(self):
        self.phases.ensure_h3.side_effect=RuntimeError('WINDOWS_H3_BUSY')
        self.stabilize(2);self.executor.finish();self.tick()
        self.assertEqual(self.scheduler.mode,'dedicated');self.assertEqual(self.scheduler.h3_state,'blocked');self.assertTrue(self.tts.resume.called)
    def test_queue_outage_never_reconfigures(self):
        self.status['redis_ready']=False
        with self.assertRaisesRegex(RuntimeError,'QUEUE_UNAVAILABLE'):self.tick()
        self.placement.select.assert_not_called()

    def test_idle_h3_is_rechecked_without_restarting_warm_container(self):
        self.stabilize(2);self.finish_h3()
        self.tick(20)
        self.assertFalse(self.executor.jobs)
        self.tick(10)
        self.assertEqual(len(self.executor.jobs),1)
        self.executor.finish();self.tick()
        self.phases.h3_ready.assert_called_once()
        self.phases.ensure_h3.assert_called_once()
        self.phases.stop_tts.assert_not_called()

    def test_h3_restarts_after_idle_resident_container_lost(self):
        self.stabilize(2);self.finish_h3()
        self.phases.h3_ready.return_value=False
        self.tick(30);self.executor.finish();self.tick()
        self.assertEqual(self.phases.ensure_h3.call_count,2)
        self.assertEqual(self.scheduler.mode,'dedicated')
        self.assertEqual(self.scheduler.h3_state,'ready')

    def test_h3_probe_transport_failure_keeps_primary_tts_accepting(self):
        self.stabilize(2);self.finish_h3()
        self.phases.h3_ready.side_effect=OSError('unreachable')
        self.tick(30);self.executor.finish();self.tick()
        self.assertEqual(self.scheduler.h3_state,'blocked')
        self.assertEqual(self.scheduler.tts_state,'ready')
        self.assertEqual(self.scheduler.mode,'dedicated')
        self.phases.stop_tts.assert_not_called()

    def test_online_primary_start_timeout_falls_back_after_debounce(self):
        self.probe.update(ready=False,primary_tts={'state':'starting'});self.status['queue_depth']=1
        self.stabilize(2);self.finish_h3();self.tick(600)
        self.placement.select.assert_not_called()
        self.stabilize(2)
        self.assertEqual(self.scheduler.primary_state,'unavailable')
        self.assertEqual(self.scheduler.primary_failure_reason,'TTS_MODEL_START_TIMEOUT')
        self.assertEqual(self.scheduler.target_node,F)

    def test_explicit_model_failure_falls_back_even_with_ray_online(self):
        self.probe.update(ready=False,primary_tts={'state':'failed'});self.status['queue_depth']=1
        self.stabilize(2);self.placement.select.assert_not_called()
        self.tick();self.assertEqual(self.scheduler.target_node,F)
        self.assertEqual(self.scheduler.primary_failure_reason,'PRIMARY_TTS_FAILED')

    def test_busy_http_timeout_does_not_failover_healthy_controller(self):
        self.probe['ready']=False;self.status.update(queue_depth=1,active_jobs=1,draining=False)
        self.stabilize(3);self.finish_h3();self.tick(700)
        self.assertEqual(self.scheduler.primary_state,'online')
        self.assertEqual(self.scheduler.tts_state,'ready')
        self.placement.select.assert_not_called();self.phases.prepare_fallback.assert_not_called()
        self.tts.resume.assert_not_called()

    def test_busy_http_timeout_never_reopens_drained_admission(self):
        self.probe['ready']=False;self.status.update(queue_depth=1,active_jobs=0,draining=True)
        self.stabilize(3)
        self.tts.resume.assert_not_called();self.placement.select.assert_not_called()

    def test_lifecycle_drift_never_bypasses_not_ready(self):
        self.probe.update(ready=False,lifecycle_target=F)
        self.status.update(queue_depth=1,active_jobs=1,draining=False)
        self.stabilize(3)
        self.assertNotEqual(self.scheduler.tts_state,'ready')
        self.tts.resume.assert_not_called()

    def test_unknown_primary_service_never_uses_fallback_ready_as_primary_health(self):
        self.probe.update(target_node=F,primary_tts={'state':'unknown'},ready=True)
        self.status['queue_depth']=1;self.stabilize(4)
        self.assertEqual(self.scheduler.primary_state,'unknown');self.placement.select.assert_not_called()

    def test_failed_primary_recovery_waits_cooldown_before_reassignment(self):
        self.probe.update(ready=False,primary_tts={'state':'failed'});self.status['queue_depth']=1
        self.stabilize(3);self.assertEqual(self.scheduler.target_node,F)
        self.probe.update(ready=True,primary_tts={'state':'unassigned'})
        self.stabilize(3);self.assertEqual(self.scheduler.target_node,F)
        self.tick(300);self.tick()
        self.assertEqual(self.scheduler.target_node,P)
        self.assertEqual(self.placement.select.call_count,2)

    def test_cooldown_persists_across_scheduler_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            self.scheduler.state_path=Path(directory)/'policy.json'
            self.scheduler.wall_clock=lambda:1000+self.now
            self.probe.update(ready=False,primary_tts={'state':'failed'});self.status['queue_depth']=1
            self.stabilize(3)
            self.probe.update(ready=True,primary_tts={'state':'unassigned'})
            self.scheduler=module.DedicatedScheduler(self.tts,self.phases,self.h3,self.placement,
                clock=lambda:self.now,wall_clock=lambda:1000+self.now,executor=self.executor,
                state_path=Path(directory)/'policy.json')
            self.stabilize(3)
            self.assertEqual(self.scheduler.target_node,F)
            self.assertEqual(self.placement.select.call_count,1)

    def test_offline_idle_primary_releases_h3_without_loading_anything(self):
        self.probe['nodes'][P]['online']=False;self.stabilize(4)
        self.phases.ensure_idle.assert_called_once();self.assertFalse(self.executor.jobs)
        self.phases.prepare_fallback.assert_not_called()

    def test_idle_release_waits_for_h3_completion(self):
        self.stabilize(2);self.probe['nodes'][P]['online']=False
        self.stabilize(3);self.phases.ensure_idle.assert_not_called()
        self.finish_h3();self.phases.ensure_idle.assert_called_once()

    def test_fallback_h3_job_is_released_after_queue_becomes_empty(self):
        self.probe.update(target_node=F);self.probe['nodes'][P]['online']=False
        self.h3.has_pending.return_value=True;self.stabilize(3)
        self.executor.finish();self.h3.has_pending.return_value=False;self.tick()
        self.h3.run_one.assert_called_once();self.phases.ensure_idle.assert_called_once()

    def test_healthy_fallback_slice_does_not_drain_for_every_queued_job(self):
        self.probe.update(target_node=F);self.probe['nodes'][P]['online']=False
        self.status.update(queue_depth=2,active_jobs=0,draining=False)
        self.scheduler.tts_started=0;self.stabilize(3)
        self.tts.drain.assert_not_called();self.phases.prepare_fallback.assert_not_called()

    def test_healthy_accepting_primary_does_not_repeat_resume(self):
        self.status['draining']=False;self.stabilize(3)
        self.assertEqual(self.scheduler.tts_state,'ready')
        self.tts.resume.assert_not_called()
        self.status['draining']=True;self.tick()
        self.tts.resume.assert_called_once()

    def test_recovery_after_new_drain_resumes_even_when_snapshot_was_accepting(self):
        self.probe['target_node']=F;self.status.update(queue_depth=1,draining=False)
        self.stabilize(2)
        self.tts.resume.assert_called_once()
        self.assertEqual(self.calls[-3:],['drain','stop_tts',('select',P)])

    def test_fallback_after_new_drain_resumes_even_when_snapshot_was_accepting(self):
        self.probe['target_node']=F;self.probe['nodes'][P]['online']=False
        self.status.update(queue_depth=1,draining=False);self.stabilize(3)
        self.tts.resume.assert_called_once()

    def test_manual_primary_stop_suppresses_primary_and_falls_back_only_on_demand(self):
        self.scheduler.control_state = lambda node, service: False if (node, service) == (P, 'tts') else None
        self.stabilize(3)
        self.assertEqual(self.scheduler.primary_state, 'offline')
        self.assertEqual(self.scheduler.mode, 'idle')
        self.placement.select.assert_not_called()
        self.phases.prepare_fallback.assert_not_called()
        self.status['queue_depth'] = 1
        self.tick()
        self.assertEqual(self.scheduler.target_node, F)
        self.assertGreaterEqual(self.phases.prepare_fallback.call_count, 1)

    def test_manual_h3_stop_suppresses_resident_h3(self):
        self.scheduler.control_state = lambda node, service: False if (node, service) == (F, 'h3') else None
        self.stabilize(3)
        self.assertFalse(self.executor.jobs)
        self.phases.ensure_h3.assert_not_called()
        self.h3.has_pending.return_value = True
        self.probe['nodes'][P]['online'] = False
        self.status['queue_depth'] = 1
        self.stabilize(4)
        self.assertFalse(self.executor.jobs)
        self.h3.run_one.assert_not_called()
        self.phases.ensure_h3.assert_not_called()

if __name__=='__main__':unittest.main()
