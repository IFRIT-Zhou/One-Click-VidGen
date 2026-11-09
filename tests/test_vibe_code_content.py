"""Offline contracts for authored-code content, measurements and geometry.

These fixtures contain no real patient/project data and make no provider calls.
"""
import copy
from pathlib import Path
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from backend.app.vibe_code_workflow import Probe, Turn, validate_rendered
from backend.app import vibe_code_renderer as renderer


def turn(**changes):
    return Turn.model_validate(dict(action='preview', summary='按原图复原并分阶段出现',
        code='function Scene(ctx){return <Text id="title">研究</Text>;}', **changes))


def inventory(texts=('研究结果', '49对患者'), subject_count=0):
    return dict(labels=[dict(text=value) for value in texts],
                subjects=[dict(crop=[0, 0, 1, 1], embedded_text=[]) for _ in range(subject_count)])


def measured(texts=('研究结果', '49对患者'), asset_ids=()):
    labels=[dict(id=f'text_{index}', text=text, visible=True, opacity=1,
                 font_size=40, box=[40, 30+index*80, 500, 60],
                 rects=[dict(left=40, top=30+index*80, right=500, bottom=80+index*80)])
            for index, text in enumerate(texts)]
    assets=[dict(id=value, visible=True, opacity=1, box=[0, 100, 400, 200], crop=[0, 0, 1, 1])
            for value in asset_ids]
    return dict(texts=labels, assets=assets, diagnostics=[], frames=[
        dict(id='early', time=.5, texts=[dict(row, visible=False, opacity=0) for row in labels],
             assets=[dict(row, visible=False, opacity=0) for row in assets], diagnostics=[]),
        dict(id='hold', time=9, texts=copy.deepcopy(labels), assets=copy.deepcopy(assets), diagnostics=[]),
    ])


class CodeTurnContractTests(unittest.TestCase):
    def test_measure_and_preview_are_exclusive_actions(self):
        request=Probe(id='title', text='结论', font_size=64)
        value=Turn(action='measure', summary='测量标题', probes=[request])
        self.assertEqual(value.probes[0].font_family, 'sans')
        self.assertEqual(value.probes[0].font_weight, 600)
        self.assertEqual(turn().action, 'preview')
        for change in [dict(action='measure', summary='x'),
                       dict(action='measure', summary='x', probes=[request], code='extra'),
                       dict(action='preview', summary='x'),
                       dict(action='preview', summary='x', code='x', probes=[request])]:
            with self.subTest(change=change), self.assertRaises(ValidationError):
                Turn.model_validate(change)

    def test_probe_matches_browser_measurement_bounds(self):
        for change in [dict(font_size=513), dict(font_weight=450), dict(max_width=4096),
                       dict(font_family='url(https://example.invalid/font)'),
                       dict(font_size=float('nan')), dict(max_width=float('inf'))]:
            data=dict(id='title', text='标题', font_size=60)
            data.update(change)
            with self.subTest(change=change), self.assertRaises(ValidationError):
                Probe.model_validate(data)
        for weight in range(100, 901, 100):
            self.assertEqual(Probe(id='title', text='标题', font_size=60, font_weight=weight).font_weight, weight)
        self.assertEqual(Probe(id='number', text='42', font_size=512).font_size, 512)

    def test_duplicate_probe_ids_and_unbounded_declarations_are_rejected(self):
        probe=dict(id='title', text='标题', font_size=60)
        with self.assertRaises(ValidationError):
            Turn(action='measure', summary='x', probes=[probe, probe])
        with self.assertRaises(ValidationError):
            turn(embedded_text={f'subject_{i}':[] for i in range(25)})
        with self.assertRaises(ValidationError):
            turn(embedded_text={'subject_0':['x'*501]})

    def test_only_exact_top_level_json_mode_marker_is_accepted(self):
        data=turn().model_dump()
        self.assertEqual(Turn.model_validate(dict(data, type='json_object')).code, data['code'])
        for extra in [dict(type='other'), dict(unknown='x')]:
            with self.assertRaises(ValidationError):
                Turn.model_validate(dict(data, **extra))


