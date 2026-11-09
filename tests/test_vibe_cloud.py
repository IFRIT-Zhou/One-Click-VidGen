"""Offline cloud orchestration contracts; every paid call and renderer is mocked."""
import copy
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
import wave

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from backend.app import image_layer_packs as packs
from backend.app import vibe_cloud as api
from backend.app import vibe_cloud_client as client
from backend.app import vibe_preview as previews
from backend.app.vibe_materials import MaterialError, MaterialWaitStopped


class VibeCloudTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.identity = 'b' * 32
        self.project = self.root / '1' / self.identity
        self.project.mkdir(parents=True)
        for name, colour in [('core.png', '#154060'), ('ref3.png', '#317951'), ('ref4.png', '#B63322')]:
            Image.new('RGB', (640, 360), colour).save(self.project / name)
        with wave.open(str(self.project / 'narration.wav'), 'wb') as out:
            out.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            out.writeframes(b'\x15\x00' * 64000)
        self.cues = [dict(slide_id='s1', text='第一组的结果。', start=1, end=2),
                     dict(slide_id='s2', text='第二组的结果。', start=2, end=3.5)]
        self.shot = dict(id='shot1', image='core.png', image_status='completed',
                         start=1, end=3.5, slide_ids=['s1', 's2'], kind='dynamic',
                         reference_ids=['ref_03'], intent='解释两组结果')
        self.record = dict(audio='narration.wav', shots=[self.shot], scenes=self.cues,
                           references=[{'id': 'ref_03', 'file': 'ref3.png'},
                                       {'id': 'ref_04', 'file': 'ref4.png'}])
        self.persist()
        self.config = dict(api_key='offline-test-key-do-not-store', base_url='https://example.invalid',
                           model='test-vision', max_image_requests=4)
        self.workers = []
        workers = self.workers

        class CapturedThread:
            def __init__(self, target, args, daemon):
                self.target, self.args = target, args
                workers.append(self)

            def start(self):
                pass

        for item in [
            patch.object(packs.studio, 'ROOT', self.root),
            patch.object(packs, 'require_user', side_effect=lambda request: {'id': int(request.headers.get('user', '1'))}),
            patch.object(api, 'require_user', side_effect=lambda request: {'id': int(request.headers.get('user', '1'))}),
            patch.object(api, 'get_config', side_effect=lambda _: copy.deepcopy(self.config)),
            patch.object(api, '_active', {}),
            patch.object(api, 'threading', SimpleNamespace(Event=threading.Event, Thread=CapturedThread)),
            patch.object(api, 'ffmpeg_binary', return_value='offline-unused-ffmpeg'),
            patch.object(api, 'preflight_remotion', return_value={'renderer': 'ocv-remotion-v1'}),
            patch.object(api, 'render_scene', side_effect=self.render),
            patch.object(api, 'generate_asset', side_effect=self.material),
        ]:
            item.start()
            self.addCleanup(item.stop)
        app = FastAPI()
        app.include_router(api.router)
        app.include_router(previews.router)
        self.http = TestClient(app)
        self.addCleanup(self.http.close)
        self.base = f'/api/video-studio/{self.identity}/shots/shot1'

    def persist(self):
        (self.project / 'record.json').write_text(json.dumps(self.record, ensure_ascii=False), encoding='utf-8')

    def proposal(self, materials=None):
        return dict(summary='按配音呈现研究结果', materials=materials or [],
                    source_style=dict(source='core',style_notes='原图深蓝背景，青绿色强调与浅色文字',
                        palette=dict(background='#154060',ink='#FFFFFF',muted='#C6D8E4',surface='#214A66',
                                     line='#58768D',accent='#78D7CC'), background=dict(kind='technical_grid')),
                    visual_basis=dict(mode='native', style_notes='输入为纯数据图表，保留颜色并原生重排',
                        elements=[dict(label='两组研究结果', source='core', kind='chart', treatment='native')]),
                    scene=dict(canvas=[1920, 1080], title='研究结果', stages=[
                        dict(id='results', layout='findings', cue='s1', headline='两组结果',
                             items=[dict(id='group1', title='第一组'), dict(id='group2', title='第二组')])]))

    @staticmethod
    def response(proposal):
        return proposal, {'model': 'offline-test', 'total_tokens': 10}

    def start(self, **data):
        result = self.http.post(self.base + '/vibe-cloud-jobs', json={'confirm_cloud': True, **data})
        self.assertEqual(result.status_code, 202, result.text)
        return result.json()

    def finish(self):
        worker = self.workers[-1]
        worker.target(*worker.args)
        return self.http.get(self.base + '/vibe-cloud-job/latest').json()

    @staticmethod
    def render(**kwargs):
        assert kwargs['start'] == 0
        assert kwargs['duration'] == 2.5
        assert kwargs['cues'] == {'s1': 0, 's2': 1}
        with wave.open(str(kwargs['audio']), 'rb') as audio:
            assert audio.getnframes() == 40000
        out = kwargs['output_dir']
        for name in ('preview.mp4', 'silent.mp4'):
            (out / name).write_bytes(b'ftyp-offline-mocked-video')
        Image.new('RGB', (160, 90), '#345678').save(out / 'proof.jpg')
        for name in ('detail_early.png', 'detail_middle.png', 'detail_end.png'):
            Image.new('RGB', (1920, 1080), '#345678').save(out / name)
        return dict(frames=75, duration_seconds=2.5, proof_times=[.5, 1.5, 2.2],
                    detail_times={'early': .5, 'middle': 1.25, 'end': 1.9}, checks={})

    @staticmethod
    def material(user_id, prompt, references, output_dir, name, cancelled, **kwargs):
        assert user_id == 1
        target = output_dir / (name + '.png')
        Image.new('RGB', (100, 100), '#506070').save(target)
        kwargs['on_submitted']('studio-' + name)
        return {'path': target}

    def test_only_explicit_boolean_true_authorizes_a_job(self):
        for payload in ({}, {'confirm_cloud': False}, {'confirm_cloud': None},
                        {'confirm_cloud': 'true'}, {'confirm_cloud': 1}):
            with self.subTest(payload=payload):
                response = self.http.post(self.base + '/vibe-cloud-jobs', json=payload)
                self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.workers, [])

    def test_missing_key_and_wrong_owner_never_create_job(self):
        self.config['api_key'] = ''
        self.assertEqual(self.http.post(self.base + '/vibe-cloud-jobs', json={'confirm_cloud': True}).status_code, 409)
        self.config['api_key'] = 'test'
        self.assertEqual(self.http.post(self.base + '/vibe-cloud-jobs', json={'confirm_cloud': True}, headers={'user': '2'}).status_code, 404)
        self.assertEqual(self.workers, [])

    def test_missing_remotion_runtime_blocks_before_any_billable_call(self):
        with patch.object(api, 'preflight_remotion', side_effect=RuntimeError('Remotion 组件未安装')), \
             patch.object(api, 'chat_json') as model:
            response = self.http.post(self.base + '/vibe-cloud-jobs', json={'confirm_cloud': True})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn('Remotion', response.text)
        self.assertEqual(self.workers, [])
        model.assert_not_called()
        api.generate_asset.assert_not_called()

    def test_completed_preview_is_reusable_private_and_does_not_adopt(self):
        before = (self.project / 'record.json').read_bytes()
        with patch.object(api, 'chat_json', side_effect=[self.response(self.proposal()), self.response({'passed': True, 'issues': []})]) as model:
            self.start()
            task = self.finish()
            self.assertEqual(task['status'], 'completed', task)
            receipt = task['preview']
            self.assertEqual(receipt['engine'], 'cloud')
            self.assertEqual(receipt['renderer'], 'ocv-remotion-v1')
            self.assertEqual(receipt['visual_basis']['mode'], 'native')
            self.assertEqual(receipt['material_policy'],'original_inputs_only')
            self.assertEqual(receipt['max_image_requests'],0)
            self.assertEqual(receipt['source_style'],self.proposal()['source_style'])
            self.assertEqual(receipt['plan']['palette'],receipt['source_style']['palette'])
            self.assertEqual(receipt['plan']['background'],receipt['source_style']['background'])
            self.assertEqual(task['renderer'], 'ocv-remotion-v1')
            self.assertEqual(receipt['shot_id'], 'shot1')
            self.assertEqual(model.call_count, 2)
            self.assertEqual(model.call_args_list[0].kwargs['max_tokens'], 8000)
            self.assertEqual(model.call_args_list[0].args[2]['display_asset_ids'],['ref_03'])
            self.assertEqual(model.call_args_list[0].args[2]['timing_example'],
                             api._timing_example({'s1':0,'s2':1}))
            self.assertNotIn('box', json.dumps(model.call_args_list[0].args[2]['schema']['$defs']['MotionItem']))
            review_args=model.call_args_list[1].args
            self.assertEqual(review_args[1], api.REVIEWER)
            self.assertEqual(review_args[2]['visual_basis']['mode'], 'native')
            self.assertEqual(review_args[2]['source_style'],receipt['source_style'])
            self.assertEqual(review_args[2]['review_reference']['stage_windows'],
                             [{'id':'results','start':0.0,'end':2.5}])
            self.assertIn('分步呈现', review_args[1])
            self.assertIn('图标压字', review_args[1])
            self.assertIn('render_detail_early', review_args[3])
            self.assertIn('render_detail_middle', review_args[3])
            self.assertIn('render_detail_end', review_args[3])
            self.assertTrue(self.http.get(self.base + '/vibe-preview/latest').json()['available'])
            for key in ('preview_url', 'silent_url', 'proof_url'):
                self.assertEqual(self.http.get(receipt[key]).status_code, 200)
                self.assertEqual(self.http.get(receipt[key], headers={'user': '2'}).status_code, 404)
            for _ in range(3):
                self.assertEqual(self.http.get(self.base + '/vibe-cloud-job/latest').json()['preview']['preview_id'], receipt['preview_id'])
            self.assertEqual(model.call_count, 2, 'Polling must not reissue paid calls')
        self.assertEqual((self.project / 'record.json').read_bytes(), before)
        for file in (self.project / 'assets').rglob('*.json'):
            self.assertNotIn(self.config['api_key'], file.read_text(encoding='utf-8'))
        self.assertFalse(api._active)

    def test_empty_materials_echoed_inside_scene_does_not_spend_a_repair_call(self):
        proposal=self.proposal();proposal['scene']['materials']=[]
        proposal['visual_basis']['elements_note']=''
        before=copy.deepcopy(proposal)
        with patch.object(api,'chat_json',side_effect=[self.response(proposal),self.response({'passed':True})]) as model:
            self.start();task=self.finish()
        self.assertEqual(task['status'],'completed',task)
        self.assertEqual(model.call_count,2)
        self.assertNotIn('materials',task['preview']['plan'])
        self.assertEqual(proposal,before)
        api.generate_asset.assert_not_called()

    def test_core_poster_cannot_be_claimed_as_extracted_subject_in_code_only_cloud(self):
        for location in ('visual','item'):
            raw=self.proposal()
            raw['visual_basis']=dict(mode='hybrid',style_notes='声称从core取出主体',
                elements=[dict(label='肝脏示意',source='core',kind='subject',treatment='preserve')])
            stage=raw['scene']['stages'][0]
            if location=='visual':stage['visual']={'asset_id':'core'}
            else:stage['items'][0]['asset_id']='core'
            proposal=api.Proposal.model_validate(raw)
            with self.subTest(location=location),self.assertRaisesRegex(ValueError,'core 仅供理解风格/内容'):
                api._validate_code_only_plan(proposal,{'core','ref_03'},{'s1':0,'s2':1},2.5)
            # This cloud policy does not invalidate legacy/offline motion plans.
            api.compile_motion_plan(proposal.scene,{'s1':0,'s2':1},2.5,{'core','ref_03'})

    def test_core_only_evidence_cannot_be_silently_omitted(self):
        raw=self.proposal()
        raw['visual_basis']['elements'].append(dict(label='研究切片',source='core',kind='evidence',
            treatment='omit',reason='没有独立素材所以忽略'))
        with self.assertRaisesRegex(ValueError,'questions 请求对应原素材'):
            api._validate_code_only_plan(api.Proposal.model_validate(raw),{'core'},{'s1':0},2.5)

    def test_missing_independent_evidence_requests_input_without_render_or_extra_calls(self):
        raw=self.proposal();raw['scene']=None
        raw['questions']=['本镜需要海报中的组织切片，请提供独立原图参考素材。']
        with patch.object(api,'chat_json',return_value=self.response(raw)) as model:
            self.start();task=self.finish()
        self.assertEqual(task['stage'],'needs_input')
        self.assertIn('独立原图',task['message'])
        self.assertEqual(model.call_count,1)
        api.render_scene.assert_not_called()
        api.generate_asset.assert_not_called()

    def test_over_budget_proposal_is_blocked_before_dispatch(self):
        materials = [dict(id=f'mat_{i}', mode='illustration', prompt=f'装饰图 {i}') for i in range(5)]
        with patch.object(api, 'chat_json', return_value=self.response(self.proposal(materials))) as model:
            self.start()
            task = self.finish()
        self.assertEqual(task['status'], 'failed', task)
        self.assertIn('materials 必须为空',task['message'])
        self.assertEqual(task['usage']['image_requests'], 0)
        self.assertEqual(len(task['usage']['image_task_ids']), 0)
        api.generate_asset.assert_not_called()
        self.assertEqual(model.call_count, 3)
        self.assertFalse(previews._pointer(self.project, 'shot1').is_file())

    def test_zero_budget_request_and_legacy_positive_budget_both_have_zero_image_policy(self):
        for requested in (0,4,12):
            with self.subTest(requested=requested),patch.object(api,'chat_json',side_effect=[
                    self.response(self.proposal()),self.response({'passed':True})]) as model:
                started=self.start(max_image_requests=requested)
                finished=self.finish()
                self.assertEqual(started['max_image_requests'],0)
                self.assertEqual(finished['status'],'completed',finished)
                self.assertEqual(finished['usage']['image_requests'],0)
                payload=model.call_args_list[0].args[2]
                self.assertEqual(payload['max_image_requests'],0)
                self.assertEqual(payload['remaining_image_requests'],0)
                self.assertEqual(payload['available_material_specs'],{})
                self.assertEqual(set(model.call_args_list[0].args[3]),{'core','ref_03'})
                self.assertEqual(payload['original_asset_ids'],payload['asset_ids'])
        api.generate_asset.assert_not_called()

    def test_new_plan_missing_source_style_never_falls_back_to_default_white(self):
        raw=self.proposal();raw.pop('source_style')
        self.assertIsNone(api.Proposal.model_validate(raw).source_style)
        with patch.object(api,'chat_json',return_value=self.response(raw)) as model:
            self.start();task=self.finish()
        self.assertEqual(task['status'],'failed',task)
        self.assertIn('source_style',task['message'])
        self.assertIn('不能默默改成默认白底',task['message'])
        self.assertEqual(model.call_count,3)
        api.render_scene.assert_not_called()
        api.generate_asset.assert_not_called()

    def test_repair_cannot_replace_frozen_source_palette_or_background(self):
        first=self.proposal();repair=self.proposal()
        repair['source_style']['palette']['background']='#FFFFFF'
        repair['source_style']['background']['kind']='paper'
        repair['scene']['palette']={'background':'#FFFFFF','ink':'#000000'}
        repair['scene']['background']={'kind':'paper'}
        with patch.object(api,'chat_json',side_effect=[self.response(first),
                self.response({'passed':False,'issues':['第一条标签被遮挡']}),
                self.response(repair),self.response({'passed':True})]) as model:
            self.start();task=self.finish()
        self.assertEqual(task['status'],'completed',task)
        frozen=first['source_style']
        self.assertEqual(task['source_style'],frozen)
        self.assertEqual(task['preview']['source_style'],frozen)
        self.assertEqual(model.call_args_list[2].args[2]['frozen_source_style'],frozen)
        for call in api.render_scene.call_args_list:
            self.assertEqual(call.kwargs['plan'].palette.model_dump(),frozen['palette'])
            self.assertEqual(call.kwargs['plan'].background.model_dump(),frozen['background'])
        api.generate_asset.assert_not_called()

    def test_echoed_json_object_marker_does_not_trigger_paid_repair(self):
        proposal = self.proposal()
        proposal['type'] = 'json_object'
        with patch.object(api, 'chat_json', side_effect=[self.response(proposal),
                self.response({'passed': True, 'issues': []})]) as model:
            self.start()
            task = self.finish()
        self.assertEqual(task['status'], 'completed', task)
        self.assertEqual(model.call_count, 2, 'Only initial design and review, no repair')
        rendered_plan = api.render_scene.call_args.kwargs['plan'].model_dump()
        self.assertNotIn('type', rendered_plan)
        self.assertEqual(proposal['type'], 'json_object', 'Raw diagnostic response remains unchanged')

    def test_new_plan_missing_visual_basis_stops_before_any_paid_material(self):
        raw = self.proposal()
        raw.pop('visual_basis')
        # Old saved schemas remain readable; only a new worker requires it.
        self.assertIsNone(api.Proposal.model_validate(raw).visual_basis)
        with patch.object(api, 'chat_json', return_value=self.response(raw)) as model:
            self.start()
            task = self.finish()
        self.assertEqual(task['status'], 'failed', task)
        self.assertIn('visual_basis', task['message'])
        self.assertEqual(model.call_count, 3, 'Only the existing bounded correction budget is used')
        api.generate_asset.assert_not_called()
        api.render_scene.assert_not_called()
        self.assertEqual(task['usage']['image_requests'], 0)

    def test_nominal_hybrid_without_displayed_subject_blocks_before_paid_material(self):
        raw = self.proposal()
        raw['visual_basis'] = dict(mode='hybrid', style_notes='保留人物和配色',
            elements=[dict(label='人物', source='ref_03', kind='subject', treatment='preserve')])
        with patch.object(api, 'chat_json', return_value=self.response(raw)):
            self.start()
            task = self.finish()
        self.assertEqual(task['status'], 'failed', task)
        self.assertIn('未在 stage.visual.asset_id 或 item.asset_id 中实际显示', task['message'])
        api.generate_asset.assert_not_called()
        api.render_scene.assert_not_called()

    def test_hybrid_reuses_original_in_visual_slot_without_paid_image(self):
        raw = self.proposal()
        raw['source_notes'] = '保留原人物形象，文字按口播逐步出现'
        raw['visual_basis'] = dict(mode='hybrid', style_notes='保留人物与参考图配色',
            elements=[dict(label='人物', source='ref_03', kind='subject', treatment='preserve')])
        raw['scene']['stages'][0]['visual'] = dict(asset_id='ref_03', placement='left', treatment='contain')
        with patch.object(api, 'chat_json', side_effect=[self.response(raw), self.response({'passed': True})]) as model:
            self.start()
            task = self.finish()
        self.assertEqual(task['status'], 'completed', task)
        self.assertEqual(task['usage']['image_requests'], 0)
        api.generate_asset.assert_not_called()
        self.assertEqual(task['preview']['visual_basis']['mode'], 'hybrid')
        self.assertEqual(task['preview']['source_notes'], raw['source_notes'])
        self.assertEqual(api.render_scene.call_args.kwargs['plan'].stages[0].visual.asset_id, 'ref_03')
        self.assertIn('ref_03', model.call_args_list[0].args[2]['original_asset_ids'])

    def test_structured_review_preserves_concrete_failure_for_next_design(self):
        issue = dict(element='肝脏主体', time='1.0s / 5.4s（持续）', reason='抠图蒙版移位并残留半截文字')
        with patch.object(api, 'chat_json', side_effect=[self.response(self.proposal()),
                self.response({'passed': False, 'issues': [issue]}),
                self.response(self.proposal()), self.response({'passed': True})]) as model:
            self.start()
            task = self.finish()
        self.assertEqual(task['status'], 'completed', task)
        feedback = model.call_args_list[2].args[2]['feedback']
        for value in issue.values(): self.assertIn(value, feedback)
        self.assertNotIn('质量检查响应格式无效', feedback)
        self.assertEqual(model.call_count, 4)
        review_schema = model.call_args_list[1].args[2]['schema']
        self.assertEqual(review_schema['properties']['issues']['items']['type'], 'string')

    def test_legacy_material_validator_still_counts_paid_requests(self):
        materials = [dict(id=f'mat_{i}', mode='illustration', prompt=f'装饰图 {i}') for i in range(4)]
        count=api._validate_material_plan(api.Proposal.model_validate(self.proposal(materials)),
            {'core':self.project/'core.png','ref_03':self.project/'ref3.png'}, {}, {'s1':0,'s2':1},2.5,False)
        self.assertEqual(count,4)
        api.generate_asset.assert_not_called()

    def test_legacy_material_validator_keeps_crops_free(self):
        material = dict(id='mat_crop', mode='crop', source='ref_03', box=[.1, .1, .9, .9])
        count=api._validate_material_plan(api.Proposal.model_validate(self.proposal([material])),
            {'core':self.project/'core.png','ref_03':self.project/'ref3.png'}, {}, {'s1':0,'s2':1},2.5,False)
        self.assertEqual(count,0)
        api.generate_asset.assert_not_called()

    def test_forbidden_material_is_never_dispatched_even_if_provider_would_fail(self):
        material = dict(id='mat_a', mode='illustration', prompt='装饰元素')
        with patch.object(api, 'chat_json', return_value=self.response(self.proposal([material]))) as model, \
             patch.object(api, 'generate_asset', side_effect=MaterialError('上游失败')) as generate:
            self.start()
            task = self.finish()
        self.assertEqual(task['status'], 'failed')
        self.assertEqual(task['usage']['image_requests'], 0)
        self.assertEqual(generate.call_count, 0)
        self.assertEqual(model.call_count, 3)

    def test_all_material_bindings_checked_before_first_paid_image(self):
        materials = [dict(id='mat_ok', mode='illustration', prompt='合法装饰图'),
                     dict(id='mat_bad', mode='matte', source='unbound_private_file', prompt='非法来源')]
        with patch.object(api, 'chat_json', return_value=self.response(self.proposal(materials))):
            self.start()
            task = self.finish()
        self.assertEqual(task['status'], 'failed', task)
        self.assertEqual(task['usage']['image_requests'], 0)
        api.generate_asset.assert_not_called()

    def test_unknown_scene_asset_checked_before_generating_other_materials(self):
        proposal = self.proposal([dict(id='mat_ok', mode='illustration', prompt='合法装饰图')])
        proposal['scene']['stages'][0]['items'][0]['asset_id'] = 'unknown'
        with patch.object(api, 'chat_json', return_value=self.response(proposal)):
            self.start()
            task = self.finish()
        self.assertEqual(task['status'], 'failed', task)
        self.assertEqual(task['usage']['image_requests'], 0)
        api.generate_asset.assert_not_called()

    def test_two_repair_rounds_stay_code_only_without_image_billing(self):
        answers = []
        for _ in range(3):
            answers.extend([self.response(self.proposal()), self.response({'passed': False, 'issues': ['标签遮挡需要修正']})])
        with patch.object(api, 'chat_json', side_effect=answers) as model:
            self.start()
            task = self.finish()
        self.assertEqual(task['status'], 'failed', task)
        self.assertEqual(model.call_count, 6, 'Initial design + at most two repairs, each with review')
        self.assertEqual(api.generate_asset.call_count, 0)
        self.assertEqual(task['usage']['image_requests'], 0)
        self.assertFalse(previews._pointer(self.project, 'shot1').is_file())

    def test_invalid_scenes_use_only_three_design_attempts(self):
        bad = self.proposal()
        bad['scene']['stages'][0]['cue'] = 'missing-cue'
        with patch.object(api, 'chat_json', return_value=self.response(bad)) as model:
            self.start()
            task = self.finish()
        self.assertEqual(task['status'], 'failed', task)
        self.assertEqual(model.call_count, 3)
        api.render_scene.assert_not_called()

    def test_stop_before_worker_prevents_all_paid_requests(self):
        task = self.start()
        result = self.http.post(self.base + f"/vibe-cloud-jobs/{task['task_id']}/stop")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['status'],'stopping')
        self.assertEqual(self.http.get(self.base+'/vibe-cloud-job/latest').json()['status'],'stopping')
        with patch.object(api, 'chat_json') as model:
            task = self.finish()
        self.assertEqual(task['status'], 'stopped', task)
        model.assert_not_called()
        api.generate_asset.assert_not_called()

    def test_damaged_old_pointer_does_not_block_fresh_validated_request(self):
        pointer=api._pointer(self.project,'shot1')
        pointer.write_text('{bad json',encoding='utf-8')
        task=self.start()
        self.assertEqual(task['status'],'queued')
        self.assertNotIn('reuse_from',task)

    def test_stop_during_director_wait_remains_stopped_not_failed(self):
        task = self.start()

        def stopped(*args, **kwargs):
            self.http.post(self.base + f"/vibe-cloud-jobs/{task['task_id']}/stop")
            raise InterruptedError('用户已停止')

        with patch.object(api, 'chat_json', side_effect=stopped) as model:
            result = self.finish()
        self.assertEqual(result['status'], 'stopped', result)
        self.assertEqual(model.call_count, 1)
        api.render_scene.assert_not_called()

    def test_modified_subtitles_prevent_publish(self):
        def model(*args, **kwargs):
            if 'previous_proposal' in args[2]:
                return self.response(self.proposal())
            self.cues[0]['text'] = '保存了新的口播文字'
            self.persist()
            return self.response({'passed': True})

        with patch.object(api, 'chat_json', side_effect=model):
            self.start()
            task = self.finish()
        self.assertEqual(task['status'], 'failed', task)
        self.assertFalse(previews._pointer(self.project, 'shot1').is_file())

    def test_input_changed_while_queued_stops_before_paid_calls(self):
        self.start()
        self.shot['reference_ids'] = ['ref_04']
        self.persist()
        with patch.object(api, 'chat_json') as model:
            task = self.finish()
        self.assertEqual(task['status'], 'failed', task)
        model.assert_not_called()
        api.generate_asset.assert_not_called()

    def test_stop_after_render_does_not_review_or_publish(self):
        task = self.start()

        def render_and_stop(**kwargs):
            result = self.render(**kwargs)
            self.http.post(self.base + f"/vibe-cloud-jobs/{task['task_id']}/stop")
            return result

        with patch.object(api, 'chat_json', return_value=self.response(self.proposal())) as model, \
             patch.object(api, 'render_scene', side_effect=render_and_stop):
            result = self.finish()
        self.assertEqual(result['status'], 'stopped', result)
        self.assertEqual(model.call_count, 1)
        self.assertFalse(previews._pointer(self.project, 'shot1').is_file())

    def test_upstream_error_remains_inspectable_as_possibly_charged(self):
        with patch.object(api, 'chat_json', side_effect=client.CloudCallError('离线模拟上游失败')) as model:
            self.start()
            task = self.finish()
        self.assertEqual(task['status'], 'failed', task)
        self.assertEqual(model.call_count, 1)
        self.assertEqual(len(task['usage']['model_calls']), 1)
        self.assertTrue(task['usage']['model_calls'][0]['may_charge'])
        self.assertEqual(task['usage']['model_calls'][0]['status'], 'unconfirmed')

    def test_failed_design_phase_preserves_reported_usage_without_reasoning_text(self):
        raw_reasoning = 'sensitive-thought-must-not-persist'
        response = CloudClientTests.stream([
            {'choices': [{'delta': {'reasoning_content': raw_reasoning, 'content': '{invalid'}, 'finish_reason': 'stop'}]},
            {'usage': {'prompt_tokens': 17, 'completion_tokens': 8, 'total_tokens': 25}}])
        with patch.object(client.requests, 'post', return_value=response) as post, \
             patch.object(api, 'chat_json', side_effect=client.chat_json):
            self.start()
            task = self.finish()
        self.assertEqual(task['status'], 'failed', task)
        self.assertEqual(task['stage'], 'design')
        self.assertEqual(post.call_count, 1)
        usage = task['usage']['model_calls'][0]
        self.assertEqual(usage['total_tokens'], 25)
        self.assertEqual(usage['completion_tokens'], 8)
        self.assertEqual(usage['answer_chars'], len('{invalid'))
        self.assertEqual(usage['reasoning_chars'], len(raw_reasoning))
        self.assertEqual(usage['status'], 'invalid_response')
        self.assertEqual(usage['finish_reason'], 'stop')
        self.assertEqual(usage['error_code'], 'model_json_invalid')
        self.assertTrue(usage['may_charge'])
        self.assertNotIn(raw_reasoning, json.dumps(task))
        diagnostic=api._job(self.project,task['task_id']).parent/usage['response_diagnostic']
        stored=json.loads(diagnostic.read_text(encoding='utf-8'))
        self.assertEqual(stored['response_text'],'{invalid')
        self.assertNotIn(raw_reasoning,json.dumps(stored))
        self.assertNotIn(self.config['api_key'],json.dumps(stored))

    def test_network_failure_records_stage_without_retry_or_project_changes(self):
        before=(self.project/'record.json').read_bytes()
        error=client.requests.ConnectionError(client.urllib3_errors.ReadTimeoutError(
            None, '/private?api_key=do-not-store', 'private-network-message'))
        response=CloudClientTests.stream([])
        response.iter_lines.side_effect=error
        with patch.object(client.requests,'post',return_value=response) as post, \
             patch.object(api,'chat_json',side_effect=client.chat_json):
            self.start()
            task=self.finish()
        self.assertEqual(post.call_count,1)
        self.assertEqual(task['status'],'failed')
        self.assertIn('【编排动效方案 · 初次编排】',task['message'])
        self.assertIn('读取等待上限 45 秒',task['message'])
        call=task['usage']['model_calls'][0]
        self.assertEqual(call['stage'],'design')
        self.assertEqual(call['error_code'],'read_timeout')
        self.assertEqual(call['transport_phase'],'streaming')
        self.assertTrue(call['may_charge'])
        self.assertEqual(call['status'],'unconfirmed')
        self.assertEqual((self.project/'record.json').read_bytes(),before)
        self.assertNotIn('private-network-message',json.dumps(task))
        self.assertNotIn('do-not-store',json.dumps(task))

    def test_retry_never_reuses_old_generated_materials(self):
        with patch.object(api, 'chat_json', return_value=self.response(self.proposal())), \
             patch.object(api, 'render_scene', side_effect=RuntimeError('模拟素材完成后渲染失败')):
            original = self.start()
            failed = self.finish()
        self.assertEqual(failed['status'], 'failed')
        self.assertEqual(failed['usage']['image_requests'], 0)
        source = api._job(self.project, original['task_id']).parent / 'materials' / 'mat_kept.png'
        Image.new('RGB',(80,60),'red').save(source)
        api.generate_asset.reset_mock()
        with patch.object(api, 'chat_json', side_effect=[self.response(self.proposal()), self.response({'passed': True})]) as model, \
             patch.object(api,'_reuse_materials',side_effect=AssertionError('旧生成素材不得复用')):
            retry = self.start()
            result = self.finish()
        self.assertNotIn('reuse_from',retry)
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(result['usage']['image_requests'], 0)
        self.assertEqual(result['usage'].get('reused_materials',0), 0)
        api.generate_asset.assert_not_called()
        copied = api._job(self.project, retry['task_id']).parent / 'materials' / 'mat_kept.png'
        self.assertFalse(copied.exists())
        self.assertEqual(model.call_args_list[0].args[2]['available_material_specs'],{})
        self.assertNotIn('mat_kept',model.call_args_list[0].args[3])

    def test_reuse_changed_inputs_or_corrupt_manifest_cannot_leave_orphan_copies(self):
        previous_id = 'd' * 32
        folder = api._root(self.project) / previous_id
        old_resources = folder / 'materials'
        old_resources.mkdir(parents=True)
        api._write(folder / 'task.json', {'task_id': previous_id, 'status': 'failed',
                                        'shot_id': 'shot1', 'cloud_input_stamp': 'same-input'})
        assets, specs = {}, {}
        for name in ('mat_a', 'mat_b'):
            file = old_resources / (name + '.png')
            Image.new('RGB', (80, 60), '#125678').save(file)
            assets[name] = file
            specs[name] = api.Material(id=name, mode='illustration', prompt='已完成的素材').model_dump(mode='json')
        api._save_materials(folder, assets, specs)
        manifest = json.loads((folder / 'materials.json').read_text(encoding='utf-8'))

        original_copy = api.shutil.copyfileobj
        for case in ('changed-input', 'changed-hash', 'malformed-later-spec', 'partial-copy-error', 'copy-hash-race'):
            with self.subTest(case=case):
                altered = copy.deepcopy(manifest)
                stamp = 'other-input' if case == 'changed-input' else 'same-input'
                if case == 'changed-hash':
                    altered['hashes'] = {key: '0' * 64 for key in altered['hashes']}
                elif case == 'malformed-later-spec':
                    altered['specs']['mat_b']['mode'] = 'invalid-mode'
                api._write(folder / 'materials.json', altered)
                target = self.project / ('reuse-' + case)
                target.mkdir()
                sentinel = target / 'source_00.png'
                sentinel.write_bytes(b'pre-existing-owned-source')
                def copying(inp, out, *args):
                    if Path(inp.name).name == 'mat_b.png' and case in ('partial-copy-error', 'copy-hash-race'):
                        out.write(b'partial-or-concurrently-changed')
                        if case == 'partial-copy-error':
                            raise OSError('模拟复制中途失败')
                        return
                    return original_copy(inp, out, *args)
                with patch.object(api.shutil, 'copyfileobj', side_effect=copying):
                    kept, retained_specs = api._reuse_materials(self.project, previous_id, target, stamp)
                # A function may salvage independently valid items, but every
                # copied item must be returned, and no failed item may remain.
                self.assertEqual(set(kept), set(retained_specs))
                copied = {p.stem for p in target.glob('mat_*.png')}
                self.assertEqual(copied, set(kept), 'failed reuse left an untracked filename that blocks a later crop')
                self.assertNotIn('mat_b', kept)
                if case in ('changed-input', 'changed-hash'):
                    self.assertEqual(kept, {})
                self.assertEqual(sentinel.read_bytes(), b'pre-existing-owned-source')
        api.generate_asset.assert_not_called()

    def test_changed_reference_binding_prevents_publish(self):
        def model(*args, **kwargs):
            if 'previous_proposal' in args[2]:
                return self.response(self.proposal())
            self.shot['reference_ids'] = ['ref_04']
            self.persist()
            return self.response({'passed': True})

        with patch.object(api, 'chat_json', side_effect=model):
            self.start()
            task = self.finish()
        self.assertEqual(task['status'], 'failed', task)
        self.assertFalse(previews._pointer(self.project, 'shot1').is_file())

    def test_latest_hides_preview_after_reference_pixels_change(self):
        with patch.object(api, 'chat_json', side_effect=[self.response(self.proposal()), self.response({'passed': True})]):
            self.start()
            self.assertEqual(self.finish()['status'], 'completed')
        Image.new('RGB', (640, 360), '#FFEEDD').save(self.project / 'ref3.png')
        result = self.http.get(self.base + '/vibe-cloud-job/latest').json()
        self.assertIsNone(result['preview'], result)

    def test_restart_does_not_resubmit_paid_job_and_wrong_shot_cannot_stop(self):
        task = self.start()
        other = self.base.replace('/shot1', '/different')
        self.assertEqual(self.http.post(other + f"/vibe-cloud-jobs/{task['task_id']}/stop").status_code, 404)
        api._active.clear()
        result = self.http.get(self.base + '/vibe-cloud-job/latest').json()
        self.assertEqual(result['status'], 'stopped')
        self.assertEqual(len(self.workers), 1)


