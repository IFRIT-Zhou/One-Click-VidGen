import os
import unittest
from unittest.mock import patch

import story_agents
import module4_video_render as render


class DirectorAcceptanceTest(unittest.TestCase):
    def test_missing_intent_is_rejected(self):
        for units in ([], [{"unit_id": "u1"}], [{"visual_intent": {"message": "x"}}]):
            with self.assertRaises(ValueError):
                story_agents._validate_visual_intents(units)

    def test_complete_intent_is_accepted(self):
        story_agents._validate_visual_intents([{"visual_intent": {
            key: "有效内容" for key in ("message", "viewer_takeaway", "source_basis", "narrative_role", "fact_status", "progression")}}])

    def test_paper_guard_only_changes_enhanced(self):
        with patch.dict(os.environ, {"DIRECTOR_STRATEGY": "enhanced_beta"}):
            self.assertEqual(render._apply_device_shot_guard("手持账单", "device_interaction", "纸质文件", ""), "手持账单")
            self.assertIn("屏幕", render._apply_device_shot_guard("看手机", "device_interaction", "手机", ""))
        with patch.dict(os.environ, {"DIRECTOR_STRATEGY": "stable"}):
            self.assertIn("屏幕", render._apply_device_shot_guard("手持账单", "device_interaction", "纸质文件", ""))
