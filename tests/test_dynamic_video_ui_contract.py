import unittest
from pathlib import Path


class DynamicVideoUiContractTests(unittest.TestCase):
    def test_dynamic_audio_review_renders_guided_next_step(self):
        source = (Path(__file__).resolve().parents[1] / "frontend" / "src" / "Studio.vue").read_text(encoding="utf-8")
        self.assertIn("['video','dynamic'].includes(studioKind) && isGuidedWorkflowJob(studioJob)", source)
        self.assertIn("guidedStage === 'audio_review' && studioJob?.request?.dynamic_video", source)
        self.assertIn("确认配音与字幕，进入动态分镜 →", source)

    def test_single_shot_reedit_has_contextual_return_action(self):
        source = (Path(__file__).resolve().parents[1] / "frontend" / "src" / "components" / "VideoStudio.vue").read_text(encoding="utf-8")
        self.assertIn("project.reedit_shot_id===shot.id", source)
        self.assertIn("完成本镜返修，进入动态重生成", source)
        self.assertIn("点击这里不会自动调用视频 API 或扣费", source)
        self.assertIn("是否仍将当前动态片段标记为待重新生成", source)
        self.assertIn("仍需再次点击生成并确认费用", source)

    def test_dynamic_video_supports_persisted_one_click_progression(self):
        root = Path(__file__).resolve().parents[1]
        studio = (root / "frontend" / "src" / "Studio.vue").read_text(encoding="utf-8")
        workflow = (root / "frontend" / "src" / "components" / "VideoStudio.vue").read_text(encoding="utf-8")
        model = (root / "backend" / "app" / "main.py").read_text(encoding="utf-8")
        self.assertIn('v-model="form.dynamic_auto_advance"', studio)
        self.assertIn("dynamic_auto_advance: bool = False", model)
        self.assertIn("async function advanceAutoPilot()", workflow)
        self.assertIn("await generateVideos(dynamicShots.value,false,true)", workflow)
        self.assertIn("await exportVideo(true)", workflow)

    def test_dynamic_parameter_review_wires_reset_action(self):
        source = (Path(__file__).resolve().parents[1] / "frontend" / "src" / "components" / "VideoStudio.vue").read_text(encoding="utf-8")
        self.assertIn("function resetFromSnapshot()", source)
        self.assertIn('@reset="resetFromSnapshot"', source)


if __name__ == "__main__":
    unittest.main()
