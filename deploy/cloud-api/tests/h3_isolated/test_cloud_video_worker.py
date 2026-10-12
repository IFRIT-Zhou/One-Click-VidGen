import base64
import hashlib
import importlib.util
from pathlib import Path
import sys
import unittest

import test_cloud_video_api as fixture
from sqlalchemy.orm import Session, sessionmaker

spec = importlib.util.spec_from_file_location("h3test.cloud_video_worker", Path(__file__).parents[2] / "app/cloud_video_worker.py")
worker = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = worker
spec.loader.exec_module(worker)


class Node:
    def __init__(self):
        self.calls = []
        self.state = "succeeded"
        self.busy = False
        self.fail_submit = False
        self.bad_artifact = False

    def call(self, action, **kwargs):
        self.calls.append((action, kwargs))
        if action == "status":
            return {"busy": self.busy}
        if action == "submit":
            if self.fail_submit:
                raise TimeoutError("secret upstream raw trace")
            return {"prompt_id": "fixed-prompt-id"}
        if action == "poll":
            return {"status": self.state, "timings": {"inference_seconds": 2, "prompt": "secret"}}
        if action == "cancel":
            self.busy = False
            if self.state == "running":
                self.state = "cancelled"
            return {"status": self.state}
        if action == "result":
            data = b"\0\0\0\x18ftypisom\0\0\0\0"
            return {"data_b64": base64.b64encode(data).decode(), "bytes": len(data), "sha256": "bad" if self.bad_artifact else hashlib.sha256(data).hexdigest()}
        raise AssertionError(action)


class WorkerTests(fixture.ApiTests):
    # Inherit fixtures/helpers only; API tests below also run in this suite.
    def queue(self, node):
        return worker.H3Queue(node, sessionmaker(fixture.engine, expire_on_commit=False), sleep=lambda _: None)

    def test_worker_success_frozen_attempt(self):
        job, _ = self.job()
        node = Node()
        queue = self.queue(node)
        self.assertTrue(queue.has_pending())
        self.assertTrue(queue.run_one())
        self.assertFalse(queue.has_pending())
        with Session(fixture.engine) as db:
            record = db.get(fixture.api.CloudVideoJob, job["job_id"])
            self.assertEqual(record.status, "succeeded")
            self.assertEqual(record.remote_prompt_id, "fixed-prompt-id")
            self.assertEqual(record.timings, {"inference_seconds": 2.0})
            submit = next(args for action, args in node.calls if action == "submit")
            self.assertEqual(record.attempt_token, submit["attempt_token"])
            self.assertTrue(fixture.api.storage_path(record.result_path).is_file())

    def test_ambiguous_submit_recovers_without_resubmit(self):
        job, _ = self.job()
        node = Node()
        node.fail_submit = True
        queue = self.queue(node)
        with self.assertRaises(worker.WorkerUnsafeState):
            queue.run_one()
        with Session(fixture.engine) as db:
            record = db.get(fixture.api.CloudVideoJob, job["job_id"])
            self.assertEqual(record.status, "running")
            token = record.attempt_token
        node.fail_submit = False
        self.assertTrue(self.queue(node).run_one())
        self.assertEqual(sum(action == "submit" for action, _ in node.calls), 1)
        with Session(fixture.engine) as db:
            self.assertEqual(db.get(fixture.api.CloudVideoJob, job["job_id"]).attempt_token, token)

    def test_unknown_never_resubmitted(self):
        job, _ = self.job()
        node = Node()
        node.fail_submit = True
        queue = self.queue(node)
        with self.assertRaises(worker.WorkerUnsafeState):
            queue.run_one()
        node.state = "unknown"
        node.fail_submit = False
        self.assertTrue(self.queue(node).run_one())
        with Session(fixture.engine) as db:
            record = db.get(fixture.api.CloudVideoJob, job["job_id"])
            self.assertEqual(record.status, "failed")
            self.assertEqual(record.error_code, "EXECUTION_OUTCOME_UNKNOWN")
        self.assertEqual(sum(action == "submit" for action, _ in node.calls), 1)

    def test_busy_prevents_terminal_and_gpu_release(self):
        job, _ = self.job()
        node = Node()
        node.busy = True
        with self.assertRaises(worker.WorkerUnsafeState):
            self.queue(node).run_one()
        with Session(fixture.engine) as db:
            self.assertEqual(db.get(fixture.api.CloudVideoJob, job["job_id"]).status, "running")

    def test_cancellation_wins_success_race(self):
        job, _ = self.job()
        node = Node()
        original = node.call
        def rpc(action, **kwargs):
            if action == "result":
                with Session(fixture.engine) as db:
                    db.get(fixture.api.CloudVideoJob, job["job_id"]).status = "cancelling"
                    db.commit()
            return original(action, **kwargs)
        node.call = rpc
        self.queue(node).run_one()
        with Session(fixture.engine) as db:
            record = db.get(fixture.api.CloudVideoJob, job["job_id"])
            self.assertEqual(record.status, "cancelled")
            self.assertIsNone(record.result_path)

    def test_fencing_blocks_stale_worker(self):
        job, _ = self.job()
        queue = self.queue(Node())
        handle = queue._claim()
        with Session(fixture.engine) as db:
            db.get(fixture.api.CloudVideoJob, job["job_id"]).attempt_token = "new-owner-token"
            db.commit()
        with self.assertRaises(worker.WorkerUnsafeState):
            queue._heartbeat(handle)


if __name__ == "__main__":
    unittest.main(verbosity=2)
