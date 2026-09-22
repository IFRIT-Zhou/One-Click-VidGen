"""Offline regressions for the proven single-image / phased-video handoff.

These tests exercise data and prompt preservation, not the aesthetic quality of
an LLM or video model. Every Agent response is supplied locally.
"""
import copy
import json
import re
import unittest
from unittest.mock import patch

from backend.app.video_agents import write_image_prompts, write_video_prompts
from backend.app.video_director_contracts import SINGLE_REFERENCE_GUIDE, VIDEO_DIRECTOR_REVISION
from backend.app.video_motion_plan import (
    normalize_motion_plan,
    prompt_plan_issues,
    render_motion_action,
)
from backend.app.video_plan import normalize_shots, parse_srt, plan_storyboard


USER_IMAGE_PROMPT = (
    '简笔火柴人，黑色钢笔轮廓线，简约手绘，一个红色衣服的小人站在演台中央，'
    '周围坐着很多观众围观他的讲话，他的脑袋上有个对话气泡框，里面是日本和韩国的标志性景观。'
    '观众表情惊讶紧张，观众们头上也有一个气泡框，里面是汉奸、不爱国，还有飞机战火'
)
PURPOSE = '表示当下中国普通人对于日韩旅行的刻板印象'
# The finalizer gives the same pictured person a consistent name; it does not
# invent another visual design. Keep the user's original wording above intact.
FINAL_IMAGE_PROMPT = (
    '【人物与画风】简笔火柴人，黑色钢笔轮廓线，简约手绘。'
    '【画面内容】' + USER_IMAGE_PROMPT.replace('一个红色衣服的小人', '红色衣服的主讲人') +
    '。【必要限制】图案与评论属于对应人物的气泡，不是会场正在发生的战争。'
)
SUCCESS_VIDEO_BODY = (
    '生成一段正常全屏、横屏16:9的连续动画，保持参考图中的白底手绘火柴人画风、'
    '红色身体的主讲人、讲台、会场和观众造型稳定。主讲人在会场中央演讲。'
    '第一阶段，主讲人开口提问日韩旅游，主讲人上方的气泡显示日本和韩国的标志性景观；'
    '随后该气泡消失。第二阶段，周围观众产生激烈反应，观众的气泡分别出现“汉奸”和“不爱国”，'
    '另一个想象气泡表现战机、炮火与城市硝烟；观众表情和肢体由疑惑变得惊恐，主讲人显得无奈；'
    '随后这些气泡全部消失。第三阶段，主讲人再次开口，上方气泡显示长城、兵马俑等祖国景观，'
    '观众转为欢呼雀跃，主讲人尴尬微笑。各阶段依次发生，不同时堆出所有气泡。'
    '动作自然连贯，允许围绕同一会场合理切换中景和观众反应镜头，不新增角色，不添加计划外文字，静音。'
)
EXPECTED_GUIDE = (
    '图1是单张复合内容参考，不是必须照搬的首帧，也不表示所有内容同时存在。'
    '按提示词把图中的元素拆成先后阶段，输出始终是普通全屏视频。'
)


def success_video_row(identity, prefix=''):
    """Split the actual successful text without inventing a new video direction."""
    boundaries = [SUCCESS_VIDEO_BODY.index(label) for label in
                  ('第一阶段', '第二阶段', '第三阶段', '各阶段依次发生')]
    return {
        'id': identity,
        'continuity_prompt': prefix + SUCCESS_VIDEO_BODY[:boundaries[0]],
        'beat_prompts': [
            {'beat': index + 1, 'prompt': SUCCESS_VIDEO_BODY[start:end]}
            for index, (start, end) in enumerate(zip(boundaries, boundaries[1:]))
        ],
        'ending_prompt': SUCCESS_VIDEO_BODY[boundaries[-1]:],
    }


def composite_plan():
    audience_labels = [
        {'text': '汉奸', 'owner': '观众', 'container': '气泡'},
        {'text': '不爱国', 'owner': '观众', 'container': '气泡'},
    ]
    return {
        'version': 2,
        'scene_anchor': '主讲人在会场中央的讲台后，观众围坐在讲台前方。',
        'participants': ['主讲人', '观众'],
        'reference_participants': ['主讲人', '观众'],
        'beats': [
            {'action': '主讲人提问，主讲人的气泡出现日韩景观，随后消失。', 'texts': []},
            {'action': '观众气泡出现指责与战争想象，观众变得恐慌，主讲人无奈；随后气泡消失。',
             'texts': copy.deepcopy(audience_labels)},
            {'action': '主讲人的气泡出现长城与兵马俑，观众欢呼，主讲人尴尬微笑。', 'texts': []},
        ],
        'reference_beat': 1,
        'reference_visual': FINAL_IMAGE_PROMPT,
        'reference_texts': audience_labels,
    }