class RenderedContentTests(unittest.TestCase):
    def test_layout_only_is_opt_in_and_does_not_weaken_content_checks(self):
        result = measured()
        result['frames'] = []
        self.assertTrue(validate_rendered(turn(), result, inventory(), require_animation=False))
        with self.assertRaisesRegex(ValueError, '缺少动画关键帧'):
            validate_rendered(turn(), result, inventory())
        result['texts'][0]['visible'] = False
        with self.assertRaisesRegex(ValueError, '完整海报中'):
            validate_rendered(turn(), result, inventory(), require_animation=False)

    def test_layout_only_keeps_clipping_and_content_guards(self):
        result = measured()
        result['frames'] = []
        result['diagnostics'] = [dict(kind='text_outside_canvas', target='text_0')]
        with self.assertRaisesRegex(ValueError, '排版问题'):
            validate_rendered(turn(), result, inventory(), require_animation=False)
        result['diagnostics'] = []
        with self.assertRaises(ValueError):
            validate_rendered(turn(), result, inventory(('完全不同的文字',)), require_animation=False)

    def test_pure_text_accepts_actual_hold_id_and_staged_visibility(self):
        self.assertTrue(validate_rendered(turn(), measured(), inventory()))

    def test_split_span_content_and_whitespace_preserve_text(self):
        self.assertTrue(validate_rendered(turn(), measured(('研究结果', '49 对患者')), inventory()))
        self.assertTrue(validate_rendered(turn(), measured(('研究结果', '49', '对患者')), inventory()))

    def test_multiple_subjects_accept_exactly_one_bound_variant_each(self):
        result=measured(asset_ids=('subject_0_original', 'subject_1'))
        self.assertTrue(validate_rendered(turn(), result, inventory(subject_count=2)))

    def test_missing_duplicate_and_unknown_subjects_fail(self):
        for ids in [('subject_0',), ('subject_0', 'subject_0_original'),
                    ('subject_0', 'subject_2')]:
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                validate_rendered(turn(), measured(asset_ids=ids), inventory(subject_count=2))

    def test_missing_duplicate_and_changed_text_fail(self):
        for texts in [('研究结果',), ('研究结果', '94对患者'),
                      ('研究结果', '49对患者', '研究结果')]:
            with self.subTest(texts=texts), self.assertRaises(ValueError):
                validate_rendered(turn(), measured(texts), inventory())

    def test_hidden_or_tiny_poster_text_is_not_accepted_as_conservation(self):
        for change in [dict(visible=False), dict(font_size=0), dict(font_size=11.9)]:
            result=measured()
            result['texts'][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_rendered(turn(), result, inventory())

    def test_hidden_asset_and_duplicate_text_id_fail(self):
        result=measured(asset_ids=('subject_0',))
        result['assets'][0]['visible']=False
        with self.assertRaises(ValueError):
            validate_rendered(turn(), result, inventory(subject_count=1))
        result=measured()
        result['texts'][1]['id']=result['texts'][0]['id']
        with self.assertRaises(ValueError):
            validate_rendered(turn(), result, inventory())

    def test_animation_cannot_change_or_add_text(self):
        for change in [dict(text='94对患者'), dict(id='animation_only')]:
            result=measured()
            result['frames'][0]['texts'][1].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_rendered(turn(), result, inventory())

    def test_animation_cannot_switch_to_unreviewed_subject(self):
        result=measured(asset_ids=('subject_0',))
        result['frames'][0]['assets'][0]['id']='subject_0_original'
        with self.assertRaises(ValueError):
            validate_rendered(turn(), result, inventory(subject_count=1))

    def test_hold_must_show_all_content_and_exist(self):
        for target in ['texts', 'assets']:
            result=measured(asset_ids=('subject_0',))
            result['frames'][1][target][0]['visible']=False
            with self.subTest(target=target), self.assertRaises(ValueError):
                validate_rendered(turn(), result, inventory(subject_count=1))
        for frames in [[], measured()['frames'][:1]]:
            result=measured()
            result['frames']=frames
            with self.assertRaises(ValueError):
                validate_rendered(turn(), result, inventory())

    def test_browser_diagnostics_cannot_be_ignored(self):
        for frame in [None, 0]:
            result=measured()
            target=result if frame is None else result['frames'][frame]
            target['diagnostics']=[dict(kind='hidden_text_fragment', target='text_0')]
            with self.subTest(frame=frame), self.assertRaises(ValueError):
                validate_rendered(turn(), result, inventory())

    def test_canvas_geometry_overrules_stale_nonzero_dom_visibility(self):
        result=measured(asset_ids=('subject_0',))
        result['assets'][0].update(box=[2540,300,400,200], anchor='source',
            geometry_visibility=dict(visible=False,state='outside_canvas',visible_box=None),
            source_anchor=dict(expected_canvas_box=[300,300,400,200],actual_canvas_box=[2540,300,400,200]))
        with self.assertRaisesRegex(ValueError, '完全位于画布之外.*anchor="source".*anchor="manual"'):
            validate_rendered(turn(),result,inventory(subject_count=1))

    def test_fully_clipped_asset_reports_ancestor_and_not_just_invisible(self):
        result=measured(asset_ids=('subject_0',))
        result['assets'][0].update(visible=False,geometry_visibility=dict(visible=False,state='clipped',
            visible_box=None,clipped_by=[dict(target='small_panel',box=[0,0,40,40])]))
        with self.assertRaisesRegex(ValueError, 'overflow.*small_panel'):
            validate_rendered(turn(),result,inventory(subject_count=1))

    def test_partial_subject_clip_is_visible_review_evidence_not_a_hard_layout_ban(self):
        result=measured(asset_ids=('subject_0',))
        result['assets'][0]['geometry_visibility']=dict(visible=True,state='partial',visible_fraction=.6)
        result['diagnostics']=[dict(kind='asset_partial_clip',severity='warning',target='subject_0'),
                               dict(kind='source_anchor_offset',severity='warning',target='subject_0')]
        result['frames'][0]['diagnostics']=[dict(kind='asset_transient_clip',severity='warning',target='subject_0')]
        self.assertTrue(validate_rendered(turn(),result,inventory(subject_count=1)))
        for kind in ('hidden_text_fragment','text_clipped','unknown_issue','asset_not_in_view'):
            result['diagnostics']=[dict(kind=kind,severity='warning',target='subject_0')]
            with self.subTest(kind=kind),self.assertRaises(ValueError):
                validate_rendered(turn(),result,inventory(subject_count=1))

    def test_hold_asset_cannot_claim_visible_when_geometry_is_outside(self):
        result=measured(asset_ids=('subject_0',))
        result['frames'][1]['assets'][0]['geometry_visibility']=dict(visible=False,state='outside_canvas')
        with self.assertRaisesRegex(ValueError,'结尾阅读阶段.*完全位于画布之外'):
            validate_rendered(turn(),result,inventory(subject_count=1))

    def test_fully_clipped_text_gives_geometry_reason(self):
        result=measured()
        result['texts'][0]['geometry_visibility']=dict(visible=False,state='clipped',
            clipped_by=[dict(target='text_panel')])
        with self.assertRaisesRegex(ValueError,'Text text_0.*overflow.*text_panel'):
            validate_rendered(turn(),result,inventory())

    def test_embedded_text_must_have_rendered_asset_and_no_code_duplicate(self):
        original=inventory(subject_count=1)
        original['subjects'][0]['embedded_text']=['联合组']
        request=turn(embedded_text={'subject_0':['联合组']})
        self.assertTrue(validate_rendered(request, measured(asset_ids=('subject_0',)), original))
        with self.assertRaises(ValueError):
            validate_rendered(request, measured(), original)
        with self.assertRaises(ValueError):
            validate_rendered(request, measured(('研究结果', '49对患者', '联合组'), ('subject_0',)), original)

    def test_recovered_text_is_visible_and_not_a_license_to_add_invisible_claims(self):
        recovered=[dict(text='严谨核查', source_box=[.1, .7, .6, .8])]
        request=turn(recovered_text=recovered)
        self.assertTrue(validate_rendered(request, measured(('研究结果', '49对患者', '严谨核查')), inventory()))
        with self.assertRaises(ValueError):
            validate_rendered(request, measured(), inventory())


class RuntimeGeometryTests(unittest.TestCase):
    def test_horizontal_and_portrait_canvas_use_xywh_not_ltrb(self):
        for canvas in ([1920, 1080], [1080, 1920]):
            manifest=dict(canvas=canvas, subjects=[dict(asset_id='subject_0',
                source_anchor_box=[.1, .2, .6, .8], prepared_size=[500, 600], anchor_available=True)])
            result=renderer.runtime_geometry(manifest, {'subject_0':'unused', 'subject_0_original':'unused'})
            expected=[canvas[0]*.1, canvas[1]*.2, canvas[0]*.5, canvas[1]*.6]
            for actual, wanted in zip(result['subject_0']['box'], expected):
                self.assertAlmostEqual(actual, wanted)
            self.assertEqual(result['subject_0_original'], result['subject_0'])

    def test_reference_image_without_source_anchor_remains_manual(self):
        manifest=dict(canvas=[1080, 1920], subjects=[dict(asset_id='subject_0',
            source_anchor_box=None, prepared_size=[400, 500], anchor_available=False,
            anchor_unavailable_reason='独立参考图')])
        result=renderer.runtime_geometry(manifest, {'subject_0':'unused'})
        self.assertNotIn('box', result['subject_0'])
        self.assertFalse(result['subject_0']['anchor_available'])
        self.assertNotIn('subject_0_original', result)

    def test_no_subject_pure_text_has_empty_geometry(self):
        self.assertEqual(renderer.runtime_geometry(dict(canvas=[1920,1080], subjects=[]), {}), {})

    def test_canvas_matches_javascript_renderer_limits_before_launch(self):
        common=dict(mode='preview', code='code', duration=10, assets={'background':Path(__file__)},
                    geometry={}, output_dir=Path(__file__).parent)
        with patch.object(Path, 'iterdir', return_value=iter(())), \
                patch.object(renderer.host, '_runtime_paths', side_effect=RuntimeError('runtime reached')):
            for canvas in ([1920, 1080], [1080, 1920], [64, 64]):
                with self.subTest(canvas=canvas), self.assertRaisesRegex(RuntimeError, 'runtime reached'):
                    renderer.run(canvas=canvas, **common)
            for canvas in ([1921,1080], [1920,1081], [1920,1920], [63,64], [2048,1024], [True,1080]):
                with self.subTest(canvas=canvas), self.assertRaisesRegex(ValueError, '画布'):
                    renderer.run(canvas=canvas, **common)


if __name__ == '__main__':
    unittest.main()
