import importlib.util,os,unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('admin_runtime',Path(__file__).parents[2]/'app/admin_runtime.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class RuntimeTest(unittest.TestCase):
 def setUp(self):os.environ['GPU_SCHEDULER_ENABLED']='1'
 def check(self,mode='off',ready=False,age=0,control=True):
  return m.cluster_runtime({'ok':ready,'dispatcher':{'consumer_alive':control,'redis_ready':True,'dependencies':{'model_worker':{'ready':ready},'audio_finalizer':{'ready':True}}}},now=100,heartbeat={'time':100-age,'mode':mode,'scheduler_ready':True,'queue_ready':True})
 def test_sleep_is_healthy_but_not_loaded(self):
  r=self.check();self.assertTrue(r['healthy']);self.assertFalse(r['model_ready']);self.assertEqual(r['state'],'sleeping')
 def test_h3_allows_sleeping_tts(self):self.assertEqual(self.check('h3')['state'],'video')
 def test_starting(self):self.assertEqual(self.check('tts_starting')['state'],'starting')
 def test_expected_running_model_missing_is_error(self):self.assertFalse(self.check('tts')['healthy'])
 def test_ready(self):self.assertTrue(self.check('tts',True)['healthy'])
 def test_stale_or_future_heartbeat(self):
  self.assertFalse(self.check(age=16)['healthy']);self.assertFalse(self.check(age=-1)['healthy'])
 def test_control_fault_not_hidden(self):self.assertFalse(self.check(control=False)['healthy'])
 def test_unknown_mode_not_healthy(self):self.assertFalse(self.check('unknown')['healthy'])
 def test_legacy_requires_ready(self):
  os.environ['GPU_SCHEDULER_ENABLED']='0';self.assertFalse(self.check()['healthy']);self.assertTrue(self.check(ready=True)['healthy'])
class DualServiceTest(unittest.TestCase):
 def check(self,mode='off',**node):
  ray={'dispatcher':{'consumer_alive':True,'redis_ready':True,'dependencies':{'model_worker':{'ready':mode=='tts'},'audio_finalizer':{'ready':True}}}}
  runtime={'on_demand':True,'heartbeat_fresh':True,'scheduler_ready':True,'mode':mode}
  snapshot={'time':100,'reachable':True,'exclusive':True,'tts_running':False,'h3_running':False,**node}
  return m.service_statuses(ray,runtime,now=100,node=snapshot)
 def test_both_sleeping(self):self.assertTrue(all(x['state']=='sleeping' for x in self.check().values()))
 def test_h3_running_tts_sleeping(self):
  d=self.check('h3',h3_running=True,h3_ready=True,h3_busy=True);self.assertEqual(d['h3']['state'],'busy');self.assertEqual(d['tts']['state'],'sleeping')
 def test_tts_ready_h3_sleeping(self):
  d=self.check('tts',tts_running=True);self.assertTrue(d['tts']['ready']);self.assertEqual(d['h3']['state'],'sleeping')
 def test_h3_api_fault(self):self.assertEqual(self.check('h3',h3_running=True,h3_ready=False)['h3']['state'],'error')
 def test_expired_probe_not_sleep(self):self.assertEqual(self.check(time=40)['h3']['state'],'unknown')
 def test_unreachable_not_sleep(self):self.assertEqual(self.check(reachable=False)['tts']['state'],'unknown')
 def test_exclusive_violation(self):self.assertFalse(self.check(exclusive=False)['h3']['healthy'])
 def test_starting(self):self.assertEqual(self.check('h3_starting')['h3']['state'],'starting')
if __name__=='__main__':unittest.main()
