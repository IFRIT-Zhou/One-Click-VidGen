import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from backend.app.pipeline import (
    Job,
    JobStore,
    _standalone_subtitle_command,
    _standalone_subtitle_progress_handler,
    parse_noisy_progress_log,
    render_standalone_subtitle_video,
)


class PipelineLoggingTest(unittest.TestCase):
    def test_cancelled_job_can_be_explicitly_resumed(self) -> None:
        job = Job(
            id="resume-cancelled",
            status="cancelled",
            request={"tts_engine": "cluster", "_cloud_job_id": "old-cloud-job"},
        )
        store = JobStore()
        store._jobs[job.id] = job
        with (
            patch("backend.app.pipeline.append_generation_job_log"),
            patch("backend.app.pipeline.upsert_generation_job"),
            patch.object(store, "run_async") as run_async,
        ):
            payload = store.resume(job)

        self.assertEqual(payload["status"], "queued")
        self.assertEqual(payload["step"], "queued")
        self.assertNotIn("_cloud_job_id", job.request)
        run_async.assert_called_once_with(job, resume=True, priority=True)

    def test_load_persisted_resumes_queued_but_fails_running_job(self) -> None:
        rows = [
            {"id": "running", "user_id": 7, "status": "running", "created_at": 1},
            {"id": "queued", "user_id": 7, "status": "queued", "created_at": 2},
        ]
        store = JobStore()
        with (
            patch("backend.app.pipeline.load_generation_jobs", return_value=rows),
            patch("backend.app.pipeline.append_generation_job_log"),
            patch("backend.app.pipeline.upsert_generation_job"),
            patch.object(store, "run_async") as run_async,
        ):
            store.load_persisted()

        self.assertEqual(store.get("running").status, "failed")
        self.assertEqual(store.get("queued").status, "queued")
        run_async.assert_called_once_with(store.get("queued"))

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
        run_async.assert_called_once_with(job, resume=False, priority=True)

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

    def test_new_job_is_queued_while_pipeline_is_stopping_or_pending(self) -> None:
        store = JobStore()
        stopping = Job(id="stopping", user_id=7, status="cancelled")
        pending = Job(id="pending", user_id=7, status="queued")
        store._jobs = {stopping.id: stopping, pending.id: pending}
        store._pipeline_owner_id = stopping.id

        self.assertIsNone(store.new_job_block_reason(7))

        store._pipeline_owner_id = None
        self.assertIsNone(store.new_job_block_reason(7))

    def test_dispatcher_runs_queued_jobs_in_fifo_order(self) -> None:
        store = JobStore()
        first = Job(id="first", user_id=7, created_at=1)
        second = Job(id="second", user_id=7, created_at=2)
        store._jobs = {first.id: first, second.id: second}
        order: list[str] = []

        def run(job: Job, resume: bool = False) -> None:
            order.append(job.id)
            job.status = "completed"

        with patch.object(store, "_run_guarded", side_effect=run):
            store._pending_runs = {second.id: (False, False), first.id: (False, False)}
            store._dispatcher_running = True
            store._dispatch_loop()

        self.assertEqual(order, ["first", "second"])

    def test_dispatcher_preserves_step_mode_workspace(self) -> None:
        store = JobStore()
        paused = Job(id="paused", status="waiting_confirmation")
        queued = Job(id="queued", status="queued")
        store._jobs = {paused.id: paused, queued.id: queued}
        store._pending_runs = {queued.id: (False, False)}
        store._dispatcher_running = True

        with patch.object(store, "_run_guarded") as run:
            store._dispatch_loop()

        run.assert_not_called()
        self.assertFalse(store._dispatcher_running)

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

    def test_standalone_subtitle_command_prefers_nvenc_and_reports_progress(self) -> None:
        command = _standalone_subtitle_command(
            Path("input.mp4"),
            Path("output.mp4"),
            "subtitles=filename='test.srt'",
            source_is_video=True,
            use_nvenc=True,
        )
        self.assertIn("h264_nvenc", command)
        self.assertIn("-progress", command)
        self.assertIn("pipe:1", command)
        self.assertEqual(command[-1], "output.mp4")

        job = Job(id="subtitle-progress", status="running", step="subtitle_render", progress=12)
        store = JobStore()
        handler = _standalone_subtitle_progress_handler(job, store, 100.0)
        with (
            patch("backend.app.pipeline.append_generation_job_log"),
            patch("backend.app.pipeline.upsert_generation_job"),
        ):
            self.assertTrue(handler("out_time_us=50000000\n"))
        self.assertEqual(job.progress, 52)
        self.assertIn("50/100 秒", job.message)

    def test_standalone_subtitle_render_falls_back_to_cpu(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "input.mp4"
            subtitle = root / "input.srt"
            output = root / "output.mp4"
            source.write_bytes(b"source")
            subtitle.write_text("1\n00:00:00,000 --> 00:00:01,000\n测试\n", encoding="utf-8")
            job = Job(id="subtitle-fallback", status="running")
            store = JobStore()
            commands: list[list[str]] = []

            def fake_run(_job, _store, command, _label, **_kwargs):
                commands.append(command)
                if len(commands) == 1:
                    raise RuntimeError("NVENC unavailable")
                output.write_bytes(b"0" * 2048)

            with (
                patch("backend.app.pipeline.append_generation_job_log"),
                patch("backend.app.pipeline.upsert_generation_job"),
                patch("backend.app.pipeline.probe_media_duration", return_value=10.0),
                patch("backend.app.pipeline.run_command", side_effect=fake_run),
            ):
                render_standalone_subtitle_video(
                    job,
                    store,
                    source,
                    subtitle,
                    output,
                    style_key="navy_bg_white",
                    font_name="Microsoft YaHei",
                )

        self.assertEqual(len(commands), 2)
        self.assertIn("h264_nvenc", commands[0])
        self.assertIn("libx264", commands[1])
        self.assertTrue(any("自动切换 CPU x264" in line for line in job.logs))


if __name__ == "__main__":
    unittest.main()