class CloudClientTests(unittest.TestCase):
    def config(self):
        return dict(base_url='https://example.invalid/v1', api_key='local-fake-key', model='vision-only')

    @staticmethod
    def stream(events, status=200):
        response = MagicMock()
        response.status_code = status
        response.__enter__.return_value = response
        response.iter_lines.return_value = [b'data: ' + json.dumps(event).encode() for event in events] + [b'data: [DONE]']
        return response

    def test_uses_dedicated_model_no_redirects_or_reasoning_leak(self):
        response = self.stream([
            {'choices': [{'delta': {'reasoning_content': 'must-not-persist', 'content': '{"passed":true}'}, 'finish_reason': 'stop'}]},
            {'usage': {'prompt_tokens': 2, 'total_tokens': 9, 'arbitrary_secret': 'no'}}])
        with patch.object(client.requests, 'post', return_value=response) as post:
            result, usage = client.chat_json(self.config(), 'system', {}, {}, lambda: False)
        self.assertEqual(result, {'passed': True})
        self.assertEqual(usage['total_tokens'], 9)
        self.assertNotIn('arbitrary_secret', usage)
        self.assertNotIn('must-not-persist', json.dumps((result, usage)))
        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.args[0], 'https://example.invalid/v1/chat/completions')
        self.assertEqual(post.call_args.kwargs['json']['model'], 'vision-only')
        self.assertFalse(post.call_args.kwargs['allow_redirects'])

    def test_deepseek_thinking_disabled_never_sends_reasoning_effort(self):
        config = {**self.config(), 'model': 'deepseek-flash'}
        response = self.stream([{'choices': [{'delta': {'content': '{"passed":true}'}, 'finish_reason': 'stop'}]}])
        with patch.object(client.requests, 'post', return_value=response) as post:
            client.chat_json(config, 'system', {}, {}, lambda: False, thinking=False)
        body = post.call_args.kwargs['json']
        self.assertEqual(body['thinking'], {'type': 'disabled'})
        self.assertNotIn('reasoning_effort', body)

    def test_error_and_cancellation_do_not_retry_or_switch_channels(self):
        with patch.object(client.requests, 'post', return_value=self.stream([], 500)) as post:
            with self.assertRaises(client.CloudCallError):
                client.chat_json(self.config(), 'system', {}, {}, lambda: False)
            self.assertEqual(post.call_count, 1)
        with patch.object(client.requests, 'post') as post:
            with self.assertRaises(InterruptedError):
                client.chat_json(self.config(), 'system', {}, {}, lambda: True)
            post.assert_not_called()

    def test_truncated_model_answer_is_not_accepted_as_a_success(self):
        response = self.stream([{'choices': [{'delta': {'content': '{"passed":true}'}, 'finish_reason': 'length'}]}])
        with patch.object(client.requests, 'post', return_value=response):
            with self.assertRaisesRegex(client.CloudCallError, '截断'):
                client.chat_json(self.config(), 'system', {}, {}, lambda: False)

    def test_more_than_supported_images_is_rejected_not_silently_omitted(self):
        images = {f'asset_{index}': Path('not-opened.png') for index in range(33)}
        with patch.object(client.requests, 'post') as post, patch.object(client, 'image_block') as image:
            with self.assertRaisesRegex(client.CloudCallError, '32'):
                client.chat_json(self.config(), 'system', {}, images, lambda: False)
            post.assert_not_called()
            image.assert_not_called()

    def test_typed_transport_errors_are_distinct_and_never_leak_exception_text(self):
        errors=client.requests.exceptions
        secret='https://user:password@example.invalid/?api_key=do-not-store'
        cases=[(errors.ConnectTimeout(secret),'connect_timeout','连接上游超时'),
               (errors.ReadTimeout(secret),'read_timeout','读取等待上限'),
               (errors.ProxyError(secret),'proxy_error','代理连接失败'),
               (errors.SSLError(secret),'tls_error','安全连接失败'),
               (errors.ChunkedEncodingError(secret),'stream_interrupted','传输中断'),
               (errors.ConnectionError(secret),'connection_error','连接失败或中断'),
               (errors.Timeout(secret),'network_timeout','等待超时'),
               (errors.RequestException(secret),'request_error','网络异常')]
        for error,code,label in cases:
            with self.subTest(code=code),patch.object(client.requests,'post',side_effect=error) as post:
                with self.assertRaises(client.CloudCallError) as caught:
                    client.chat_json(self.config(),'private system',{}, {},lambda:False)
            result=caught.exception
            self.assertEqual(result.usage['error_code'],code)
            self.assertEqual(result.usage['transport_phase'],'waiting_response')
            self.assertEqual(result.usage['answer_chars'],0)
            self.assertGreaterEqual(result.usage['seconds'],0)
            self.assertIn(label,str(result))
            self.assertIn('未自动重试',str(result))
            self.assertNotIn('do-not-store',str(result)+json.dumps(result.usage))
            self.assertNotIn('local-fake-key',str(result)+json.dumps(result.usage))
            self.assertEqual(post.call_count,1)

    def test_wrapped_stream_read_timeout_keeps_partial_counts_not_contents(self):
        response=self.stream([])
        partial='{"private_content":'
        thought='private-deliberation'
        def lines(**kwargs):
            yield b'data: '+json.dumps({'choices':[{'delta':{'content':partial,'reasoning_content':thought}}]}).encode()
            yield b'data: '+json.dumps({'usage':{'total_tokens':17,'secret':'omit'}}).encode()
            raise client.requests.ConnectionError(client.urllib3_errors.ReadTimeoutError(None,'/private','secret'))
        response.iter_lines.side_effect=lines
        with patch.object(client.requests,'post',return_value=response) as post:
            with self.assertRaises(client.CloudCallError) as caught:
                client.chat_json(self.config(),'system',{}, {},lambda:False)
        error=caught.exception
        self.assertEqual(error.usage['error_code'],'read_timeout')
        self.assertEqual(error.usage['transport_phase'],'streaming')
        self.assertEqual(error.usage['answer_chars'],len(partial))
        self.assertEqual(error.usage['reasoning_chars'],len(thought))
        self.assertEqual(error.usage['received_events'],2)
        self.assertEqual(error.usage['total_tokens'],17)
        self.assertEqual(error.usage['http_status'],200)
        self.assertIn(f'已收到 {len(partial)} 字符',str(error))
        self.assertNotIn('private',str(error)+json.dumps(error.usage))
        self.assertNotIn('secret',json.dumps(error.usage))
        self.assertEqual(post.call_count,1)

    def test_wrapped_new_connection_error_is_not_claimed_to_be_timeout(self):
        inner=client.urllib3_errors.NewConnectionError(None,'sensitive-address')
        wrapped=client.urllib3_errors.MaxRetryError(None,'/private',reason=inner)
        code,_=client._transport_failure(client.requests.ConnectionError(wrapped))
        self.assertEqual(code,'connection_error')
        wrapped.reason=wrapped  # Pathological exception chains must terminate.
        self.assertEqual(client._transport_failure(client.requests.ConnectionError(wrapped))[0],'connection_error')

    def test_malformed_stream_and_total_budget_failure_keep_safe_diagnostics(self):
        response=self.stream([])
        response.iter_lines.return_value=[b'data: {not-json-secret']
        with patch.object(client.requests,'post',return_value=response):
            with self.assertRaises(client.CloudCallError) as caught:
                client.chat_json(self.config(),'system',{}, {},lambda:False)
        self.assertEqual(caught.exception.usage['error_code'],'invalid_stream')
        self.assertNotIn('not-json-secret',str(caught.exception))
        response=self.stream([{'choices':[]}])
        with patch.object(client.requests,'post',return_value=response) as post, \
             patch.object(client.time,'monotonic',side_effect=[0,181,181]):
            with self.assertRaises(client.CloudCallError) as caught:
                client.chat_json(self.config(),'system',{}, {},lambda:False)
        self.assertEqual(caught.exception.usage['error_code'],'total_timeout')
        self.assertEqual(caught.exception.usage['seconds'],181)
        self.assertEqual(post.call_count,1)

    def test_http_error_only_recommends_key_check_for_auth_errors(self):
        for status in (401,403,429,500):
            with self.subTest(status=status),patch.object(client.requests,'post',return_value=self.stream([],status)):
                with self.assertRaises(client.CloudCallError) as caught:
                    client.chat_json(self.config(),'system',{}, {},lambda:False)
            self.assertEqual(caught.exception.usage['http_status'],status)
            self.assertEqual(caught.exception.usage['error_code'],'http_error')
            self.assertEqual('密钥' in str(caught.exception),status in (401,403))


