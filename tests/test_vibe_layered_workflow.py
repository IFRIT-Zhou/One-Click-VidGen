"""Offline workflow regression: real assets/contracts, no provider or renderer job."""
import copy
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image

from backend.app import vibe_layered_workflow as workflow
from backend.app.vibe_layered import Design


class LayeredWorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = self.root / 'project'
        self.job_id = 'a' * 32
        self.folder = self.project / 'assets' / 'vibe_cloud' / self.job_id
        self.folder.mkdir(parents=True)
        self.path = self.folder / 'task.json'
        self.path.write_text(json.dumps(dict(task_id=self.job_id, shot_id='shot',
            status='running', stage='materials', usage={})), encoding='utf-8')
        self.subject = self.root / 'subject.png'
        Image.new('RGBA', (200, 160), (45, 67, 89, 255)).save(self.subject)
        self.background = self.root / 'background.png'
        Image.new('RGB', (640, 360), '#13283C').save(self.background)
        self.core = self.root / 'core.png'
        Image.new('RGB', (640, 360), '#26465C').save(self.core)
        self.audio = self.root / 'audio.wav'
        self.audio.write_bytes(b'offline renderer fixture')
        self.assets = {'background': self.background, 'subject_0': self.subject}
        self.context = dict(canvas=[640, 360], duration=12, subtitles=[
            dict(start=0, end=2, text='研究结果'), dict(start=2, end=5, text='观察对象'),
            dict(start=5, end=12, text='49对患者')])
        self.raw = dict(background_color='#13283C', subjects=[dict(asset_id='subject_0',
            crop=[0, 0, 1, .75], box=[.05, .2, .35, .6], group='subject')], labels=[
            dict(box=[.1, .02, .9, .15], text='研究结果', color='#FFFFFF', font_size=36, group='title'),
            dict(box=[.4, .3, .9, .6], text='', plate=dict(color='#254863', stroke='#4DB6E8'), group='result'),
            dict(box=[.42, .33, .88, .57], text='49 对患者', color='#FFFFFF', font_size=42, group='result')],
            decorations=[dict(type='polyline', box=[.36, .35, .42, .5],
                points=[[0, .5], [.8, .5], [.6, .2], [.8, .5], [.6, .8]],
                stroke='#4DB6E8', group='result')])
        self.inventory = Design.model_validate(dict(background='extract', background_color='#13283C',
            subjects=[dict(source='core', treatment='cutout', crop=[.02, .2, .3, .6],
                           box=[.05, .2, .35, .6], at=0)],
            labels=[dict(box=[.1, .02, .9, .15], text='研究结果', color='#FFFFFF', font_size=36, at=0),
                    dict(box=[.42, .33, .88, .57], text='49 对患者', color='#FFFFFF', font_size=42, at=0)]))
        self.schedule = dict(groups=[dict(id='title', at=0, enter=.6, motion='fade', exit=.3),
            dict(id='subject', at=2, enter=.7, motion='slide_left', exit=.3),
            dict(id='result', at=5, enter=.8, motion='draw', exit=.3)])
        self.pass_review = dict(passed=True, issues=[], summary='已检查完整海报与原图，未发现遗漏或遮挡。')
        self.fail_review = dict(passed=False, issues=[dict(kind='crop', target='subject_0',
            explanation='主体下方仍夹带半截文字', correction='缩小裁片下边界，保留完整主体，避免重复文字')],
            summary='需要返工裁片，不能直接采用。')
        self.event = threading.Event()
        self.calls = []
        self.queue = []
        self.cancel_phase = None
        self.cancel_still = False
        self.call_mock = self.start_patch('backend.app.vibe_layered_workflow.api._call', side_effect=self.fake_call)
        self.still_mock = self.start_patch('backend.app.vibe_layered_workflow.renderer.render_layered_still', side_effect=self.fake_still)
        self.render_mock = self.start_patch('backend.app.vibe_layered_workflow.renderer.render_compiled', side_effect=self.fake_render)
        self.start_patch('backend.app.vibe_layered_workflow.api.ffmpeg_binary', return_value='offline-ffmpeg')

    def start_patch(self, target, **kwargs):
        active = patch(target, **kwargs)
        mocked = active.start()
        self.addCleanup(active.stop)
        return mocked

    def fake_call(self, path, config, system, payload, images, cancelled, **kwargs):
        phase = json.loads(path.read_text(encoding='utf-8'))['stage']
        self.calls.append(dict(phase=phase, payload=copy.deepcopy(payload), images=dict(images), options=kwargs))
        self.assertFalse(cancelled(), 'a new model call must not start after cancellation')
        self.assertIs(kwargs.get('thinking'),False,
                      'layout, visual review/repair and motion use bounded non-thinking JSON responses')
        self.assertTrue(self.queue, f'unexpected model call: {phase}')
        expected_phase, response = self.queue.pop(0)
        self.assertEqual(phase, expected_phase)
        self.assertIn('schema', payload)
        for value in images.values():
            self.assertTrue(Path(value).is_file(), f'missing model image: {value}')
        if self.cancel_phase == phase:
            self.event.set()
        if isinstance(response, BaseException):
            raise response
        return copy.deepcopy(response)

    def fake_still(self, plan, canvas, assets, output, cancelled=None):
        self.assertTrue(all(element['at'] == 0 and element['motion'] == 'none'
                            for element in plan['elements']))
        self.assertTrue(Path(assets['placed_subject_0']).is_file())
        target = output / 'poster.png'
        Image.new('RGB', (64, 36), '#13283C').save(target)
        if self.cancel_still:
            self.event.set()
        return target

    def fake_render(self, plan, canvas, renderer, assets, audio, seek, duration, output,
                    ffmpeg, timeout, cancelled):
        self.assertEqual(renderer, 'ocv-remotion-layered-v1')
        self.assertEqual(audio, self.audio)
        self.assertFalse(cancelled())
        for name in ('proof.jpg', 'detail_early.png', 'detail_middle.png', 'detail_end.png'):
            Image.new('RGB', (64, 36), '#13283C').save(output / name)
        return dict(duration=duration, renderer=renderer)

    def run_workflow(self):
        return workflow.finish(self.project, 'shot', self.job_id, self.inventory,
            self.context, {'core': self.core}, self.assets, {'model': 'offline'},
            SimpleNamespace(instruction='', pace='normal'), self.event, self.audio, 0)

    def happy_queue(self):
        return [('assembly', self.raw), ('poster_review', self.pass_review),
                ('motion', self.schedule), ('motion_review', self.pass_review)]

    def test_visual_repair_can_select_prepared_original_without_another_image_request(self):
        original=self.root/'subject-original.png'
        Image.new('RGBA',(200,160),(90,120,160,255)).save(original)
        self.assets['subject_0_original']=original
        corrected=copy.deepcopy(self.raw)
        corrected['subjects'][0]['prepared_variant']='original'
        self.queue=[('assembly',self.raw),('poster_review',self.fail_review),
            ('visual_repair',corrected),('poster_recheck',self.pass_review),
            ('motion',self.schedule),('motion_review',self.pass_review)]
        _,assets,_,_,audit=self.run_workflow()
        with Image.open(assets['placed_subject_0']) as im:
            self.assertEqual(im.getpixel((100,100)),(90,120,160,255))
        self.assertEqual(audit['subject_variants'],[dict(asset_id='subject_0',prepared_variant='original')])
        self.assertEqual(self.calls[0]['images']['subject_0_original'],original)
        manifest=self.calls[0]['payload']['subject_variants'][0]
        self.assertEqual(manifest['variants'],dict(cutout='subject_0',original='subject_0_original'))
        self.assertEqual(manifest['source_id'],'core')
        self.assertEqual(self.calls[1]['payload']['subject_variants'][0],manifest)
        self.assertNotIn('source_crop',manifest)
        self.assertIn('whole supplied material',manifest['coordinate_space'])
        sent_inventory=self.calls[0]['payload']['inventory']
        self.assertNotIn('crop',sent_inventory['subjects'][0])
        self.assertNotIn('box',sent_inventory['subjects'][0])
        self.assertNotIn('box',sent_inventory['labels'][0])
        self.assertNotIn('font_size',sent_inventory['labels'][0])
        self.assertEqual(sent_inventory['labels'][0]['source_box'],list(self.inventory.labels[0].box))
        self.assertEqual(sent_inventory['labels'][0]['coordinate_space'],'core')
        self.assertIn('source_geometry',self.calls[0]['payload'])

    def test_core_anchor_uses_source_pixels_and_prepared_crop_not_guessed_model_box(self):
        # Source 640x360, floor/ceil(.02,.2,.3,.6) -> (12,72,192,216).
        Image.new('RGBA',(180,144),(45,67,89,255)).save(self.subject)
        self.raw['subjects'][0].update(placement='source_anchor',crop=[0,0,1,.75])
        self.raw['subjects'][0].pop('box')
        self.queue=self.happy_queue()
        plan,_,_,_,_=self.run_workflow()
        subject=next(row for row in plan['elements'] if row['id']=='g:subject:subject_0')
        self.assertEqual(subject['box'],[12,72,180,108])
        recorded=json.loads((self.folder/'assembly_0.json').read_text(encoding='utf-8'))
        self.assertEqual(recorded['subjects'][0]['placement'],'source_anchor')
        self.assertEqual(recorded['subjects'][0]['box'],[12/640,72/360,192/640,180/360])
        self.assertTrue(self.calls[0]['payload']['source_geometry']['subjects'][0]['anchor_available'])

    def test_decoration_source_location_survives_to_assembly_and_review(self):
        decoration=dict(kind='line',description='分隔线',source_box=[.1,.2,.9,.21],coordinate_space='core')
        self.context['inventory_notes']={'decorations':[decoration]}
        self.queue=self.happy_queue()
        self.run_workflow()
        for call in self.calls[:2]:
            self.assertEqual(call['payload']['inventory']['decorations'],[decoration])

    def test_browser_overlap_value_error_enters_single_visual_repair(self):
        original=self.fake_still
        count=0
        def still(*args,**kwargs):
            nonlocal count
            count+=1
            if count==1:raise ValueError('Remotion 排版校验失败：OCV_LAYOUT_OVERLAP:g:title:text_0<->g:result:text_2[80x45]')
            return original(*args,**kwargs)
        self.still_mock.side_effect=still
        self.queue=[('assembly',self.raw),('visual_repair',self.raw),
            ('poster_recheck',self.pass_review),('motion',self.schedule),('motion_review',self.pass_review)]
        _,_,_,_,audit=self.run_workflow()
        self.assertEqual(audit['visual_repairs'],1)
        self.assertIn('OCV_LAYOUT_OVERLAP',self.calls[1]['payload']['visual_issues']['issues'][0]['explanation'])

    def test_missing_original_is_a_precise_format_error_not_silent_cutout_fallback(self):
        invalid=copy.deepcopy(self.raw)
        invalid['subjects'][0]['prepared_variant']='original'
        self.queue=[('assembly',invalid),*self.happy_queue()]
        self.run_workflow()
        self.assertIn('subject_0_original',self.calls[1]['payload']['validation_errors'])
        self.assertEqual(self.still_mock.call_count,1)

    def test_variant_dimension_mismatch_stops_before_any_model_call(self):
        original=self.root/'mismatched-original.png'
        Image.new('RGBA',(200,120),(90,120,160,255)).save(original)
        self.assets['subject_0_original']=original
        with self.assertRaisesRegex(ValueError,'坐标范围不一致'):
            self.run_workflow()
        self.call_mock.assert_not_called()
        self.still_mock.assert_not_called()

    def test_complete_workflow_uses_real_crops_static_poster_then_frozen_layout_motion(self):
        self.queue = self.happy_queue()
        originals = {key: path.read_bytes() for key, path in self.assets.items()}
        plan, assets, metrics, output, audit = self.run_workflow()
        self.assertFalse(self.queue)
        self.assertEqual([call['phase'] for call in self.calls],
                         ['assembly', 'poster_review', 'motion', 'motion_review'])
        self.assertEqual(self.still_mock.call_count, 1)
        self.assertEqual(self.render_mock.call_count, 1)
        self.assertFalse(self.calls[0]['options']['thinking'])
        self.assertFalse(next(row for row in self.calls if row['phase']=='motion')['options']['thinking'])
        self.assertFalse(next(row for row in self.calls if row['phase']=='poster_review')['options']['thinking'])
        with Image.open(assets['placed_subject_0']) as crop:
            self.assertEqual(crop.size, (200, 120))
            self.assertEqual(crop.getpixel((100, 100)), (45, 67, 89, 255))
        self.assertEqual({key: path.read_bytes() for key, path in self.assets.items()}, originals)
        static = self.still_mock.call_args.args[0]
        timing = {'at', 'end', 'enter', 'exit', 'motion'}
        for old, new in zip(static['elements'], plan['elements']):
            self.assertEqual({k: v for k, v in old.items() if k not in timing},
                             {k: v for k, v in new.items() if k not in timing})
        self.assertEqual(audit['quality_status'], 'passed')
        self.assertEqual(metrics['visual_repairs'], 0)
        self.assertTrue((output / 'proof.jpg').is_file())
        self.assertEqual(json.loads((self.folder / 'quality.json').read_text(encoding='utf-8')), audit)

    def test_one_visual_repair_reuses_original_assets_and_reviews_reconstructed_poster(self):
        corrected = copy.deepcopy(self.raw)
        corrected['subjects'][0]['crop'] = [0, 0, 1, .5]
        corrected['labels'][1]['plate']['radius'] = 18
        self.queue = [('assembly', self.raw), ('poster_review', self.fail_review),
            ('visual_repair', corrected), ('poster_recheck', self.pass_review),
            ('motion', self.schedule), ('motion_review', self.pass_review)]
        _, assets, _, _, audit = self.run_workflow()
        self.assertEqual(audit['visual_repairs'], 1)
        self.assertEqual(audit['quality_status'], 'passed')
        self.assertEqual(self.still_mock.call_count, 2)
        with Image.open(assets['placed_subject_0']) as crop:
            self.assertEqual(crop.size, (200, 80))
        self.assertEqual({key:self.calls[2]['payload']['visual_issues'][key] for key in self.fail_review}, self.fail_review)
        self.assertEqual(self.calls[3]['images']['poster'].parent.name, 'poster-1')
        self.assertEqual(self.calls[2]['images']['subject_0'], self.subject)

    def test_first_poster_review_retakes_source_before_single_visual_repair(self):
        retake=dict(asset_id='subject_0',source_crop=[.02,.1,.52,.8],reason='当前裁片已截断主体底部，需要从原图重新取材')
        review={**self.fail_review,'source_retakes':[retake]}
        corrected=copy.deepcopy(self.raw)
        corrected['subjects'][0].update(crop=[0,0,1,1],prepared_variant='original')
        self.queue=[('assembly',self.raw),('poster_review',review),('visual_repair',corrected),
                    ('poster_recheck',self.pass_review),('motion',self.schedule),('motion_review',self.pass_review)]
        original_bytes={key:file.read_bytes() for key,file in self.assets.items()}
        original_inventory=self.inventory.model_dump()
        prepared={}

        def local_retake(requests,inventory,sources,assets,output,cancelled):
            self.assertEqual([row['phase'] for row in self.calls],['assembly','poster_review'])
            self.assertEqual([item.model_dump(mode='json') for item in requests],[retake])
            self.assertFalse(cancelled())
            self.assertEqual(sources['core'],self.core)
            self.assertIs(inventory,self.inventory)
            output.mkdir()
            original=output/'subject_0-original.png'
            cutout=output/'subject_0-rgba.png'
            Image.new('RGBA',(320,240),(90,120,160,255)).save(original)
            Image.new('RGBA',(320,240),(45,67,89,128)).save(cutout)
            replacement=inventory.model_copy(deep=True)
            replacement.subjects[0].crop=tuple(retake['source_crop'])
            prepared.update(inventory=replacement,original=original,cutout=cutout)
            return replacement,{**assets,'subject_0':cutout,'subject_0_original':original},[retake]

        with patch.object(workflow,'apply_retakes',side_effect=local_retake) as take, \
             patch.object(workflow,'validate_content',wraps=workflow.validate_content) as validate:
            _,assets,_,_,audit=self.run_workflow()
        take.assert_called_once()
        self.assertIs(validate.call_args_list[1].args[1],prepared['inventory'])
        repair=next(row for row in self.calls if row['phase']=='visual_repair')
        recheck=next(row for row in self.calls if row['phase']=='poster_recheck')
        for call in (repair,recheck):
            self.assertEqual(call['images']['subject_0'],prepared['cutout'])
            self.assertEqual(call['images']['subject_0_original'],prepared['original'])
            self.assertEqual(call['payload']['material_specs']['subject_0']['width'],320)
            self.assertEqual(call['payload']['material_specs']['subject_0']['height'],240)
            self.assertEqual(call['payload']['inventory'],workflow.inventory_manifest(prepared['inventory']))
            self.assertEqual(call['payload']['subject_variants'][0]['variants']['original'],'subject_0_original')
        self.assertTrue(self.calls[1]['payload']['source_retake_available'])
        self.assertFalse(recheck['payload']['source_retake_available'])
        self.assertTrue(self.calls[1]['payload']['source_retake_manifest'])
        self.assertEqual(len(self.calls),6,'local source re-take adds no model call to the single visual repair')
        self.assertEqual(self.still_mock.call_count,2)
        self.assertEqual(self.render_mock.call_count,1)
        self.assertEqual(audit['source_retakes'],[retake])
        self.assertEqual(audit['quality_status'],'passed')
        with Image.open(assets['placed_subject_0']) as image:
            self.assertEqual(image.size,(320,240))
            self.assertEqual(image.getpixel((100,100)),(90,120,160,255))
        self.assertEqual({key:file.read_bytes() for key,file in self.assets.items()},original_bytes)
        self.assertEqual(self.inventory.model_dump(),original_inventory)

    def test_recheck_request_for_another_retake_is_needs_review_not_second_retake(self):
        retake=dict(asset_id='subject_0',source_crop=[.02,.1,.52,.8],reason='原始裁片缺少主体下边缘')
        review={**self.fail_review,'source_retakes':[retake]}
        second_retake={**retake,'source_crop':[0,0,.65,.95],'reason':'返工后仍需要更大的原图范围'}
        second_review={**self.fail_review,'source_retakes':[second_retake]}
        updated_inventory=self.inventory.model_copy(deep=True)
        updated_inventory.subjects[0].crop=tuple(retake['source_crop'])
        self.queue=[('assembly',self.raw),('poster_review',review),
                    ('visual_repair',self.raw),('poster_recheck',second_review)]
        with patch.object(workflow,'apply_retakes',return_value=(updated_inventory,self.assets,[retake])) as take:
            plan,_,metrics,_,audit=self.run_workflow()
        take.assert_called_once()
        self.assertEqual(len(self.calls),4)
        self.assertEqual(audit['quality_status'],'needs_review')
        self.assertEqual(metrics['quality_status'],'needs_review')
        recorded=audit['static_reviews'][-1]['source_retakes']
        self.assertEqual([{**item,'source_crop':list(item['source_crop'])} for item in recorded],[second_retake])
        self.assertEqual(audit['visual_repairs'],1)
        self.assertTrue(all(element['motion']=='none' for element in plan['elements']))
        self.assertFalse((self.folder/'motion_schedule.json').exists())

    def test_passed_review_cannot_hide_a_source_retake_request(self):
        retake=dict(asset_id='subject_0',source_crop=[.02,.1,.52,.8],reason='主体下边缘缺失')
        with self.assertRaises(ValueError):
            workflow.Review.model_validate({**self.pass_review,'source_retakes':[retake]})

    def test_recheck_cannot_start_its_first_retake_after_visual_repair_is_spent(self):
        retake=dict(asset_id='subject_0',source_crop=[.02,.1,.52,.8],reason='返工后才发现源裁片缺少底座')
        self.queue=[('assembly',self.raw),('poster_review',self.fail_review),
                    ('visual_repair',self.raw),('poster_recheck',{**self.fail_review,'source_retakes':[retake]})]
        with patch.object(workflow,'apply_retakes',side_effect=AssertionError('retake only on first poster review')) as take:
            _,_,_,_,audit=self.run_workflow()
        take.assert_not_called()
        self.assertEqual(audit['quality_status'],'needs_review')
        self.assertEqual(audit['source_retakes'],[])
        self.assertEqual(len(self.calls),4)

    def test_failed_recheck_returns_needs_review_without_paid_motion_or_automatic_adoption(self):
        self.queue = [('assembly', self.raw), ('poster_review', self.fail_review),
            ('visual_repair', self.raw), ('poster_recheck', self.fail_review)]
        plan, _, metrics, _, audit = self.run_workflow()
        self.assertEqual(audit['quality_status'], 'needs_review')
        self.assertEqual(audit['quality_issues'], self.fail_review['issues'])
        self.assertEqual(audit['visual_repairs'], 1)
        self.assertEqual(metrics['quality_status'], 'needs_review')
        self.assertNotIn('motion', [call['phase'] for call in self.calls])
        self.assertNotIn('motion_review', [call['phase'] for call in self.calls])
        self.assertTrue(all(element['motion'] == 'none' and element['at'] == 0 for element in plan['elements']))
        self.assertEqual(self.render_mock.call_count, 1)
        self.assertFalse((self.folder / 'motion_schedule.json').exists())

    def test_motion_contract_failure_gets_exactly_one_format_repair(self):
        simultaneous = copy.deepcopy(self.schedule)
        for group in simultaneous['groups']:
            group['at'] = 0
        self.queue = [('assembly', self.raw), ('poster_review', self.pass_review),
            ('motion', simultaneous), ('motion', self.schedule), ('motion_review', self.pass_review)]
        self.run_workflow()
        motion_calls = [call for call in self.calls if call['phase'] == 'motion']
        self.assertEqual(len(motion_calls), 2)
        self.assertIn('validation_errors', motion_calls[1]['payload'])
        self.assertEqual(motion_calls[1]['payload']['previous_response'], simultaneous)
        self.assertTrue((self.folder / 'motion_response_0.json').is_file())
        self.assertTrue((self.folder / 'motion_response_1.json').is_file())
        self.assertFalse((self.folder / 'motion_response_2.json').exists())

    def test_second_invalid_motion_stops_before_render_and_never_requests_third_attempt(self):
        unknown_group = copy.deepcopy(self.schedule)
        unknown_group['groups'][1]['id'] = 'invented'
        self.queue = [('assembly', self.raw), ('poster_review', self.pass_review),
            ('motion', unknown_group), ('motion', unknown_group)]
        with self.assertRaisesRegex(ValueError, '格式修正仍未通过'):
            self.run_workflow()
        self.assertEqual([call['phase'] for call in self.calls].count('motion'), 2)
        self.assertEqual(self.render_mock.call_count, 0)
        self.assertFalse((self.folder / 'quality.json').exists())

    def test_transport_interruption_is_not_retried_or_replaced_by_another_model(self):
        self.queue = [('assembly', self.raw), ('poster_review', self.pass_review),
            ('motion', ConnectionError('upstream disconnected'))]
        with self.assertRaisesRegex(ConnectionError, 'upstream disconnected'):
            self.run_workflow()
        self.assertEqual([call['phase'] for call in self.calls], ['assembly', 'poster_review', 'motion'])
        self.assertEqual(self.render_mock.call_count, 0)
        self.assertFalse((self.folder / 'motion_response_1.json').exists())

    def test_invalid_static_content_is_rejected_before_render_and_repaired_only_once(self):
        missing = copy.deepcopy(self.raw)
        missing['labels'][-1]['text'] = '94 对患者'
        self.queue = [('assembly', missing), ('assembly', self.raw),
            ('poster_review', self.pass_review), ('motion', self.schedule), ('motion_review', self.pass_review)]
        self.run_workflow()
        self.assertEqual(self.still_mock.call_count, 1)
        self.assertIn('validation_errors', self.calls[1]['payload'])
        self.assertEqual(self.calls[1]['payload']['previous_response'], missing)

    def test_invalid_geometry_is_repaired_before_materialization_and_browser_render(self):
        inverted = copy.deepcopy(self.raw)
        inverted['labels'][-1]['box'] = [.8, .33, .42, .57]
        self.queue = [('assembly', inverted), ('assembly', self.raw),
            ('poster_review', self.pass_review), ('motion', self.schedule), ('motion_review', self.pass_review)]
        self.run_workflow()
        self.assertEqual(self.still_mock.call_count, 1)
        self.assertIn('倒置', self.calls[1]['payload']['validation_errors'])
        self.assertFalse((self.folder / 'assembly-assets-1').exists())

    def test_pixel_empty_crop_is_rejected_before_materialization(self):
        empty = copy.deepcopy(self.raw)
        empty['subjects'][0]['crop'] = [0, 0, .00001, .5]
        self.queue = [('assembly', empty), ('assembly', self.raw),
            ('poster_review', self.pass_review), ('motion', self.schedule), ('motion_review', self.pass_review)]
        self.run_workflow()
        self.assertIn('裁片为空', self.calls[1]['payload']['validation_errors'])
        self.assertEqual(self.still_mock.call_count, 1)

    def test_measured_overflow_uses_single_visual_repair_without_reviewing_missing_poster(self):
        def overflow_once(*args, **kwargs):
            if self.still_mock.call_count == 1:
                raise RuntimeError('OCV_LAYOUT_OVERFLOW:g:result:text_2[211x176/211x130]')
            return self.fake_still(*args, **kwargs)
        self.still_mock.side_effect = overflow_once
        self.queue = [('assembly', self.raw), ('visual_repair', self.raw),
            ('poster_recheck', self.pass_review), ('motion', self.schedule), ('motion_review', self.pass_review)]
        _, _, _, _, audit = self.run_workflow()
        self.assertEqual(audit['quality_status'], 'passed')
        self.assertEqual(audit['visual_repairs'], 1)
        self.assertEqual(audit['static_reviews'][0]['issues'][0]['kind'], 'readability')
        self.assertNotIn('poster', self.calls[1]['images'])
        self.assertNotIn('poster_review', [call['phase'] for call in self.calls])
        self.assertIn('OCV_LAYOUT_OVERFLOW', self.calls[1]['payload']['visual_issues']['issues'][0]['explanation'])

    def test_second_measured_overflow_fails_safely_without_motion_or_final_render(self):
        self.still_mock.side_effect = RuntimeError('OCV_LAYOUT_OVERFLOW:g:result:text_2[211x176/211x130]')
        self.queue = [('assembly', self.raw), ('visual_repair', self.raw)]
        with self.assertRaisesRegex(ValueError, '视觉返工后排版仍存在文字溢出或遮挡'):
            self.run_workflow()
        self.assertEqual(self.still_mock.call_count, 2)
        self.assertEqual(self.render_mock.call_count, 0)
        self.assertEqual([call['phase'] for call in self.calls], ['assembly', 'visual_repair'])
        self.assertFalse((self.folder / 'quality.json').exists())

    def test_other_renderer_errors_do_not_trigger_paid_visual_repair(self):
        self.still_mock.side_effect = RuntimeError('browser startup failed')
        self.queue = [('assembly', self.raw)]
        with self.assertRaisesRegex(RuntimeError, 'browser startup failed'):
            self.run_workflow()
        self.assertEqual([call['phase'] for call in self.calls], ['assembly'])
        self.assertEqual(self.render_mock.call_count, 0)

    def test_cancel_before_model_call_does_not_generate_or_render(self):
        self.event.set()
        with self.assertRaises(InterruptedError):
            self.run_workflow()
        self.assertEqual(self.call_mock.call_count, 0)
        self.assertEqual(self.still_mock.call_count, 0)
        self.assertEqual(self.render_mock.call_count, 0)

    def test_cancel_after_still_prevents_review_and_all_later_calls(self):
        self.queue = [('assembly', self.raw)]
        self.cancel_still = True
        with self.assertRaises(InterruptedError):
            self.run_workflow()
        self.assertEqual([call['phase'] for call in self.calls], ['assembly'])
        self.assertEqual(self.render_mock.call_count, 0)

    def test_cancel_during_final_review_cannot_mark_result_successful(self):
        self.queue = self.happy_queue()
        self.cancel_phase = 'motion_review'
        with self.assertRaises(InterruptedError):
            self.run_workflow()
        self.assertFalse((self.folder / 'quality.json').exists())

    def test_failed_motion_review_is_visible_and_not_auto_repaired(self):
        self.queue = self.happy_queue()
        self.queue[-1] = ('motion_review', self.fail_review)
        _, _, metrics, _, audit = self.run_workflow()
        self.assertEqual(audit['quality_status'], 'needs_review')
        self.assertEqual(metrics['quality_status'], 'needs_review')
        self.assertEqual(len(self.calls), 4)
        self.assertEqual(audit['visual_repairs'], 0)

    def test_recovered_original_title_requires_explicit_visual_check(self):
        self.inventory.labels=self.inventory.labels[1:]
        self.raw['recovered_text']=[dict(text='研究结果',source_box=[.1,.02,.9,.15])]
        checked={**self.pass_review,'recovered_text_checks':[dict(text='研究结果',matched=True,
            explanation='core 顶部框中逐字可见研究结果')],'type':'json_object'}
        self.queue=[('assembly',self.raw),('poster_review',checked),
                    ('motion',self.schedule),('motion_review',self.pass_review)]
        _,_,_,_,audit=self.run_workflow()
        self.assertEqual(audit['quality_status'],'passed')
        self.assertEqual(audit['recovered_text'][0]['text'],'研究结果')
        self.assertTrue(audit['static_reviews'][0]['recovered_text_checks'][0]['matched'])

    def test_recovered_text_cannot_pass_without_source_verification(self):
        self.inventory.labels=self.inventory.labels[1:]
        self.raw['recovered_text']=[dict(text='研究结果',source_box=[.1,.02,.9,.15])]
        self.queue=[('assembly',self.raw),('poster_review',self.pass_review),
                    ('poster_review',self.pass_review)]
        with self.assertRaisesRegex(ValueError,'必须逐项核对全部 recovered_text'):
            self.run_workflow()
        self.render_mock.assert_not_called()

    def test_review_transport_marker_does_not_relax_unknown_fields_or_failed_checks(self):
        self.assertTrue(workflow.Review.model_validate({**self.pass_review,'type':'json_object'}).passed)
        for invalid in ({**self.pass_review,'type':'object'},
                        {**self.pass_review,'unexpected':True},
                        {**self.pass_review,'recovered_text_checks':[dict(text='新增结论',matched=False,explanation='原图没有')]}):
            with self.assertRaises(ValueError):workflow.Review.model_validate(invalid)


if __name__ == '__main__':
    unittest.main()
