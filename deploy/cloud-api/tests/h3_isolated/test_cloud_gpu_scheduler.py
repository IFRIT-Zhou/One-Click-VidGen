import importlib.util
from pathlib import Path
import sys
import unittest
import io
import json

import test_cloud_video_worker  # supplies isolated database and app imports
from test_cloud_video_api import Base, engine
from sqlalchemy import String, Boolean
from sqlalchemy.orm import Mapped, mapped_column, Session
from unittest.mock import patch


class FakeCloudJob(Base):
    __tablename__ = 'cloud_jobs'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    status: Mapped[str] = mapped_column(String)
    ray_job_id: Mapped[str | None] = mapped_column(String, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)


sys.modules['h3test.models'].CloudJob = FakeCloudJob

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tts"))
# Load the scheduler's control dependencies into the isolated app package.
for name in ("service_control", "device_control_executor"):
    dependency_spec = importlib.util.spec_from_file_location("h3test." + name, ROOT / "app" / (name + ".py"))
    dependency = importlib.util.module_from_spec(dependency_spec)
    sys.modules[dependency_spec.name] = dependency
    dependency_spec.loader.exec_module(dependency)
spec = importlib.util.spec_from_file_location("h3test.cloud_gpu_scheduler", Path(__file__).parents[2] / "app/cloud_gpu_scheduler.py")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class FakeNode:
    def __init__(self):
        self.calls = []
        self.state = {"tts_running": False, "h3_running": False, "busy": False, "ready": False}

    def call(self, action):
        self.calls.append(action)
        if action == "start_tts":
            self.state.update(tts_running=True, h3_running=False)
        if action == "start_h3":
            self.state.update(tts_running=False, h3_running=True, ready=True)
        if action == "stop_all":
            self.state.update(tts_running=False, h3_running=False, busy=False)
        return dict(self.state)


class PhaseTests(unittest.TestCase):
    def test_resident_h3_requires_current_exclusive_health(self):
        node = FakeNode()
        node.state.update(h3_running=True, h3_ready=True, tts_running=False)
        phases = module.NodePhases(node)
        self.assertTrue(phases.h3_ready())
        node.state['local_h3'] = {'blocked': True}
        self.assertFalse(phases.h3_ready())
        node.state['local_h3'] = {'blocked': False}
        node.state['tts_running'] = True
        self.assertFalse(phases.h3_ready())

    def test_unavailable_tts_closes_admission_heartbeat(self):
        import tempfile
        from types import SimpleNamespace
        class Stop:
            done = False
            def is_set(self): return self.done
            def wait(self, seconds): self.done = True
        scheduler = SimpleNamespace(mode='dedicated_starting', tts_state='unavailable',
            tts_blocked_reason='TTS_MODEL_START_TIMEOUT', target_node='10.250.0.20',
            primary_state='online', h3_state='ready', blocked_reason=None,
            tts=SimpleNamespace(admission_snapshot=lambda: {'redis_ready':True,'consumer_alive':True}))
        with tempfile.TemporaryDirectory() as root, patch.dict(module.os.environ, {'GPU_SCHEDULER_STATE_DIR':root}):
            module.heartbeat_loop(scheduler, Stop())
            heartbeat = json.loads((Path(root)/'heartbeat.json').read_text())
        self.assertTrue(heartbeat['queue_ready'])
        self.assertFalse(heartbeat['scheduler_ready'])
        self.assertEqual(heartbeat['tts_blocked_reason'], 'TTS_MODEL_START_TIMEOUT')

    def test_admission_proves_consumer_alive_without_model(self):
        adapter = object.__new__(module.CloudTTSStatus)
        adapter.base_url = 'http://127.0.0.1:8000/api/tts'
        for alive in (False, True):
            payload = {'ok': False, 'dispatcher': {'consumer_alive': alive, 'redis_ready': True,
                       'dependencies': {'model_worker': {'ready': False}}}}
            with patch.object(module.CloudTTSStatus, 'snapshot', return_value={'redis_ready': True}), \
                    patch.object(module.urllib.request, 'urlopen', return_value=io.BytesIO(json.dumps(payload).encode())):
                result = adapter.admission_snapshot()
                self.assertEqual(result['consumer_alive'], alive)
                self.assertTrue(result['redis_ready'])

    def test_cloud_db_pending_wakes_tts_before_ray_forward(self):
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            db.add_all([FakeCloudJob(id='q', status='queued'), FakeCloudJob(id='r', status='running'),
                        FakeCloudJob(id='remote', status='running', ray_job_id='ray1'),
                        FakeCloudJob(id='cancel', status='queued', cancel_requested=True)])
            db.commit()
        adapter = object.__new__(module.CloudTTSStatus)
        with patch.object(module.TTSRedisHTTP, 'snapshot', return_value={'queue_depth': 1}):
            self.assertEqual(adapter.snapshot()['queue_depth'], 3)

    def test_waits_for_model_and_finalizer_before_tts_resume(self):
        node = FakeNode()
        probes = iter([{"ok": False}, {"ok": True, "dispatcher": {"dependencies": {"model_worker": {"ready": True}, "audio_finalizer": {"ready": True}}}}])
        sleeps = []
        phases = module.NodePhases(node, health_probe=lambda: next(probes), sleep=sleeps.append)
        phases.ensure_tts()
        self.assertEqual(sleeps, [3])
        self.assertNotIn("resume", node.calls)

    def test_h3_allows_busy_existing_execution_for_recovery(self):
        node = FakeNode()
        node.state["busy"] = True
        module.NodePhases(node).ensure_h3()
        self.assertTrue(node.state["busy"])

    def test_startup_timeout_does_not_resume(self):
        node = FakeNode()
        times = iter([0, 20])
        phases = module.NodePhases(node, tts_start_timeout=10, health_probe=lambda: {"ok": False}, monotonic=lambda: next(times))
        with self.assertRaises(RuntimeError):
            phases.ensure_tts()
        self.assertNotIn("resume", node.calls)

    def test_stop_checks_actual_state(self):
        node = FakeNode()
        module.NodePhases(node).stop_all()
        self.assertEqual(node.calls, ["stop_all", "status"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