class ProposalContractTests(unittest.TestCase):
    @staticmethod
    def minimal_scene():
        return {'title':'结果','stages':[{'id':'one','layout':'findings','headline':'要点',
                                        'items':[{'id':'a','title':'第一项'}]}]}

    def test_only_exact_empty_nested_materials_array_is_ignored(self):
        raw={'summary':'有效方案','type':'json_object','materials':[],
             'scene':{**self.minimal_scene(),'materials':[]}}
        before=copy.deepcopy(raw)
        normalized=api.Proposal.model_validate(raw).model_dump()
        self.assertNotIn('materials',normalized['scene'])
        self.assertEqual(raw,before)
        for value in (None,{},'',(),[{'id':'mat_x','mode':'crop','source':'core'}],[None]):
            invalid={**raw,'scene':{**self.minimal_scene(),'materials':value}}
            with self.subTest(value=value),self.assertRaises(api.ValidationError):
                api.Proposal.model_validate(invalid)
        with self.assertRaises(api.ValidationError):
            api.Proposal.model_validate({**raw,'scene':{**raw['scene'],'unrecognized':[]}})

    def test_only_empty_elements_note_is_ignored_without_mutating_diagnostics(self):
        raw={'summary':'方案','visual_basis':dict(mode='native',style_notes='原图颜色',elements_note='',
            elements=[dict(label='结论',source='core',kind='text',treatment='native')])}
        before=copy.deepcopy(raw)
        result=api.Proposal.model_validate(raw).model_dump()
        self.assertNotIn('elements_note',result['visual_basis'])
        self.assertEqual(raw,before)
        for value in ('有实际含义',' ',None,[],{},0):
            with self.subTest(value=value),self.assertRaises(api.ValidationError):
                api.Proposal.model_validate({**raw,'visual_basis':{**raw['visual_basis'],'elements_note':value}})
        with self.assertRaises(api.ValidationError):
            api.Proposal.model_validate({**raw,'visual_basis':{**raw['visual_basis'],'unknown':''}})

    def test_actual_cue_example_explains_offset_without_mutating_saved_timing(self):
        cues={'first':0.0,'second':4.48,'third':8.08};before=dict(cues)
        example=api._timing_example(cues)
        self.assertEqual(example['recommended_stage_anchor'],{'cue':'second'})
        self.assertEqual(example['resolved_start_seconds'],4.48)
        self.assertEqual(example['wrong_double_added_start_seconds'],8.96)
        self.assertEqual(cues,before)
        self.assertNotIn('cue',api._timing_example({})['recommended_first_stage_anchor'])
        for instruction in ('只填写某条字幕cue并省略at','不能把该字幕start再次填进at',
                            'core仅供理解风格和内容','不能把整张海报冒充',
                            'scene=null并在questions请求独立原素材'):
            self.assertIn(instruction,api.DIRECTOR)

    def test_known_transport_marker_removed_without_changing_original(self):
        raw = {'summary': '有效方案', 'type': 'json_object'}
        before = copy.deepcopy(raw)
        normalized = api.Proposal.model_validate(raw).model_dump()
        self.assertNotIn('type', normalized)
        self.assertEqual(raw, before)
        schema = api.Proposal.model_json_schema()
        self.assertFalse(schema['additionalProperties'])
        self.assertNotIn('type', schema['properties'])

    def test_every_other_unknown_or_nested_field_is_still_rejected(self):
        cases = [
            {'summary': '方案', 'type': 'json_schema'},
            {'summary': '方案', 'type': 'JSON_OBJECT'},
            {'summary': '方案', 'type': None},
            {'summary': '方案', 'type': 'json_object', 'response_format': {}},
            {'summary': '方案', 'unknown': 'value'},
            {'summary': '方案', 'scene': {'type': 'json_object', 'title': '标题',
                'stages': [{'id': 's', 'layout': 'findings', 'headline': '信息',
                            'items': [{'id': 'i', 'title': '条目'}]}]}},
            {'summary': '方案', 'materials': [{'id': 'mat_x', 'mode': 'crop', 'type': 'json_object'}]},
        ]
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaises(api.ValidationError):
                api.Proposal.model_validate(raw)

    def test_director_explains_public_contract_and_visual_semantics(self):
        for instruction in ('顶层仅允许summary、source_notes、questions、materials、scene、visual_basis、source_style',
                            '禁止用process画成前后或因果链',
                            '不要把原海报中的纯文字块裁成制作素材'):
            self.assertIn(instruction, api.DIRECTOR)

    def test_matte_schema_limits_prompt_to_selection_and_visible_text_to_source_facts(self):
        description = api.Material.model_json_schema()['properties']['prompt']['description']
        self.assertIn('ONLY select the source subject',description)
        self.assertIn('Do not specify colors',description)
        self.assertIn('禁止请求crop/matte/illustration',api.DIRECTOR)
        self.assertIn('不得写“以真实贡献与开放的后续价值收束”',api.DIRECTOR)
        self.assertIn('源字幕和材料未支持',api.DIRECTOR)
        self.assertIn('源字幕/材料未支持的断言',api.REVIEWER)

    def test_reviewer_checks_precise_relationships_not_style_preferences(self):
        for instruction in ('不得用process箭头串联成前后依赖或因果链',
                            '同一完整结论占据主要画面',
                            '必须结合最终字幕和plan确认关系确实错误',
                            '不因个人美感、布局偏好', '明确约束违背而非审美偏好',
                            '修正必须继续沿用冻结source_style'):
            self.assertIn(instruction, api.REVIEWER)

    def test_director_uses_information_density_not_a_fixed_stage_quota(self):
        self.assertNotIn('选择2–4个',api.DIRECTOR)
        for instruction in ('不要求固定阶段数','简短总结通常1–2阶段足够',
                            '禁止仅同义改写','materials只放Proposal顶层'):
            self.assertIn(instruction,api.DIRECTOR)

    def test_review_rules_distinguish_relayout_from_evidence_or_text_errors(self):
        for instruction in ('不要求最后一帧继续包含前面所有条目',
                            '文字核查先读取plan的精确字符串',
                            '期刊信息本身不需要DOI前缀',
                            '不要从低分辨率缩略图的OCR猜测制造错字',
                            '不能因缺少原肝脏等示意物件判失败',
                            '省略声明不豁免真实身份、关键研究证据或叙事必要信息',
                            '以冻结source_style.palette和background.kind为明确校验范围',
                            '确定的渲染漏字、裁切或错误仍应报告'):
            self.assertIn(instruction,api.REVIEWER)

    def test_review_reference_tracks_all_stages_and_explicit_omission_without_waiving_evidence(self):
        scene=self.minimal_scene()
        scene['stages'].append({'id':'citation','layout':'findings','cue':'second','headline':'引用',
            'items':[{'id':'paper','title':'Author A et al.','body':'Journal. 2025;60:442–455.',
                      'note':'DOI:10.1007/example'}]})
        raw={'summary':'代码重排','scene':scene,'visual_basis':{
            'mode':'native','style_notes':'保持深色科技感',
            'elements':[{'label':'教学器官示意','source':'core','kind':'subject','treatment':'omit',
                         'reason':'非研究证据，与当前结论无关'},
                        {'label':'研究数据','source':'core','kind':'chart','treatment':'native'}]}}
        proposal=api.Proposal.model_validate(raw);before=proposal.model_dump()
        reference=api._review_reference(proposal,{'second':3.0},6.0,{'core'})
        self.assertEqual(reference['stage_windows'],[{'id':'one','start':0.0,'end':3.0},
                                                    {'id':'citation','start':3.0,'end':6.0}])
        self.assertEqual(reference['presentation'],'sequential_stages_not_cumulative_poster')
        self.assertEqual(reference['declared_omissions'][0]['kind'],'subject')
        self.assertEqual(len(reference['declared_omissions']),1)
        self.assertIn('relevant_evidence',reference['omissions_do_not_waive'])
        self.assertEqual(reference['style_comparison'],
                         ['background_brightness','dominant_palette','background_kind'])
        # Exact citation stays in the plan; no OCR guess/rewrite or forced DOI prefix on journal text.
        self.assertEqual(proposal.scene.stages[1].items[0].note,'DOI:10.1007/example')
        self.assertEqual(proposal.model_dump(),before)


