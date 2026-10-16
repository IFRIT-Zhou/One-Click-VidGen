import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).parents[2]
spec = importlib.util.spec_from_file_location("service_control", ROOT / "app/service_control.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ServiceControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.node3 = {
            "time": 100,
            "node_ip": "10.250.0.3",
            "reachable": True,
            "h3_running": True,
            "h3_busy": False,
            "h3_ready": True,
            "tts_running": False,
            "exclusive": True,
            "control_ready": True,
        }
        self.node20 = {
            "time": 100,
            "node_ip": "10.250.0.20",
            "reachable": True,
            "containers": {
                "tts": {"installed": True, "running": True},
                "h3": {"installed": False, "running": False},
            },
            "control_ready": True,
        }
        (self.root / "node-health.json").write_text(json.dumps(self.node3))
        (self.root / "node20-health.json").write_text(json.dumps(self.node20))
        (self.root / "placement-health.json").write_text(json.dumps({
            "time": 100, "ok": True, "target_node": "10.250.0.20",
            "actual_pin": "10.250.0.20", "lifecycle_target": "10.250.0.20",
            "transaction_pending": False,
        }))
        (self.root / "heartbeat.json").write_text(json.dumps({
            "time": 100, "policy": "dedicated", "scheduler_ready": True,
        }))
        self.common = {
            "admin_id": "admin-1", "device_id": "device-20",
            "node_ip": "10.250.0.20", "service": "tts", "action": "stop",
            "reason": "planned maintenance", "idempotency_key": "key-00000000000001",
            "observed": {"status": "ready", "container_running": True, "control_ready": True},
            "root": self.root, "now": 100,
        }

    def tearDown(self):
        self.temp.cleanup()

    def test_supported_pairs_and_public_shape(self):
        result, created = module.enqueue(**self.common)
        self.assertTrue(created)
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["action"], "stop")
        self.assertEqual(result["service"], "tts")
        self.assertNotIn("admin_id", result)
        self.assertEqual(len(module.list_pending(root=self.root)), 1)

    def test_unsupported_pair_fails_closed(self):
        with self.assertRaises(module.ServiceControlError) as caught:
            module.enqueue(**{**self.common, "node_ip": "10.250.0.20", "service": "h3"})
        self.assertEqual(caught.exception.code, "UNSUPPORTED_SERVICE")

    def test_idempotency_replay_and_conflict(self):
        first, created = module.enqueue(**self.common)
        self.assertTrue(created)
        replay, created = module.enqueue(**self.common)
        self.assertFalse(created)
        self.assertEqual(replay, first)
        with self.assertRaises(module.ServiceControlError) as caught:
            module.enqueue(**{**self.common, "action": "start"})
        self.assertEqual(caught.exception.code, "IDEMPOTENCY_CONFLICT")

    def test_concurrent_operation_is_busy(self):
        module.enqueue(**self.common)
        with self.assertRaises(module.ServiceControlError) as caught:
            module.enqueue(**{**self.common, "idempotency_key": "key-00000000000002"})
        self.assertEqual(caught.exception.code, "CONTROL_BUSY")

    def test_unreachable_and_busy_are_rejected(self):
        with self.assertRaises(module.ServiceControlError) as caught:
            module.enqueue(**{**self.common, "now": 200})
        self.assertEqual(caught.exception.code, "NODE_UNREACHABLE")
        with self.assertRaises(module.ServiceControlError) as caught:
            module.enqueue(**{**self.common, "idempotency_key": "key-00000000000002",
                               "now": 100, "observed": {"status": "busy", "container_running": True, "control_ready": True},
                               "node_ip": "10.250.0.3", "device_id": "device-3", "service": "h3"})
        self.assertEqual(caught.exception.code, "SERVICE_BUSY")

    def test_claim_and_terminal_result_are_idempotent(self):
        created, _ = module.enqueue(**self.common)
        record = module.claim(created["operation_id"], root=self.root, now=101)
        self.assertEqual(record["status"], "running")
        finished = module.finish(created["operation_id"], root=self.root, status="succeeded",
                                 result={"container_running": False, "ready": False, "secret": "redact"}, now=102)
        self.assertEqual(finished["status"], "succeeded")
        self.assertEqual(finished["result"], {"container_running": False, "ready": False})
        again = module.finish(created["operation_id"], root=self.root, status="failed",
                              error_code="OTHER", now=103)
        self.assertEqual(again["status"], "succeeded")

    def test_decorated_capabilities_fail_closed(self):
        values = module.decorate_services({
            "tts": {"status": "ready", "container_running": True, "control_ready": True},
            "h3": {"status": "unknown", "container_running": None, "control_ready": False},
        }, "10.250.0.20", root=self.root, now=100)
        self.assertTrue(values["tts"]["control"]["supported"])
        self.assertFalse(values["h3"]["control"]["supported"])

    def test_capability_and_scheduler_must_both_be_ready(self):
        with self.assertRaises(module.ServiceControlError) as caught:
            module.enqueue(**{**self.common, "observed": {**self.common["observed"], "control_ready": False}})
        self.assertEqual(caught.exception.code, "CONTROL_AGENT_UNAVAILABLE")
        (self.root / "heartbeat.json").write_text(json.dumps({"time": 1, "policy": "generic"}))
        with self.assertRaises(module.ServiceControlError) as caught:
            module.enqueue(**self.common)
        self.assertEqual(caught.exception.code, "SCHEDULER_UNAVAILABLE")

    def test_successful_desired_state_persists_and_runner_finishes_operation(self):
        result, _ = module.enqueue(**self.common)
        self.assertIsNone(module.desired_state("10.250.0.20", "tts", root=self.root))
        finished = module.process_next(lambda record: {"container_running": False, "ready": False},
                                       root=self.root, now=101)
        self.assertEqual(finished["status"], "succeeded")
        self.assertFalse(module.desired_state("10.250.0.20", "tts", root=self.root))
        self.assertEqual(module.get_operation(result["operation_id"], root=self.root)["status"], "succeeded")


if __name__ == "__main__":
    unittest.main(verbosity=2)
