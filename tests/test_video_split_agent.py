import copy
import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch
from backend.app import video_agents as agents
from backend.app.video_duration_limits import max_video_duration, validate_video_duration
from backend.app.video_auto_split import plan_split, split_limit, split_window
from backend.app.video_split_agent import plan_split_groups, split_direction
from test_video_auto_split import fixture, planners
from test_video_duration_limits import group, scenes_for


class SplitAgentTests(unittest.TestCase):
    def test_preference_and_execution_are_separate(self):
        parameters = dict(video_generation_backend='cloud_h3', dynamic_max_shot_duration=30,
                          dynamic_cluster_duration_recommended=False)
        self.assertEqual(max_video_duration(parameters), 30)
        self.assertEqual(validate_video_duration(22, parameters, planning=True), 22)
        with self.assertRaisesRegex(ValueError, '14 秒'):
            validate_video_duration(22, parameters)
        self.assertEqual(validate_video_duration(40, {**parameters, 'video_generation_backend': 'api'}), 40)
        self.assertEqual(validate_video_duration(40, {'video_generation_backend': 'comfyui'}), 40)
        self.assertEqual(max_video_duration({**parameters, 'dynamic_cluster_duration_recommended': True}), 14)
        self.assertEqual(parameters['dynamic_max_shot_duration'], 30)

    def test_routine_planning_keeps_1b_and_long_semantically_continuous_shot(self):
        scenes = scenes_for([10, 10])
        rows = [group('same_fact', ['s0', 's1'])]
        parameters = dict(video_generation_backend='cloud_h3', dynamic_max_shot_duration=28,
                          dynamic_cluster_duration_recommended=False)
        ask = Mock(return_value={'shots': rows})
        with patch('backend.app.video_split_agent.plan_split_groups') as split:
            result = agents.plan_groups({}, scenes, parameters, ask=ask)
        split.assert_not_called()
        self.assertEqual([row['slide_ids'] for row in result], [['s0', 's1']])
        self.assertEqual(ask.call_args.args[1]['arrangement']['max_video_duration'], 28)
        self.assertEqual(split_direction({}, result), '')

    def response(self):
        rows = [group('ignored_a', ['s1']), group('ignored_b', ['s2'])]
        rows[0]['split_assignment'] = dict(role='core', focus='阶段1核心事实', inherit='父镜主体与核心构图',
            difference='承担核心过程，另一镜交代阶段2补充证据', continuity='保持同一主体与场景')
        rows[1]['split_assignment'] = dict(role='support', focus='阶段2补充证据', inherit='同一主体与场景',
            difference='聚焦阶段2的局部证据，不重演阶段1', continuity='延续核心镜的主体状态')
        return {'shots': rows}

    def test_1c_core_inheritance_sibling_division_and_identity(self):
        record = fixture()
        before = copy.deepcopy(record)
        with ExitStack() as stack:
            core = planners(stack)
            ask = stack.enter_context(patch.object(agents, 'ask_json', return_value=self.response()))
            split = stack.enter_context(patch('backend.app.video_group_repair.repair_groups', side_effect=AssertionError('1C must not call 1B')))
            rows = plan_split(record, 'target')
        split.assert_not_called()
        self.assertEqual(ask.call_count, 1)
        self.assertIn('Agent 1C', ask.call_args.args[0])
        self.assertEqual([r['split_assignment']['role'] for r in rows], ['core', 'support'])
        self.assertEqual(rows[0]['split_assignment']['source_parent_ids'], ['target'])
        context = core.call_args.args[0]
        self.assertEqual(context['local_split_plan']['parents'][0]['visual_description'], '旧构图')
        self.assertNotIn('image', context['local_split_plan']['parents'][0])
        self.assertEqual([a['id'] for a in context['local_split_plan']['assignments']], [r['id'] for r in rows])
        self.assertTrue(all(r['duration_repair']['planner'] == '1C' for r in rows))
        self.assertIn('core 子镜可以继承', split_direction(context, rows))
        self.assertEqual(record, before)

    def test_bad_or_duplicate_division_gets_one_repair_then_honest_fallback(self):
        record = fixture()
        invalid = self.response()
        invalid['shots'][1]['split_assignment'] = copy.deepcopy(invalid['shots'][0]['split_assignment'])
        ask = Mock(return_value=invalid)
        scenes = record['scenes'][1:3]
        rows, _ = plan_split_groups({'final_narration_script':'最终配音'}, scenes, record['shots'][1:2], ask, 14)
        self.assertEqual(ask.call_count, 2)
        self.assertTrue(all(r['duration_repair']['method']=='boundary_fallback' for r in rows))
        self.assertNotEqual(rows[0]['split_assignment']['focus'], rows[1]['split_assignment']['focus'])
        self.assertEqual([sid for r in rows for sid in r['slide_ids']], ['s1', 's2'])

    def test_noncluster_uses_custom_split_target_not_fourteen(self):
        record = fixture()
        record['creation_parameters'].update(video_generation_backend='api', dynamic_max_shot_duration=20)
        for row in record['shots']:
            row['video_generation_options'] = {'backend':'api'}
        self.assertEqual(split_limit(record, 'target'), 20)
        with self.assertRaisesRegex(ValueError, '未超过20秒'):
            split_window(record, 'target')
        record['scenes'][2]['end'] += 8
        record['shots'][1]['duration'] += 8
        for scene in record['scenes'][3:]:
            scene['start'] += 8
            scene['end'] += 8
        with ExitStack() as stack:
            planners(stack)
            rows = plan_split(record, 'target')
        self.assertTrue(any(r['generation_duration'] > 14 for r in rows))
        self.assertTrue(all(r['generation_duration'] <= 20 for r in rows))
        self.assertTrue(all(r['video_generation_options']['backend']=='api' for r in rows))

    def test_neighbors_use_strictest_saved_route_and_final_subtitles(self):
        record = fixture()
        record['creation_parameters'].update(dynamic_max_shot_duration=30, video_generation_backend='api')
        record['shots'][1]['video_generation_options'] = {'backend':'api'}
        self.assertEqual(split_limit(record, 'target'), 30)
        self.assertEqual(split_limit(record, 'target', True), 14)
        record['context']['final_narration_script'] = '过时文本'
        record['scenes'][2]['text'] = '最终编辑后的配音文案。'
        with ExitStack() as stack:
            planners(stack)
            ask=stack.enter_context(patch.object(agents,'ask_json',return_value={'shots':[]}))
            plan_split(record, 'target', True)
        self.assertIn('最终编辑后的配音文案', ask.call_args.args[1]['source_text'])
        self.assertNotIn('过时文本', ask.call_args.args[1]['source_text'])

    def test_final_spoken_snapshot_overrides_independent_caption_annotation(self):
        record = fixture()
        record['final_narration'] = {'script': '真正已合成的配音文案', 'fingerprint': 'v1'}
        record['scenes'][1]['text'] = '独立字幕括号注释'
        with ExitStack() as stack:
            planners(stack)
            ask=stack.enter_context(patch.object(agents,'ask_json',return_value={'shots':[]}))
            children=plan_split(record, 'target')
        self.assertEqual(ask.call_args.args[1]['source_text'], '真正已合成的配音文案')
        self.assertEqual(children[0]['source_subtitles'][0]['text'], '独立字幕括号注释')
        from backend.app.video_auto_split import merge_split
        current=copy.deepcopy(record)
        current['final_narration']['fingerprint']='v2'
        with self.assertRaisesRegex(ValueError, '已改变'):
            merge_split(current,record,'target',False,children)


if __name__ == '__main__':
    unittest.main()
