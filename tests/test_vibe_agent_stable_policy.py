"""Offline material boundaries, not shot-specific design answers."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from backend.app.vibe_agent_materials import MaterialWorkspace
from backend.app.vibe_agent_policy import subject_policy
from backend.app.vibe_layered_inventory import Inventory
from backend.app.vibe_materials import MaterialError


def inventory(role='object', treatment='cutout'):
    return dict(background='extract', background_color='#123456',
        foreground_summary=dict(has_nontext_subjects=True, subject_count=1,
                                description='左侧原有主体、右侧说明'),
        subjects=[dict(role=role, name='测试主体', source='core',
                       source_crop=[.05,.1,.45,.9], treatment=treatment)],
        texts=[dict(text='说明', source_box=[.6,.1,.9,.3])])


def retained_inventory():
    value = inventory()
    value['subjects'] = []
    value['background_retained'] = [dict(name='半透明示意物体', source_box=[.05,.1,.45,.9],
        reason='完整保留半透明物体及其与背景光线的融合', content_kind='illustration')]
    value['foreground_summary'].update(subject_count=0, background_retained_count=1)
    return value


class StableSubjectPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        self.core = self.project/'core.png'
        Image.new('RGB', (200,100), '#123456').save(self.core)
        self.sources = {'core': self.core}
        self.folder = self.project/'jobs'/'stable'
        self.work = MaterialWorkspace(self.project, self.folder, self.sources, (640,360),
                                      advanced_subject_separation=False)

    def test_default_stable_policy_is_clear_and_not_a_fixed_template(self):
        policy = subject_policy(False)
        self.assertFalse(policy['advanced_subject_separation'])
        for text in ('background_retained', 'person', 'photo/evidence', '按授权', '由你设计'):
            self.assertIn(text, policy['instruction'])

    def test_stable_rejects_object_before_cropping_or_mutating_inventory(self):
        for treatment in ('cutout', 'preserve'):
            with self.subTest(treatment=treatment), patch('backend.app.vibe_agent_materials.crop_asset') as crop, \
                    patch('backend.app.vibe_agent_materials.rmbg.run_cutouts') as rmbg:
                with self.assertRaisesRegex(ValueError, '进阶主体分离'):
                    self.work.set_inventory(inventory(treatment=treatment))
                crop.assert_not_called()
                rmbg.assert_not_called()
                self.assertIsNone(self.work.inventory)
                self.assertFalse(self.folder.exists())

    def test_retained_subject_needs_no_independent_prepare(self):
        self.work.set_inventory(retained_inventory())
        self.assertEqual(self.work.material_status()['missing'], [])
        self.assertEqual(len(self.work.design.background_retained), 1)
        with self.assertRaisesRegex(MaterialError, '纯色会丢失主体'):
            self.work.select_background(color='#123456')

    def test_old_erased_background_cannot_replace_retained_subject(self):
        self.folder.mkdir(parents=True)
        bg = self.folder/'old.png'
        Image.new('RGB', (640,360), '#123456').save(bg)
        self.work.bind_background(bg, recipe={'retained': []})
        identity = self.work.material_status()['selected_background_asset_id']
        self.work.set_inventory(retained_inventory())
        with self.assertRaisesRegex(MaterialError, '保留在底图'):
            self.work.select_background(asset_id=identity)
        self.assertNotIn('background', self.work.assets())

    def test_person_is_allowed_without_enabling_complex_object_cutouts(self):
        self.work.set_inventory(inventory(role='person', treatment='preserve'))
        result = self.work.prepare_subject('subject_0')
        self.assertFalse(result['receipt']['rmbg_used'])
        self.assertEqual(self.work.inventory.subjects[0].role, 'person')
        raw = self.work.inventory.model_dump(mode='json')
        raw['subjects'][0]['role'] = 'object'
        # Even a stale/programmatically supplied inventory is checked again
        # at the last boundary before a pixel-producing tool is invoked.
        self.work.inventory = Inventory.model_validate(raw)
        with patch('backend.app.vibe_agent_materials.rmbg.run_cutouts') as rmbg:
            with self.assertRaisesRegex(ValueError, '进阶主体分离'):
                self.work.prepare_subject('subject_0', treatment='cutout')
            rmbg.assert_not_called()

    def test_photographs_and_evidence_keep_original_pixels(self):
        for role in ('photo', 'evidence'):
            with self.subTest(role=role):
                self.work.set_inventory(inventory(role=role, treatment='preserve'))
                result = self.work.prepare_subject('subject_0')
                with Image.open(self.core) as source, Image.open(result['images']['subject_0']) as actual:
                    self.assertEqual(actual.convert('RGB').tobytes(), source.crop((10,10,90,90)).tobytes())
                with self.assertRaisesRegex(ValueError, '保留原始像素'):
                    self.work.prepare_subject('subject_0', treatment='cutout')

    def test_advanced_keeps_optional_object_extraction(self):
        advanced = MaterialWorkspace(self.project, self.project/'jobs'/'advanced', self.sources, (640,360),
                                     advanced_subject_separation=True)
        advanced.set_inventory(inventory(treatment='preserve'))
        self.assertFalse(advanced.prepare_subject('subject_0')['receipt']['rmbg_used'])
        self.assertIn('不是要求所有主体都抠图', subject_policy(True)['instruction'])
        snapshot = advanced.snapshot()
        same_folder_stable = MaterialWorkspace(self.project, advanced.folder, self.sources, (640,360),
                                               advanced_subject_separation=False)
        with self.assertRaisesRegex(ValueError, '进阶主体分离'):
            same_folder_stable.restore(snapshot)
        self.assertIsNone(same_folder_stable.inventory)

    def test_stable_snapshot_roundtrip_and_readonly_imported_materials(self):
        raw = retained_inventory()
        self.work.set_inventory(raw)
        snapshot = copy.deepcopy(self.work.snapshot())
        restored = MaterialWorkspace(self.project, self.folder, self.sources, (640,360),
                                     advanced_subject_separation=False)
        restored.restore(snapshot)
        self.assertEqual(restored.fingerprint(), self.work.fingerprint())
        self.assertEqual(raw, retained_inventory())


if __name__ == '__main__':
    unittest.main()
