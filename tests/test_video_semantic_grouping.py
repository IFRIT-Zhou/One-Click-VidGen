import copy
import unittest
from unittest.mock import Mock

from backend.app.video_agents import plan_groups
from backend.app.video_boundary_review import review_boundaries
from backend.app.video_group_repair import repair_groups, validate_group_structure
from backend.app.video_plan import normalize_shots


def scene_list(texts, lengths=None):
    rows, start = [], 0.0
    for i, text in enumerate(texts):
        end = start + (lengths[i] if lengths else 3)
        rows.append(dict(slide_id=f's{i}', start=start, end=end, text=text))
        start = end
    return rows


def shot(identity, ids, kind='video'):
    return dict(id=identity, slide_ids=ids, kind=kind, intent='表达本镜原文关系',
                motion_basis='问答与补充在同一语义链中', progression_plan='先提问再回应',
                semantic=dict(message='原意', source_basis='原文', fact_status='quoted',
                              progression='先后关系', continuity_requirement='同一场景'))


class SemanticGroupingTests(unittest.TestCase):
    def test_long_mode_preserves_twenty_second_group(self):
        scenes = scene_list(['前半过程', '后半过程'], [10, 10])
        rows = [shot('a', ['s0', 's1'])]
        ask = Mock(return_value={'shots': rows})
        result = plan_groups({}, scenes, {'dynamic_max_shot_duration': 30}, ask)
        self.assertEqual(len(result), 1)
        self.assertEqual(ask.call_count, 1)
        self.assertEqual(ask.call_args.args[1]['arrangement']['max_video_duration'], 30)
        self.assertIn('30秒', ask.call_args.args[0])

    def test_long_mode_legal_ranges_and_fallback_use_thirty_seconds(self):
        from backend.app.video_group_repair import legal_ranges, safe_partitions
        scenes = scene_list(['甲', '乙', '丙'], [10, 10, 10])
        self.assertTrue(any(row['duration'] == 30 for row in legal_ranges(scenes, 30)))
        self.assertFalse(any(row['duration'] > 15 for row in legal_ranges(scenes)))
        self.assertEqual(len(safe_partitions(scenes, 30)), 1)
        self.assertEqual(len(safe_partitions(scenes)), 3)

    def test_cross_narration_is_not_rejected_or_forced_split(self):
        scenes = scene_list(['问题', '回答', '补充', '新话题'])
        rows = [shot('a', ['s0', 's1', 's2']), shot('b', ['s3'])]
        ask = Mock(return_value={'shots': rows})
        result = plan_groups({}, scenes, {'narration_groups': [{'slide_ids': ['s0', 's1']}, {'slide_ids': ['s2', 's3']}]}, ask)
        self.assertEqual([r['slide_ids'] for r in result], [r['slide_ids'] for r in rows])
        self.assertEqual(ask.call_count, 1)

    def test_short_narration_can_have_a_semantic_cut(self):
        scenes = scene_list(['第一话题', '另一个独立过程'])
        rows = [shot('a', ['s0']), shot('b', ['s1'])]
        result = plan_groups({}, scenes, {'narration_groups': [{'slide_ids': ['s0', 's1']}]}, Mock(return_value={'shots': rows}))
        self.assertEqual(len(result), 2)

    def test_real_coverage_error_still_fails(self):
        scenes = scene_list(['甲', '乙'])
        with self.assertRaisesRegex(ValueError, '覆盖'):
            plan_groups({}, scenes, {}, Mock(return_value={'shots': [shot('a', ['s0'])]}))

    def test_cross_narration_move_follows_source_meaning(self):
        scenes = scene_list(['日韩旅游', '汉奸、不爱国', '将军的炮火已经瞄准南边', '出国不如逛祖国'])
        original = copy.deepcopy(scenes)
        rows = [shot('a', ['s0', 's1']), shot('b', ['s2', 's3'])]
        move = dict(left_id='a', right_id='b', after_slide_id='s2',
                    reason='炮火是对前一日韩案例的补充，不是下一结论', evidence_slide_ids=['s1', 's2'],
                    left=shot('x', []), right=shot('y', []))
        result = review_boundaries({}, scenes, rows, Mock(return_value={'moves': [move]}),
                                   narration_groups=[{'slide_ids': ['s0', 's1']}, {'slide_ids': ['s2', 's3']}])
        self.assertEqual([r['slide_ids'] for r in result], [['s0', 's1', 's2'], ['s3']])
        self.assertEqual(scenes, original)
        self.assertEqual(rows[0]['slide_ids'], ['s0', 's1'])

    def test_merge_tiny_response_preserves_other_shot(self):
        scenes = scene_list(['提出问题', '没错', '下面另讲一个例子'], [6, 1, 7])
        rows = [shot('a', ['s0']), shot('b', ['s1']), shot('c', ['s2'])]
        proposal = dict(left_id='a', right_id='b', reason='回应依附前问', evidence_slide_ids=['s0', 's1'], shots=[shot('new', ['s0', 's1'])])
        result = review_boundaries({}, scenes, rows, Mock(return_value={'repartitions': [proposal]}))
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['slide_ids'], ['s0', 's1'])
        self.assertEqual(result[1], rows[2])

    def test_overlong_merge_rejected_without_corrupting_plan(self):
        scenes = scene_list(['前段', '后段'], [9, 9])
        rows = [shot('a', ['s0']), shot('b', ['s1'])]
        proposal = dict(left_id='a', right_id='b', reason='测试', evidence_slide_ids=['s0', 's1'], shots=[shot('new', ['s0', 's1'])])
        result = review_boundaries({}, scenes, rows, Mock(return_value={'repartitions': [proposal]}))
        self.assertEqual([r['slide_ids'] for r in result], [['s0'], ['s1']])

    def test_merge_requires_evidence_on_both_sides(self):
        scenes = scene_list(['前段', '后段'])
        rows = [shot('a', ['s0']), shot('b', ['s1'])]
        proposal = dict(left_id='a', right_id='b', reason='凑时长', evidence_slide_ids=['s0'], shots=[shot('new', ['s0', 's1'])])
        self.assertEqual(len(review_boundaries({}, scenes, rows, Mock(return_value={'repartitions': [proposal]}))), 2)

    def test_locked_boundary_is_not_sent_for_review(self):
        scenes = scene_list(['甲', '乙'])
        rows = [dict(shot('a', ['s0']), boundary_locked=True), shot('b', ['s1'])]
        ask = Mock()
        self.assertEqual(review_boundaries({}, scenes, rows, ask), rows)
        ask.assert_not_called()

    def test_duration_agent_reads_whole_text_and_can_rebalance_neighbors(self):
        scenes = scene_list(['前补充', '开始', '过程', '回答', '尾补充', '后接句', '窗口外'], [4]*7)
        rows = [shot('a', ['s0']), shot('long', ['s1', 's2', 's3', 's4']), shot('b', ['s5']), shot('keep', ['s6'])]
        ask = Mock(return_value={'shots': [shot('x', ['s0', 's1', 's2']), shot('y', ['s3', 's4', 's5'])]})
        result = repair_groups({}, scenes, rows, ask)
        self.assertEqual([r['slide_ids'] for r in result], [['s0', 's1', 's2'], ['s3', 's4', 's5'], ['s6']])
        self.assertEqual(result[-1], rows[-1])
        payload = ask.call_args.args[1]
        self.assertIn('窗口外', payload['source_text'])
        self.assertEqual(len(payload['editable_shots']), 3)
        self.assertEqual(payload['neighbors']['following']['subtitles'], [scenes[-1]])
        validate_group_structure(result, scenes)

    def test_failed_duration_revision_does_not_change_neighbors(self):
        scenes = scene_list(['前段', '甲', '乙', '丙', '丁', '后段'], [4]*6)
        rows = [shot('a', ['s0']), shot('long', ['s1', 's2', 's3', 's4']), shot('b', ['s5'])]
        result = repair_groups({}, scenes, rows, Mock(return_value={'shots': []}))
        self.assertEqual(result[0], rows[0])
        self.assertEqual(result[-1], rows[-1])
        validate_group_structure(result, scenes)

    def test_single_overlong_subtitle_does_not_invent_audio_cut(self):
        scenes = scene_list(['单条超长配音字幕'], [20])
        ask = Mock()
        result = repair_groups({}, scenes, [shot('a', ['s0'])], ask)
        self.assertEqual(result[0]['kind'], 'static')
        self.assertEqual(result[0]['slide_ids'], ['s0'])
        ask.assert_not_called()

    def test_review_failure_leaves_valid_plan(self):
        scenes = scene_list(['甲', '乙'])
        rows = [shot('a', ['s0']), shot('b', ['s1'])]
        result = review_boundaries({}, scenes, rows, Mock(side_effect=ValueError('invalid')))
        self.assertEqual([r['slide_ids'] for r in result], [['s0'], ['s1']])

    def test_duration_window_respects_locks(self):
        scenes = scene_list(['前段', '甲', '乙', '丙', '丁', '后段'], [4]*6)
        for previous_flags, parent_flags, following_flags in (
                ({'boundary_locked': True}, {'boundary_locked': True}, {}),
                ({'manual_locked': True}, {}, {'manual_locked': True})):
            with self.subTest(previous_flags=previous_flags):
                rows = [dict(shot('a', ['s0']), **previous_flags),
                        dict(shot('long', ['s1', 's2', 's3', 's4']), **parent_flags),
                        dict(shot('b', ['s5']), **following_flags)]
                ask = Mock(return_value={'shots': [shot('x', ['s1', 's2']), shot('y', ['s3', 's4'])]})
                result = repair_groups({}, scenes, rows, ask)
                self.assertEqual([r['id'] for r in ask.call_args.args[1]['editable_shots']], ['long'])
                self.assertEqual(result[0], rows[0])
                self.assertEqual(result[-1], rows[-1])
                self.assertEqual(bool(result[-2].get('boundary_locked')), bool(parent_flags.get('boundary_locked')))

    def test_repartition_preserves_outer_lock_and_uses_real_timestamps(self):
        scenes = scene_list(['问', '答', '其他'], [6, 1, 7])
        rows = [shot('a', ['s0']), dict(shot('b', ['s1']), boundary_locked=True), shot('c', ['s2'])]
        proposed = dict(shot('new', ['s0', 's1']), start=99, end=100,
                        video='invented.mp4', image_prompt='stale', manual_locked=True)
        response = {'repartitions': [dict(left_id='a', right_id='b', reason='答依附问',
                                        evidence_slide_ids=['s0', 's1'], shots=[proposed])]}
        result = review_boundaries({}, scenes, rows, Mock(return_value=response))
        self.assertTrue(result[0]['boundary_locked'])
        self.assertNotIn('video', result[0])
        self.assertNotIn('image_prompt', result[0])
        self.assertNotIn('manual_locked', result[0])
        normalized = normalize_shots(result, scenes)
        self.assertEqual([(r['start'], r['end']) for r in normalized], [(0, 7), (7, 14)])
        self.assertEqual(normalized[0]['source_subtitles'], scenes[:2])

    def test_noop_repartition_does_not_replace_ids(self):
        scenes = scene_list(['甲', '乙'])
        rows = [shot('a', ['s0']), shot('b', ['s1'])]
        response = {'repartitions': [dict(left_id='a', right_id='b', reason='原样返回',
                                        evidence_slide_ids=['s0', 's1'], shots=rows)]}
        result = review_boundaries({}, scenes, rows, Mock(return_value=response))
        self.assertEqual([r['id'] for r in result], ['a', 'b'])

    def test_merges_do_not_skip_later_review_batches(self):
        scenes = scene_list([f'句{i}' for i in range(10)], [1]*10)
        rows = [shot(f'a{i}', [f's{i}']) for i in range(10)]
        def response(_system, data):
            entry = data['boundaries'][0]
            ids = entry['left_slide_ids'] + entry['right_slide_ids']
            return {'repartitions': [dict(left_id=entry['left_id'], right_id=entry['right_id'],
                                         reason='相邻问答', evidence_slide_ids=ids,
                                         shots=[shot('new', ids)])]}
        ask = Mock(side_effect=response)
        result = review_boundaries({}, scenes, rows, ask)
        self.assertEqual(ask.call_count, 2)
        self.assertEqual([r['slide_ids'] for r in result], [
            ['s0', 's1'], ['s2'], ['s3'], ['s4'], ['s5'], ['s6', 's7'], ['s8'], ['s9']])
        validate_group_structure(result, scenes)
