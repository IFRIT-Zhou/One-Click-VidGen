"""Bounded code-authoring tool loop, entirely offline and without a browser."""
import copy
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pydantic import ValidationError
from PIL import Image

from backend.app import vibe_code_workflow as workflow
from backend.app import vibe_cloud as api
from backend.app.vibe_cloud_client import CloudCallError, ModelResponseFormatError


def measured_scene():
    text = dict(id='result', text='49对患者', visible=True, font_size=48)
    subject = dict(id='subject_0', visible=True, crop=[0, 0, 1, 1])
    return dict(texts=[text], assets=[subject], diagnostics=[], frames=[
        dict(id='early', time=.4, texts=[], assets=[], diagnostics=[]),
        dict(id='hold', time=10, texts=[copy.deepcopy(text)],
             assets=[copy.deepcopy(subject)], diagnostics=[])])


def source_inventory():
    return SimpleNamespace(labels=[SimpleNamespace(text='49对患者')],
        subjects=[SimpleNamespace(crop=[0, 0, 1, 1], embedded_text=[])])


class CodeWorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = self.root / 'project'
        self.job_id = 'a' * 32
        self.folder = self.project / 'assets' / 'vibe_cloud' / self.job_id
        self.folder.mkdir(parents=True)
        self.path = self.folder / 'task.json'
        api._write(self.path, dict(task_id=self.job_id, shot_id='shot',
                                  status='running', stage='materials', usage={'model_calls': []}))
        self.sources = {'core': self.root / 'core.png'}
        self.assets = {'background': self.root / 'background.png',
                       'subject_0': self.root / 'subject.png',
                       'subject_0_original': self.root / 'original.png'}
        self.audio = self.root / 'audio.wav'
        self.context = dict(canvas=[640, 360], duration=12,
                            inventory_notes={'subject_0': '外围小主体和底座必须保留'},
                            subtitles=[dict(start=0, end=12, text='49对患者')])
        self.inventory = source_inventory()
        self.event = threading.Event()
        self.queue = []
        self.calls = []
        self.renders = []
        self.cancel_on_call = None
        self.cancel_on_render = None
        self.video_measurements = None
        self.preview_measurements = None
        self.preview_turn = dict(action='preview', summary='按原素材重建并编排',
            code='function Scene(ctx) { return <Text id="result">49对患者</Text>; }')
        self.measure_turn = dict(action='measure', summary='测量关键数值',
            probes=[dict(id='result', text='49对患者', font_size=48)])
        self.pass_review = dict(passed=True, summary='原文、主体与关键帧均已核验', issues=[],
            embedded_text_verified=True, recovered_text_verified=True,
            background_checks=[],
            subject_checks=[dict(asset_id='subject_0', complete=True,
                                 evidence='原片与透明合成中的外围主体和底座均完整')],
            layout_checks=[dict(text_id='result', faithful=True,
                                evidence='数字单位的比例及基线与原图局部一致')])
        self.fail_review = dict(passed=False, summary='需要修复标题遮挡', issues=['result 与主体互相遮挡，请调整基线'],
            embedded_text_verified=True, recovered_text_verified=True,
            subject_checks=copy.deepcopy(self.pass_review['subject_checks']),
            layout_checks=[dict(text_id='result', faithful=False,
                                evidence='局部对照中数字过大并挤占主体与单位的留白')])
        self.material_review = dict(version=workflow.material_review.VERSION, subjects=[
            dict(asset_id='subject_0', prepared_complete=True, original_complete=True,
                 source_crop_complete=True, source_crop_evidence='对照完整原图及裁框外邻域，主体全部轮廓均已在裁区之内',
                 source_edges={edge: dict(continues_outside=False, evidence='对照完整来源和边界两侧像素，该边界外仅有背景没有同一主体延伸')
                               for edge in ('top', 'right', 'bottom', 'left')},
                 recommended_asset_id='subject_0',
                 evidence='原裁片与透明合成对照显示外围主体、头部轮廓和底座均完整保留')], issues=[], retakes=[])
        self.checkpoint = {'material_decisions': dict(version=workflow.material_review.VERSION,
            fingerprint='test-hash', review=self.material_review)}
        self.call_limit = workflow.MAX_CALLS
        self.material_fingerprint = self.start_patch(
            'backend.app.vibe_code_workflow.material_review.fingerprint', return_value='test-hash')
        self.material_evidence = dict(
            images={'material_subject_0': self.root / 'material-subject-0.png'},
            report={'version': 'material-inspection-v1', 'subject_0': {'parts': '外围主体和底座'}})
        self.preview_evidence = []
        self.inspection_items = []
        self.material_inspection = self.start_patch(
            'backend.app.vibe_code_workflow.inspection.build_material_inspection',
            return_value=self.material_evidence)
        self.preview_inspection = self.start_patch(
            'backend.app.vibe_code_workflow.inspection.build_preview_inspection',
            side_effect=self.fake_preview_inspection)
        self.start_patch('backend.app.vibe_code_workflow.geometry_manifest',
                         return_value={'version': 'geometry-v1', 'canvas': [640, 360], 'subjects': []})
        self.start_patch('backend.app.vibe_code_workflow.renderer.runtime_geometry', return_value={})
        self.start_patch('backend.app.vibe_code_workflow.inventory_manifest', return_value={'texts': ['49对患者']})
        self.asset_specs = self.start_patch('backend.app.vibe_code_workflow.asset_specs',
            side_effect=lambda bound: {identity: {'width': 640, 'height': 360, 'file_label': path.name}
                                       for identity, path in bound.items()})
        self.start_patch('backend.app.vibe_code_workflow.subject_variants', return_value=[])
        self.source_manifest = self.start_patch('backend.app.vibe_code_workflow.source_retake.source_manifest',
            return_value=[dict(asset_id='subject_0', source_id='core', source_image_id='core',
                               current_source_crop=[0, 0, 1, 1], treatment='cutout', source_size=[640, 360])])
        self.model = self.start_patch('backend.app.vibe_code_workflow.api._call', side_effect=self.fake_call)
        self.render = self.start_patch('backend.app.vibe_code_workflow.renderer.run', side_effect=self.fake_run)
        self.mux = self.start_patch('backend.app.vibe_code_workflow.renderer.mux')
        self.guard_original = workflow.validate_rendered
        self.guard = self.start_patch('backend.app.vibe_code_workflow.validate_rendered', return_value=True)
        self.start_patch('backend.app.vibe_code_workflow.api.ffmpeg_binary', return_value='offline-ffmpeg')
        self.start_patch('backend.app.vibe_code_workflow.api._render_lock', threading.Lock())

    def start_patch(self, target, *args, **kwargs):
        active = patch(target, *args, **kwargs)
        mocked = active.start()
        self.addCleanup(active.stop)
        return mocked

    def fake_call(self, path, config, system, payload, images, cancelled, **kwargs):
        self.assertFalse(cancelled(), 'no model request after cancellation')
        self.assertIs(kwargs['thinking'], False)
        self.assertIn('schema', payload)
        self.calls.append(dict(payload=copy.deepcopy(payload), images=dict(images), system=system,
                               task=json.loads(path.read_text(encoding='utf-8'))))
        self.assertTrue(self.queue, 'unexpected extra model request')
        answer = self.queue.pop(0)
        if self.cancel_on_call == len(self.calls):
            self.event.set()
        if isinstance(answer, BaseException):
            raise answer
        return copy.deepcopy(answer)

    def fake_run(self, **kwargs):
        self.assertFalse(kwargs['cancelled'](), 'no render request after cancellation')
        self.renders.append(kwargs)
        output = kwargs['output_dir']
        if kwargs['mode'] == 'measure':
            result = dict(measurements={'measured': [dict(probe, width=180, height=55)
                        for probe in kwargs['measure_requests']]})
        else:
            (output / 'poster.png').write_bytes(b'offline-poster')
            (output / 'proof.jpg').write_bytes(b'offline-proof')
            if kwargs['mode'] == 'video':
                (output / 'silent.mp4').write_bytes(b'offline-video')
            result = dict(renderer=workflow.RENDERER, frames=360, fps=30,
                          measurements=copy.deepcopy(self.video_measurements
                              if kwargs['mode'] == 'video' and self.video_measurements is not None
                              else self.preview_measurements if self.preview_measurements is not None else measured_scene()))
        if self.cancel_on_render == kwargs['mode']:
            self.event.set()
        return result

    def fake_preview_inspection(self, inventory, sources, poster, measured, folder, canvas):
        self.assertEqual(poster.name, 'poster.png')
        self.assertEqual(canvas, self.context['canvas'])
        folder.mkdir(parents=True, exist_ok=True)
        evidence = dict(images={
            'preview_subject_0': folder / 'subject-0.png',
            'preview_text_result': folder / 'text-result.png'},
            report={'version': 'preview-inspection-v1', 'iteration': len(self.preview_evidence) + 1,
                    'subjects': ['subject_0'], 'texts': ['result'],
                    'items': copy.deepcopy(self.inspection_items) if self.inspection_items else [
                        dict(kind='subject', id='subject_0', image_id='preview_subject_0',
                             matched_render_ids=[row['id'] for row in measured['assets']]),
                        dict(kind='text', id='result', text='49对患者', image_id='preview_text_result',
                             matched_render_ids=['result'])]})
        for path in evidence['images'].values():
            Image.new('RGB', (16, 16), '#123456').save(path)
        self.preview_evidence.append(evidence)
        return evidence

    def compact_report(self, index):
        evidence = workflow.inspection.compact_preview_evidence(self.preview_evidence[index], max_images=4)
        return workflow.inspection_prompt_report(evidence['report'])

    def finish(self):
        return workflow.finish(self.project, 'shot', self.job_id, self.inventory, self.context,
            self.sources, self.assets, {'model': 'offline'},
            SimpleNamespace(instruction='保持原图比例，分阶段出现', pace='normal'),
            self.event, self.audio, 0, checkpoint=self.checkpoint, call_limit=self.call_limit)

    def test_measured_dimensions_feed_next_author_turn_and_only_passed_result_finishes(self):
        self.queue = [self.measure_turn, self.preview_turn, self.pass_review]
        plan, assets, metrics, output, quality = self.finish()
        self.assertEqual([row['mode'] for row in self.renders], ['measure', 'preview', 'video'])
        self.assertEqual(self.calls[1]['payload']['measurements']['result']['width'], 180)
        self.assertEqual(self.calls[1]['payload']['feedback']['tool'], 'measure')
        self.assertEqual(self.calls[1]['payload']['remaining']['measure_tools'], 1)
        self.assertEqual(self.guard.call_count, 2, 'preview and final render both need content validation')
        self.assertEqual(quality['quality_status'], 'passed')
        self.assertEqual((quality['model_calls'], quality['measure_tools'], quality['visual_repairs']), (3, 1, 0))
        self.assertEqual(plan['code'], self.preview_turn['code'])
        self.assertEqual(assets, self.assets)
        self.assertEqual(metrics['quality_status'], 'passed')
        self.mux.assert_called_once()
        self.assertEqual(self.mux.call_args.kwargs['audio'], self.audio)
        self.assertEqual(output.name, 'render-code-v3')

    def test_two_visual_repairs_receive_screenshots_dom_feedback_and_can_pass(self):
        self.queue = [self.preview_turn, self.fail_review] * 2 + [self.preview_turn, self.pass_review]
        _, _, _, _, quality = self.finish()
        self.assertEqual((quality['model_calls'], quality['visual_repairs']), (6, 2))
        self.assertEqual([row['mode'] for row in self.renders], ['preview'] * 3 + ['video'])
        self.assertEqual(self.calls[2]['payload']['previous_code'], self.preview_turn['code'])
        self.assertEqual(self.calls[2]['payload']['feedback']['review']['issues'], self.fail_review['issues'])
        self.assertIn('dom_measurements', self.calls[2]['payload']['feedback'])
        self.assertIn('previous_poster', self.calls[2]['images'])
        self.assertIn('previous_keyframes', self.calls[2]['images'])
        self.assertEqual([row['task']['attempt'] for row in self.calls], [0, 0, 1, 1, 2, 2])
        self.mux.assert_called_once()

    def test_author_sees_material_options_but_review_only_sees_composite_evidence(self):
        self.queue = [self.preview_turn, self.fail_review, self.preview_turn, self.pass_review]
        self.finish()
        self.material_inspection.assert_called_once()
        self.assertEqual(self.preview_inspection.call_count, 2)
        first_author, first_review, rework, final_review = self.calls
        for call in (first_author, rework):
            self.assertEqual(call['payload']['material_inspection'], self.material_evidence['report'])
            self.assertEqual(call['images']['material_subject_0'],
                             self.material_evidence['images']['material_subject_0'])
            self.assertEqual(call['images']['core'], self.sources['core'])
            self.assertEqual(call['images']['background'], self.assets['background'])
            self.assertNotIn('subject_0', call['images'])
            self.assertNotIn('subject_0_original', call['images'])
            self.assertNotIn(self.assets['subject_0'], call['images'].values())
            self.assertNotIn(self.assets['subject_0_original'], call['images'].values())
        for call in (first_review, final_review):
            self.assertNotIn('material_inspection', call['payload'])
            self.assertNotIn('subject_variants', call['payload'])
            self.assertNotIn('material_subject_0', call['images'])
            self.assertNotIn(self.material_evidence['images']['material_subject_0'], call['images'].values())
            self.assertNotIn(self.assets['subject_0_original'], call['images'].values())
            self.assertNotIn(self.assets['subject_0'], call['images'].values())
            self.assertEqual(call['payload']['active_assets'],
                             [{'asset_id': 'subject_0', 'image_id': 'poster',
                               'comparison_image_ids': ['preview_subject_0']}])
            self.assertEqual(set(call['payload']['material_specs']), {'subject_0'})
            self.assertEqual(call['payload']['material_decisions'], self.material_review)
            self.assertEqual(call['images']['core'], self.sources['core'])
            self.assertNotIn('background', call['images'])
            self.assertNotIn(self.assets['background'], call['images'].values())
            self.assertEqual(set(call['images']),
                             {'core', 'poster', 'keyframes', 'preview_subject_0', 'preview_text_result'})
        self.assertNotIn('preview_subject_0', first_author['images'])
        self.assertEqual(first_review['payload']['inventory_notes'], self.context['inventory_notes'])
        self.assertEqual(first_review['payload']['review_targets'],
                         {'subject_checks': ['subject_0'], 'layout_checks': ['result'], 'background_checks': []})
        self.assertEqual(first_review['payload']['preview_inspection'], self.compact_report(0))
        self.assertEqual(rework['payload']['feedback']['preview_inspection'], self.preview_evidence[0]['report'])
        self.assertEqual(final_review['payload']['preview_inspection'], self.compact_report(1))
        for call, evidence in ((first_review, self.preview_evidence[0]),
                               (rework, self.preview_evidence[0]),
                               (final_review, self.preview_evidence[1])):
            for name, path in evidence['images'].items():
                self.assertEqual(call['images'][name], path)
        self.assertNotIn(self.preview_evidence[0]['images']['preview_subject_0'], final_review['images'].values())
        self.assertIn('previous_poster', rework['images'])
        self.assertIn('previous_keyframes', rework['images'])
        self.assertEqual(self.renders[0]['assets'], self.assets,
                         'renderer still needs original alpha assets, not composite evidence sheets')

    def test_review_of_selected_original_never_receives_rejected_cutout_even_on_format_repair(self):
        self.material_review['subjects'][0].update(
            prepared_complete=False, original_complete=True, recommended_asset_id='subject_0_original')
        self.preview_measurements = measured_scene()
        self.preview_measurements['assets'][0]['id'] = 'subject_0_original'
        self.preview_measurements['frames'][-1]['assets'][0]['id'] = 'subject_0_original'
        self.pass_review['subject_checks'][0]['asset_id'] = 'subject_0_original'
        self.queue = [self.preview_turn, {'passed': 'yes'}, self.pass_review]
        self.finish()
        author, review, retry = self.calls
        self.assertIn('material_subject_0', author['images'])
        self.assertIn('subject_0', author['payload']['material_specs'])
        self.assertIn('subject_0_original', author['payload']['material_specs'])
        for call in (review, retry):
            self.assertEqual(call['payload']['active_assets'], [
                {'asset_id': 'subject_0_original', 'image_id': 'poster',
                 'comparison_image_ids': ['preview_subject_0']}])
            self.assertNotIn(self.assets['subject_0_original'], call['images'].values())
            self.assertNotIn(self.assets['subject_0'], call['images'].values())
            self.assertNotIn('used_asset_subject_0', call['images'])
            self.assertNotIn('used_asset_subject_0_original', call['images'])
            self.assertNotIn('background', call['images'])
            self.assertNotIn('material_subject_0', call['images'])
            self.assertNotIn('material_inspection', call['payload'])
            self.assertNotIn('subject_variants', call['payload'])
            self.assertEqual(set(call['payload']['material_specs']), {'subject_0_original'})
            self.assertEqual(call['payload']['material_decisions'], self.material_review)
            self.assertIn('preview_subject_0', call['images'])
            self.assertIn('preview_text_result', call['images'])
            self.assertIn('poster', call['images'])
            self.assertIn('keyframes', call['images'])
        self.assertEqual(review['images'], retry['images'])

    def test_changed_variant_rebuilds_active_review_assets_without_previous_candidate_leak(self):
        original_measurements = measured_scene()
        original_measurements['assets'][0]['id'] = 'subject_0_original'
        original_measurements['frames'][-1]['assets'][0]['id'] = 'subject_0_original'
        self.video_measurements = original_measurements
        self.pass_review['subject_checks'][0]['asset_id'] = 'subject_0_original'
        real_run = self.fake_run
        previews = 0
        def render_switch(**kwargs):
            nonlocal previews
            result = real_run(**kwargs)
            if kwargs['mode'] == 'preview':
                previews += 1
                if previews == 2:
                    result['measurements'] = copy.deepcopy(original_measurements)
            return result
        self.render.side_effect = render_switch
        self.queue = [self.preview_turn, self.fail_review, self.preview_turn, self.pass_review]
        self.finish()
        first_review, rework, final_review = self.calls[1:]
        self.assertEqual(first_review['payload']['active_assets'][0]['asset_id'], 'subject_0')
        self.assertNotIn(self.assets['subject_0'], first_review['images'].values())
        self.assertNotIn(self.assets['subject_0_original'], first_review['images'].values())
        self.assertIn('material_subject_0', rework['images'])
        self.assertEqual(final_review['payload']['active_assets'][0]['asset_id'], 'subject_0_original')
        self.assertEqual(final_review['payload']['active_assets'][0]['image_id'], 'poster')
        self.assertNotIn(self.assets['subject_0_original'], final_review['images'].values())
        self.assertNotIn('used_asset_subject_0', final_review['images'])
        self.assertNotIn(self.assets['subject_0'], final_review['images'].values())
        self.assertNotIn('material_subject_0', final_review['images'])
        self.assertNotIn(self.preview_evidence[0]['images']['preview_subject_0'], final_review['images'].values())
        self.assertEqual(set(final_review['payload']['material_specs']), {'subject_0_original'})

    def test_review_sends_at_most_four_roi_images_while_rework_keeps_full_evidence(self):
        base_inspection = self.fake_preview_inspection
        def many_regions(*args):
            evidence = base_inspection(*args)
            folder = args[4]
            for index in range(6):
                identity = f'preview_extra_{index}'
                path = folder / f'extra-{index}.png'
                Image.new('RGB', (16, 16), '#567890').save(path)
                evidence['images'][identity] = path
                evidence['report']['items'].append(dict(
                    id=f'source_label_{index}', kind='text', text=f'额外局部 {index}',
                    image_id=identity, matched_render_ids=['subject_0'] if index == 5 else ['result'],
                    scale_hint={'height_ratio': 1.8, 'width_ratio': 1.0, 'soft_hint_only': True}))
            return evidence
        self.preview_inspection.side_effect = many_regions
        self.queue = [self.preview_turn, self.fail_review, self.preview_turn, self.pass_review]
        self.finish()
        first_review, rework, final_review = self.calls[1:]
        expected_rois = {'preview_subject_0', 'preview_text_result', 'preview_extra_0', 'preview_extra_1'}
        for call in (first_review, final_review):
            self.assertEqual(set(call['images']), {'core', 'poster', 'keyframes'} | expected_rois)
            self.assertEqual(len(call['images']), 7)
            report = call['payload']['preview_inspection']
            self.assertEqual({row['image_id'] for row in report['items']}, expected_rois)
            self.assertEqual(report['selection']['max_images'], 4)
            self.assertEqual(report['selection']['omitted_roi_count'], 4)
            self.assertTrue(all(row['image_id'] in expected_rois for row in call['payload']['fidelity_focus']))
            self.assertEqual(call['payload']['active_assets'][0]['comparison_image_ids'], ['preview_subject_0'])
        self.assertEqual(len(self.preview_evidence[0]['images']), 8)
        self.assertEqual(len(rework['payload']['feedback']['preview_inspection']['items']), 8)
        self.assertIn('material_subject_0', rework['images'])
        self.assertIn('background', rework['images'])
        for image_id, path in self.preview_evidence[0]['images'].items():
            self.assertEqual(rework['images'][image_id], path)
        self.assertNotIn(self.preview_evidence[0]['images']['preview_extra_0'], final_review['images'].values())
        saved = json.loads((self.folder / 'code-inspection-1.json').read_text(encoding='utf-8'))
        self.assertEqual(len(saved['items']), 8, 'compaction must not overwrite the complete persisted inspection')

    def test_actual_crop_and_css_mask_remain_visible_to_reviewer_without_raw_asset_image(self):
        self.preview_measurements = measured_scene()
        actual = self.preview_measurements['assets'][0]
        actual.update(crop=[.03, .02, .97, .89], box=[120, 70, 210, 220], opacity=.94,
                      mask_image='linear-gradient(to bottom, black 85%, transparent 100%)',
                      clip_path='none')
        self.queue = [self.preview_turn, self.pass_review]
        self.finish()
        review = self.calls[1]
        self.assertEqual(review['payload']['dom_summary']['assets'][0], actual)
        self.assertEqual(review['payload']['active_assets'][0]['image_id'], 'poster')
        self.assertNotIn(self.assets['subject_0'], review['images'].values())
        self.assertNotIn(self.assets['subject_0_original'], review['images'].values())
        self.assertNotIn('background', review['images'])
        self.assertEqual(review['payload']['material_decisions'], self.material_review)

    def test_dom_rejection_returns_local_comparison_to_rework_without_visual_review(self):
        self.queue = [self.preview_turn, self.preview_turn, self.pass_review]
        self.guard.side_effect = [ValueError('实际文字字号过小'), True, True]
        self.finish()
        self.assertEqual(self.calls[1]['task']['stage'], 'code_author')
        self.assertEqual(self.calls[1]['payload']['feedback']['preview_inspection'],
                         self.preview_evidence[0]['report'])
        self.assertEqual(self.calls[1]['images']['preview_subject_0'],
                         self.preview_evidence[0]['images']['preview_subject_0'])
        self.assertIn('实际文字字号过小', self.calls[1]['payload']['feedback']['validation_error'])
        self.assertEqual(self.calls[2]['task']['stage'], 'code_review')

    def test_schema_repair_receives_actual_rejected_response_not_stale_code(self):
        bad = dict(self.preview_turn, probes=self.measure_turn['probes'])
        bad['code'] = 'function Scene(ctx) { return <Text id="changed">new draft</Text>; }'
        self.queue = [bad, self.preview_turn, self.pass_review]
        self.finish()
        self.assertEqual(self.calls[1]['payload']['feedback']['previous_response'], bad)

    def test_compile_repair_does_not_consume_visual_rework_budget(self):
        real_run = self.fake_run
        attempts = 0
        def compile_once(**kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise ValueError('OCV_CODE_CONTRACT: invalid local helper')
            return real_run(**kwargs)
        self.render.side_effect = compile_once
        self.queue = [self.preview_turn, self.preview_turn, self.pass_review]
        _, _, _, _, quality = self.finish()
        self.assertEqual(quality['compile_repairs'], 1)
        self.assertEqual(quality['visual_repairs'], 0)

    def test_compile_schema_and_measure_repairs_preserve_prior_visual_problems_and_images(self):
        self.inspection_items = [dict(kind='text', id='source_label_0', text='49对患者',
            image_id='preview_text_result', matched_render_ids=['result'],
            scale_hint={'height_ratio': 1.8, 'width_ratio': 1.1, 'soft_hint_only': True},
            intersecting_texts=[measured_scene()['texts'][0]])]
        original = dict(self.preview_turn,
            code='function Scene(ctx) { return <Asset id="subject_0" variant="original" />; }')
        schema_error = dict(original, probes=self.measure_turn['probes'])
        fixed = dict(self.preview_turn,
            code='function Scene(ctx) { return <Asset id="subject_0_original" anchor="source" />; }')
        self.queue = [self.preview_turn, self.fail_review, original, schema_error,
                      self.measure_turn, fixed, self.pass_review]
        real_run = self.fake_run
        def reject_variant(**kwargs):
            if kwargs['code'] == original['code']:
                raise ValueError('OCV_CODE_CONTRACT: Asset variant is not supported')
            return real_run(**kwargs)
        self.render.side_effect = reject_variant
        _, _, _, _, quality = self.finish()
        initial = self.calls[2]
        for call in self.calls[3:6]:
            feedback = call['payload']['feedback']
            self.assertEqual(feedback['review'], initial['payload']['feedback']['review'])
            self.assertEqual(feedback['fidelity_focus'], initial['payload']['feedback']['fidelity_focus'])
            self.assertEqual(feedback['preview_inspection'], initial['payload']['feedback']['preview_inspection'])
            self.assertEqual(feedback['dom_measurements'], initial['payload']['feedback']['dom_measurements'])
            self.assertEqual(call['images'], initial['images'])
        self.assertIn('Asset variant', self.calls[3]['payload']['feedback']['validation_error'])
        self.assertEqual(self.calls[4]['payload']['feedback']['previous_response'], schema_error)
        self.assertEqual(self.calls[5]['payload']['feedback']['tool'], 'measure')
        self.assertEqual(self.calls[5]['payload']['measurements']['result']['width'], 180)
        self.assertEqual(quality['compile_repairs'], 1)
        self.assertEqual(quality['visual_repairs'], 1)
        self.assertEqual(quality['model_calls'], 7)

    def test_measure_budget_rejection_does_not_erase_pending_visual_feedback(self):
        self.inspection_items = [dict(kind='text', id='source_label_0', text='49对患者',
            image_id='preview_text_result', matched_render_ids=['result'],
            scale_hint={'height_ratio': 1.7, 'width_ratio': 1.0})]
        self.queue = ([self.preview_turn, self.fail_review] + [self.measure_turn] * 3
                      + [self.preview_turn, self.pass_review])
        _, _, _, _, quality = self.finish()
        before, after = self.calls[2], self.calls[5]
        self.assertIn('测量次数已用完', after['payload']['feedback']['error'])
        for key in ('review', 'fidelity_focus', 'preview_inspection', 'dom_measurements'):
            self.assertEqual(after['payload']['feedback'][key], before['payload']['feedback'][key])
        self.assertEqual(after['images'], before['images'])
        self.assertEqual(quality['measure_tools'], 2)
        self.assertEqual(quality['model_calls'], 7)

    def test_review_payload_is_compact_but_retains_actual_text_and_temporal_evidence(self):
        self.inspection_items = [dict(kind='text', id='source_label_0', text='49对患者',
            image_id='preview_text_result', matched_render_ids=['result'],
            scale_hint={'height_ratio': 1.8, 'width_ratio': 1.0, 'soft_hint_only': True},
            intersecting_texts=[measured_scene()['texts'][0]])]
        self.queue = [self.preview_turn, self.pass_review]
        _, _, _, _, quality = self.finish()
        review = self.calls[1]['payload']
        self.assertEqual(next(iter(review)), 'fidelity_focus')
        self.assertNotIn('code', review)
        self.assertNotIn('dom_measurements', review)
        self.assertEqual(review['dom_summary']['texts'], measured_scene()['texts'])
        self.assertEqual(review['dom_summary']['assets'], measured_scene()['assets'])
        self.assertEqual(review['dom_summary']['frames'][-1]['time'], 10)
        self.assertEqual(review['dom_summary']['frames'][-1]['visible_text_ids'], ['result'])
        self.assertNotIn('texts', review['dom_summary']['frames'][-1])
        item = review['preview_inspection']['items'][0]
        self.assertNotIn('intersecting_texts', item)
        self.assertEqual(item['matched_render_ids'], ['result'])
        self.assertEqual(item['scale_hint']['height_ratio'], 1.8)
        self.assertEqual(review['subtitles'], self.context['subtitles'])
        self.assertEqual(quality['quality_status'], 'passed', 'scale clue alone is not a deterministic rejection')

    def test_third_visual_rejection_keeps_non_adoptable_inspection_video(self):
        self.queue = [self.preview_turn, self.fail_review] * 3
        _, _, _, _, quality = self.finish()
        self.assertEqual(self.model.call_count, 6)
        self.assertEqual([row['mode'] for row in self.renders], ['preview'] * 3 + ['video'])
        self.mux.assert_called_once()
        self.assertEqual(quality['quality_status'], 'needs_review')
        from backend.app.vibe_adoption import _require_quality_passed
        from fastapi import HTTPException
        with self.assertRaises(HTTPException):
            _require_quality_passed(quality)

    def test_invalid_dom_has_separate_bounded_contract_budget_without_visual_review_or_mux(self):
        self.queue = [self.preview_turn] * 3
        self.guard.side_effect = ValueError('实际文字49被改为94')
        with self.assertRaisesRegex(ValueError, '0 次预览/0 轮返工'):
            self.finish()
        self.assertEqual(self.model.call_count, 3)
        self.assertEqual(self.guard.call_count, 3)
        self.assertIn('实际文字49被改为94', self.calls[1]['payload']['feedback']['validation_error'])
        self.mux.assert_not_called()

    def test_two_invalid_dom_drafts_leave_visual_rework_available(self):
        self.guard.side_effect = [ValueError('不完整'), ValueError('仍不完整'), True, True, True]
        self.queue = [self.preview_turn] * 3 + [self.fail_review, self.preview_turn, self.pass_review]
        _, _, _, _, quality = self.finish()
        self.assertEqual(quality['compile_repairs'], 2)
        self.assertEqual(quality['visual_repairs'], 1)
        self.assertEqual(quality['quality_status'], 'passed')
        self.assertEqual(self.calls[2]['payload']['remaining']['previews'], 3)
        self.assertEqual(self.model.call_count, 6)

    def test_invalid_return_contract_cannot_exceed_eight_model_calls(self):
        self.queue = [{'action': 'arbitrary_tool'}] * 8
        with self.assertRaisesRegex(ValueError, '未通过'):
            self.finish()
        self.assertEqual(self.model.call_count, 7, 'reserve the eighth call for independent review')
        self.render.assert_not_called()
        self.mux.assert_not_called()
        quality = json.loads((self.folder / 'quality.json').read_text(encoding='utf-8'))
        self.assertEqual(quality['model_calls'], 7)
        self.assertEqual(quality['quality_status'], 'needs_review')

    def test_repeated_measure_request_cannot_execute_more_than_two_measure_tools(self):
        self.queue = [self.measure_turn] * 8
        with self.assertRaises(ValueError):
            self.finish()
        self.assertEqual(self.model.call_count, 7, 'never spend the last review slot on another author call')
        self.assertEqual([row['mode'] for row in self.renders], ['measure', 'measure'])
        self.assertIn('测量次数已用完', self.calls[3]['payload']['feedback']['error'])
        self.mux.assert_not_called()

    def test_no_eighth_author_request_when_only_the_review_budget_remains(self):
        self.queue = [{'action': 'wrong'}] * 7 + [self.preview_turn]
        with self.assertRaises(ValueError):
            self.finish()
        self.assertEqual(self.model.call_count, 7)
        self.assertEqual(self.queue, [self.preview_turn])
        self.render.assert_not_called()
        self.mux.assert_not_called()

    def test_two_measures_three_previews_and_reviews_can_use_exactly_eight_calls(self):
        self.queue = [self.measure_turn] * 2 + [self.preview_turn, self.fail_review] * 2 + [self.preview_turn, self.pass_review]
        _, _, _, _, quality = self.finish()
        self.assertEqual(self.model.call_count, 8)
        self.assertEqual((quality['model_calls'], quality['measure_tools'], quality['visual_repairs']), (8, 2, 2))
        self.assertEqual([row['mode'] for row in self.renders], ['measure'] * 2 + ['preview'] * 3 + ['video'])
        self.mux.assert_called_once()

    def test_invalid_eighth_review_cannot_trigger_ninth_format_repair(self):
        self.queue = [self.measure_turn] * 2 + [self.preview_turn, self.fail_review] * 2 + [self.preview_turn, {'passed': 'yes'}]
        _, _, _, _, quality = self.finish()
        self.assertEqual(self.model.call_count, 8)
        self.assertEqual([row['mode'] for row in self.renders], ['measure'] * 2 + ['preview'] * 3 + ['video'])
        self.assertEqual(quality['quality_status'], 'needs_review')
        self.mux.assert_called_once()

    def test_internal_checkpoint_replays_saved_code_before_spending_remaining_review_call(self):
        self.queue = [self.pass_review]
        result = workflow.finish(self.project, 'shot', self.job_id, self.inventory, self.context,
            self.sources, self.assets, {'model': 'offline'},
            SimpleNamespace(instruction='', pace='normal'), self.event, self.audio, 0,
            checkpoint={**self.checkpoint, 'turn': self.preview_turn, 'measurements': {}}, call_limit=1)
        self.assertEqual([r['mode'] for r in self.renders], ['preview', 'video'])
        self.assertEqual(self.calls[0]['task']['stage'], 'code_review')
        self.assertEqual(result[4]['quality_status'], 'passed')
        self.assertEqual(self.model.call_count, 1)

    def test_review_format_error_has_one_bounded_format_retry_without_redesign(self):
        self.queue = [self.preview_turn, {'passed': 'yes'}, self.pass_review]
        _, _, _, _, quality = self.finish()
        self.assertEqual(self.model.call_count, 3)
        self.assertEqual([row['mode'] for row in self.renders], ['preview', 'video'])
        self.assertEqual(self.calls[1]['system'], self.calls[2]['system'])
        self.assertIn('validation_error', self.calls[2]['payload'])
        self.assertEqual(quality['quality_status'], 'passed')
        self.mux.assert_called_once()

    def test_missing_coverage_repair_receives_rejected_review_targets_and_same_evidence(self):
        incomplete = copy.deepcopy(self.pass_review)
        incomplete['subject_checks'] = []
        self.queue = [self.preview_turn, incomplete, self.pass_review]
        _, _, _, _, quality = self.finish()
        self.assertEqual(self.model.call_count, 3)
        repair = self.calls[2]
        self.assertEqual(repair['task']['stage'], 'code_review')
        self.assertEqual(repair['payload']['previous_response'], incomplete)
        self.assertIn('subject_checks', repair['payload']['validation_error'])
        self.assertEqual(repair['payload']['review_targets'], self.calls[1]['payload']['review_targets'])
        self.assertEqual(repair['payload']['preview_inspection'], self.compact_report(0))
        self.assertEqual(repair['images'], self.calls[1]['images'])
        self.assertEqual(quality['quality_status'], 'passed')

    def test_two_incomplete_reviews_fail_without_unbounded_schema_repair(self):
        incomplete = copy.deepcopy(self.pass_review)
        incomplete['layout_checks'] = []
        self.queue = [self.preview_turn, incomplete, incomplete, self.pass_review]
        with self.assertRaisesRegex(workflow.ResponseContractError, 'layout_checks'):
            self.finish()
        self.assertEqual(self.model.call_count, 3)
        self.assertEqual(len(self.queue), 1)
        self.assertEqual([row['mode'] for row in self.renders], ['preview'])
        self.mux.assert_not_called()

    def test_eighth_review_missing_item_cannot_request_ninth_schema_repair(self):
        incomplete = copy.deepcopy(self.pass_review)
        incomplete['subject_checks'] = []
        self.queue = ([self.measure_turn] * 2 + [self.preview_turn, self.fail_review] * 2
                      + [self.preview_turn, incomplete, self.pass_review])
        _, _, _, _, quality = self.finish()
        self.assertEqual(self.model.call_count, 8)
        self.assertEqual(quality['quality_status'], 'needs_review')
        self.assertEqual(self.queue, [self.pass_review])
        self.assertEqual(self.preview_inspection.call_count, 3)

    def test_review_schema_repair_consumes_budget_without_granting_extra_visual_round(self):
        incomplete = copy.deepcopy(self.pass_review)
        incomplete['layout_checks'] = []
        self.queue = [self.preview_turn, incomplete, self.fail_review,
                      self.preview_turn, self.fail_review, self.preview_turn, self.pass_review]
        _, _, _, _, quality = self.finish()
        self.assertEqual(self.model.call_count, 7)
        self.assertEqual(quality['visual_repairs'], 2)
        self.assertEqual([row['task']['attempt'] for row in self.calls], [0, 0, 0, 1, 1, 2, 2])
        self.assertEqual([row['mode'] for row in self.renders], ['preview'] * 3 + ['video'])

    def test_runtime_dependency_failure_does_not_spend_cloud_repair_calls(self):
        self.queue = [self.preview_turn]
        self.render.side_effect = RuntimeError('renderer dependency missing')
        with self.assertRaisesRegex(RuntimeError, 'dependency missing'):
            self.finish()
        self.model.assert_called_once()
        self.mux.assert_not_called()

    def test_upstream_interruption_during_author_is_not_automatically_retried(self):
        self.queue = [CloudCallError('upstream interrupted')]
        with self.assertRaisesRegex(CloudCallError, 'upstream interrupted'):
            self.finish()
        self.model.assert_called_once()
        self.render.assert_not_called()
        self.mux.assert_not_called()

    def test_upstream_interruption_during_review_does_not_call_author_again(self):
        self.queue = [self.preview_turn, CloudCallError('review transport interrupted')]
        with self.assertRaisesRegex(CloudCallError, 'review transport interrupted'):
            self.finish()
        self.assertEqual(self.model.call_count, 2)
        self.assertEqual([row['mode'] for row in self.renders], ['preview'])
        self.mux.assert_not_called()

    def test_cancel_before_start_does_not_call_model_or_renderer(self):
        self.event.set()
        with self.assertRaises(InterruptedError):
            self.finish()
        self.model.assert_not_called()
        self.render.assert_not_called()
        self.mux.assert_not_called()

    def test_cancel_during_author_does_not_render_returned_code(self):
        self.queue = [self.preview_turn]
        self.cancel_on_call = 1
        with self.assertRaises(InterruptedError):
            self.finish()
        self.model.assert_called_once()
        self.render.assert_not_called()
        self.mux.assert_not_called()

    def test_cancel_after_preview_does_not_start_review_or_mux(self):
        self.queue = [self.preview_turn]
        self.cancel_on_render = 'preview'
        with self.assertRaises(InterruptedError):
            self.finish()
        self.model.assert_called_once()
        self.mux.assert_not_called()

    def test_cancel_during_final_video_does_not_enter_mux_or_mark_passed(self):
        self.queue = [self.preview_turn, self.pass_review]
        self.cancel_on_render = 'video'
        with self.assertRaises(InterruptedError):
            self.finish()
        self.assertEqual(self.model.call_count, 2)
        self.mux.assert_not_called()
        self.assertFalse((self.folder / 'quality.json').exists())

    def test_final_video_content_is_revalidated_and_cannot_mux_changed_number(self):
        self.queue = [self.preview_turn, self.pass_review]
        self.guard.side_effect = self.guard_original
        self.video_measurements = measured_scene()
        self.video_measurements['texts'][0]['text'] = '94对患者'
        with self.assertRaisesRegex(ValueError, '数值'):
            self.finish()
        self.assertEqual(self.guard.call_count, 2)
        self.assertEqual([row['mode'] for row in self.renders], ['preview', 'video'])
        self.mux.assert_not_called()
        self.assertFalse((self.folder / 'quality.json').exists())

    def test_missing_measurement_row_stops_before_author_can_use_fabricated_size(self):
        self.queue = [self.measure_turn]
        self.render.side_effect = lambda **_: {'measurements': {'measured': []}}
        with self.assertRaisesRegex(ValueError, '缺少本次请求项目'):
            self.finish()
        self.model.assert_called_once()
        self.mux.assert_not_called()

    def test_first_material_review_is_counted_and_persisted_before_authoring(self):
        self.checkpoint = None
        self.queue = [self.material_review, self.preview_turn, self.pass_review]
        _, _, _, _, quality = self.finish()
        self.assertEqual([row['task']['stage'] for row in self.calls],
                         ['code_material_review', 'code_author', 'code_review'])
        self.assertEqual(quality['model_calls'], 3)
        self.assertEqual(self.calls[1]['payload']['remaining']['model_calls'], 7)
        for call in self.calls[1:]:
            self.assertEqual(call['payload']['material_decisions'], self.material_review)
        saved = json.loads((self.folder / 'code-material-decisions.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['fingerprint'], 'test-hash')
        self.assertEqual(saved['review'], self.material_review)
        self.assertIs(saved['cached'], False)
        self.assertEqual(quality['material_decisions'], saved)
        self.assertNotIn('subject_0', self.calls[0]['images'])
        self.assertIn('material_subject_0', self.calls[0]['images'])

    def test_valid_material_cache_does_not_spend_a_model_call(self):
        self.queue = [self.preview_turn, self.pass_review]
        _, _, _, _, quality = self.finish()
        self.assertEqual(quality['model_calls'], 2)
        self.assertIs(quality['material_decisions']['cached'], True)
        self.assertEqual([row['task']['stage'] for row in self.calls], ['code_author', 'code_review'])

    def retained_base_case(self):
        self.inventory.background_retained=[SimpleNamespace(name='透明示意物体',source_box=[.1,.2,.3,.6])]
        self.pass_review['background_checks']=[dict(base_id='base_0',preserved=True,compositing_ok=True,
            evidence='完整原图与编辑底图中的透明轮廓及材质一致，合成画面没有重复或遮盖。')]

    def test_retained_base_is_checked_against_actual_background_without_duplicate_asset(self):
        self.retained_base_case()
        self.queue=[self.preview_turn,self.pass_review]
        _,_,_,_,quality=self.finish()
        review=self.calls[1]
        self.assertEqual(review['images']['background'],self.assets['background'])
        self.assertEqual(review['images']['core'],self.sources['core'])
        self.assertIn('poster',review['images'])
        self.assertEqual(review['payload']['review_targets']['background_checks'],['base_0'])
        self.assertEqual(review['payload']['review_targets']['subject_checks'],['subject_0'])
        self.assertNotIn('base_0',self.assets)
        self.assertEqual(quality['quality_status'],'passed')
        self.assertEqual(self.model.call_count,2)

    def test_changed_background_subject_stops_without_charging_for_another_image_or_authoring(self):
        self.retained_base_case()
        failed=copy.deepcopy(self.pass_review)
        failed.update(passed=False,issues=['base_0编辑底图内的主体轮廓缺失，应修改背景处理方案'])
        failed['background_checks'][0].update(preserved=False,evidence='对照core与实际background，物体边缘已被删除，不是代码层遮挡。')
        self.queue=[self.preview_turn,failed]
        with self.assertRaisesRegex(ValueError,'底图保留主体未通过原图对照'):
            self.finish()
        self.assertEqual(self.model.call_count,2)
        self.assertEqual([row['mode'] for row in self.renders],['preview'])
        self.mux.assert_not_called()
        self.assertTrue((self.folder/'code-reviews.json').is_file())
        self.assertFalse((self.folder/'quality.json').exists())

    def test_background_is_intact_but_code_overlap_can_be_repaired_by_model(self):
        self.retained_base_case()
        failed=copy.deepcopy(self.pass_review)
        failed.update(passed=False,issues=['文本底板盖住base_0，移动该底板，不必重做底图'])
        failed['background_checks'][0].update(compositing_ok=False,
            evidence='core与background的主体完整，poster中被新增文字底板遮住一侧，可用代码调整。')
        self.queue=[self.preview_turn,failed,self.preview_turn,self.pass_review]
        _,_,_,_,quality=self.finish()
        self.assertEqual(self.model.call_count,4)
        self.assertIn('文本底板',self.calls[2]['payload']['feedback']['review']['issues'][0])
        self.assertEqual(quality['quality_status'],'passed')
        self.mux.assert_called_once()

    def test_first_material_format_repair_counts_in_total_budget(self):
        self.checkpoint = None
        self.queue = [{'unexpected': 'wrong'}, self.material_review, self.preview_turn, self.pass_review]
        _, _, _, _, quality = self.finish()
        self.assertEqual(quality['model_calls'], 4)
        self.assertEqual(self.calls[0]['images'], self.calls[1]['images'])
        self.assertEqual(self.calls[1]['task']['stage'], 'code_material_review')
        self.assertIn('validation_error', self.calls[1]['payload'])
        self.assertEqual(self.calls[2]['payload']['remaining']['model_calls'], 6)

    def two_subject_material_case(self):
        self.checkpoint=None
        self.inventory.subjects.append(copy.deepcopy(self.inventory.subjects[0]))
        self.assets.update(subject_1=self.root/'second.png',subject_1_original=self.root/'second-original.png')
        second=copy.deepcopy(self.material_review['subjects'][0])
        second.update(asset_id='subject_1',recommended_asset_id='subject_1')
        self.material_review['subjects'].append(second)
        self.source_manifest.return_value.append(dict(asset_id='subject_1',source_id='core',
            source_image_id='core',current_source_crop=[0,0,1,1],treatment='cutout',source_size=[640,360]))

    def test_two_subject_repair_receives_full_identity_contract_not_a_local_patch(self):
        self.two_subject_material_case()
        incomplete=copy.deepcopy(self.material_review)
        incomplete['subjects'].pop()
        self.queue=[incomplete,self.material_review,CloudCallError('offline author boundary')]
        with self.assertRaisesRegex(CloudCallError,'offline author boundary'):
            self.finish()
        self.assertEqual(self.model.call_count,3)
        for call in self.calls[:2]:
            contract=call['payload']['response_contract']
            self.assertEqual(contract['required_subject_ids'],['subject_0','subject_1'])
            self.assertEqual(contract['subject_count'],2)
            self.assertEqual(contract['response_mode'],'complete_report')
            schema=call['payload']['schema']
            self.assertEqual(schema['properties']['subjects']['minItems'],2)
            self.assertEqual(schema['properties']['subjects']['maxItems'],2)
            self.assertEqual(schema['$defs']['SubjectMaterialReview']['properties']['asset_id']['enum'],
                             ['subject_0','subject_1'])
        self.assertEqual(self.calls[1]['payload']['previous_response'],incomplete)
        self.assertIn('不是只输出报错项',self.calls[1]['payload']['instruction'])
        saved=json.loads((self.folder/'code-material-decisions.json').read_text(encoding='utf-8'))
        self.assertEqual([row['asset_id'] for row in saved['review']['subjects']],['subject_0','subject_1'])
        self.render.assert_not_called()

    def test_unchanged_crop_repair_cannot_drop_another_subject_or_reuse_old_verdict(self):
        self.two_subject_material_case()
        first=copy.deepcopy(self.material_review)
        first['subjects'][0].update(prepared_complete=False,original_complete=False,recommended_asset_id=None)
        first.update(issues=['subject_0需要重新取材，现有裁片边缘有其他旧文字污染'],
            retakes=[dict(asset_id='subject_0',source_crop=[0,0,1,1],reason='当前框不能排除旧文字，需另行处理')])
        second=copy.deepcopy(first)
        second['subjects'].pop()
        self.queue=[first,second]
        with patch.object(workflow.source_retake,'validate_retakes',
                side_effect=ValueError('subject_0.source_crop 未改变，不能重复裁取同一区域')) as retakes, \
             patch.object(workflow.source_retake,'apply_retakes') as apply:
            with self.assertRaisesRegex(ValueError,'第 1 轮格式修正.*缺少') as failed:
                self.finish()
        self.assertIn('subject_1',str(failed.exception))
        retakes.assert_called_once()
        apply.assert_not_called()
        self.assertEqual(self.model.call_count,2)
        self.assertEqual(self.calls[1]['payload']['previous_response'],
            workflow.material_review.MaterialReview.model_validate(first).model_dump())
        rules=self.calls[1]['payload']['response_contract']['retake_rules']
        self.assertFalse(rules['allow_same_pixel_crop'])
        self.assertFalse(rules['supports_mask_edit'])
        self.assertIn('不能提交原框',self.calls[1]['payload']['instruction'])
        self.assertFalse((self.folder/'code-material-decisions.json').exists())
        self.render.assert_not_called()

    def test_material_format_repair_is_bounded_to_one_retry(self):
        self.checkpoint = None
        self.queue = [{'unexpected': 'wrong'}, {'unexpected': 'still wrong'}, self.material_review]
        with self.assertRaises(workflow.ResponseContractError):
            self.finish()
        self.assertEqual(self.model.call_count, 2)
        self.assertEqual(self.queue, [self.material_review])
        self.render.assert_not_called()
        self.mux.assert_not_called()
        self.assertFalse((self.folder / 'code-material-decisions.json').exists())

    def test_complete_malformed_material_json_uses_existing_format_repair_budget(self):
        self.checkpoint = None
        answer='{"subjects": [{"asset_id": "subject_0", "evidence": "引号格式错误"}],}'
        error=ModelResponseFormatError('完整响应不是有效JSON',
            {'finish_reason':'stop','error_code':'model_json_invalid'},response_text=answer)
        self.queue=[error,self.material_review,self.preview_turn,self.pass_review]
        _,_,_,_,quality=self.finish()
        self.assertEqual(quality['model_calls'],4)
        self.assertEqual(self.calls[1]['payload']['previous_response'],answer)
        self.assertEqual(self.calls[0]['images'],self.calls[1]['images'])
        self.assertEqual([row['task']['attempt'] for row in self.calls[:2]],[0,1])
        self.assertEqual(self.calls[2]['payload']['remaining']['model_calls'],6)
        self.assertEqual(self.calls[1]['task']['stage'],'code_material_review')

    def test_repeated_malformed_material_json_stops_without_rendering_or_success_cache(self):
        self.checkpoint = None
        error=ModelResponseFormatError('完整响应不是有效JSON',response_text='{"subjects": [}')
        self.queue=[error,error,self.material_review]
        with self.assertRaises(workflow.ResponseContractError):
            self.finish()
        self.assertEqual(self.model.call_count,2)
        self.assertEqual(len(self.queue),1)
        self.render.assert_not_called()
        self.mux.assert_not_called()
        self.assertFalse((self.folder/'code-material-decisions.json').exists())

    def test_cancel_after_complete_malformed_response_prevents_format_repair(self):
        self.checkpoint = None
        self.queue=[ModelResponseFormatError('完整响应不是有效JSON',response_text='not JSON'),self.material_review]
        self.cancel_on_call=1
        with self.assertRaises(InterruptedError):
            self.finish()
        self.assertEqual(self.model.call_count,1)
        self.render.assert_not_called()

    def test_format_repair_does_not_add_implicit_retry_to_code_author(self):
        self.queue=[ModelResponseFormatError('完整响应不是有效JSON',response_text='not JSON'),self.preview_turn]
        with self.assertRaises(ModelResponseFormatError):
            self.finish()
        self.assertEqual(self.model.call_count,1)
        self.render.assert_not_called()

    def test_material_semantic_contract_error_is_repaired_once_not_treated_as_complete(self):
        self.checkpoint = None
        incomplete = copy.deepcopy(self.material_review)
        incomplete['subjects'][0]['prepared_complete'] = False
        # The recommendation still points to the explicitly incomplete prepared variant.
        corrected = copy.deepcopy(incomplete)
        corrected['subjects'][0]['recommended_asset_id'] = 'subject_0_original'
        self.preview_measurements = measured_scene()
        self.preview_measurements['assets'][0]['id'] = 'subject_0_original'
        self.preview_measurements['frames'][-1]['assets'][0]['id'] = 'subject_0_original'
        self.pass_review['subject_checks'][0]['asset_id'] = 'subject_0_original'
        self.queue = [incomplete, corrected, self.preview_turn, self.pass_review]
        _, _, _, _, quality = self.finish()
        self.assertEqual(quality['model_calls'], 4)
        self.assertIn('validation_error', self.calls[1]['payload'])
        self.assertEqual(self.calls[1]['task']['stage'], 'code_material_review')
        self.assertEqual(self.calls[1]['payload']['previous_response'], incomplete)

    def test_material_format_repair_cannot_erase_defect_to_avoid_required_retake(self):
        self.checkpoint = None
        incomplete = self.retake_review()
        incomplete.update(issues=[], retakes=[])
        self.queue = [incomplete, self.material_review]
        with self.assertRaisesRegex(ValueError, '格式修正不能'):
            self.finish()
        self.assertEqual(self.model.call_count, 2)
        self.render.assert_not_called()
        self.mux.assert_not_called()

    def test_material_transport_failure_does_not_retry_or_cache_success(self):
        self.checkpoint = None
        self.queue = [CloudCallError('material review transport interrupted'), self.material_review]
        with self.assertRaisesRegex(CloudCallError, 'transport interrupted'):
            self.finish()
        self.model.assert_called_once()
        self.render.assert_not_called()
        self.mux.assert_not_called()
        self.assertFalse((self.folder / 'code-material-decisions.json').exists())

    def test_changed_material_fingerprint_forces_fresh_review(self):
        self.checkpoint['material_decisions']['fingerprint'] = 'stale-material-hash'
        self.queue = [self.material_review, self.preview_turn, self.pass_review]
        _, _, _, _, quality = self.finish()
        self.assertEqual(self.calls[0]['task']['stage'], 'code_material_review')
        self.assertIs(quality['material_decisions']['cached'], False)
        self.assertEqual(quality['material_decisions']['fingerprint'], 'test-hash')
        self.assertEqual(quality['model_calls'], 3)

    def test_changed_material_contract_version_invalidates_cached_verdict(self):
        self.checkpoint['material_decisions']['version'] = 'old-review-version'
        self.queue = [self.material_review, self.preview_turn, self.pass_review]
        _, _, _, _, quality = self.finish()
        self.assertEqual(self.calls[0]['task']['stage'], 'code_material_review')
        self.assertIs(quality['material_decisions']['cached'], False)

    def test_matching_cache_is_still_validated_for_complete_subject_coverage(self):
        self.material_review['subjects'] = []
        with self.assertRaisesRegex(ValueError, '覆盖每个已有主体'):
            self.finish()
        self.model.assert_not_called()
        self.render.assert_not_called()
        self.mux.assert_not_called()

    def test_explicitly_incomplete_variant_cannot_be_rehabilitated_by_visual_review(self):
        self.material_review['subjects'][0].update(
            prepared_complete=False, original_complete=True, recommended_asset_id='subject_0_original')
        self.queue = [self.preview_turn] * 3 + [self.pass_review]
        with self.assertRaises(ValueError):
            self.finish()
        self.assertEqual(self.model.call_count, 3)
        self.assertTrue(all(row['task']['stage'] == 'code_author' for row in self.calls))
        self.assertEqual(self.queue, [self.pass_review], 'unchanged broken mask must not reach a second opinion')
        self.assertIn('subject_0', self.calls[1]['payload']['feedback']['validation_error'])
        self.assertEqual(self.calls[1]['payload']['material_decisions'], self.material_review)
        self.assertEqual([row['mode'] for row in self.renders], ['preview'] * 3)
        self.mux.assert_not_called()

    def test_final_render_rechecks_material_choice_even_after_passed_visual_review(self):
        self.material_review['subjects'][0].update(
            prepared_complete=False, original_complete=True, recommended_asset_id='subject_0_original')
        self.preview_measurements = measured_scene()
        self.preview_measurements['assets'][0]['id'] = 'subject_0_original'
        self.preview_measurements['frames'][-1]['assets'][0]['id'] = 'subject_0_original'
        self.pass_review['subject_checks'][0]['asset_id'] = 'subject_0_original'
        self.video_measurements = measured_scene()  # A changed final render tries the defective cutout again.
        self.queue = [self.preview_turn, self.pass_review]
        with self.assertRaisesRegex(ValueError, 'subject_0'):
            self.finish()
        self.assertEqual(self.model.call_count, 2)
        self.assertEqual([row['mode'] for row in self.renders], ['preview', 'video'])
        self.mux.assert_not_called()
        self.assertFalse((self.folder / 'quality.json').exists())

    def test_no_complete_material_stops_without_silent_generation_or_selection(self):
        self.material_review['subjects'][0].update(prepared_complete=False,
            original_complete=False, recommended_asset_id=None)
        self.material_review['issues'] = ['subject_0 两个版本均丢失外围主体，需要重新取材']
        with self.assertRaisesRegex(ValueError, '重新取材'):
            self.finish()
        self.model.assert_not_called()
        self.render.assert_not_called()
        self.mux.assert_not_called()
        self.assertTrue((self.folder / 'code-material-decisions.json').exists())

    def test_no_cache_and_insufficient_budget_stops_before_any_paid_call(self):
        self.checkpoint = None
        self.call_limit = 2
        with self.assertRaisesRegex(ValueError, '预算不足'):
            self.finish()
        self.model.assert_not_called()
        self.render.assert_not_called()

    def test_pending_preview_without_material_cache_needs_two_calls_not_one(self):
        self.checkpoint = {'turn': self.preview_turn}
        self.call_limit = 1
        with self.assertRaisesRegex(ValueError, '预算不足'):
            self.finish()
        self.model.assert_not_called()
        self.render.assert_not_called()

    def retake_review(self):
        review = copy.deepcopy(self.material_review)
        review['subjects'][0].update(source_crop_complete=False, prepared_complete=False,
            original_complete=False, recommended_asset_id=None,
            source_crop_evidence='完整原图中主体下方仍有轮廓延伸，现有裁框切断脚部，需要扩大裁区')
        review['subjects'][0]['source_edges']['bottom'].update(continues_outside=True,
            evidence='下边界两侧仍有主体连续轮廓，脚部在下方框外可见，因此原裁区已经截断主体')
        review['retakes'] = [dict(asset_id='subject_0', source_crop=[.1, .1, .9, .95],
                                 reason='原图完整主体的脚部超出现有下边界')]
        review['issues'] = ['subject_0 的源裁区截断脚部，需要重新取材']
        return review

    def mock_retake(self):
        new_inventory = source_inventory()
        new_inventory.subjects[0].crop = [.1, .1, .9, .95]
        new_assets = {**self.assets, 'subject_0': self.root / 'retaken.png',
                      'subject_0_original': self.root / 'retaken-original.png'}
        validate = self.start_patch('backend.app.vibe_code_workflow.source_retake.validate_retakes')
        apply = self.start_patch('backend.app.vibe_code_workflow.source_retake.apply_retakes',
            return_value=(new_inventory, new_assets, [{'asset_id': 'subject_0', 'source_crop': [.1, .1, .9, .95]}]))
        return new_inventory, new_assets, validate, apply

    def test_ds_retake_rebuilds_materials_and_forces_fresh_review_before_code(self):
        self.checkpoint = None
        new_inventory, new_assets, validate, apply = self.mock_retake()
        self.material_fingerprint.side_effect = ['old-hash', 'new-hash']
        self.queue = [self.retake_review(), self.material_review, self.preview_turn, self.pass_review]
        _, assets, _, _, quality = self.finish()
        self.assertEqual(assets, new_assets)
        self.assertEqual(quality['material_retakes'], 1)
        self.assertEqual(quality['model_calls'], 4)
        self.assertEqual(quality['material_decisions']['fingerprint'], 'new-hash')
        self.assertEqual([row['task']['stage'] for row in self.calls],
                         ['code_material_review', 'code_material_review', 'code_author', 'code_review'])
        self.assertEqual(self.calls[1]['payload']['retakes_remaining'], 0)
        self.assertNotIn('previous_material_checks', self.calls[1]['payload'])
        self.assertEqual(self.calls[1]['payload']['retake_operations'][0]['asset_id'], 'subject_0')
        self.assertEqual(self.calls[1]['task']['source_retake_round'], 1)
        self.assertEqual(self.material_inspection.call_count, 2)
        self.assertIs(self.material_inspection.call_args.args[0], new_inventory)
        self.assertEqual(self.material_inspection.call_args.args[2], new_assets)
        self.assertEqual(self.calls[2]['payload']['material_specs']['subject_0']['file_label'], 'retaken.png')
        self.assertTrue(all(row['assets'] == new_assets for row in self.renders))
        validate.assert_called_once()
        apply.assert_called_once()
        self.assertEqual(list(apply.call_args.args[0][0].source_crop), [.1, .1, .9, .95])
        history = json.loads((self.folder / 'code-material-history.json').read_text(encoding='utf-8'))
        self.assertEqual([row['fingerprint'] for row in history], ['old-hash', 'new-hash'])
        self.assertEqual(self.inventory.subjects[0].crop, [0, 0, 1, 1], 'source inventory is not mutated')

    def test_retake_invalidates_checkpoint_jsx_and_font_measurements(self):
        self.checkpoint = {'turn': {**self.preview_turn, 'code': 'old-crop-code'},
                           'measurements': {'old': {'width': 999}}}
        self.mock_retake()
        self.queue = [self.retake_review(), self.material_review, self.preview_turn, self.pass_review]
        self.finish()
        self.assertEqual(self.calls[2]['payload']['measurements'], {})
        self.assertIsNone(self.calls[2]['payload']['previous_code'])
        self.assertTrue(all(row['code'] != 'old-crop-code' for row in self.renders))

    def test_pending_retake_cache_is_not_reused(self):
        self.checkpoint['material_decisions']['review'] = self.retake_review()
        self.queue = [self.material_review, self.preview_turn, self.pass_review]
        _, _, _, _, quality = self.finish()
        self.assertEqual(self.calls[0]['task']['stage'], 'code_material_review')
        self.assertIs(quality['material_decisions']['cached'], False)

    def test_stale_material_cache_discards_bound_checkpoint_code_even_without_new_retake(self):
        self.checkpoint['material_decisions']['fingerprint'] = 'stale'
        self.checkpoint.update(turn={**self.preview_turn, 'code': 'old-crop-code'},
                               measurements={'old': {'width': 999}})
        self.queue = [self.material_review, self.preview_turn, self.pass_review]
        self.finish()
        self.assertEqual(self.calls[1]['task']['stage'], 'code_author')
        self.assertEqual(self.calls[1]['payload']['measurements'], {})
        self.assertTrue(all(row['code'] != 'old-crop-code' for row in self.renders))

    def test_second_retake_request_stops_without_second_local_operation(self):
        self.checkpoint = None
        _, _, _, apply = self.mock_retake()
        second = self.retake_review()
        second['retakes'][0]['source_crop'] = [.05, .05, .95, 1]
        self.queue = [self.retake_review(), second]
        with self.assertRaisesRegex(ValueError, '达到1轮取材上限'):
            self.finish()
        apply.assert_called_once()
        self.assertEqual(self.model.call_count, 2)
        self.render.assert_not_called()

    def test_insufficient_post_retake_budget_does_not_recrop_or_run_rmbg(self):
        self.checkpoint = None
        self.call_limit = 3
        _, _, _, apply = self.mock_retake()
        self.queue = [self.retake_review()]
        with self.assertRaisesRegex(ValueError, '预算不足以重新取材'):
            self.finish()
        apply.assert_not_called()
        self.render.assert_not_called()

    def test_retake_cancelled_after_local_operation_never_rechecks_or_authors(self):
        self.checkpoint = None
        _, _, _, apply = self.mock_retake()
        result = apply.return_value
        def cancelled_retake(*args, **kwargs):
            self.event.set()
            return result
        apply.side_effect = cancelled_retake
        self.queue = [self.retake_review()]
        with self.assertRaises(InterruptedError):
            self.finish()
        self.assertEqual(self.model.call_count, 1)
        self.render.assert_not_called()

    def test_bound_reference_full_image_is_sent_with_explicit_coordinate_mapping(self):
        self.checkpoint = None
        self.sources['ref_2'] = self.root / 'bound-reference.png'
        self.source_manifest.return_value = [dict(asset_id='subject_0', source_id='ref_2',
            source_image_id='retake_source_ref_2', current_source_crop=[.1, .2, .8, .9],
            treatment='cutout', source_size=[800, 1200])]
        self.queue = [self.material_review, self.preview_turn, self.pass_review]
        self.finish()
        self.assertEqual(self.calls[0]['images']['retake_source_ref_2'], self.sources['ref_2'])
        self.assertEqual(self.calls[0]['payload']['source_manifest'], self.source_manifest.return_value)

    def test_edge_closeups_are_only_sent_to_material_reviewer_not_author_or_layout_review(self):
        self.checkpoint = None
        closeup = self.root / 'edges.png'
        self.material_evidence['review_images'] = {'material_edges_00': closeup}
        self.queue = [self.material_review, self.preview_turn, self.pass_review]
        self.finish()
        self.assertEqual(self.calls[0]['images']['material_edges_00'], closeup)
        self.assertNotIn('material_edges_00', self.calls[1]['images'])
        self.assertNotIn('material_edges_00', self.calls[2]['images'])


class CodeVisualFeedbackTests(unittest.TestCase):
    def test_scale_outlier_cues_are_content_agnostic_and_do_not_rewrite_layout(self):
        report = {'items': [
            dict(kind='text', id='city_name', text='不同项目的任意文字', image_id='detail_a',
                 matched_render_ids=['city_title'],
                 scale_hint={'height_ratio': 1.85, 'width_ratio': 1.05,
                             'source_height': 53, 'actual_height': 98.05, 'soft_hint_only': True}),
            dict(kind='text', id='sample_count', text='137 位参与者', image_id='detail_b',
                 matched_render_ids=['count', 'unit'],
                 scale_hint={'height_ratio': .6, 'width_ratio': .8,
                             'source_height': 110, 'actual_height': 66, 'soft_hint_only': True}),
            dict(kind='text', id='faithful', text='保持原样',
                 scale_hint={'height_ratio': 1.0, 'width_ratio': 1.0}),
            dict(kind='subject', id='subject_7',
                 scale_hint={'height_ratio': 3.0, 'width_ratio': 3.0}),
            dict(kind='text', id='no_measurement', text='没有测量时不猜') ]}
        before = copy.deepcopy(report)
        cues = workflow.fidelity_focus(report)
        self.assertEqual(report, before)
        self.assertEqual([row['id'] for row in cues], ['city_name', 'sample_count'])
        for cue, original in zip(cues, report['items']):
            self.assertEqual(cue['text'], original['text'])
            self.assertEqual(cue['matched_render_ids'], original['matched_render_ids'])
            self.assertEqual(cue['scale_hint'], original['scale_hint'])
            self.assertIn('not an automatic failure', cue['instruction'])
            self.assertNotIn('font_size', cue)
            self.assertNotIn('passed', cue)
            self.assertNotIn('replacement_text', cue)

    def test_review_dom_summary_keeps_fragments_style_and_timing_without_repeated_dom(self):
        measured = measured_scene()
        measured['texts'][0].update(box=[80, 90, 300, 60], color='#66ccff',
            line_height='60px', font_weight='700', fragments=[
                dict(text='49', font_size=48, color='#66ccff', visible=True,
                     rects=[[80, 90, 90, 60]]),
                dict(text='对患者', font_size=24, color='#ffffff', visible=True,
                     rects=[[170, 115, 100, 30]])])
        measured['groups'] = [dict(id='title_group', at=.5, enter=.7, exit=.3,
                                  motion='slide_left', box=[0, 0, 640, 360])]
        measured['frames'][0]['texts'] = [dict(measured['texts'][0], visible=False)]
        measured['frames'][0]['assets'] = [dict(measured['assets'][0], visible=False)]
        measured['frames'][-1]['diagnostics'] = ['offline diagnostic']
        before = copy.deepcopy(measured)
        compact = workflow.review_dom_summary(measured)
        self.assertEqual(measured, before)
        self.assertEqual(compact['texts'], measured['texts'])
        self.assertEqual(compact['assets'], measured['assets'])
        self.assertEqual(compact['groups'], [dict(id='title_group', at=.5, enter=.7,
                                                exit=.3, motion='slide_left')])
        self.assertEqual(compact['frames'][0], dict(id='early', time=.4,
            visible_text_ids=[], visible_asset_ids=[], diagnostics=[]))
        self.assertEqual(compact['frames'][-1], dict(id='hold', time=10,
            visible_text_ids=['result'], visible_asset_ids=['subject_0'],
            diagnostics=['offline diagnostic']))
        self.assertNotIn('texts', compact['frames'][-1])
        self.assertNotIn('assets', compact['frames'][-1])

    def test_inspection_prompt_compaction_only_removes_duplicate_intersection_dom(self):
        report = dict(version='inspection-v1', canvas=[1280, 720], coordinate_contract='same source window',
            items=[dict(id='label_x', kind='text', text='不同项目文字',
                source_box=[.1, .2, .4, .3], matched_render_ids=['headline'],
                image_id='comparison_x', scale_hint={'height_ratio': 1.45, 'width_ratio': .9},
                intersecting_texts=[dict(id='headline', text='不同项目文字', fragments=[{'text': '不同项目文字'}])])])
        before = copy.deepcopy(report)
        compact = workflow.inspection_prompt_report(report)
        self.assertEqual(report, before)
        expected = copy.deepcopy(report)
        del expected['items'][0]['intersecting_texts']
        self.assertEqual(compact, expected)


class CodeReviewCoverageTests(unittest.TestCase):
    def setUp(self):
        self.review = dict(passed=True, summary='已逐项对照实际渲染', issues=[],
            embedded_text_verified=True, recovered_text_verified=True,
            subject_checks=[dict(asset_id='subject_0', complete=True,
                                 evidence='原裁片和透明预览的外围主体全部保留')],
            layout_checks=[dict(text_id='result', faithful=True,
                                evidence='数字和单位的字号比例与基线接近原图')])

    def validate(self, body, measured=None):
        review = workflow.CodeReview.model_validate(body)
        workflow.validate_review_coverage(review, measured if measured is not None else measured_scene())
        return review

    def test_exact_actual_dom_coverage_passes_without_background_review(self):
        measured = measured_scene()
        measured['assets'].append(dict(id='background', visible=True))
        self.assertTrue(self.validate(self.review, measured).passed)

    def test_missing_duplicate_and_fabricated_checks_are_rejected(self):
        for field, key in (('subject_checks', 'asset_id'), ('layout_checks', 'text_id')):
            for mode in ('missing', 'duplicate', 'fabricated', 'extra'):
                with self.subTest(field=field, mode=mode):
                    body = copy.deepcopy(self.review)
                    if mode == 'missing': body[field] = []
                    elif mode == 'duplicate': body[field] *= 2
                    elif mode == 'fabricated': body[field][0][key] = 'invented_item'
                    else: body[field].append(dict(body[field][0], **{key: 'invented_item'}))
                    with self.assertRaisesRegex(workflow.ResponseContractError, field):
                        self.validate(body)

    def test_actual_original_variant_must_be_checked_by_its_exact_id(self):
        measured = measured_scene()
        measured['assets'][0]['id'] = 'subject_0_original'
        with self.assertRaises(workflow.ResponseContractError):
            self.validate(self.review, measured)
        self.review['subject_checks'][0]['asset_id'] = 'subject_0_original'
        self.assertTrue(self.validate(self.review, measured).passed)

    def test_false_item_cannot_be_overridden_by_top_level_pass(self):
        for field, verdict in (('subject_checks', 'complete'), ('layout_checks', 'faithful')):
            with self.subTest(field=field):
                body = copy.deepcopy(self.review)
                body[field][0][verdict] = False
                with self.assertRaisesRegex(ValidationError, '不能判定通过'):
                    self.validate(body)
                body['passed'] = False
                body['issues'] = ['局部对照发现缺失或不一致，需返工']
                self.assertFalse(self.validate(body).passed)

    def test_each_item_requires_strict_bool_and_concrete_evidence(self):
        for field, verdict in (('subject_checks', 'complete'), ('layout_checks', 'faithful')):
            for invalid in ('true', 1, None):
                with self.subTest(field=field, invalid=invalid):
                    body = copy.deepcopy(self.review)
                    body[field][0][verdict] = invalid
                    with self.assertRaises(ValidationError):
                        self.validate(body)
            body = copy.deepcopy(self.review)
            body[field][0]['evidence'] = '已检查'
            with self.assertRaises(ValidationError):
                self.validate(body)

    def test_failed_review_still_must_cover_all_actual_items(self):
        self.review.update(passed=False, issues=['发现一处主体缺失'])
        self.review['subject_checks'] = []
        with self.assertRaises(workflow.ResponseContractError):
            self.validate(self.review)

    def test_empty_scene_needs_no_invented_subject_or_text_checks(self):
        self.review.update(subject_checks=[], layout_checks=[])
        self.assertTrue(self.validate(self.review,
            dict(assets=[dict(id='background')], texts=[])).passed)


class CodeRenderedGuardTests(unittest.TestCase):
    def setUp(self):
        self.turn = workflow.Turn(action='preview', summary='离线守恒校验', code='function Scene(ctx) { return <div />; }')
        self.inventory = source_inventory()

    def test_valid_actual_dom_can_pass_with_early_stage_still_hidden(self):
        self.assertTrue(workflow.validate_rendered(self.turn, measured_scene(), self.inventory))

    def test_hidden_or_tiny_text_missing_subject_and_wrong_number_reject(self):
        for mode in ('hidden', 'tiny', 'missing_subject', 'wrong_number', 'duplicate_subject'):
            with self.subTest(mode=mode):
                measured = measured_scene()
                if mode == 'hidden': measured['texts'][0]['visible'] = False
                if mode == 'tiny': measured['texts'][0]['font_size'] = 3
                if mode == 'missing_subject': measured['assets'] = []
                if mode == 'wrong_number': measured['texts'][0]['text'] = '94对患者'
                if mode == 'duplicate_subject': measured['assets'].append(dict(id='subject_0_original', visible=True))
                with self.assertRaises(ValueError):
                    workflow.validate_rendered(self.turn, measured, self.inventory)

    def test_changed_timeline_text_or_missing_hold_cannot_pass_static_poster(self):
        for mode in ('changed_text', 'missing_text', 'missing_subject', 'missing_hold', 'overflow'):
            with self.subTest(mode=mode):
                measured = measured_scene()
                hold = measured['frames'][-1]
                if mode == 'changed_text': hold['texts'][0]['text'] = '94对患者'
                if mode == 'missing_text': hold['texts'] = []
                if mode == 'missing_subject': hold['assets'] = []
                if mode == 'missing_hold': measured['frames'] = measured['frames'][:1]
                if mode == 'overflow': hold['diagnostics'] = ['result overflow']
                with self.assertRaises(ValueError):
                    workflow.validate_rendered(self.turn, measured, self.inventory)

    def test_declared_raster_text_requires_actually_rendered_bound_asset(self):
        self.turn.embedded_text = {'subject_99': ['49对患者']}
        with self.assertRaisesRegex(ValueError, '未实际渲染'):
            workflow.validate_rendered(self.turn, measured_scene(), self.inventory)


class CodeCallRoundLabelTests(unittest.TestCase):
    def test_format_error_retains_typed_response_and_private_diagnostic(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'task.json'
            api._write(path,{'stage':'code_material_review','attempt':1,
                'source_retake_round':0,'usage':{'model_calls':[]}})
            answer='{"evidence":"private image observation",}'
            error=ModelResponseFormatError('模型已结束返回，但JSON格式无效',
                {'finish_reason':'stop','error_code':'model_json_invalid'},response_text=answer)
            with patch.object(api,'chat_json',side_effect=error) as model:
                with self.assertRaises(ModelResponseFormatError) as caught:
                    api._call(path,{'model':'offline','api_key':'not-in-diagnostic'},
                              'private system',{}, {},lambda:False)
            model.assert_called_once()
            self.assertEqual(caught.exception.response_text,answer)
            self.assertIn('首次核查 · 第 1 轮格式修正',str(caught.exception))
            self.assertNotIn('private image observation',str(caught.exception))
            usage=json.loads(path.read_text(encoding='utf-8'))['usage']['model_calls']
            self.assertEqual(len(usage),1)
            self.assertEqual(usage[0]['status'],'invalid_response')
            self.assertTrue(usage[0]['may_charge'])
            self.assertEqual(usage[0]['format_repair_round'],1)
            self.assertNotIn('private image observation',json.dumps(usage))
            diagnostic=json.loads((path.parent/usage[0]['response_diagnostic']).read_text(encoding='utf-8'))
            self.assertEqual(diagnostic['response_text'],answer)
            self.assertNotIn('not-in-diagnostic',json.dumps(diagnostic))
            self.assertNotIn('private system',json.dumps(diagnostic))

    def test_material_recheck_logs_retake_round_separately_from_format_repair(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'task.json'
            for attempt in (0, 1):
                api._write(path, {'stage': 'code_material_review', 'attempt': attempt,
                    'source_retake_round': 1, 'usage': {'model_calls': []}})
                with patch.object(api, 'chat_json', side_effect=CloudCallError('timeout')):
                    with self.assertRaises(CloudCallError) as failure:
                        api._call(path, {'model': 'offline'}, 'system', {}, {}, lambda: False)
                self.assertIn('第 1 轮重新取材后核查', str(failure.exception))
                if attempt:
                    self.assertIn('第 1 轮格式修正', str(failure.exception))
                usage = json.loads(path.read_text(encoding='utf-8'))['usage']['model_calls'][0]
                self.assertEqual(usage['source_retake_round'], 1)
                self.assertEqual(usage['format_repair_round'], attempt)

    def test_transport_error_and_usage_identify_actual_visual_repair_round(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / 'task.json'
        for stage, name in (('code_author', '代码编写'), ('code_review', '代码视觉验收')):
            for attempt in range(3):
                with self.subTest(stage=stage, attempt=attempt):
                    api._write(path, {'stage': stage, 'attempt': attempt, 'usage': {'model_calls': []}})
                    with patch.object(api, 'chat_json', side_effect=CloudCallError('timeout')) as model:
                        with self.assertRaises(CloudCallError) as failed:
                            api._call(path, {'model': 'offline'}, 'system', {}, {}, lambda: False)
                    model.assert_called_once()
                    label = '初次设计' if attempt == 0 else f'第 {attempt} 轮视觉返工'
                    self.assertIn(name, str(failed.exception))
                    self.assertIn(label, str(failed.exception))
                    usage = json.loads(path.read_text(encoding='utf-8'))['usage']['model_calls']
                    self.assertEqual(len(usage), 1)
                    self.assertEqual(usage[0]['visual_repair_round'], attempt)
                    self.assertEqual(usage[0]['status'], 'unconfirmed')


if __name__ == '__main__':
    unittest.main()
