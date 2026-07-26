import json
import os
import unittest
from unittest.mock import patch

import module4_video_render as visual
import story_agents


class VisualPacingTest(unittest.TestCase):
    def test_pacing_groups_use_agent_recommendation_and_timestamps(self) -> None:
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
            ["scene_001", "scene_002"],
            ["scene_003", "scene_004"],
            ["scene_005", "scene_006"],
        ])

    def test_short_tail_is_merged_to_respect_selected_minimum_dwell_time(self) -> None:
        scenes = [
            {"slide_id": f"scene_{index:03d}", "start": (index - 1) * 3, "end": index * 3}
            for index in range(1, 6)
        ]
        plan = {"story_beats": [
            {"slide_ids": ["scene_001", "scene_002", "scene_003"], "visual_pacing": "normal"},
            {"slide_ids": ["scene_004", "scene_005"], "visual_pacing": "fast"},
        ]}
        with patch.dict(os.environ, {
            "VISUAL_MIN_DURATION_SECONDS": "6",
            "VISUAL_TARGET_DURATION_SECONDS": "8",
            "VISUAL_MAX_DURATION_SECONDS": "12",
            "VISUAL_MAX_SLIDES_PER_IMAGE": "6",
        }, clear=False):
            groups = visual._visual_groups(scenes, plan)
        durations = [group[-1]["end"] - group[0]["start"] for group in groups]
        self.assertTrue(all(duration >= 6 for duration in durations))

    def test_semantic_unit_keeps_one_event_together_before_hard_limit(self) -> None:
        scenes = [
            {"slide_id": f"scene_{index:03d}", "start": (index - 1) * 2, "end": index * 2}
            for index in range(1, 5)
        ]
        plan = {"semantic_units": [
            {"start_slide_id": "scene_001", "end_slide_id": "scene_003", "visual_pacing": "normal"},
            {"start_slide_id": "scene_004", "end_slide_id": "scene_004", "visual_pacing": "normal"},
        ]}
        with patch.dict(os.environ, {
            "VISUAL_MIN_DURATION_SECONDS": "6",
            "VISUAL_TARGET_DURATION_SECONDS": "8",
            "VISUAL_MAX_DURATION_SECONDS": "12",
            "VISUAL_MAX_SLIDES_PER_IMAGE": "2",
        }, clear=False):
            groups = visual._visual_groups(scenes, plan)
        self.assertEqual(
            [[item["slide_id"] for item in group] for group in groups],
            [["scene_001", "scene_002", "scene_003"], ["scene_004"]],
        )

    def test_hard_boundary_wins_over_minimum_dwell_time_but_soft_can_merge(self) -> None:
        scenes = [
            {"slide_id": "scene_001", "start": 0, "end": 3},
            {"slide_id": "scene_002", "start": 3, "end": 6},
        ]
        hard_plan = {"semantic_units": [
            {"start_slide_id": "scene_001", "end_slide_id": "scene_001", "boundary_after": "hard"},
            {"start_slide_id": "scene_002", "end_slide_id": "scene_002", "boundary_after": "hard"},
        ]}
        soft_plan = {"semantic_units": [
            {"start_slide_id": "scene_001", "end_slide_id": "scene_001", "boundary_after": "soft"},
            {"start_slide_id": "scene_002", "end_slide_id": "scene_002", "boundary_after": "hard"},
        ]}
        with patch.dict(os.environ, {
            "VISUAL_MIN_DURATION_SECONDS": "7",
            "VISUAL_TARGET_DURATION_SECONDS": "8",
            "VISUAL_MAX_DURATION_SECONDS": "12",
            "VISUAL_MAX_SLIDES_PER_IMAGE": "6",
        }, clear=False):
            hard_groups = visual._visual_groups(scenes, hard_plan)
            soft_groups = visual._visual_groups(scenes, soft_plan)
        self.assertEqual([[item["slide_id"] for item in group] for group in hard_groups], [["scene_001"], ["scene_002"]])
        self.assertEqual([[item["slide_id"] for item in group] for group in soft_groups], [["scene_001", "scene_002"]])

    def test_agent_one_receives_global_character_bible(self) -> None:
        scenes = [
            {"slide_id": "scene_001", "text_content": "The door opens."},
            {"slide_id": "scene_002", "text_content": "She runs."},
        ]
        response = json.dumps({
            "story_type": "urban_suspense", "logline": "test", "theme": "test", "narrative_tone": "calm",
            "characters": [], "locations": [], "continuity_rules": [],
            "story_beats": [{
                "slide_ids": ["scene_001", "scene_002"], "purpose": "turn", "emotion": "tense",
                "visual_focus": "door", "visual_pacing": "fast",
            }],
        })
        with (
            patch.object(story_agents, "gemini_configured", return_value=True),
            patch.object(story_agents, "generate_gemini_text", return_value=response) as generate,
            patch.dict(os.environ, {"GLOBAL_CHARACTER_PROMPT": "lead character has a red cap"}, clear=False),
        ):
            plan = story_agents.create_story_plan(scenes)
        payload = json.loads(generate.call_args.kwargs["user_prompt"])
        self.assertEqual(payload["user_global_character_bible"], "lead character has a red cap")
        self.assertEqual(plan["story_beats"][0]["visual_pacing"], "fast")


if __name__ == "__main__":
    unittest.main()
