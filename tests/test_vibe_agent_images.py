"""Paid-image accounting and recovery in the autonomous tool host, offline."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from PIL import Image

from backend.app import vibe_agent_workflow as worker, vibe_cloud as api
from backend.app.vibe_agent_contract import parse_tool_call
from backend.app.vibe_materials import MaterialError


def inventory():
    return dict(background='extract', background_color='#122233', background_notes='保留背景光带',
                foreground_summary=dict(has_nontext_subjects=False, subject_count=0,
                    description='纯文字海报', text_only_reason='只有标题，没有主体插图'),
                subjects=[], texts=[dict(text='研究结果', source_box=[.1,.1,.9,.3])])


class AgentImagesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name).resolve()/'project'
        self.project.mkdir()
        self.core = self.project/'core.png'
        Image.new('RGB', (640,360), '#122233').save(self.core)
        self.sources = {'core':self.core}
        self.route = dict(key='c'*64, label='个人 API · 测试图像模型')
        self.config = dict(layered_owner_id=123, layered_image_route=self.route,
                           layered_image_config={'model':'test-image','api_key':'TEST_NOT_SERIALIZED'})
        self.data = api.CloudRequest(confirm_cloud=True, template_mode='layered',
                                     max_image_requests=2, max_model_calls=16)
        self.context = dict(shot_id='shot', duration=8, canvas=[640,360], subtitles=[])
        self.event = threading.Event()
        self.ids = 0
        self.session = self.new_session()
        self.generate = self.mock('backend.app.vibe_agent_workflow.api.generate_asset', side_effect=self.generated)
        self.resume = self.mock('backend.app.vibe_agent_workflow.resume_asset', side_effect=self.resumed)
        self.mock('backend.app.vibe_agent_workflow.layered._cached_background', return_value=None)

    def mock(self, name, **kwargs):
        active = patch(name, **kwargs)
        result = active.start()
        self.addCleanup(active.stop)
        return result

    def new_session(self):
        self.ids += 1
        identity = f'{self.ids:032x}'
        path = api._root(self.project)/identity/'task.json'
        path.parent.mkdir(parents=True)
        api._write(path, dict(task_id=identity, shot_id='shot', workflow_version=worker.VERSION,
            template_mode='layered', status='running', stage='agent_tool', cloud_input_stamp='d'*64,
            max_image_requests=2, max_model_calls=16, image_route=copy.deepcopy(self.route),
            usage={'model_calls':[],'image_requests':0,'image_task_ids':[]}))
        session = worker.AgentSession(self.project,'shot',identity,self.config,self.data,self.event,self.context,self.sources)
        session.workspace.set_inventory(inventory())
        session.save()
        return session

    def generated(self, user, prompt, refs, output, name, cancelled, **kwargs):
        task = self.session.task()
        self.assertGreater(task['usage']['image_requests'],0, 'reserve before network submission')
        self.assertEqual(kwargs['image_route'], self.route)
        self.assertEqual(kwargs['image_config'], self.config['layered_image_config'])
        self.assertEqual(user,123)
        identity = f'{task["usage"]["image_requests"]+500:032x}'
        kwargs['on_submitted'](identity)
        return self.resumed(user, identity, output, name, cancelled)

    @staticmethod
    def resumed(user, identity, output, name, cancelled):
        target = output/(name+'.png')
        Image.new('RGB',(640,360),'#224466').save(target)
        return {'path':target,'studio_task_id':identity}

    def test_explicit_request_uses_project_route_and_retains_all_paid_ids(self):
        first = self.session.edit_background('保留装饰，不重新设计')
        old = first['images']['background']
        second = self.session.edit_background('修正第一次的装饰缺失')
        self.assertNotEqual(old,second['images']['background'])
        self.assertTrue(old.exists())
        task = self.session.task()
        self.assertEqual(task['usage']['image_requests'],2)
        self.assertEqual(len(task['usage']['image_task_ids']),2)
        self.assertIsNone(self.session.state['pending_image'])
        self.assertNotIn('TEST_NOT_SERIALIZED',(self.session.folder/'agent-state.json').read_text('utf-8'))
        self.assertNotIn('api_key',(self.session.folder/'background_ready.json').read_text('utf-8'))

    def test_budget_zero_or_exhausted_never_submits(self):
        api._update(self.session.path,max_image_requests=0)
        with self.assertRaisesRegex(ValueError,'额度'):
            self.session.edit_background('')
        self.generate.assert_not_called()
        api._update(self.session.path,max_image_requests=2,
                    usage={'image_requests':2,'image_task_ids':['a'*32,'b'*32],'model_calls':[]})
        with self.assertRaisesRegex(ValueError,'额度'):
            self.session.edit_background('')
        self.generate.assert_not_called()

    def test_identical_completed_edit_reuses_current_result(self):
        self.session.edit_background('保留既有光带')
        result=self.session.edit_background('保留既有光带')
        self.assertTrue(result['receipt']['cached'])
        self.assertEqual(self.generate.call_count,1)
        self.assertEqual(self.session.task()['usage']['image_requests'],1)

    def test_targeted_repair_keeps_notes_and_uses_remaining_authorized_request(self):
        original = self.session.workspace.design.background_notes
        self.session.edit_background('移除文字，保留原背景')
        name, args = parse_tool_call(dict(tool='edit_background', summary='修补残留前景', arguments=dict(
            prompt='完全移除残留条形像素并按原背景补全',
            remove_regions=[dict(description='橙色数据条及残留边缘',region=[.2,.4,.7,.53])],
            preserve_details=['保留背景反光'])))
        result = self.session.execute(name, args, '修补残留前景')
        self.assertEqual(self.generate.call_count, 2)
        self.assertEqual(self.generate.call_args.args[2], [self.core])
        prompt = self.generate.call_args.args[1]
        self.assertIn('橙色数据条及残留边缘', prompt)
        self.assertIn('完全移除残留条形像素', prompt)
        self.assertIn('保留背景反光', prompt)
        self.assertEqual(self.session.workspace.design.background_notes, original)
        self.assertEqual(result['receipt']['budget']['image_remaining'], 0)
        self.assertEqual(self.session.task()['usage']['image_requests'], 2)
        self.assertFalse(self.session.background_edit_status()['can_submit'])

    def test_changed_region_is_new_recipe_but_identical_target_reuses(self):
        regions = [dict(description='独立数据条',region=[.1,.3,.7,.4])]
        self.session.edit_background('定向修补',remove_regions=regions)
        cached = self.session.edit_background('定向修补',remove_regions=regions)
        self.assertTrue(cached['receipt']['cached'])
        self.assertEqual(self.generate.call_count, 1)
        self.session.edit_background('定向修补',remove_regions=[dict(
            description='独立数据条',region=[.1,.3,.7,.5])])
        self.assertEqual(self.generate.call_count, 2)

    def test_invalid_region_fails_before_reserving_or_submitting(self):
        with self.assertRaises(ValueError):
            self.session.edit_background('修补',remove_regions=[dict(description='条带',region=[.8,.2,.1,.4])])
        self.generate.assert_not_called()
        self.assertEqual(self.session.budgets()['image_used'], 0)

    def test_edit_status_reports_permission_config_and_pending_independently(self):
        self.assertTrue(self.session.background_edit_status()['can_submit'])
        self.session.state['pending_image'] = dict(image_task_id='a'*32)
        status = self.session.background_edit_status()
        self.assertFalse(status['can_submit'])
        self.assertIn('pending_image_requires_reconciliation',status['blockers'])
        self.session.state['pending_image'] = None
        self.session.state['new_images_allowed'] = False
        self.assertIn('new_images_not_authorized',self.session.background_edit_status()['blockers'])
        self.session.state['new_images_allowed'] = True
        self.config.pop('layered_image_config')
        self.assertIn('project_image_config_missing',self.session.background_edit_status()['blockers'])
        self.generate.assert_not_called()

    def test_uncertain_submission_is_saved_and_cannot_be_automatically_reissued(self):
        def waiting(*args,**kwargs):
            kwargs['on_submitted']('e'*32)
            raise MaterialError('等待超时','e'*32)
        self.generate.side_effect=waiting
        with self.assertRaisesRegex(MaterialError,'未自动补发'):
            self.session.edit_background('')
        state=json.loads((self.session.folder/'agent-state.json').read_text('utf-8'))
        self.assertEqual(state['pending_image']['image_task_id'],'e'*32)
        self.assertEqual(self.session.task()['usage']['image_requests'],1)
        with self.assertRaisesRegex(MaterialError,'未重复出图'):
            self.session.edit_background('再试试')
        self.assertEqual(self.generate.call_count,1)

    def test_resume_uses_same_task_without_resetting_budgets_or_new_images(self):
        def waiting(*args,**kwargs):
            kwargs['on_submitted']('e'*32)
            raise MaterialError('等待超时','e'*32)
        self.generate.side_effect=waiting
        with self.assertRaises(MaterialError):self.session.edit_background('保留左侧光斑')
        task=self.session.task()
        task['usage']['model_calls']=[{'status':'completed'}]*6
        api._update(self.session.path,usage=task['usage'])
        restored=worker.AgentSession(self.project,'shot',self.session.job_id,
            {'layered_owner_id':123},self.data,self.event,self.context,self.sources)
        restored.restore()
        restored.resume_image()
        self.resume.assert_called_once()
        self.assertEqual(self.resume.call_args.args[1],'e'*32)
        self.assertEqual(restored.budgets()['image_used'],1)
        self.assertEqual(restored.budgets()['model_used'],6)
        self.assertEqual(restored.budgets()['model_remaining'],10)
        self.assertIn('background',restored.workspace.assets())
        with self.assertRaisesRegex(ValueError,'续接'):
            restored.edit_background('需要新图')
        self.assertEqual(self.generate.call_count,1)

    def test_submission_failure_without_receipt_still_consumes_budget_and_stops(self):
        self.generate.side_effect=RuntimeError('upstream closed')
        with self.assertRaises(MaterialError):self.session.edit_background('')
        self.assertEqual(self.session.task()['usage']['image_requests'],1)
        self.assertIsNone(self.session.state['pending_image']['image_task_id'])
        with self.assertRaises(MaterialError):self.session.edit_background('')
        self.assertEqual(self.generate.call_count,1)

    def test_route_mismatch_stops_before_submission(self):
        self.config['layered_image_route']={**self.route,'key':'f'*64}
        with self.assertRaisesRegex(ValueError,'渠道'):
            self.session.edit_background('')
        self.generate.assert_not_called()

    def test_cancelled_request_never_submits(self):
        self.event.set()
        with self.assertRaises(InterruptedError):self.session.edit_background('')
        self.generate.assert_not_called()

    def test_resume_rejects_corrupt_original_job_identity(self):
        self.session.state['pending_image']=dict(image_task_id='e'*32,image_route_key=self.route['key'])
        with self.assertRaisesRegex(ValueError,'记录不一致'):
            self.session.resume_image()
        self.resume.assert_not_called()

    def test_material_recovery_visibility_is_owned_and_read_only(self):
        def waiting(*args,**kwargs):
            kwargs['on_submitted']('e'*32)
            raise MaterialError('等待超时','e'*32)
        self.generate.side_effect=waiting
        with self.assertRaises(MaterialError):self.session.edit_background('')
        api._update(self.session.path,status='failed')
        studio=self.project/'studio';studio.mkdir()
        api._write(studio/'record.json',{'status':'completed'})
        with patch('backend.app.image_studio.folder',return_value=studio) as folder:
            self.assertTrue(worker.recoverable_material(self.project,self.session.task(),123))
            folder.assert_called_once_with(123,'e'*32)
        self.resume.assert_not_called()
        self.assertEqual(self.generate.call_count,1)


if __name__=='__main__':unittest.main()
