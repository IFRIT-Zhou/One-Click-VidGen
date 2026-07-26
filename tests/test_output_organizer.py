import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app import pipeline


class OutputOrganizerTest(unittest.TestCase):
    def test_split_job_creates_editing_package(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            jobs = workspace / "jobs"
            final = workspace / "4_final_video"
            output = root / "output"
            job = pipeline.Job(id="job123", user_id=1, request={})
            job_dir = jobs / job.id
            parts = job_dir / "artifacts" / "parts"
            parts.mkdir(parents=True)
            (job_dir / "script.txt").write_text("测试文案", encoding="utf-8")

            audio_dir = workspace / "2_audio_srt"
            audio_dir.mkdir(parents=True)
            (audio_dir / "final_output.wav").write_bytes(b"audio")
            (audio_dir / "final_short.srt").write_text(
                "1\n00:00:00,000 --> 00:00:01,000\n测试字幕\n",
                encoding="utf-8",
            )
            final.mkdir(parents=True)
            (final / "final_with_subtitles.mp4").write_bytes(b"sub-video")
            (final / "final_raw_presentation.mp4").write_bytes(b"raw-video")

            for index, prompt in ((1, "第一张提示词"), (2, "第二张提示词")):
                part_name = f"part_{index:03d}"
                (parts / f"{part_name}_poster_mapping.json").write_text(
                    json.dumps(
                        [{
                            "macro_scene_id": "poster_001",
                            "includes_slides": ["scene_001"],
                            "image_prompt": prompt,
                            "reference_image_ids": ["图1"],
                        }],
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                (parts / f"{part_name}_fine_grained_timeline.json").write_text(
                    json.dumps(
                        [{
                            "slide_id": "scene_001",
                            "start": 0,
                            "end": 1,
                            "text_content": f"第 {index} 段",
                            "visual_summary": f"第 {index} 段",
                        }],
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                image_dir = parts / f"{part_name}_images"
                image_dir.mkdir()
                (image_dir / "poster_001_test.jpg").write_bytes(f"image-{index}".encode())

            with (
                patch.object(pipeline, "PROJECT_ROOT", root),
                patch.object(pipeline, "WORKSPACE_DIR", workspace),
                patch.object(pipeline, "JOBS_DIR", jobs),
                patch.object(pipeline, "FINAL_DIR", final),
                patch.object(pipeline, "OUTPUT_DIR", output),
                patch.object(pipeline, "register_job_asset"),
            ):
                result = pipeline.organize_project_output(job, {"project_name": "测试项目"})

            self.assertEqual(result.name, "测试项目")
            self.assertEqual(
                {path.name for path in result.iterdir() if path.is_dir()},
                {"input", "image", "video", "other"},
            )
            images = sorted((result / "image").glob("*.jpg"))
            self.assertEqual(len(images), 2)
            self.assertEqual(
                [path.with_suffix(".txt").read_text(encoding="utf-8") for path in images],
                ["第一张提示词", "第二张提示词"],
            )
            self.assertTrue((result / "video" / "最终视频_字幕版.mp4").is_file())
            self.assertTrue((result / "video" / "最终视频_纯净版.mp4").is_file())
            self.assertTrue((result / "other" / "最终字幕.srt").is_file())
            html_text = (result / "other" / "最终画面.html").read_text(encoding="utf-8")
            self.assertIn("../image/part_001_poster_001_test.jpg", html_text)
            self.assertIn("../image/part_002_poster_001_test.jpg", html_text)
            self.assertNotIn('window.base64Subtitle = "";', html_text)
            mapping = json.loads((result / "other" / "画面映射.json").read_text(encoding="utf-8"))
            self.assertEqual(mapping[0]["includes_slides"], ["part_001_scene_001"])
            self.assertEqual(mapping[1]["includes_slides"], ["part_002_scene_001"])
            self.assertEqual(mapping[0]["reference_image_ids"], ["图1"])
            timeline = json.loads((result / "other" / "画面时间线.json").read_text(encoding="utf-8"))
            self.assertEqual(
                [item["slide_id"] for item in timeline],
                ["part_001_scene_001", "part_002_scene_001"],
            )


if __name__ == "__main__":
    unittest.main()
