import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app import pipeline


class StepWorkflowV2Test(unittest.TestCase):
    def test_initialize_and_explicit_waiting_transitions_are_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            jobs = root / "jobs"
            output = root / "output"
            job = pipeline.Job(
                id="guided-job",
                user_id=1,
                request={"step_mode": True, "project_name": "分步测试"},
            )
            store = pipeline.JobStore()
            store._jobs[job.id] = job
            store._cancel_events[job.id] = pipeline.threading.Event()
            with (
                patch.object(pipeline, "JOBS_DIR", jobs),
                patch.object(pipeline, "OUTPUT_DIR", output),
                patch.object(pipeline, "register_job_asset"),
                patch.object(pipeline, "upsert_generation_job"),
                patch.object(pipeline, "append_generation_job_log"),
            ):
                pipeline.initialize_step_workflow(job)
                self.assertTrue(pipeline.is_step_workflow_v2(job.request))
                self.assertEqual(job.request["_step_mode_stage"], "audio_running")
                state = output / job.request["_step_output_dir"] / "other" / "step_workflow_state_v2.json"
                self.assertTrue(state.is_file())

                job.status = "waiting_confirmation"
                pipeline.persist_step_workflow_state(job, "audio_review")
                snapshot = store.advance_step_workflow(job, "confirm_audio")
                self.assertEqual(snapshot["status"], "waiting_confirmation")
                self.assertEqual(snapshot["request"]["_step_mode_stage"], "visual_setup")

                with self.assertRaises(ValueError):
                    store.advance_step_workflow(job, "start_render")

    def test_srt_parser_has_no_workflow_state_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.srt"
            path.write_text(
                "1\n00:00:00,000 --> 00:00:01,000\n第一句\n\n"
                "2\n00:00:01,000 --> 00:00:02,000\n第二句\n",
                encoding="utf-8",
            )
            self.assertEqual(pipeline._parse_srt_texts(path), ["第一句", "第二句"])

    def test_partial_visual_runtime_is_restored_before_agent_resume(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            jobs = root / "jobs"
            workspace = root / "workspace"
            checkpoint = jobs / "guided-job" / "artifacts" / "visual_runtime" / "story_plan"
            (checkpoint / "assets").mkdir(parents=True)
            for name, content in (
                ("story_context.json", "{}"),
                ("story_plan.json", "{}"),
                ("poster_mapping.json", "[]"),
                ("visual_prompt_plan.json", "{}"),
            ):
                (checkpoint / name).write_text(content, encoding="utf-8")
            (checkpoint / "assets" / "poster_001_hash.jpg").write_bytes(b"image")
            job = pipeline.Job(id="guided-job", request={"step_mode": True})
            with (
                patch.object(pipeline, "JOBS_DIR", jobs),
                patch.object(pipeline, "WORKSPACE_DIR", workspace),
            ):
                self.assertTrue(pipeline.restore_step_visual_runtime_checkpoint(job))
            visual = workspace / "3_visual_template"
            self.assertTrue((visual / "story_plan.json").is_file())
            self.assertTrue((visual / "poster_mapping.json").is_file())
            self.assertTrue((visual / "assets" / "poster_001_hash.jpg").is_file())

    def test_long_guided_final_render_validates_combined_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            jobs = root / "jobs"
            workspace = root / "workspace"
            final = root / "final"
            state = jobs / "guided-long" / "artifacts" / "long_split_state.json"
            state.parent.mkdir(parents=True)
            state.write_text("{}", encoding="utf-8")
            job = pipeline.Job(
                id="guided-long",
                request={
                    "step_mode": True,
                    "_step_workflow_version": 2,
                    "_step_mode_stage": "render_running",
                    "video_render_variant": "raw",
                },
            )
            with (
                patch.object(pipeline, "JOBS_DIR", jobs),
                patch.object(pipeline, "WORKSPACE_DIR", workspace),
                patch.object(pipeline, "FINAL_DIR", final),
                patch.object(pipeline, "validate_visual_coverage") as coverage,
                patch.object(pipeline, "probe_media_duration", return_value=10.0),
                patch.object(pipeline, "validate_media_duration") as duration,
                patch.object(pipeline, "load_long_split_state", side_effect=AssertionError("must use combined output")),
            ):
                pipeline.require_validated_output(job, job.request)
            coverage.assert_called_once()
            duration.assert_called_once()


if __name__ == "__main__":
    unittest.main()
