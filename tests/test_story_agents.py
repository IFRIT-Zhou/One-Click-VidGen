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
            replacement["agent_version"] = story_agents.STORY_AGENT_VERSION
            replacement["character_continuity_version"] = story_agents.CHARACTER_CONTINUITY_VERSION
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
            replacement["agent_version"] = story_agents.STORY_AGENT_VERSION
            replacement["character_continuity_version"] = story_agents.CHARACTER_CONTINUITY_VERSION
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

    def test_agent0_reads_plain_full_text_without_subtitle_timing(self) -> None:
        response = json.dumps({
            "story_type": "other", "logline": "test", "theme": "test", "narrative_tone": "calm",
            "characters": [], "locations": [], "clues_and_payoffs": [],
            "continuity_rules": [], "visual_safety": [],
        })
        with (
            patch.object(story_agents, "gemini_configured", return_value=True),
            patch.object(story_agents, "generate_gemini_text", return_value=response) as generate,
        ):
            context = story_agents.create_story_context("first paragraph\nsecond paragraph")
        payload = json.loads(generate.call_args.kwargs["user_prompt"])
        self.assertEqual(payload["complete_text"], "first paragraph\nsecond paragraph")
        self.assertNotIn("subtitle_timeline", payload)
        self.assertEqual(context["generation_source"], "gemini")

    def test_agent0_repairs_duplicate_person_labels_into_unique_registry(self) -> None:
        ambiguous = json.dumps({
            "story_type": "family", "logline": "夫妻争执", "theme": "沟通", "narrative_tone": "calm",
            "characters": [
                {"character_id": "wife", "name": "家庭经营者", "aliases": [], "group_aliases": [], "role": "妻子", "appearance": "黑色中长发"},
                {"character_id": "husband", "name": "家庭经营者", "aliases": [], "group_aliases": [], "role": "丈夫", "appearance": "短黑发"},
            ],
            "locations": [], "clues_and_payoffs": [], "continuity_rules": [], "visual_safety": [],
        }, ensure_ascii=False)
        repaired = json.dumps({
            "story_type": "family", "logline": "夫妻争执", "theme": "沟通", "narrative_tone": "calm",
            "characters": [
                {"character_id": "wife", "name": "妻子", "aliases": [], "group_aliases": ["家庭经营者"], "role": "家庭经营者", "appearance": "黑色中长发"},
                {"character_id": "husband", "name": "丈夫", "aliases": [], "group_aliases": ["家庭经营者"], "role": "家庭经营者", "appearance": "短黑发"},
            ],
            "locations": [], "clues_and_payoffs": [], "continuity_rules": [], "visual_safety": [],
        }, ensure_ascii=False)
        with (
            patch.object(story_agents, "gemini_configured", return_value=True),
            patch.object(story_agents, "generate_gemini_text", side_effect=[ambiguous, repaired]) as generate,
        ):
            context = story_agents.create_story_context("妻子和丈夫都在经营这个家庭。")
        self.assertEqual(generate.call_count, 2)
        self.assertEqual([item["name"] for item in context["characters"]], ["妻子", "丈夫"])
        self.assertEqual([item["character_id"] for item in context["characters"]], ["wife", "husband"])
        self.assertIn("系统固定角色身份结构", generate.call_args_list[0].kwargs["system_prompt"])

    def test_agent1_uses_timestamps_and_only_returns_timeline_plan(self) -> None:
        scenes = sample_scenes()[:3]
        context = story_agents._fallback_story_context("full text", story_agents.CONTENT_MODE_STORY)
        response = json.dumps({
            "story_beats": [{
                "slide_ids": [scene["slide_id"] for scene in scenes], "purpose": "advance",
                "emotion": "calm", "visual_focus": "scene", "visual_pacing": "normal",
            }],
            "semantic_units": [
                {"unit_id": "u1", "start_slide_id": "scene_001", "end_slide_id": "scene_002", "purpose": "event one", "visual_focus": "shot one", "visual_pacing": "normal"},
                {"unit_id": "u2", "start_slide_id": "scene_003", "end_slide_id": "scene_003", "purpose": "transition", "visual_focus": "shot two", "visual_pacing": "hold"},
            ],
        })
        with (
            patch.object(story_agents, "gemini_configured", return_value=True),
            patch.object(story_agents, "generate_gemini_text", return_value=response) as generate,
        ):
            plan = story_agents.create_story_plan(scenes, story_context=context)
        payload = json.loads(generate.call_args.kwargs["user_prompt"])
        self.assertEqual(payload["subtitle_timeline"][0]["start"], 0.0)
        self.assertEqual(payload["subtitle_timeline"][0]["end"], 5.0)
        self.assertEqual(plan["semantic_units"][1]["start_slide_id"], "scene_003")

    def test_agent1_device_modes_are_normalized_and_screen_insert_hides_people(self) -> None:
        scenes = sample_scenes()[:2]
        raw_units = [
            {
                "unit_id": "u1",
                "start_slide_id": "scene_001",
                "end_slide_id": "scene_001",
                "purpose": "展示匿名短信",
                "visual_focus": "手机屏幕",
                "visual_pacing": "hold",
                "boundary_after": "hard",
                "character_ids": ["wife"],
                "device_shot_mode": "screen_insert",
                "device_type": "手机",
                "screen_content": "今晚别回家",
            },
            {
                "unit_id": "u2",
                "start_slide_id": "scene_002",
                "end_slide_id": "scene_002",
                "purpose": "人物查看手机",
                "visual_focus": "妻子低头查看手机",
                "visual_pacing": "normal",
                "boundary_after": "hard",
                "character_ids": ["wife"],
                "device_shot_mode": "device_interaction",
                "device_type": "手机",
                "screen_content": "模型不应保留的虚构文字",
            },
        ]
        normalized = story_agents._normalize_semantic_units(raw_units, scenes)
        self.assertEqual(normalized[0]["device_shot_mode"], "screen_insert")
        self.assertEqual(normalized[0]["screen_content"], "今晚别回家")
        self.assertEqual(normalized[1]["device_shot_mode"], "device_interaction")
        self.assertEqual(normalized[1]["screen_content"], "")

    def test_agent0_exposes_source_backed_device_information_contract(self) -> None:
        self.assertIn("key_information_objects", story_agents.AGENT0_SYSTEM_PROMPT)
        self.assertIn("不得猜测或补写", story_agents.AGENT0_SYSTEM_PROMPT)
        self.assertIn("screen_insert", story_agents.DEVICE_SHOT_CONTRACT)
        self.assertIn("device_interaction", story_agents.DEVICE_SHOT_CONTRACT)

    def test_agent0_device_information_survives_agent1_plan_normalization(self) -> None:
        scenes = sample_scenes()[:1]
        raw = story_agents._fallback_story_plan(scenes)
        raw["key_information_objects"] = [{
            "object_id": "anonymous_message",
            "device_type": "手机",
            "content": "今晚别回家",
            "first_context": "匿名短信",
            "later_references": ["那条消息"],
        }]
        plan = story_agents._normalize_story_plan(raw, scenes)
        self.assertEqual(plan["key_information_objects"][0]["content"], "今晚别回家")
        self.assertEqual(plan["key_information_objects"][0]["later_references"], ["那条消息"])

    def test_phone_dialogue_is_not_mistaken_for_screen_content_by_local_fallback(self) -> None:
        scenes = [{"text_content": "她拿着手机说：“我已经到家了。”"}]
        mode, device_type, content = story_agents._fallback_device_shot(scenes)
        self.assertEqual(mode, "device_interaction")
        self.assertEqual(device_type, "手机")
        self.assertEqual(content, "")

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

    def test_character_wardrobe_states_are_normalized_to_valid_slide_ranges(self) -> None:
        scenes = sample_scenes()[:4]
        raw = story_agents._fallback_story_plan(scenes)
        raw["characters"] = [{
            "name": "林岚",
            "role": "主角",
            "appearance": "35岁，黑色长发",
            "wardrobe": "存在多阶段换装",
            "wardrobe_states": [
                {
                    "state_id": "home",
                    "start_slide_id": "scene_001",
                    "end_slide_id": "scene_002",
                    "wardrobe": "起球的灰色旧居家服",
                    "headwear": "红色鸭舌帽",
                    "carried_items": "无",
                },
                {
                    "state_id": "invalid",
                    "start_slide_id": "missing",
                    "end_slide_id": "scene_004",
                    "wardrobe": "不应保留",
                },
            ],
        }]
        plan = story_agents._normalize_story_plan(raw, scenes)
        self.assertIsNotNone(plan)
        states = plan["characters"][0]["wardrobe_states"]
        self.assertEqual(len(states), 1)
        self.assertEqual(states[0]["wardrobe"], "起球的灰色旧居家服")
        self.assertEqual(plan["character_continuity_version"], story_agents.CHARACTER_CONTINUITY_VERSION)

    def test_agent1_character_ids_survive_plan_normalization(self) -> None:
        scenes = sample_scenes()[:2]
        raw = story_agents._fallback_story_plan(scenes)
        raw["characters"] = [
            {"character_id": "wife", "name": "妻子", "appearance": "黑色中长发"},
            {"character_id": "husband", "name": "丈夫", "appearance": "短黑发"},
        ]
        raw["story_beats"][0]["character_ids"] = ["wife", "unknown"]
        raw["semantic_units"] = [{
            "unit_id": "unit_01", "start_slide_id": "scene_001", "end_slide_id": "scene_002",
            "purpose": "夫妻交谈", "visual_focus": "客厅", "visual_pacing": "hold",
            "boundary_after": "hard", "character_ids": ["wife", "husband", "unknown"],
        }]
        plan = story_agents._normalize_story_plan(raw, scenes)
        self.assertEqual(plan["story_beats"][0]["character_ids"], ["wife"])
        self.assertEqual(plan["semantic_units"][0]["character_ids"], ["wife", "husband"])


if __name__ == "__main__":
    unittest.main()
