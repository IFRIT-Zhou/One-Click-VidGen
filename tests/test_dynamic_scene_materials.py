import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from backend.app import video_studio, video_scene_references


class DynamicSceneMaterialsTests(unittest.TestCase):
    def test_retry_preserves_uploaded_scene_without_replanning(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            Image.new('RGB', (8, 8), 'red').save(root / 'scene.jpg')
            asset = {'id': 'original_scene', 'scene_id': 'location_1', 'name': 'room',
                     'image': 'scene.jpg', 'image_status': 'pending', 'image_origin': 'upload',
                     'image_task': {'status': 'completed', 'action': 'upload'},
                     'image_prompt': 'manually edited scene', 'used_by': ['a', 'b']}
            record = {'settings': {}, 'logs': [], 'scene_assets': [asset], 'shots': [
                {'id': 'a', 'image_prompt': 'changed shot'}, {'id': 'b', 'image_prompt': 'changed too'}]}
            original = (root / 'scene.jpg').read_bytes()
            self.assertTrue(video_studio._recover_uploaded_image_status(root, record))
            with patch.object(video_scene_references, 'enabled', return_value=True), \
                 patch.object(video_scene_references, 'plan_references') as planner, \
                 patch.object(video_studio, 'save'), \
                 patch('module4_video_render._render_poster_with_retry') as render:
                video_studio._prepare_scene_assets(root, record, [], threading.Event())
            planner.assert_not_called()
            render.assert_not_called()
            self.assertEqual(record['scene_assets'][0]['id'], 'original_scene')
            self.assertEqual(record['shots'][0]['scene_reference_id'], 'original_scene')
            self.assertEqual((root / 'scene.jpg').read_bytes(), original)

    def test_legacy_upload_recovery_requires_existing_file_and_completed_upload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = {'shots': [{'image': 'missing.jpg', 'image_origin': 'upload',
                                'image_status': 'failed', 'image_task': {'action': 'upload', 'status': 'completed'}}]}
            self.assertFalse(video_studio._recover_uploaded_image_status(root, record))

    def test_illustrated_scene_binding_preserves_source_materials(self):
        from scene_reference_coordinator import bind_scene_references
        from backend.app.visual_editor import VisualEditor
        import json
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset_dir = root / 'other' / 'scene_references'
            asset_dir.mkdir(parents=True)
            scene = asset_dir / 'room.jpg'
            tool = asset_dir / 'tool.jpg'
            scene.write_bytes(b'scene')
            tool.write_bytes(b'tool')
            plan = {'scenes': [{'scene_id': 'location_1', 'members': [0, 1],
                               'reference_prompt': 'room', 'reason': 'same room',
                               'reference_ids': ['tool'], 'reference_image_paths': [str(tool)]}]}
            rows = bind_scene_references([{'image_prompt': 'a'}, {'image_prompt': 'b'}], plan,
                                         {'location_1': scene}, {})
            self.assertEqual(rows[0]['scene_reference']['reference_image_paths'], [str(tool)])
            for row in rows:
                row['scene_reference']['reference_image_paths'] = ['old/staging/tool.jpg']
            mapping_path = VisualEditor._mapping_path(root)
            mapping_path.parent.mkdir(parents=True, exist_ok=True)
            mapping_path.write_text(json.dumps(rows), encoding='utf-8')
            loaded = VisualEditor._load_mapping(root)
            self.assertEqual(loaded[0]['scene_reference']['reference_image_paths'], [str(tool.resolve())])

    def test_scene_generation_receives_selected_instrument(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = root / 'instrument.png'
            Image.new('RGB', (8, 8)).save(reference)
            record = {'settings': {}, 'logs': [], 'shots': [
                {'id': 'a', 'image_prompt': 'room'}, {'id': 'b', 'image_prompt': 'room'}],
                'references': [{'id': 'tool', 'file': 'instrument.png'}]}
            plan = {'scenes': [{'scene_id': 'location_1', 'members': [0, 1],
                               'reference_prompt': '图1的器械在手术室', 'reason': 'same room',
                               'reference_ids': ['tool']}]}
            def render(item, pool):
                self.assertEqual(item['reference_image_paths'], [str(reference.resolve())])
                target = Path(item['_output_path'])
                Image.new('RGB', (8, 8)).save(target)
                return target
            with patch.object(video_scene_references, 'enabled', return_value=True), \
                 patch.object(video_scene_references, 'plan_references', return_value=plan), \
                 patch.object(video_studio, 'save'), \
                 patch('module4_video_render._render_poster_with_retry', side_effect=render) as generated:
                video_studio._prepare_scene_assets(root, record, [], threading.Event())
            self.assertEqual(generated.call_count, 1)
            self.assertEqual(record['scene_assets'][0]['reference_ids'], ['tool'])

    def test_planner_receives_catalog_and_rejects_unknown_reference(self):
        record = {'references': [{'id': 'tool', 'description': '手术器械', 'kind': 'object'}]}
        with patch.object(video_scene_references, 'plan_scene_references', return_value={
                'scenes': [{'reference_ids': ['invented']} ]}) as planner:
            with self.assertRaises(ValueError):
                video_scene_references.plan_references(Path('.'), record)
            self.assertEqual(planner.call_args.args[1]['references'][0]['description'], '手术器械')
