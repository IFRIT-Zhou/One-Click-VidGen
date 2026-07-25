import unittest
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
