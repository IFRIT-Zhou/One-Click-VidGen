"""Offline speech-owner handoffs, not a benchmark of real LLM understanding."""
import copy
import json
import unittest
from unittest.mock import patch

from backend.app.video_agents import (
    design_core_images,
    direct_motion,
    plan_groups,
    write_image_prompts,
    write_video_prompts,
)
from backend.app.video_plan import edit_structure, normalize_shots, plan_storyboard


CASES = [
    {
        'name': 'student_quotes_teacher_question',
        'source': '学生问老师：为什么水会沸腾？老师请学生观察烧杯。',
        'turns': [
            {'source_text': '为什么水会沸腾', 'speaker': '学生', 'addressee': '老师',
             'mode': 'spoken', 'basis': '原文明确学生问老师，不是旁白自己提问'},
        ],
        'participants': ['学生', '老师'],
    },
    {
        'name': 'customer_reaction_not_customer_service',
        'source': '客服问：换货可以吗？顾客回答：可以，但请尽快。',
        'turns': [
            {'source_text': '换货可以吗', 'speaker': '客服', 'addressee': '顾客',
             'mode': 'spoken', 'basis': '原文明确客服提出处理方案'},
            {'source_text': '可以，但请尽快', 'speaker': '顾客', 'addressee': '客服',
             'mode': 'spoken', 'basis': '回应归顾客，不归复述整段话的旁白'},
        ],
        'participants': ['客服', '顾客'],
    },
    {
        'name': 'rhetorical_self_answer_does_not_invent_audience',
        'source': '我为什么坚持记录？因为记录能提醒我已经走过多远。',
        'turns': [
            {'source_text': '我为什么坚持记录', 'speaker': '讲述者', 'addressee': '',
             'mode': 'narration', 'basis': '讲述者自问，没有另一个提问角色'},
            {'source_text': '因为记录能提醒我已经走过多远', 'speaker': '讲述者', 'addressee': '',
             'mode': 'narration', 'basis': '承接自己的设问，不是观众回答'},
        ],
        'participants': ['讲述者'],
    },
]


def fixture(case):
    scenes = [{'slide_id': 's1', 'start': 0, 'end': 5, 'text': case['source']}]
    semantic = {'message': case['source'], 'source_basis': case['source'],
                'speech_turns': copy.deepcopy(case['turns'])}
    row = {'id': 'shot1', 'slide_ids': ['s1'], 'kind': 'video',
           'intent': '看清本段的问题与回应关系', 'semantic': semantic,
           'motion_basis': '互动或者思考过程可用动作表达', 'progression_plan': '提出问题后呈现相应回应',
           'visual_description': '、'.join(case['participants']) + '在同一场景互动',
           'reference_ids': []}
    return scenes, normalize_shots([row], scenes)[0]


