import copy
import unittest
from unittest.mock import patch
from test_codex_bridge_scenes import SceneBridgeTests
from backend.app import video_studio as studio
from backend.app.video_scene_readiness import scene_readiness


class SceneConfirmationTests(unittest.TestCase):
    def setUp(self):
        SceneBridgeTests.setUp(self)
        self.record['creation_parameters'].update(dynamic_text_mode='visual_first', director_strategy='stable')
        for key in ('scene_assets', 'scene_reference_plan', 'scene_references_status'):
            self.record.pop(key, None)
        for shot in self.record['shots']:
            shot.pop('scene_reference_id', None)
        studio.save(self.path, self.record)

    def confirm(self, revision=1):
        return self.client.post(f'/api/video-studio/{self.id}/images/confirm', json={'revision': revision})

    def load(self):
        from backend.app.codex_bridge import read_record
        return read_record(self.path)

    def test_empty_bridge_scene_state_can_confirm_without_generation(self):
        before = copy.deepcopy(self.record)
        result = self.client.get(f'/api/video-studio/{self.id}')
        self.assertTrue(result.json()['scene_reference_readiness']['can_confirm'])
        with patch.object(studio, '_start_storyboard_images') as worker:
            response = self.confirm()
        self.assertEqual(response.status_code, 200, response.text)
        worker.assert_not_called()
        after = self.load()
        for field in ('shots', 'audio', 'scenes', 'creation_parameters'):
            self.assertEqual(before[field], after[field])
        self.assertEqual(after['status'], 'video_review')
        self.assertNotIn('scene_references_status', after)
        self.assertNotIn('scene_reference_readiness', after)

    def test_binding_without_asset_blocks_and_names_shot(self):
        self.record['shots'][0]['scene_reference_id'] = 'missing-scene'
        studio.save(self.path, self.record)
        readiness = scene_readiness(self.record, self.path)
        self.assertFalse(readiness['can_confirm'])
        self.assertEqual(readiness['blockers'][0]['shot_ids'], ['one'])
        self.assertEqual(self.confirm().status_code, 409)

    def test_pending_plan_not_mistaken_for_no_demand(self):
        self.record['scene_reference_plan'] = {'scenes': [{'scene_id': 'room', 'name': '室内', 'members': [0, 1]}]}
        studio.save(self.path, self.record)
        self.assertEqual(scene_readiness(self.record, self.path)['blockers'][0]['code'], 'SCENE_PLAN_PENDING')
        self.assertEqual(self.confirm().status_code, 409)

    def test_failed_and_missing_file_still_block(self):
        self.record['shots'][0]['scene_reference_id'] = 'room'
        self.record['scene_assets'] = [{'id': 'room', 'image_status': 'failed', 'image': 'assets/old.jpg'}]
        studio.save(self.path, self.record)
        self.assertEqual(self.confirm().status_code, 409)
        self.record['scene_assets'][0].update(image_status='completed', image='assets/missing.jpg')
        studio.save(self.path, self.record)
        self.assertEqual(scene_readiness(self.record, self.path)['blockers'][0]['code'], 'SCENE_FILE_MISSING')
        self.assertEqual(self.confirm().status_code, 409)

    def test_ready_scene_does_not_require_redundant_global_status(self):
        self.record['shots'][0]['scene_reference_id'] = 'room'
        self.record['scene_assets'] = [{'id': 'room', 'image_status': 'completed', 'image': 'assets/old.jpg'}]
        studio.save(self.path, self.record)
        self.assertEqual(self.confirm().status_code, 200)

    def test_disabled_scene_and_unbound_plan_do_not_block(self):
        self.record['shots'][0].update(scene_reference_id='room', scene_reference_disabled=True)
        self.record['scene_reference_plan'] = {'scenes': [{'scene_id': 'room', 'members': [0]}]}
        self.record['scene_assets'] = [{'id': 'room', 'disabled': True, 'image_status': 'failed'}]
        self.record['scene_references_status'] = 'failed'
        studio.save(self.path, self.record)
        self.assertEqual(self.confirm().status_code, 200)

    def test_running_scene_and_image_and_stale_revision_still_block(self):
        self.record['scene_references_status'] = 'generating'; studio.save(self.path, self.record)
        self.assertEqual(self.confirm().status_code, 409)
        self.record.pop('scene_references_status'); self.record['shots'][0]['image_status'] = 'pending'
        studio.save(self.path, self.record)
        self.assertEqual(self.confirm().status_code, 409)
        self.record['shots'][0]['image_status'] = 'completed'; studio.save(self.path, self.record)
        self.assertEqual(self.confirm(revision=99).status_code, 409)
        studio.IMAGE_EDITS.add(studio.image_edit_key(self.id, 'one'))
        try: self.assertEqual(self.confirm().status_code, 409)
        finally: studio.IMAGE_EDITS.clear()

    def test_other_users_cannot_confirm(self):
        with patch.object(studio, 'require_user', return_value={'id': 2}):
            self.assertEqual(self.confirm().status_code, 404)
