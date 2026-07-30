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

    def test_explicit_screen_content_becomes_device_only_and_clears_character_reference(self) -> None:
        scenes = [{
            "slide_id": "scene_001", "start": 0, "end": 6,
            "text_content": "她举起手机，屏幕上写着“今晚别回家”。",
        }]
        mapping = [{
            "includes_slides": ["scene_001"],
            "image_prompt": "林晚举着手机贴近脸部，角色形象参考图1，惊恐地看向镜头。",
            "character_ids": ["lin_wan"],
            "reference_image_ids": ["图1"],
        }]
        story_plan = {
            "characters": [{
                "character_id": "lin_wan", "name": "林晚", "role": "主角",
                "appearance": "35岁黑色长发女性",
            }],
            "semantic_units": [{
                "start_slide_id": "scene_001", "end_slide_id": "scene_001",
                "character_ids": [], "device_shot_mode": "screen_insert",
                "device_type": "手机", "screen_content": "今晚别回家",
            }],
        }
        with patch.dict(os.environ, {"USER_REFERENCE_IMAGE_PATHS_JSON": '["lin.png"]'}, clear=False):
            result = visual._finalize_mapping(mapping, scenes, story_plan)[0]
        self.assertEqual(result["device_shot_mode"], "screen_insert")
        self.assertEqual(result["character_ids"], [])
        self.assertEqual(result["reference_image_ids"], [])
        self.assertIn("只展示手机正面屏幕", result["image_prompt"])
        self.assertIn("今晚别回家", result["image_prompt"])
        self.assertNotIn("林晚举着手机贴近脸部", result["image_prompt"])
        self.assertNotIn("本镜头唯一角色卡", result["image_prompt"])

    def test_unspecified_screen_content_keeps_person_but_forbids_readable_ui(self) -> None:
        scenes = [{
            "slide_id": "scene_001", "start": 0, "end": 6,
            "text_content": "林晚坐在床边，低头看了很久手机。",
        }]
        mapping = [{
            "includes_slides": ["scene_001"],
            "image_prompt": "林晚坐在床边低头查看手机，神情疲惫。",
            "character_ids": ["lin_wan"],
            "reference_image_ids": ["图1"],
        }]
        story_plan = {
            "characters": [{
                "character_id": "lin_wan", "name": "林晚", "role": "主角",
                "appearance": "35岁黑色长发女性",
            }],
            "semantic_units": [{
                "start_slide_id": "scene_001", "end_slide_id": "scene_001",
                "character_ids": ["lin_wan"], "device_shot_mode": "device_interaction",
                "device_type": "手机", "screen_content": "不应保留",
            }],
        }
        with patch.dict(os.environ, {"USER_REFERENCE_IMAGE_PATHS_JSON": '["lin.png"]'}, clear=False):
            result = visual._finalize_mapping(mapping, scenes, story_plan)[0]
        self.assertEqual(result["device_shot_mode"], "device_interaction")
        self.assertEqual(result["reference_image_ids"], ["图1"])
        self.assertEqual(result["screen_content"], "")
        self.assertIn("屏幕必须背向镜头、虚化或不可读", result["image_prompt"])
        self.assertIn("林晚", result["image_prompt"])

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
        self.assertIn("萱萱妈妈：35岁中年女性", prompt)
        self.assertEqual(prompt.count("35岁中年女性，黑色长发，随时都戴着红色鸭舌帽"), 1)
        self.assertIn("本镜头服装=磨旧的深灰色骑行服", prompt)
        self.assertNotIn("前期居家服，后期骑行服或运动装", prompt)
        self.assertNotIn("本镜头头部状态=白色骑行头盔", prompt)
        self.assertNotIn("同一角色的脸型、发型、年龄、服装和标志性物件", prompt)
        self.assertIn("本镜头唯一角色卡", prompt)
        self.assertIn("【统一画面风格】", prompt)
        self.assertIn("【视觉媒介锁】", prompt)

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
            self.assertIn(style.rstrip("。"), item["image_prompt"])
            self.assertIn("保留所选画风需要的线稿", item["image_prompt"])
            self.assertEqual(item["image_prompt"].count(style.rstrip("。")), 1)
            self.assertEqual(item["image_prompt"].count("保留所选画风需要的线稿"), 1)
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
        self.assertEqual(prompt.count(style.rstrip("。")), 1)
        self.assertNotIn("去除燥波燥点", prompt)
        self.assertEqual(prompt.count("保留所选画风需要的线稿"), 1)
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
        self.assertNotIn("去除燥波燥点", result[0]["image_prompt"])
        self.assertEqual(result[0]["image_prompt"].count("保留所选画风需要的线稿"), 1)

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

    def test_character_reference_marker_is_explicit_and_safe_by_default(self) -> None:
        scenes = [{"slide_id": "scene_001", "start": 0, "end": 5}]
        without_marker = visual._normalize_mapping(
            [{"includes_slides": ["scene_001"], "image_prompt": "空走廊"}], scenes,
        )
        with_marker = visual._normalize_mapping(
            [{"includes_slides": ["scene_001"], "image_prompt": "男主角：角色形象参考图1", "reference_image_ids": ["图1", "图2", "无效编号"]}], scenes,
        )
        self.assertEqual(without_marker[0]["reference_image_ids"], [])
        self.assertEqual(with_marker[0]["reference_image_ids"], ["图1", "图2"])

    def test_reference_catalog_preserves_uploaded_order(self) -> None:
        with patch.dict(
            os.environ,
            {"USER_REFERENCE_IMAGE_PATHS_JSON": '["male.png", "female.png", "second.png"]'},
            clear=False,
        ):
            self.assertEqual(
                visual._reference_image_catalog(),
                {"图1": "male.png", "图2": "female.png", "图3": "second.png"},
            )
            prompt = visual.build_visual_prompt_system(content_mode=visual.CONTENT_MODE_GENERAL)
        self.assertIn("角色形象参考图N", prompt)
        self.assertIn("图1、图2、图3", prompt)

    def test_stale_reference_state_is_removed_from_saved_expert_prompt(self) -> None:
        prompt = (
            "只输出严格 JSON。\n"
            "- 本次未上传角色形象参考图；reference_image_ids 必须输出 []。\n"
            "保留这一条创作规则。"
        )
        cleaned = visual._strip_dynamic_reference_image_instructions(prompt)
        self.assertNotIn("本次未上传", cleaned)
        self.assertIn("保留这一条创作规则", cleaned)

    def test_named_characters_recover_reference_ids_and_keep_clear_boundaries(self) -> None:
        scenes = [{"slide_id": "scene_001", "start": 0, "end": 5}]
        mapping = [{
            "macro_scene_id": "poster_001",
            "includes_slides": ["scene_001"],
            "image_prompt": "莱恩正向艾德里安严肃地讲述情况",
            "reference_image_ids": [],
        }]
        story_plan = {"characters": [
            {"name": "艾德里安", "role": "王国骑士", "appearance": "年轻，身着银色胸甲"},
            {"name": "莱恩", "role": "精灵弓手", "appearance": "金色长发束在脑后"},
        ]}
        with patch.dict(os.environ, {
            "GLOBAL_CHARACTER_PROMPT": "艾德里安：图1，深蓝披风\n莱恩：图3，白银铠甲",
            "USER_REFERENCE_IMAGE_PATHS_JSON": '["knight.png", "witch.png", "elf.png"]',
            "VISUAL_STYLE_PROMPT": "",
        }, clear=False):
            result = visual._finalize_mapping(mapping, scenes, story_plan)
        prompt = result[0]["image_prompt"]
        self.assertIn("莱恩：金色长发束在脑后；角色形象参考图3", prompt)
        self.assertIn("艾德里安：年轻，身着银色胸甲；角色形象参考图1", prompt)
        self.assertIn("莱恩正向艾德里安严肃地讲述情况", prompt)
        self.assertEqual(result[0]["reference_image_ids"], ["图1", "图3"])

    def test_natural_character_reference_wording_is_supported(self) -> None:
        with patch.dict(os.environ, {
            "GLOBAL_CHARACTER_PROMPT": "林晚形象参考图1\n男主角参考图2\n女主角角色形象参考图3",
            "USER_REFERENCE_IMAGE_PATHS_JSON": '["lin.png", "male.png", "female.png"]',
        }, clear=False):
            self.assertEqual(visual._character_reference_label("林晚"), "图1")
            self.assertEqual(visual._character_reference_label("男主角"), "图2")
            self.assertEqual(visual._character_reference_label("女主角"), "图3")

    def test_agent_one_context_does_not_bind_reference_to_environment_shot(self) -> None:
        scenes = [{"slide_id": "scene_001", "start": 0, "end": 5}]
        mapping = [{
            "macro_scene_id": "poster_001",
            "includes_slides": ["scene_001"],
            "character_ids": [],
            "image_prompt": "阳台上一条洗得发白的旧毛巾特写",
            "reference_image_ids": [],
        }]
        story_plan = {
            "characters": [{
                "character_id": "lin_wan",
                "name": "林晚",
                "role": "主角",
                "appearance": "三十岁，黑色中长发",
            }],
            "semantic_units": [{
                "start_slide_id": "scene_001",
                "end_slide_id": "scene_001",
                "character_ids": ["lin_wan"],
            }],
        }
        with patch.dict(os.environ, {
            "GLOBAL_CHARACTER_PROMPT": "林晚形象参考图1",
            "USER_REFERENCE_IMAGE_PATHS_JSON": '["lin.png"]',
            "VISUAL_STYLE_PROMPT": "",
        }, clear=False):
            result = visual._finalize_mapping(mapping, scenes, story_plan)
        self.assertEqual(result[0]["reference_image_ids"], [])

    def test_named_visible_character_recovers_reference_from_natural_wording(self) -> None:
        scenes = [{"slide_id": "scene_001", "start": 0, "end": 5}]
        mapping = [{
            "macro_scene_id": "poster_001",
            "includes_slides": ["scene_001"],
            "character_ids": [],
            "image_prompt": "林晚站在水槽前安静地洗碗",
            "reference_image_ids": [],
        }]
        story_plan = {"characters": [{
            "character_id": "lin_wan",
            "name": "林晚",
            "role": "主角",
            "appearance": "三十岁，黑色中长发",
        }]}
        with patch.dict(os.environ, {
            "GLOBAL_CHARACTER_PROMPT": "林晚形象参考图1",
            "USER_REFERENCE_IMAGE_PATHS_JSON": '["lin.png"]',
            "VISUAL_STYLE_PROMPT": "",
        }, clear=False):
            result = visual._finalize_mapping(mapping, scenes, story_plan)
        self.assertEqual(result[0]["reference_image_ids"], ["图1"])
        self.assertIn("角色形象参考图1", result[0]["image_prompt"])

    def test_shared_role_characters_get_one_unique_card_and_no_duplicate_appearance(self) -> None:
        scenes = [
            {"slide_id": "scene_001", "start": 0, "end": 5},
            {"slide_id": "scene_002", "start": 5, "end": 10},
        ]
        wife_appearance = "31岁左右，黑色中长发，气质温和但略显疲惫"
        husband_appearance = "33岁左右，短黑发，面容朴实，工作后略显疲惫"
        mapping = [{
            "macro_scene_id": "poster_001",
            "includes_slides": ["scene_001", "scene_002"],
            "character_ids": ["wife", "husband"],
            "image_prompt": (
                f"家庭经营者妻子（{wife_appearance}）站在窗边，"
                f"家庭经营者丈夫（{husband_appearance}）坐在沙发上。"
            ),
        }]
        story_plan = {
            "characters": [
                {"character_id": "wife", "name": "妻子", "role": "家庭经营者", "appearance": wife_appearance, "wardrobe": "米白针织衫"},
                {"character_id": "husband", "name": "丈夫", "role": "家庭经营者", "appearance": husband_appearance, "wardrobe": "深蓝衬衫"},
            ],
            "semantic_units": [{
                "start_slide_id": "scene_001", "end_slide_id": "scene_002",
                "character_ids": ["wife", "husband"],
            }],
        }
        with patch.dict(os.environ, {"VISUAL_STYLE_PROMPT": "温暖治愈的都市情感口播插画风"}, clear=False):
            result = visual._finalize_mapping(mapping, scenes, story_plan)
        prompt = result[0]["image_prompt"]
        self.assertIn(f"妻子：{wife_appearance}", prompt)
        self.assertIn(f"丈夫：{husband_appearance}", prompt)
        self.assertEqual(prompt.count(wife_appearance), 1)
        self.assertEqual(prompt.count(husband_appearance), 1)
        self.assertNotIn("家庭经营者：", prompt)
        self.assertIn("妻子站在窗边", prompt)
        self.assertIn("丈夫坐在沙发上", prompt)
        self.assertIn("【视觉媒介锁】", prompt)

    def test_character_expansion_is_idempotent_and_deduplicates_role_and_reference(self) -> None:
        story_plan = {"characters": [{
            "name": "艾德里安",
            "role": "王国骑士",
            "appearance": "年轻，身着银色胸甲",
        }]}
        with patch.dict(os.environ, {
            "GLOBAL_CHARACTER_PROMPT": "艾德里安：图1，深蓝色旧披风",
            "USER_REFERENCE_IMAGE_PATHS_JSON": '["knight.png"]',
        }, clear=False):
            once, _ = visual._expand_character_names(
                "年轻的王国骑士艾德里安（角色形象参考图1，深蓝色旧披风）站在城门前",
                story_plan,
            )
            twice, _ = visual._expand_character_names(once, story_plan)
        self.assertEqual(once, twice)
        self.assertNotIn("王国骑士王国骑士", once)
        self.assertEqual(once.count("角色形象参考图1"), 1)
        self.assertIn("王国骑士艾德里安（年轻，身着银色胸甲，角色形象参考图1，深蓝色旧披风）", once)

    def test_visual_editor_redraws_share_one_round_robin_account_pool(self) -> None:
        configs = [
            {"api_key": "redraw-key-1", "endpoint": "/generate", "ratio": "2:1", "resolution": "1k", "account_label": "账号 1"},
            {"api_key": "redraw-key-2", "endpoint": "/generate", "ratio": "2:1", "resolution": "1k", "account_label": "账号 2"},
        ]
        namespace = f"test-redraw-{id(self)}"
        first_call_pool = visual.shared_runninghub_account_pool(configs, namespace=namespace)
        second_call_pool = visual.shared_runninghub_account_pool(list(configs), namespace=namespace)
        self.assertIs(first_call_pool, second_call_pool)
        self.assertEqual(first_call_pool.acquire()["account_label"], "账号 1")
        self.assertEqual(second_call_pool.acquire()["account_label"], "账号 2")

    def test_power_exhausted_account_is_skipped_by_new_pool(self) -> None:
        configs = [
            {"api_key": "quota-key-1", "endpoint": "/generate", "ratio": "2:1", "resolution": "1k", "account_label": "账号 1"},
            {"api_key": "quota-key-2", "endpoint": "/generate", "ratio": "2:1", "resolution": "1k", "account_label": "账号 2"},
        ]
        try:
            first = visual.RunningHubAccountPool(configs)
            first.mark_power_exhausted(configs[0])
            next_batch = visual.RunningHubAccountPool(configs)
            self.assertEqual(next_batch.acquire()["account_label"], "账号 2")
            self.assertTrue(visual._looks_like_power_insufficient(None, "账户余额不足"))
        finally:
            with visual._ACCOUNT_STATE_LOCK:
                visual._POWER_EXHAUSTED_ACCOUNT_KEYS.difference_update({"quota-key-1", "quota-key-2"})

    def test_multi_moment_prompt_gets_single_scene_guard_but_comparison_is_allowed(self) -> None:
        risky = visual._single_scene_guard("村民从窗后窥视，随后男人走出浓雾并忘记名字")
        comparison = visual._single_scene_guard("同一器材使用前后效果对比")
        self.assertIn("单镜头构图硬约束", risky)
        self.assertIn("不使用多格漫画", risky)
        self.assertEqual(comparison, "同一器材使用前后效果对比")


if __name__ == "__main__":
    unittest.main()
