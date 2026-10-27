import copy
import io
import unittest
from unittest.mock import patch

from PIL import Image
from test_codex_bridge_patch import PatchTests
from backend.app import codex_bridge as bridge, codex_image_generation as images


class ShotEditTests(unittest.TestCase):
    endpoint = PatchTests.endpoint
    load = PatchTests.load
    dynamic = PatchTests.dynamic
    run_patch = PatchTests.run_patch

    def setUp(self):
        PatchTests.setUp(self)
        for name in ('image.jpg', 'other.jpg'):
            Image.new('RGB', (64, 36), 'white').save(self.path / 'assets' / name)
        self.before = self.load()

    def png(self, color='red'):
        out = io.BytesIO()
        Image.new('RGB', (128, 72), color).save(out, format='PNG')
        return out.getvalue()

    def upload(self, color='red'):
        response = self.client.post(self.endpoint('uploads'), files={'file': ('photo.png', self.png(color), 'image/png')})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_single_pack_is_scoped_and_read_only(self):
        before = (self.path / 'record.json').read_bytes()
        result = self.client.get(self.endpoint('shots/shot01')).json()
        self.assertEqual(result['shot']['id'], 'shot01')
        self.assertNotIn('shots', result)
        self.assertNotIn('untouched', str(result))
        self.assertNotIn('must-not-export', str(result))
        self.assertEqual(result['current_image']['width'], 64)
        self.assertEqual(result['current_image']['format'], 'JPEG')
        self.assertEqual(len(result['current_image']['sha256']), 64)
        self.assertEqual(self.client.get(result['current_image']['url']).status_code, 200)
        self.assertEqual(self.client.get(self.endpoint('shots/shot01/image?v=' + '0' * 64)).status_code, 409)
        self.assertEqual(result['subtitles'], self.before['scenes'])
        self.assertEqual((self.path / 'record.json').read_bytes(), before)
        self.assertEqual(self.client.get(self.endpoint('shots/missing')).status_code, 404)

    def test_upload_stable_scoped_no_revision_or_project_write(self):
        before = (self.path / 'record.json').read_bytes()
        one, two = self.upload(), self.upload()
        self.assertEqual(one['upload_id'], two['upload_id'])
        self.assertEqual(one['image']['width'], 128)
        self.assertEqual(one['image']['format'], 'PNG')
        self.assertFalse(one['project_modified'])
        self.assertEqual((self.path / 'record.json').read_bytes(), before)
        self.assertEqual(self.client.get(one['url']).content, self.png())
        self.user = {'id': 2}
        self.assertEqual(self.client.get(one['url']).status_code, 404)
        self.assertEqual(self.client.get(self.endpoint('shots/shot01')).status_code, 404)
        self.assertEqual(self.client.post(self.endpoint('uploads'), files={'file': ('x.png', self.png())}).status_code, 404)

    def test_bad_upload_and_fake_id_rejected(self):
        response = self.client.post(self.endpoint('uploads'), files={'file': ('x.png', b'not-image')})
        self.assertEqual(response.status_code, 400)
        self.repair['shots'] = [{'id': 'shot01', 'image_upload_id': 'img_' + 'f' * 64}]
        self.assertFalse(self.run_patch().json()['ok'])
        self.assertEqual(self.load(), self.before)

    def test_atomic_prompt_reference_motion_and_image_single_revision(self):
        new = self.upload(); ref = self.upload('blue')
        motion = copy.deepcopy(self.before['shots'][0]['motion_plan'])
        motion['beats'][0]['action'] = '主体缓慢上升后停住'
        self.repair['shots'][0].update(reference_ids=[ref['reference_id']], image_upload_id=new['upload_id'],
            motion_plan=motion, image_prompt='主体处于初始状态，图1为外观参考，清晰全景')
        raw = (self.path / 'record.json').read_bytes()
        preview = self.run_patch(False).json()
        self.assertTrue(preview['ok'], preview)
        self.assertEqual((self.path / 'record.json').read_bytes(), raw)
        result = self.run_patch().json()
        self.assertTrue(result['ok'], result)
        self.assertTrue(result['readback_verified'])
        after = self.load()
        self.assertEqual(after['revision'], self.before['revision'] + 1)
        self.assertEqual(after['shots'][1], self.before['shots'][1])
        shot = after['shots'][0]
        self.assertFalse(shot['image_prompt_out_of_sync'])
        self.assertEqual(shot['image_applied_prompt'], shot['image_prompt'])
        self.assertEqual(shot['image_version'], new['image']['sha256'])
        self.assertIn('主体缓慢上升后停住', shot['video_prompt'])
        self.assertEqual(shot['video_history'][-1]['video'], self.before['shots'][0]['video'])
        self.assertTrue((self.path / shot['image_history'][-1]['image']).is_file())
        self.assertEqual(shot['image_history'][-1]['prompt'], self.before['shots'][0]['image_prompt'])
        check = result['verification']
        for key in ('audio_unchanged', 'subtitles_unchanged', 'timing_unchanged', 'other_shots_unchanged'):
            self.assertTrue(check[key], key)
        self.assertEqual(check['shots'][0]['image']['sha256'], new['image']['sha256'])
        mapping = check['shots'][0]['reference_preview']['references']
        self.assertEqual(mapping[0]['id'], ref['reference_id'])
        self.assertEqual(mapping[0]['local_image_number'], 1)
        raw = (self.path / 'record.json').read_bytes()
        repeated = self.run_patch().json()
        self.assertTrue(repeated['already_applied'])
        self.assertEqual((self.path / 'record.json').read_bytes(), raw)

    def test_replacement_only_keeps_design_and_noop_is_safe(self):
        new = self.upload()
        self.repair['shots'] = [{'id': 'shot01', 'image_upload_id': new['upload_id']}]
        result = self.run_patch().json()
        self.assertTrue(result['ok'], result)
        after = self.load()
        for key in ('image_prompt', 'reference_ids', 'motion_plan', 'video_prompt'):
            self.assertEqual(after['shots'][0][key], self.before['shots'][0][key])
        self.repair['revision'] = after['revision']
        self.assertFalse(self.run_patch().json()['written'])
        self.assertEqual(self.load()['revision'], after['revision'])

    def test_bad_second_shot_does_not_commit_first_image(self):
        new = self.upload()
        self.repair['shots'] = [{'id': 'shot01', 'image_upload_id': new['upload_id']}, {'id': 'missing', 'intent': 'x'}]
        self.assertFalse(self.run_patch().json()['ok'])
        self.assertEqual(self.load(), self.before)

    def test_preview_does_not_require_generation_stage_provider_or_idle(self):
        for status in ('storyboard_review', 'image_review', 'completed', 'planning'):
            r = self.load(); r['status'] = status; bridge.studio.save(self.path, r)
            raw = (self.path / 'record.json').read_bytes()
            with patch.object(bridge.studio, '_image_configs', side_effect=AssertionError('No API lookup')), \
                 patch.object(bridge.studio, 'ACTIVE', {str(self.path)}), patch.object(images, 'pump') as pump:
                result = self.client.get(self.endpoint('shots/shot01/references-preview'))
                self.assertEqual(result.status_code, 200, result.text)
                self.assertTrue(result.json()['ok'], result.text)
                self.assertFalse(result.json()['may_charge'])
                pump.assert_not_called()
            self.assertEqual((self.path / 'record.json').read_bytes(), raw)

    def test_preview_maps_current_image_and_reference_order(self):
        r = self.load()
        r['redraw_references'] = [{'id': 'photo', 'file': 'assets/other.jpg'}]
        r['shots'][0].update(reference_ids=['photo'], image_material_numbers_bound=True,
            redraw_selection={'reference_ids': ['photo'], 'use_current_image': True}, image_prompt='图1为当前图，图2为外观')
        bridge.studio.save(self.path, r)
        result = self.client.get(self.endpoint('shots/shot01/references-preview')).json()
        self.assertEqual([m['kind'] for m in result['references']], ['current_image', 'reference'])
        self.assertEqual([m['local_image_number'] for m in result['references']], [1, 2])
        self.assertEqual(result['prompt_image_numbers'], [1, 2])
        result = self.client.get(self.endpoint('shots/shot01/references-preview?use_current_image=false')).json()
        self.assertFalse(result['ok'])
        self.assertEqual(result['error']['code'], 'REFERENCE_IDENTITY_MISSING')
        self.assertIn('current_image:shot01', result['error']['message'])

    def test_enabling_current_image_remaps_material_by_identity_once(self):
        r = self.load()
        r['redraw_references'] = [{'id': 'ref_04', 'file': 'assets/other.jpg'}]
        r['shots'][0].update(reference_ids=['ref_04'], image_material_numbers_bound=True,
                            image_prompt='图1为文献中的器械结构')
        bridge.studio.save(self.path, r)
        url = self.endpoint('shots/shot01/references-preview?use_current_image=true')
        first = self.client.get(url).json()
        self.assertTrue(first['ok'], first)
        self.assertIn('图2为文献', first['effective_prompt'])
        second = self.client.get(url).json()
        self.assertEqual(first['effective_prompt'], second['effective_prompt'])
        self.assertEqual(self.load()['shots'][0]['image_prompt'], '图1为文献中的器械结构')

    def test_patch_context_and_generation_preview_agree(self):
        r = self.load()
        r['redraw_references'] = [{'id': 'photo', 'file': 'assets/other.jpg'}]
        bridge.studio.save(self.path, r)
        self.repair['shots'][0].update(reference_ids=['photo'], image_prompt='图1保持主体与底图布局，图2为器械参考',
            image_reference_context={'use_current_image': True, 'use_scene_reference': False})
        result = self.run_patch().json()
        self.assertTrue(result['ok'], result)
        saved = self.load()['shots'][0]
        self.assertTrue(saved['redraw_selection']['use_current_image'])
        preview = self.client.get(self.endpoint('shots/shot01/references-preview')).json()
        self.assertTrue(preview['ok'], preview)
        self.assertEqual([r['kind'] for r in preview['references']], ['current_image', 'reference'])
        self.assertEqual(preview['effective_prompt'], saved['image_prompt'])

    def test_replacement_readback_failure_restores_old_record_and_image(self):
        new = self.upload()
        self.repair['shots'] = [{'id': 'shot01', 'image_upload_id': new['upload_id']}]
        old_bytes = (self.path / 'assets/image.jpg').read_bytes()
        real_read = bridge.read_record
        count = 0
        def broken(path):
            nonlocal count
            record = real_read(path)
            if path == self.path:
                count += 1
                if count == 2:
                    record['shots'] = []
            return record
        with patch.object(bridge, 'read_record', side_effect=broken):
            self.assertEqual(self.run_patch().status_code, 500)
        self.assertEqual(self.load()['shots'], self.before['shots'])
        self.assertEqual((self.path / 'assets/image.jpg').read_bytes(), old_bytes)

    def test_tampered_upload_not_applied(self):
        new = self.upload()
        target = next((self.path / 'assets/bridge_uploads').glob('*.png'))
        target.write_bytes(self.png('blue'))
        self.repair['shots'] = [{'id': 'shot01', 'image_upload_id': new['upload_id']}]
        self.assertFalse(self.run_patch().json()['ok'])
        self.assertEqual(self.load(), self.before)

    def test_missing_old_image_can_be_repaired_without_fake_history(self):
        r = self.load(); r['shots'][0].update(image='assets/not-yet-generated.png', image_status='pending')
        bridge.studio.save(self.path, r)
        new = self.upload()
        self.repair['shots'] = [{'id': 'shot01', 'image_upload_id': new['upload_id']}]
        result = self.run_patch().json()
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.load()['shots'][0]['image_status'], 'completed')
        self.assertFalse(self.load()['shots'][0].get('image_history'))

    def test_write_failure_rolls_back_and_preserves_original_file(self):
        new = self.upload()
        self.repair['shots'] = [{'id': 'shot01', 'image_upload_id': new['upload_id']}]
        save = bridge.studio.save
        def fail_once(path, record):
            if path == self.path and record['revision'] == self.before['revision'] + 1:
                raise OSError('simulated write failure')
            return save(path, record)
        with patch.object(bridge.studio, 'save', side_effect=fail_once):
            self.assertEqual(self.run_patch().status_code, 500)
        self.assertEqual(self.load()['shots'], self.before['shots'])
        with Image.open(self.path / self.before['shots'][0]['image']) as image:
            self.assertEqual(image.size, (64, 36))

    def test_generation_still_requires_image_review_and_confirmation(self):
        r = self.load(); r['status'] = 'storyboard_review'; bridge.studio.save(self.path, r)
        payload = {'revision': r['revision'], 'timeline_token': self.repair['timeline_token'],
                   'audio_confirmed': True, 'shot_id': 'shot01', 'request_id': 'no-charge-01'}
        with patch.object(images, 'pump') as pump:
            result = self.client.post(self.endpoint('image-apply'), json=payload)
            self.assertEqual(result.status_code, 409)
            pump.assert_not_called()

    def test_replacement_is_separate_from_staged_reference_even_after_native_overwrite(self):
        new = self.upload()
        self.repair['shots'][0].update(reference_ids=[new['reference_id']], image_upload_id=new['upload_id'],
                                     image_prompt='主体处于初始状态，图1为主体参考')
        result = self.run_patch().json()
        self.assertTrue(result['ok'], result)
        after = self.load()
        image = self.path / after['shots'][0]['image']
        reference = self.path / after['redraw_references'][0]['file']
        self.assertNotEqual(image, reference)
        image.write_bytes(self.png('blue'))  # Simulate the native editor overwriting current image.
        self.assertEqual(reference.read_bytes(), self.png())
        self.assertEqual(self.run_patch().status_code, 409)  # Old receipt no longer matches current image.
        self.repair['revision'] = after['revision']
        result = self.run_patch().json()
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['verification']['shots'][0]['image']['sha256'], new['image']['sha256'])
