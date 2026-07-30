import json
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

import module4_video_render as visual
from backend.app.visual_editor import VisualEditor


class VisualEditorTimingTest(unittest.TestCase):
    def test_new_bgm_selection_is_archived_for_visual_rerender(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "output" / "demo"
            source_a = root / "warm.mp3"
            source_b = root / "ending.wav"
            source_a.write_bytes(b"music-a")
            source_b.write_bytes(b"music-b")
            count = VisualEditor._archive_bgm_override(project, {
                "tracks": [
                    {"path": str(source_a), "volume_db": -10},
                    {"path": str(source_b), "volume_db": -8},
                ],
                "fade_enabled": True,
                "fade_duration": 2.5,
            })
            self.assertEqual(count, 2)
            manifest_path = project / "other" / "BGM设置.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual([item["filename"] for item in manifest["tracks"]], ["001.mp3", "002.wav"])
            self.assertEqual([item["volume_db"] for item in manifest["tracks"]], [-10.0, -8.0])
            self.assertTrue(manifest["fade_enabled"])
            self.assertEqual(manifest["fade_duration"], 2.5)
            self.assertEqual((project / "input" / "BGM" / "001.mp3").read_bytes(), b"music-a")
            inspected = VisualEditor._inspect_bgm_settings("job-1", project)
            self.assertTrue(inspected["enabled"])
            self.assertEqual(len(inspected["tracks"]), 2)
            self.assertIn("/api/jobs/job-1/visual-bgm/001.mp3", inspected["tracks"][0]["url"])

            # Reordering an already archived track must stage it before the old
            # BGM directory is cleaned, rather than deleting its own source.
            archived_source = project / "input" / "BGM" / "001.mp3"
            replaced = VisualEditor._archive_bgm_override(project, {
                "tracks": [{"path": str(archived_source), "volume_db": -6}],
                "fade_enabled": False,
                "fade_duration": 1,
            })
            self.assertEqual(replaced, 1)
            self.assertEqual((project / "input" / "BGM" / "001.mp3").read_bytes(), b"music-a")

    def test_prepare_render_workspace_recreates_cleaned_module_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = VisualEditor._prepare_render_workspace(root / "output" / "other" / ".render_runtime")

            self.assertTrue(paths["visual"].is_dir())
            self.assertTrue(paths["assets"].is_dir())
            self.assertTrue(paths["audio"].is_dir())
            self.assertTrue(paths["final"].is_dir())

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

    def test_saved_current_timing_becomes_new_reset_baseline_and_archives_old_one(self) -> None:
        self.editor.adjust_timing(
            job_id="job", user_id=1, macro_id="poster_001", action="extend_next"
        )
        saved_groups = [list(item["includes_slides"]) for item in VisualEditor._load_mapping(self.project)]
        self.editor.commit_timing_baseline(job_id="job", user_id=1)
        self.assertTrue(any((self.project / "other" / "时序历史基准").rglob("画面映射.初始时序.json")))

        self.editor.adjust_timing(
            job_id="job", user_id=1, macro_id="poster_001", action="shrink_next"
        )
        self.editor.reset_timing(job_id="job", user_id=1)
        restored_groups = [list(item["includes_slides"]) for item in VisualEditor._load_mapping(self.project)]
        self.assertEqual(restored_groups, saved_groups)

    def test_selecting_history_restores_it_without_replacing_current_baseline(self) -> None:
        original_groups = [list(item["includes_slides"]) for item in self.mapping]
        self.editor.adjust_timing(
            job_id="job", user_id=1, macro_id="poster_001", action="extend_next"
        )
        saved_groups = [list(item["includes_slides"]) for item in VisualEditor._load_mapping(self.project)]
        self.editor.commit_timing_baseline(job_id="job", user_id=1)
        history = VisualEditor._timing_history_entries(self.project)
        self.assertEqual(len(history), 1)

        self.editor.restore_timing_history(
            job_id="job", user_id=1, history_id=history[0]["id"]
        )
        historical_groups = [list(item["includes_slides"]) for item in VisualEditor._load_mapping(self.project)]
        self.assertEqual(historical_groups, original_groups)

        self.editor.reset_timing(job_id="job", user_id=1)
        reset_groups = [list(item["includes_slides"]) for item in VisualEditor._load_mapping(self.project)]
        self.assertEqual(reset_groups, saved_groups)

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

    def test_archived_main_references_keep_original_numbering(self) -> None:
        reference_dir = self.project / "other" / "reference_images"
        reference_dir.mkdir()
        for index in (1, 2, 3):
            (reference_dir / f"main_{index:02d}.png").write_bytes(b"image")
        (self.project / "other" / "参考图清单.json").write_text(
            json.dumps([
                {"reference_id": f"图{index}", "filename": f"main_{index:02d}.png"}
                for index in (1, 2, 3)
            ], ensure_ascii=False),
            encoding="utf-8",
        )
        paths = VisualEditor._archived_main_reference_paths(self.project)
        self.assertEqual([Path(path).name for path in paths], ["main_01.png", "main_02.png", "main_03.png"])

    def test_confirm_current_picture_becomes_new_baseline_without_deleting_history(self) -> None:
        image = self.project / "image" / "poster_001.jpg"
        image.write_bytes(b"first image")
        image.with_suffix(".txt").write_text("first prompt", encoding="utf-8")
        VisualEditor._backup_current(self.project, image, "poster_001")
        image.write_bytes(b"satisfied redraw")

        job = SimpleNamespace(id="job", user_id=1)
        with patch.object(self.editor, "_log"):
            self.editor.commit_baseline(
                job=job,
                user_id=1,
                macro_id="poster_001",
                prompt="satisfied prompt",
            )

        backup_dir = VisualEditor._backup_dir(self.project)
        self.assertEqual((backup_dir / "poster_001.original.jpg").read_bytes(), b"satisfied redraw")
        self.assertEqual((backup_dir / "poster_001.original.txt").read_text(encoding="utf-8"), "satisfied prompt")
        self.assertTrue(any((backup_dir / "历史基准").rglob("poster_001.original.jpg")))
        self.assertEqual(VisualEditor._load_mapping(self.project)[0]["image_prompt"], "satisfied prompt")

        # A later redraw first backs up the confirmed baseline, then replaces it.
        VisualEditor._backup_current(self.project, image, "poster_001")
        image.write_bytes(b"later redraw")
        self.editor.undo(job_id="job", user_id=1, macro_id="poster_001")
        self.assertEqual(image.read_bytes(), b"satisfied redraw")

    def test_legacy_segmented_output_namespaces_duplicate_slide_ids(self) -> None:
        mapping = [
            {"macro_scene_id": "part_001_poster_001", "includes_slides": ["scene_001", "scene_002"], "image_prompt": "one"},
            {"macro_scene_id": "part_002_poster_001", "includes_slides": ["scene_001", "scene_002"], "image_prompt": "two"},
        ]
        timeline = [
            {"id": "segment_001", "slide_id": "scene_001", "start": 0, "end": 1, "text_content": "part one a"},
            {"id": "segment_002", "slide_id": "scene_002", "start": 1, "end": 2, "text_content": "part one b"},
            {"id": "segment_001", "slide_id": "scene_001", "start": 2, "end": 3, "text_content": "part two a"},
            {"id": "segment_002", "slide_id": "scene_002", "start": 3, "end": 4, "text_content": "part two b"},
        ]
        VisualEditor._save_mapping(self.project, mapping)
        VisualEditor._timeline_path(self.project).write_text(json.dumps(timeline), encoding="utf-8")
        self.assertTrue(VisualEditor._migrate_segmented_slide_ids(self.project))
        migrated_mapping = VisualEditor._load_mapping(self.project)
        migrated_timeline = VisualEditor._load_timeline(self.project)
        self.assertEqual(migrated_mapping[0]["includes_slides"], ["part_001_scene_001", "part_001_scene_002"])
        self.assertEqual(migrated_mapping[1]["includes_slides"], ["part_002_scene_001", "part_002_scene_002"])
        self.assertEqual(
            [item["slide_id"] for item in migrated_timeline],
            ["part_001_scene_001", "part_001_scene_002", "part_002_scene_001", "part_002_scene_002"],
        )
        self.assertTrue((self.project / "other" / "画面映射.分段编号修复前.json").is_file())


if __name__ == "__main__":
    unittest.main()
