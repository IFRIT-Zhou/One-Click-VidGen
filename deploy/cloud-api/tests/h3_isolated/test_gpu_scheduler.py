import unittest
from gpu_scheduler import Scheduler
from tts_redis_status import TTSRedisHTTP


class FakeTTS:
    def __init__(self, q=0, active=0):
        self.q, self.active, self.paused = q, active, False
        self.events = []
        self.ready = True
    def snapshot(self):
        return dict(queue_depth=self.q, active_jobs=self.active, draining=self.paused,
                    consumer_alive=True, redis_ready=True)
    def drain(self):
        self.events.append("drain")
        self.paused = True
        return dict(draining=True, drained=self.active == 0, active_jobs=self.active)
    def resume(self):
        self.events.append("resume")
        self.paused = not self.ready
        return dict(resumed=self.ready)


class FakePhases:
    def __init__(self, tts):
        self.tts, self.events = tts, []
        self.fail_h3 = False
    def ensure_tts(self):
        assert self.tts.paused and self.tts.active == 0
        self.events.append("tts")
    def ensure_h3(self):
        assert self.tts.paused and self.tts.active == 0
        self.events.append("h3")
        if self.fail_h3:
            raise RuntimeError("GPU still occupied")
    def stop_all(self):
        assert self.tts.paused and self.tts.active == 0
        self.events.append("off")


class FakeH3:
    def __init__(self, n): self.n, self.runs, self.inflight = n, 0, False
    def has_pending(self): return self.n > 0
    def has_inflight(self): return self.inflight
    def run_one(self): self.n -= 1; self.runs += 1


class SchedulerTests(unittest.TestCase):
    def make(self, q=0, active=0, h3=0):
        self.tts, self.h3 = FakeTTS(q, active), FakeH3(h3)
        self.phase = FakePhases(self.tts)
        self.now = 0
        self.scheduler = Scheduler(self.tts, self.phase, self.h3,
                                   clock=lambda: self.now, tts_quantum_seconds=10)
        return self.scheduler

    def test_pending_tts_wakes_then_releases_when_idle(self):
        s = self.make(q=1)
        s.tick()
        self.assertEqual(s.mode, "tts")
        self.assertEqual(self.phase.events, ["tts"])
        self.tts.q = 0; self.tts.active = 1
        s.tick()
        self.assertEqual(self.phase.events, ["tts"])
        self.tts.active = 0
        s.tick()
        self.assertEqual(s.mode, "off")
        self.assertEqual(self.phase.events, ["tts", "off"])

    def test_drain_blocks_h3_until_active_tts_complete(self):
        s = self.make(q=5, h3=1)
        s.tick()
        self.tts.active = 1
        self.now = 11
        s.tick()
        self.assertEqual(s.mode, "tts_draining")
        self.assertEqual(self.h3.runs, 0)
        self.tts.active = 0
        s.tick()
        self.assertEqual(self.h3.runs, 1)
        self.assertEqual(self.phase.events, ["tts", "h3"])
        s.tick()
        self.assertEqual(self.phase.events, ["tts", "h3", "tts"])

    def test_h3_fairness_one_job_then_tts(self):
        s = self.make(h3=4)
        s.tick()
        self.tts.q = 1
        s.tick()
        self.assertEqual(self.phase.events, ["h3", "tts"])
        self.assertEqual(self.h3.runs, 1)

    def test_restart_never_stops_active_tts(self):
        s = self.make(active=1, h3=1)
        s.tick()
        self.assertEqual(s.mode, "recovering")
        self.assertEqual(self.phase.events, [])

    def test_restart_recovers_inflight_h3_before_new_tts(self):
        s = self.make(q=2, h3=1)
        self.h3.inflight = True
        s.tick()
        self.assertEqual(self.phase.events, ["h3"])
        self.assertNotIn("resume", self.tts.events)

    def test_readiness_failure_does_not_release_dispatch(self):
        s = self.make(q=1)
        self.tts.ready = False
        with self.assertRaises(RuntimeError): s.tick()
        self.assertTrue(self.tts.paused)
        self.assertEqual(s.mode, "recovering")

    def test_h3_start_failure_never_runs_job_or_resumes_tts(self):
        s = self.make(h3=1)
        self.phase.fail_h3 = True
        with self.assertRaises(RuntimeError): s.tick()
        self.assertEqual(self.h3.runs, 0)
        self.assertTrue(self.tts.paused)
        self.assertEqual(s.mode, "recovering")

    def test_uncertain_h3_recovers_before_waiting_tts(self):
        s = self.make(h3=1)
        self.phase.fail_h3 = True
        with self.assertRaises(RuntimeError): s.tick()
        self.tts.q = 2
        self.phase.fail_h3 = False
        s.tick()
        self.assertEqual(self.phase.events, ["h3", "h3"])
        self.assertNotIn("resume", self.tts.events)
        self.assertEqual(self.h3.runs, 1)

    def test_arrival_during_idle_drain_is_kept_for_next_tick(self):
        s = self.make()
        original = self.tts.drain
        calls = []
        def drain():
            calls.append(1)
            if len(calls) == 2: self.tts.q = 1
            return original()
        self.tts.drain = drain
        s.tick()
        self.assertEqual(s.mode, "off")
        self.assertEqual(self.tts.q, 1)
        s.tick()
        self.assertEqual(s.mode, "tts")

    def test_missing_drain_ack_fails_closed(self):
        s = self.make(h3=1)
        self.tts.drain = lambda: dict(active_jobs=0)
        s.tick()
        self.assertEqual(self.phase.events, [])

    def test_redis_status_uses_authenticated_active_count(self):
        class Redis:
            def llen(self, key):
                assert key == "raytts:queue"
                return 2
        adapter = TTSRedisHTTP(redis_client=Redis())
        adapter._request = lambda action: dict(draining=True, active_jobs=3, drained=False)
        result = adapter.snapshot()
        self.assertEqual(result["queue_depth"], 2)
        self.assertEqual(result["active_jobs"], 3)
        self.assertFalse(result["drained"])

    def test_redis_failure_never_reports_empty_queue(self):
        class Redis:
            def llen(self, key): raise ConnectionError("offline")
        adapter = TTSRedisHTTP(redis_client=Redis())
        adapter._request = lambda action: dict(draining=True, active_jobs=0, drained=True)
        with self.assertRaises(ConnectionError): adapter.snapshot()


if __name__ == "__main__": unittest.main()
