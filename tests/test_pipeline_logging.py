import unittest
from unittest.mock import Mock, patch

from backend.app.pipeline import Job, JobStore, parse_noisy_progress_log


class PipelineLoggingTest(unittest.TestCase):
    def test_retry_tts_restarts_only_from_audio_review_checkpoint(self) -> None:
        job = Job(
            id="retry-tts-test",
            status="waiting_confirmation",
            step="tts",
            progress=30,
            artifacts={"audio": "/api/jobs/retry-tts-test/artifacts/final_output.wav"},
            request={"step_mode": True, "_step_mode_stage": "audio"},
        )
        store = JobStore()
        with (
            patch("backend.app.pipeline.append_generation_job_log"),
            patch("backend.app.pipeline.upsert_generation_job"),
            patch.object(store, "run_async") as run_async,
            patch("backend.app.pipeline.shutil.rmtree"),
        ):
            payload = store.retry_tts(job)

        self.assertEqual(payload["status"], "queued")
        self.assertEqual(payload["progress"], 0)
        self.assertEqual(payload["artifacts"], {})
        self.assertNotIn("_step_mode_stage", job.request)
        run_async.assert_called_once_with(job, resume=False)

    def test_tts_sentence_progress_updates_job_without_progress_bar_noise(self) -> None:
        job = Job(id="tts-test", status="running", step="tts", progress=8)
        store = JobStore()
        with (
            patch("backend.app.pipeline.append_generation_job_log"),
            patch("backend.app.pipeline.upsert_generation_job"),
        ):
            store.log(job, "[TTS_PROGRESS] 配音进度 5/10：原文第 9 句已生成｜测试文案")
        self.assertEqual(job.progress, 18)
        self.assertEqual(job.message, "配音进度 5/10：原文第 9 句已生成｜测试文案")
        self.assertIn("配音进度 5/10", job.logs[-1])
        self.assertNotIn("TTS_PROGRESS", job.logs[-1])

    def test_render_versions_keep_separate_progress_keys(self) -> None:
        subtitle = parse_noisy_progress_log(
            "[字幕版 1/2] █████░░ 36% Streaming frame 400/1929"
        )
        clean = parse_noisy_progress_log(
            "[纯净版 2/2] █████░░ 12% Streaming frame 120/1929"
        )
        self.assertEqual(subtitle, ("字幕版 1/2 Streaming frame", 36))
        self.assertEqual(clean, ("纯净版 2/2 Streaming frame", 12))

    def test_new_job_is_blocked_while_pipeline_is_stopping_or_pending(self) -> None:
        store = JobStore()
        stopping = Job(id="stopping", user_id=7, status="cancelled")
        pending = Job(id="pending", user_id=7, status="queued")
        store._jobs = {stopping.id: stopping, pending.id: pending}
        store._pipeline_owner_id = stopping.id

        self.assertIn("正在停止", store.new_job_block_reason(7) or "")

        store._pipeline_owner_id = None
        self.assertIn("等待或运行中", store.new_job_block_reason(7) or "")

    def test_tts_cancel_requests_graceful_stop_without_taskkill(self) -> None:
        job = Job(id="safe-tts-stop", status="running", step="tts")
        process = Mock()
        store = JobStore()
        store._processes[job.id] = process
        with (
            patch("backend.app.pipeline.append_generation_job_log"),
            patch("backend.app.pipeline.upsert_generation_job"),
            patch("backend.app.pipeline._request_graceful_tts_stop") as graceful_stop,
            patch("backend.app.pipeline._terminate_process_tree") as hard_stop,
        ):
            snapshot = store.cancel(job)
        graceful_stop.assert_called_once_with(process)
        hard_stop.assert_not_called()
        self.assertEqual(snapshot["status"], "cancelled")
        self.assertEqual(snapshot["step"], "tts")

    def test_cluster_cancel_does_not_claim_local_cuda_cleanup(self) -> None:
        job = Job(id="cluster-stop", status="running", step="tts", request={"tts_engine": "cluster"})
        store = JobStore()
        with (
            patch("backend.app.pipeline.append_generation_job_log"),
            patch("backend.app.pipeline.upsert_generation_job"),
            patch("backend.app.pipeline._request_graceful_tts_stop") as graceful_stop,
        ):
            snapshot = store.cancel(job)
        graceful_stop.assert_not_called()
        self.assertEqual(snapshot["message"], "正在取消集群云端任务")


if __name__ == "__main__":
    unittest.main()