class VideoCompositeReferenceTest(unittest.TestCase):
    def setUp(self):
        self.plan = composite_plan()
        self.scenes = parse_srt(
            '1\n00:00:31,281 --> 00:00:34,200\n那如果我说去日韩旅游呢？\n\n'
            '2\n00:00:34,200 --> 00:00:38,700\n汉奸、不爱国。将军的炮火已经瞄准了南边。\n\n'
            '3\n00:00:38,700 --> 00:00:42,822\n最后所有的落点都会回到一句：出国不如逛祖国的大好河山。'
        )
        self.shot = normalize_shots([{
            'id': 'travel_reference',
            'slide_ids': [row['slide_id'] for row in self.scenes],
            'kind': 'video',
            'intent': PURPOSE,
            'reference_ids': [],
            'visual_description': USER_IMAGE_PROMPT,
            'image_prompt': FINAL_IMAGE_PROMPT,
            'video_prompt': SUCCESS_VIDEO_BODY,
            'motion_plan': self.plan,
            'action': render_motion_action(self.plan),
        }], self.scenes)[0]

    def test_proven_composite_image_and_video_pass_independent_text_checks(self):
        self.assertEqual(self.plan['beats'][0]['texts'], [])
        self.assertEqual(len(self.plan['reference_texts']), 2)
        self.assertEqual(prompt_plan_issues(FINAL_IMAGE_PROMPT, self.plan, 'image'), [])
        self.assertEqual(prompt_plan_issues(SUCCESS_VIDEO_BODY, self.plan, 'video'), [])
        # The last phase need not be painted in the reference image beforehand.
        self.assertNotIn('长城', FINAL_IMAGE_PROMPT)
        self.assertNotIn('兵马俑', FINAL_IMAGE_PROMPT)
        self.assertIn('长城、兵马俑', SUCCESS_VIDEO_BODY)

    def test_roundtrip_preserves_reference_selection_and_authoritative_timeline(self):
        before = copy.deepcopy((self.plan, self.shot, self.scenes))
        persisted = normalize_shots([self.shot], self.scenes)[0]
        reopened = normalize_shots([persisted], self.scenes)[0]
        self.assertEqual(reopened['motion_plan'], self.plan)
        self.assertEqual(reopened['source_subtitles'], self.scenes)
        self.assertEqual(reopened['slide_ids'], [row['slide_id'] for row in self.scenes])
        self.assertEqual((reopened['start'], reopened['end']), (31.281, 42.822))
        self.assertEqual(reopened['duration'], 11.541)
        self.assertEqual(reopened['generation_duration'], 12)
        self.assertEqual((self.plan, self.shot, self.scenes), before)

    def test_all_phases_including_unpictured_ending_reach_video_finalizer(self):
        captured = []

        def ask(_system, payload):
            captured.append(copy.deepcopy(payload))
            return {'shots': [success_video_row(self.shot['id'])]}

        prompt = write_video_prompts({}, [self.shot], [], ask=ask)[0]['video_prompt']
        self.assertEqual(len(captured), 1)
        forwarded = captured[0]['shots'][0]
        self.assertEqual(forwarded['motion_plan']['beats'], self.plan['beats'])
        self.assertEqual(forwarded['duration'], 11.541)
        self.assertEqual(forwarded['generation_duration'], 12)
        self.assertEqual(forwarded['numbered_references'][0]['number'], '图1')
        self.assertEqual(len(forwarded['numbered_references']), 1)
        self.assertTrue(prompt.replace('\n', '').endswith(SUCCESS_VIDEO_BODY))
        positions = [prompt.index(name) for name in ('第一阶段', '第二阶段', '第三阶段')]
        self.assertEqual(positions, sorted(positions))
        action = render_motion_action(self.plan)
        self.assertIn(self.plan['beats'][-1]['action'], action)
        self.assertEqual(action.count('当前可见文字：无'), 2)

    def test_reference_only_legend_does_not_become_a_video_wide_requirement(self):
        plan = copy.deepcopy(self.plan)
        plan['reference_texts'].append(
            {'text': '景观示意', 'owner': '画面标注', 'container': '图例'}
        )
        image = FINAL_IMAGE_PROMPT + '画面标注的图例写“景观示意”。'
        plan['reference_visual'] = image
        self.assertEqual(prompt_plan_issues(image, plan, 'image'), [])
        self.assertEqual(prompt_plan_issues(SUCCESS_VIDEO_BODY, plan, 'video'), [])
        self.assertNotIn('景观示意', render_motion_action(plan))
        self.assertEqual(normalize_motion_plan(plan)['reference_texts'][-1]['text'], '景观示意')

    def test_reference_text_owner_must_be_visible_or_an_explicit_annotation(self):
        for owner in ('未登记的人', '后续来宾'):
            with self.subTest(owner=owner):
                plan = copy.deepcopy(self.plan)
                plan['participants'].append('后续来宾')
                plan['reference_texts'][0]['owner'] = owner
                with self.assertRaises(ValueError):
                    normalize_motion_plan(plan)

    def test_reference_subjects_cannot_invent_or_duplicate_participants(self):
        for names in (['新主持人'], ['主讲人', '主讲人']):
            with self.subTest(names=names), self.assertRaises(ValueError):
                normalize_motion_plan(dict(self.plan, reference_participants=names))

    def test_nonhuman_process_allows_later_people_and_props_outside_core_image(self):
        plan = {
            'version': 2,
            'scene_anchor': '固定机位观察透明管道内的阀门。',
            'participants': ['阀门', '检修员', '工具箱'],
            'reference_participants': ['阀门'],
            'beats': [
                {'action': '阀门逐渐打开，管内液体开始流动。', 'texts': []},
                {'action': '液体流动趋于平稳，检修员提着工具箱来到管道旁检查。', 'texts': []},
            ],
            'reference_beat': 1,
            'reference_visual': '透明管道内的阀门微开，画面中无人物，无文字。',
            'reference_texts': [],
        }
        image = plan['reference_visual']
        video = '阀门逐渐打开，液体开始流动；随后检修员提着工具箱进入画面检查。静音。'
        self.assertEqual(prompt_plan_issues(image, plan, 'image'), [])
        self.assertEqual(prompt_plan_issues(video, plan, 'video'), [])
        self.assertNotIn('检修员', image)
        self.assertNotIn('工具箱', image)
        self.assertEqual(normalize_motion_plan(plan)['reference_participants'], ['阀门'])
        action = render_motion_action(plan)
        for unrelated in ('主讲人', '观众', '会场', '气泡'):
            self.assertNotIn(unrelated, action)

    def test_composite_reference_normalization_remains_detached_from_input(self):
        before = copy.deepcopy(self.plan)
        result = normalize_motion_plan(self.plan)
        result['reference_participants'].append('被修改的副本')
        result['reference_texts'][0]['text'] = '被修改的文字'
        result['beats'][1]['texts'][0]['text'] = '另一个修改'
        self.assertEqual(self.plan, before)

    def test_legacy_version_one_and_unplanned_drafts_reopen_without_migration(self):
        old = {key: copy.deepcopy(value) for key, value in self.plan.items()
               if key not in ('reference_participants', 'reference_texts')}
        old['version'] = 1
        old['reference_beat'] = 2
        self.assertEqual(normalize_motion_plan(old), old)
        old_shot = dict(self.shot, motion_plan=old)
        reopened = normalize_shots([old_shot], self.scenes)[0]
        self.assertEqual(reopened['motion_plan'], old)
        self.assertNotIn('reference_texts', reopened['motion_plan'])
        unplanned = dict(self.shot)
        del unplanned['motion_plan']
        self.assertNotIn('motion_plan', normalize_shots([unplanned], self.scenes)[0])

    def test_successful_reference_guide_is_prepended_exactly_once(self):
        self.assertEqual(SINGLE_REFERENCE_GUIDE, EXPECTED_GUIDE)
        for prefix in ('', EXPECTED_GUIDE):
            with self.subTest(already_prefixed=bool(prefix)):
                rows = write_video_prompts({}, [self.shot], [], ask=lambda *_: {
                    'shots': [success_video_row(self.shot['id'], prefix)]
                })
                prompt = rows[0]['video_prompt']
                self.assertTrue(prompt.startswith(EXPECTED_GUIDE))
                self.assertEqual(prompt.count(EXPECTED_GUIDE), 1)
                self.assertTrue(prompt.replace('\n', '').endswith(SUCCESS_VIDEO_BODY))

    def test_omitted_text_free_final_phase_has_only_one_repair_attempt(self):
        calls = []

        def ask(_system, payload):
            calls.append(copy.deepcopy(payload))
            incomplete = success_video_row(self.shot['id'])
            incomplete['beat_prompts'].pop()
            return {'shots': [incomplete]}

        with self.assertRaisesRegex(ValueError, '定稿阶段缺失、重复或顺序改变'):
            write_video_prompts({}, [self.shot], [], ask=ask)
        self.assertEqual(len(calls), 2)
        self.assertNotIn('validation_errors', calls[0])
        self.assertTrue(calls[1]['validation_errors'])
        self.assertIn('[1, 2, 3]', calls[1]['validation_errors'][0])

    def test_literal_video_warning_preserves_every_phase_and_final_reference_guide(self):
        calls, drafts = [], []
        def ask(_system, payload):
            calls.append(copy.deepcopy(payload))
            row = success_video_row(self.shot['id'])
            for part in row['beat_prompts']:
                part['prompt'] = part['prompt'].replace('气泡', '对话框')
            row['ending_prompt'] = row['ending_prompt'].replace('气泡', '对话框')
            return {'shots': [row]}
        result = write_video_prompts({}, [self.shot], [], ask=ask,
                                     on_draft=lambda rows: drafts.append(copy.deepcopy(rows)))[0]
        self.assertEqual(len(calls), 1)
        self.assertTrue(result['video_prompt_warnings'])
        self.assertEqual(len(drafts), 1)
        for row in [result] + [row for batch in drafts for row in batch]:
            prompt = row['video_prompt']
            self.assertTrue(prompt.startswith(EXPECTED_GUIDE))
            self.assertEqual(prompt.count(EXPECTED_GUIDE), 1)
            for phase in ('第一阶段', '第二阶段', '第三阶段'):
                self.assertIn(phase, prompt)
            self.assertIn('长城、兵马俑', prompt)
            self.assertTrue(row['video_prompt_warnings'])

    def test_valid_video_candidate_remains_available_when_repair_loses_final_phase(self):
        calls, drafts = [], []
        def ask(_system, payload):
            calls.append(copy.deepcopy(payload))
            row = success_video_row(self.shot['id'])
            if len(calls) == 1:
                # A literal label mismatch requests the single correction pass.
                row['beat_prompts'][1]['prompt'] = row['beat_prompts'][1]['prompt'].replace('不爱国', '标签')
            else:
                row['beat_prompts'].pop()
            return {'shots': [row]}
        with self.assertRaisesRegex(ValueError, '定稿阶段缺失、重复或顺序改变'):
            write_video_prompts({}, [self.shot], [], ask=ask,
                                on_draft=lambda rows: drafts.append(copy.deepcopy(rows)))
        self.assertEqual(len(calls), 2)
        self.assertTrue(drafts)
        preserved = drafts[0][0]
        self.assertTrue(preserved['video_prompt'].startswith(EXPECTED_GUIDE))
        self.assertIn('第三阶段', preserved['video_prompt'])
        self.assertTrue(preserved['video_prompt_warnings'])

    def test_reviewable_image_difference_does_not_prevent_video_finalization(self):
        shot = copy.deepcopy(self.shot)
        shot['motion_plan']['reference_texts'] = []
        image = (FINAL_IMAGE_PROMPT + '观众的气泡分别显示“汉奸”和“不爱国”。')
        calls = []
        def images(_system, payload):
            calls.append('image')
            return {'shots': [{'id': shot['id'], 'image_prompt': image}]}
        def videos(_system, payload):
            calls.append('video')
            self.assertEqual(payload['shots'][0]['image_prompt'], shot['image_prompt'])
            return {'shots': [success_video_row(shot['id'])]}
        shot.update(write_image_prompts({}, '简笔火柴人', [shot], [], ask=images)[0])
        self.assertTrue(shot['image_prompt_warnings'])
        shot.update(write_video_prompts({}, [shot], [], ask=videos)[0])
        self.assertEqual(calls, ['image', 'image', 'video'])
        self.assertIn('第三阶段', shot['video_prompt'])
        self.assertTrue(shot['image_prompt'])

    def test_finalized_successful_pair_persists_without_silent_prompt_rewriting(self):
        shot = copy.deepcopy(self.shot)
        shot.update(write_image_prompts({}, '简笔火柴人', [shot], [], ask=lambda *_: {
            'shots': [{'id': shot['id'], 'image_prompt': FINAL_IMAGE_PROMPT}]
        })[0])
        shot.update(write_video_prompts({}, [shot], [], ask=lambda *_: {
            'shots': [success_video_row(shot['id'])]
        })[0])
        self.assertTrue(shot['image_prompt'].startswith('【本图旨在】' + PURPOSE))
        reopened = normalize_shots([shot], self.scenes)[0]
        self.assertEqual(reopened['image_prompt'], shot['image_prompt'])
        self.assertEqual(reopened['video_prompt'], shot['video_prompt'])
        self.assertEqual(reopened['motion_plan'], self.plan)

    def test_real_planning_pipeline_parses_and_preserves_the_successful_contract(self):
        responses = iter([
            {'shots': [{
                'id': self.shot['id'], 'slide_ids': self.shot['slide_ids'],
                'kind': 'video', 'intent': PURPOSE,
                'motion_basis': '提问、观众反应与旅行建议形成前后对比',
                'progression_plan': '提出日韩旅行，观众产生刻板印象，转到国内旅行后欢呼',
            }]},
            {'shots': [{
                'id': self.shot['id'], 'visual_description': USER_IMAGE_PROMPT,
                'visual_design': {
                    'candidates': ['人物互动与有归属的想象气泡', '分别展示各个旅行景点'],
                    'selection_reason': '共同现场能表现提问与观众反应的关系',
                    'expression': 'narrative', 'human_presence': 'present',
                    'visible_evidence': '主讲人提出日韩景点，观众气泡表现指责与战争想象',
                },
                'reference_ids': [],
            }]},
            {'shots': [{'id': self.shot['id'], 'motion_plan': self.plan}]},
            {'shots': [{'id': self.shot['id'], 'image_prompt': FINAL_IMAGE_PROMPT}]},
            {'shots': [success_video_row(self.shot['id'])]},
        ])
        calls, logs = [], []

        def fake_generate(**kwargs):
            calls.append((kwargs['system_prompt'], json.loads(kwargs['user_prompt'])))
            return json.dumps(next(responses), ensure_ascii=False)

        with patch('story_agents.create_story_context', return_value={'summary': '旅行提问与观众反应'}), \
             patch('backend.app.video_agents.generate_gemini_text', side_effect=fake_generate):
            context, shots = plan_storyboard(
                self.scenes, '简笔火柴人，黑色钢笔轮廓线', '主讲人穿红衣',
                '主讲人与观众在同一会场', [], logs.append,
                {'director_strategy': 'enhanced', 'video_orientation': 'landscape'},
            )
        self.assertEqual(len(calls), 5)
        self.assertEqual([int(re.search(r'Agent (\d+)', system).group(1))
                          for system, _payload in calls], [1, 2, 3, 4, 5])
        self.assertEqual([index + 1 for index, (_system, payload) in enumerate(calls)
                          if 'method_example' in payload], [2, 3])
        self.assertEqual(context['video_direction']['director_revision'], VIDEO_DIRECTOR_REVISION)
        self.assertEqual(len(shots), 1)
        final = shots[0]
        self.assertTrue(final['audit']['ready'])
        self.assertEqual(final['image_prompt'], '【本图旨在】' + PURPOSE + '\n' + FINAL_IMAGE_PROMPT)
        self.assertTrue(final['video_prompt'].replace('\n', '').endswith(SUCCESS_VIDEO_BODY))
        self.assertEqual(final['video_prompt'].count(EXPECTED_GUIDE), 1)
        self.assertEqual(final['motion_plan'], self.plan)
        self.assertEqual(final['source_subtitles'], self.scenes)
        self.assertEqual(final['duration'], 11.541)
        self.assertEqual(final['generation_duration'], 12)
        self.assertEqual(calls[4][1]['shots'][0]['image_prompt'], final['image_prompt'])


if __name__ == '__main__':
    unittest.main()
