import json
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import module1_agent_director as module1
import module4_video_render as visual
import story_agents
from backend.app.structural_blanks import parse_structural_blanks, segment_structural_script
from backend.app.cloud_tts import build_quote_payload
from backend.app import pipeline


def _wav(path: Path, seconds: float = 0.1) -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(24000)
        output.writeframes(b"\0\0" * round(24000 * seconds))


class StructuralBlankTests(unittest.TestCase):
    def test_marker_is_removed_and_forces_chunk_boundary(self) -> None:
        plan = segment_structural_script(
            "第一句没有结束【OCV留白：3秒】第二句继续。",
            lambda value: [value],
        )
        self.assertEqual(plan.chunks, ["第一句没有结束", "第二句继续。"])
        self.assertEqual(plan.pauses_after, [3.0, 0.0])
        self.assertNotIn("OCV留白", "".join(plan.chunks))

    def test_invalid_marker_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "格式不正确"):
            parse_structural_blanks("第一句【OCV留白三秒】第二句")
        with self.assertRaisesRegex(ValueError, "0.2"):
            parse_structural_blanks("第一句【OCV留白：31秒】第二句")

    def test_cloud_quote_never_receives_control_marker(self) -> None:
        payload = build_quote_payload({
            "script": "前句。【OCV留白：2.5秒】后句。",
            "cluster_voice_type": "preset",
            "cluster_voice_id": "voice_01",
        })
        texts = [item["text"] for item in payload["chunks"]]
        self.assertEqual(texts, ["前句。", "后句。"])
        self.assertNotIn("OCV留白", "".join(texts))

    def test_archive_timing_contains_exact_pause(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            first, second = root / "a.wav", root / "b.wav"
            _wav(first)
            _wav(second)
            archive = root / "archive"
            args = SimpleNamespace(
                tts_engine="indextts25", tts_voice_id="voice.wav", tts_speed=1,
                tts_volume=1, tts_pitch=0, tts_emotion="", tts_emotion_weight=.65,
                qwen_voice="", qwen_instructions="",
            )
            module1.export_tts_segments(
                archive, ["前句", "后句"], [first, second], args,
                pauses_after=[3.0, 0.0],
            )
            manifest = json.loads((archive / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["segments"][0]["pause_after"], 3.0)
            self.assertTrue(manifest["segments"][0]["structural_blank_after"])
            self.assertAlmostEqual(manifest["segments"][1]["start"], 3.1, places=2)

    def test_agent1_units_are_split_at_structural_boundary(self) -> None:
        scenes = [
            {"slide_id": "scene_001", "text_content": "前句"},
            {"slide_id": "scene_002", "text_content": "后句", "hard_boundary_before": True},
        ]
        raw = [{"start_slide_id": "scene_001", "end_slide_id": "scene_002"}]
        units = story_agents._normalize_semantic_units(raw, scenes)
        self.assertEqual([(u["start_slide_id"], u["end_slide_id"]) for u in units], [
            ("scene_001", "scene_001"), ("scene_002", "scene_002"),
        ])

    def test_agent2_mapping_is_split_at_structural_boundary(self) -> None:
        scenes = [
            {"slide_id": "scene_001", "start": 0, "end": 1, "text_content": "前句"},
            {"slide_id": "scene_002", "start": 4, "end": 5, "text_content": "后句", "hard_boundary_before": True},
        ]
        mapping = [{"includes_slides": ["scene_001", "scene_002"], "image_prompt": "同一画面", "reference_image_ids": []}]
        result = visual._finalize_mapping(mapping, scenes, {})
        self.assertEqual([item["includes_slides"] for item in result], [["scene_001"], ["scene_002"]])

    def test_corrected_subtitles_are_pushed_out_of_blank_interval(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            jobs = root / "jobs"
            workspace = root / "workspace"
            manifest_dir = jobs / "job1" / "artifacts" / "tts_segments"
            timeline_dir = workspace / "3_visual_template"
            subtitle_dir = workspace / "2_audio_srt"
            manifest_dir.mkdir(parents=True)
            timeline_dir.mkdir(parents=True)
            subtitle_dir.mkdir(parents=True)
            (manifest_dir / "manifest.json").write_text(json.dumps({"segments": [
                {"text": "前句。", "start": 0, "end": 4, "pause_after": 3, "structural_blank_after": True},
                {"text": "看到了吧？后续。", "start": 7, "end": 10, "pause_after": 0},
            ]}, ensure_ascii=False), encoding="utf-8")
            (timeline_dir / "scene_timeline.json").write_text(json.dumps([
                {"slide_id": "scene_001", "start": 0, "end": 4, "text_content": "前句。"},
                {"slide_id": "scene_002", "start": 4, "end": 5, "text_content": "看到了吧？"},
                {"slide_id": "scene_003", "start": 5, "end": 10, "text_content": "后续。", "hard_boundary_before": True},
            ], ensure_ascii=False), encoding="utf-8")
            logs = []
            store = SimpleNamespace(log=lambda _job, message: logs.append(message))
            with patch.object(pipeline, "JOBS_DIR", jobs), patch.object(pipeline, "WORKSPACE_DIR", workspace):
                applied = pipeline.apply_structural_blank_boundaries(SimpleNamespace(id="job1"), store)
            result = json.loads((timeline_dir / "scene_timeline.json").read_text(encoding="utf-8"))
            self.assertEqual(applied, 1)
            self.assertEqual(result[0]["end"], 4.0)
            self.assertEqual(result[1]["start"], 7.0)
            self.assertTrue(result[1]["hard_boundary_before"])
            self.assertFalse(result[2].get("hard_boundary_before", False))
            self.assertNotIn("看到了吧", (subtitle_dir / "final_short.srt").read_text(encoding="utf-8").split("00:00:07,000")[0])


if __name__ == "__main__":
    unittest.main()
