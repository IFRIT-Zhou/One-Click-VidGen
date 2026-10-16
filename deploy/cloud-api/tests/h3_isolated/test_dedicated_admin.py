import importlib.util
from pathlib import Path
import os
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('dedicated_admin',Path(__file__).parents[2]/'app/admin_runtime.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class DedicatedAdminTests(unittest.TestCase):
    def test_idle_fallback_reports_released_models(self):
        ray={'dispatcher':{'consumer_alive':True,'redis_ready':True,'dependencies':{'model_worker':{'ready':False},'audio_finalizer':{'ready':True}}}}
        heartbeat={'time':100,'mode':'idle','queue_ready':True,'scheduler_ready':True,'policy':'dedicated','tts_node':'10.250.0.3','tts_state':'idle'}
        with patch.dict(os.environ,{'GPU_SCHEDULER_ENABLED':'1'}):
            runtime=m.cluster_runtime(ray,now=100,heartbeat=heartbeat)
        result=m.service_statuses(ray,runtime,now=100,node={'time':100,'reachable':True,'exclusive':True,'tts_running':False,'h3_running':False})
        self.assertTrue(runtime['healthy'])
        self.assertEqual(runtime['state'],'sleeping')
        self.assertTrue(all(service['state']=='sleeping' for service in result.values()))

    def test_primary_start_timeout_is_an_error_not_permanent_starting(self):
        ray={'dispatcher':{'consumer_alive':True,'redis_ready':True,'dependencies':{'model_worker':{'ready':False},'audio_finalizer':{'ready':True}}}}
        heartbeat={'time':100,'mode':'dedicated_starting','queue_ready':True,'scheduler_ready':False,'policy':'dedicated','tts_node':'10.250.0.20','tts_state':'unavailable','tts_blocked_reason':'TTS_MODEL_START_TIMEOUT'}
        with patch.dict(os.environ,{'GPU_SCHEDULER_ENABLED':'1'}):
            runtime=m.cluster_runtime(ray,now=100,heartbeat=heartbeat)
        result=m.service_statuses(ray,runtime,now=100,node={})
        self.assertFalse(runtime['healthy'])
        self.assertEqual(runtime['tts_blocked_reason'],'TTS_MODEL_START_TIMEOUT')
        self.assertEqual(result['tts']['state'],'error')
        self.assertFalse(result['tts']['ready'])

    def test_primary_health_independent_of_node3_tts_container(self):
        ray={'dispatcher':{'consumer_alive':True,'redis_ready':True,'dependencies':{'model_worker':{'ready':True},'audio_finalizer':{'ready':True}}}}
        with patch.dict(os.environ,{'GPU_SCHEDULER_ENABLED':'1'}):
            runtime=m.cluster_runtime(ray,now=100,heartbeat={'time':100,'mode':'dedicated','queue_ready':True,'scheduler_ready':True,'policy':'dedicated','tts_node':'10.250.0.20','h3_state':'ready'})
        result=m.service_statuses(ray,runtime,now=100,node={'time':100,'reachable':True,'exclusive':True,'tts_running':False,'h3_running':True,'h3_ready':True})
        self.assertTrue(result['tts']['ready']);self.assertTrue(result['h3']['ready'])
        self.assertEqual(result['tts']['node_ip'],'10.250.0.20')

    def test_windows_h3_blocker_does_not_mark_primary_tts_failed(self):
        ray={'dispatcher':{'consumer_alive':True,'redis_ready':True,'dependencies':{'model_worker':{'ready':True},'audio_finalizer':{'ready':True}}}}
        runtime={'policy':'dedicated','tts_node':'10.250.0.20','on_demand':True,'heartbeat_fresh':True,'scheduler_ready':True,'h3_state':'blocked'}
        result=m.service_statuses(ray,runtime,now=100,node={'time':100,'reachable':True,'exclusive':True,'local_h3':{'blocked':True}})
        self.assertTrue(result['tts']['healthy']);self.assertEqual(result['h3']['state'],'blocked')
