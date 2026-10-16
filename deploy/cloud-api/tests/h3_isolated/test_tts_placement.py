import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('tts_placement', ROOT/'tts_placement.py')
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)
A, B = m.NODES


class Proto:
    def __init__(self, **values):
        self.__dict__.update(values)

    def SerializeToString(self):
        return json.dumps(self.__dict__, sort_keys=True).encode()


def route(name, pin=A):
    return NS(route='/api/tts' if name == 'TTSIngress' else '', deployment_info=NS(
        deployment_config=Proto(version='stable-' + name, num_replicas=2),
        replica_config=Proto(ray_actor_options=json.dumps({'num_gpus': .5,
            'resources': {'tts_gpu': .5, 'node:' + pin: .001},
            'runtime_env': {'opaque': 'preserve'}}),
            deployment_def='opaque-code', init_args='opaque-args', init_kwargs='opaque-kwargs'),
        ingress_request_router=False))


class FakeRay:
    def __init__(self):
        self.target_node = A
        self.mutations = 0
        self.digest = 'stable'
        self.fail_after_repin = False

    def capture(self):
        return {}, {}

    def target(self, routes):
        return self.target_node

    def control_digest(self, routes):
        return self.digest

    def repin(self, target, digest, retry_version=None):
        if self.target_node != target or retry_version is not None:
            self.target_node = target
            self.mutations += 1
        if self.fail_after_repin:
            self.fail_after_repin = False
            raise RuntimeError('simulated crash')

    def snapshot(self):
        return {'target_node': self.target_node, 'actual_pin': self.target_node,
                'placement_ready': True, 'nodes': {}, 'control_actor_ids': {}}


