"""Offline end-to-end tool loop contracts, with real material workspaces.

No model/image network calls or renderer processes are started. The model stub
does charge every attempted call to usage so budget assertions exercise the
same session boundary as production (including failed upstream calls).
"""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image
from pydantic import ValidationError

from backend.app import vibe_agent_workflow as workflow
from backend.app.vibe_agent_contract import VERSION, TOOL_RESPONSE_GUIDE
from backend.app.vibe_materials import MaterialError


CODE = '''function Scene(ctx) { return <Group id="intro" at={0} enter={0.6} motion="fade"><Asset id="subject_0" anchor="source" /><Text id="title" style={{fontSize:32}}>研究结果</Text></Group>; }'''


def command(tool, **arguments):
    return dict(tool=tool, summary='自主选择工具：' + tool, arguments=arguments)


def content_inventory():
    return dict(background='solid', background_color='#123456', background_is_uniform=True,
                foreground_summary=dict(has_nontext_subjects=True, subject_count=1,
                                        description='左侧示意主体与标题'),
                subjects=[dict(name='示意主体', source='core', source_crop=[.1, .2, .4, .8],
                               treatment='preserve')],
                texts=[dict(text='研究结果', source_box=[.2, .02, .8, .15])])


class DeliveryReviewTests(unittest.TestCase):
    def passed(self, **changes):
        return dict(passed=True, summary='检查通过', issues=[], **changes)

    def test_valid_reviews_accept_exact_top_level_json_mode_marker_without_mutation(self):
        for passed, issues in ((True, []), (False, ['poster 左上角标题被裁切'])):
            with self.subTest(passed=passed):
                raw = dict(type='json_object', passed=passed, summary='已核查当前预览', issues=issues)
                original = copy.deepcopy(raw)
                review = workflow.DeliveryReview.model_validate(raw)
                self.assertEqual(review.model_dump(), {key: value for key, value in original.items()
                                                      if key != 'type'})
                self.assertEqual(raw, original)
                self.assertIs(raw['issues'], issues)
                self.assertEqual(raw['type'], 'json_object')

    def test_unmarked_reviews_remain_valid(self):
        for raw in (self.passed(), dict(passed=False, summary='需要返工', issues=['标题越界'])):
            with self.subTest(passed=raw['passed']):
                self.assertEqual(workflow.DeliveryReview.model_validate(raw).model_dump(), raw)

    def test_other_top_level_type_values_remain_forbidden(self):
        for marker in ('object', 'JSON_OBJECT', None, True, {'type': 'json_object'}):
            with self.subTest(marker=marker):
                raw = self.passed(type=marker)
                original = copy.deepcopy(raw)
                with self.assertRaises(ValidationError) as caught:
                    workflow.DeliveryReview.model_validate(raw)
                self.assertIn(('type',), [error['loc'] for error in caught.exception.errors()])
                self.assertEqual(raw, original)

    def test_other_extra_fields_remain_forbidden_with_or_without_marker(self):
        for marker in ({}, {'type': 'json_object'}):
            for extra in ({'unexpected': 'value'}, {'response_format': {'type': 'json_object'}}):
                with self.subTest(marker=marker, extra=extra):
                    raw = {**self.passed(), **marker, **extra}
                    original = copy.deepcopy(raw)
                    with self.assertRaises(ValidationError) as caught:
                        workflow.DeliveryReview.model_validate(raw)
                    self.assertIn((next(iter(extra)),),
                                  [error['loc'] for error in caught.exception.errors()])
                    self.assertEqual(raw, original)

    def test_nested_marker_data_is_not_unwrapped_or_normalized(self):
        for changes in ({'summary': {'type': 'json_object', 'summary': '检查通过'}},
                        {'passed': False, 'issues': [{'type': 'json_object', 'issue': '标题越界'}]}):
            with self.subTest(changes=changes):
                raw = {**self.passed(type='json_object'), **changes}
                original = copy.deepcopy(raw)
                with self.assertRaises(ValidationError):
                    workflow.DeliveryReview.model_validate(raw)
                self.assertEqual(raw, original)

    def test_json_mode_marker_does_not_relax_strict_passed_bool(self):
        for value in ('true', 'false', 1, 0, None):
            with self.subTest(value=value):
                raw = {**self.passed(type='json_object'), 'passed': value}
                with self.assertRaises(ValidationError) as caught:
                    workflow.DeliveryReview.model_validate(raw)
                self.assertIn('bool_type', [error['type'] for error in caught.exception.errors()])

    def test_json_mode_marker_does_not_relax_review_consistency(self):
        for passed, issues in ((True, ['标题越界']), (False, [])):
            with self.subTest(passed=passed):
                raw = dict(type='json_object', passed=passed, summary='检查结论', issues=issues)
                with self.assertRaisesRegex(ValidationError, '验收结论与问题清单不一致'):
                    workflow.DeliveryReview.model_validate(raw)

    def test_json_mode_marker_does_not_relax_text_or_issue_constraints(self):
        for changes in ({'summary': ''}, {'summary': '长' * 1201},
                        {'passed': False, 'issues': ['  ']},
                        {'passed': False, 'issues': ['长' * 1601]},
                        {'passed': False, 'issues': ['标题越界'] * 21}):
            with self.subTest(changes=changes):
                raw = {**self.passed(type='json_object'), **changes}
                with self.assertRaises(ValidationError):
                    workflow.DeliveryReview.model_validate(raw)

    def test_schema_still_advertises_only_strict_review_fields(self):
        schema = workflow.DeliveryReview.model_json_schema()
        self.assertEqual(set(schema['properties']), {'passed', 'summary', 'issues'})
        self.assertIs(schema['additionalProperties'], False)

    def test_review_requires_same_image_evidence_not_ordinal_or_optional_style(self):
        for text in ('按image_id/source_sha256及候选完整ID区分',
                     '原图core与独立底图background是不同图像',
                     '两条同时可见的线及位置', '可选美化写在summary',
                     '真实缺字、改数、遮挡、残留仍须拦截'):
            self.assertIn(text,workflow.REVIEW_GUIDE)
        description=workflow.DeliveryReview.model_json_schema()['properties']['issues']['description']
        self.assertIn('阻止交付的实际问题',description)


class AgentWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        self.folder = self.project / 'vibe' / 'job'
        self.folder.mkdir(parents=True)
        self.path = self.folder / 'task.json'
        self.core = self.project / 'core.png'
        Image.new('RGB', (320, 180), '#123456').save(self.core)
        self.task = dict(workflow_version=VERSION, template_mode='layered', max_model_calls=30,
                         max_image_requests=2, usage=dict(model_calls=[], image_requests=0, image_task_ids=[]),
                         image_route=dict(key='project-personal', label='本项目个人API'))
        self.event = threading.Event()
        self.calls = []
        self.responses = []
        self.reviews = []
        self.renders = []
        self.status_updates = []
        self.model = self.mock('backend.app.vibe_agent_workflow.api._call', side_effect=self.model_call)
        self.mock('backend.app.vibe_agent_workflow.api._job', return_value=self.path)
        self.mock('backend.app.vibe_agent_workflow.api._read_job', side_effect=lambda *_: self.task)
        self.mock('backend.app.vibe_agent_workflow.api._update', side_effect=self.update_task)
        self.render_call = self.mock('backend.app.vibe_agent_workflow.renderer.run', side_effect=self.render)
        self.mock('backend.app.vibe_agent_workflow.inspection.build_preview_inspection',
                  return_value=dict(images={}, report={'inspection': 'offline'}))
        self.mock('backend.app.vibe_agent_workflow.inspection.compact_preview_evidence',
                  side_effect=lambda evidence, **_: evidence)
        self.validate_call = self.mock('backend.app.vibe_agent_workflow.validate_rendered')
        self.ocr_call = self.mock('backend.app.vibe_agent_workflow.local_ocr.analyze_image',
            return_value=dict(status='unavailable', reason='offline unit-test fixture', texts=[]))
        self.image_call = self.mock('backend.app.vibe_agent_workflow.api.generate_asset',
                                    side_effect=AssertionError('Unexpected image-provider request'))
        self.rmbg_call = self.mock('backend.app.vibe_agent_materials.rmbg.run_cutouts',
                                   side_effect=AssertionError('Unexpected RMBG invocation'))
        self.session = workflow.AgentSession(self.project, 'shot_generic', 'a' * 32, {},
            SimpleNamespace(max_model_calls=30, max_image_requests=2, instruction='忠实而精致的动效', pace='standard'),
            self.event, dict(canvas=[640, 360], duration=8, subtitles=[]), {'core': self.core})

    def mock(self, name, **kwargs):
        patcher = patch(name, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def update_task(self, path, **values):
        self.status_updates.append(copy.deepcopy(values))
        self.task.update(values)
        return self.task

    def model_call(self, path, config, system, payload, images, cancelled, **kwargs):
        self.task['usage']['model_calls'].append({'attempt': len(self.calls) + 1})
        self.calls.append(dict(system=system, payload=copy.deepcopy(payload), images=dict(images), options=kwargs))
        responses = self.reviews if system == workflow.REVIEW_GUIDE else self.responses
        if not responses:
            self.fail('Unexpected extra model call')
        response = responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response() if callable(response) else copy.deepcopy(response)

    def render(self, **kwargs):
        self.renders.append(kwargs)
        if kwargs['mode'] == 'measure':
            return dict(measurements=dict(measured=[dict(id=probe['id'], width=120, height=38)
                                                   for probe in kwargs['measure_requests']]))
        Image.new('RGB', (640, 360), '#123456').save(kwargs['output_dir'] / 'poster.png')
        Image.new('RGB', (640, 180), '#234567').save(kwargs['output_dir'] / 'proof.jpg')
        return dict(measurements=dict(texts=[], assets=[], groups=[], diagnostics=[]))

    def setup_commands(self):
        return [command('set_inventory', inventory=content_inventory()),
                command('select_background', color='#123456'),
                command('prepare_subject', subject_id='subject_0')]

    def finish_latest(self):
        return command('finish', preview_id=next(reversed(self.session.state['previews'])))

    def preview(self, suffix=''):
        # Existing review/freshness fixtures exercise an explicitly requested
        # full preview. Default-layout behavior is covered separately below.
        return command('preview_scene', code=CODE + suffix, preview_level='full')

    def passed(self):
        return dict(passed=True, summary='原文、主体、边缘和动态截图均符合任务', issues=[])

    def ocr_result(self, texts=None, **changes):
        return dict(status='ready', backend='RapidOCR/ONNXRuntime-CPU', version='test',
            source_sha256=hashlib.sha256(self.core.read_bytes()).hexdigest(), source_size=[320,180],
            coordinate_space='source_pixels', texts=texts or [], **changes)

    def test_source_localization_runs_before_model_and_reuses_pixels_without_paid_work(self):
        self.ocr_call.return_value = self.ocr_result([
            dict(id='ocr_0',text='研究结果',box=[100,20,200,40],confidence=.98)])
        a = self.session.payload()['source_measurements']
        b = self.session.payload()['source_measurements']
        self.assertEqual(a,b)
        self.assertEqual(a['ocr_status'],'ready')
        self.assertEqual(a['canvas'],[640,360])
        self.assertEqual(self.ocr_call.call_count,1)
        self.assertFalse(self.calls)
        self.assertFalse(self.renders)
        self.image_call.assert_not_called()

    def test_locator_unavailable_is_not_an_empty_verified_source_and_can_be_retried(self):
        first = self.session.local_measurements()
        self.assertEqual(first['ocr_status'],'unavailable')
        self.ocr_call.return_value = self.ocr_result([
            dict(id='ocr_0',text='研究结果',box=[100,20,200,40],confidence=.98)])
        from backend.app.vibe_agent_contract import LocateElementsArgs
        result = self.session.execute('locate_elements',LocateElementsArgs(text='研究结果'),'组件可用后复核')
        self.assertTrue(result['receipt']['ok'])
        self.assertEqual(result['receipt']['measurements']['text_query']['status'],'located')
        self.assertEqual(self.session.local_measurements()['ocr_status'],'ready')

    def test_partial_localization_persists_and_is_used_by_following_full_source_query(self):
        self.ocr_call.side_effect = [self.ocr_result(), self.ocr_result([
            dict(id='ocr_region',text='研究结果',box=[110,125,210,150],confidence=.99)])]
        self.session.local_measurements()
        partial = self.session.local_measurements('core',[.2,.6,.8,.9],text='研究结果')
        self.assertEqual(partial['text_query']['status'],'located')
        full = self.session.local_measurements(text='研究结果')
        self.assertEqual(full['text_query']['status'],'located')
        for actual, expected in zip(full['text_query']['tool_box'],[110/320,125/180,210/320,150/180]):
            self.assertAlmostEqual(actual, expected, places=9)
        self.assertEqual(len(self.session.state['source_measurement_supplements']),1)
        self.assertEqual(self.ocr_call.call_count,2)
        self.session.source_measurement_overrides.clear()
        self.assertEqual(self.session.local_measurements(text='研究结果')['text_query']['status'],'located')

    def recovery_error(self):
        from backend.app.vibe_layered_assembly import RecoveredTextConflict
        return RecoveredTextConflict('两个申报的原文位置重叠', conflict=dict(
            kind='source_occurrence_overlap',
            submitted=dict(path='recovered_text[1]',text='0',source_box=[.625,.68,.645,.72]),
            existing=dict(path='recovered_text[0]',text='20',source_box=[.625,.68,.65,.72]),
            coordinate_space='core_normalized_ltrb'))

    def test_recovery_conflict_rechecks_local_pixels_without_rewriting_or_paid_calls(self):
        self.ocr_call.side_effect = [self.ocr_result([
            dict(id='left_zero',text='0',box=[40,122,45,129],confidence=.99),
            dict(id='right_twenty',text='20',box=[200,122,208,130],confidence=.99)]),
            self.ocr_result([
                dict(id='right_zero',text='0',box=[187,123,191,129],confidence=.99)])]
        geometry = dict(source_measurements=self.session.local_measurements('core'))
        error = self.recovery_error()
        original = copy.deepcopy(error.conflict)
        feedback = self.session.recovery_feedback(error,geometry)
        self.assertEqual(error.conflict,original)
        self.assertTrue(feedback['local_recheck']['attempted'])
        self.assertEqual(feedback['local_recheck']['status'],'ready')
        self.assertTrue(feedback['local_recheck']['region_is_search_window_not_verified_box'])
        region = self.ocr_call.call_args.kwargs['region']
        self.assertLess(region[0],187/320)
        self.assertGreater(region[2],208/320)
        zero,twenty = [row['localization'] for row in feedback['exact_text_candidates']]
        self.assertEqual(zero['status'],'ambiguous')
        self.assertEqual(len(zero['candidates']),2)
        self.assertTrue(any(row['box'][0] == 187/320 for row in zero['candidates']))
        self.assertEqual(twenty['status'],'located')
        self.assertEqual(twenty['tool_box'][0],200/320)
        self.assertEqual(feedback['source']['source_size'],[320,180])
        for key in ('declarations_changed','code_changed','auto_accepted'):
            self.assertFalse(feedback[key])
        # Repeating the same faulty coordinates reuses the local measurement.
        again = self.session.recovery_feedback(error,geometry)
        self.assertEqual(again['exact_text_candidates'],feedback['exact_text_candidates'])
        self.assertEqual(self.ocr_call.call_count,2)
        self.assertEqual(len(self.session.state['source_measurement_supplements']),1)
        self.model.assert_not_called()
        self.image_call.assert_not_called()
        self.rmbg_call.assert_not_called()
        self.render_call.assert_not_called()

    def test_recovery_conflict_missing_ocr_stays_uncertain(self):
        feedback = self.session.recovery_feedback(self.recovery_error())
        self.assertFalse(feedback['local_recheck']['attempted'])
        self.assertEqual(feedback['local_recheck']['status'],'unavailable')
        self.assertTrue(all(row['localization']['status']=='unresolved'
                            for row in feedback['exact_text_candidates']))
        self.assertFalse(feedback['auto_accepted'])
        self.assertEqual(self.ocr_call.call_count,1)
        self.model.assert_not_called()

    def test_recovery_conflict_optional_local_failure_does_not_erase_validation(self):
        self.ocr_call.side_effect = [self.ocr_result(), OSError('local worker unavailable')]
        feedback = self.session.recovery_feedback(self.recovery_error())
        self.assertEqual(feedback['local_recheck']['status'],'unavailable')
        self.assertIn('local worker unavailable',feedback['local_recheck']['error'])
        self.assertFalse(feedback['auto_accepted'])
        self.assertFalse(feedback['code_changed'])
        self.model.assert_not_called()

    def test_untyped_model_blocker_does_not_trigger_local_recheck(self):
        self.assertIsNone(self.session.recovery_feedback(ValueError('source_occurrence_overlap')))
        self.ocr_call.assert_not_called()

    def test_preview_retains_conflict_evidence_but_cannot_finish_invalid_result(self):
        self.validate_call.side_effect = self.recovery_error()
        candidate = self.saved_candidate()
        self.assertFalse(candidate['locally_valid'])
        self.assertEqual(candidate['turn']['code'],CODE)
        feedback = candidate['validation_feedback']
        self.assertEqual(feedback['conflict']['submitted']['text'],'0')
        receipt = self.session.state['events'][-1]['receipt']
        self.assertFalse(receipt['ok'])
        self.assertEqual(receipt['validation_feedback'],feedback)
        self.assertEqual(self.session.payload()['current_preview']['validation_feedback'],feedback)
        with self.assertRaisesRegex(ValueError,'不能交付'):
            self.session.finish(candidate['preview_id'])
        self.assertEqual(len(self.renders),1)
        self.model.assert_not_called()
        self.image_call.assert_not_called()

    def test_locator_cannot_read_unknown_or_changed_bound_source(self):
        with self.assertRaises(MaterialError):
            self.session.local_measurements('unbound')
        self.ocr_call.assert_not_called()
        Image.new('RGB',(320,180),'red').save(self.core)
        with self.assertRaisesRegex(MaterialError,'改变'):
            self.session.local_measurements('core')
        self.ocr_call.assert_not_called()

    def test_source_measurement_cache_distinguishes_same_image_with_different_canvas(self):
        self.session.workspace._entries['ref_copy'] = dict(self.session.workspace._entries['core'])
        self.ocr_call.return_value = self.ocr_result()
        core = self.session.local_measurements('core')
        reference = self.session.local_measurements('ref_copy')
        self.assertEqual(core['canvas'],[640,360])
        self.assertEqual(reference['canvas'],[320,180])
        self.assertEqual(self.ocr_call.call_count,2)

    def test_background_localization_updates_current_evidence_and_review_binding(self):
        from backend.app.vibe_agent_contract import SelectBackgroundArgs
        self.session.execute('select_background',SelectBackgroundArgs(asset_id='core'),'离线底图测试')
        original = self.session.geometry_context()
        raw = self.ocr_result([
            dict(id='ocr_residual',text='残留旧字',box=[70,80,140,100],confidence=.99)])
        _, entry = self.session.workspace._resolve('background')
        raw.update(source_sha256=entry['sha256'],source_size=[entry['width'],entry['height']])
        self.ocr_call.return_value = raw
        self.session.local_measurements('background',[.1,.3,.6,.7])
        current = self.session.geometry_context()
        self.assertEqual(current['background_measurements']['texts'][0]['text'],'残留旧字')
        self.assertNotEqual(self.session.geometry_stamp(original),self.session.geometry_stamp(current))
        self.assertEqual(len(self.session.state['source_measurement_supplements']),1)

    def test_updated_measurement_evidence_rebuilds_comparisons_without_rerender_or_reusing_review(self):
        self.ocr_call.return_value = self.ocr_result()
        self.responses = [*self.setup_commands(), self.preview(), self.finish_latest]
        self.reviews = [self.passed()]
        candidate = self.session.run()
        original_stamp = candidate['measurement_stamp']
        old_image_ids = set(candidate['image_ids'])
        original_code = candidate['turn']['code']
        renders = len(self.renders)
        self.ocr_call.return_value = self.ocr_result([
            dict(id='ocr_0',text='研究结果',box=[100,20,200,40],confidence=.98)])
        self.session.local_measurements('core',[.2,0,.8,.4])
        self.session.payload()
        self.assertNotEqual(candidate['measurement_stamp'],original_stamp)
        self.assertEqual(candidate['turn']['code'],original_code)
        self.assertEqual(len(self.renders),renders)
        current_images = self.session.creator_images()
        self.assertTrue(set(candidate['image_ids']).issubset(current_images))
        self.assertTrue((old_image_ids-set(candidate['image_ids'])).isdisjoint(current_images))
        old = self.session.state['delivery_reviews'][candidate['preview_id']]
        self.assertFalse(workflow.review_matches(old,candidate))
        self.reviews = [self.passed()]
        self.session.finish(candidate['preview_id'])
        self.assertEqual(self.calls[-1]['payload']['source_measurements']['texts'][0]['text'],'研究结果')
        self.assertTrue(workflow.review_matches(self.session.state['delivery_reviews'][candidate['preview_id']],candidate))

    def test_measure_can_precede_inventory_without_hidden_material_work(self):
        self.responses = [command('measure_text', probes=[dict(id='title', text='研究结果', font_size=32)]),
                          command('set_inventory', inventory=content_inventory()),
                          command('report_blocker', message='测试到此结束，尚未执行取材')]
        with self.assertRaisesRegex(workflow.CreatorBlocked, '测试到此'):
            self.session.run()
        self.assertEqual([event['tool'] for event in self.session.state['events']], ['measure_text', 'set_inventory'])
        self.assertEqual(self.session.state['measurements']['title']['width'], 120)
        self.assertEqual(self.session.workspace.assets(), {})
        self.assertEqual(self.renders[0]['mode'], 'measure')
        self.rmbg_call.assert_not_called()
        self.image_call.assert_not_called()
        self.assertEqual(len(self.task['usage']['model_calls']), 3)
        self.assertTrue(all(call['system'].endswith(TOOL_RESPONSE_GUIDE) for call in self.calls))

    def test_measurement_binding_ignores_cpu_timing_but_not_coordinate_changes(self):
        geometry = {'source_measurements':self.session.local_measurements(), 'background_measurements':None}
        changed = copy.deepcopy(geometry)
        changed['source_measurements'].update(inference_seconds=.001,ocr_cache_hit=True)
        self.assertEqual(self.session.geometry_stamp(geometry),self.session.geometry_stamp(changed))
        changed['source_measurements']['texts'] = [{'text':'new', 'normalized':[.1,.1,.2,.2]}]
        self.assertNotEqual(self.session.geometry_stamp(geometry),self.session.geometry_stamp(changed))

    def test_reserved_review_refreshes_relocalized_source_before_considering_old_rejection(self):
        candidate = self.saved_candidate()
        self.task['max_model_calls'] = 1
        rejected = {**workflow.binding(candidate),'passed':False,'summary':'旧定位需要复核','issues':['原文疑似缺失']}
        self.session.state['delivery_reviews'][candidate['preview_id']] = rejected
        self.ocr_call.return_value = self.ocr_result([
            dict(id='ocr_0',text='研究结果',box=[100,20,200,40],confidence=.98)])
        self.session.local_measurements('core',retry_unavailable=True)
        self.reviews = [self.passed()]
        self.assertTrue(self.session.closing_review())
        self.assertIs(self.session.accepted,candidate)
        self.assertFalse(workflow.review_matches(rejected,candidate))
        self.assertEqual(len(self.calls),1)

    def test_false_budget_blocker_returns_facts_once_then_respects_quality_stop(self):
        self.session.config['layered_image_config'] = {'model':'test-image'}
        self.task['usage']['image_requests'] = 1
        self.responses = [command('report_blocker',message='需要增加图片编辑预算才能清除残留'),
                          command('report_blocker',reason='quality',message='原背景有未解决的接缝，选择停止等待检查')]
        with self.assertRaisesRegex(workflow.CreatorBlocked,'未解决的接缝'):
            self.session.run()
        self.assertEqual(len(self.calls),2)
        receipt=self.session.state['events'][0]['receipt']
        self.assertEqual(receipt['code'],'image_budget_claim_mismatch')
        self.assertEqual(receipt['budget']['image_remaining'],1)
        self.assertTrue(self.session.state['image_budget_claim_corrected'])
        saved=json.loads((self.folder/'agent-state.json').read_text('utf-8'))
        self.assertTrue(saved['image_budget_claim_corrected'])
        self.assertEqual(saved['blocker_report']['arguments']['reason'],'quality')
        self.assertFalse(saved['blocker_report']['returned_for_reconsideration'])
        self.assertEqual(self.task['usage']['image_requests'],1)
        self.image_call.assert_not_called()
        self.assertEqual(self.session.workspace.assets(),{})

    def test_repeated_false_budget_blocker_stops_truthfully_without_loop(self):
        self.session.config['layered_image_config'] = {'model':'test-image'}
        self.task['usage']['image_requests'] = 1
        self.responses = [command('report_blocker',reason='image_budget',message='图片额度不足')]*2
        with self.assertRaises(workflow.CreatorBlocked) as caught:
            self.session.run()
        self.assertEqual(len(self.calls),2)
        self.assertIn('仍剩 1 次',str(caught.exception))
        self.assertNotIn('图片额度不足',str(caught.exception))
        self.assertEqual(self.session.state['blocker_report']['arguments']['message'],'图片额度不足')
        self.image_call.assert_not_called()

    def test_legitimate_additional_images_request_does_not_force_spend(self):
        self.session.config['layered_image_config'] = {'model':'test-image'}
        self.task['usage']['image_requests'] = 1
        self.responses = [command('report_blocker',reason='image_budget',required_image_requests=3,
            message='三张不同来源底板各需一次编辑，剩余1次不够，需要追加图片预算')]
        with self.assertRaisesRegex(workflow.CreatorBlocked,'三张不同来源'):
            self.session.run()
        self.assertEqual(len(self.calls),1)
        self.assertFalse(self.session.state.get('image_budget_claim_corrected',False))
        self.image_call.assert_not_called()

    def test_budget_feedback_exposes_tool_availability_not_credentials(self):
        self.session.config['layered_image_config'] = {'model':'test-image','api_key':'PRIVATE_KEY'}
        payload=self.session.payload()
        self.assertTrue(payload['background_edit_status']['can_submit'])
        self.assertFalse(payload['background_edit_status']['inventory_registered'])
        self.assertIn('core_normalized',payload['background_edit_status']['region_coordinates'])
        self.assertNotIn('PRIVATE_KEY',json.dumps(payload))
        self.session.state['pending_image']={'image_task_id':'a'*32}
        self.assertFalse(self.session.payload()['background_edit_status']['can_submit'])
        self.image_call.assert_not_called()

    def test_clean_empty_upstream_answer_stops_without_retry_or_tool_execution(self):
        error = workflow.api.CloudCallError('上游已结束响应但正文为空或只有空白',
            {'error_code': 'model_empty_completed', 'answer_chars': 27,
             'non_whitespace_chars': 0, 'finish_reason': 'stop', 'stream_done': True})
        self.responses = [error]
        with self.assertRaises(workflow.api.CloudCallError) as caught:
            self.session.run()
        self.assertIs(caught.exception, error)
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(self.calls[0]['system'].endswith(TOOL_RESPONSE_GUIDE))
        self.assertEqual(self.session.state['events'], [])
        self.assertEqual(self.renders, [])
        self.rmbg_call.assert_not_called()
        self.image_call.assert_not_called()

    def test_full_preview_finish_keeps_formal_project_untouched(self):
        formal = self.project / 'record.json'
        formal.write_text('{"formal":"unchanged"}', encoding='utf-8')
        self.responses = self.setup_commands() + [self.preview(), self.finish_latest]
        self.reviews = [self.passed()]
        result = self.session.run()
        self.assertEqual(result['preview_id'], self.session.accepted['preview_id'])
        self.assertEqual(len(self.task['usage']['model_calls']), 6)
        self.assertEqual(formal.read_text(encoding='utf-8'), '{"formal":"unchanged"}')
        self.assertEqual(set(self.session.workspace.assets()), {'background', 'subject_0', 'subject_0_original'})
        self.assertTrue((self.folder / 'agent-state.json').is_file())
        self.assertTrue(all('core' in call['images'] for call in self.calls))
        self.rmbg_call.assert_not_called()
        self.image_call.assert_not_called()

    def test_finish_accepts_json_mode_marker_without_retry_or_formal_project_change(self):
        formal = self.project / 'record.json'
        formal.write_text('{"formal":"unchanged"}', encoding='utf-8')
        core_before = self.core.read_bytes()
        self.responses = self.setup_commands() + [self.preview(), self.finish_latest]
        self.reviews = [{**self.passed(), 'type': 'json_object'}]

        candidate = self.session.run()

        self.assertIs(candidate, self.session.accepted)
        self.assertEqual(len(self.calls), 6)
        self.assertEqual(len(self.task['usage']['model_calls']), 6)
        self.assertEqual(sum(call['system'] == workflow.REVIEW_GUIDE for call in self.calls), 1)
        self.assertEqual([event['tool'] for event in self.session.state['events']],
                         ['set_inventory', 'select_background', 'prepare_subject', 'preview_scene', 'finish'])
        receipt = self.session.state['events'][-1]['receipt']
        self.assertTrue(receipt['ok'])
        self.assertTrue(receipt['review']['passed'])
        self.assertNotIn('type', receipt['review'])
        persisted = json.loads((self.project / candidate['output'] / 'delivery-review.json').read_text('utf-8'))
        self.assertEqual(persisted, receipt['review'])
        self.assertEqual(formal.read_text('utf-8'), '{"formal":"unchanged"}')
        self.assertEqual(self.core.read_bytes(), core_before)
        self.image_call.assert_not_called()
        self.rmbg_call.assert_not_called()

    def test_failed_review_with_json_mode_marker_is_normal_feedback_not_format_retry(self):
        self.responses = self.setup_commands() + [self.preview(), self.finish_latest,
            command('report_blocker', message='离线检查到此结束')]
        self.reviews = [dict(type='json_object', passed=False, summary='标题需修正',
                             issues=['poster 左上角标题被裁切'])]

        with self.assertRaisesRegex(workflow.CreatorBlocked, '离线检查到此结束'):
            self.session.run()

        receipt = next(event['receipt'] for event in self.session.state['events'] if event['tool'] == 'finish')
        self.assertFalse(receipt['ok'])
        self.assertFalse(receipt['review']['passed'])
        self.assertEqual(receipt['review']['issues'], ['poster 左上角标题被裁切'])
        self.assertNotIn('error', receipt)
        self.assertNotIn('type', receipt['review'])
        self.assertIsNone(self.session.accepted)
        self.assertEqual(len(self.calls), 7)
        self.assertEqual(sum(call['system'] == workflow.REVIEW_GUIDE for call in self.calls), 1)
        self.assertEqual(len(self.session.state['review_history']), 1)
        self.image_call.assert_not_called()
        self.rmbg_call.assert_not_called()

    def test_finish_still_rejects_unknown_extra_alongside_json_mode_marker(self):
        self.responses = self.setup_commands() + [self.preview(), self.finish_latest,
            command('report_blocker', message='离线检查到此结束')]
        self.reviews = [{**self.passed(), 'type': 'json_object',
                         'response_format': {'type': 'json_object'}}]

        with self.assertRaisesRegex(workflow.CreatorBlocked, '离线检查到此结束'):
            self.session.run()

        receipt = next(event['receipt'] for event in self.session.state['events'] if event['tool'] == 'finish')
        self.assertFalse(receipt['ok'])
        self.assertIn('交付核查响应未通过', receipt['error'])
        self.assertIn('response_format', receipt['error'])
        self.assertNotIn('review', receipt)
        self.assertIsNone(self.session.accepted)
        self.assertEqual(self.session.state['delivery_reviews'], {})
        self.assertEqual(self.session.state['review_history'], [])
        self.assertEqual(len(self.calls), 7)
        self.assertEqual(sum(call['system'] == workflow.REVIEW_GUIDE for call in self.calls), 1)
        self.assertEqual(list(self.folder.rglob('delivery-review.json')), [])
        self.image_call.assert_not_called()
        self.rmbg_call.assert_not_called()

    def test_review_failure_allows_model_to_inspect_recrop_and_preview_again(self):
        self.responses = self.setup_commands() + [self.preview(), self.finish_latest,
            command('inspect_image', asset_id='core', region=[0, .1, .5, .9]),
            command('prepare_subject', subject_id='subject_0', source_crop=[.05, .1, .45, .9]),
            self.preview('\n'), self.finish_latest]
        self.reviews = [dict(passed=False, summary='外围主体被截断', issues=['左侧头顶被裁掉，需要扩大源图裁区']),
                        self.passed()]
        result = self.session.run()
        self.assertIsNotNone(result)
        self.assertEqual(len(self.session.state['previews']), 2)
        self.assertEqual(self.session.workspace.inventory.subjects[0].source_crop, (.05, .1, .45, .9))
        self.assertEqual(len([call for call in self.calls if call['system'] == workflow.REVIEW_GUIDE]), 2)
        followup = next(call for call in self.calls if call['payload'].get('review_context', {}).get('current_review', {})
                        and call['payload']['review_context']['current_review'].get('passed') is False)
        self.assertIn('扩大源图裁区', followup['payload']['review_context']['current_review']['issues'][0])
        self.assertEqual([row['tool'] for row in self.session.state['events']].count('prepare_subject'), 2)
        self.rmbg_call.assert_not_called()

    def test_changed_material_invalidates_old_preview_without_spending_review_call(self):
        self.responses = self.setup_commands() + [self.preview(),
            command('prepare_subject', subject_id='subject_0', source_crop=[.1, .1, .5, .9]),
            self.finish_latest, self.preview('\n'), self.finish_latest]
        self.reviews = [self.passed()]
        self.session.run()
        finishes = [event['receipt'] for event in self.session.state['events'] if event['tool'] == 'finish']
        self.assertFalse(finishes[0]['ok'])
        self.assertIn('素材/清单已变化', finishes[0]['error'])
        self.assertTrue(finishes[1]['ok'])
        self.assertEqual(len([call for call in self.calls if call['system'] == workflow.REVIEW_GUIDE]), 1)

    def test_preview_detail_ids_match_images_and_remain_inspectable(self):
        detail = self.folder/'detail.png'
        Image.new('RGB', (320,180), '#123456').save(detail)
        with patch('backend.app.vibe_agent_workflow.inspection.build_preview_inspection',
            return_value={'images':{'old_detail':detail},
                          'report':{'items':[{'image_id':'old_detail'}],'selected_image_ids':['old_detail']}}):
            self.responses = self.setup_commands()+[self.preview(),self.finish_latest]
            self.reviews = [self.passed()]
            candidate = self.session.run()
        identity = candidate['inspection']['items'][0]['image_id']
        self.assertIn(identity,candidate['image_ids'])
        self.assertEqual(candidate['inspection']['selected_image_ids'],[identity])
        self.assertIn(identity,self.session.workspace.registry)
        review = next(call for call in self.calls if call['system']==workflow.REVIEW_GUIDE)
        self.assertIn(identity,review['images'])
        self.assertNotIn(candidate['preview_id'],review['images'],'do not send poster pixels twice')

    def test_later_font_measurements_do_not_change_selected_candidate(self):
        self.responses = self.setup_commands()+[self.preview(),
            command('measure_text',probes=[dict(id='title',text='研究结果',font_size=80)]),self.finish_latest]
        self.reviews = [self.passed()]
        candidate = self.session.run()
        self.assertEqual(candidate['measurements'],{})
        self.assertIn('title',self.session.state['measurements'])
        with patch.object(self.session,'render',return_value=({'measurements':{}},self.folder)) as render, \
                patch('backend.app.vibe_agent_workflow.renderer.mux'), \
                patch('backend.app.vibe_agent_workflow.api.ffmpeg_binary',return_value='offline'):
            self.session.render_delivery(candidate,self.project/'audio.wav',0)
        self.assertEqual(render.call_args.kwargs['measurements'],{})

    def test_local_material_failure_is_feedback_not_pipeline_deadlock(self):
        self.responses = [command('set_inventory', inventory=content_inventory()),
            command('prepare_subject', subject_id='subject_0', treatment='cutout'),
            command('prepare_subject', subject_id='subject_0', treatment='preserve'),
            command('select_background', color='#123456'), self.preview(), self.finish_latest]
        self.rmbg_call.side_effect = MaterialError('本地模型组件暂不可用')
        self.reviews = [self.passed()]
        self.session.run()
        attempts = [event['receipt'] for event in self.session.state['events'] if event['tool'] == 'prepare_subject']
        self.assertFalse(attempts[0]['ok'])
        self.assertIn('暂不可用', attempts[0]['error'])
        self.assertFalse(attempts[1]['rmbg_used'])
        self.assertEqual(self.session.workspace.inventory.subjects[0].treatment, 'preserve')
        self.rmbg_call.assert_called_once()
        self.image_call.assert_not_called()

    def test_authorized_total_budget_stops_without_one_more_request(self):
        self.task['max_model_calls'] = 2
        self.responses = [command('inspect_image', asset_id='core'), command('inspect_image', asset_id='core')]
        with self.assertRaisesRegex(workflow.BudgetEnded, '2 次'):
            self.session.run()
        self.assertEqual(self.model.call_count, 2)
        self.assertEqual(len(self.task['usage']['model_calls']), 2)
        self.assertIsNone(self.session.accepted)

    def test_transport_failure_never_auto_retries_or_switches_channel(self):
        self.responses = [RuntimeError('upstream response transport interrupted')]
        with self.assertRaisesRegex(RuntimeError, 'transport interrupted'):
            self.session.run()
        self.assertEqual(self.model.call_count, 1)
        self.assertEqual(len(self.task['usage']['model_calls']), 1)
        self.assertEqual(self.task['image_route']['key'], 'project-personal')
        self.assertEqual(self.session.state['events'], [])

    def test_cancellation_before_call_and_after_response_does_not_execute_tool(self):
        self.event.set()
        with self.assertRaises(InterruptedError):
            self.session.run()
        self.model.assert_not_called()
        self.event.clear()

        def cancelled_response():
            self.event.set()
            return command('set_inventory', inventory=content_inventory())

        self.responses = [cancelled_response]
        with self.assertRaises(InterruptedError):
            self.session.run()
        self.assertEqual(self.model.call_count, 1)
        self.assertIsNone(self.session.workspace.inventory)
        self.assertEqual(self.session.state['sequence'], 0)

    def test_uncertain_image_submission_stops_loop_instead_of_retry_feedback(self):
        self.responses = [command('set_inventory', inventory=content_inventory()), command('edit_background')]
        self.mock('backend.app.vibe_agent_workflow.AgentSession.edit_background',
                  side_effect=MaterialError('已提交图片但结果尚未确认，停止避免重复收费', 'image-task-id'))
        with self.assertRaisesRegex(MaterialError, '避免重复收费'):
            self.session.run()
        self.assertEqual(self.model.call_count, 2)
        self.assertEqual([event['tool'] for event in self.session.state['events']], ['set_inventory'])
        self.assertIsNone(self.session.accepted)

    def test_invalid_preview_reports_error_and_model_may_rewrite(self):
        self.responses = self.setup_commands() + [self.preview(), self.preview('\n'), self.finish_latest]
        self.validate_call.side_effect = [ValueError('文字越界'), None]
        self.reviews = [self.passed()]
        self.session.run()
        previews = list(self.session.state['previews'].values())
        self.assertFalse(previews[0]['locally_valid'])
        self.assertIn('文字越界', previews[0]['validation_error'])
        self.assertTrue(previews[1]['locally_valid'])
        self.assertEqual(self.session.accepted['preview_id'], previews[1]['preview_id'])

    def test_review_context_is_versioned_and_old_findings_are_not_new_facts(self):
        self.responses = self.setup_commands() + [self.preview(), self.finish_latest,
            self.preview('\n'), self.finish_latest]
        self.reviews = [dict(passed=False, summary='检查边缘', issues=['左侧缺一个主体']), self.passed()]
        self.session.run()
        first, second = list(self.session.state['previews'].values())
        next_finish = next(call for call in self.calls
            if call['payload'].get('current_preview', {}).get('preview_id') == second['preview_id'])
        payload = next_finish['payload']
        self.assertIsNone(payload['review_context']['current_review'])
        issue = payload['review_context']['previous_findings_to_recheck'][0]
        self.assertFalse(issue['current_fact'])
        self.assertEqual(issue['status'], 'needs_visual_recheck')
        self.assertEqual(issue['observed_on'][0]['preview_id'], first['preview_id'])
        self.assertNotIn('delivery_review', payload['observations'])
        for item in payload['previews']:
            self.assertNotIn('inspection', item)
            self.assertNotIn('dom', item)
            self.assertNotIn('turn', item)
        second_review = [call for call in self.calls if call['system'] == workflow.REVIEW_GUIDE][-1]
        self.assertEqual(second_review['payload']['candidate']['preview_id'], second['preview_id'])
        self.assertEqual(second_review['payload']['previous_findings_to_recheck'][0]['observation'], '左侧缺一个主体')
        self.assertEqual(len(self.session.state['delivery_reviews']), 2)
        self.assertEqual(self.session.state['events'][4]['receipt']['review']['issues'], ['左侧缺一个主体'])

    def test_review_images_have_roles_and_explicit_actual_background_binding(self):
        self.responses = self.setup_commands() + [self.preview(), self.finish_latest]
        self.reviews = [self.passed()]
        candidate = self.session.run()
        review = next(call for call in self.calls if call['system'] == workflow.REVIEW_GUIDE)
        self.assertIn('background', review['images'])
        self.assertEqual(set(review['images']), set(review['payload']['image_roles']))
        roles = review['payload']['image_roles']
        self.assertEqual(roles['background']['role'],'prepared_background_plate')
        self.assertFalse(roles['background']['is_rendered_result'])
        self.assertEqual(roles['background'],review['payload']['background_binding'])
        self.assertEqual(roles['core']['role'], 'source_poster')
        self.assertEqual(roles['poster']['role'], 'rendered_poster')
        self.assertEqual(roles['poster']['preview_id'], candidate['preview_id'])
        self.assertEqual(roles[candidate['preview_id']+'_keyframes']['role'], 'animation_keyframes')

    def test_final_render_does_not_trust_another_candidates_passed_review(self):
        self.responses = self.setup_commands() + [self.preview(), self.finish_latest]
        self.reviews = [self.passed()]
        candidate = self.session.run()
        self.session.state['delivery_reviews'][candidate['preview_id']]['code_sha256'] = 'wrong'
        # Even a global 'passed' cannot bypass exact candidate binding.
        self.session.state['observations']['delivery_review'] = self.passed()
        with patch.object(self.session, 'render') as render:
            with self.assertRaisesRegex(ValueError, '绑定本版代码'):
                self.session.render_delivery(candidate, self.project/'audio.wav', 0)
        render.assert_not_called()

    def test_preview_zoom_is_labelled_with_its_source_candidate(self):
        def inspect_latest():
            identity = next(reversed(self.session.state['previews']))
            return command('inspect_image', asset_id=identity, region=[.3,.2,.8,.8])
        self.responses = self.setup_commands() + [self.preview(), inspect_latest, self.finish_latest]
        self.reviews = [self.passed()]
        candidate = self.session.run()
        call = next(call for call in self.calls if any(name.startswith('inspection_') for name in call['images']))
        role = next(value for key, value in call['payload']['image_roles'].items() if key.startswith('inspection_'))
        self.assertEqual(role['role'], 'rendered_poster')
        self.assertEqual(role['preview_id'], candidate['preview_id'])
        self.assertEqual(role['region'], (.3,.2,.8,.8))

    def test_context_keeps_only_one_current_dom_and_no_historical_inspection(self):
        self.responses = self.setup_commands() + [self.preview(), self.preview('\n'), self.preview('\n\n'), self.finish_latest]
        self.reviews = [self.passed()]
        self.session.run()
        payload = self.session.payload()
        self.assertIn('dom', payload['current_preview'])
        for receipt in payload['observations'].values():
            self.assertNotIn('dom', receipt)
            self.assertNotIn('inspection', receipt)
        for row in payload['recent_tool_results']:
            self.assertNotIn('dom', row['receipt'])
            self.assertNotIn('inspection', row['receipt'])
        self.assertEqual(len(payload['previews']), 3)
        self.assertEqual(len([p for p in payload['previews'] if p['current']]), 1)
        # Disk audit keeps full evidence independently of the prompt projection.
        self.assertIn('inspection', self.session.state['previews'][payload['previews'][0]['preview_id']])

    def test_format_feedback_expires_after_a_valid_tool_without_erasing_audit(self):
        error = workflow.ModelResponseFormatError('JSON格式错误', response_text='{invalid')
        self.responses = [error, command('inspect_image', asset_id='core'),
                          command('report_blocker', message='离线验证结束')]
        old_review = dict(passed=False, summary='待复查', issues=['主体边缘仍需复查'])
        self.session.state['observations']['delivery_review'] = copy.deepcopy(old_review)
        with self.assertRaises(workflow.CreatorBlocked):
            self.session.run()
        active = self.calls[1]['payload']['observations']['response_format']
        self.assertEqual(active['model_call_number'], 1)
        self.assertEqual(active['tool_sequence'], 0)
        self.assertEqual(active['scope'], 'this_model_response_only')
        self.assertNotIn('response_format', self.calls[2]['payload']['observations'])
        self.assertNotIn('response_format', self.session.state['observations'])
        formats = [event for event in self.session.state['events'] if event['tool'] == 'response_format']
        self.assertEqual(len(formats), 1)
        self.assertEqual(formats[0]['receipt']['previous_response'], '{invalid')
        self.assertEqual(self.session.state['observations']['delivery_review'], old_review)
        self.assertIsNone(self.session.accepted)
        self.assertEqual(self.model.call_count, 3)
        self.image_call.assert_not_called()

    def test_corrected_tool_contract_does_not_leave_old_inventory_error_active(self):
        bad = command('set_inventory', inventory={})
        self.responses = [bad, command('set_inventory', inventory=content_inventory()),
                          command('inspect_image', asset_id='core'),
                          command('report_blocker', message='离线验证结束')]
        with self.assertRaises(workflow.CreatorBlocked):
            self.session.run()
        self.assertIn('response_format', self.calls[1]['payload']['observations'])
        for call in self.calls[2:]:
            self.assertNotIn('response_format', call['payload']['observations'])
        self.assertEqual(self.session.state['events'][0]['receipt']['previous_response'], bad)
        self.assertTrue(self.session.state['observations']['set_inventory']['ok'])
        self.assertEqual(self.model.call_count, 4)

    def test_valid_envelope_with_tool_failure_replaces_only_parse_feedback(self):
        self.responses = [workflow.ModelResponseFormatError('JSON格式错误', response_text='{invalid'),
                          command('inspect_image', asset_id='missing_material'),
                          command('inspect_image', asset_id='core'),
                          command('report_blocker', message='离线验证结束')]
        with self.assertRaises(workflow.CreatorBlocked):
            self.session.run()
        payload = self.calls[2]['payload']
        self.assertNotIn('response_format', payload['observations'])
        self.assertFalse(payload['observations']['inspect_image']['ok'])
        self.assertTrue(payload['observations']['inspect_image']['error'])
        self.assertEqual(self.session.state['events'][0]['tool'], 'response_format')
        self.assertEqual(self.session.state['events'][1]['tool'], 'inspect_image')
        self.assertFalse(self.session.state['events'][1]['receipt']['ok'])
        self.assertIsNone(self.session.accepted)

    def test_repeated_format_errors_keep_only_latest_round_active(self):
        self.responses = [workflow.ModelResponseFormatError('第一次JSON错误', response_text='{first'),
                          workflow.ModelResponseFormatError('第二次JSON错误', response_text='{second'),
                          command('inspect_image', asset_id='core'),
                          command('report_blocker', message='离线验证结束')]
        with self.assertRaises(workflow.CreatorBlocked):
            self.session.run()
        self.assertEqual(self.calls[1]['payload']['observations']['response_format']['model_call_number'], 1)
        self.assertEqual(self.calls[2]['payload']['observations']['response_format']['model_call_number'], 2)
        self.assertNotIn('response_format', self.calls[3]['payload']['observations'])
        formats = [event['receipt'] for event in self.session.state['events'] if event['tool'] == 'response_format']
        self.assertEqual([item['model_call_number'] for item in formats], [1, 2])
        self.assertEqual([item['previous_response'] for item in formats], ['{first', '{second'])
        self.assertEqual(self.model.call_count, 4)

    def test_restore_legacy_state_clears_superseded_error_and_preserves_latest_error(self):
        old = dict(ok=False, error='旧契约错误', previous_response={'tool': 'set_inventory'})
        for later_tool in (False, True):
            with self.subTest(later_tool=later_tool):
                self.session.state['events'] = [dict(tool='response_format', summary='错误', receipt=copy.deepcopy(old))]
                self.session.state['observations']['response_format'] = copy.deepcopy(old)
                if later_tool:
                    self.session.state['events'].append(dict(tool='inspect_image', summary='合法调用但素材不可用',
                                                            receipt=dict(ok=False, error='素材不存在')))
                audit = copy.deepcopy(self.session.state['events'])
                self.session.save()
                self.session.restore()
                self.assertEqual('response_format' in self.session.state['observations'], not later_tool)
                self.assertEqual(self.session.state['events'], audit)
        self.model.assert_not_called()
        self.image_call.assert_not_called()

    def test_restore_uses_tool_sequence_to_expire_feedback_before_missing_tool_receipt(self):
        self.session.state['sequence'] = 4
        old = dict(ok=False, error='旧格式错误', model_call_number=3, tool_sequence=3,
                   scope='this_model_response_only')
        self.session.state['observations']['response_format'] = copy.deepcopy(old)
        self.session.state['events'] = [dict(tool='response_format', summary='错误', receipt=copy.deepcopy(old))]
        self.session.save()
        self.session.restore()
        self.assertNotIn('response_format', self.session.state['observations'])
        self.assertEqual(self.session.state['events'][0]['receipt'], old)
        self.model.assert_not_called()

    def stable_session(self):
        self.task['advanced_subject_separation'] = False
        self.session = workflow.AgentSession(self.project, 'shot_generic', 'a'*32, {}, self.session.data,
            self.event, self.session.context, {'core': self.core})
        raw = content_inventory()
        raw['background'] = 'extract'
        raw['background_is_uniform'] = False
        raw['subjects'] = []
        raw['background_retained'] = [dict(name='示意主体', source_box=[.1,.2,.4,.8],
            reason='默认保留完整主体与底图光照关系，不作独立运动',content_kind='illustration')]
        raw['foreground_summary'].update(subject_count=0, background_retained_count=1)
        return raw

    def saved_candidate(self):
        for raw in self.setup_commands() + [self.preview()]:
            name, args = workflow.parse_tool_call(raw)
            result = self.session.execute(name, args, raw['summary'])
            self.session.record(name, raw['summary'], result['receipt'], result.get('images'))
        return self.session.current_candidate()

    def layout_candidate(self):
        for raw in self.setup_commands() + [command('preview_scene', code=CODE, preview_level='layout')]:
            name, args = workflow.parse_tool_call(raw)
            result = self.session.execute(name, args, raw['summary'])
            self.session.record(name, raw['summary'], result['receipt'], result.get('images'))
        return self.session.current_candidate()

    def test_layout_check_is_explicit_and_finish_promotes_same_code_before_review(self):
        candidate = self.layout_candidate()
        self.assertEqual(candidate['preview_level'], 'layout')
        self.assertEqual(self.renders[-1]['preview_level'], 'layout')
        self.assertFalse(self.validate_call.call_args.kwargs['require_animation'])
        self.assertTrue(self.session.payload()['current_preview']['full_motion_check_required'])
        self.session.state['measurements']['unrelated_later_probe'] = {'width': 900}
        self.reviews = [self.passed()]
        result = self.session.finish(candidate['preview_id'])
        new = self.session.accepted
        self.assertTrue(result['receipt']['ok'])
        self.assertNotEqual(new['preview_id'], candidate['preview_id'])
        self.assertEqual(new['code_sha256'], candidate['code_sha256'])
        self.assertEqual(new['measurements'], candidate['measurements'])
        self.assertEqual(new['preview_level'], 'full')
        self.assertEqual([r['preview_level'] for r in self.renders], ['layout', 'full'])
        self.assertTrue(self.validate_call.call_args.kwargs['require_animation'])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]['payload']['candidate']['preview_id'], new['preview_id'])
        self.image_call.assert_not_called()

    def test_default_layout_then_finish_checks_motion_without_extra_creator_call(self):
        self.responses = self.setup_commands() + [command('preview_scene',code=CODE),self.finish_latest]
        self.reviews = [self.passed()]
        accepted=self.session.run()
        self.assertEqual([r['preview_level'] for r in self.renders],['layout','full'])
        self.assertEqual(accepted['preview_level'],'full')
        self.assertEqual(len([c for c in self.calls if c['system'] != workflow.REVIEW_GUIDE]),5)
        self.assertEqual(len([c for c in self.calls if c['system'] == workflow.REVIEW_GUIDE]),1)
        self.assertTrue(self.validate_call.call_args.kwargs['require_animation'])
        self.assertTrue(all(c['payload']['preview_policy']['default']=='layout' for c in self.calls
                            if c['system'] != workflow.REVIEW_GUIDE))
        self.image_call.assert_not_called()

    def test_local_preview_progress_distinguishes_wait_layout_and_motion(self):
        candidate=self.layout_candidate()
        stages=[row.get('stage') for row in self.status_updates if row.get('stage')]
        self.assertEqual(stages[-2:],['render_wait','layout_preview'])
        self.assertIn('不编码整段视频',self.task['message'])
        self.reviews=[self.passed()]
        self.session.finish(candidate['preview_id'])
        stages=[row.get('stage') for row in self.status_updates if row.get('stage')]
        self.assertIn('motion_check',stages)
        self.assertEqual(stages[-1],'agent_review')
        motion=next(row for row in self.status_updates if row.get('stage')=='motion_check')
        self.assertIn('关键帧',motion['message'])
        self.assertIn('不编码整段视频',motion['message'])

    def test_failed_motion_promotion_returns_new_evidence_without_paid_review(self):
        candidate = self.layout_candidate()
        self.validate_call.side_effect = ValueError('结尾阅读阶段文字被裁切')
        result = self.session.finish(candidate['preview_id'])
        self.assertFalse(result['receipt']['ok'])
        self.assertIn('结尾阅读', result['receipt']['error'])
        self.assertEqual(result['receipt']['promoted_from_preview'], candidate['preview_id'])
        self.assertEqual(self.session.current_candidate()['preview_level'], 'full')
        self.assertFalse(self.session.current_candidate()['locally_valid'])
        self.assertIsNone(self.session.accepted)
        self.model.assert_not_called()

    def test_layout_cannot_directly_render_delivery_even_with_forged_passed_review(self):
        candidate = self.layout_candidate()
        self.session.state['delivery_reviews'][candidate['preview_id']] = {
            **workflow.binding(candidate), **self.passed()}
        with patch.object(self.session, 'render') as render:
            with self.assertRaisesRegex(ValueError, '快速静态校样'):
                self.session.render_delivery(candidate, self.project/'audio.wav', 0)
        render.assert_not_called()

    def test_layout_promotion_refuses_changed_materials_and_preserves_review_budget(self):
        candidate = self.layout_candidate()
        raw = content_inventory()
        raw['subjects'][0]['name'] = '更新主体描述'
        self.session.workspace.set_inventory(raw)
        with self.assertRaisesRegex(ValueError, '素材/清单已变化'):
            self.session.finish(candidate['preview_id'])
        self.assertEqual(len(self.renders), 1)
        self.model.assert_not_called()

    def test_last_review_call_can_promote_layout_without_an_extra_creator_call(self):
        candidate = self.layout_candidate()
        self.task['max_model_calls'] = 1
        self.reviews = [self.passed()]
        accepted = self.session.run()
        self.assertNotEqual(candidate['preview_id'], accepted['preview_id'])
        self.assertEqual(accepted['preview_level'], 'full')
        self.assertEqual(len(self.calls), 1)

    def measure(self, **values):
        args = workflow.ARGUMENT_MODELS['measure_text'].model_validate(values)
        return self.session.execute('measure_text', args, '自主测量')['receipt']

    def test_measurement_cache_reuses_identical_text_with_different_ids(self):
        probe = dict(text='通用文字', font_size=32)
        first = self.measure(probes=[dict(probe, id='first'), dict(probe, id='second')])
        self.assertEqual(first['browser_measurements'], 1)
        self.assertEqual([r['id'] for r in first['measured']], ['first', 'second'])
        first['measured'][0]['width'] = 999
        second = self.measure(probes=[dict(probe, id='third')])
        self.assertTrue(second['cached'])
        self.assertEqual(second['measured'][0]['width'], 120)
        self.assertEqual(second['measured'][0]['id'], 'third')
        self.assertEqual(len(self.renders), 1)
        self.assertNotIn('measurement_cache', self.session.state)
        self.model.assert_not_called()

    def test_measurement_cache_keys_include_font_text_width_and_canvas(self):
        probe = dict(id='measure', text='通用文字', font_size=32)
        self.measure(probes=[probe])
        for change in (dict(font_size=34), dict(font_weight=800), dict(font_family='serif'),
                       dict(max_width=200), dict(text='另一段文字')):
            result = self.measure(probes=[{**probe, **change}])
            self.assertFalse(result['cached'])
        self.session.context['canvas'] = [360, 640]
        self.assertFalse(self.measure(probes=[probe])['cached'])
        self.assertEqual(len(self.renders), 7)

    def test_partial_measurement_cache_only_renders_missing_probes(self):
        old = dict(id='old', text='相同文字', font_size=32)
        new = dict(id='new', text='新文字', font_size=32)
        self.measure(probes=[old])
        result = self.measure(probes=[old, new])
        self.assertEqual(result['browser_measurements'], 1)
        self.assertEqual([r['id'] for r in self.renders[-1]['measure_requests']], ['new'])
        self.assertEqual([r['id'] for r in result['measured']], ['old', 'new'])

    def test_last_call_reserved_for_real_review_without_extra_creator_decision(self):
        candidate = self.saved_candidate()
        self.task['max_model_calls'] = 1
        self.reviews = [self.passed()]
        self.assertEqual(self.session.run()['preview_id'], candidate['preview_id'])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]['system'], workflow.REVIEW_GUIDE)
        self.assertTrue(self.session.state['events'][-1]['receipt']['reserved_review_call'])
        self.image_call.assert_not_called()

    def test_reserved_review_never_accepts_failed_verdict(self):
        self.saved_candidate()
        self.task['max_model_calls'] = 1
        self.reviews = [dict(passed=False, summary='文字实际遮挡', issues=['poster 标题遮挡主体'])]
        with self.assertRaises(workflow.BudgetEnded):
            self.session.run()
        self.assertIsNone(self.session.accepted)
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(self.session.state['events'][-1]['receipt']['review']['passed'])

    def test_reserved_review_does_not_revive_old_code_or_repeat_known_rejection(self):
        candidate = self.saved_candidate()
        self.task['max_model_calls'] = 1
        self.session.state['latest_code']['code'] += '\n'
        self.assertFalse(self.session.closing_review())
        self.session.state['latest_code']['code'] = candidate['turn']['code']
        rejected = {**workflow.binding(candidate), 'passed': False, 'summary': '仍需修正', 'issues': ['标题越界']}
        self.session.state['delivery_reviews'][candidate['preview_id']] = rejected
        with self.assertRaisesRegex(workflow.BudgetEnded, '不足以返工'):
            self.session.closing_review()
        self.model.assert_not_called()

    def test_current_poster_survives_inspection_or_measurement_tool(self):
        candidate = self.saved_candidate()
        args = workflow.ARGUMENT_MODELS['inspect_image'].model_validate({'asset_id': 'core'})
        result = self.session.execute('inspect_image', args, '检查输入')
        self.session.record('inspect_image', '检查输入', result['receipt'], result.get('images'))
        images = self.session.creator_images()
        self.assertIn('core', images)
        self.assertIn(candidate['preview_id'], images)
        self.assertIn(candidate['preview_id']+'_keyframes', images)
        self.session.record('measure_text', '测量', {'ok': True})
        self.assertIn(candidate['preview_id'], self.session.creator_images())

    def test_identical_inspection_reuses_crop_and_preserves_scope(self):
        args = workflow.ARGUMENT_MODELS['inspect_image'].model_validate(
            {'asset_id': 'core', 'region': [.1, .1, .8, .8]})
        first = self.session.inspect(args)
        self.session.workspace.set_inventory(content_inventory())
        second = self.session.inspect(args)
        self.assertEqual(first['receipt']['asset_id'], second['receipt']['asset_id'])
        self.assertTrue(second['receipt']['cached'])
        self.assertEqual(second['receipt']['requested_asset_id'], 'core')
        self.assertEqual(second['receipt']['region'], (.1, .1, .8, .8))
        self.assertEqual(second['receipt']['epoch'], self.session.workspace.epoch)
        self.assertEqual(second['receipt']['fingerprint'], self.session.workspace.fingerprint())
        self.assertEqual(len(self.session.state['inspected_images']), 1)
        different = self.session.inspect(workflow.ARGUMENT_MODELS['inspect_image'].model_validate(
            {'asset_id': 'core', 'region': [.2, .1, .8, .8]}))
        self.assertNotEqual(first['receipt']['asset_id'], different['receipt']['asset_id'])
        self.model.assert_not_called()

    def test_continue_preview_revalidates_same_code_without_inheriting_review(self):
        old = self.saved_candidate()
        self.session.state['measurements']['later_probe'] = {'width': 999, 'height': 80}
        self.session.state['delivery_reviews'][old['preview_id']] = {**workflow.binding(old), **self.passed()}
        self.session.save()
        self.session.restore()
        self.validate_call.side_effect = ValueError('新版检查发现真实问题')
        self.session.continue_saved_preview()
        new = self.session.current_candidate()
        self.assertNotEqual(new['preview_id'], old['preview_id'])
        self.assertEqual(new['code_sha256'], old['code_sha256'])
        self.assertEqual(new['fingerprint'], old['fingerprint'])
        self.assertEqual(new['measurements'], old['measurements'])
        self.assertEqual(self.renders[-1]['measurements'], old['measurements'])
        self.assertEqual(self.renders[-1]['preview_level'],'layout')
        self.assertEqual(new['preview_level'],'layout')
        self.assertFalse(self.validate_call.call_args.kwargs['require_animation'])
        self.assertFalse(new['locally_valid'])
        self.assertFalse(self.session.state['new_images_allowed'])
        self.assertEqual(self.session.budgets()['image_remaining'], 0)
        self.assertNotIn(new['preview_id'], self.session.state['delivery_reviews'])
        self.assertIn(new['preview_id'], self.session.creator_images())
        self.assertEqual(self.session.state['events'][-1]['receipt']['continued_from_preview'], old['preview_id'])
        self.model.assert_not_called()

    def test_continue_quick_layout_cannot_reuse_old_pass_or_skip_full_check(self):
        old=self.saved_candidate()
        self.session.state['delivery_reviews'][old['preview_id']]={**workflow.binding(old),**self.passed()}
        self.session.save()
        self.session.restore()
        self.session.continue_saved_preview()
        quick=self.session.current_candidate()
        self.assertEqual(quick['preview_level'],'layout')
        self.assertNotIn(quick['preview_id'],self.session.state['delivery_reviews'])
        self.model.assert_not_called()
        self.reviews=[self.passed()]
        self.session.finish(quick['preview_id'])
        self.assertEqual([r['preview_level'] for r in self.renders],['full','layout','full'])
        self.assertEqual(self.session.accepted['code_sha256'],old['code_sha256'])
        self.assertEqual(self.session.accepted['preview_level'],'full')
        self.assertNotEqual(self.session.accepted['preview_id'],old['preview_id'])
        self.assertEqual(len(self.calls),1)
        self.assertFalse(self.session.state['new_images_allowed'])
        self.image_call.assert_not_called()

    def test_repeat_feedback_uses_last_result_and_accepts_success_without_ok_field(self):
        self.session.state['events'] = [
            dict(tool='inspect_image', summary='失败', request_key='same', receipt={'error': '旧错误'}),
            dict(tool='inspect_image', summary='成功', request_key='same', receipt={'asset_id': 'core'})]
        repeated = self.session.payload()['repeated_requests']
        self.assertEqual(repeated, [dict(tool='inspect_image', attempts=2, latest_result='成功')])

    def test_failed_worker_restore_does_not_overwrite_saved_checkpoint(self):
        self.saved_candidate()
        checkpoint = self.folder/'agent-state.json'
        old = json.loads(checkpoint.read_text(encoding='utf-8'))
        old['version'] = 999
        checkpoint.write_text(json.dumps(old), encoding='utf-8')
        original = checkpoint.read_bytes()
        self.task['cloud_input_stamp'] = 'same-input'
        context = self.session.context
        snap = (None, None, 'audio-digest', None, None, None)
        with patch.object(workflow.api, '_context', return_value=(snap, {}, context, {'core': self.core})), \
             patch.object(workflow.api, '_cloud_stamp', return_value='same-input'), \
             patch.object(workflow.api.previews, '_audio', return_value=('audio-digest', 0)):
            workflow.run_worker(self.project, 'shot_generic', 'a'*32, {}, self.session.data,
                                self.event, continue_preview=True)
        self.assertEqual(checkpoint.read_bytes(), original)
        self.assertEqual(self.task['status'], 'failed')
        self.assertIn('版本不一致', self.task['message'])
        self.model.assert_not_called()
        self.image_call.assert_not_called()

    def test_continue_preview_refuses_changed_code_or_pending_paid_material(self):
        self.saved_candidate()
        self.session.state['pending_image'] = {'image_task_id': 'submitted'}
        with self.assertRaisesRegex(ValueError, '待核对'):
            self.session.continue_saved_preview()
        self.session.state['pending_image'] = None
        self.session.state['latest_code']['code'] += '\n'
        with self.assertRaisesRegex(ValueError, '不一致'):
            self.session.continue_saved_preview()
        self.model.assert_not_called()

    def test_stable_tool_loop_recovers_object_plan_without_hidden_cutout(self):
        raw = self.stable_session()
        material=self.folder/'reusable-base.png'
        Image.new('RGB',(640,360),'#123456').save(material)
        # The recipe is from the actual inventory adapter, not a hand-written
        # candidate answer. This local material substitutes only the paid image.
        from backend.app.vibe_layered_inventory import Inventory
        recipe=workflow.layered._background_recipe(Inventory.model_validate(raw).to_design(
            self.session.context,self.session.sources),advanced_subject_separation=False)
        identity=self.session.workspace.bind_background(material,recipe)['receipt']['asset_id']
        code='function Scene(ctx) { return <Group id="intro" at={0.4} enter={0.7} motion="fade"><Text id="title" style={{fontSize:32}}>研究结果</Text></Group>; }'
        self.responses=[command('set_inventory',inventory=content_inventory()),
            command('set_inventory',inventory=raw),command('select_background',asset_id=identity),
            command('preview_scene',code=code),self.finish_latest]
        self.reviews=[self.passed()]
        result=self.session.run()
        self.assertIsNotNone(result)
        self.assertEqual(set(self.session.workspace.assets()), {'background'})
        self.assertFalse(self.session.state['events'][0]['receipt']['ok'])
        self.assertIn('进阶主体分离', self.session.state['events'][0]['receipt']['error'])
        self.assertTrue(all(not call['payload']['material_strategy']['advanced_subject_separation'] for call in self.calls))
        self.assertEqual(self.renders[0]['code'],code)
        self.rmbg_call.assert_not_called()
        self.image_call.assert_not_called()

    def test_stable_background_edit_uses_user_policy_and_project_route(self):
        raw=self.stable_session()
        self.session.workspace.set_inventory(raw)
        self.session.config.update(layered_image_config={'model':'project-image'},layered_owner_id=1)
        image=self.folder/'completed-background.png'
        Image.new('RGB',(640,360),'#123456').save(image)
        self.image_call.side_effect=None
        self.image_call.return_value={'path':image}
        with patch.object(workflow.layered,'_cached_background',return_value=None), \
                patch.object(workflow.layered,'_save_background_ready'):
            result=self.session.edit_background('保留原装饰')
        self.assertEqual(result['receipt']['render_asset_id'],'background')
        self.image_call.assert_called_once()
        self.assertIn('采用稳定分层策略',self.image_call.call_args.args[1])
        self.assertEqual(self.image_call.call_args.kwargs['image_route']['key'],'project-personal')
        self.assertFalse(self.session.workspace.snapshot()['background']['recipe']['advanced_subject_separation'])
        self.assertEqual(self.task['usage']['image_requests'],1)
        self.rmbg_call.assert_not_called()


if __name__ == '__main__':
    unittest.main()
