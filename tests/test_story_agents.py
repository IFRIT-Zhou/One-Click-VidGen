import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import story_agents


def sample_scenes() -> list[dict]:
    return [
        {
            "slide_id": f"scene_{index:03d}",
            "start": (index - 1) * 5,
            "end": index * 5,
            "text_content": f"第 {index} 段故事",
            "visual_summary": f"人物走进第 {index} 个场景",
        }
        for index in range(1, 9)
    ]


class StoryAgentsTest(unittest.TestCase):
    def test_old_fallback_plan_is_replaced_when_gemini_is_available(self) -> None:
        scenes = sample_scenes()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "story_plan.json"
            stale = story_agents._fallback_story_plan(scenes)
            stale["source_fingerprint"] = story_agents.story_fingerprint(scenes)
            stale["agent_version"] = 1
            path.write_text(json.dumps(stale, ensure_ascii=False), encoding="utf-8")
            replacement = dict(stale)
            replacement["agent_version"] = 2
            replacement["generation_source"] = "gemini"
            with (
                patch.object(story_agents, "gemini_configured", return_value=True),
                patch.object(story_agents, "create_story_plan", return_value=replacement) as create,
            ):
                result = story_agents.load_or_create_story_plan(scenes, resume=True, path=path)
            create.assert_called_once_with(scenes, story_agents.CONTENT_MODE_STORY)
            self.assertEqual(result["generation_source"], "gemini")

    def test_truncated_agent_response_retries_with_larger_compact_budget(self) -> None:
        scenes = sample_scenes()
        response = json.dumps({
            "story_type": "urban_suspense",
            "logline": "一名女人走进异常建筑。",
            "theme": "未知与选择",
            "narrative_tone": "克制悬疑",
            "characters": [],
            "locations": [],
            "story_beats": [{
                "beat_id": "beat_01",
                "slide_ids": [scene["slide_id"] for scene in scenes],
                "purpose": "建立悬念",
                "emotion": "不安",
                "visual_focus": "走廊",
            }],
            "continuity_rules": ["场景连续"],
        }, ensure_ascii=False)
        with (
            patch.object(story_agents, "gemini_configured", return_value=True),
            patch.object(
                story_agents,
                "generate_gemini_text",
                side_effect=[story_agents.GeminiOutputTruncated("length"), response],
            ) as generate,
        ):
            plan = story_agents.create_story_plan(scenes)

        self.assertEqual(generate.call_count, 2)
        self.assertEqual(generate.call_args_list[0].kwargs["max_output_tokens"], 8192)
        self.assertEqual(generate.call_args_list[1].kwargs["max_output_tokens"], 12288)
        self.assertIn("紧凑 JSON", generate.call_args_list[1].kwargs["system_prompt"])
        self.assertEqual(plan["story_type"], "urban_suspense")

    def test_local_plan_is_file_backed_and_reused(self) -> None:
        scenes = sample_scenes()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "story_plan.json"
            with patch.object(story_agents, "gemini_configured", return_value=False):
                first = story_agents.load_or_create_story_plan(scenes, path=path)
            self.assertTrue(path.is_file())
            self.assertEqual(first["source_fingerprint"], story_agents.story_fingerprint(scenes))
            self.assertTrue(first["story_beats"])

            with (
                patch.object(story_agents, "gemini_configured", return_value=False),
                patch.object(story_agents, "create_story_plan") as create,
            ):
                second = story_agents.load_or_create_story_plan(scenes, resume=True, path=path)
            create.assert_not_called()
            self.assertEqual(first, second)

    def test_changed_story_does_not_reuse_stale_context(self) -> None:
        scenes = sample_scenes()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "story_plan.json"
            path.write_text(
                json.dumps({"source_fingerprint": "stale", "story_beats": []}),
                encoding="utf-8",
            )
            replacement = story_agents._fallback_story_plan(scenes)
            replacement["source_fingerprint"] = story_agents.story_fingerprint(scenes)
            replacement["agent_version"] = 2
            replacement["generation_source"] = "gemini"
            with patch.object(story_agents, "create_story_plan", return_value=replacement) as create:
                result = story_agents.load_or_create_story_plan(scenes, resume=True, path=path)
            create.assert_called_once_with(scenes, story_agents.CONTENT_MODE_STORY)
            self.assertEqual(result["source_fingerprint"], replacement["source_fingerprint"])

    def test_science_mode_uses_its_own_context_and_fingerprint(self) -> None:
        scenes = sample_scenes()
        story_hash = story_agents.story_fingerprint(scenes, story_agents.CONTENT_MODE_STORY)
        science_hash = story_agents.story_fingerprint(scenes, story_agents.CONTENT_MODE_SCIENCE)
        self.assertNotEqual(story_hash, science_hash)

        plan = story_agents._fallback_story_plan(scenes, story_agents.CONTENT_MODE_SCIENCE)
        self.assertEqual(plan["content_mode"], story_agents.CONTENT_MODE_SCIENCE)
        self.assertEqual(plan["story_type"], "science_explainer")
        self.assertIn("红色围巾", plan["characters"][0]["appearance"])
        self.assertIn("知识", story_agents.SCIENCE_AGENT_SYSTEM_PROMPT)

    def test_segment_plan_keeps_global_character_identity_and_local_beats(self) -> None:
        scenes = sample_scenes()[:3]
        global_plan = story_agents._fallback_story_plan(sample_scenes())
        global_plan["characters"] = [{
            "name": "林岚",
            "role": "主角",
            "appearance": "35岁左右，黑色长发",
            "wardrobe": "深色风衣",
            "signature_item": "红色鸭舌帽",
            "relationships": "独自调查异常事件",
        }]
        global_plan["source_fingerprint"] = story_agents.story_fingerprint(sample_scenes())
        local_plan = story_agents._fallback_story_plan(scenes)
        local_plan["characters"] = [
            {
                "name": "林岚",
                "appearance": "错误的短发少女设定",
                "wardrobe": "白色连衣裙",
            },
            {"name": "保安", "role": "局部人物", "appearance": "中年男性"},
        ]
        local_plan["story_beats"][0]["purpose"] = "细化当前分段的门口冲突"

        merged = story_agents.merge_global_and_segment_plan(global_plan, local_plan, scenes)

        protagonist = next(item for item in merged["characters"] if item["name"] == "林岚")
        self.assertEqual(protagonist["appearance"], "35岁左右，黑色长发")
        self.assertEqual(protagonist["signature_item"], "红色鸭舌帽")
        self.assertTrue(any(item["name"] == "保安" for item in merged["characters"]))
        self.assertEqual(merged["story_beats"][0]["purpose"], "细化当前分段的门口冲突")
        self.assertEqual(merged["planning_scope"], "hierarchical_segment")

    def test_segment_agent_receives_global_bible_and_current_segment(self) -> None:
        scenes = sample_scenes()[:2]
        global_plan = story_agents._fallback_story_plan(sample_scenes())
        global_plan["characters"] = [{"name": "林岚", "appearance": "黑色长发，红色鸭舌帽"}]
        global_plan["source_fingerprint"] = story_agents.story_fingerprint(sample_scenes())
        response = json.dumps({
            "story_type": "urban_suspense",
            "logline": "林岚进入门厅。",
            "theme": "未知",
            "narrative_tone": "克制",
            "characters": [],
            "locations": [],
            "story_beats": [{
                "slide_ids": [scene["slide_id"] for scene in scenes],
                "purpose": "进入现场",
                "emotion": "警惕",
                "visual_focus": "昏暗门厅",
            }],
            "continuity_rules": [],
        }, ensure_ascii=False)
        with (
            patch.object(story_agents, "gemini_configured", return_value=True),
            patch.object(story_agents, "generate_gemini_text", return_value=response) as generate,
        ):
            plan = story_agents.create_segment_story_plan(scenes, global_plan)

        payload = json.loads(generate.call_args.kwargs["user_prompt"])
        self.assertEqual(payload["global_story_bible"]["characters"][0]["name"], "林岚")
        self.assertEqual(len(payload["current_segment"]), 2)
        self.assertEqual(plan["generation_source"], "gemini")
        self.assertEqual(plan["planning_scope"], "hierarchical_segment")


if __name__ == "__main__":
    unittest.main()
