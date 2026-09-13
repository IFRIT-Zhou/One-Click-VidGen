import copy
import json
import unittest
from unittest.mock import patch
import director_prompt_editor as editor


class PromptEditorTest(unittest.TestCase):
    def test_confirmed_empty_scene_clears_character_inputs_only_for_that_frame(self):
        self.mapping[0].update(character_ids=["zhou_yu", "xu_ning"], reference_image_paths=["person.jpg"])
        self.mapping.append({"id": "poster_002", "image_prompt": "有人", "character_ids": ["person"],
                             "reference_image_ids": ["ref2"], "visual_design": {"message": "动作"}})
        empty = {"index": 0, "image_prompt": "【人物与画风】手绘【画面内容】餐桌饭菜账单【必要限制】清晰", "human_presence": "none", "conflicts": []}
        person = {"index": 1, "image_prompt": "人物正在行动", "human_presence": "present", "conflicts": []}
        confirmed = {**empty, "empty_scene_confirmed": True, "evidence": "设计主体仅为餐桌静物，无人物动作，名单为冗余"}
        with patch.object(editor, "generate_gemini_text", side_effect=[json.dumps({"items": [empty, person]}), json.dumps({"items": [confirmed]})]):
            result = editor.finalize_prompts(self.mapping, [], {})
        self.assertEqual(result[0]["character_ids"], [])
        self.assertEqual(result[0]["reference_image_ids"], [])
        self.assertNotIn("reference_image_paths", result[0])
        self.assertIn("无人物出镜", result[0]["image_prompt"])
        self.assertEqual(result[1]["character_ids"], ["person"])
        self.assertEqual(result[1]["reference_image_ids"], ["ref2"])
        self.assertEqual(self.mapping[0]["character_ids"], ["zhou_yu", "xu_ning"])

    def test_presence_conflict_falls_back_without_losing_references(self):
        self.mapping[0]["character_ids"] = ["person"]
        row = {"index": 0, "image_prompt": "空镜", "human_presence": "none", "conflicts": []}
        with patch.object(editor, "generate_gemini_text", side_effect=[json.dumps({"items": [row]}), RuntimeError("timeout")]) as call:
            result = editor.finalize_prompts(self.mapping, [], {})
        self.assertEqual(call.call_count, 2)
        self.assertEqual(result[0]["reference_image_ids"], ["ref1"])
        self.assertEqual(result[0]["character_ids"], ["person"])
        self.assertIn("原文", result[0]["image_prompt"])
        self.assertNotIn("无人物出镜", result[0]["image_prompt"])
        self.assertEqual(result[0]["prompt_editor"]["status"], "original_prompt_fallback")

    def test_presence_conflict_can_be_corrected_once(self):
        self.mapping[0]["character_ids"] = ["person"]
        row = {"index": 0, "image_prompt": "空镜", "human_presence": "none", "conflicts": []}
        fixed = {**row, "human_presence": "present", "image_prompt": "人物在桌边"}
        with patch.object(editor, "generate_gemini_text", side_effect=[json.dumps({"items": [row]}), json.dumps({"items": [fixed]})]) as call:
            result = editor.finalize_prompts(self.mapping, [], {})
        self.assertEqual(call.call_count, 2)
        self.assertEqual(result[0]["prompt_editor"]["status"], "corrected")

    def test_order_and_explicit_empty_scene(self):
        prompt = "【画面内容】餐桌与饭菜\n【人物与画风】手绘\n【必要限制】干净"
        result = editor._order_sections(prompt, no_people=True)
        self.assertTrue(result.startswith("【人物与画风】"))
        self.assertIn("无人物出镜", result)
        self.assertLess(result.index("人物与画风"), result.index("画面内容"))

    def test_no_automatic_exclusion_from_empty_character_ids(self):
        for presence in ("present", "partial", "unspecified", "none"):
            with self.subTest(presence=presence), patch.object(editor, "generate_gemini_text", return_value=json.dumps({"items": [
                {"index": 0, "image_prompt": "【人物与画风】手绘【画面内容】原方案【必要限制】清晰", "human_presence": presence, "conflicts": []}]})):
                result = editor.finalize_prompts(self.mapping, [], {})
                self.assertEqual("无人物出镜" in result[0]["image_prompt"], presence == "none")

    def setUp(self):
        self.mapping = [{"image_prompt": "原文", "visual_design": {"message": "表达共同承担"},
                         "includes_slides": ["scene_001"], "reference_image_ids": ["ref1"], "id": "poster_001"}]

    def test_keeps_identity_and_purpose_without_mutating_input(self):
        before = copy.deepcopy(self.mapping)
        with patch.object(editor, "generate_gemini_text", return_value=json.dumps({"items": [
                {"index": 0, "image_prompt": "整理内容", "conflicts": []}]})) as call:
            result = editor.finalize_prompts(self.mapping, [], {})
        self.assertEqual(self.mapping, before)
        self.assertEqual(result[0]["includes_slides"], ["scene_001"])
        self.assertEqual(result[0]["reference_image_ids"], ["ref1"])
        self.assertEqual(result[0]["id"], "poster_001")
        self.assertTrue(result[0]["image_prompt"].startswith("【本图旨在】表达共同承担"))
        self.assertEqual(call.call_count, 1)

    def test_rejects_missing_reordered_or_conflicting_results(self):
        for rows in ([], [{"index": 2}], [{"index": 0, "image_prompt": "x", "conflicts": ["矛盾"]}],
                     [{"index": 0, "image_prompt": "", "conflicts": []}]):
            with self.subTest(rows=rows), patch.object(editor, "generate_gemini_text", return_value=json.dumps({"items": rows})):
                with self.assertRaises(ValueError):
                    editor.finalize_prompts(self.mapping, [], {})