class VideoSpeechAttributionTest(unittest.TestCase):
    def test_normalization_and_json_roundtrip_preserve_turns_without_mutating_input(self):
        for case in CASES:
            with self.subTest(case=case['name']):
                before = copy.deepcopy(case)
                scenes, shot = fixture(case)
                self.assertEqual(shot['semantic']['speech_turns'], case['turns'])
                reloaded = json.loads(json.dumps(shot, ensure_ascii=False))
                saved = normalize_shots([reloaded], scenes)[0]
                self.assertEqual(saved['semantic']['speech_turns'], case['turns'])
                self.assertEqual(saved['source_subtitles'][0]['text'], case['source'])
                self.assertEqual(case, before)

    def test_optional_legacy_or_malformed_turns_do_not_block_or_invent_owners(self):
        scenes, shot = fixture(CASES[0])
        legacy = copy.deepcopy(shot)
        legacy['semantic'].pop('speech_turns')
        self.assertNotIn('speech_turns', normalize_shots([legacy], scenes)[0]['semantic'])
        for value in (None, '老师说的', {}, [None], [{'speaker': '老师'}],
                      [{'source_text': 123, 'speaker': '老师'}],
                      [{'source_text': '这段话不在原文里面', 'speaker': '老师', 'mode': 'spoken'}]):
            with self.subTest(value=value):
                modified = copy.deepcopy(shot)
                modified['semantic']['speech_turns'] = value
                saved = normalize_shots([modified], scenes)[0]
                self.assertEqual(saved['semantic'].get('speech_turns', []), [])
                self.assertEqual(saved['intent'], shot['intent'])
                self.assertEqual(saved['source_subtitles'], scenes)

    def test_boundary_edit_clears_only_changed_shots_speech_ownership(self):
        texts = ['学生问老师：为什么水会沸腾？', '老师请学生观察烧杯。',
                 '客服问：换货可以吗？', '顾客回答：可以，但请尽快。',
                 '我为什么坚持记录？', '因为记录能提醒我已经走过多远。']
        scenes = [{'slide_id': 's' + str(i), 'start': i * 2, 'end': (i + 1) * 2,
                   'text': text} for i, text in enumerate(texts)]
        rows = normalize_shots([
            {'id': 'shot' + str(i), 'slide_ids': ['s' + str(i * 2), 's' + str(i * 2 + 1)],
             'kind': 'video', 'semantic': {'speech_turns': copy.deepcopy(CASES[i]['turns'])},
             'image_prompt': '保留的画面草稿', 'video_prompt': '保留的视频草稿'}
            for i in range(3)
        ], scenes)
        for i, row in enumerate(rows):
            self.assertEqual(row['semantic']['speech_turns'], CASES[i]['turns'])
        before = copy.deepcopy(rows)
        moved = edit_structure(rows, scenes, 'boundary', 0, 3)
        for row in moved[:2]:
            self.assertNotIn('speech_turns', row.get('semantic', {}))
            self.assertTrue(row['design_needs_review'])
            self.assertEqual(row['image_prompt'], '保留的画面草稿')
        self.assertEqual(moved[2], rows[2])
        self.assertEqual(rows, before)

    def test_split_and_merge_discard_stale_source_attribution(self):
        texts = ['学生问老师：为什么水会沸腾？', '老师请学生观察烧杯。', CASES[1]['source']]
        scenes = [{'slide_id': 's' + str(i), 'start': i * 2, 'end': (i + 1) * 2,
                   'text': text} for i, text in enumerate(texts)]
        rows = normalize_shots([
            {'id': 'first', 'slide_ids': ['s0', 's1'], 'kind': 'video',
             'semantic': {'speech_turns': copy.deepcopy(CASES[0]['turns'])}},
            {'id': 'last', 'slide_ids': ['s2'], 'kind': 'video',
             'semantic': {'speech_turns': copy.deepcopy(CASES[1]['turns'])}},
        ], scenes)
        for i, row in enumerate(rows):
            self.assertEqual(row['semantic']['speech_turns'], CASES[i]['turns'])
        split = edit_structure(rows, scenes, 'split', 0, 1)
        for shot in split[:2]:
            self.assertNotIn('speech_turns', shot.get('semantic', {}))
        self.assertEqual(split[2]['semantic'], rows[1]['semantic'])
        merged = edit_structure(rows, scenes, 'merge', 0)
        self.assertNotIn('speech_turns', merged[0].get('semantic', {}))

    def test_agent1_requests_source_ownership_without_extra_api_call(self):
        for case in CASES:
            with self.subTest(case=case['name']):
                scenes, shot = fixture(case)
                calls = []

                def ask(system, payload):
                    calls.append((system, copy.deepcopy(payload)))
                    return {'shots': [copy.deepcopy(shot)]}

                rows = plan_groups({'summary': case['source']}, scenes, {}, ask=ask)
                self.assertEqual(len(calls), 1)
                self.assertIn('speech_turns', calls[0][0])
                self.assertIn('旁白', calls[0][0])
                self.assertEqual(rows[0]['semantic']['speech_turns'], case['turns'])

    def test_creative_and_finalizing_agents_receive_the_same_source_owners(self):
        for case in CASES:
            with self.subTest(case=case['name']):
                scenes, shot = fixture(case)
                context = {'summary': case['source']}
                original = copy.deepcopy(shot)
                calls = []

                def check_handoff(system, payload):
                    calls.append(system)
                    received = payload['shots'][0]
                    self.assertEqual(received['semantic']['speech_turns'], case['turns'])
                    self.assertEqual(received['source_subtitles'], scenes)
                    self.assertIn('speech_turns', system)
                    self.assertIn('旁白', system)

                def core_ask(system, payload):
                    check_handoff(system, payload)
                    return {'shots': [{'id': shot['id'], 'visual_description': shot['visual_description'],
                                       'reference_ids': [], 'semantic': copy.deepcopy(shot['semantic'])}]}

                core = design_core_images(context, scenes, [shot], [], ask=core_ask)[0]
                self.assertEqual(core['semantic']['speech_turns'], case['turns'])
                labels = [{'text': turn['source_text'], 'owner': turn['speaker'],
                           'container': '对话气泡'} for turn in case['turns']]
                picture = '；'.join(case['participants']) + '。' + '；'.join(
                    label['owner'] + '的对话气泡显示“' + label['text'] + '”' for label in labels)
                plan = {'version': 2, 'scene_anchor': shot['visual_description'],
                        'participants': case['participants'], 'beats': [{'action': picture, 'texts': labels}],
                        'reference_beat': 1, 'reference_visual': picture,
                        'reference_participants': case['participants'], 'reference_texts': labels}

                def motion_ask(system, payload):
                    check_handoff(system, payload)
                    return {'shots': [{'id': shot['id'], 'motion_plan': copy.deepcopy(plan)}]}

                motion = direct_motion(context, [shot], [], ask=motion_ask)[0]
                prepared = dict(shot, motion_plan=motion['motion_plan'], action=motion['action'])

                def image_ask(system, payload):
                    check_handoff(system, payload)
                    return {'shots': [{'id': shot['id'], 'image_prompt': '【人物与画风】手绘【画面内容】' + picture}]}

                image = write_image_prompts(context, '手绘', [prepared], [], ask=image_ask)[0]
                prepared['image_prompt'] = image['image_prompt']

                def video_ask(system, payload):
                    check_handoff(system, payload)
                    return {'shots': [{'id': shot['id'], 'continuity_prompt': '保持人物和场景。',
                                       'beat_prompts': [{'beat': 1, 'prompt': picture}],
                                       'ending_prompt': '保持最后状态，静音。'}]}

                video = write_video_prompts(context, [prepared], [], ask=video_ask)[0]
                self.assertEqual(len(calls), 4)
                for turn in case['turns']:
                    self.assertIn(turn['speaker'] + '的对话气泡显示“' + turn['source_text'] + '”',
                                  image['image_prompt'])
                    self.assertIn(turn['speaker'] + '的对话气泡显示“' + turn['source_text'] + '”',
                                  video['video_prompt'])
                self.assertEqual(shot, original)

    def test_core_attribution_correction_reaches_motion_finalizers_and_saved_shot(self):
        self._assert_core_correction_pipeline(with_reason=True)

    def test_core_without_correction_reason_cannot_rewrite_intent_or_source_basis(self):
        self._assert_core_correction_pipeline(with_reason=False)

    def _assert_core_correction_pipeline(self, with_reason):
        case = CASES[0]
        scenes, correct = fixture(case)
        wrong = copy.deepcopy(correct)
        wrong['intent'] = '老师自己提出问题'
        wrong['semantic']['source_basis'] = '老师提问'
        wrong['semantic']['speech_turns'][0]['speaker'] = '老师'
        wrong['semantic']['speech_turns'][0]['addressee'] = '学生'
        intended = '学生向老师提出疑问'
        stages = []
        plan = {'version': 2, 'scene_anchor': '学生与老师在教室',
                'participants': ['学生', '老师'],
                'beats': [{'action': '学生举手，老师倾听', 'texts': []}],
                'reference_beat': 1, 'reference_visual': '学生举手，老师倾听',
                'reference_participants': ['学生', '老师'], 'reference_texts': []}

        def assert_correct(shots):
            self.assertFalse(shots[0].get('design_needs_review'))
            self.assertEqual(shots[0]['semantic']['speech_turns'], case['turns'])
            self.assertEqual(shots[0]['intent'], intended if with_reason else wrong['intent'])
            self.assertEqual(shots[0]['semantic']['source_basis'],
                             case['source'] if with_reason else wrong['semantic']['source_basis'])

        def core(_context, _scenes, shots, _references):
            stages.append('core')
            self.assertEqual(shots[0]['intent'], wrong['intent'])
            update = {'id': 'shot1', 'visual_description': plan['reference_visual'], 'reference_ids': [],
                      'semantic': copy.deepcopy(correct['semantic']), 'intent': intended}
            if with_reason:
                update['attribution_correction'] = '原文明确学生问老师，不能因旁白复述就调换提问者'
            return [update]

        def motion(_context, shots, _references):
            stages.append('motion')
            assert_correct(shots)
            return [{'id': 'shot1', 'motion_plan': plan, 'action': '学生举手，老师倾听'}]

        def image(_context, _style, shots, _references, on_draft=None):
            stages.append('image')
            assert_correct(shots)
            return [{'id': 'shot1', 'image_prompt':
                     '【本图旨在】学生向老师提出疑问【人物与画风】手绘学生与老师。'
                     '【画面内容】学生举手，老师倾听。【必要限制】保持人物一致。'}]

        def video(_context, shots, _references, on_draft=None):
            stages.append('video')
            assert_correct(shots)
            return [{'id': 'shot1', 'video_prompt': '参考图1核心分镜的人物和场景，学生举手提问，老师点头倾听，静音。'}]

        with patch('story_agents.create_story_context', return_value={'summary': case['source']}), \
             patch('backend.app.video_agents.plan_groups', return_value=[wrong]), \
             patch('backend.app.video_agents.design_core_images', side_effect=core), \
             patch('backend.app.video_agents.direct_motion', side_effect=motion), \
             patch('backend.app.video_agents.write_image_prompts', side_effect=image), \
             patch('backend.app.video_agents.write_video_prompts', side_effect=video):
            _context, saved = plan_storyboard(scenes, '手绘', '', '', [], lambda _message: None)
        assert_correct(saved)
        self.assertEqual(stages, ['core', 'motion', 'image', 'video'])


if __name__ == '__main__':
    unittest.main()
