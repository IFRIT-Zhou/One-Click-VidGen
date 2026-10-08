import tempfile
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
import module4_video_render as visual
from backend.app import video_studio as studio


class StoryboardRegenerationTests(unittest.TestCase):
    def test_existing_image_still_archived_before_replacement(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            original = path / 'original.jpg'
            Image.new('RGB', (16, 16), 'blue').save(original)
            shot = {'id': 'existing', 'image': 'original.jpg', 'image_prompt': 'original'}
            target = studio._archive_storyboard_image(path, shot)
            self.assertEqual(target, original)
            self.assertEqual(len(shot['image_history']), 1)
            self.assertEqual((path / shot['image_history'][0]['image']).read_bytes(), original.read_bytes())

    def test_new_shot_redraw_commits_first_image_without_original(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            rendered = path / 'rendered.jpg'
            Image.new('RGB', (16, 16), 'red').save(rendered)
            shot = {'id': 'new-shot', 'image_status': 'pending', 'kind': 'static'}
            record = {'id': 'new-image', 'revision': 1, 'logs': [], 'shots': [shot]}
            with patch.object(studio.threading, 'Thread', side_effect=lambda **kw: SimpleNamespace(start=kw['target'])), \
                 patch.object(studio, 'read', return_value=record), \
                 patch.object(studio, 'save'), \
                 patch.object(studio, '_image_language_scope', return_value=ExitStack()), \
                 patch.object(visual, 'shared_runninghub_account_pool', return_value=object()), \
                 patch.object(visual, '_render_poster_with_retry', return_value=rendered):
                studio._start_storyboard_redraw(path, record, shot,
                    studio.StoryboardRedraw(revision=1, prompt='new prompt'), [{}], [])
            self.assertEqual(shot['image_task']['status'], 'completed')
            self.assertEqual(shot['image_status'], 'completed')
            self.assertTrue((path / shot['image']).is_file())
            self.assertNotIn('image_history', shot)
            self.assertTrue(shot['image_version'])

    def record(self):
        old = {'id': 'a', 'image': 'assets/storyboards/a.jpg', 'image_status': 'completed',
               'image_origin': 'generated', 'image_version': 'old-hash', 'image_prompt': 'old'}
        return {'id': 'test', 'status': 'image_review', 'revision': 1, 'logs': [],
                'shots': [{**old, 'image_prompt': 'new'}], 'replanning_history': [{'shots': [old]}]}

    def test_legacy_reuse_recovered_once_without_deleting_assets(self):
        record = self.record()
        self.assertTrue(studio._recover_replanned_image_cache(record))
        self.assertEqual(record['shots'][0]['image_status'], 'failed')
        self.assertEqual(record['shots'][0]['image'], 'assets/storyboards/a.jpg')
        self.assertFalse(studio._recover_replanned_image_cache(record))

    def test_completed_projects_manual_edits_and_new_generations_untouched(self):
        for field, value in [('image_generation_id', 'new'), ('image_origin', 'reference_redraw'),
                             ('image_version', 'different'), ('image_history', [{'image': 'history.jpg'}])]:
            record = self.record()
            record['shots'][0][field] = value
            self.assertFalse(studio._recover_replanned_image_cache(record))
        record = self.record()
        record['status'] = 'completed'
        self.assertFalse(studio._recover_replanned_image_cache(record))

    def test_regeneration_never_hits_existing_output_and_preserves_old_file(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            old = root / 'assets/storyboards/a.jpg'
            old.parent.mkdir(parents=True)
            Image.new('RGB', (8, 8), 'red').save(old)
            old_bytes = old.read_bytes()
            record = self.record()
            record['shots'][0]['image_status'] = 'pending'
            targets = []
            def render(macro, pool):
                target = Path(macro['_output_path'])
                self.assertFalse(target.exists(), 'stale output would be reused by renderer')
                targets.append(target)
                Image.new('RGB', (8, 8), 'green').save(target)
                return target
            fake_threading = SimpleNamespace(Event=threading.Event,
                Thread=lambda target, **kwargs: SimpleNamespace(start=target))
            stack.enter_context(patch.object(studio, 'threading', fake_threading))
            stack.enter_context(patch.object(studio, 'save'))
            stack.enter_context(patch.object(studio, '_prepare_scene_assets'))
            stack.enter_context(patch.object(studio, '_bind_material_numbers', side_effect=lambda r, s, p: p))
            stack.enter_context(patch.object(studio, '_image_inputs', return_value=('new', [], None)))
            stack.enter_context(patch.object(visual, 'shared_runninghub_account_pool', return_value=object()))
            stack.enter_context(patch.object(visual, '_render_poster_with_retry', side_effect=render))
            for _ in range(2):
                record['shots'][0]['image_status'] = 'pending'
                studio._start_storyboard_images(root, record, [{}])
                self.assertEqual(record['shots'][0]['image_status'], 'completed')
            self.assertEqual(len(set(targets)), 2)
            self.assertEqual(old.read_bytes(), old_bytes)
            self.assertTrue(all(path.is_file() for path in targets))
            self.assertEqual((root / record['shots'][0]['image']).resolve(), targets[-1])
            self.assertTrue(record['shots'][0]['image_generation_id'])


if __name__ == '__main__':
    unittest.main()
