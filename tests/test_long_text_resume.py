import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from backend.app.pipeline import (
    Job,
    concat_videos,
    restore_long_split_checkpoint,
    restore_tts_checkpoint,
    reusable_part_outputs,
    save_tts_checkpoint,
    validate_visual_coverage,
)


class LongTextResumeTest(unittest.TestCase):
    @staticmethod
    def _write_wav(path: Path, frames: bytes = b"\x00\x00" * 800) -> None:
        import wave

        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(8000)
            audio.writeframes(frames)

    def test_completed_tts_is_checkpointed_and_restored_after_workspace_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "workspace"
            jobs = workspace / "jobs"
            job = Job(id="tts-resume")
            audio_dir = workspace / "2_audio_srt"
            self._write_wav(audio_dir / "final_output.wav")
            (audio_dir / "final_output.srt").write_text("1\n00:00:00,000 --> 00:00:00,100\n测试\n", encoding="utf-8")

            with patch("backend.app.pipeline.WORKSPACE_DIR", workspace), patch("backend.app.pipeline.JOBS_DIR", jobs):
                checkpoint = save_tts_checkpoint(job)
                shutil.rmtree(audio_dir)
                restored = restore_tts_checkpoint(job)

            self.assertEqual(restored, checkpoint)
            self.assertTrue((audio_dir / "final_output.wav").is_file())
            self.assertIn("测试", (audio_dir / "final_output.srt").read_text(encoding="utf-8"))

    def test_legacy_complete_segment_archive_restores_without_tts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "workspace"
            jobs = workspace / "jobs"
            job = Job(id="legacy-tts-resume")
            segments = jobs / job.id / "artifacts" / "tts_segments"
            self._write_wav(segments / "segment_0001.wav")
            self._write_wav(segments / "segment_0002.wav")
            (segments / "manifest.json").write_text(
                '{"segments":['
                '{"filename":"segment_0001.wav","text":"第一句"},'
                '{"filename":"segment_0002.wav","text":"第二句"}'
                ']}',
                encoding="utf-8",
            )

            with patch("backend.app.pipeline.WORKSPACE_DIR", workspace), patch("backend.app.pipeline.JOBS_DIR", jobs):
                restored = restore_tts_checkpoint(job)

            self.assertEqual(restored, segments)
            self.assertTrue((workspace / "2_audio_srt" / "final_output.wav").is_file())
            subtitle = (workspace / "2_audio_srt" / "final_output.srt").read_text(encoding="utf-8")
            self.assertIn("第一句", subtitle)
            self.assertIn("第二句", subtitle)

    def test_resume_restores_full_sources_over_leftover_segment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "workspace"
            jobs = workspace / "jobs"
            job = Job(id="resume-test")
            checkpoint = jobs / job.id / "artifacts" / "long_split_source"
            checkpoint.mkdir(parents=True)
            (checkpoint / "final_output.full.wav").write_bytes(b"full-audio")
            (checkpoint / "final_short.full.srt").write_text("full subtitle", encoding="utf-8")
            (checkpoint / "scene_timeline.full.json").write_text('[{"text_content":"full"}]', encoding="utf-8")
            leftover = workspace / "3_visual_template"
            leftover.mkdir(parents=True)
            (leftover / "scene_timeline.json").write_text('[{"text_content":"part 4"}]', encoding="utf-8")

            with patch("backend.app.pipeline.WORKSPACE_DIR", workspace), patch("backend.app.pipeline.JOBS_DIR", jobs):
                restored = restore_long_split_checkpoint(job)

            self.assertEqual(restored, checkpoint)
            self.assertEqual((workspace / "2_audio_srt" / "final_output.wav").read_bytes(), b"full-audio")
            self.assertIn("full", (leftover / "scene_timeline.json").read_text(encoding="utf-8"))
            self.assertNotIn("part 4", (leftover / "scene_timeline.json").read_text(encoding="utf-8"))

    def test_only_complete_requested_part_variants_are_reused(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            jobs = Path(temp_dir) / "jobs"
            job = Job(id="parts-test")
            parts = jobs / job.id / "artifacts" / "parts"
            parts.mkdir(parents=True)
            subtitles = parts / "part_001_with_subtitles.mp4"
            raw = parts / "part_001_raw.mp4"
            subtitles.write_bytes(b"subtitles")
            (parts / "part_001_scene_timeline.json").write_text(
                '[{"slide_id":"scene_001","text_content":"全文"}]', encoding="utf-8"
            )
            (parts / "part_001_poster_mapping.json").write_text(
                '[{"macro_scene_id":"poster_001","includes_slides":["scene_001"]}]',
                encoding="utf-8",
            )
            (parts / "part_001.srt").write_text(
                "1\n00:00:00,000 --> 00:00:10,000\n全文\n", encoding="utf-8"
            )
            images = parts / "part_001_images"
            images.mkdir()
            (images / "poster_001.png").write_bytes(b"image")

            with patch("backend.app.pipeline.JOBS_DIR", jobs), patch(
                "backend.app.pipeline.probe_media_duration", return_value=10.0
            ):
                self.assertEqual(
                    reusable_part_outputs(job, 1, "subtitles", 10.0),
                    {"video_with_subtitles": subtitles},
                )
                self.assertEqual(reusable_part_outputs(job, 1, "both", 10.0), {})
                raw.write_bytes(b"raw")
                self.assertEqual(
                    reusable_part_outputs(job, 1, "both", 10.0),
                    {"video_with_subtitles": subtitles, "video_raw": raw},
                )

    def test_raw_variant_skips_subtitle_coverage_but_keeps_visual_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            timeline = root / "timeline.json"
            mapping = root / "mapping.json"
            images = root / "images"
            images.mkdir()
            timeline.write_text(
                '[{"slide_id":"scene_001","text_content":"第一句"},'
                '{"slide_id":"scene_002","text_content":"第二句"}]',
                encoding="utf-8",
            )
            mapping.write_text(
                '[{"macro_scene_id":"poster_001",'
                '"includes_slides":["scene_001","scene_002"]}]',
                encoding="utf-8",
            )
            (images / "poster_001.png").write_bytes(b"image")

            result = validate_visual_coverage(
                timeline, mapping, images, subtitle_path=None
            )

            self.assertFalse(result["subtitle_checked"])
            self.assertEqual(result["covered_slide_count"], 2)

    def test_duplicate_or_missing_slide_mapping_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            timeline = root / "timeline.json"
            mapping = root / "mapping.json"
            images = root / "images"
            images.mkdir()
            timeline.write_text(
                '[{"slide_id":"scene_001","text_content":"一"},'
                '{"slide_id":"scene_002","text_content":"二"}]',
                encoding="utf-8",
            )
            mapping.write_text(
                '[{"macro_scene_id":"poster_001",'
                '"includes_slides":["scene_001","scene_001"]}]',
                encoding="utf-8",
            )
            (images / "poster_001.png").write_bytes(b"image")

            with self.assertRaisesRegex(RuntimeError, "画面映射未完整覆盖全文"):
                validate_visual_coverage(timeline, mapping, images, subtitle_path=None)

    def test_invalid_new_concat_does_not_overwrite_previous_valid_video(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "part.mp4"
            output = root / "final.mp4"
            source.write_bytes(b"part")
            output.write_bytes(b"previous-valid-video")

            def fake_run(_job, _store, command, _label, **_kwargs):
                Path(command[-1]).write_bytes(b"truncated-new-video")

            with patch("backend.app.pipeline.run_command", side_effect=fake_run), patch(
                "backend.app.pipeline.probe_media_duration", return_value=2.0
            ):
                with self.assertRaisesRegex(RuntimeError, "时长校验失败"):
                    concat_videos(
                        Job(id="atomic-test"),
                        Mock(),
                        [source],
                        output,
                        "测试拼接",
                        expected_duration=10.0,
                    )

            self.assertEqual(output.read_bytes(), b"previous-valid-video")

    def test_legacy_shared_checkpoint_is_migrated_only_for_its_part_job(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "workspace"
            jobs = workspace / "jobs"
            job = Job(id="legacy-resume")
            legacy = workspace / "temp_chunks" / "long_split_source"
            legacy.mkdir(parents=True)
            (legacy / "final_output.full.wav").write_bytes(b"legacy-audio")
            (legacy / "final_short.full.srt").write_text("legacy subtitle", encoding="utf-8")
            (legacy / "scene_timeline.full.json").write_text("[]", encoding="utf-8")
            (legacy / "story_plan.full.json").write_text('{"source":"legacy"}', encoding="utf-8")
            parts = jobs / job.id / "artifacts" / "parts"
            parts.mkdir(parents=True)
            (parts / "part_001_raw.mp4").write_bytes(b"complete part")

            with patch("backend.app.pipeline.WORKSPACE_DIR", workspace), patch("backend.app.pipeline.JOBS_DIR", jobs):
                restored = restore_long_split_checkpoint(job)

            persistent = jobs / job.id / "artifacts" / "long_split_source"
            self.assertEqual(restored, persistent)
            self.assertTrue((persistent / "story_plan.full.json").is_file())


if __name__ == "__main__":
    unittest.main()
