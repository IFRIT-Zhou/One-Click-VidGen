import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import module4_video_render as visual
from backend.app import pipeline


class SceneReferenceResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = self.root / "workspace" / "3_visual_template"
        self.assets = self.directory / "assets"
        self.assets.mkdir(parents=True)
        self.checkpoint = self.root / "checkpoint"
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict("os.environ", {"VISUAL_CHECKPOINT_DIR": str(self.checkpoint)}))
        for name, value in {
            "VISUAL_DIR": self.directory, "ASSETS_DIR": self.assets,
            "STORY_PLAN_PATH": self.directory / "story_plan.json",
            "POSTER_MAPPING_PATH": self.directory / "poster_mapping.json",
            "VISUAL_PROMPT_PLAN_PATH": self.directory / "visual_prompt_plan.json",
            "CLOUD_RETRY_STATE_PATH": self.directory / "cloud_image_retry_state.json",
        }.items():
            self.stack.enter_context(patch.object(visual, name, value))

    def mapping(self, path):
        return [{
            "macro_scene_id": f"poster_{i}", "asset_filename": f"poster_{i}_done.jpg",
            "image_prompt": "original shot",
            "reference_image_paths": ["user-reference.png", str(path)],
            "scene_reference": {"scene_id": "location_1", "prompt": "original room",
                                "path": str(path), "input_number": 2},
        } for i in (1, 2)]

    def test_scene_assets_and_plan_survive_workspace_cleanup(self):
        reference = self.assets / "scene_ref_location_1_hash.jpg"
        reference.write_bytes(b"paid reference")
        plan = self.directory / "scene_reference_plan.json"
        plan.write_text('{"scenes":[]}', encoding="utf-8")
        visual._sync_visual_checkpoint()
        reference.unlink()
        plan.unlink()
        self.assertTrue(visual._restore_visual_checkpoint())
        self.assertEqual(reference.read_bytes(), b"paid reference")
        self.assertTrue(plan.is_file())

    def test_legacy_missing_shared_reference_is_generated_once(self):
        old = self.assets / "scene_ref_location_1_lost.jpg"
        replacement = self.assets / "scene_ref_location_1_new.jpg"
        replacement.write_bytes(b"replacement")
        mapping = self.mapping(old)
        with patch.object(visual, "_render_poster_with_retry", return_value=replacement) as render:
            result = visual._recover_missing_scene_references(mapping, [{"api_key": "test"}])
        render.assert_called_once()
        for item in result:
            self.assertEqual(item["reference_image_paths"], ["user-reference.png", str(replacement.resolve())])
            self.assertEqual(item["scene_reference"]["input_number"], 2)
            self.assertEqual(item["image_prompt"], "original shot")
            self.assertIn("done.jpg", item["asset_filename"])
        self.assertTrue((self.checkpoint / "assets" / replacement.name).is_file())
        self.assertEqual(json.loads(visual.POSTER_MAPPING_PATH.read_text(encoding="utf-8")), result)

    def test_moved_original_is_reused_without_paid_generation(self):
        reference = self.assets / "scene_ref_location_1_hash.jpg"
        reference.write_bytes(b"original")
        mapping = self.mapping(self.root / "old-install" / reference.name)
        with patch.object(visual, "_render_poster_with_retry") as render:
            visual._recover_missing_scene_references(mapping, [])
        render.assert_not_called()
        self.assertEqual(mapping[0]["scene_reference"]["path"], str(reference.resolve()))

    def test_existing_reference_does_not_trigger_generation(self):
        reference = self.assets / "scene_ref_location_1_hash.jpg"
        reference.write_bytes(b"original")
        mapping = self.mapping(reference)
        with patch.object(visual, "_render_poster_with_retry") as render:
            self.assertEqual(visual._recover_missing_scene_references(mapping, []), mapping)
        render.assert_not_called()

    def test_invalid_reference_slot_is_not_guessed(self):
        mapping = self.mapping(self.assets / "lost.jpg")
        mapping[0]["scene_reference"]["input_number"] = 1
        with patch.object(visual, "_render_poster_with_retry") as render:
            with self.assertRaisesRegex(ValueError, "编号与路径不一致"):
                visual._recover_missing_scene_references(mapping, [])
        render.assert_not_called()

    def test_guided_restore_includes_scene_plan_and_reference(self):
        jobs = self.root / "jobs"
        checkpoint = jobs / "job" / "artifacts" / "visual_runtime" / "story_plan"
        (checkpoint / "assets").mkdir(parents=True)
        (checkpoint / "scene_reference_plan.json").write_text("{}", encoding="utf-8")
        (checkpoint / "assets" / "scene_ref_location_1_hash.jpg").write_bytes(b"paid")
        with patch.object(pipeline, "JOBS_DIR", jobs), patch.object(pipeline, "WORKSPACE_DIR", self.root / "workspace"):
            self.assertTrue(pipeline.restore_step_visual_runtime_checkpoint(pipeline.Job(id="job", request={})))
        self.assertTrue((self.directory / "scene_reference_plan.json").is_file())
        self.assertTrue((self.assets / "scene_ref_location_1_hash.jpg").is_file())
