"""V2 route handlers, worker publishing and material-resume consent offline."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from PIL import Image
from starlette.requests import Request

from backend.app import image_studio
from backend.app import vibe_cloud as cloud
from backend.app import vibe_layered as layered
from backend.app import vibe_layered_workflow as workflow
from backend.app.vibe_layered_assembly import Assembly, compile_assembly, materialize_subjects
from backend.app.vibe_materials import MaterialError
import test_vibe_cloud as fixture


class LayeredV2RouteTests(unittest.TestCase):
    # Reuse only setup/helpers, not the entire legacy test class.
    persist = fixture.VibeCloudTests.persist
    render = staticmethod(fixture.VibeCloudTests.render)
    material = staticmethod(fixture.VibeCloudTests.material)

    def setUp(self):
        fixture.VibeCloudTests.setUp(self)
        # Persisted v2 jobs retain their original protocol when v3 is the default.
        self.start_patch('backend.app.vibe_layered.WORKFLOW_VERSION', workflow.VERSION)
        self.req = Request({'type': 'http', 'headers': []})
        self.design = dict(background='solid', background_is_uniform=True, background_color='#253446',
            subjects=[dict(box=[.02, .1, .45, .9], source='ref_03', treatment='preserve', at=0)],
            labels=[dict(box=[.52, .1, .96, .45], text='第一组', color='#FAFAFA', font_size=42, at=.2)])
        self.quality = dict(workflow_version=layered.WORKFLOW_VERSION, quality_status='passed',
                           quality_issues=[], visual_repairs=0, static_reviews=[])
        self.cancel_in_finish = False
        self.mutate_in_finish = False
        self.finishes = []
        self.inventory_decorations = []
        self.start_patch('backend.app.video_studio._image_configs', return_value=[dict(
            endpoint='https://example.invalid/generate', query_url='https://example.invalid/query',
            api_key='offline-image-key', model='personal-image', resolution='2k', ratio='16:9')])
        self.start_patch('backend.app.image_studio.ROOT', self.root / 'images')
        self.model = self.start_patch('backend.app.vibe_cloud.chat_json',
            side_effect=self.inventory_response)
        self.pipeline = self.start_patch('backend.app.vibe_layered_workflow.finish', side_effect=self.finish_pipeline)
        self.legacy_renderer = self.start_patch('backend.app.vibe_layered.render_compiled',
            side_effect=AssertionError('V2 must not fall back to the legacy layered renderer'))

    def start_patch(self, target, *args, **kwargs):
        active = patch(target, *args, **kwargs)
        mocked = active.start()
        self.addCleanup(active.stop)
        return mocked

    def inventory_response(self, *args, **kwargs):
        """Keep convenient legacy fixtures, return only the v2 material contract."""
        design=copy.deepcopy(self.design)
        subjects=[dict(name=f'主体 {index+1}',source=subject['source'],
            source_crop=subject.get('crop',[0,0,1,1]),treatment=subject['treatment'],
            embedded_text=subject.get('embedded_text',[]))
            for index,subject in enumerate(design['subjects'])]
        inventory=dict(background=design['background'],background_color=design['background_color'],
            background_notes=design.get('background_notes',''),
            background_is_uniform=design.get('background_is_uniform',False),subjects=subjects,
            texts=[dict(text=label['text'],source_box=label['box'])
                   for label in design['labels'] if label.get('text','').strip()],
            foreground_summary=dict(has_nontext_subjects=bool(subjects),subject_count=len(subjects),
                description='保留参考图中的前景主体，文字另行提取。' if subjects else '本图仅包含文字与背景。'),
            decorations=copy.deepcopy(self.inventory_decorations))
        if not subjects:inventory['foreground_summary']['text_only_reason']='原图确实只有文字和背景，没有独立的非文字主体。'
        return fixture.VibeCloudTests.response(inventory)

    def start_v2(self, budget=0, **extra):
        # Call the real FastAPI handler with validated bodies. TestClient's
        # Windows event-loop socketpair can hang under the restricted runner.
        return cloud.start(self.identity, 'shot1', cloud.CloudRequest(confirm_cloud=True,
            template_mode='layered', max_image_requests=budget, **extra), self.req)

    def execute(self):
        worker = self.workers[-1]
        worker.target(*worker.args)
        return cloud.latest(self.identity, 'shot1', self.req)

    def finish_pipeline(self, project, sid, job_id, inventory, context, sources, assets,
                        config, data, event, audio, seek):
        self.finishes.append(dict(project=project, shot_id=sid, job_id=job_id,
            instruction=data.instruction, pace=data.pace, config=copy.deepcopy(config),
            sources=sources, assets=assets, context=context, audio=audio, seek=seek))
        self.assertEqual(sid, 'shot1')
        self.assertEqual(context['duration'], 2.5)
        self.assertEqual(context['subtitles'][0]['start'], 0)
        self.assertTrue(assets['subject_0'].is_file())
        self.assertTrue(assets['background'].is_file())
        assembly = Assembly.model_validate(dict(background_color=inventory.background_color,
            subjects=[dict(asset_id='subject_0', box=inventory.subjects[0].box, group='subject')],
            labels=[dict(box=inventory.labels[0].box, text=inventory.labels[0].text,
                color=inventory.labels[0].color, font_size=inventory.labels[0].font_size, group='caption')]))
        folder = cloud._job(project, job_id).parent
        placed = materialize_subjects(assembly, assets, folder / 'offline-assembly-assets')
        plan = compile_assembly(assembly, context, placed)
        output = folder / 'render-v2'
        output.mkdir()
        metrics = self.render(start=seek, duration=context['duration'], cues={'s1': 0, 's2': 1},
                              audio=audio, output_dir=output)
        metrics.update(quality_status=self.quality['quality_status'], visual_repairs=self.quality['visual_repairs'])
        if self.cancel_in_finish:
            event.set()
        if self.mutate_in_finish:
            self.shot['intent'] = '用户此时修改了镜头设计'
            self.persist()
        return plan, placed, metrics, output, copy.deepcopy(self.quality)

    def prepare_failed_material(self):
        self.design['background'] = 'extract'
        self.studio_id = 'c' * 32

        def pending(user, prompt, refs, output, name, cancelled, **kwargs):
            target = image_studio.folder(user, self.studio_id)
            target.mkdir(parents=True)
            image_studio.save(target, dict(id=self.studio_id, status='failed',
                remote_task_id='original-remote-background', message='等待超时'))
            kwargs['on_submitted'](self.studio_id)
            raise MaterialError('背景任务等待超时', self.studio_id)

        with patch.object(cloud, 'generate_asset', side_effect=pending):
            task = self.start_v2(budget=1, instruction='配合字幕逐层入场', pace='slow')
            failed = self.execute()
        self.assertEqual(failed['status'], 'failed', failed)
        self.assertTrue(failed['can_resume_materials'])
        self.assertTrue(failed['resume_requires_model'])
        self.assertEqual(failed['usage']['image_requests'], 1)
        self.assertEqual(failed['usage']['image_task_ids'], [self.studio_id])
        self.pipeline.assert_not_called()
        self.assertFalse(cloud._active)
        return task

    def recovered_asset(self, user, identity, output, name, cancelled):
        self.assertEqual((user, identity), (1, self.studio_id))
        target = output / (name + '.png')
        Image.new('RGB', (640, 360), '#253446').save(target)
        return {'path': target, 'studio_task_id': identity}

    def test_start_worker_publish_persists_version_instruction_pace_without_adopting(self):
        before = (self.project / 'record.json').read_bytes()
        started = self.start_v2(instruction='标题慢一点，保持原图排版', pace='slow')
        self.assertEqual(started['workflow_version'], workflow.VERSION)
        self.assertEqual(started['instruction'], '标题慢一点，保持原图排版')
        self.assertEqual(started['pace'], 'slow')
        stored = cloud._read_job(self.project, started['task_id'])
        self.assertEqual(stored['workflow_version'], workflow.VERSION)
        task = self.execute()
        self.assertEqual(task['status'], 'completed', task)
        self.assertEqual(self.finishes[0]['instruction'], started['instruction'])
        self.assertEqual(self.finishes[0]['pace'], 'slow')
        self.assertEqual(self.model.call_args.args[2]['instruction'], started['instruction'])
        self.assertEqual(self.model.call_args.args[2]['pace'], 'slow')
        self.model.assert_called_once()
        self.assertIs(self.model.call_args.kwargs['thinking'],False)
        self.assertEqual(self.model.call_args.kwargs['max_tokens'],9000)
        schema=self.model.call_args.args[2]['schema']
        self.assertIn('texts',schema['properties'])
        self.assertIn('foreground_summary',schema['properties'])
        self.assertNotIn('labels',schema['properties'])
        # Inventory may locate source pixels but cannot pre-layout, typeset or
        # animate them before the actual prepared materials are inspected.
        forbidden={'at','enter','exit','motion','font_size','line_height','align','valign','plate','box','group'}
        def property_names(value):
            if isinstance(value,dict):
                yield from (value.get('properties') or {})
                for child in value.values():yield from property_names(child)
            elif isinstance(value,list):
                for child in value:yield from property_names(child)
        self.assertFalse(forbidden.intersection(property_names(schema)),schema)
        receipt = task['preview']
        self.assertEqual(receipt['workflow_version'], workflow.VERSION)
        self.assertEqual(receipt['quality_status'], 'passed')
        self.assertEqual(receipt['template_mode'], 'layered')
        self.assertEqual(receipt['renderer'], layered.RENDERER)
        self.assertIn('g:caption:text_0', [element['id'] for element in receipt['plan']['elements']])
        for asset_id in ('preview', 'proof', 'silent'):
            response = cloud.previews.download(self.identity, receipt['preview_id'], asset_id, self.req)
            self.assertTrue(Path(response.path).is_file())
            other = Request({'type': 'http', 'headers': [(b'user', b'2')]})
            with self.assertRaises(HTTPException) as denied:
                cloud.previews.download(self.identity, receipt['preview_id'], asset_id, other)
            self.assertEqual(denied.exception.status_code, 404)
        latest = cloud.previews.latest(self.identity, 'shot1', self.req)
        self.assertTrue(latest['available'])
        self.assertEqual(latest['quality_status'], 'passed')
        self.assertEqual((self.project / 'record.json').read_bytes(), before)
        self.assertFalse(cloud._active)
        cloud.generate_asset.assert_not_called()
        self.legacy_renderer.assert_not_called()
        for path in (self.project / 'assets').rglob('*.json'):
            self.assertNotIn(self.config['api_key'], path.read_text(encoding='utf-8'))

    def test_needs_review_receipt_is_exposed_as_failed_quality_without_mutating_formal_shot(self):
        self.quality.update(quality_status='needs_review', visual_repairs=1, quality_issues=[
            dict(kind='overlap', target='caption', explanation='文本遮挡', correction='调整文字框')])
        before = (self.project / 'record.json').read_bytes()
        self.start_v2()
        task = self.execute()
        self.assertEqual(task['status'], 'completed', task)
        self.assertIn('未通过验收', task['message'])
        self.assertEqual(task['preview']['quality_issues'], self.quality['quality_issues'])
        latest = cloud.previews.latest(self.identity, 'shot1', self.req)
        self.assertEqual(latest['quality_status'], 'needs_review')
        self.assertEqual(latest['visual_repairs'], 1)
        self.assertEqual((self.project / 'record.json').read_bytes(), before)

    def test_cutout_supplies_independent_raw_and_rgba_candidates_in_same_coordinate_frame(self):
        self.design['subjects'][0].update(treatment='cutout',crop=[.1,.15,.85,.9])

        def cutout(pending,output,**kwargs):
            self.assertIsNone(kwargs['trim_padding'])
            result={}
            for name,source in pending.items():
                with Image.open(source) as im:
                    rgba=im.convert('RGBA')
                    rgba.putpixel((0,0),(0,0,0,0))
                    target=output/(name+'-rgba.png');rgba.save(target)
                    result[name]=target
            return result

        with patch.object(layered.rmbg,'run_cutouts',side_effect=cutout) as remove:
            self.start_v2()
            task=self.execute()
        self.assertEqual(task['status'],'completed',task)
        remove.assert_called_once()
        assets=self.finishes[-1]['assets']
        self.assertNotEqual(assets['subject_0'],assets['subject_0_original'])
        self.assertEqual(assets['subject_0_original'].name,'crop_0.png')
        with Image.open(assets['subject_0_original']) as raw,Image.open(assets['subject_0']) as rgba:
            self.assertEqual(raw.size,rgba.size)
            self.assertEqual(raw.getchannel('A').getextrema(),(255,255))
            self.assertEqual(rgba.getpixel((0,0))[3],0)
            self.assertEqual(raw.getpixel((1,1)),rgba.getpixel((1,1)))
        self.model.assert_called_once()
        cloud.generate_asset.assert_not_called()

    def test_preserve_supplies_original_candidate_without_background_removal(self):
        with patch.object(layered.rmbg,'run_cutouts',side_effect=AssertionError('preserve must not run RMBG')) as remove:
            self.start_v2()
            task=self.execute()
        self.assertEqual(task['status'],'completed',task)
        assets=self.finishes[-1]['assets']
        self.assertEqual(assets['subject_0'],assets['subject_0_original'])
        with Image.open(assets['subject_0_original']) as raw,Image.open(self.project/'ref3.png') as reference:
            self.assertEqual(raw.size,reference.size)
            self.assertEqual(raw.convert('RGB').tobytes(),reference.convert('RGB').tobytes())
        remove.assert_not_called()
        self.model.assert_called_once()
        cloud.generate_asset.assert_not_called()

    def test_resuming_cached_cutout_keeps_original_and_does_not_repeat_rmbg(self):
        self.design['subjects'][0].update(treatment='cutout',crop=[.1,.15,.85,.9])
        task=self.prepare_failed_material()
        folder=cloud._job(self.project,task['task_id']).parent/'materials'
        source=cloud._context(self.project,'shot1')[-1]
        raw=cloud.crop_asset(source,'ref_03',self.design['subjects'][0]['crop'],folder,'crop_0')
        rgba=folder/'subject_0-rgba.png'
        with Image.open(raw) as im:
            cut=im.convert('RGBA');cut.putpixel((0,0),(0,0,0,0));cut.save(rgba)
        raw_before=raw.read_bytes();rgba_before=rgba.read_bytes()
        self.model.reset_mock()
        with patch.object(layered,'resume_asset',side_effect=self.recovered_asset) as recover, \
             patch.object(cloud,'crop_asset',side_effect=AssertionError('reuse original crop')) as crop, \
             patch.object(layered.rmbg,'run_cutouts',side_effect=AssertionError('reuse cached RGBA')) as remove:
            cloud.resume_materials(self.identity,'shot1',task['task_id'],self.req,
                                   cloud.ResumeMaterialsRequest(confirm_model=True))
            completed=self.execute()
        self.assertEqual(completed['status'],'completed',completed)
        assets=self.finishes[-1]['assets']
        self.assertEqual(assets['subject_0_original'],raw)
        self.assertEqual(assets['subject_0'],rgba)
        self.assertEqual(raw.read_bytes(),raw_before)
        self.assertEqual(rgba.read_bytes(),rgba_before)
        recover.assert_called_once();crop.assert_not_called();remove.assert_not_called()
        self.model.assert_not_called()
        cloud.generate_asset.assert_not_called()

    def test_cancel_before_worker_does_not_call_model_or_publish(self):
        started = self.start_v2()
        stopped = cloud.stop(self.identity, 'shot1', started['task_id'], self.req)
        self.assertEqual(stopped['status'], 'stopping')
        task = self.execute()
        self.assertEqual(task['status'], 'stopped', task)
        self.model.assert_not_called()
        self.pipeline.assert_not_called()
        self.assertFalse(cloud.previews._pointer(self.project, 'shot1').exists())
        self.assertFalse(cloud._active)

    def test_cancel_after_pipeline_return_cannot_publish_or_switch_formal_mode(self):
        before = (self.project / 'record.json').read_bytes()
        self.cancel_in_finish = True
        self.start_v2()
        task = self.execute()
        self.assertEqual(task['status'], 'stopped', task)
        self.assertNotIn('preview', task)
        self.assertFalse(cloud.previews._pointer(self.project, 'shot1').exists())
        self.assertEqual((self.project / 'record.json').read_bytes(), before)
        self.assertFalse(cloud._active)

    def test_changed_inputs_after_pipeline_cannot_publish_stale_preview(self):
        self.mutate_in_finish = True
        self.start_v2()
        task = self.execute()
        self.assertEqual(task['status'], 'failed', task)
        self.assertIn('未发布过期预览', task['message'])
        self.assertFalse(cloud.previews._pointer(self.project, 'shot1').exists())
        self.assertFalse(cloud._active)

    def test_v2_resume_requires_explicit_model_consent_without_changing_failed_task(self):
        task = self.prepare_failed_material()
        path = cloud._job(self.project, task['task_id'])
        before = path.read_bytes()
        workers = len(self.workers)
        for data in (None, cloud.ResumeMaterialsRequest(), cloud.ResumeMaterialsRequest(confirm_model=False)):
            with self.assertRaises(HTTPException) as denied:
                cloud.resume_materials(self.identity, 'shot1', task['task_id'], self.req, data)
            self.assertEqual(denied.exception.status_code, 409)
            self.assertIn('可能计费', denied.exception.detail)
            self.assertEqual(path.read_bytes(), before)
        for value in ('true', 1, None):
            with self.assertRaises(ValueError):
                cloud.ResumeMaterialsRequest(confirm_model=value)
        self.assertEqual(len(self.workers), workers)
        self.assertFalse(cloud._active)

    def test_resume_config_error_does_not_reserve_active_slot_or_queue_task(self):
        task = self.prepare_failed_material()
        path = cloud._job(self.project, task['task_id'])
        before = path.read_bytes()
        workers = len(self.workers)
        with patch.object(cloud, 'get_config', side_effect=HTTPException(409, 'Vibe API 配置不可用')):
            with self.assertRaises(HTTPException) as denied:
                cloud.resume_materials(self.identity, 'shot1', task['task_id'], self.req,
                                       cloud.ResumeMaterialsRequest(confirm_model=True))
        self.assertEqual(denied.exception.status_code, 409)
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(cloud._active)
        self.assertEqual(len(self.workers), workers)
        with patch.object(cloud, 'get_config', side_effect=RuntimeError('configuration read failed')):
            with self.assertRaisesRegex(RuntimeError, 'configuration read failed'):
                cloud.resume_materials(self.identity, 'shot1', task['task_id'], self.req,
                                       cloud.ResumeMaterialsRequest(confirm_model=True))
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(cloud._active)
        self.assertEqual(len(self.workers), workers)

    def test_resume_missing_key_preserves_failed_state_and_original_material_task(self):
        task = self.prepare_failed_material()
        path = cloud._job(self.project, task['task_id'])
        before = path.read_bytes()
        self.config['api_key'] = ''
        with self.assertRaises(HTTPException) as denied:
            cloud.resume_materials(self.identity, 'shot1', task['task_id'], self.req,
                                   cloud.ResumeMaterialsRequest(confirm_model=True))
        self.assertEqual(denied.exception.status_code, 409)
        self.assertIn('Vibe API', denied.exception.detail)
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(cloud._active)

    def test_confirmed_resume_uses_same_image_task_and_retains_v2_settings_and_quality(self):
        before = (self.project / 'record.json').read_bytes()
        self.inventory_decorations=[dict(kind='arrow',description='两组之间保留双向比较箭头',
                                        source_box=[.44,.45,.56,.55])]
        task = self.prepare_failed_material()
        inventory_file=cloud._job(self.project,task['task_id']).parent/'inventory.json'
        saved_inventory=json.loads(inventory_file.read_text(encoding='utf-8'))
        self.model.reset_mock()
        with patch.object(layered, 'resume_asset', side_effect=self.recovered_asset) as recover:
            queued = cloud.resume_materials(self.identity, 'shot1', task['task_id'], self.req,
                                           cloud.ResumeMaterialsRequest(confirm_model=True))
            self.assertEqual(queued['task_id'], task['task_id'])
            self.assertEqual(queued['workflow_version'], workflow.VERSION)
            self.assertEqual(queued['status'], 'queued')
            self.assertEqual(queued['stage'], 'materials')
            self.assertTrue(cloud._active)
            self.assertIs(self.workers[-1].target, layered.run_worker)
            self.assertIs(self.workers[-1].args[-1], True)
            completed = self.execute()
        self.assertEqual(completed['status'], 'completed', completed)
        self.assertEqual(completed['preview']['workflow_version'], workflow.VERSION)
        self.assertEqual(completed['preview']['quality_status'], 'passed')
        self.assertEqual(self.finishes[-1]['instruction'], '配合字幕逐层入场')
        self.assertEqual(self.finishes[-1]['pace'], 'slow')
        self.assertEqual(self.finishes[-1]['config']['api_key'], self.config['api_key'])
        self.assertEqual(self.finishes[-1]['context']['inventory_notes'],{
            'foreground_summary':saved_inventory['foreground_summary'],
            'subjects':[{'id':f'subject_{index}','name':subject['name'],
                         'role':subject.get('role','object'),
                         'evidence_note':subject.get('evidence_note')}
                        for index,subject in enumerate(saved_inventory['subjects'])],
            'background_retained':[],
            'decorations':[{'kind':'arrow','description':'两组之间保留双向比较箭头',
                            'source_box':[.44,.45,.56,.55],'coordinate_space':'core'}]})
        self.assertEqual(completed['usage']['image_task_ids'], [self.studio_id])
        self.assertEqual(completed['usage']['image_requests'], 1)
        recover.assert_called_once()
        self.model.assert_not_called()  # Inventory is reused; finish owns the next paid model stages.
        cloud.generate_asset.assert_not_called()
        self.assertEqual((self.project / 'record.json').read_bytes(), before)
        self.assertFalse(cloud._active)

    def test_resume_changed_inventory_retention_cannot_use_previous_background_layout(self):
        task=self.prepare_failed_material()
        inventory_file=cloud._job(self.project,task['task_id']).parent/'inventory.json'
        saved=json.loads(inventory_file.read_text(encoding='utf-8'))
        saved['background_retained']=[dict(name='额外留在底图的透明示意物',
            source_box=[.1,.1,.3,.5],content_kind='illustration',reason='边缘透射依赖原背景，希望保留而不再抠图。')]
        saved['foreground_summary']['background_retained_count']=1
        cloud._write(inventory_file,saved)
        self.model.reset_mock()
        with patch.object(layered,'resume_asset',side_effect=self.recovered_asset) as recover:
            cloud.resume_materials(self.identity,'shot1',task['task_id'],self.req,
                                   cloud.ResumeMaterialsRequest(confirm_model=True))
            failed=self.execute()
        self.assertEqual(failed['status'],'failed')
        self.assertIn('素材清单与原背景保留方案不一致',failed['message'])
        recover.assert_not_called()
        self.model.assert_not_called()
        cloud.generate_asset.assert_not_called()
        self.pipeline.assert_not_called()

    def test_resume_without_inventory_file_keeps_older_layered_task_compatible(self):
        before=(self.project/'record.json').read_bytes()
        task=self.prepare_failed_material()
        # Historical layered jobs already have an internal layout but did not
        # persist the new material-only inventory contract.
        inventory_file=cloud._job(self.project,task['task_id']).parent/'inventory.json'
        inventory_file.unlink()
        self.model.reset_mock()
        with patch.object(layered,'resume_asset',side_effect=self.recovered_asset) as recover:
            cloud.resume_materials(self.identity,'shot1',task['task_id'],self.req,
                                   cloud.ResumeMaterialsRequest(confirm_model=True))
            completed=self.execute()
        self.assertEqual(completed['status'],'completed',completed)
        self.assertNotIn('inventory_notes',self.finishes[-1]['context'])
        self.assertEqual(completed['usage']['image_task_ids'],[self.studio_id])
        self.assertEqual(completed['usage']['image_requests'],1)
        self.assertFalse(inventory_file.exists(),'recovery must not fabricate a new inventory')
        recover.assert_called_once()
        self.model.assert_not_called()
        cloud.generate_asset.assert_not_called()
        self.assertEqual((self.project/'record.json').read_bytes(),before)
        self.assertFalse(cloud._active)


if __name__ == '__main__':
    unittest.main()
