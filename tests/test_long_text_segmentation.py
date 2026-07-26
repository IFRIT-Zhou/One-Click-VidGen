import unittest

from backend.app.pipeline import (
    scene_text_length,
    split_scenes_by_agent1_boundaries,
    split_scenes_by_text_length,
)


def make_scenes(lengths: list[int]) -> list[dict]:
    return [
        {
            "id": f"segment_{index:03d}",
            "slide_id": f"scene_{index:03d}",
            "start": float(index - 1),
            "end": float(index),
            "text_content": "字" * length,
        }
        for index, length in enumerate(lengths, 1)
    ]


class LongTextSegmentationTest(unittest.TestCase):
    def assert_complete(self, source: list[dict], groups: list[list[dict]], threshold: int) -> None:
        flattened = [scene["slide_id"] for group in groups for scene in group]
        self.assertEqual(flattened, [scene["slide_id"] for scene in source])
        self.assertTrue(all(scene_text_length(group) <= threshold for group in groups))

    def test_plain_fallback_balances_a_tiny_tail(self) -> None:
        scenes = make_scenes([900] * 7)
        groups = split_scenes_by_text_length(scenes, 3000)
        self.assert_complete(scenes, groups, 3000)
        self.assertEqual(len(groups), 3)
        self.assertGreaterEqual(min(scene_text_length(group) for group in groups), 1800)

    def test_agent1_semantic_units_are_never_split_when_they_fit(self) -> None:
        scenes = make_scenes([600] * 6)
        plan = {
            "semantic_units": [
                {
                    "start_slide_id": f"scene_{start:03d}",
                    "end_slide_id": f"scene_{start + 1:03d}",
                    "boundary_after": "hard" if start == 3 else "soft",
                }
                for start in (1, 3, 5)
            ]
        }
        groups = split_scenes_by_agent1_boundaries(scenes, 3000, plan)
        self.assertIsNotNone(groups)
        assert groups is not None
        self.assert_complete(scenes, groups, 3000)
        group_end_ids = {group[-1]["slide_id"] for group in groups[:-1]}
        self.assertTrue(group_end_ids.issubset({"scene_002", "scene_004"}))

    def test_oversized_semantic_unit_uses_subtitle_boundaries_as_safety_cut(self) -> None:
        scenes = make_scenes([1000] * 5)
        plan = {
            "semantic_units": [{
                "start_slide_id": "scene_001",
                "end_slide_id": "scene_005",
                "boundary_after": "hard",
            }]
        }
        groups = split_scenes_by_agent1_boundaries(scenes, 3000, plan)
        self.assertIsNotNone(groups)
        assert groups is not None
        self.assert_complete(scenes, groups, 3000)
        self.assertEqual(len(groups), 2)

    def test_invalid_agent1_coverage_still_returns_none_for_safe_fallback(self) -> None:
        scenes = make_scenes([500] * 4)
        plan = {
            "semantic_units": [{
                "start_slide_id": "scene_002",
                "end_slide_id": "scene_004",
                "boundary_after": "hard",
            }]
        }
        self.assertIsNone(split_scenes_by_agent1_boundaries(scenes, 3000, plan))


if __name__ == "__main__":
    unittest.main()
