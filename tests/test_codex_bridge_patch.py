import copy
import unittest
from unittest.mock import patch

import test_codex_bridge as fixtures
from backend.app import codex_bridge as bridge


class PatchTests(unittest.TestCase):
    endpoint = fixtures.CodexBridgeTests.endpoint
    load = fixtures.CodexBridgeTests.load
    dynamic = fixtures.CodexBridgeTests.dynamic

    def setUp(self):
        fixtures.CodexBridgeTests.setUp(self)
        self.dynamic()
        self.assertTrue(self.client.post(self.endpoint('apply'), json=self.payload).json()['ok'])
        r = self.load()
        r['shots'].append({'id': 'untouched', 'kind': 'static', 'slide_ids': [], 'image': 'assets/other.jpg',
                           'image_prompt': 'legacy draft', 'private_field': {'keep': True}})
        s = r['shots'][0]
        s.update(image='assets/image.jpg', image_status='completed', video='assets/video.mp4',
                 video_status='completed', video_version='old', generation_options={'backend': 'api'})
        r.update(status='completed', export={'raw': '/paid-output.mp4'})
        bridge.studio.save(self.path, r)
        for name in ('image.jpg', 'video.mp4', 'other.jpg'):
            (self.path/'assets'/name).write_bytes(b'paid-asset')
        self.before = self.load()
        self.repair = {'revision': r['revision'], 'timeline_token': bridge.timeline_token(r, self.path),
                       'audio_confirmed': True, 'shots': [{'id': 'shot01', 'image_prompt': '主体处于初始状态，清晰全景'}]}

    def run_patch(self, commit=True):
        return self.client.post(self.endpoint('patch-apply' if commit else 'patch-validate'), json=self.repair)

    def test_patch_dry_run_no_writes(self):
        raw = (self.path/'record.json').read_bytes()
        result = self.run_patch(False).json()
        self.assertTrue(result['ok'], result)
        self.assertTrue(result['impacts'][0]['image_needs_review'])
        self.assertTrue(result['impacts'][0]['video_needs_review'])
        self.assertEqual((self.path/'record.json').read_bytes(), raw)

    def test_patch_preserves_timeline_other_shots_assets_settings(self):
        result = self.run_patch().json()
        self.assertTrue(result['written'], result)
        after = self.load()
        for key in ('audio', 'scenes', 'settings', 'references'):
            self.assertEqual(after[key], self.before[key])
        self.assertEqual(after['shots'][1], self.before['shots'][1])
        a, b = after['shots'][0], self.before['shots'][0]
        for key in ('id', 'slide_ids', 'start', 'end', 'duration', 'kind', 'generation_options', 'image'):
            self.assertEqual(a[key], b[key], key)
        self.assertTrue(a['image_prompt_out_of_sync'])
        self.assertTrue(a['image_material_numbers_bound'])
        self.assertNotIn('video', a)
        self.assertEqual(a['video_history'][-1]['video'], b['video'])
        self.assertEqual(a['video_status'], 'pending')
        self.assertNotIn('export', after)
        self.assertEqual(after['codex_bridge']['export_history'][-1]['export'], self.before['export'])
        self.assertEqual(after['status'], 'image_review')
        self.assertEqual(after['codex_bridge']['evidence'], self.before['codex_bridge']['evidence'])
        self.assertFalse(result['generation_started'])
        for name in ('image.jpg', 'video.mp4', 'other.jpg'):
            self.assertEqual((self.path/'assets'/name).read_bytes(), b'paid-asset')
        backup = bridge.read_record(self.path/'codex_bridge_backups'/result['backup_id'])
        for key in self.before:
            if key != 'updated_at':
                self.assertEqual(backup[key], self.before[key], key)

    def test_video_only_does_not_invalidate_image(self):
        self.repair['shots'] = [{'id': 'shot01', 'video_prompt': '图1为主体参考，主体逐步变化，固定机位。'}]
        result = self.run_patch().json()
        self.assertTrue(result['ok'], result)
        self.assertFalse(result['impacts'][0]['image_needs_review'])
        self.assertEqual(self.load()['shots'][0]['image'], self.before['shots'][0]['image'])
        self.assertNotIn('image_prompt_out_of_sync', self.load()['shots'][0])
        self.assertEqual(self.load()['status'], 'video_review')

    def test_motion_update_compiles_new_video_prompt(self):
        motion = copy.deepcopy(self.before['shots'][0]['motion_plan'])
        motion['beats'][0]['action'] = '主体缓慢上升后停住'
        self.repair['shots'] = [{'id': 'shot01', 'motion_plan': motion}]
        result = self.run_patch().json()
        self.assertTrue(result['ok'], result)
        self.assertIn('主体缓慢上升后停住', self.load()['shots'][0]['video_prompt'])
        self.assertIn('主体缓慢上升后停住', self.load()['shots'][0]['action'])

    def test_receipt_idempotent_then_rejects_other_edits(self):
        result = self.run_patch().json()
        self.assertTrue(result['written'], result)
        raw = (self.path/'record.json').read_bytes()
        self.assertTrue(self.run_patch().json()['already_applied'])
        self.assertEqual((self.path/'record.json').read_bytes(), raw)
        r = self.load(); r['revision'] += 1; bridge.studio.save(self.path, r)
        self.assertEqual(self.run_patch().status_code, 409)

    def test_noop_no_revision_or_backup(self):
        self.repair['shots'][0]['image_prompt'] = self.before['shots'][0]['image_prompt']
        raw = (self.path/'record.json').read_bytes()
        result = self.run_patch().json()
        self.assertTrue(result['ok']); self.assertFalse(result['written'])
        self.assertEqual(result['changed'], [])
        self.assertEqual((self.path/'record.json').read_bytes(), raw)

    def test_frozen_fields_null_and_empty_patch_rejected(self):
        for field, value in [('kind', 'static'), ('slide_ids', []), ('start', 0), ('video', ''),
                             ('image_prompt', None), ('reference_ids', None)]:
            self.repair['shots'] = [{'id': 'shot01', field: value}]
            self.assertEqual(self.run_patch().status_code, 422, field)
        self.repair['shots'] = [{'id': 'shot01'}]
        self.assertEqual(self.run_patch().status_code, 422)

    def test_unknown_duplicate_and_atomic_batch(self):
        raw = (self.path/'record.json').read_bytes()
        for identity in ('missing', 'shot01'):
            self.repair['shots'] = [{'id': 'shot01', 'intent': 'new'}, {'id': identity, 'intent': 'new'}]
            self.assertFalse(self.run_patch().json()['ok'])
            self.assertEqual((self.path/'record.json').read_bytes(), raw)

    def test_reference_binding_requires_prompt_and_real_file(self):
        r = self.load(); r['references'] = [{'id': 'r5', 'label': '图5', 'file': 'assets/ref.jpg'}]
        bridge.studio.save(self.path, r)
        self.repair['shots'] = [{'id': 'shot01', 'reference_ids': ['r5']}]
        self.assertFalse(self.run_patch().json()['ok'])
        self.repair['shots'][0]['image_prompt'] = '主体处于初始状态，按图1形象'
        self.assertFalse(self.run_patch().json()['ok'])
        (self.path/'assets/ref.jpg').write_bytes(b'ref')
        result = self.run_patch().json()
        self.assertTrue(result['ok'], result)
        row = self.load()['shots'][0]
        self.assertIn('图1', bridge.studio._bind_material_numbers(self.load(), row, row['image_prompt']))

    def test_evidence_only_preserves_media_and_stage(self):
        self.repair['shots'] = [{'id': 'shot01', 'evidence': '第8页'}]
        result = self.run_patch().json()
        self.assertTrue(result['ok'], result)
        self.assertFalse(result['impacts'][0]['video_needs_review'])
        self.assertEqual(self.load()['export'], self.before['export'])
        self.assertEqual(self.load()['status'], 'completed')
        self.assertEqual(self.load()['shots'][0]['video'], self.before['shots'][0]['video'])

    def test_busy_timeline_and_ownership_guards(self):
        with patch.object(bridge.studio, 'ACTIVE', {str(self.path)}):
            self.assertEqual(self.run_patch().status_code, 409)
        with patch.object(bridge.studio, 'project_has_image_edits', return_value=True):
            self.assertEqual(self.run_patch().status_code, 409)
        self.repair['timeline_token'] = '0'*64
        self.assertEqual(self.run_patch().status_code, 409)
        self.user = {'id': 2}
        self.assertEqual(self.run_patch().status_code, 404)

    def test_patch_backup_failure_no_write(self):
        raw = (self.path/'record.json').read_bytes()
        with patch.object(bridge.studio, 'save', side_effect=OSError('full')):
            with self.assertRaises(OSError):
                self.run_patch()
        self.assertEqual((self.path/'record.json').read_bytes(), raw)

    def test_static_repair_keeps_kind_and_reference_only_clear_is_explicit(self):
        r = self.load(); s = r['shots'][0]
        s.update(kind='static', action='', motion_plan={}, video_prompt='')
        for key in ('video', 'video_version', 'video_status'):
            s.pop(key, None)
        bridge.studio.save(self.path, r)
        result = self.run_patch().json()
        self.assertTrue(result['ok'], result)
        self.assertFalse(result['impacts'][0]['video_needs_review'])
        self.assertEqual(self.load()['shots'][0]['kind'], 'static')
        self.assertEqual(self.load()['shots'][0]['image'], s['image'])

    def test_pending_paid_task_identity_is_not_discarded(self):
        r = self.load(); r['shots'][0].update(video_status='failed', video_task_id='paid', video_terminal=False)
        bridge.studio.save(self.path, r)
        self.assertEqual(self.run_patch().status_code, 409)
        self.assertEqual(self.load()['shots'][0]['video_task_id'], 'paid')

    def test_source_audio_edit_requires_sync(self):
        from backend.app.tts_editor import tts_editor
        r = self.load(); r['source_project'] = {'id': 'audio-job'}
        bridge.studio.save(self.path, r)
        with patch.object(tts_editor, 'status', return_value={'status': 'running'}):
            self.assertEqual(self.run_patch().status_code, 409)
        with patch.object(tts_editor, 'status', return_value={'status': 'done'}), \
             patch.object(tts_editor, '_project_dir', return_value=self.root/'missing'):
            self.assertEqual(self.run_patch().status_code, 409)

    def test_readback_failure_restores_record(self):
        real_read = bridge.read_record
        reads = 0

        def broken(path):
            nonlocal reads
            r = real_read(path)
            if path == self.path:
                reads += 1
                if reads == 2:
                    r['shots'] = []
            return r

        with patch.object(bridge, 'read_record', side_effect=broken):
            self.assertEqual(self.run_patch().status_code, 500)
        r = self.load()
        for key in self.before:
            if key != 'updated_at':
                self.assertEqual(r[key], self.before[key], key)

    def test_invalid_dynamic_text_contract_no_partial_write(self):
        self.repair['shots'] = [{'id': 'shot01', 'image_prompt': '空白房间'}]
        raw = (self.path/'record.json').read_bytes()
        self.assertFalse(self.run_patch().json()['ok'])
        self.assertEqual((self.path/'record.json').read_bytes(), raw)
