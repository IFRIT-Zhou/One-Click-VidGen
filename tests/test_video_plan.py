import unittest
from unittest.mock import patch
from backend.app.video_plan import parse_srt, normalize_shots, edit_structure, enforce_opening_motion, plan_storyboard

class VideoPlanTest(unittest.TestCase):
    def setUp(self):
        self.scenes = parse_srt('1\n00:00:00,000 --> 00:00:02,300\n甲\n\n2\n00:00:02,300 --> 00:00:06,300\n乙\n\n3\n00:00:06,300 --> 00:00:23,000\n丙')
        self.rows = normalize_shots([{'slide_ids':['scene_001','scene_002'],'kind':'video'},
                                     {'slide_ids':['scene_003'],'kind':'video'}], self.scenes)
        self.phase_plan = {'version': 1, 'scene_anchor': '同一场景', 'participants': [],
                           'beats': [{'action': '先开始再结束', 'texts': []}],
                           'reference_beat': 1, 'reference_visual': '可见的代表状态'}
    def test_duration_and_long_sentence(self):
        self.assertEqual(self.rows[0]['generation_duration'],7)
        self.assertEqual(self.rows[1]['kind'],'static')
    def test_missing_duplicate_or_reordered_subtitles_rejected(self):
        for ids in (['scene_001'],['scene_001','scene_001','scene_003'],['scene_002','scene_001','scene_003']):
            with self.assertRaises(ValueError):
                normalize_shots([{'slide_ids':ids}],self.scenes)
    def test_delete_first_transfers_forward(self):
        rows=edit_structure(self.rows,self.scenes,'delete',0)
        self.assertEqual(rows[0]['slide_ids'],[s['slide_id'] for s in self.scenes])
    def test_delete_last_transfers_backward(self):
        rows=edit_structure(self.rows,self.scenes,'delete',1)
        self.assertEqual(rows[0]['slide_ids'],[s['slide_id'] for s in self.scenes])
        self.assertEqual(rows[0]['kind'],'static')
    def test_insert_partitions_and_clears_prompts(self):
        self.rows[0]['image_prompt']='旧图'
        rows=edit_structure(self.rows,self.scenes,'insert',0,1)
        self.assertEqual(rows[1]['slide_ids'],['scene_002'])
        self.assertEqual(rows[1]['image_prompt'],'')
        self.assertEqual(rows[0]['image_prompt'],'旧图')
    def test_single_sentence_cannot_split(self):
        with self.assertRaises(ValueError):
            edit_structure(self.rows,self.scenes,'split',1,1)
    def test_unknown_reference_rejected(self):
        self.rows[0]['reference_ids']=['missing']
        with self.assertRaises(ValueError):
            normalize_shots(self.rows,self.scenes)
    def test_overlapping_subtitles_rejected(self):
        with self.assertRaises(ValueError):
            parse_srt('1\n00:00:00,000 --> 00:00:04,000\n甲\n\n2\n00:00:03,000 --> 00:00:05,000\n乙')

    def test_first_two_shots_are_dynamic_when_duration_allows(self):
        rows = normalize_shots([
            {'slide_ids':['scene_001'], 'kind':'static'},
            {'slide_ids':['scene_002'], 'kind':'static'},
            {'slide_ids':['scene_003'], 'kind':'static'},
        ], self.scenes)
        forced = enforce_opening_motion(rows)
        self.assertEqual([row['kind'] for row in forced], ['video', 'video', 'static'])
        self.assertEqual(forced[0]['generation_duration'], 4)

    def test_opening_motion_respects_model_duration_limit(self):
        forced = enforce_opening_motion([self.rows[1]])
        self.assertEqual(forced[0]['kind'], 'static')

    def test_agent_pipeline_keeps_prompts_on_normalized_shots(self):
        calls = []
        logs = []
        checkpoints = []
        def groups(_context, _scenes, _arrangement, **kwargs):
            return [{'id': 'shot1', 'slide_ids': ['scene_001', 'scene_002'],
                     'kind': 'video', 'intent': '表达过程'},
                    {'id': 'shot2', 'slide_ids': ['scene_003'], 'kind': 'static', 'intent': '交代结果'}]
        def motion(_context, _scenes, shots, _references):
            calls.append('core')
            return [{'id': row['id'], 'action': '先开始再结束' if row['kind']=='video' else '',
                     'visual_description': '具体核心画面' + row['id'],
                     'visual_design': {'visible_evidence': '具体关系'},
                     'reference_ids': []} for row in shots]
        def images(_context, _style, shots, _references, on_draft=None):
            calls.append('image')
            for row in shots:
                self.assertEqual(row['visual_description'], '具体核心画面'+row['id'])
                self.assertEqual(row['visual_design']['visible_evidence'], '具体关系')
                self.assertTrue(row['source_subtitles'][0]['text'])
                if row['kind'] == 'video':
                    self.assertEqual(row['motion_plan'], self.phase_plan)
                    self.assertEqual(row['action'], '先开始再结束')
            rows = [{'id': row['id'], 'image_prompt':
                     '【人物与画风】简笔画\n【画面内容】主体明确\n【必要限制】无文字',
                     'image_prompt_warnings': ['待核对文字归属']} for row in shots]
            on_draft(rows)
            return rows
        def videos(_context, shots, _references, on_draft=None):
            calls.append('video')
            self.assertIn('【画面内容】', shots[0]['image_prompt'])
            self.assertEqual(shots[0]['motion_plan'], self.phase_plan)
            rows = [{'id': row['id'], 'video_prompt': '参考图1的人物和场景，先开始再结束，静音。'}
                    for row in shots if row['kind']=='video']
            on_draft(rows)
            return rows
        def animate(_context, shots, _references):
            calls.append('motion')
            self.assertEqual(shots[0]['image_prompt'], '')
            self.assertEqual(shots[0]['action'], '')
            return [{'id': row['id'], 'action': '先开始再结束', 'motion_plan': self.phase_plan}
                    for row in shots if row['kind']=='video']
        with patch('story_agents.create_story_context', return_value={'summary': '全文'}), \
             patch('backend.app.video_agents.plan_groups', side_effect=groups), \
             patch('backend.app.video_agents.design_core_images', side_effect=motion), \
             patch('backend.app.video_agents.direct_motion', side_effect=animate), \
             patch('backend.app.video_agents.write_image_prompts', side_effect=images), \
             patch('backend.app.video_agents.write_video_prompts', side_effect=videos):
            _context, shots = plan_storyboard(self.scenes, '简笔画', '', '', [], logs.append,
                checkpoint=lambda context, rows, stage: checkpoints.append((rows, stage)))
        self.assertEqual(checkpoints[0][0][0]['visual_description'], '')
        self.assertEqual(checkpoints[1][0][0]['visual_description'], '具体核心画面shot1')
        self.assertEqual(checkpoints[1][0][0]['image_prompt'], '')
        self.assertNotIn('motion_plan', checkpoints[1][0][0])
        self.assertTrue(checkpoints[-1][0][0]['video_prompt'])
        self.assertEqual(calls, ['core', 'motion', 'image', 'video'])
        self.assertTrue(any('动态视频表达模式：文字辅助' in message for message in logs))
        self.assertLess(next(i for i, message in enumerate(logs) if 'Agent 2' in message),
                        next(i for i, message in enumerate(logs) if 'Agent 3' in message))
        self.assertTrue(any('Agent 4' in message for message in logs))
        self.assertEqual(shots[0]['image_prompt_warnings'], ['待核对文字归属'])
        self.assertTrue(any(stage.startswith('图像提示词草案') and rows[0]['image_prompt']
                            for rows, stage in checkpoints))
        self.assertIn('参考图1', shots[0]['video_prompt'])
        self.assertTrue(shots[0]['audit']['ready'])
        self.assertEqual(shots[1]['video_prompt'], '')
        self.assertEqual(shots[1]['visual_description'], '具体核心画面shot2')
        self.assertEqual(shots[0]['motion_plan'], self.phase_plan)
        self.assertNotIn('motion_plan', shots[1])

    def test_new_plan_roundtrip_and_legacy_without_plan(self):
        import copy
        before = copy.deepcopy(self.rows)
        self.assertNotIn('motion_plan', normalize_shots(self.rows, self.scenes)[0])
        row = dict(self.rows[0], motion_plan=self.phase_plan)
        saved = normalize_shots([row, self.rows[1]], self.scenes)
        self.assertEqual(saved[0]['motion_plan'], self.phase_plan)
        self.assertEqual(normalize_shots(saved, self.scenes)[0]['motion_plan'], self.phase_plan)
        self.assertEqual(self.rows, before)

    def test_new_plan_structure_edit_invalidates_only_changed_shots(self):
        scenes = [{'slide_id': f's{i}', 'start': i * 2, 'end': (i+1)*2, 'text': str(i)} for i in range(6)]
        raw = [dict(id=f'shot{i}', slide_ids=[f's{i*2}', f's{i*2+1}'], kind='video',
                    motion_plan=self.phase_plan, action='旧动作', image_prompt='旧图', video_prompt='旧视频')
               for i in range(3)]
        rows = normalize_shots(raw, scenes)
        for action, index, expected_unchanged in (
            ('split', 0, {'shot1', 'shot2'}), ('insert', 0, {'shot1', 'shot2'}),
            ('merge', 0, {'shot2'}), ('delete', 0, {'shot2'}), ('delete', 2, {'shot0'}),
        ):
            with self.subTest(action=action, index=index):
                result = edit_structure(rows, scenes, action, index, 1)
                self.assertEqual([identity for shot in result for identity in shot['slide_ids']], [s['slide_id'] for s in scenes])
                for shot in result:
                    if shot['id'] in expected_unchanged:
                        self.assertEqual(shot['motion_plan'], self.phase_plan)
                        self.assertEqual(shot['image_prompt'], '旧图')
                    else:
                        self.assertNotIn('motion_plan', shot)
                        self.assertTrue(shot['design_needs_review'])
                        self.assertTrue(shot['previous_designs'])
                        if action != 'insert' or shot['id'] == 'shot0':
                            self.assertEqual(shot['action'], '旧动作')
                            self.assertEqual(shot['image_prompt'], '旧图')
                            self.assertEqual(shot['video_prompt'], '旧视频')
        self.assertEqual(rows[0]['image_prompt'], '旧图')

    def test_boundary_move_retains_ids_design_and_coverage(self):
        scenes = [{'slide_id': f's{i}', 'start': i*2, 'end': (i+1)*2,
                   'text': text} for i, text in enumerate(['日韩旅游', '不爱国', '将军炮火', '国内旅游', '总结'])]
        rows = normalize_shots([dict(id='left', slide_ids=['s0','s1'], kind='video',
                                     action='左动作', image_prompt='左图', video_prompt='左视频'),
                                dict(id='right', slide_ids=['s2','s3'], kind='video',
                                     action='右动作', image_prompt='右图', video_prompt='右视频'),
                                dict(id='last', slide_ids=['s4'], kind='static', image_prompt='末图')], scenes)
        result = edit_structure(rows, scenes, 'boundary', 0, 3)
        self.assertEqual([r['id'] for r in result], ['left','right','last'])
        self.assertEqual(result[0]['slide_ids'], ['s0','s1','s2'])
        self.assertEqual(result[1]['slide_ids'], ['s3'])
        for index in (0,1):
            for field in ('action','image_prompt','video_prompt'):
                self.assertEqual(result[index][field], rows[index][field])
            self.assertTrue(result[index]['design_needs_review'])
        self.assertEqual(result[2], rows[2])
        self.assertEqual([s for r in result for s in r['slide_ids']], [s['slide_id'] for s in scenes])
        with self.assertRaises(ValueError):
            edit_structure(rows, scenes, 'boundary', 0, 0)

    def test_switch_to_static_removes_phase_plan_and_motion(self):
        row = dict(self.rows[0], kind='static', motion_plan=self.phase_plan, action='动作', video_prompt='视频')
        saved = normalize_shots([row, self.rows[1]], self.scenes)[0]
        self.assertNotIn('motion_plan', saved)
        self.assertEqual(saved['action'], '')
        self.assertEqual(saved['video_prompt'], '')

    def test_normalization_preserves_design_and_rebuilds_source(self):
        row = dict(self.rows[0], source_subtitles=[{'text': '错误旧字幕'}],
                   semantic={'message': '原文关系'},
                   visual_design={'candidates': ['方案一', '方案二'], 'visible_evidence': '可见关系'})
        saved = normalize_shots([row, self.rows[1]], self.scenes)
        self.assertEqual(saved[0]['source_subtitles'][0]['text'], '甲')
        self.assertEqual(saved[0]['semantic']['message'], '原文关系')
        self.assertEqual(saved[0]['visual_design']['candidates'], ['方案一', '方案二'])

if __name__=='__main__':
    unittest.main()
