import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from backend.app import codex_bridge as bridge


class CodexBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.user = {'id': 1}
        for target, attribute, value in [(bridge, 'ROOT', self.root/'bridge'),
                (bridge.studio, 'ROOT', self.root/'video'), (bridge, 'enabled', lambda: True),
                (bridge, 'require_user', lambda request: self.user)]:
            p = patch.object(target, attribute, value); p.start(); self.addCleanup(p.stop)
        app = FastAPI(); app.include_router(bridge.router)
        self.client = TestClient(app)
        self.id = 'a'*32
        self.path = bridge.studio.directory(1, self.id)
        self.record = {'id': self.id, 'revision': 1, 'status': 'draft', 'settings': {'name': 'Test'},
            'scenes': [{'slide_id': 'scene_001', 'start': 0., 'end': 4., 'text': '第一句'},
                       {'slide_id': 'scene_002', 'start': 4., 'end': 9., 'text': '第二句'}],
            'audio': 'assets/audio.wav', 'references': [], 'shots': [], 'logs': [],
            'creation_parameters': {'api_key': 'must-not-export', 'dynamic_auto_advance': True}}
        bridge.studio.save(self.path, self.record)
        (self.path/'assets').mkdir()
        (self.path/'assets/audio.wav').write_bytes(b'fixture')
        self.payload = {'revision': 1, 'timeline_token': bridge.timeline_token(self.record, self.path),
            'audio_confirmed': True, 'shots': [{'id': 'shot01', 'slide_ids': ['scene_001', 'scene_002'],
                'kind': 'static', 'intent': '完整叙事', 'image_prompt': '简明画面', 'evidence': '原文第2页'}]}

    def endpoint(self, verb):
        return f'/api/codex-bridge/projects/{self.id}/{verb}'

    def load(self):
        return bridge.read_record(self.path)

    def test_pack_excludes_secrets_and_histories(self):
        r = self.client.get(self.endpoint('pack'))
        self.assertEqual(r.status_code, 200)
        self.assertNotIn('must-not-export', r.text)
        self.assertEqual(len(r.json()['subtitles']), 2)
        self.assertNotIn('creation_parameters', r.json())

    def test_bundled_guide_is_generic_and_discoverable(self):
        info = self.client.get('/api/codex-bridge/info').json()
        guide = self.client.get(info['guide_url'])
        self.assertEqual(guide.status_code, 200)
        self.assertEqual(guide.json()['skill_path'], info['skill_path'])
        self.assertEqual(guide.json()['content'], Path(info['skill_path']).read_text(encoding='utf-8-sig'))
        self.assertNotIn('$ocv-medical-production', guide.text)
        self.assertIn('ocv-production-bridge', guide.json()['content'])
        with patch.object(bridge, 'enabled', return_value=False):
            self.assertEqual(self.client.get(info['guide_url']).status_code, 404)
        with patch.object(bridge, 'require_user', side_effect=HTTPException(401, 'login')):
            self.assertEqual(self.client.get(info['guide_url']).status_code, 401)
        with patch.object(bridge, 'PROJECT_ROOT', self.root):
            self.assertEqual(self.client.get(info['guide_url']).status_code, 404)

    def test_dry_run_has_no_writes(self):
        before = (self.path/'record.json').read_bytes()
        result = self.client.post(self.endpoint('validate'), json=self.payload).json()
        self.assertTrue(result['ok']); self.assertFalse(result['written'])
        self.assertEqual((self.path/'record.json').read_bytes(), before)
        self.assertFalse((self.path/'codex_bridge_backups').exists())

    def test_apply_backup_readback_idempotency_no_generation(self):
        result = self.client.post(self.endpoint('apply'), json=self.payload).json()
        self.assertTrue(result['written']); self.assertFalse(result['generation_started'])
        saved = self.load()
        self.assertEqual(saved['scenes'], self.record['scenes'])
        self.assertEqual(saved['audio'], self.record['audio'])
        self.assertEqual(saved['shots'][0]['end'], 9.)
        self.assertFalse(saved['creation_parameters']['dynamic_auto_advance'])
        self.assertEqual(saved['codex_bridge']['evidence']['shot01'], '原文第2页')
        backup = bridge.read_record(self.path/'codex_bridge_backups'/result['backup_id'])
        self.assertEqual(backup['shots'], [])
        retry = self.client.post(self.endpoint('apply'), json=self.payload).json()
        self.assertTrue(retry['already_applied']); self.assertEqual(self.load()['revision'], 2)
        validation = self.client.post(self.endpoint('validate'), json=self.payload).json()
        self.assertTrue(validation['already_applied'])

    def test_stale_revision_and_busy_rejected(self):
        for status, revision in [('planning', 1), ('draft', 2), ('video_generating', 1)]:
            self.record.update(status=status, revision=revision); bridge.studio.save(self.path, self.record)
            self.assertEqual(self.client.post(self.endpoint('apply'), json=self.payload).status_code, 409)

    def test_timeline_change_rejected(self):
        self.record['scenes'][0]['text'] = '新字幕'; bridge.studio.save(self.path, self.record)
        self.assertEqual(self.client.post(self.endpoint('apply'), json=self.payload).status_code, 409)

    def test_existing_assets_not_overwritten(self):
        self.record['shots'] = [{'id': 'paid', 'image': 'paid.jpg'}]
        bridge.studio.save(self.path, self.record)
        self.assertEqual(self.client.post(self.endpoint('apply'), json=self.payload).status_code, 409)
        self.assertEqual(self.load()['shots'][0]['image'], 'paid.jpg')

    def test_missing_subtitle_rejected_without_write(self):
        self.payload['shots'][0]['slide_ids'] = ['scene_001']
        result = self.client.post(self.endpoint('apply'), json=self.payload).json()
        self.assertFalse(result['ok']); self.assertEqual(self.load()['revision'], 1)

    def test_time_gap_at_cut_rejected_but_gap_inside_shot_allowed(self):
        self.record['scenes'][1]['start'] = 4.247; bridge.studio.save(self.path, self.record)
        self.payload['timeline_token'] = bridge.timeline_token(self.record, self.path)
        self.assertTrue(self.client.post(self.endpoint('validate'), json=self.payload).json()['ok'])
        second = copy.deepcopy(self.payload['shots'][0]); second.update(id='shot02', slide_ids=['scene_002'])
        self.payload['shots'][0]['slide_ids'] = ['scene_001']; self.payload['shots'].append(second)
        self.assertFalse(self.client.post(self.endpoint('validate'), json=self.payload).json()['ok'])

    def dynamic(self):
        row = self.payload['shots'][0]
        row.update(kind='video', image_prompt='主体处于初始状态', motion_plan={
            'version': 2, 'scene_anchor': '主体的固定视野', 'participants': ['主体'],
            'reference_participants': ['主体'], 'reference_texts': [], 'reference_beat': 1,
            'reference_visual': '主体处于初始状态', 'beats': [{'action': '主体逐步变化', 'texts': []}]})
        return row

    def test_motion_compiles_without_llm(self):
        self.dynamic()
        r = self.client.post(self.endpoint('apply'), json=self.payload).json()
        self.assertTrue(r['ok'], r)
        row = self.load()['shots'][0]
        self.assertIn('图1', row['video_prompt']); self.assertIn('主体逐步变化', row['action'])

    def test_overlong_video_is_error_not_silent_static(self):
        self.dynamic(); self.record['scenes'][1]['end'] = 18.
        bridge.studio.save(self.path, self.record)
        self.payload['timeline_token'] = bridge.timeline_token(self.record, self.path)
        result = self.client.post(self.endpoint('apply'), json=self.payload).json()
        self.assertFalse(result['ok']); self.assertIn('超过15秒', result['errors'][0])

    def test_unknown_fields_rejected(self):
        self.payload['shots'][0]['start'] = 2
        self.assertEqual(self.client.post(self.endpoint('apply'), json=self.payload).status_code, 422)

    def test_no_audio_cannot_import(self):
        (self.path/'assets/audio.wav').unlink()
        result = self.client.post(self.endpoint('apply'), json=self.payload).json()
        self.assertFalse(result['ok']); self.assertIn('真实配音', result['errors'][0])

    def test_no_false_confirmation(self):
        self.payload['audio_confirmed'] = False
        self.assertEqual(self.client.post(self.endpoint('apply'), json=self.payload).status_code, 422)

    def test_invalid_reference_and_text_contract(self):
        self.payload['shots'][0]['reference_ids'] = ['other_user_ref']
        self.assertFalse(self.client.post(self.endpoint('apply'), json=self.payload).json()['ok'])
        self.payload['shots'][0]['reference_ids'] = []
        row = self.dynamic(); row['image_prompt'] = '空白房间'
        self.assertFalse(self.client.post(self.endpoint('apply'), json=self.payload).json()['ok'])

    def test_retry_after_other_edit_does_not_revert(self):
        self.assertTrue(self.client.post(self.endpoint('apply'), json=self.payload).json()['ok'])
        record = self.load(); record['revision'] += 1; record['shots'][0]['image_prompt'] = '用户新修改'
        bridge.studio.save(self.path, record)
        self.assertEqual(self.client.post(self.endpoint('apply'), json=self.payload).status_code, 409)
        self.assertEqual(self.load()['shots'][0]['image_prompt'], '用户新修改')

    def test_backup_failure_leaves_project_untouched(self):
        before = (self.path/'record.json').read_bytes()
        with patch.object(bridge.studio, 'save', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.client.post(self.endpoint('apply'), json=self.payload)
        self.assertEqual((self.path/'record.json').read_bytes(), before)

    def test_other_user_cannot_read_project(self):
        self.user = {'id': 2}
        self.assertEqual(self.client.get(self.endpoint('pack')).status_code, 404)

    def test_disabled_and_unauthenticated(self):
        with patch.object(bridge, 'enabled', return_value=False):
            self.assertEqual(self.client.get('/api/codex-bridge/info').status_code, 404)
        with patch.object(bridge, 'require_user', side_effect=HTTPException(401, 'login')):
            self.assertEqual(self.client.get('/api/codex-bridge/info').status_code, 401)

    def test_draft_only_saves_inputs_and_rejects_credentials(self):
        from backend.app import main
        payload = {'id': 'b'*32, 'parameters': {'project_name': '新项目', 'script': '完整文案'}}
        with patch.object(main, 'list_uploads', return_value=[]):
            result = self.client.put('/api/codex-bridge/drafts', json=payload)
            self.assertEqual(result.status_code, 200, result.text)
            value = result.json(); self.assertFalse(value['generation_started'])
            self.assertTrue(value['parameters']['step_mode'])
            self.assertFalse(value['parameters']['dynamic_auto_advance'])
            self.assertEqual(self.client.put('/api/codex-bridge/drafts', json=payload).json()['revision'], 1)
            payload['parameters']['script'] = '新文案'
            self.assertEqual(self.client.put('/api/codex-bridge/drafts', json=payload).status_code, 409)
            payload['parameters']['api_key'] = 'no'
            self.assertEqual(self.client.put('/api/codex-bridge/drafts', json=payload).status_code, 422)

    def test_draft_references_must_belong_to_user_and_match_kind(self):
        from backend.app import main
        payload = {'id': 'b'*32, 'parameters': {'project_name': '新项目', 'script': '文案', 'reference_image_ids': ['missing']}}
        with patch.object(main, 'list_uploads', return_value=[]):
            self.assertEqual(self.client.put('/api/codex-bridge/drafts', json=payload).status_code, 422)

    def test_from_audio_requires_confirmation_and_reuses_existing(self):
        self.assertEqual(self.client.post('/api/codex-bridge/from-audio', json={'job_id': 'audio'}).status_code, 422)
        with patch.object(bridge.studio, 'find_audio_storyboard', return_value=self.record), \
             patch.object(bridge.studio, 'sync_edited_audio', return_value=self.record) as sync, \
             patch.object(bridge.studio, 'import_new_project') as create:
            result = self.client.post('/api/codex-bridge/from-audio', json={'job_id': 'audio', 'audio_confirmed': True})
            self.assertEqual(result.status_code, 200); sync.assert_called_once(); create.assert_not_called()


if __name__ == '__main__':
    unittest.main()
