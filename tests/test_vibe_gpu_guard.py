"""GPU mutual exclusion within one OCV backend, without starting any service."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import time
import unittest
from unittest.mock import patch, Mock

from backend.app import managed_comfyui as engine


class TestGPUOwnership(unittest.TestCase):
    def setUp(self):
        for field, value in [('PROCESS', None), ('PORT', 0), ('LEASES', 0),
                             ('_ENGINE_STARTERS', 0), ('_STANDALONE_ACTIVE', False)]:
            item = patch.object(engine, field, value)
            item.start()
            self.addCleanup(item.stop)

    def test_no_engine_needed_and_child_failure_releases_claim(self):
        with patch.object(engine, 'ensure_ready') as start, patch.object(engine, 'stop') as stop:
            with self.assertRaisesRegex(ValueError, 'cutout failed'):
                with engine.standalone_gpu_lease():
                    self.assertTrue(engine._STANDALONE_ACTIVE)
                    raise ValueError('cutout failed')
            self.assertFalse(engine._STANDALONE_ACTIVE)
            with engine.standalone_gpu_lease():
                pass
            start.assert_not_called()
            stop.assert_not_called()

    def test_managed_lease_delays_and_wait_can_cancel_or_timeout(self):
        with patch.object(engine, 'ensure_ready', return_value='unused'):
            with engine.execution_lease():
                with self.assertRaises(InterruptedError):
                    with engine.standalone_gpu_lease(cancelled=lambda: True):
                        self.fail()
                with self.assertRaises(TimeoutError):
                    with engine.standalone_gpu_lease(timeout=.02):
                        self.fail()
                self.assertFalse(engine._STANDALONE_ACTIVE)

    def test_managed_and_next_cutout_cannot_overlap_standalone(self):
        ready = Event()
        def submit_video():
            with engine.execution_lease():
                self.assertFalse(engine._STANDALONE_ACTIVE)
                ready.set()
        with patch.object(engine, 'ensure_ready', return_value='unused') as start, ThreadPoolExecutor(1) as pool:
            with engine.standalone_gpu_lease():
                video = pool.submit(submit_video)
                until=time.monotonic()+2
                while not engine.LEASES and time.monotonic()<until:
                    time.sleep(.005)
                self.assertEqual(engine.LEASES, 1)
                start.assert_not_called()
                self.assertFalse(ready.is_set())
                with self.assertRaises(TimeoutError):
                    with engine.standalone_gpu_lease(timeout=.02):
                        self.fail()
            video.result(timeout=2)
            self.assertTrue(ready.is_set())
            self.assertEqual(engine.LEASES, 0)

    def test_live_queue_is_fail_closed_and_read_only(self):
        process = Mock()
        process.poll.return_value = None
        cases = [({},False), ({'error': 'busy'},False),
                 ({'queue_pending': [], 'queue_running':[1]},False),
                 ({'queue_running': [], 'queue_pending':[2]},False),
                 ({'queue_running': [], 'queue_pending':[]},True)]
        with patch.object(engine.requests,'Session') as session:
            client=session.return_value.__enter__.return_value
            for queue,want in cases:
                client.get.return_value.json.return_value=queue
                self.assertEqual(engine._managed_queue_idle(process, 9999, .1),want)
            client.get.assert_called_with('http://127.0.0.1:9999/queue',timeout=.1)
            client.post.assert_not_called()
            process.terminate.assert_not_called()

    def test_live_queue_waits_even_when_managed_lease_has_ended(self):
        with patch.object(engine,'_managed_queue_idle', return_value=False):
            with self.assertRaises(TimeoutError):
                with engine.standalone_gpu_lease(timeout=.025):
                    self.fail()
        self.assertFalse(engine._STANDALONE_ACTIVE)
