import copy
import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

from backend.app import video_agents
from backend.app.video_boundary_review import review_boundaries
from backend.app.video_duration_limits import generation_duration, max_video_duration, validate_video_duration
from backend.app.video_group_repair import legal_ranges, repair_groups, validate_group_structure
from backend.app.video_plan import (enforce_opening_motion, normalize_shots,
                                    plan_storyboard, planning_fingerprint)


CLOUD = {'video_generation_backend': 'cloud_h3', 'dynamic_max_shot_duration': 30}


def scenes_for(lengths):
    scenes, start = [], 0.0
    for index, duration in enumerate(lengths):
        end = round(start + duration, 3)
        scenes.append(dict(slide_id=f's{index}', start=start, end=end, text=f'第{index}句完整配音。'))
        start = end
    return scenes


def group(identity, ids, kind='video'):
    return dict(id=identity, slide_ids=ids, kind=kind, intent='依次表达当前字幕',
                motion_basis='字幕表现前后变化', progression_plan='依照字幕先后呈现过程',
                action='呈现过程', image_prompt='本段核心图', video_prompt='参考图1呈现过程',
                semantic=dict(message='当前字幕', source_basis='当前字幕', fact_status='fact'))


class DurationLimitTests(unittest.TestCase):
    def test_cloud_limit_overrides_old_thirty_second_preference(self):
        self.assertEqual(max_video_duration(CLOUD), 15)
        self.assertEqual(max_video_duration({'dynamic_max_shot_duration': 30}), 30)
        self.assertEqual(max_video_duration({'dynamic_max_shot_duration': 10}), 10)
        self.assertEqual(max_video_duration({}), 15)

    def test_request_ceiling_is_validated_without_shortening_audio(self):
        self.assertEqual(validate_video_duration(9.999, CLOUD), 10)
        self.assertEqual(generation_duration(10.001), 11)
        with self.assertRaisesRegex(ValueError, '16 秒.*15 秒'):
            validate_video_duration(15.001, CLOUD)
        with self.assertRaisesRegex(ValueError, '小于字幕跨度'):
            validate_video_duration(10.001, CLOUD, requested_duration=10)
        with self.assertRaisesRegex(ValueError, '整数秒'):
            validate_video_duration(8, CLOUD, requested_duration=8.5)
        for invalid in (float('nan'), float('inf'), 0, -1, None):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                generation_duration(invalid)

    def test_provider_ignoring_fifteen_second_prompt_is_safely_partitioned(self):
        scenes = scenes_for([5, 5.001, 4, 4])
        original = copy.deepcopy(scenes)
        row = group('old', [s['slide_id'] for s in scenes])
        ask = Mock(return_value={'shots': [row]})
        result = video_agents.plan_groups({}, scenes, CLOUD, ask)
        normalized = normalize_shots(result, scenes, max_duration=15)
        validate_group_structure(result, scenes)
        self.assertTrue(all(s['generation_duration'] <= 15 for s in normalized))
        self.assertEqual(scenes, original)
        self.assertEqual(normalized[-1]['end'], 18.001)
        self.assertEqual(ask.call_args_list[0].args[1]['arrangement']['max_video_duration'], 15)
        self.assertIn('15秒', ask.call_args_list[0].args[0])

    def test_single_overlong_subtitle_keeps_full_static_duration(self):
        scenes = scenes_for([15.001])
        ask = Mock()
        result = repair_groups(CLOUD, scenes, [group('old', ['s0'])], ask)
        normalized = normalize_shots(result, scenes, max_duration=15)
        self.assertEqual(normalized[0]['kind'], 'static')
        self.assertEqual(normalized[0]['duration'], 15.001)
        self.assertEqual(normalized[0]['source_subtitles'], scenes)
        self.assertEqual(normalized[0]['duration_repair']['method'], 'single_subtitle_static')
        ask.assert_not_called()

    def test_static_medical_shot_is_not_subject_to_video_limit(self):
        scenes = scenes_for([20, 20])
        rows = [group('static', ['s0', 's1'], 'static')]
        ask = Mock()
        result = repair_groups({**CLOUD, 'video_direction': {'dynamic_text_mode': 'medical_paper'}}, scenes, rows, ask)
        self.assertEqual(result, rows)
        ask.assert_not_called()

    def test_opening_rule_cannot_turn_long_static_exception_back_into_video(self):
        scenes = scenes_for([16])
        rows = normalize_shots([group('old', ['s0'], 'static')], scenes, max_duration=15)
        self.assertEqual(enforce_opening_motion(rows, max_duration=15)[0]['kind'], 'static')

    def test_boundary_review_rejects_merge_above_cloud_limit(self):
        scenes = scenes_for([8, 8])
        rows = [group('a', ['s0']), group('b', ['s1'])]
        proposal = dict(left_id='a', right_id='b', reason='相邻过程', evidence_slide_ids=['s0', 's1'],
                        shots=[group('new', ['s0', 's1'])])
        result = review_boundaries(CLOUD, scenes, rows, Mock(return_value={'repartitions': [proposal]}))
        self.assertEqual([row['slide_ids'] for row in result], [['s0'], ['s1']])

    def test_ranges_include_pause_time_and_compact_ranges_respect_limit(self):
        scenes = scenes_for([2] * 45)
        scenes[0]['start'] = -0.001
        for item in legal_ranges(scenes, 10):
            self.assertLessEqual(item['duration'], 10)
        first = legal_ranges(scenes, 10)[0]
        self.assertEqual(first['last_slide_id'], 's3')

    def test_final_audit_rejects_legacy_overlong_dynamic_draft(self):
        scenes = scenes_for([8, 8])
        row = normalize_shots([group('old', ['s0', 's1'])], scenes)[0]
        with self.assertRaisesRegex(ValueError, '15 秒上限'):
            video_agents.audit_storyboard([row], set(), parameters=CLOUD)
        self.assertTrue(video_agents.audit_storyboard([row], set(), parameters={'dynamic_max_shot_duration': 30})[0]['audit']['ready'])

    def test_resume_repairs_only_overlong_shots_and_preserves_completed_neighbor(self):
        scenes = scenes_for([8, 8, 8])
        saved = normalize_shots([group('old', ['s0', 's1']), group('keep', ['s2'])], scenes)
        state = dict(fingerprint=planning_fingerprint(scenes, '', '', '', [], CLOUD), context={},
                     stage='提示词定稿', shots=saved,
                     completed={phase: ['old', 'keep'] for phase in ('core', 'motion', 'image', 'video')})
        snapshots = []
        motion = dict(version=1, scene_anchor='同一场景', participants=[], reference_beat=1,
                      reference_visual='当前场景', beats=[dict(action='依次变化', texts=[])])
        with ExitStack() as stack:
            ask = stack.enter_context(patch.object(video_agents, 'ask_json', return_value={'shots': []}))
            core = stack.enter_context(patch.object(video_agents, 'design_core_images',
                side_effect=lambda context, scenes, rows, refs: [dict(row, visual_description='当前图') for row in rows]))
            stack.enter_context(patch.object(video_agents, 'direct_motion',
                side_effect=lambda context, rows, refs: [dict(row, motion_plan=motion, action='依次变化') for row in rows]))
            stack.enter_context(patch.object(video_agents, 'write_image_prompts',
                side_effect=lambda context, style, rows, refs, **kwargs: [dict(row, image_prompt='当前核心图') for row in rows]))
            stack.enter_context(patch.object(video_agents, 'write_video_prompts',
                side_effect=lambda context, rows, refs, **kwargs: [dict(row, video_prompt='参考图1依次变化') for row in rows]))
            context, result = plan_storyboard(scenes, '', '', '', [], lambda message: None,
                parameters=CLOUD, resume_state=state, save_state=snapshots.append)
        self.assertEqual([row['slide_ids'] for row in result], [['s0'], ['s1'], ['s2']])
        self.assertEqual(result[-1]['id'], 'keep')
        self.assertEqual(result[-1]['image_prompt'], saved[-1]['image_prompt'])
        self.assertEqual(result[-1]['video_prompt'], saved[-1]['video_prompt'])
        self.assertTrue(all(row['generation_duration'] <= 15 for row in result))
        self.assertNotIn('keep', [row['id'] for call in core.call_args_list for row in call.args[2]])
        self.assertEqual(ask.call_count, 2)
        self.assertEqual(len(ask.call_args.args[1]['editable_shots']), 1)
        self.assertEqual(context['dynamic_max_shot_duration'], 15)
        self.assertEqual(result[-1]['end'], scenes[-1]['end'])
        self.assertIn('keep', snapshots[-1]['completed']['core'])
        self.assertNotIn('old', snapshots[-1]['completed']['core'])


if __name__ == '__main__':
    unittest.main()
