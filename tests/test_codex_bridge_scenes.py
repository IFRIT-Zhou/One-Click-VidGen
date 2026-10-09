import copy
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from PIL import Image
from backend.app import codex_bridge as bridge, video_studio as studio


class SceneBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for target, name, value in ((studio, 'ROOT', self.root), (bridge, 'enabled', lambda: True),
                                   (bridge, 'require_user', lambda r: {'id': 1}),
                                   (studio, 'require_user', lambda r: {'id': 1})):
            p = patch.object(target, name, value); p.start(); self.addCleanup(p.stop)
        self.id = 'a' * 32; self.path = studio.directory(1, self.id)
        (self.path / 'assets').mkdir(parents=True)
        (self.path / 'assets/audio.wav').write_bytes(b'audio')
        for name in ('old.jpg', 'other.jpg'):
            Image.new('RGB', (16, 16), 'red').save(self.path / 'assets' / name)
        self.record = {'id': self.id, 'revision': 1, 'status': 'image_review', 'settings': {},
            'audio': 'assets/audio.wav', 'scenes': [{'slide_id': 's', 'start': 0, 'end': 4, 'text': 'test'}],
            'logs': [], 'references': [], 'creation_parameters': {'scene_references_enabled': True},
            'scene_references_status': 'completed',
            'scene_assets': [{'id': 'loc1', 'image': 'assets/old.jpg', 'image_status': 'completed',
                              'image_version': 'old', 'used_by': ['one', 'two'], 'image_prompt': 'room', 'reason': 'same'},
                             {'id': 'loc2', 'image': 'assets/other.jpg', 'image_status': 'completed',
                              'used_by': ['three'], 'image_prompt': 'other', 'reason': 'same'}],
            'shots': [{'id': i, 'scene_reference_id': 'loc1' if i != 'three' else 'loc2',
                       'kind': 'static', 'image_prompt': 'subject', 'reference_ids': [], 'image_status': 'completed'}
                      for i in ('one', 'two', 'three')]}
        studio.save(self.path, self.record)
        app = FastAPI(); app.include_router(bridge.router); app.include_router(studio.router)
        self.client = TestClient(app)

    def endpoint(self, action):
        return f'/api/codex-bridge/projects/{self.id}/scene-{action}'

    def load(self):
        return bridge.read_record(self.path)

    def payload(self, action, **values):
        r = self.load()
        return dict(revision=r['revision'], timeline_token=bridge.timeline_token(r, self.path),
                    audio_confirmed=True, action=action, **values)

    def apply(self, data):
        preview = self.client.post(self.endpoint('validate'), json=data)
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertTrue(preview.json()['ok'], preview.text)
        data = {**data, 'confirmed': True, 'confirmation_token': preview.json()['confirmation_token']}
        response = self.client.post(self.endpoint('apply'), json=data)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['written'], response.text)
        return response.json(), data

    def actual_redraw_paths(self, shot_id, reference_ids=None):
        r = self.load()
        with patch.object(studio, '_image_configs', return_value=[]), \
                patch.object(studio, '_start_storyboard_redraw') as start:
            response = self.client.post(f'/api/video-studio/{self.id}/images/{shot_id}/redraw', json={
                'revision': r['revision'], 'prompt': 'subject', 'reference_ids': reference_ids or [],
                'use_scene_reference': True})
            self.assertEqual(response.status_code, 200, response.text)
            return start.call_args.args[5]

    def test_disable_preview_confirm_and_actual_request(self):
        before = (self.path / 'record.json').read_bytes()
        data = self.payload('disable', scene_id='loc1')
        preview = self.client.post(self.endpoint('validate'), json=data).json()
        self.assertEqual(preview['changed'], ['one', 'two'])
        self.assertEqual((self.path / 'record.json').read_bytes(), before)
        self.assertFalse((self.path / 'codex_bridge_backups').exists())
        self.assertEqual(self.client.post(self.endpoint('apply'), json=data).status_code, 409)
        result, sent = self.apply(data)
        self.assertEqual(self.actual_redraw_paths('one'), [])
        r = self.load()
        self.assertEqual(r['scene_assets'][1], self.record['scene_assets'][1])
        self.assertEqual(r['scenes'], self.record['scenes'])
        self.assertEqual((self.path / 'assets/audio.wav').read_bytes(), b'audio')
        self.assertTrue((self.path / 'assets/old.jpg').exists())
        # Old explicit paths cannot bypass the final generation boundary.
        prompt, paths, asset = studio._image_inputs(self.path, r, r['shots'][0], 'subject',
                                                  references=[str(self.path / 'assets/old.jpg')])
        self.assertEqual(paths, [])

    def test_unbind_one_preserves_other_shots_and_restore(self):
        result, data = self.apply(self.payload('unbind', shot_ids=['one']))
        r = self.load()
        self.assertIsNone(studio._scene_asset(r, r['shots'][0]))
        self.assertIsNotNone(studio._scene_asset(r, r['shots'][1]))
        self.assertEqual(r['shots'][1], self.record['shots'][1])
        restored, _ = self.apply(self.payload('restore', backup_id=result['backup_id']))
        self.assertEqual(self.load()['shots'][0]['scene_reference_id'], 'loc1')
        self.assertNotIn('scene_reference_disabled', self.load()['shots'][0])

    def test_replace_actual_request_no_old_image_and_restore(self):
        upload = self.root / 'upload.png'; Image.new('RGB', (16, 16), 'blue').save(upload)
        from backend.app import editor
        with patch.object(editor, 'upload_path', return_value=upload):
            result, sent = self.apply(self.payload('replace', scene_id='loc1', upload_id='upload.png', image_confirmed=True))
        r = self.load(); asset = r['scene_assets'][0]
        self.assertNotEqual(asset['image'], 'assets/old.jpg')
        paths = self.actual_redraw_paths('one', ['loc1'])
        self.assertNotIn(str((self.path / 'assets/old.jpg').resolve()), paths)
        self.assertIn(str((self.path / asset['image']).resolve()), paths)
        # Restore only this scene, even after independent user edits elsewhere.
        r = self.load(); r['shots'][2]['image_prompt'] = 'user edit'; studio.save(self.path, r)
        self.apply(self.payload('restore', backup_id=result['backup_id']))
        self.assertEqual(self.load()['scene_assets'][0]['image'], 'assets/old.jpg')
        self.assertEqual(self.load()['shots'][2]['image_prompt'], 'user edit')
        self.assertTrue((self.path / asset['image']).exists())

    def test_stale_permission_busy_and_changed_upload(self):
        data = self.payload('disable', scene_id='loc1')
        r = self.load(); r['revision'] += 1; studio.save(self.path, r)
        self.assertEqual(self.client.post(self.endpoint('validate'), json=data).status_code, 409)
        with patch.object(bridge, 'require_user', side_effect=HTTPException(401, 'login')):
            self.assertEqual(self.client.post(self.endpoint('validate'), json=self.payload('disable', scene_id='loc1')).status_code, 401)
        with patch.object(bridge, 'enabled', return_value=False):
            self.assertEqual(self.client.post(self.endpoint('validate'), json=data).status_code, 404)
        studio.ACTIVE.add(str(self.path))
        try:
            self.assertEqual(self.client.post(self.endpoint('validate'), json=self.payload('disable', scene_id='loc1')).status_code, 409)
        finally:
            studio.ACTIVE.discard(str(self.path))
        from backend.app import editor
        upload = self.root / 'upload.png'; Image.new('RGB', (16, 16), 'blue').save(upload)
        with patch.object(editor, 'upload_path', return_value=upload):
            data = self.payload('replace', scene_id='loc1', upload_id='upload.png', image_confirmed=True)
            preview = self.client.post(self.endpoint('validate'), json=data).json()
            Image.new('RGB', (16, 16), 'green').save(upload)
            response = self.client.post(self.endpoint('apply'), json={**data, 'confirmed': True,
                                        'confirmation_token': preview['confirmation_token']})
            self.assertEqual(response.status_code, 409)

    def test_retry_keeps_disabled_and_unbound_without_generation(self):
        import threading
        import module4_video_render as visual
        self.apply(self.payload('disable', scene_id='loc1'))
        self.apply(self.payload('unbind', shot_ids=['three']))
        r = self.load()
        with patch.object(visual, '_render_poster_with_retry') as render:
            studio._prepare_scene_assets(self.path, r, [], threading.Event())
            render.assert_not_called()
        self.assertIsNone(studio._scene_asset(r, r['shots'][0]))
        self.assertIsNone(studio._scene_asset(r, r['shots'][2]))
        self.assertTrue(r['shots'][2]['scene_reference_disabled'])

    def test_apply_idempotency_and_readback_rollback(self):
        result, data = self.apply(self.payload('disable', scene_id='loc1'))
        retry = self.client.post(self.endpoint('apply'), json=data).json()
        self.assertTrue(retry['already_applied'])
        self.assertEqual(retry['revision'], result['revision'])
        data = self.payload('unbind', shot_ids=['three'])
        preview = self.client.post(self.endpoint('validate'), json=data).json()
        before = self.load()
        original = bridge.read_record
        def corrupt_readback(path):
            value = original(path)
            if value['revision'] > before['revision']:
                value['audio'] = 'wrong.wav'
            return value
        with patch.object(bridge, 'read_record', side_effect=corrupt_readback):
            response = self.client.post(self.endpoint('apply'), json={**data, 'confirmed': True,
                                        'confirmation_token': preview['confirmation_token']})
        self.assertEqual(response.status_code, 500)
        recovered = self.load()
        self.assertEqual(recovered['shots'], before['shots'])
        self.assertEqual(recovered['audio'], before['audio'])
