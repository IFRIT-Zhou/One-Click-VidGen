import copy
import unittest

from backend.app.gemini_client import GeminiError
from backend.app.video_agents import plan_groups
from backend.app.video_group_repair import legal_ranges, safe_partitions
from backend.app.video_plan import normalize_shots


def scene(identity, start, end, text=None):
    return dict(slide_id=identity, start=start, end=end, text=text or identity)


def group(ids, *, identity=None, kind='video', intent='原镜头含义'):
    row = dict(slide_ids=ids, kind=kind, intent=intent, motion_basis='表达先后变化',
               progression_plan='先提问再回应' if kind == 'video' else '',
               semantic=dict(message=intent, source_basis='本镜原文', fact_status='quoted',
                             progression='问题到回应', continuity_requirement='同一会场'))
    if identity:
        row['id'] = identity
    return row


class VideoGroupRepairTest(unittest.TestCase):
    def setUp(self):
        self.scenes = [scene('s0', 0, 6), scene('s1', 6, 12),
                       scene('s2', 58.56, 61), scene('s3', 61, 65.28),
                       scene('s4', 65.28, 70), scene('s5', 70, 75.08)]
        self.rows = [group(['s0'], identity='first'), group(['s1'], identity='second'),
                     group(['s2', 's3', 's4', 's5'], identity='too_long')]

    def test_semantic_repair_is_local_and_preserves_other_shots(self):
        original = copy.deepcopy(self.rows)
        calls, drafts = [], []
        def ask(_system, data):
            calls.append(copy.deepcopy(data))
            if len(calls) == 1:
                return {'shots': self.rows}
            self.assertEqual(data['parent_shot']['id'], 'too_long')
            self.assertEqual([s['slide_id'] for s in data['scenes']], ['s2', 's3', 's4', 's5'])
            self.assertNotIn('previous_shots', data)
            return {'shots': [group(['s2', 's3'], intent='前半问题'), group(['s4', 's5'], intent='后半回应')]}
        result = plan_groups({}, self.scenes, {}, ask=ask, on_draft=lambda rows: drafts.append(rows))
        self.assertEqual(len(calls), 2)
        self.assertEqual(result[:2], original[:2])
        self.assertEqual([r['slide_ids'] for r in result[2:]], [['s2', 's3'], ['s4', 's5']])
        self.assertEqual(result[2]['duration_repair']['method'], 'semantic')
        self.assertEqual(result[2]['duration_repair']['original_duration'], 16.52)
        self.assertEqual(result[2]['parent_shot_id'], 'too_long')
        self.assertNotEqual(result[2]['id'], 'too_long')
        self.assertEqual(self.rows, original)
        self.assertEqual(drafts[0], original)
        self.assertEqual(len(drafts), 2)

    def test_invalid_local_answer_cannot_drop_subtitles_or_downgrade(self):
        calls = []
        def ask(_system, data):
            calls.append(copy.deepcopy(data))
            if len(calls) == 1:
                return {'shots': [group(['s2', 's3'], kind='static'), group(['s4', 's5'])]}
            return {'shots': [group(['s2', 's3'])]}
        result = plan_groups({}, self.scenes, {}, ask=ask, resume_rows=self.rows)
        self.assertEqual(len(calls), 2)
        self.assertIn('降为静态', calls[1]['validation_errors'][0])
        self.assertEqual(result[:2], self.rows[:2])
        self.assertEqual([value for row in result for value in row['slide_ids']], [s['slide_id'] for s in self.scenes])
        self.assertTrue(all(row['kind'] == 'video' for row in result))
        self.assertTrue(all(row['duration_repair']['method'] == 'boundary_fallback' for row in result[2:]))
        self.assertTrue(all('原镜头含义' not in row['intent'] for row in result[2:]))

    def test_model_outage_uses_boundary_fallback_with_finite_calls(self):
        calls = []
        def ask(*_args):
            calls.append(1)
            raise GeminiError('模型暂时不可用')
        result = plan_groups({}, self.scenes, {}, ask=ask, resume_rows=self.rows)
        self.assertEqual(len(calls), 2)
        self.assertEqual(result[-1]['duration_repair']['method'], 'boundary_fallback')
        self.assertTrue(result[-1]['duration_repair']['note'])

    def test_cancellation_and_callback_failure_are_not_swallowed(self):
        class PlanningStopped(RuntimeError):
            pass
        def stop(*_args):
            raise PlanningStopped('用户停止')
        with self.assertRaises(PlanningStopped):
            plan_groups({}, self.scenes, {}, ask=stop, resume_rows=self.rows)
        with self.assertRaises(PlanningStopped):
            plan_groups({}, self.scenes, {}, ask=lambda *_: self.fail('应先执行停止检查'),
                        resume_rows=self.rows, progress=stop)
        with self.assertRaisesRegex(ValueError, '保存失败'):
            plan_groups({}, self.scenes, {}, ask=lambda *_: self.fail('保存失败应直接中止'),
                        resume_rows=self.rows, on_draft=lambda _: (_ for _ in ()).throw(ValueError('保存失败')))

    def test_single_long_subtitle_is_static_without_model_call(self):
        scenes = [scene('s0', 0, 16.52)]
        result = plan_groups({}, scenes, {}, resume_rows=[group(['s0'], identity='long')],
                             ask=lambda *_: self.fail('单条不可拆无需调用 Agent'))
        self.assertEqual(result[0]['kind'], 'static')
        self.assertEqual(result[0]['duration_repair']['method'], 'single_subtitle_static')
        again = plan_groups({}, scenes, {}, resume_rows=result, ask=lambda *_: self.fail('恢复不得重复处理'))
        self.assertEqual(result, again)

    def test_single_overlong_inside_parent_isolated_from_short_neighbors(self):
        scenes = [scene('s0', 0, 5), scene('s1', 5, 21), scene('s2', 21, 26)]
        result = plan_groups({}, scenes, {}, resume_rows=[group(['s0', 's1', 's2'], identity='long')],
                             ask=lambda *_: {'shots': []})
        self.assertEqual([r['slide_ids'] for r in result], [['s0'], ['s1'], ['s2']])
        self.assertEqual([r['kind'] for r in result], ['video', 'static', 'video'])
        self.assertEqual(result[1]['duration_repair']['method'], 'single_subtitle_static')

    def test_static_metadata_repair_keeps_later_static_shot_static(self):
        rows = copy.deepcopy(self.rows)
        rows[2] = group(['s2', 's3', 's4', 's5'], kind='static', identity='static')
        rows[2]['motion_basis'] = ''
        result = plan_groups({}, self.scenes, {}, resume_rows=rows,
                             ask=lambda *_: self.fail('静态缺省信息可由自身字幕补全'))
        self.assertEqual(result[-1]['kind'], 'static')
        self.assertEqual(result[-1]['slide_ids'], rows[-1]['slide_ids'])

    def test_resume_completed_repair_does_not_repeat_agents(self):
        rows = [group(['s0'], identity='first'), group(['s1'], identity='second'),
                group(['s2', 's3'], identity='part1'), group(['s4', 's5'], identity='part2')]
        result = plan_groups({}, self.scenes, {}, resume_rows=rows,
                             ask=lambda *_: self.fail('已完成分组不应再次调用 Agent'))
        self.assertEqual(result, rows)

    def test_structural_retry_is_once_and_precedes_local_work(self):
        calls = []
        good = [group(['s0'], identity='first')]
        def ask(_system, data):
            calls.append(data)
            return {'shots': [] if len(calls) == 1 else good}
        result = plan_groups({}, self.scenes[:1], {}, ask=ask)
        self.assertEqual(result, good)
        self.assertEqual(len(calls), 2)
        with self.assertRaisesRegex(ValueError, '字幕覆盖修订'):
            plan_groups({}, self.scenes, {}, ask=lambda *_: {'shots': []})

    def test_boundary_partition_prefers_balanced_non_tiny_children(self):
        scenes = [scene('s0', 0, 6), scene('s1', 6, 12), scene('s2', 12, 16.52)]
        parts = safe_partitions(scenes)
        self.assertEqual([[s['slide_id'] for s in p] for p in parts], [['s0'], ['s1', 's2']])
        # Pauses are part of the span: 2+2 spoken seconds still span sixteen seconds.
        spaced = [scene('a', 0, 2), scene('b', 14, 16)]
        self.assertEqual(len(safe_partitions(spaced)), 2)
        self.assertFalse(any(r['first_slide_id'] == 'a' and r['last_slide_id'] == 'b' for r in legal_ranges(spaced)))

    def test_large_candidate_table_is_linear_and_bounds_are_valid(self):
        scenes = [scene(f's{i}', i * .1, (i + 1) * .1) for i in range(1000)]
        ranges = legal_ranges(scenes)
        self.assertEqual(len(ranges), 1000)
        self.assertTrue(all(r['duration'] <= 15 for r in ranges))
        self.assertTrue(all(r['range_mode'] == 'any_end_up_to_last' for r in ranges))

    def test_fallback_keeps_colon_introduction_with_its_following_content(self):
        times = [58.56, 61.4, 65.28, 68.12, 69.6, 72.12, 75.08]
        texts = ['如果每天都跟你说：', '别人深受欺骗，以至于不敢旅行。',
                 '那么有思考能力的人就会开始想：', '我不敢旅行，',
                 '好像也可能是因为，', '我同样受到了媒体影响。']
        scenes = [scene(f's{i}', times[i], times[i + 1], text) for i, text in enumerate(texts)]
        parts = safe_partitions(scenes)
        self.assertEqual([[s['slide_id'] for s in part] for part in parts],
                         [['s0', 's1'], ['s2', 's3', 's4', 's5']])

    def test_repair_metadata_survives_normalization_round_trip(self):
        for semantic in (True, False):
            with self.subTest(semantic=semantic):
                answer = {'shots': [group(['s2', 's3']), group(['s4', 's5'])]} if semantic else {'shots': []}
                rows = plan_groups({}, self.scenes, {}, resume_rows=self.rows, ask=lambda *_: answer)
                normalized = normalize_shots(rows, self.scenes)
                again = normalize_shots(normalized, self.scenes)
                self.assertEqual(normalized, again)
                for original, saved in zip(rows[2:], again[2:]):
                    self.assertEqual(saved['duration_repair'], original['duration_repair'])
                    self.assertTrue(saved['duration_repair']['note'])
                    self.assertEqual(saved['parent_shot_id'], 'too_long')
                    self.assertLessEqual(saved['duration'], 15)
        scenes = [scene('single', 0, 20)]
        rows = plan_groups({}, scenes, {}, resume_rows=[group(['single'])],
                           ask=lambda *_: self.fail('单条超限不调用模型'))
        saved = normalize_shots(rows, scenes)[0]
        self.assertEqual(saved['duration_repair']['method'], 'single_subtitle_static')
        self.assertEqual(saved['duration_repair']['note'], rows[0]['duration_repair']['note'])


if __name__ == '__main__':
    unittest.main()
