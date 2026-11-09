"""Offline new-attempt reuse: preserve evidence, never inherit an accepted answer."""
import copy
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from PIL import Image

from backend.app import vibe_cloud as api, vibe_agent_workflow as worker
from backend.app.vibe_agent_contract import parse_tool_call


def inventory():
    return dict(background='solid', background_color='#102030', background_is_uniform=True,
        foreground_summary=dict(has_nontext_subjects=True, subject_count=1, description='普通示意图'),
        subjects=[dict(name='圆形主体', source='core', source_crop=[.1,.2,.4,.8], treatment='preserve')],
        texts=[dict(text='研究结果', source_box=[.4,.1,.9,.3])])


class AgentReuseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name).resolve()
        self.core = self.project/'core.png'
        Image.new('RGB', (640,360), '#102030').save(self.core)
        self.formal = self.project/'record.json'
        self.formal.write_text('{"revision":13}', encoding='utf-8')
        self.data = api.CloudRequest(confirm_cloud=True, template_mode='layered', max_image_requests=0)
        self.context = dict(shot_id='generic', canvas=[640,360], duration=8, subtitles=[])
        self.old = self.session('a'*32)
        self.old.workspace.set_inventory(inventory())
        result = self.old.workspace.prepare_subject('subject_0')
        self.old.record('prepare_subject', '原像素取材', result['receipt'])
        self.old.workspace.select_background(color='#102030')
        self.old.state['latest_code'] = {'code':'OLD_CODE_MUST_NOT_BE_INHERITED'}
        self.old.state['previews'] = {'old_preview':{'locally_valid':True}}
        self.old.state['observations']['delivery_review'] = {'passed':True}
        self.old.save()
        api._update(self.old.path, status='failed', message='旧尝试未交付',
            usage=dict(model_calls=[{'old':True}]*16,image_requests=1,image_task_ids=['old-image']))

    def session(self, identity, advanced=None):
        folder = api._root(self.project)/identity
        folder.mkdir()
        api._write(folder/'task.json', dict(task_id=identity,shot_id='generic',
            template_mode='layered',workflow_version=worker.VERSION,cloud_input_stamp='c'*64,
            status='running', max_model_calls=16,max_image_requests=0,
            usage=dict(model_calls=[],image_requests=0,image_task_ids=[])))
        if advanced is not None:
            api._update(folder/'task.json', advanced_subject_separation=advanced)
        return worker.AgentSession(self.project,'generic',identity,{},self.data,threading.Event(),
                                   self.context,{'core':self.core})

    def test_new_attempt_copies_materials_but_not_code_review_or_old_budget(self):
        old_bytes = (self.old.folder/'agent-state.json').read_bytes()
        formal = self.formal.read_bytes()
        fresh = self.session('b'*32)
        with patch.object(api, '_call') as model, patch.object(api, 'generate_asset') as image, \
                patch('backend.app.vibe_agent_materials.rmbg.run_cutouts') as rmbg:
            fresh.reuse_materials('a'*32)
        model.assert_not_called(); image.assert_not_called(); rmbg.assert_not_called()
        self.assertEqual(set(fresh.workspace.assets()), {'background','subject_0','subject_0_original'})
        for key, path in fresh.workspace.assets().items():
            self.assertTrue(path.is_relative_to(fresh.folder))
            self.assertEqual(path.read_bytes(),self.old.workspace.assets()[key].read_bytes())
        self.assertIsNone(fresh.state['latest_code'])
        self.assertEqual(fresh.state['previews'],{})
        self.assertNotIn('delivery_review',fresh.state['observations'])
        self.assertEqual(fresh.task()['usage']['model_calls'],[])
        self.assertEqual(fresh.task()['usage']['image_requests'],0)
        self.assertEqual((self.old.folder/'agent-state.json').read_bytes(),old_bytes)
        self.assertEqual(self.formal.read_bytes(),formal)
        self.assertIn('background',fresh.pictures)
        self.assertIn('material_status',fresh.payload())
        self.assertEqual(fresh.task()['reused_from_task'],'a'*32)

    def test_inventory_receipt_exposes_retained_and_missing_without_generation(self):
        fresh = self.session('b'*32)
        fresh.reuse_materials('a'*32)
        raw = copy.deepcopy(inventory())
        raw['subjects'][0]['name']='仅改展示名称'
        _, args = parse_tool_call(dict(tool='set_inventory',summary='改名',arguments={'inventory':raw}))
        result = fresh.execute('set_inventory',args,'改名')
        self.assertIn('changes',result['receipt'])
        self.assertIn('material_status',result['receipt'])
        self.assertIn('subject_0',fresh.workspace.assets())
        raw['subjects'][0]['source_crop']=[.2,.2,.4,.8]
        _, args = parse_tool_call(dict(tool='set_inventory',summary='换裁区',arguments={'inventory':raw}))
        changed = fresh.execute('set_inventory',args,'换裁区')
        self.assertNotIn('subject_0',fresh.workspace.assets())
        self.assertIn('subject_0',str(changed['receipt']['changes']))
        self.assertIn('subject_0',str(changed['receipt']['material_status']))

    def test_reuse_remaps_explicit_background_inventory_and_can_resume(self):
        raw=self.old.workspace.inventory.model_dump(mode='json')
        old_background=self.old.workspace.material_status()['selected_background_asset_id']
        raw['background']=old_background
        self.old.workspace.set_inventory(raw)
        self.old.save()
        fresh=self.session('b'*32)
        with patch.object(api,'_call') as model, patch.object(api,'generate_asset') as image, \
                patch('backend.app.vibe_agent_materials.rmbg.run_cutouts') as rmbg:
            fresh.reuse_materials('a'*32)
            self.assertNotEqual(fresh.workspace.inventory.background,old_background)
            self.assertIn(fresh.workspace.inventory.background,fresh.workspace.registry)
            fresh.workspace.prepare_subject('subject_0')
            fresh.save()
            fresh.restore()
        model.assert_not_called();image.assert_not_called();rmbg.assert_not_called()
        self.assertEqual(set(fresh.workspace.assets()),{'background','subject_0','subject_0_original'})
        self.assertEqual(fresh.workspace.assets()['background'].read_bytes(),
                         self.old.workspace.assets()['background'].read_bytes())
        self.assertIsNone(fresh.state['latest_code'])
        self.assertEqual(fresh.state['previews'],{})

    def test_reuse_rechecks_input_before_import(self):
        fresh = self.session('b'*32)
        api._update(fresh.path,cloud_input_stamp='d'*64)
        with self.assertRaises(Exception):
            fresh.reuse_materials('a'*32)
        self.assertEqual(fresh.workspace.assets(),{})
        self.assertEqual(fresh.task()['usage']['model_calls'],[])

    def test_stable_reuse_imports_pixels_but_not_advanced_object_plan(self):
        old_bytes=(self.old.folder/'agent-state.json').read_bytes()
        fresh=self.session('b'*32, advanced=False)
        with patch.object(api, '_call') as model, patch.object(api, 'generate_asset') as image, \
                patch('backend.app.vibe_agent_materials.rmbg.run_cutouts') as rmbg:
            fresh.reuse_materials('a'*32)
        model.assert_not_called();image.assert_not_called();rmbg.assert_not_called()
        self.assertIsNone(fresh.workspace.inventory)
        self.assertEqual(fresh.workspace.assets(), {})
        self.assertGreater(len(fresh.workspace.material_status()['reusable_subjects']), 0)
        self.assertIn('进阶主体分离', fresh.state['observations']['reuse_materials']['materials']['inventory_policy_error'])
        self.assertFalse(fresh.payload()['material_strategy']['advanced_subject_separation'])
        self.assertIsNone(fresh.state['latest_code'])
        self.assertEqual((self.old.folder/'agent-state.json').read_bytes(),old_bytes)


if __name__ == '__main__':
    unittest.main()
