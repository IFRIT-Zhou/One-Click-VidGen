"""Exercise the actual single-shot route and material ownership/consent boundaries offline."""
import unittest
import os
import copy
import json
from pathlib import Path
from unittest.mock import patch
from starlette.requests import Request
from fastapi import HTTPException
from backend.app import vibe_cloud as cloud, vibe_layered as layered
from backend.app import image_studio
from backend.app.vibe_materials import MaterialError
import test_vibe_cloud as legacy


class LayeredPipeline(unittest.TestCase):
    persist=legacy.VibeCloudTests.persist
    render=staticmethod(legacy.VibeCloudTests.render)
    material=staticmethod(legacy.VibeCloudTests.material)

    def setUp(self):
        legacy.VibeCloudTests.setUp(self)
        # Existing v1/recovery compatibility tests; v2 stages are covered by
        # test_vibe_layered_workflow and test_vibe_layered_assembly.
        legacy_mode=patch.object(layered,'WORKFLOW_VERSION',None)
        legacy_mode.start();self.addCleanup(legacy_mode.stop)
        route_patch=patch('backend.app.video_studio._image_configs',return_value=[dict(
            endpoint='https://example.invalid/generate',query_url='https://example.invalid/query',
            api_key='offline-image-key',model='personal-image',resolution='2k',ratio='16:9')])
        route_patch.start();self.addCleanup(route_patch.stop)
        self.req=Request({'type':'http','headers':[]})
        self.design=dict(background='solid',background_is_uniform=True,background_color='#253446',
            subjects=[dict(box=[0,.05,.5,.9],source='ref_03',treatment='preserve',at=0)],
            labels=[dict(box=[.52,.1,.96,.45],text='第一组',color='#FAFAFA',font_size=42,at=.2)])

    def run_job(self,budget,raw):
        def render(plan,canvas,renderer,assets,audio,start,duration,out,ffmpeg,timeout,cancelled):
            self.assertEqual(renderer,layered.RENDERER)
            self.assertTrue(assets['subject_0'].is_file())
            self.assertIn('label_1',[e['id'] for e in plan['elements']])
            return self.render(start=start,duration=duration,cues={'s1':0,'s2':1},audio=audio,output_dir=out)
        before=(self.project/'record.json').read_bytes()
        task=cloud.start(self.identity,'shot1',cloud.CloudRequest(confirm_cloud=True,template_mode='layered',max_image_requests=budget),self.req)
        cloud.preflight_remotion.assert_called_with(required_renderer=layered.RENDERER,required_plan_version=layered.VERSION)
        responses=raw if isinstance(raw,list) else [raw]*3
        with patch.object(cloud,'chat_json',side_effect=[legacy.VibeCloudTests.response(r) for r in responses]) as model,patch.object(layered,'render_compiled',side_effect=render):
            self.workers[-1].target(*self.workers[-1].args)
            self.assertEqual(set(model.call_args.args[3]),{'core','ref_03'})
            self.assertEqual(model.call_args.args[2]['subtitles'][0]['start'],0)
            self.last_model_payload=model.call_args.args[2]
        self.assertEqual(before,(self.project/'record.json').read_bytes())
        self.assertFalse(cloud._active)
        return cloud.latest(self.identity,'shot1',self.req)

    def test_existing_layers_do_not_submit_image_job(self):
        self.design['background']='ref_03'
        result=self.run_job(1,self.design)
        self.assertEqual(result['status'],'completed',result)
        self.assertEqual(result['preview']['template_mode'],'layered')
        cloud.generate_asset.assert_not_called()

    def test_background_receives_project_image_snapshot_and_safe_receipt(self):
        self.design['background']='extract'
        result=self.run_job(1,self.design)
        self.assertEqual(result['status'],'completed',result)
        kwargs=cloud.generate_asset.call_args.kwargs
        self.assertEqual(kwargs['image_config']['model'],'personal-image')
        self.assertEqual(kwargs['image_route']['provider'],'custom')
        self.assertEqual(result['image_route'],kwargs['image_route'])
        self.assertNotIn('offline-image-key',json.dumps(result))

    def test_changed_route_since_confirmation_stops_before_worker(self):
        with self.assertRaises(HTTPException) as failure:
            cloud.start(self.identity,'shot1',cloud.CloudRequest(confirm_cloud=True,
                template_mode='layered',max_image_requests=1,image_route_key='f'*64),self.req)
        self.assertEqual(failure.exception.status_code,409)
        self.assertFalse(self.workers)
        cloud.generate_asset.assert_not_called()

    def test_new_route_requires_explicit_acknowledgment_of_pending_old_task(self):
        with patch.object(layered,'recoverable_material',return_value=True), \
             patch('backend.app.vibe_image_route.same_pending_route',return_value=False):
            first=cloud.start(self.identity,'shot1',cloud.CloudRequest(confirm_cloud=True,
                template_mode='layered',max_image_requests=1),self.req)
            cloud._active.clear()
            with self.assertRaises(HTTPException) as failure:
                cloud.start(self.identity,'shot1',cloud.CloudRequest(confirm_cloud=True,
                    template_mode='layered',max_image_requests=1),self.req)
            self.assertEqual(failure.exception.status_code,409)
            second=cloud.start(self.identity,'shot1',cloud.CloudRequest(confirm_cloud=True,
                template_mode='layered',max_image_requests=1,confirm_new_image_task=True),self.req)
            self.assertNotEqual(first['task_id'],second['task_id'])

    def test_echoed_transport_marker_is_accepted_without_extra_model_call(self):
        self.design['type']='json_object'
        before=copy.deepcopy(self.design)
        result=self.run_job(0,self.design)
        self.assertEqual(result['status'],'completed',result)
        self.assertEqual(len(result['usage']['model_calls']),1)
        self.assertEqual(self.design,before)
        folder=cloud._job(self.project,result['task_id']).parent
        raw=json.loads((folder/'design_response.json').read_text(encoding='utf-8'))
        layout=json.loads((folder/'layout.json').read_text(encoding='utf-8'))
        self.assertEqual(raw['type'],'json_object')
        self.assertNotIn('type',layout)
        cloud.generate_asset.assert_not_called()

    def test_independent_plate_does_not_spend_a_repair_call(self):
        self.design['labels'].append(dict(box=[.5,.5,.95,.8],text='',at=.2,
            plate=dict(color='#06203A',gradient_to='#0A4E7A',stroke='#4DB6E8',radius=28,glow=8)))
        result=self.run_job(0,self.design)
        self.assertEqual(result['status'],'completed',result)
        self.assertEqual(len(result['usage']['model_calls']),1)
        self.assertEqual(result['usage']['model_calls'][0]['round_label'],'初次编排')
        self.assertEqual(result['attempt'],0)
        cloud.generate_asset.assert_not_called()

    def test_invalid_response_is_retained_without_material_generation(self):
        self.design['unexpected']='invalid field'
        with patch.object(layered.rmbg,'run_cutouts') as cutouts:
            result=self.run_job(1,self.design)
        self.assertEqual(result['status'],'failed')
        folder=cloud._job(self.project,result['task_id']).parent
        self.assertEqual(json.loads((folder/'design_response.json').read_text(encoding='utf-8')),self.design)
        self.assertFalse((folder/'layout.json').exists())
        self.assertEqual(len(result['usage']['model_calls']),3)
        cutouts.assert_not_called()
        cloud.generate_asset.assert_not_called()

    def test_other_unknown_and_nested_fields_remain_invalid(self):
        for fields in ({'type':'json_schema'},{'type':'JSON_OBJECT'},
                       {'type':None},{'type':'json_object','response_format':{}},
                       {'unexpected':'value'}):
            with self.subTest(fields=fields),self.assertRaises(ValueError):
                layered.Design.model_validate({**self.design,**fields})
        for key in ('subjects','labels'):
            raw=copy.deepcopy(self.design)
            raw[key][0]['type']='json_object'
            with self.subTest(key=key),self.assertRaises(ValueError):
                layered.Design.model_validate(raw)
        schema=layered.Design.model_json_schema()
        self.assertFalse(schema['additionalProperties'])
        self.assertNotIn('type',schema['properties'])

    def test_cutout_routes_to_standalone_without_starting_engine(self):
        self.design['subjects'][0]['treatment']='cutout'
        with patch.object(layered.rmbg,'run_cutouts',side_effect=lambda sources,*a,**kw: sources) as run:
            task=self.run_job(0,self.design)
        self.assertEqual(task['status'],'completed',task)
        self.assertEqual(set(run.call_args.args[0]),{'subject_0'})
        self.assertTrue(callable(run.call_args.kwargs['should_stop']))
        cloud.generate_asset.assert_not_called()

    def test_extract_requires_explicit_budget(self):
        self.design['background']='extract'
        self.assertIn('允许一次',self.run_job(0,self.design)['message'])
        cloud.generate_asset.assert_not_called()

    def test_extract_submits_once(self):
        self.design['background']='extract'
        result=self.run_job(1,self.design)
        self.assertEqual(result['status'],'completed',result)
        self.assertEqual(result['usage']['image_requests'],1)

    def test_core_is_extraction_input_not_a_missing_background(self):
        self.design['background']='core'
        result=self.run_job(1,self.design)
        self.assertEqual(result['status'],'completed',result)
        self.assertEqual(result['usage']['image_requests'],1)
        cloud.generate_asset.assert_called_once()

    def test_decorated_solid_is_not_flattened(self):
        self.design['background_notes']='深蓝渐变，网络节点与粒子光带'
        result=self.run_job(1,self.design)
        self.assertEqual(result['status'],'completed',result)
        cloud.generate_asset.assert_called_once()
        self.assertIn('不简化成纯色',cloud.generate_asset.call_args.args[1])

    def test_implicit_solid_without_evidence_requires_consent(self):
        del self.design['background_is_uniform']
        result=self.run_job(0,self.design)
        self.assertEqual(result['status'],'failed')
        self.assertIn('原图有背景',result['message'])
        cloud.generate_asset.assert_not_called()

    def test_invalid_region_gets_specific_bounded_repair_before_materials(self):
        broken=copy.deepcopy(self.design)
        broken['labels'][0]['box']=[.5,.04,.5,.16]
        result=self.run_job(0,[broken,self.design])
        self.assertEqual(result['status'],'completed',result)
        self.assertEqual(len(result['usage']['model_calls']),2)
        self.assertEqual(result['attempt'],1)
        self.assertIn('labels[0].box',self.last_model_payload['validation_errors'])
        self.assertEqual(self.last_model_payload['previous_design'],broken)
        folder=cloud._job(self.project,result['task_id']).parent
        self.assertEqual(json.loads((folder/'design_response_0.json').read_text(encoding='utf-8')),broken)
        cloud.generate_asset.assert_not_called()

    def test_plate_and_text_have_independent_boxes_and_faithful_style(self):
        self.design['labels'][0].update(valign='middle',gradient_to='#B0EEFF',
            plate=dict(box=[.5,.05,.98,.7],color='#06253A',gradient_to='#10456A',
                       stroke='#39DAEE',stroke_width=2,radius=18,glow=6))
        plan=layered.compile_plan(layered.Design.model_validate(self.design),
            {'canvas':[1280,720],'duration':5},{'ref_03':Path('unused')})
        plate,text=plan['elements'][-2:]
        self.assertNotEqual(plate['box'],text['box'])
        self.assertEqual(text['text'],'第一组')
        self.assertEqual(text['valign'],'middle')
        self.assertLess(text['min_font_size'],text['font_size'])
        self.assertEqual(plate['gradient']['color'],'#10456A')
        self.assertEqual(plate['stroke'],'#39DAEE')
        self.assertEqual(plate['glow'],6)

    def test_preserved_card_text_cannot_be_overlaid_twice(self):
        self.design['subjects'][0].update(box=[0,0,1,.8],embedded_text=['第一组'])
        with self.assertRaisesRegex(ValueError,'重复叠字'):
            layered.compile_plan(layered.Design.model_validate(self.design),
                {'canvas':[1280,720],'duration':5},{'ref_03':Path('unused')})
        # The same institution can legitimately have a caption outside a photo.
        self.design['labels'][0]['box']=[.1,.82,.9,.95]
        plan=layered.compile_plan(layered.Design.model_validate(self.design),
            {'canvas':[1280,720],'duration':5},{'ref_03':Path('unused')})
        self.assertEqual(plan['elements'][-1]['text'],'第一组')

    def test_resume_reuses_original_design_and_image_id_without_paid_calls(self):
        from PIL import Image
        studio_id='a'*32
        self.design['background']='extract'
        with patch.object(image_studio,'ROOT',self.root/'images'):
            def pending(user,prompt,refs,out,name,cancelled,**kw):
                path=image_studio.folder(user,studio_id);path.mkdir(parents=True)
                image_studio.save(path,dict(id=studio_id,status='failed',remote_task_id='remote-original',message='等待超时'))
                kw['on_submitted'](studio_id)
                raise MaterialError('等待超时',studio_id)
            with patch.object(cloud,'generate_asset',side_effect=pending) as generate:
                failed=self.run_job(1,self.design)
                self.assertTrue(failed['can_resume_materials'])
                other=Request({'type':'http','headers':[(b'user',b'2')]})
                with self.assertRaises(HTTPException) as denied:
                    cloud.resume_materials(self.identity,'shot1',failed['task_id'],other)
                self.assertEqual(denied.exception.status_code,404)
                old_intent=self.shot['intent'];self.shot['intent']='修改后的镜头内容';self.persist()
                with self.assertRaises(HTTPException) as stale:
                    cloud.resume_materials(self.identity,'shot1',failed['task_id'],self.req)
                self.assertEqual(stale.exception.status_code,409)
                self.shot['intent']=old_intent;self.persist()
                cloud._update(cloud._job(self.project,failed['task_id']),status='running')
                restarted=cloud.latest(self.identity,'shot1',self.req)
                self.assertEqual(restarted['status'],'stopped')
                self.assertTrue(restarted['can_resume_materials'])
                with self.assertRaises(HTTPException) as conflict:
                    cloud.start(self.identity,'shot1',cloud.CloudRequest(confirm_cloud=True,template_mode='layered',max_image_requests=1),self.req)
                self.assertEqual(conflict.exception.status_code,409)
                self.assertIn('继续原素材任务',conflict.exception.detail)
                def resumed(user,identity,out,name,cancelled):
                    self.assertEqual((user,identity),(1,studio_id))
                    file=out/(name+'.png');Image.new('RGB',(640,360),'navy').save(file)
                    return {'path':file,'studio_task_id':identity}
                def render(plan,canvas,renderer,assets,audio,start,duration,out,ffmpeg,timeout,cancelled):
                    return self.render(start=start,duration=duration,cues={'s1':0,'s2':1},audio=audio,output_dir=out)
                with (patch.object(cloud,'chat_json') as model,patch.object(cloud,'get_config',side_effect=AssertionError('Recovery must not need DS')),
                      patch.object(layered,'resume_asset',side_effect=resumed) as recover,patch.object(layered,'render_compiled',side_effect=render)):
                    response=cloud.resume_materials(self.identity,'shot1',failed['task_id'],self.req)
                    self.assertEqual(response['task_id'],failed['task_id'])
                    self.workers[-1].target(*self.workers[-1].args)
                    result=cloud.latest(self.identity,'shot1',self.req)
                self.assertEqual(result['status'],'completed',result)
                self.assertEqual(len(result['usage']['model_calls']),1)
                self.assertEqual(result['usage']['image_requests'],1)
                self.assertEqual(generate.call_count,1)
                recover.assert_called_once();model.assert_not_called()

    def test_unbound_sources_never_reach_image_or_render(self):
        self.design['subjects'][0]['source']='ref_04'
        self.assertIn('未绑定',self.run_job(1,self.design)['message'])
        cloud.generate_asset.assert_not_called()

    def test_templates_and_implicit_budget_rejected(self):
        for kw in ({'template_ids':['card-stack'],'max_image_requests':1},{},{'max_image_requests':5}):
            with self.assertRaises(ValueError):
                cloud.CloudRequest(confirm_cloud=True,template_mode='layered',**kw)

    @unittest.skipUnless(os.environ.get('OCV_TEST_REAL_LAYERED_RENDER')=='1','requires installed local Remotion and FFmpeg')
    def test_real_preview_with_original_audio(self):
        import subprocess
        from backend.app.vibe_remotion import preflight_remotion
        bin_dir=Path(__file__).resolve().parents[1]/'tools'/'ffmpeg'/'bin'
        with patch.object(cloud,'preflight_remotion',preflight_remotion),patch.object(cloud,'ffmpeg_binary',return_value=str(bin_dir/'ffmpeg.exe')),patch.object(cloud,'chat_json',return_value=legacy.VibeCloudTests.response(self.design)):
            cloud.start(self.identity,'shot1',cloud.CloudRequest(confirm_cloud=True,template_mode='layered',max_image_requests=0),self.req)
            self.workers[-1].target(*self.workers[-1].args)
        task=cloud.latest(self.identity,'shot1',self.req)
        self.assertEqual(task['status'],'completed',task['message'])
        movie=cloud.previews._store(self.project)/task['preview']['preview_id']/'preview.mp4'
        streams=subprocess.check_output([str(bin_dir/'ffprobe.exe'),'-v','error','-show_entries','stream=codec_name','-of','csv=p=0',str(movie)])
        self.assertIn(b'h264',streams);self.assertIn(b'aac',streams)
