import copy
import hashlib
import io
import stat
import tempfile
import threading
import unittest
from contextlib import ExitStack, nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
import module4_video_render as visual
from backend.app import storyboard_reference_snapshot as snapshots
from backend.app import video_studio as studio


class ReferenceSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name, color in [('current.jpg', 'blue'), ('previous.jpg', 'red'), ('material.jpg', 'green'), ('scene.png', 'yellow')]:
            Image.new('RGB', (16, 16), color).save(self.root / name)
        self.shot = {'id': 's2', 'image': 'current.jpg', 'kind': 'static', 'image_status': 'completed',
                     'image_prompt': '现有画面', 'reference_ids': ['ref_03']}
        self.record = {'id': 'snapshot-test', 'revision': 1, 'logs': [], 'status': 'image_review', 'creation_parameters': {},
                       'references': [{'id': 'ref_03', 'file': 'material.jpg'}],
                       'scene_assets': [{'id': 'room', 'image': 'scene.png', 'name': '房间', 'image_status': 'completed'}],
                       'shots': [{'id': 's1', 'image': 'previous.jpg', 'image_status': 'completed', 'kind': 'static'}, self.shot]}
        studio._image_version(self.root, self.shot)
        self.shot['image_reference_snapshot'] = snapshots.make_snapshot(self.shot['image_version'], [])

    def worker_mocks(self, render):
        stack = ExitStack()
        fake_threading = SimpleNamespace(Event=threading.Event, Thread=lambda target, **kw: SimpleNamespace(start=target))
        for owner, name, value in [(studio, 'threading', fake_threading), (studio, 'read', lambda p: self.record),
                                   (studio, 'save', lambda *a: None), (studio, '_image_language_scope', lambda *a: nullcontext()),
                                   (studio, '_prepare_scene_assets', lambda *a: None),
                                   (visual, 'shared_runninghub_account_pool', lambda *a, **kw: object()),
                                   (visual, '_render_poster_with_retry', render)]:
            stack.enter_context(patch.object(owner, name, value))
        stack.enter_context(patch.object(studio.image_continuity, 'run_with_dependencies',
            side_effect=lambda r, pending, fn, workers, event: [fn(*item) for item in pending]))
        return stack

    def render_green(self, macro, pool):
        target = Path(macro['_output_path'])
        Image.new('RGB', (16, 16), 'green').save(target)
        return target

    def test_request_order_identity_and_immutable_copies(self):
        ordered = ['current.jpg', 'material.jpg', 'scene.png', 'previous.jpg']
        paths, entries = snapshots.capture_reference_inputs(self.root, self.record, self.shot,
            [str(self.root / name) for name in ordered], selection_ids=['ref_03'], use_current_image=True)
        self.assertEqual([(r['kind'], r['id'], r['local_image_number']) for r in entries],
                         [('current_image', 's2', 1), ('reference', 'ref_03', 2), ('scene_reference', 'room', 3), ('previous_shot', 's1', 4)])
        original = (self.root / 'current.jpg').read_bytes()
        for path, name, entry in zip(paths, ordered, entries):
            self.assertEqual(Path(path).read_bytes(), (self.root / name).read_bytes())
            self.assertEqual(entry['sha256'], hashlib.sha256(Path(path).read_bytes()).hexdigest())
            self.assertEqual(Path(path).resolve(), (self.root / entry['file']).resolve())
        (self.root / 'current.jpg').write_bytes(b'new output at same path')
        self.assertEqual(Path(paths[0]).read_bytes(), original)

    def test_same_bytes_deduplicate_without_collapsing_material_identity(self):
        (self.root / 'same.jpeg').write_bytes((self.root / 'material.jpg').read_bytes())
        self.record['references'].append({'id': 'ref_09', 'file': 'same.jpeg'})
        paths, entries = snapshots.capture_reference_inputs(self.root, self.record, self.shot,
            [str(self.root / 'same.jpeg'), str(self.root / 'material.jpg')], selection_ids=['ref_09', 'ref_03'])
        self.assertEqual(paths[0], paths[1])
        self.assertEqual([r['id'] for r in entries], ['ref_09', 'ref_03'])
        self.assertEqual([r['local_image_number'] for r in entries], [1, 2])

    @unittest.skipUnless(snapshots.os.name == 'nt', 'Windows portable compatibility')
    def test_windows_publish_does_not_require_hardlinks(self):
        with patch.object(snapshots.os, 'link', side_effect=OSError('hard links unavailable')):
            paths, entries = snapshots.capture_reference_inputs(self.root, self.record, self.shot,
                [str(self.root / 'material.jpg')])
        self.assertEqual(Path(paths[0]).read_bytes(), (self.root / 'material.jpg').read_bytes())
        self.assertEqual(entries[0]['sha256'], hashlib.sha256(Path(paths[0]).read_bytes()).hexdigest())

    def test_outside_unknown_and_corrupt_snapshot_are_rejected(self):
        with tempfile.TemporaryDirectory() as other:
            foreign = Path(other) / 'a.jpg'; foreign.write_bytes(b'outside')
            self.record['references'].append({'id': 'foreign', 'file': str(foreign)})
            with self.assertRaisesRegex(ValueError, '超出当前项目'):
                snapshots.capture_reference_inputs(self.root, self.record, self.shot, [str(foreign)])
        (self.root / 'unknown.jpg').write_bytes(b'unknown')
        with self.assertRaisesRegex(ValueError, '无法定位素材身份'):
            snapshots.capture_reference_inputs(self.root, self.record, self.shot, [str(self.root / 'unknown.jpg')])
        paths, _ = snapshots.capture_reference_inputs(self.root, self.record, self.shot, [str(self.root / 'material.jpg')])
        frozen = Path(paths[0]); frozen.chmod(stat.S_IREAD | stat.S_IWRITE); frozen.write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, '快照校验失败'):
            snapshots.capture_reference_inputs(self.root, self.record, self.shot, [str(self.root / 'material.jpg')])

    def test_first_generation_uses_frozen_actual_references_and_binds_success(self):
        self.shot.update(image_status='pending')
        calls = []
        def render(macro, pool):
            calls.extend(macro['reference_image_paths'])
            self.assertEqual(len(calls), 1)
            self.assertIn('storyboard_reference_snapshots', Path(calls[0]).parts)
            self.assertEqual(Path(calls[0]).read_bytes(), (self.root / 'material.jpg').read_bytes())
            return self.render_green(macro, pool)
        with self.worker_mocks(render):
            studio._start_storyboard_images(self.root, self.record, [{}])
        self.assertEqual(self.shot['image_status'], 'completed')
        snapshot = self.shot['image_reference_snapshot']
        self.assertEqual(snapshot['image_sha256'], hashlib.sha256((self.root / self.shot['image']).read_bytes()).hexdigest())
        self.assertEqual([(r['kind'], r['id']) for r in snapshot['references']], [('reference', 'ref_03')])

    def test_redraw_freezes_current_image_archives_old_snapshot_and_failure_preserves_it(self):
        old_snapshot = copy.deepcopy(self.shot['image_reference_snapshot'])
        old_bytes = (self.root / 'current.jpg').read_bytes()
        with self.worker_mocks(self.render_green):
            studio._start_storyboard_redraw(self.root, self.record, self.shot,
                studio.StoryboardRedraw(revision=1, prompt='新画面', use_current_image=True), [{}], [str(self.root / 'current.jpg')])
        self.assertEqual(self.shot['image_task']['status'], 'completed')
        self.assertEqual(self.shot['image_history'][-1]['image_reference_snapshot'], old_snapshot)
        current = copy.deepcopy(self.shot['image_reference_snapshot'])
        self.assertEqual(current['references'][0]['kind'], 'current_image')
        self.assertEqual((self.root / current['references'][0]['file']).read_bytes(), old_bytes)
        history = copy.deepcopy(self.shot['image_history'])
        output = (self.root / 'current.jpg').read_bytes()
        with self.worker_mocks(lambda *a: (_ for _ in ()).throw(RuntimeError('mock failure'))):
            studio._start_storyboard_redraw(self.root, self.record, self.shot,
                studio.StoryboardRedraw(revision=1, prompt='失败重试', reference_ids=['ref_03']), [{}], [str(self.root / 'material.jpg')])
        self.assertEqual(self.shot['image_task']['status'], 'failed')
        self.assertEqual(self.shot['image_reference_snapshot'], current)
        self.assertEqual(self.shot['image_history'], history)
        self.assertEqual((self.root / 'current.jpg').read_bytes(), output)
        self.assertNotIn(studio.image_edit_key(self.record['id'], self.shot['id']), studio.IMAGE_EDITS)

    def test_upload_uses_empty_reference_snapshot_and_undo_restores_previous(self):
        _, refs = snapshots.capture_reference_inputs(self.root, self.record, self.shot, [str(self.root / 'material.jpg')])
        self.shot['image_reference_snapshot'] = snapshots.make_snapshot(self.shot['image_version'], refs)
        old = copy.deepcopy(self.shot['image_reference_snapshot'])
        content = io.BytesIO(); Image.new('RGB', (16, 16), 'orange').save(content, format='PNG')
        async def read_upload():
            return content.getvalue()
        with patch.object(studio, 'require_user', return_value={'id': 1}), patch.object(studio, 'directory', return_value=self.root), \
             patch.object(studio, 'read', return_value=self.record), patch.object(studio, 'shot_editable'), patch.object(studio, 'save'):
            # Upload read is an immediate in-memory coroutine; no network/event
            # loop is needed for this endpoint contract test.
            call = studio.replace_storyboard_image('snapshot-test', 's2', None, revision=1,
                file=SimpleNamespace(read=read_upload, filename='upload.png'), prompt='')
            with self.assertRaises(StopIteration):
                call.send(None)
            self.assertEqual(self.shot['image_reference_snapshot']['references'], [])
            self.assertNotEqual(self.shot['image_reference_snapshot']['image_sha256'], old['image_sha256'])
            studio.undo_storyboard_image('snapshot-test', 's2', studio.Review(revision=self.record['revision']), None)
        self.assertEqual(self.shot['image_reference_snapshot'], old)


if __name__ == '__main__':
    unittest.main()
