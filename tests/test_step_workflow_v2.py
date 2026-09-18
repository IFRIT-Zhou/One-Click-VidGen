import tempfile
import unittest
import json
import wave
from pathlib import Path
from unittest.mock import patch

from backend.app import pipeline


class StepWorkflowV2Test(unittest.TestCase):
    @staticmethod
    def _write_audio_snapshot(root: Path, timeline_count: int, srt_count: int) -> pipeline.Job:
        output = root / "output" / "guided"
        (output / "input").mkdir(parents=True)
        segment_dir = output / "other" / "tts_segments"
        segment_dir.mkdir(parents=True)
        with wave.open(str(output / "input" / "配音.wav"), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(b"\0\0" * 32000)
        (output / "other" / "最终字幕.srt").write_text("\n\n".join(
            f"{index}\n00:00:0{index - 1},000 --> 00:00:0{index},000\n第{index}句"
            for index in range(1, srt_count + 1)
        ), encoding="utf-8")
        (output / "other" / "画面时间线.json").write_text(json.dumps([
            {"slide_id": f"scene_{index:03d}", "text_content": f"第{index}句",
             "start": index - 1, "end": index}
            for index in range(1, timeline_count + 1)
        ], ensure_ascii=False), encoding="utf-8")
        (segment_dir / "manifest.json").write_text(json.dumps({
            "revision": 2,
            "segments": [{"index": 1, "text": "原始长句", "filename": "segment.wav"}],
        }, ensure_ascii=False), encoding="utf-8")
        return pipeline.Job(id="guided", request={
            "step_mode": True, "_step_workflow_version": 2, "_step_output_dir": "guided",
        })

    def test_pre_image_validation_rejects_deterministic_srt_timeline_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = self._write_audio_snapshot(root, timeline_count=3, srt_count=2)
            with patch.object(pipeline, "OUTPUT_DIR", root / "output"):
                with self.assertRaisesRegex(RuntimeError, "时间轴 3 句，SRT 2 句"):
                    pipeline.validate_step_audio_snapshot(job)

    def test_matching_refined_snapshot_gets_stable_revision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = self._write_audio_snapshot(root, timeline_count=2, srt_count=2)
            with patch.object(pipeline, "OUTPUT_DIR", root / "output"):
                first = pipeline.validate_step_audio_snapshot(job)
                second = pipeline.validate_step_audio_snapshot(job)
            self.assertEqual(first["fingerprint"], second["fingerprint"])
            self.assertEqual(first["sentence_count"], 2)

    def test_uploaded_audio_snapshot_builds_missing_segment_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            jobs = root / "jobs"
            workspace = root / "workspace"
            output = root / "output"
            audio_dir = workspace / "2_audio_srt"
            visual_dir = workspace / "3_visual_template"
            audio_dir.mkdir(parents=True)
            visual_dir.mkdir(parents=True)
            with wave.open(str(audio_dir / "final_output.wav"), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(16000)
                audio.writeframes(b"\0\0" * 40000)
            (audio_dir / "final_short.srt").write_text(
                "1\n00:00:00,100 --> 00:00:01,000\n第一句\n\n"
                "2\n00:00:01,200 --> 00:00:02,300\n第二句\n",
                encoding="utf-8",
            )
            (visual_dir / "scene_timeline.json").write_text(json.dumps([
                {"slide_id": "scene_001", "text_content": "第一句", "start": .1, "end": 1},
                {"slide_id": "scene_002", "text_content": "第二句", "start": 1.2, "end": 2.3},
            ], ensure_ascii=False), encoding="utf-8")
            job = pipeline.Job(id="uploaded", user_id=1, request={
                "step_mode": True,
                "skip_tts": True,
                "_step_workflow_version": 2,
                "_step_output_dir": "uploaded",
            })
            with (
                patch.object(pipeline, "JOBS_DIR", jobs),
                patch.object(pipeline, "WORKSPACE_DIR", workspace),
                patch.object(pipeline, "OUTPUT_DIR", output),
                patch.object(pipeline, "register_job_asset"),
                patch.object(pipeline.store, "update"),
            ):
                project = pipeline.sync_step_audio_snapshot(job)
            manifest = json.loads(
                (project / "other" / "tts_segments" / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertTrue(manifest["uploaded_finished_audio"])
            self.assertEqual([item["text"] for item in manifest["segments"]], ["第一句", "第二句"])
            self.assertAlmostEqual(manifest["segments"][0]["pause_after"], .2, places=3)
            self.assertTrue((project / "other" / "tts_segments" / "segment_0002.wav").is_file())
            self.assertTrue((project / "other" / "audio_revision.json").is_file())

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
