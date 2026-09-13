import copy
import tempfile
import unittest
import json
from unittest.mock import patch
from pathlib import Path
from scene_reference_coordinator import validate_plan, bind_scene_references, plan_scene_references, filter_eligible_members


class SceneReferenceTest(unittest.TestCase):
    def test_mixed_scene_excludes_metaphor_but_keeps_real_pair(self):
        self.mapping[2]["visual_design"]["expression"] = "metaphor"
        raw = copy.deepcopy(self.plan)
        raw["scenes"][0]["members"] = [0, 1, 2]
        filtered = filter_eligible_members(raw, self.mapping)
        self.assertEqual(validate_plan(filtered, self.mapping)[0]["members"], [0, 1])
        self.assertEqual(raw["scenes"][0]["members"], [0, 1, 2])

    def test_ineligible_pair_becomes_cached_no_reference_plan(self):
        self.mapping[0]["visual_design"]["fact_status"] = "hypothetical"
        with tempfile.TemporaryDirectory() as directory, patch("scene_reference_coordinator.generate_gemini_text", return_value=json.dumps(self.plan)) as call:
            cache = Path(directory) / "plan.json"
            result = plan_scene_references(self.mapping, [], cache)
            self.assertEqual(result["scenes"], [])
            self.assertTrue(result["exclusions"])
            self.assertEqual(plan_scene_references(self.mapping, [], cache), result)
            self.assertEqual(call.call_count, 1)

    def test_model_labels_are_normalized_and_cached_without_retry(self):
        for label in ("出租屋/餐桌", "scene-1", "", None):
            raw = copy.deepcopy(self.plan)
            raw["scenes"][0]["scene_id"] = label
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory, patch(
                "scene_reference_coordinator.generate_gemini_text", return_value=json.dumps(raw)
            ) as generate:
                cache = Path(directory) / "plan.json"
                result = plan_scene_references(self.mapping, [], cache)
                self.assertEqual(result["scenes"][0]["scene_id"], "location_1")
                self.assertEqual(result["scenes"][0]["members"], [0, 1])
                self.assertEqual(result, plan_scene_references(self.mapping, [], cache))
                self.assertEqual(generate.call_count, 1)

    def test_normalization_does_not_relax_membership_validation(self):
        raw = copy.deepcopy(self.plan)
        raw["scenes"][0].update(scene_id="任意编号", members=[0, 99])
        with tempfile.TemporaryDirectory() as directory, patch(
            "scene_reference_coordinator.generate_gemini_text", return_value=json.dumps(raw)
        ):
            with self.assertRaises(ValueError):
                plan_scene_references(self.mapping, [], Path(directory) / "plan.json")

    def test_same_plan_reuses_cache_without_llm(self):
        with tempfile.TemporaryDirectory() as directory, patch(
            "scene_reference_coordinator.generate_gemini_text", return_value=json.dumps(self.plan)
        ) as generate:
            cache = Path(directory) / "plan.json"
            first = plan_scene_references(self.mapping, [], cache)
            second = plan_scene_references(self.mapping, [], cache)
            self.assertEqual(first, second)
            self.assertEqual(generate.call_count, 1)

    def setUp(self):
        self.mapping = [{"image_prompt": "桌面", "visual_design": {"fact_status": "fact"}},
                        {"image_prompt": "桌子特写", "visual_design": {"fact_status": "fact"}},
                        {"image_prompt": "地铁", "visual_design": {"fact_status": "fact"}}]
        self.plan = {"scenes": [{"scene_id": "location_1", "members": [0, 1], "reference_prompt": "无人餐桌", "reason": "同一现场"}]}

    def test_only_related_shots_receive_scene(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scene.jpg"
            path.write_bytes(b"test")
            before = copy.deepcopy(self.mapping)
            result = bind_scene_references(self.mapping, self.plan, {"location_1": path}, {})
            self.assertEqual(self.mapping, before)
            self.assertIn("scene_reference", result[0])
            self.assertIn("scene_reference", result[1])
            self.assertNotIn("reference_image_paths", result[2])
            self.assertEqual(result[2], self.mapping[2])

    def test_rejects_singleton_overlap_unknown_and_metaphor(self):
        for members in ([0], [0, 0], [0, 9]):
            with self.subTest(members=members), self.assertRaises(ValueError):
                validate_plan({"scenes": [{**self.plan["scenes"][0], "members": members}]}, self.mapping)
        self.mapping[0]["visual_design"]["fact_status"] = "metaphorical"
        with self.assertRaises(ValueError):
            validate_plan(self.plan, self.mapping)

    def test_exact_character_selection_and_numbering(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scene.jpg"
            path.write_bytes(b"test")
            self.mapping[0].update(image_prompt="参考图2人物", reference_image_ids=["图2"])
            result = bind_scene_references(self.mapping, self.plan, {"location_1": path}, {"图1": "unused", "图2": "selected"})
            self.assertEqual(result[0]["reference_image_paths"], ["selected", str(path.resolve())])
            self.assertIn("参考图1人物", result[0]["image_prompt"])
            self.assertEqual(result[0]["scene_reference"]["input_number"], 2)
