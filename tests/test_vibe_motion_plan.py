"""Semantic plans never accept arbitrary code/paths or lose the subtitle clock."""
import copy
import unittest

from pydantic import ValidationError

from backend.app.vibe_motion_plan import MotionPlan, compile_motion_plan


class MotionPlanTests(unittest.TestCase):
    def plan(self):
        return dict(title='研究结果', canvas=[1920, 1080], theme='clinical', stages=[
            dict(id='input', layout='findings', cue='s1', headline='治疗前信息',
                 items=[dict(id='a', title='临床指标'), dict(id='b', title='影像指标')]),
            dict(id='findings', layout='comparison', cue='s2', headline='两个独立相关因素',
                 footer='统计关联，不代表因果', items=[
                     dict(id='afp', title='AFP', value='≥400', unit='ng/mL', cue='s2'),
                     dict(id='shape', title='初始肿瘤形态', body='不规则', cue='s3')])])

    def compile(self, raw=None):
        return compile_motion_plan(MotionPlan.model_validate(raw or self.plan()),
                                   {'s1': 0, 's2': 4.48, 's3': 8.08}, 18.04, {'core'})

    def test_uses_final_subtitle_cues_and_ends_exactly_at_audio_end(self):
        result = self.compile()
        self.assertEqual(result['version'], 'ocv-motion-v1')
        self.assertEqual(result['stages'][0]['start'], 0)
        self.assertEqual(result['stages'][0]['end'], 4.48)
        self.assertEqual(result['stages'][1]['end'], 18.04)
        self.assertEqual(result['stages'][1]['items'][1]['at'], 3.6)
        self.assertNotIn('cue', result['stages'][1]['items'][1])
        self.assertNotIn('cue', result['stages'][0])

    def test_invalid_timing_never_silently_clamps_or_reorders(self):
        for change in ('missing', 'late-first', 'unordered', 'too-short', 'late-item', 'negative-item'):
            raw = self.plan()
            if change == 'missing': raw['stages'][1]['cue'] = 'not-a-subtitle'
            if change == 'late-first': raw['stages'][0]['at'] = 2
            if change == 'unordered': raw['stages'].reverse()
            if change == 'too-short': raw['stages'][1].update(cue=None, at=.3)
            if change == 'late-item': raw['stages'][0]['items'][0].update(at=4.4)
            if change == 'negative-item': raw['stages'][1]['items'][0].update(cue='s1')
            with self.subTest(change=change), self.assertRaises(ValueError): self.compile(raw)

    def test_stage_cue_plus_absolute_time_error_reports_actual_calculation(self):
        raw=self.plan();raw['stages'][1].update(cue='s3',at=8.08)
        before=copy.deepcopy(raw)
        with self.assertRaises(ValueError) as raised:
            compile_motion_plan(MotionPlan.model_validate(raw),{'s1':0,'s2':4.48,'s3':8.08},12,{'core'})
        for detail in ('findings','8.080 + at偏移 8.080 = 16.160','12.000','通常省略at'):
            self.assertIn(detail,str(raised.exception))
        self.assertEqual(raw,before)

    def test_stage_order_error_includes_anchor_and_resolved_times(self):
        raw=self.plan()
        raw['stages'].append(dict(id='late',layout='findings',cue='s1',at=3,
                                 headline='第三阶段',items=[dict(id='a',title='内容')]))
        with self.assertRaises(ValueError) as raised:self.compile(raw)
        for detail in ('findings=4.480','late 的cue起点 0.000 + at偏移 3.000 = 3.000','不能重复'):
            self.assertIn(detail,str(raised.exception))

    def test_evidence_must_bind_an_existing_asset(self):
        raw = self.plan(); raw['stages'][0]['layout'] = 'evidence'
        with self.assertRaises(ValidationError): self.compile(raw)
        raw['stages'][0]['items'][0]['asset_id'] = 'core'
        self.assertEqual(self.compile(raw)['stages'][0]['items'][0]['asset_id'], 'core')
        raw['stages'][0]['items'][0]['asset_id'] = 'unbound'
        with self.assertRaisesRegex(ValueError, '不存在的素材'): self.compile(raw)

    def test_wire_format_cannot_inject_code_urls_or_pixel_positions(self):
        for field, value in [('html', '<script>bad()</script>'), ('box', [1, 2, 3, 4]),
                             ('src', 'https://example.invalid/private'), ('style', {'position': 'fixed'})]:
            raw = self.plan(); raw['stages'][0]['items'][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValidationError): self.compile(raw)
        raw = self.plan(); raw['stages'][0]['items'][0]['asset_id'] = 'C:/private.png'
        with self.assertRaises(ValidationError): self.compile(raw)

    def test_primary_visual_binds_original_asset_and_satisfies_evidence(self):
        raw = self.plan()
        raw['stages'][0].update(layout='evidence', visual={'asset_id': 'core', 'caption': '原始素材'})
        before = copy.deepcopy(raw)
        visual = self.compile(raw)['stages'][0]['visual']
        self.assertEqual(visual, dict(asset_id='core', caption='原始素材', placement='left', treatment='contain'))
        self.assertEqual(raw, before)
        raw['stages'][0]['visual']['asset_id'] = 'unknown'
        with self.assertRaisesRegex(ValueError, '阶段 input 主体引用了不存在的素材 unknown'):
            self.compile(raw)

    def test_primary_visual_is_not_an_arbitrary_path_or_style(self):
        for visual in ({'asset_id': 'https://example.invalid/image.png'},
                       {'asset_id': 'core', 'src': 'C:/private.png'},
                       {'asset_id': 'core', 'placement': 'absolute'},
                       {'asset_id': 'core', 'style': {'position': 'fixed'}}):
            raw = self.plan(); raw['stages'][0]['visual'] = visual
            with self.subTest(visual=visual), self.assertRaises(ValidationError): self.compile(raw)

    def test_primary_visual_at_start_allows_later_annotation_not_late_exit(self):
        raw = self.plan()
        raw['stages'][0]['visual'] = {'asset_id': 'core', 'treatment': 'cutout'}
        raw['stages'][0]['items'] = [dict(id='annotation', title='随后解释', at=1.2)]
        self.assertEqual(self.compile(raw)['stages'][0]['items'][0]['at'], 1.2)
        raw['stages'][0]['items'][0]['at'] = 3
        with self.assertRaisesRegex(ValueError, '完整阅读'): self.compile(raw)

    def test_source_inspired_palette_is_preserved_and_old_plans_stay_valid(self):
        raw = self.plan()
        self.assertNotIn('palette', self.compile(raw))
        raw['accent'] = '#123456'
        raw['palette'] = dict(background='#071C2B', ink='#F5FBFF', muted='#A8C9DB',
                              surface='#102F43', line='#347391', accent='#EDAC49')
        self.assertEqual(self.compile(raw)['palette'], raw['palette'])
        raw['palette'] = {'accent': '#EDAC49'}
        self.assertEqual(self.compile(raw)['palette'], raw['palette'])

    def test_unreadable_or_executable_palette_rejected(self):
        for palette in ({'ink': '#F2F7F8'}, {'muted': '#F2F7F8'},
                        {'background': '#102F43'}, {'background': 'url(file:///secret)'},
                        {'font': 'serif'}, {'accent': '#fff'}):
            raw = self.plan(); raw['palette'] = palette
            with self.subTest(palette=palette), self.assertRaises(ValidationError): self.compile(raw)

    def test_unique_ids_and_finite_times(self):
        raw = self.plan(); raw['stages'][1]['id'] = raw['stages'][0]['id']
        with self.assertRaises(ValidationError): self.compile(raw)
        raw = self.plan(); raw['stages'][0]['at'] = float('nan')
        with self.assertRaises(ValidationError): self.compile(raw)

    def test_original_model_plan_is_not_mutated_by_compilation(self):
        plan = MotionPlan.model_validate(self.plan()); before = copy.deepcopy(plan.model_dump())
        compile_motion_plan(plan, {'s1': 0, 's2': 4.48, 's3': 8.08}, 18.04, {'core'})
        self.assertEqual(plan.model_dump(), before)

    def test_late_item_error_reports_exact_timeline_and_legal_range(self):
        raw = self.plan()
        raw['stages'][1]['at'] = 1
        raw['stages'][0]['items'][1].update(cue='s2', at=.5)
        with self.assertRaises(ValueError) as raised: self.compile(raw)
        message = str(raised.exception)
        for fact in ('0.000–5.480', '条目 b', '4.980', '0.500', '0.000–3.330',
                     '入场 0.850', '阅读 1.000', '退出 0.300', '不得修改原字幕时间轴'):
            self.assertIn(fact, message)

    def test_first_meaningful_item_cannot_wait_for_whole_narration(self):
        raw = self.plan()
        raw['stages'][0]['items'] = [dict(id='late', title='临床指标', at=1.2)]
        before = copy.deepcopy(raw)
        with self.assertRaises(ValueError) as raised: self.compile(raw)
        message = str(raised.exception)
        for fact in ('首个有意义主体', '条目 late', '空等 1.200', '0–0.500',
                     '默认错峰', '不要等整句口播说完', '不得修改原字幕时间轴'):
            self.assertIn(fact, message)
        self.assertEqual(raw, before)

    def test_first_meaningful_item_is_earliest_not_first_in_array(self):
        raw = self.plan()
        raw['stages'][0]['items'][0]['at'] = .8
        raw['stages'][0]['items'][1]['at'] = .5
        result = self.compile(raw)
        self.assertEqual([item['at'] for item in result['stages'][0]['items']], [.8, .5])
        raw['stages'][0]['items'][1]['at'] = .51
        with self.assertRaisesRegex(ValueError, '首个有意义主体'): self.compile(raw)

    def test_last_item_requires_complete_entrance_reading_and_exit(self):
        raw = self.plan()
        # Stage length 4.48, reserve .85 + 1 + .3, latest entry 2.33.
        raw['stages'][0]['items'][1]['at'] = 2.33
        self.assertEqual(self.compile(raw)['stages'][0]['items'][1]['at'], 2.33)
        raw['stages'][0]['items'][1]['at'] = 2.34
        with self.assertRaisesRegex(ValueError, '完整阅读 1.000'): self.compile(raw)

    def test_short_single_shots_get_proportional_default_stagger(self):
        for duration in (.1, .5, 1., 1.2):
            raw = dict(title='简短信息', stages=[dict(id='short', layout='findings',
                headline='简短信息', items=[dict(id=str(i), title=f'信息{i}') for i in range(4)])])
            model = MotionPlan.model_validate(raw)
            before = copy.deepcopy(model.model_dump())
            result = compile_motion_plan(model, {}, duration, set())
            stage = result['stages'][0]
            offsets = [item['at'] for item in stage['items']]
            latest = duration-min(.85, duration*.35)-min(1, duration*.4)-min(.3, duration*.1)
            with self.subTest(duration=duration):
                self.assertEqual(stage['start'], 0)
                self.assertEqual(stage['end'], duration)
                self.assertEqual(offsets[0], 0)
                self.assertLessEqual(offsets[-1], latest+1e-6)
                self.assertEqual(offsets, sorted(offsets))
                self.assertEqual(model.model_dump(), before)

    def test_short_shot_explicit_late_cue_is_not_clamped(self):
        raw = dict(title='简短信息', stages=[dict(id='short', layout='findings',
            headline='信息', items=[dict(id='late', title='信息', cue='saved', at=.01)])])
        with self.assertRaises(ValueError) as raised:
            compile_motion_plan(MotionPlan.model_validate(raw), {'saved': .09}, .5, set())
        self.assertIn('在本镜 0.100 秒入场', str(raised.exception))
        self.assertIn('0.000–0.075', str(raised.exception))


if __name__ == '__main__': unittest.main()
