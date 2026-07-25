import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import module4_video_render as visual
from backend.app.visual_editor import VisualEditor


class VisualEditorTimingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name)
        (self.project / "other").mkdir()
        (self.project / "image").mkdir()
        self.mapping = [
            {"macro_scene_id": "poster_001", "includes_slides": ["scene_001", "scene_002"], "image_prompt": "one"},
            {"macro_scene_id": "poster_002", "includes_slides": ["scene_003", "scene_004"], "image_prompt": "two"},
            {"macro_scene_id": "poster_003", "includes_slides": ["scene_005"], "image_prompt": "three"},
        ]
        self.timeline = [
            {"slide_id": f"scene_{index:03d}", "start": (index - 1) * 2, "end": index * 2, "text_content": f"sentence {index}"}
            for index in range(1, 6)
        ]
        VisualEditor._save_mapping(self.project, self.mapping)
        VisualEditor._timeline_path(self.project).write_text(json.dumps(self.timeline), encoding="utf-8")
        for macro_id in ("poster_001", "poster_002", "poster_003"):
            (self.project / "image" / f"{macro_id}.jpg").write_bytes(b"image")
        self.editor = VisualEditor()
        self.output_patch = patch.object(VisualEditor, "output_dir", return_value=self.project)
        self.output_patch.start()

    def tearDown(self) -> None:
        self.output_patch.stop()
        self.tmp.cleanup()

    def test_extending_picture_moves_exactly_one_sentence_from_next_picture(self) -> None:
        self.editor.adjust_timing(
            job_id="job", user_id=1, macro_id="poster_001", action="extend_next"
        )
        mapping = VisualEditor._load_mapping(self.project)
        self.assertEqual(mapping[0]["includes_slides"], ["scene_001", "scene_002", "scene_003"])
        self.assertEqual(mapping[1]["includes_slides"], ["scene_004"])
        self.assertTrue(VisualEditor._timing_backup_path(self.project).is_file())

    def test_cannot_leave_neighbor_without_a_sentence(self) -> None:
        with self.assertRaisesRegex(ValueError, "只剩一句"):
            self.editor.adjust_timing(
                job_id="job", user_id=1, macro_id="poster_002", action="extend_next"
            )

    def test_reset_restores_sentence_allocation_but_not_prompt(self) -> None:
        self.editor.adjust_timing(
            job_id="job", user_id=1, macro_id="poster_001", action="extend_next"
        )
        mapping = VisualEditor._load_mapping(self.project)
        mapping[0]["image_prompt"] = "redrawn prompt remains"
        VisualEditor._save_mapping(self.project, mapping)
        self.editor.reset_timing(job_id="job", user_id=1)
        restored = VisualEditor._load_mapping(self.project)
        self.assertEqual(restored[0]["includes_slides"], ["scene_001", "scene_002"])
        self.assertEqual(restored[0]["image_prompt"], "redrawn prompt remains")

    def test_html_is_rebuilt_from_adjusted_sentence_ranges(self) -> None:
        self.editor.adjust_timing(
            job_id="job", user_id=1, macro_id="poster_001", action="extend_next"
        )
        mapping = VisualEditor._load_mapping(self.project)
        VisualEditor._write_timing_html(self.project, mapping, self.timeline)
        html = (self.project / "other" / "最终画面.html").read_text(encoding="utf-8")
        self.assertIn('"end": 6.0', html)
        self.assertIn('../image/poster_001.jpg', html)

    def test_legacy_empty_mapping_recovers_groups_from_exported_html(self) -> None:
        poster_timeline = [
            {"start": 0, "end": 4, "url": "../image/poster_001.jpg"},
            {"start": 4, "end": 8, "url": "../image/poster_002.jpg"},
            {"start": 8, "end": 10, "url": "../image/poster_003.jpg"},
        ]
        visual.write_html(self.timeline, poster_timeline, self.project / "other" / "最终画面.html")
        empty = [{**item, "includes_slides": []} for item in self.mapping]
        recovered, did_recover = VisualEditor._mapping_with_recovered_timing(self.project, empty, self.timeline)
        self.assertTrue(did_recover)
        self.assertEqual(recovered[0]["includes_slides"], ["scene_001", "scene_002"])
        self.assertEqual(recovered[2]["includes_slides"], ["scene_005"])

    def test_removing_middle_picture_distributes_subtitles_and_reset_restores_it(self) -> None:
        self.editor.remove_timing_picture(job_id="job", user_id=1, macro_id="poster_002")
        edited = VisualEditor._load_mapping(self.project)
        self.assertEqual([item["macro_scene_id"] for item in edited], ["poster_001", "poster_003"])
        self.assertEqual(edited[0]["includes_slides"], ["scene_001", "scene_002", "scene_003"])
        self.assertEqual(edited[1]["includes_slides"], ["scene_004", "scene_005"])
        self.editor.reset_timing(job_id="job", user_id=1)
        restored = VisualEditor._load_mapping(self.project)
        self.assertEqual([item["macro_scene_id"] for item in restored], ["poster_001", "poster_002", "poster_003"])
        self.assertEqual(restored[1]["includes_slides"], ["scene_003", "scene_004"])


if __name__ == "__main__":
    unittest.main()
