"""Offline contracts for explicitly invoked, immutable Vibe agent materials."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from backend.app.vibe_agent_materials import MaterialWorkspace
from backend.app.vibe_materials import MaterialError


def inventory():
    return dict(background='extract', background_color='#102038', background_notes='保留光带',
                foreground_summary=dict(has_nontext_subjects=True, subject_count=2, description='图标和照片'),
                subjects=[dict(name='普通图标', source='core', source_crop=[.1, .2, .4, .8], treatment='cutout'),
                          dict(name='研究照片', source='ref_1', source_crop=[0, 0, 1, 1],
                               treatment='preserve', role='photo')],
                texts=[dict(text='研究结果', source_box=[.2, .02, .8, .15])])


class MaterialWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        self.folder = self.project / 'jobs' / 'job_1'
        self.core = self.project / 'core.png'
        self.ref = self.project / 'ref.png'
        Image.new('RGB', (200, 100), '#123456').save(self.core)
        Image.new('RGB', (80, 120), '#998877').save(self.ref)
        self.sources = dict(core=self.core, ref_1=self.ref)
        self.workspace = MaterialWorkspace(self.project, self.folder, self.sources, (640, 360))
        self.raw = inventory()

    @staticmethod
    def fake_cutout(sources, output_dir, **kwargs):
        output_dir.mkdir(parents=True)
        result = {}
        for key, source in sources.items():
            with Image.open(source) as image:
                rgba = image.convert('RGBA')
                rgba.putpixel((0, 0), (0, 0, 0, 0))
                path = output_dir / (key + '.png')
                rgba.save(path)
                result[key] = path
        return result

    def test_inventory_only_plans_and_never_runs_tools(self):
        before = copy.deepcopy(self.raw)
        with patch('backend.app.vibe_agent_materials.rmbg.run_cutouts') as run:
            design = self.workspace.set_inventory(self.raw)
        run.assert_not_called()
        self.assertEqual(self.raw, before)
        self.assertEqual(self.workspace.assets(), {})
        self.assertEqual(design.subjects[0].crop, (.1, .2, .4, .8))
        self.assertFalse(self.folder.exists())
        self.assertEqual(self.workspace.epoch, 1)
        self.workspace.set_inventory(self.raw)
        self.assertEqual(self.workspace.epoch, 1)

    def test_preserve_crops_original_pixels_without_rmbg(self):
        self.workspace.set_inventory(self.raw)
        old_core = self.core.read_bytes()
        with patch('backend.app.vibe_agent_materials.rmbg.run_cutouts') as run:
            result = self.workspace.prepare_subject('subject_1')
        run.assert_not_called()
        self.assertFalse(result['receipt']['rmbg_used'])
        self.assertEqual(result['receipt']['render_asset_id'], 'subject_1')
        self.assertEqual(result['receipt']['original_render_asset_id'], 'subject_1_original')
        self.assertEqual((result['receipt']['width'], result['receipt']['height']), (80, 120))
        with Image.open(result['images']['subject_1']) as prepared, Image.open(self.ref) as original:
            self.assertEqual(prepared.convert('RGB').tobytes(), original.tobytes())
        self.assertEqual(self.core.read_bytes(), old_core)
        self.assertEqual(self.workspace.assets()['subject_1'], self.workspace.assets()['subject_1_original'])

    def test_cutout_explicit_and_geometry_overrides_committed_together(self):
        self.workspace.set_inventory(self.raw)
        old_fingerprint = self.workspace.fingerprint()
        with patch('backend.app.vibe_agent_materials.rmbg.run_cutouts', side_effect=self.fake_cutout) as run:
            result = self.workspace.prepare_subject('subject_0', source_crop=[.05, .1, .45, .9])
        run.assert_called_once()
        self.assertIsNone(run.call_args.kwargs['trim_padding'])
        self.assertTrue(result['receipt']['rmbg_used'])
        self.assertEqual(self.workspace.inventory.subjects[0].source_crop, (.05, .1, .45, .9))
        self.assertEqual(self.workspace.design.subjects[0].crop, (.05, .1, .45, .9))
        self.assertEqual(self.workspace.design.subjects[0].box, (.05, .1, .45, .9))
        self.assertNotEqual(self.workspace.fingerprint(), old_fingerprint)
        self.assertNotEqual(result['images']['subject_0'], result['images']['subject_0_original'])
        self.assertEqual(self.raw['subjects'][0]['source_crop'], [.1, .2, .4, .8])

    def test_failure_does_not_publish_geometry_or_aliases(self):
        self.workspace.set_inventory(self.raw)
        self.workspace.prepare_subject('subject_0', treatment='preserve')
        before = self.workspace.snapshot()
        with patch('backend.app.vibe_agent_materials.rmbg.run_cutouts', side_effect=RuntimeError('offline')):
            with self.assertRaisesRegex(RuntimeError, 'offline'):
                self.workspace.prepare_subject('subject_0', source_crop=[.1, .1, .5, .9], treatment='cutout')
        self.assertEqual(self.workspace.snapshot(), before)

    def test_invalid_cutout_cannot_replace_current_asset(self):
        self.workspace.set_inventory(self.raw)

        def wrong_size(sources, output_dir, **kwargs):
            output_dir.mkdir(parents=True)
            path = output_dir / 'bad.png'
            Image.new('RGBA', (1, 1)).save(path)
            return {'subject_0': path}

        with patch('backend.app.vibe_agent_materials.rmbg.run_cutouts', side_effect=wrong_size):
            with self.assertRaisesRegex(MaterialError, '尺寸'):
                self.workspace.prepare_subject('subject_0')
        self.assertEqual(self.workspace.assets(), {})

    def test_photo_and_evidence_never_cutout(self):
        for role in ('photo', 'evidence'):
            raw = copy.deepcopy(self.raw)
            raw['subjects'][1]['role'] = role
            self.workspace.set_inventory(raw)
            with patch('backend.app.vibe_agent_materials.rmbg.run_cutouts') as run:
                with self.assertRaisesRegex(ValueError, 'preserve'):
                    self.workspace.prepare_subject('subject_1', treatment='cutout')
            run.assert_not_called()

    def test_reprepare_keeps_immutable_old_versions(self):
        self.workspace.set_inventory(self.raw)
        first = self.workspace.prepare_subject('subject_0', treatment='preserve')
        old_path = first['images']['subject_0']
        old_data = old_path.read_bytes()
        old_id = first['receipt']['asset_id']
        second = self.workspace.prepare_subject('subject_0', source_crop=[.2, .2, .6, .8])
        self.assertNotEqual(first['receipt']['asset_id'], second['receipt']['asset_id'])
        self.assertEqual(old_path.read_bytes(), old_data)
        self.assertEqual(self.workspace.registry[old_id], old_path)
        self.assertEqual(self.workspace.inspect_image(old_id)['images'][old_id], old_path)

    def test_inventory_metadata_keeps_pixels_but_material_changes_invalidate(self):
        self.workspace.set_inventory(self.raw)
        self.workspace.prepare_subject('subject_1')
        original_path = self.workspace.assets()['subject_1']
        revised = copy.deepcopy(self.raw)
        revised['texts'][0]['text'] = '新研究标题'
        self.workspace.set_inventory(revised)
        self.assertEqual(self.workspace.assets()['subject_1'], original_path)
        revised['subjects'][1]['name'] = '另一照片'
        revised['subjects'][1]['evidence_note'] = '修改说明，不改变原始照片'
        self.workspace.set_inventory(revised)
        self.assertEqual(self.workspace.assets()['subject_1'], original_path)
        self.assertEqual(self.workspace.inventory_delta['retained'], ['subject_1'])
        revised['subjects'][1]['source_crop'] = [.1, .1, .9, .9]
        self.workspace.set_inventory(revised)
        self.assertNotIn('subject_1', self.workspace.assets())
        self.assertEqual(self.workspace.inventory_delta['invalidated'], ['subject_1'])
        self.assertIn('source_crop', self.workspace.inventory_delta['reasons']['subject_1'])
        self.assertTrue(original_path.exists())

    def test_return_to_prior_crop_reuses_exact_version_without_rmbg(self):
        self.workspace.set_inventory(self.raw)
        with patch('backend.app.vibe_agent_materials.rmbg.run_cutouts', side_effect=self.fake_cutout) as run:
            first = self.workspace.prepare_subject('subject_0')
            self.workspace.prepare_subject('subject_0', source_crop=[.15, .2, .5, .8])
            self.assertEqual(run.call_count, 2)
            revised = copy.deepcopy(self.raw)
            revised['subjects'][0]['name'] = '图标新名称'
            self.workspace.set_inventory(revised)
            self.assertEqual(self.workspace.assets()['subject_0'], first['images']['subject_0'])
            self.assertEqual(self.workspace.inventory_delta['reused'], ['subject_0'])
            second = self.workspace.prepare_subject('subject_0')
            self.assertTrue(second['receipt']['reused'])
            self.assertFalse(second['receipt']['rmbg_used'])
            self.assertEqual(run.call_count, 2)

    def test_history_survives_snapshot_and_reordered_subject_slots(self):
        self.workspace.set_inventory(self.raw)
        ready = self.workspace.prepare_subject('subject_1')
        revised = copy.deepcopy(self.raw)
        revised['subjects'][1]['source_crop'] = [.1, .1, .9, .9]
        self.workspace.set_inventory(revised)
        restored = MaterialWorkspace(self.project, self.folder, self.sources, (640, 360))
        restored.restore(self.workspace.snapshot())
        reorder = copy.deepcopy(self.raw)
        reorder['subjects'].reverse()
        restored.set_inventory(reorder)
        self.assertEqual(restored.assets()['subject_0'], ready['images']['subject_1'])
        self.assertEqual(restored.material_status()['missing'], ['subject_1'])
        self.assertEqual(restored.inventory_delta['reused'], ['subject_0'])

    def test_metadata_reuse_still_rejects_mutated_source_or_derived_file(self):
        self.workspace.set_inventory(self.raw)
        ready = self.workspace.prepare_subject('subject_1')
        revised = copy.deepcopy(self.raw)
        revised['subjects'][1]['name'] = '照片改名'
        Image.new('RGB', (80, 120), 'yellow').save(ready['images']['subject_1'])
        with self.assertRaisesRegex(MaterialError, '改变'):
            self.workspace.set_inventory(revised)
        Image.new('RGB', (80, 120), 'blue').save(self.ref)
        with self.assertRaisesRegex(MaterialError, '改变'):
            self.workspace.set_inventory(revised)

    def test_metadata_does_not_bypass_photo_policy_or_preview_fingerprint(self):
        self.workspace.set_inventory(self.raw)
        self.workspace.prepare_subject('subject_0', treatment='preserve')
        before = self.workspace.fingerprint()
        revised = self.workspace.inventory.model_dump(mode='json')
        revised['subjects'][0]['embedded_text'] = ['原图文字']
        revised['subjects'][0]['role'] = 'photo'
        self.workspace.set_inventory(revised)
        self.assertIn('subject_0', self.workspace.assets())
        self.assertNotEqual(before, self.workspace.fingerprint())
        with patch('backend.app.vibe_agent_materials.rmbg.run_cutouts') as run:
            with self.assertRaisesRegex(ValueError, 'preserve'):
                self.workspace.prepare_subject('subject_0', treatment='cutout')
            run.assert_not_called()

    def legacy_snapshot(self):
        raw = self.workspace.snapshot()
        raw['version'] = 'ocv-agent-materials-v1'
        raw.pop('subject_history', None)
        raw.pop('inventory_delta', None)
        for key, item in raw['prepared'].items():
            item['spec'] = copy.deepcopy(raw['inventory']['subjects'][int(key.split('_')[1])])
        return raw

    def test_legacy_snapshot_restores_current_material_and_upgrades_history(self):
        self.workspace.set_inventory(self.raw)
        ready = self.workspace.prepare_subject('subject_1')
        raw = self.legacy_snapshot()
        restored = MaterialWorkspace(self.project, self.folder, self.sources, (640, 360))
        restored.restore(raw)
        revised = copy.deepcopy(self.raw)
        revised['subjects'][1]['name'] = '研究照片重命名'
        restored.set_inventory(revised)
        self.assertEqual(restored.assets()['subject_1'], ready['images']['subject_1'])
        self.assertEqual(restored.snapshot()['version'], 'ocv-agent-materials-v2')

    def test_import_old_inactive_material_from_success_receipt_without_generation(self):
        self.workspace.set_inventory(self.raw)
        with patch('backend.app.vibe_agent_materials.rmbg.run_cutouts', side_effect=self.fake_cutout):
            ready = self.workspace.prepare_subject('subject_0')
        self.workspace.select_background(color='#102030')
        revised = copy.deepcopy(self.raw)
        revised['subjects'][0]['source_crop'] = [.2, .2, .5, .9]
        self.workspace.set_inventory(revised)
        old = self.legacy_snapshot()
        self.assertEqual(old['prepared'], {})
        new = MaterialWorkspace(self.project, self.project / 'jobs' / 'job_2', self.sources, (640, 360))
        with patch('backend.app.vibe_agent_materials.rmbg.run_cutouts') as run:
            result = new.import_materials(old, self.folder, history_receipts=[
                {'tool': 'inspect_image', 'receipt': {}},
                {'tool': 'prepare_subject', 'receipt': ready['receipt']},
                {'tool': 'prepare_subject', 'receipt': {'ok': False, 'error': 'failed'}}])
            new.set_inventory(self.raw)
            replay = new.prepare_subject('subject_0')
        run.assert_not_called()
        self.assertEqual(result['imported_subject_versions'], 1)
        self.assertEqual(result['imported_background_versions'], 1)
        self.assertEqual(len(result['rejected_receipts']), 1)
        self.assertTrue(replay['receipt']['reused'])
        self.assertTrue(new.assets()['subject_0'].is_relative_to(new.folder))
        self.assertNotEqual(new.assets()['subject_0'], ready['images']['subject_0'])
        self.assertEqual(new.assets()['subject_0'].read_bytes(), ready['images']['subject_0'].read_bytes())
        self.assertEqual(self.legacy_snapshot(), old)
        new.select_background(asset_id=result['previous_background_asset_id'])
        restored = MaterialWorkspace(self.project, new.folder, self.sources, (640, 360))
        restored.restore(new.snapshot())
        self.assertEqual(restored.assets(), new.assets())

    def test_import_rejects_changed_source_bad_receipts_and_outside_job(self):
        self.workspace.set_inventory(self.raw)
        ready = self.workspace.prepare_subject('subject_1')
        old = self.legacy_snapshot()
        new = MaterialWorkspace(self.project, self.project / 'jobs' / 'job_2', self.sources, (640, 360))
        bad = copy.deepcopy(ready['receipt']); bad['source_crop'] = [.1, .1, .8, .8]
        result = new.import_materials(old, self.folder, history_receipts=[bad])
        self.assertEqual(result['imported_subject_versions'], 1)
        self.assertEqual(len(result['rejected_receipts']), 1)
        with self.assertRaises(MaterialError):
            new.import_materials(old, self.project.parent)
        Image.new('RGB', (80, 120), 'orange').save(self.ref)
        with self.assertRaisesRegex(MaterialError, '改变|一致'):
            new.import_materials(old, self.folder)

    def test_inspect_is_readonly_for_render_and_validates_region(self):
        self.workspace.set_inventory(self.raw)
        epoch, fingerprint = self.workspace.epoch, self.workspace.fingerprint()
        result = self.workspace.inspect_image('core', [.25, .25, .75, .75])
        self.assertEqual((result['receipt']['width'], result['receipt']['height']), (100, 50))
        self.assertEqual(self.workspace.epoch, epoch)
        self.assertEqual(self.workspace.fingerprint(), fingerprint)
        self.assertEqual(self.workspace.assets(), {})
        with self.assertRaises(MaterialError):
            self.workspace.inspect_image('core', [.7, 0, .1, 1])
        with self.assertRaises(MaterialError):
            self.workspace.inspect_image(str(self.ref))
        with self.assertRaises(MaterialError):
            self.workspace.inspect_image('not_bound')

    def test_background_selection_is_explicit_and_versioned(self):
        self.workspace.set_inventory(self.raw)
        result = self.workspace.select_background(color='#123456')
        self.assertEqual(result['receipt']['render_asset_id'], 'background')
        self.assertEqual(result['receipt']['width'], 640)
        first = result['images']['background']
        result2 = self.workspace.select_background(asset_id='ref_1')
        self.assertEqual(result2['receipt']['height'], 120)
        self.assertTrue(first.exists())
        with self.assertRaises(MaterialError):
            self.workspace.select_background(color='#123456', asset_id='core')
        with self.assertRaises(MaterialError):
            self.workspace.select_background(color='red')

    def test_registered_preview_can_be_inspected_and_restored_without_invalidating_render(self):
        self.workspace.set_inventory(self.raw)
        self.folder.mkdir(parents=True)
        screenshot = self.folder / 'poster.png'
        Image.new('RGB', (640, 360), 'cyan').save(screenshot)
        before = self.workspace.epoch, self.workspace.fingerprint()
        result = self.workspace.register_image('preview_0001', screenshot)
        self.assertEqual(result['receipt']['asset_id'], 'preview_0001')
        inspected = self.workspace.inspect_image('preview_0001', [0, 0, .5, .5])
        self.assertEqual((inspected['receipt']['width'], inspected['receipt']['height']), (320, 180))
        self.assertEqual((self.workspace.epoch, self.workspace.fingerprint()), before)
        self.assertNotIn('preview_0001', self.workspace.assets())
        raw = self.workspace.snapshot()
        restored = MaterialWorkspace(self.project, self.folder, self.sources, (640, 360))
        restored.restore(raw)
        self.assertIn('preview_0001', restored.registry)
        with self.assertRaises(MaterialError):
            self.workspace.register_image('preview_0001', screenshot)
        with self.assertRaises(MaterialError):
            self.workspace.register_image('subject_0', screenshot)
        with self.assertRaises(MaterialError):
            self.workspace.register_image('external_preview', self.core)

    def test_background_binding_copies_edit_result(self):
        self.workspace.set_inventory(self.raw)
        self.folder.mkdir(parents=True)
        edited = self.folder / 'image-provider.png'
        Image.new('RGB', (100, 100), 'red').save(edited)
        result = self.workspace.bind_background(edited, recipe={'prompt_hash': 'test'})
        frozen = result['images']['background']
        old = frozen.read_bytes()
        Image.new('RGB', (100, 100), 'blue').save(edited)
        self.assertEqual(frozen.read_bytes(), old)
        self.workspace.assets()
        with self.assertRaises(MaterialError):
            self.workspace.bind_background(self.ref)

    def test_background_binding_default_recipe_roundtrips_and_rejects_invalid_atomically(self):
        self.workspace.set_inventory(self.raw)
        self.folder.mkdir(parents=True)
        edited = self.folder / 'image-provider.png'
        Image.new('RGB', (100, 100), 'red').save(edited)
        for kwargs in ({}, {'recipe': None}):
            with self.subTest(kwargs=kwargs):
                bound = self.workspace.bind_background(edited, **kwargs)
                self.assertEqual(bound['receipt']['background']['recipe'], {})
                raw = copy.deepcopy(self.raw)
                raw['background'] = 'background'
                self.workspace.set_inventory(raw)
                restored = MaterialWorkspace(self.project, self.folder, self.sources, (640, 360))
                restored.restore(self.workspace.snapshot())
                self.assertEqual(restored.snapshot(), self.workspace.snapshot())
        before = self.workspace.snapshot()
        files = set(self.folder.rglob('*'))
        for invalid in ([], ['invalid'], 'invalid', 0, True):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(MaterialError, '背景制作记录须为对象'):
                    self.workspace.bind_background(edited, recipe=invalid)
                self.assertEqual(self.workspace.snapshot(), before)
                self.assertEqual(set(self.folder.rglob('*')), files)

    def test_background_plan_change_clears_old_alias_only(self):
        self.workspace.set_inventory(self.raw)
        result = self.workspace.select_background(color='#123456')
        raw = copy.deepcopy(self.raw)
        raw['background_notes'] = '另一个明确背景要求'
        self.workspace.set_inventory(raw)
        self.assertNotIn('background', self.workspace.assets())
        self.assertTrue(result['images']['background'].exists())

    def test_inventory_accepts_selected_background_alias_and_keeps_exact_version(self):
        self.workspace.set_inventory(self.raw)
        self.workspace.prepare_subject('subject_1')
        bound = self.workspace.select_background(color='#123456')
        version = bound['receipt']['asset_id']
        path = self.workspace.assets()['background']
        raw = copy.deepcopy(self.raw)
        raw['background'] = 'background'
        raw['background_notes'] = '已检查并选定这张底图，不需要重新生成'
        self.workspace.set_inventory(raw)
        self.assertEqual(self.workspace.assets()['background'], path)
        self.assertEqual(self.workspace.material_status()['selected_background_asset_id'], version)
        self.assertFalse(self.workspace.inventory_delta['background']['changed'])
        self.assertEqual(self.workspace.prepare_subject('subject_1')['receipt']['reused'], True)
        restored = MaterialWorkspace(self.project, self.folder, self.sources, (640, 360))
        restored.restore(self.workspace.snapshot())
        self.assertEqual(restored.assets(), self.workspace.assets())

    def test_inventory_explicit_owned_background_version_selects_only_requested_version(self):
        self.workspace.set_inventory(self.raw)
        first = self.workspace.select_background(color='#123456')
        second = self.workspace.select_background(color='#654321')
        raw = copy.deepcopy(self.raw)
        raw['background'] = first['receipt']['asset_id']
        self.workspace.set_inventory(raw)
        self.assertEqual(self.workspace.assets()['background'], first['images']['background'])
        delta = self.workspace.inventory_delta['background']
        self.assertEqual(delta['selected'], first['receipt']['asset_id'])
        self.assertEqual(delta['previous'], second['receipt']['asset_id'])
        self.assertTrue(delta['changed'])
        # Repeating an explicit selection after another tool switched the
        # actual background must not incorrectly take the inventory no-op path.
        self.workspace.select_background(asset_id=second['receipt']['asset_id'])
        self.workspace.set_inventory(raw)
        self.assertEqual(self.workspace.assets()['background'], first['images']['background'])

    def test_inventory_background_alias_errors_are_actionable_without_registry_escape(self):
        self.workspace.set_inventory(self.raw)
        raw = copy.deepcopy(self.raw)
        raw['background'] = 'background'
        with self.assertRaisesRegex(MaterialError, 'select_background'):
            self.workspace.set_inventory(raw)
        ready = self.workspace.prepare_subject('subject_1')
        raw['background'] = ready['receipt']['asset_id']
        with self.assertRaisesRegex(MaterialError, '未绑定为本任务底图'):
            self.workspace.set_inventory(raw)
        bg = self.workspace.select_background(color='#123456')
        raw['background'] = bg['receipt']['asset_id']
        raw['subjects'][1]['source'] = bg['receipt']['asset_id']
        with self.assertRaisesRegex(MaterialError, '主体 source'):
            self.workspace.set_inventory(raw)

    def test_import_remaps_inventory_background_version_and_preserves_restore_contract(self):
        self.workspace.set_inventory(self.raw)
        bound = self.workspace.select_background(color='#123456')
        raw = copy.deepcopy(self.raw)
        raw['background'] = 'background'
        self.workspace.set_inventory(raw)
        new = MaterialWorkspace(self.project, self.project / 'jobs' / 'job_2', self.sources, (640, 360))
        result = new.import_materials(self.workspace.snapshot(), self.folder)
        self.assertNotEqual(result['inventory']['background'], bound['receipt']['asset_id'])
        self.assertEqual(result['inventory']['background'], result['previous_background_asset_id'])
        new.set_inventory(result['inventory'])
        new.prepare_subject('subject_1')
        self.assertTrue(new.assets()['background'].is_relative_to(new.folder))
        self.assertEqual(new.assets()['background'].read_bytes(), bound['images']['background'].read_bytes())
        restored = MaterialWorkspace(self.project, new.folder, self.sources, (640, 360))
        restored.restore(new.snapshot())
        self.assertEqual(restored.assets(), new.assets())

    def test_restore_rejects_malformed_background_state_before_design_atomically(self):
        self.workspace.set_inventory(self.raw)
        bound = self.workspace.select_background(color='#123456')
        raw = copy.deepcopy(self.raw)
        raw['background'] = 'background'
        self.workspace.set_inventory(raw)
        good = self.workspace.snapshot()
        for field in ('aliases', 'prepared', 'background_history'):
            for malformed in (None, [], ['not-a-mapping'], 'invalid'):
                with self.subTest(field=field, malformed=malformed):
                    bad = copy.deepcopy(good)
                    bad[field] = malformed
                    with self.assertRaises(MaterialError):
                        self.workspace.restore(bad)
                    self.assertEqual(self.workspace.snapshot(), good)
        for malformed in (None, [], 'invalid'):
            bad = copy.deepcopy(good)
            bad['background_history'][bound['receipt']['asset_id']]['recipe'] = malformed
            with self.assertRaises(MaterialError):
                self.workspace.restore(bad)
            self.assertEqual(self.workspace.snapshot(), good)

    def test_source_mutation_is_detected(self):
        self.workspace.set_inventory(self.raw)
        Image.new('RGB', (200, 100), 'red').save(self.core)
        with self.assertRaisesRegex(MaterialError, '改变'):
            self.workspace.prepare_subject('subject_0', treatment='preserve')

    def test_scoped_sources_and_job_folder(self):
        with self.assertRaises(MaterialError):
            MaterialWorkspace(self.project, self.project, self.sources, (640, 360))
        with tempfile.TemporaryDirectory() as outside:
            other = Path(outside) / 'other.png'
            Image.new('RGB', (50, 50)).save(other)
            with self.assertRaises(MaterialError):
                MaterialWorkspace(self.project, self.folder, dict(core=other), (640, 360))
        with self.assertRaises(MaterialError):
            MaterialWorkspace(self.project, self.folder, {'core': self.core, 'subject_0': self.ref}, (640, 360))

    def test_cancellation_before_tool_call(self):
        workspace = MaterialWorkspace(self.project, self.folder, self.sources, (640, 360), cancelled=lambda: True)
        with self.assertRaises(InterruptedError):
            workspace.set_inventory(self.raw)
        self.assertFalse(self.folder.exists())

    def test_snapshot_restore_roundtrip_has_no_material_execution(self):
        self.workspace.set_inventory(self.raw)
        self.workspace.prepare_subject('subject_0', treatment='preserve')
        self.workspace.prepare_subject('subject_1')
        self.workspace.select_background(color='#223344')
        self.workspace.inspect_image('core', [0, 0, .5, .5])
        raw = json.loads(json.dumps(self.workspace.snapshot()))
        restored = MaterialWorkspace(self.project, self.folder, self.sources, (640, 360))
        with patch('backend.app.vibe_agent_materials.rmbg.run_cutouts') as rmbg_call:
            restored.restore(raw)
        rmbg_call.assert_not_called()
        self.assertEqual(restored.snapshot(), self.workspace.snapshot())
        self.assertEqual(restored.assets(), self.workspace.assets())
        self.assertEqual(restored.design.model_dump(), self.workspace.design.model_dump())

    def test_restore_rejects_other_job_and_tampered_provenance_atomically(self):
        self.workspace.set_inventory(self.raw)
        result = self.workspace.prepare_subject('subject_1')
        good = self.workspace.snapshot()
        cases = []
        raw = copy.deepcopy(good); raw['folder'] = 'jobs/other'; cases.append(raw)
        raw = copy.deepcopy(good); raw['entries']['core']['path'] = '../core.png'; cases.append(raw)
        raw = copy.deepcopy(good); raw['entries']['core']['path'] = 'ref.png'; cases.append(raw)
        raw = copy.deepcopy(good); raw['aliases']['subject_1'] = 'core'; cases.append(raw)
        raw = copy.deepcopy(good); raw['prepared']['subject_1']['spec']['name'] = '替换身份'; cases.append(raw)
        raw = copy.deepcopy(good); raw['fingerprint'] = 'tampered'; cases.append(raw)
        for bad in cases:
            with self.assertRaises(MaterialError):
                self.workspace.restore(bad)
            self.assertEqual(self.workspace.snapshot(), good)
        Image.new('RGBA', (80, 120), 'yellow').save(result['images']['subject_1'])
        with self.assertRaisesRegex(MaterialError, '改变'):
            self.workspace.restore(good)

    def test_retained_background_cannot_silently_be_replaced(self):
        raw = copy.deepcopy(self.raw)
        raw['background_retained'] = [dict(name='透明瓶', source_box=[.7, .2, .9, .7],
                                           reason='难分离的半透明示意，保留原底图', content_kind='illustration')]
        raw['foreground_summary']['background_retained_count'] = 1
        self.workspace.set_inventory(raw)
        with self.assertRaisesRegex(MaterialError, '丢失主体'):
            self.workspace.select_background(color='#000000')
        with self.assertRaisesRegex(MaterialError, '保留在底图'):
            self.workspace.select_background(asset_id='ref_1')

    def test_historical_background_can_be_reused_with_matching_retained_recipe(self):
        raw = copy.deepcopy(self.raw)
        retained = dict(name='透明瓶', source_box=[.7, .2, .9, .7],
                        reason='保留难以分离的透明示意物', content_kind='illustration')
        raw['background_retained'] = [retained]
        raw['foreground_summary']['background_retained_count'] = 1
        self.workspace.set_inventory(raw)
        self.folder.mkdir(parents=True)
        background = self.folder / 'edited.png'
        Image.new('RGB', (640, 360), '#123456').save(background)
        recipe = {'retained': [{key: retained[key] for key in ('name', 'source_box', 'content_kind')}]}
        first = self.workspace.bind_background(background, recipe=recipe)
        self.workspace.bind_background(background, recipe=recipe)
        snapshot = self.workspace.snapshot()
        resumed = MaterialWorkspace(self.project, self.folder, self.sources, (640, 360))
        resumed.restore(snapshot)
        chosen = resumed.select_background(asset_id=first['receipt']['asset_id'])
        self.assertEqual(chosen['receipt']['render_asset_id'], 'background')
        self.assertEqual(chosen['receipt']['background']['recipe'], recipe)
        changed = copy.deepcopy(raw)
        changed['background_retained'][0]['name'] = '不同的透明物体'
        resumed.set_inventory(changed)
        with self.assertRaisesRegex(MaterialError, '保留在底图'):
            resumed.select_background(asset_id=first['receipt']['asset_id'])


if __name__ == '__main__':
    unittest.main()