class TransformTests(unittest.TestCase):
    def test_primary_service_evidence_is_independent_of_global_probe(self):
        details={'status':'HEALTHY','target_num_replicas':2,
                 'replicas':[{'state':'RUNNING','node_ip':B}]*2}
        self.assertEqual(m.primary_tts_summary(details,B)['state'],'ready')
        self.assertEqual(m.primary_tts_summary(details,A)['state'],'unassigned')
        details.update(status='UNHEALTHY',replicas=[])
        self.assertEqual(m.primary_tts_summary(details,B)['state'],'failed')
        details['status']='UPDATING'
        self.assertEqual(m.primary_tts_summary(details,B)['state'],'starting')
        details['status']='unexpected'
        self.assertEqual(m.primary_tts_summary(details,B)['state'],'unknown')

    def test_pin_preserves_every_other_option_without_mutating_input(self):
        options = {'num_gpus': .5, 'resources': {'tts_gpu': .5, 'node:'+A: .001},
                   'runtime_env': {'env_vars': {'SECRET_TEST': 'opaque'}}}
        original = copy.deepcopy(options)
        changed = m.repin_actor_options(options, B)
        self.assertEqual(options, original)
        self.assertEqual(changed['resources'], {'tts_gpu': .5, 'node:'+B: .001})
        self.assertEqual(changed['runtime_env'], options['runtime_env'])

    def test_retry_changes_worker_version_only(self):
        routes={name:route(name) for name in [m.WORKER,'AudioFinalizer','TTSDispatcher','TTSIngress']}
        class Args:
            def __init__(self, **kwargs): self.data=kwargs
            def SerializeToString(self): return self.data
        values=m.build_deployment_arguments(routes,A,'job',Args,'fresh-worker')
        for arg in values:
            config=json.loads(arg['deployment_config'])
            self.assertEqual(config['version'], 'fresh-worker' if arg['deployment_name']==m.WORKER
                             else 'stable-'+arg['deployment_name'])
        self.assertEqual(routes[m.WORKER].deployment_info.deployment_config.version,'stable-'+m.WORKER)

    def test_invalid_and_ambiguous_targets_rejected(self):
        with self.assertRaises(m.PlacementError):
            m.repin_actor_options({'resources': {'node:'+A: .001}}, '10.250.0.19')
        with self.assertRaises(m.PlacementError):
            m.actor_options_target({'resources': {'node:'+A: .001, 'node:'+B: .001}})

    def test_env_changes_only_target_and_rejects_duplicate(self):
        text = '# existing\nTOKEN=opaque\nTTS_WORKER_NODE_IP='+A+'\nPORT=5\n'
        self.assertEqual(m.update_env_text(text, B), text.replace(A, B))
        with self.assertRaises(m.PlacementError):
            m.update_env_text(text+'TTS_WORKER_NODE_IP='+A, B)

    def test_primary_online_independent_of_model_pin(self):
        result = m.node_summary([
            {'Alive': False, 'NodeManagerAddress': B, 'NodeID':'old', 'Resources':{'GPU':9}},
            {'Alive': True, 'NodeManagerAddress': B, 'NodeID':'new', 'Resources':{'GPU':1,'tts_gpu':1}}])
        self.assertEqual(result[B], {'online':True,'ray_alive':True,'gpu':1.,'tts_gpu':1.,'node_ids':['new']})
        self.assertFalse(result[A]['online'])

    def test_alive_node_without_tts_gpu_is_not_eligible(self):
        result=m.node_summary([{'Alive':True,'NodeManagerAddress':B,'NodeID':'n','Resources':{'GPU':1}}])
        self.assertTrue(result[B]['ray_alive'])
        self.assertFalse(result[B]['online'])

    def test_replica_readiness_demands_all_replicas_on_target(self):
        detail={'status':'HEALTHY','target_num_replicas':2,
                'replicas':[{'state':'RUNNING','node_ip':B},{'state':'RUNNING','node_ip':A}]}
        self.assertFalse(m.summarize_replicas(detail,B)['placement_ready'])
        detail['replicas'][1]['node_ip']=B
        self.assertTrue(m.summarize_replicas(detail,B)['placement_ready'])
        detail['replicas'].append({'state':'STOPPING','node_ip':A})
        self.assertFalse(m.summarize_replicas(detail,B)['placement_ready'])

    def test_serialized_nonworker_configuration_versions_and_route_preserved(self):
        routes={name:route(name) for name in [m.WORKER,'AudioFinalizer','TTSDispatcher','TTSIngress']}
        before=copy.deepcopy(routes)
        class Args:
            def __init__(self, **kwargs): self.data=kwargs
            def SerializeToString(self): return self.data
        args=m.build_deployment_arguments(routes,B,'job',Args)
        self.assertEqual(len(args),4)
        for arg in args:
            name=arg['deployment_name']
            self.assertEqual(arg['deployment_config'],before[name].deployment_info.deployment_config.SerializeToString())
            self.assertEqual(routes[name].deployment_info.replica_config.SerializeToString(),
                             before[name].deployment_info.replica_config.SerializeToString())
            if name != m.WORKER:
                self.assertEqual(arg['replica_config'],before[name].deployment_info.replica_config.SerializeToString())
            else:
                replica=json.loads(arg['replica_config'])
                self.assertEqual(m.actor_options_target(json.loads(replica['ray_actor_options'])),B)
                self.assertEqual(replica['init_args'],'opaque-args')
        ingress=next(a for a in args if a['deployment_name']=='TTSIngress')
        self.assertTrue(ingress['ingress'])
        self.assertEqual(ingress['route_prefix'],'/api/tts')


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.lifecycle=self.root/'lifecycle.json'
        self.env=self.root/'.env'
        self.lifecycle.write_text(json.dumps({'dispatch_node_ip':A,'workers':{A:{'token':'old-secret','generation':4}}}))
        self.env.write_text('OTHER=opaque\nTTS_WORKER_NODE_IP='+A+'\n')
        self.ray=FakeRay()
        self.drained=True
        self.health=True
        self.calls=[]
        self.manager=m.PlacementManager(self.ray,lifecycle=self.lifecycle,env=self.env,state=self.root/'state',http=self.http)
        # Windows runner lacks fcntl/fchmod; Linux production implementations remain real.
        self.addCleanup(patch.stopall)
        patch.object(m,'exclusive_lock',lambda p: m.contextlib.nullcontext()).start()
        def write(path,text,mode=0o600):
            path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)
        patch.object(m,'_atomic_write',write).start()

    def http(self,url,method='GET',headers=None):
        self.calls.append((url,headers))
        if url.endswith('/drain'):
            return {'draining':self.drained,'drained':self.drained,'active_jobs':0 if self.drained else 1}
        return {'ok':self.health,'dispatcher':{'dependencies':{
            'model_worker':{'ready':self.health},'audio_finalizer':{'ready':True}}}}

    def test_busy_dispatch_does_not_change_anything(self):
        self.drained=False
        with self.assertRaisesRegex(m.PlacementError,'DISPATCH_NOT_DRAINED'):
            self.manager.select(B)
        self.assertEqual(self.ray.mutations,0)
        self.assertEqual(json.loads(self.lifecycle.read_text())['dispatch_node_ip'],A)
        self.assertFalse(self.manager.transaction_path.exists())

    def test_select_persists_unique_credentials_and_does_not_resume(self):
        result=self.manager.select(B)
        config=json.loads(self.lifecycle.read_text())
        self.assertTrue(result['changed'])
        self.assertEqual(config['dispatch_node_ip'],B)
        self.assertEqual(config['workers'][A],{'token':'old-secret','generation':4})
        self.assertNotEqual(config['workers'][B]['token'],'old-secret')
        self.assertIn('TTS_WORKER_NODE_IP='+B,self.env.read_text())
        self.assertEqual(self.ray.mutations,1)
        self.assertFalse(any('/resume' in u for u,h in self.calls))
        self.assertNotIn('old-secret',json.dumps(result))

    def test_idempotent_same_target_does_not_need_pause(self):
        self.manager.select(B)
        self.drained=False
        result=self.manager.select(B)
        self.assertFalse(result['changed'])
        self.assertEqual(self.ray.mutations,1)

    def test_crash_after_repin_recovers_same_transaction(self):
        self.ray.fail_after_repin=True
        with self.assertRaises(RuntimeError):self.manager.select(B)
        self.assertTrue(self.manager.transaction_path.exists())
        self.assertEqual(json.loads(self.lifecycle.read_text())['dispatch_node_ip'],A)
        result=self.manager.select(B)
        self.assertTrue(result['changed'])
        self.assertEqual(self.ray.mutations,1)
        self.assertFalse(self.manager.transaction_path.exists())

    def test_other_target_cannot_override_pending_transition(self):
        self.ray.fail_after_repin=True
        with self.assertRaises(RuntimeError):self.manager.select(B)
        with self.assertRaisesRegex(m.PlacementError,'OTHER_TRANSITION_PENDING'):
            self.manager.select(A)

    def test_concurrent_deployment_change_aborts_recovery(self):
        self.ray.fail_after_repin=True
        with self.assertRaises(RuntimeError):self.manager.select(B)
        self.ray.digest='different'
        with self.assertRaisesRegex(m.PlacementError,'CONTROL_DEPLOYMENT_CHANGED'):
            self.manager.select(B)

    def test_retry_worker_requires_current_target_and_drained(self):
        with self.assertRaisesRegex(m.PlacementError,'RETRY_REQUIRES_CURRENT_TARGET'):
            self.manager.select(B,retry_worker=True)
        self.drained=False
        with self.assertRaisesRegex(m.PlacementError,'DISPATCH_NOT_DRAINED'):
            self.manager.select(A,retry_worker=True)
        self.drained=True
        result=self.manager.select(A,retry_worker=True)
        self.assertTrue(result['changed'])
        self.assertEqual(self.ray.mutations,1)
        transaction=json.loads((self.root/'state/last-transition.json').read_text())
        self.assertTrue(transaction['retry_version'].startswith('placement-retry-'))

    def test_snapshot_cannot_call_registered_target_ready_without_model_health(self):
        self.health=False
        self.assertFalse(self.manager.snapshot()['ready'])

    def test_untracked_mismatch_fails_closed(self):
        self.ray.target_node=B
        with self.assertRaisesRegex(m.PlacementError,'UNTRACKED_TARGET_MISMATCH'):
            self.manager.select(B)


if __name__=='__main__':unittest.main()
