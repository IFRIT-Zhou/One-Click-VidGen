import os
import unittest
from pathlib import Path
from unittest.mock import patch

import module4_video_render as visual


class VisualConstraintsTest(unittest.TestCase):
    def test_one_failed_image_reuses_neighbor_without_stopping_batch(self) -> None:
        mapping = [
            {"macro_scene_id": f"poster_{index:03d}", "image_prompt": f"画面 {index}"}
            for index in range(1, 4)
        ]

        def render_one(macro, _pool):
            if macro["macro_scene_id"] == "poster_002":
                raise RuntimeError("模拟单图持续失败")
            return Path(f"{macro['macro_scene_id']}.jpg")

        with (
            patch.object(visual, "_render_poster_with_retry", side_effect=render_one),
            patch.dict(
                os.environ,
                {"RUNNINGHUB_ACTIVE_TASK_CONCURRENCY": "2", "RUNNINGHUB_ALLOW_NEIGHBOR_FALLBACK": "1"},
                clear=False,
            ),
        ):
            results = visual.render_posters_concurrently(mapping, [{}])
        self.assertEqual(len(results), 3)
        self.assertIn(results[1], {results[0], results[2]})

    def test_moderation_failure_is_rewritten_for_single_image_retry(self) -> None:
        self.assertTrue(visual._looks_like_moderation_failure("content safety blocked"))
        with self.assertRaises(visual.RunningHubModerationError):
            visual._handle_runninghub_submit_error(
                {"code": 400, "message": "提示词触发内容审核"},
                400,
            )
        rewritten = visual._rewrite_prompt_after_moderation(
            "走廊中出现腐烂尸体和满地鲜血。",
            1,
        )
        self.assertNotIn("腐烂尸体", rewritten)
        self.assertNotIn("满地鲜血", rewritten)
        self.assertIn("安全重绘", rewritten)
        self.assertIn("遮挡", rewritten)

    def test_science_mode_restores_red_scarf_girl_and_science_agent(self) -> None:
        self.assertNotIn("黑色短发", visual.SCIENCE_VISUAL_STYLE)
        self.assertIn("黑色短发", visual.SCIENCE_GLOBAL_CHARACTER_PROMPT)
        self.assertIn("红色围巾", visual.SCIENCE_GLOBAL_CHARACTER_PROMPT)
        system_prompt = visual.build_visual_prompt_system(
            content_mode=visual.CONTENT_MODE_SCIENCE,
        )
        self.assertIn("科普科技口播视频", system_prompt)
        self.assertIn("知识点", system_prompt)
        self.assertIn("正面主体特写", system_prompt)
        self.assertNotIn("设备使用者第一视角", system_prompt)

    def test_default_style_and_object_closeup_rule_match_story_profile(self) -> None:
        self.assertIn("伊藤润二式惊悚漫画", visual.DEFAULT_VISUAL_STYLE)
        self.assertIn("高反差电影光影", visual.DEFAULT_VISUAL_STYLE)
        self.assertIn("薄雾与局部轮廓光", visual.DEFAULT_VISUAL_STYLE)
        self.assertIn("夸张血腥", visual.DEFAULT_VISUAL_STYLE)
        system_prompt = visual.build_visual_prompt_system()
        self.assertIn("手机、平板、书信或照片", system_prompt)
        self.assertIn("正面主体特写", system_prompt)
        self.assertIn("不使用第一视角或越肩机位", system_prompt)
        self.assertNotIn("红色鸭舌帽", visual.DEFAULT_VISUAL_STYLE)
        self.assertIn("红色鸭舌帽", visual.DEFAULT_GLOBAL_CHARACTER_PROMPT)

    def test_pacing_groups_use_agent_one_recommendation_and_real_timestamps(self) -> None:
        scenes = [
            {"slide_id": f"scene_{index:03d}", "start": (index - 1) * 3, "end": index * 3}
            for index in range(1, 7)
        ]
        plan = {"story_beats": [
            {"slide_ids": ["scene_001", "scene_002"], "visual_pacing": "hold"},
            {"slide_ids": ["scene_003", "scene_004"], "visual_pacing": "fast"},
            {"slide_ids": ["scene_005", "scene_006"], "visual_pacing": "normal"},
        ]}
        groups = visual._visual_groups(scenes, plan)
        self.assertEqual([[item["slide_id"] for item in group] for group in groups], [
            ["scene_001", "scene_002"], ["scene_003", "scene_004"], ["scene_005", "scene_006"],
        ])

    def test_character_name_is_expanded_and_style_meta_is_not_sent_to_image_model(self) -> None:
        scenes = [
            {"slide_id": "scene_001", "start": 0, "end": 5},
            {"slide_id": "scene_002", "start": 5, "end": 10},
        ]
        mapping = [{
            "macro_scene_id": "poster_001",
            "includes_slides": ["scene_001"],
            "image_prompt": "阿凯在萱萱妈妈身旁骑行，萱萱妈妈神情恍惚。",
        }]
        story_plan = {
            "characters": [{
                "name": "萱萱妈妈",
                "role": "主角",
                "appearance": "30岁左右、扎马尾的女性",
                "wardrobe": "前期居家服，后期骑行服或运动装",
                "wardrobe_states": [{
                    "state_id": "ride",
                    "start_slide_id": "scene_001",
                    "end_slide_id": "scene_002",
                    "wardrobe": "磨旧的深灰色骑行服",
                    "headwear": "白色骑行头盔",
                    "carried_items": "旧自行车",
                }],
                "signature_item": "头盔",
            }],
        }
        style = "都市悬疑漫画；主角为35岁中年女性，黑色长发，随时都戴着红色鸭舌帽。"
        with patch.dict(os.environ, {"VISUAL_STYLE_PROMPT": style}, clear=False):
            result = visual._finalize_mapping(mapping, scenes, story_plan)
        prompt = result[0]["image_prompt"]
        self.assertNotIn("萱萱妈妈", prompt)
        self.assertGreaterEqual(prompt.count("35岁中年女性，黑色长发，随时都戴着红色鸭舌帽"), 2)
        self.assertIn("本镜头服装=磨旧的深灰色骑行服", prompt)
        self.assertNotIn("前期居家服，后期骑行服或运动装", prompt)
        self.assertNotIn("本镜头头部状态=白色骑行头盔", prompt)
        self.assertNotIn("同一角色的脸型、发型、年龄、服装和标志性物件", prompt)
        self.assertIn("本镜头角色造型硬约束", prompt)
        self.assertIn("【统一画面风格】", prompt)

    def test_duration_and_character_style_are_enforced(self) -> None:
        scenes = [
            {
                "slide_id": f"scene_{index:03d}",
                "start": (index - 1) * 5.0,
                "end": index * 5.0,
                "text_content": f"第 {index} 句",
                "visual_summary": f"第 {index} 句",
            }
            for index in range(1, 9)
        ]
        style = "黑色短发带红色围巾的可爱少女，科教手绘漫画风。"
        with (
            patch.object(visual, "gemini_configured", return_value=False),
            patch.dict(os.environ, {"VISUAL_STYLE_PROMPT": style}, clear=False),
        ):
            mapping = visual.build_macro_mapping(scenes)

        scenes_by_id = {scene["slide_id"]: scene for scene in scenes}
        covered = []
        for item in mapping:
            included = [scenes_by_id[slide_id] for slide_id in item["includes_slides"]]
            duration = max(scene["end"] for scene in included) - min(scene["start"] for scene in included)
            self.assertLessEqual(duration, 15.0)
            self.assertIn(style, item["image_prompt"])
            self.assertIn("去除燥波燥点", item["image_prompt"])
            self.assertEqual(item["image_prompt"].count(style), 1)
            self.assertEqual(item["image_prompt"].count("去除燥波燥点"), 1)
            covered.extend(item["includes_slides"])
        self.assertEqual(covered, list(scenes_by_id))

    def test_existing_exact_style_and_quality_are_deduplicated(self) -> None:
        style = "黑色短发带红色围巾的可爱少女，科教手绘漫画风。"
        quality = "去除燥波燥点，去除涂抹感，色彩平滑，画面严格执行干净质感。"
        scenes = [{"slide_id": "scene_001", "start": 0, "end": 5}]
        mapping = [{
            "macro_scene_id": "poster_001",
            "includes_slides": ["scene_001"],
            "image_prompt": f"{style}\n少女站在讲台前。\n{quality}",
        }]
        with patch.dict(os.environ, {"VISUAL_STYLE_PROMPT": style}, clear=False):
            result = visual._finalize_mapping(mapping, scenes)
        prompt = result[0]["image_prompt"]
        self.assertEqual(prompt.count(style), 1)
        self.assertEqual(prompt.count("去除燥波燥点"), 1)
        self.assertIn("少女站在讲台前", prompt)

    def test_quality_is_not_duplicated_when_forced_style_already_contains_it(self) -> None:
        quality = "去除燥波燥点，去除涂抹感，色彩平滑，画面严格执行干净质感。"
        style = f"中式阴森漫画，红色鸭舌帽。{quality}"
        scenes = [{"slide_id": "scene_001", "start": 0, "end": 5}]
        mapping = [{
            "macro_scene_id": "poster_001",
            "includes_slides": ["scene_001"],
            "image_prompt": "女人查看手机。",
        }]
        with patch.dict(os.environ, {"VISUAL_STYLE_PROMPT": style}, clear=False):
            result = visual._finalize_mapping(mapping, scenes)
        self.assertEqual(result[0]["image_prompt"].count("去除燥波燥点"), 1)

    def test_explicit_imagery_is_rewritten_before_submission(self) -> None:
        prompt = "走廊里出现腐烂尸体，满地鲜血，画面血肉模糊。"
        guarded = visual._apply_visual_safety_guard(prompt)
        self.assertNotIn("腐烂尸体", guarded)
        self.assertNotIn("满地鲜血", guarded)
        self.assertNotIn("血肉模糊", guarded)
        self.assertIn("遮挡", guarded)
        self.assertIn("远景", guarded)

    def test_agent_two_receives_agent_one_context(self) -> None:
        scenes = [{
            "slide_id": "scene_001",
            "start": 0,
            "end": 5,
            "text_content": "她推开走廊尽头的门。",
            "visual_summary": "女人推门",
        }]
        response = '[{"includes_slides":["scene_001"],"image_prompt":"女人推开旧门"}]'
        with patch.object(visual, "generate_gemini_text", return_value=response) as generate:
            mapping = visual._plan_mapping_batch(
                scenes,
                "系统提示",
                "测试批次",
                {"characters": [{"name": "林晚", "appearance": "黑色短发"}]},
            )
        self.assertIsNotNone(mapping)
        call = generate.call_args.kwargs
        self.assertIn("Agent 1 提供的全文故事上下文", call["system_prompt"])
        self.assertIn("林晚", call["system_prompt"])
        self.assertIn("黑色短发", call["system_prompt"])


if __name__ == "__main__":
    unittest.main()
