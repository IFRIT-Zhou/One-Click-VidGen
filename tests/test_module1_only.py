import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from backend.app import pipeline


class Module1OnlyTest(unittest.TestCase):
    def test_step_mode_audio_checkpoint_is_waiting_not_failed(self) -> None:
        job = pipeline.Job(id="step-audio", request={"step_mode": True})
        store = Mock()
        with self.assertRaises(pipeline.GenerationPaused):
            pipeline.pause_for_step_confirmation(job, store, "audio", "试听配音")
        update = store.update.call_args.kwargs
        self.assertEqual(update["request"]["_step_mode_stage"], "audio")
        self.assertEqual(update["status"], "waiting_confirmation")
        self.assertEqual(update["step"], "await_audio")

    def test_pipeline_stops_after_tts_and_publishes_audio(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            jobs = workspace / "jobs"
            final = workspace / "4_final_video"
            tts_output = root / "TTS_Output"
            workspace.mkdir(parents=True)
            job = pipeline.Job(
                id="module1test",
                user_id=1,
                request={
                    "project_name": "单独配音",
                    "script": "这是一段用于测试模块一独立运行的故事文案。",
                    "module1_only": True,
                    "tts_voice_id": "voice_05.wav",
                    "tts_parallelism": 2,
                },
            )
            store = Mock()

            def fake_run_command(_job, _store, _command, _label, extra_env=None):
                output = workspace / "2_audio_srt"
                output.mkdir(parents=True, exist_ok=True)
                (output / "final_output.wav").write_bytes(b"RIFF-test")
                (output / "final_output.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\n测试\n", encoding="utf-8")

            with (
                patch.object(pipeline, "PROJECT_ROOT", root),
                patch.object(pipeline, "WORKSPACE_DIR", workspace),
                patch.object(pipeline, "JOBS_DIR", jobs),
                patch.object(pipeline, "FINAL_DIR", final),
                patch.object(pipeline, "TTS_OUTPUT_DIR", tts_output),
                patch.object(pipeline, "reset_generation_workspace"),
                patch.object(pipeline, "run_command", side_effect=fake_run_command),
                patch.object(pipeline, "register_job_asset"),
                patch.object(pipeline, "resolve_asr_python", side_effect=AssertionError("ASR must not run")),
            ):
                pipeline.run_pipeline(job, store)

            completed = [call.kwargs for call in store.update.call_args_list if call.kwargs.get("status") == "completed"]
            self.assertEqual(len(completed), 1)
            self.assertEqual(completed[0]["message"], "模块 1 配音生成完成")
            self.assertIn("audio", completed[0]["artifacts"])
            self.assertIn("module1_subtitle", completed[0]["artifacts"])
            self.assertTrue((jobs / job.id / "artifacts" / "final_output.wav").is_file())
            published = tts_output / "单独配音"
            self.assertEqual((published / "配音.wav").read_bytes(), b"RIFF-test")
            self.assertTrue((published / "配音字幕.srt").is_file())
            self.assertEqual((published / "文案.txt").read_text(encoding="utf-8"), job.request["script"])
            self.assertEqual(
                json.loads((published / "任务信息.json").read_text(encoding="utf-8"))["job_id"],
                job.id,
            )


if __name__ == "__main__":
    unittest.main()