class ReviewContractTests(unittest.TestCase):
    def test_exact_known_issue_objects_normalize_without_mutating_diagnostics(self):
        raw = {'passed': False, 'issues': [
            {'element': '肝脏主体', 'time': '1.0s / 5.4s', 'reason': '蒙版移位且文字残留'},
            {'element': '第二阶段', 'time': 5.4, 'reason': '标签被裁切'},
            '第三阶段：文字被遮挡']}
        before = copy.deepcopy(raw)
        result = api.Review.model_validate(raw)
        self.assertFalse(result.passed)
        self.assertTrue(all(isinstance(issue,str) for issue in result.issues))
        for fact in ('肝脏主体', '1.0s / 5.4s', '蒙版移位且文字残留'):
            self.assertIn(fact,result.issues[0])
        self.assertIn('5.4 秒',result.issues[1])
        self.assertEqual(raw,before)

    def test_unknown_incomplete_or_empty_review_issues_reject(self):
        valid = {'element':'主体','time':1,'reason':'缺少关键部分'}
        invalid = [None, '', '  ', 3, {}, {**valid,'extra':1},
            {'element':'主体','reason':'缺失时刻'}, {**valid,'element':''},
            {**valid,'time':''}, {**valid,'time':True}, {**valid,'time':-1},
            {**valid,'time':float('nan')}, {**valid,'reason':' '}, {**valid,'reason':{}}]
        for issue in invalid:
            with self.subTest(issue=issue),self.assertRaises(api.ValidationError):
                api.Review.model_validate({'passed':False,'issues':[issue]})
        for raw in ({'passed':False,'issues':[]}, {'passed':False},
                    {'passed':True,'issues':[],'extra':1}, {'passed':True,'issues':'none'}):
            with self.subTest(raw=raw),self.assertRaises(api.ValidationError):
                api.Review.model_validate(raw)

    def test_passed_remains_a_strict_boolean_and_clear_review_can_be_empty(self):
        for value in ('true','false',1,0,None):
            with self.subTest(value=value),self.assertRaises(api.ValidationError):
                api.Review.model_validate({'passed':value,'issues':[]})
        self.assertEqual(api.Review.model_validate({'passed':True,'issues':[]}).issues,[])

    def test_material_guidance_requires_original_inputs_and_no_generated_assets(self):
        for instruction in ('禁止复用过去生成或抠图素材', '本轮materials必须为空数组[]',
                            '图像预算为0', 'visual_basis中omit并给明确理由'):
            self.assertIn(instruction,api.DIRECTOR)


if __name__ == '__main__':
    unittest.main()
