import copy
import json
import tempfile
import unittest
from pathlib import Path

from backend.app.video_boundary_review import review_boundaries
from backend.app.video_sources import narration_groups
from backend.app.video_agents import ask_json, plan_groups


class BoundaryReviewTest(unittest.TestCase):
    def fixture(self, texts):
        scenes = [dict(slide_id=f's{i}', text=t, start=i*2., end=(i+1)*2.) for i,t in enumerate(texts)]
        rows = [dict(id='left', slide_ids=['s0','s1'], kind='video', intent='旧左',
                     motion_basis='动态', progression_plan='过程', semantic={}),
                dict(id='right', slide_ids=['s2','s3','s4'], kind='video', intent='旧右',
                     motion_basis='动态', progression_plan='过程', semantic={})]
        return scenes, rows

    def proposal(self, cut='s2'):
        return dict(left_id='left', right_id='right', after_slide_id=cut,
                    reason='尾句补充前面的具体案例，后面才开始新话题', evidence_slide_ids=['s1','s2'],
                    left=dict(intent='完整案例', motion_basis='互动', progression_plan='提问与补充', semantic={}),
                    right=dict(intent='新的总结', motion_basis='转变', progression_plan='总结展开', semantic={}))

    def test_generic_dependency_cases_no_keywords(self):
        cases = [
            ['去日韩旅游？','汉奸、不爱国。','将军的炮火已经瞄准了南边。','最后落点是：','国内旅游。'],
            ['要不要换房？','这套房子便宜。','可它每天漏水。','接着说工作。','公司搬家了。'],
            ['她为什么离开？','因为没人支持她。','连最亲近的人也一样。','数年以后，','她回来了。'],
            ['实验出现亮光。','我们重复了三次。','每次都得到同样结果。','不过另一个实验不同。','它始终没有变化。'],
        ]
        for texts in cases:
            with self.subTest(texts=texts):
                scenes, rows = self.fixture(texts)
                before = copy.deepcopy(rows)
                result = review_boundaries({}, scenes, rows, lambda *_:{'moves':[self.proposal()]})
                self.assertEqual(result[0]['slide_ids'], ['s0','s1','s2'])
                self.assertEqual(result[1]['slide_ids'], ['s3','s4'])
                self.assertEqual(result[0]['intent'], '完整案例')
                self.assertEqual(rows, before)
                self.assertEqual([s for row in result for s in row['slide_ids']], [s['slide_id'] for s in scenes])

    def test_bad_or_overlong_move_does_not_destroy_draft(self):
        scenes, rows = self.fixture(['a','b','c','d','e'])
        for proposal in [self.proposal('missing'), dict(self.proposal(), evidence_slide_ids=['s1']),
                         dict(self.proposal(), right={})]:
            result = review_boundaries({}, scenes, rows, lambda *_:{'moves':[proposal]})
            self.assertEqual([r['slide_ids'] for r in result], [r['slide_ids'] for r in rows])
        scenes[2]['end'] = 16
        scenes[3].update(start=16,end=18)
        scenes[4].update(start=18,end=20)
        result = review_boundaries({}, scenes, rows, lambda *_:{'moves':[self.proposal()]})
        self.assertEqual(result[0]['slide_ids'], ['s0','s1'])

    def test_no_change_outage_and_resume_are_safe(self):
        scenes, rows = self.fixture(['a','b','c','d','e'])
        logs = []
        def unavailable(*_):
            raise ValueError('响应无法解析')
        saved = review_boundaries({}, scenes, rows, unavailable, progress=logs.append)
        self.assertTrue(any('保留原分组' in s for s in logs))
        self.assertEqual(saved[0]['intent'], '旧左')
        review_boundaries({}, scenes, saved, lambda *_:self.fail('恢复不应重复调用'))

    def test_cancel_not_swallowed(self):
        scenes, rows = self.fixture(['a','b','c','d','e'])
        def stopped(*_):
            raise RuntimeError('stopped')
        with self.assertRaisesRegex(RuntimeError,'stopped'):
            review_boundaries({}, scenes, rows, stopped)

    def test_graphic_pacing_not_sent_to_video_planner(self):
        scenes = [dict(slide_id='s0', text='独立句', start=0., end=5.)]
        received = []
        def ask(system,payload):
            received.append(payload)
            return {'shots':[dict(id='one',slide_ids=['s0'],kind='video',intent='目的',
                                 motion_basis='动态',progression_plan='过程',semantic={})]}
        plan_groups({},scenes,dict(visual_max_duration=12, visual_target_duration=8, visual_max_slides=6),ask=ask)
        self.assertNotIn('visual_max_duration',received[0]['arrangement'])
        self.assertNotIn('visual_target_duration',received[0]['arrangement'])
        self.assertEqual(received[0]['arrangement']['max_video_duration'],15)

    def test_automatic_pipeline_reviews_valid_duration_groups(self):
        scenes=[dict(slide_id=f's{i}',text=t,start=start,end=end) for i,(t,start,end) in enumerate([
            ('提问',0.,8.),('回答',8.,14.),('补充回答',14.,20.),('新问题',20.,23.),('新回答',23.,26.)])]
        rows=[dict(id='left',slide_ids=['s0'],kind='video',intent='旧左',motion_basis='动态',progression_plan='过程',semantic={}),
              dict(id='right',slide_ids=['s1','s2'],kind='video',intent='旧右',motion_basis='动态',progression_plan='过程',semantic={}),
              dict(id='last',slide_ids=['s3','s4'],kind='video',intent='新话题',motion_basis='动态',progression_plan='过程',semantic={})]
        requests, saved = [], []
        hints=[dict(slide_ids=['s0','s1','s2'],text='提问回答补充回答'),
               dict(slide_ids=['s3','s4'],text='新问题新回答')]
        def ask(system,payload):
            requests.append(payload)
            if 'boundaries' in payload:
                self.assertEqual(payload['narration_groups'],hints)
                move=self.proposal('s1')
                move['evidence_slide_ids']=['s0','s1']
                return {'moves':[move]}
            return {'shots':copy.deepcopy(rows)}
        result=plan_groups({},scenes,{'narration_groups':hints},ask=ask,
                           boundary_review=True,on_draft=lambda r:saved.append(copy.deepcopy(r)))
        self.assertEqual(len(requests),2)
        self.assertEqual(result[0]['slide_ids'],['s0','s1'])
        self.assertEqual(result[1]['slide_ids'],['s2'])
        self.assertEqual(result[2]['slide_ids'],['s3','s4'])
        self.assertEqual(saved[-1][0]['slide_ids'],['s0','s1'])
        resumed=plan_groups({},scenes,{'narration_groups':hints},resume_rows=saved[-1],
                            boundary_review=True,ask=lambda *_:self.fail('恢复时不能重复调用'))
        self.assertEqual(resumed,result)

    def test_confirmed_short_narration_chunk_is_hard_boundary(self):
        scenes, rows = self.fixture(['提问','回答','补充回答','新问题','新回答'])
        hints=[dict(slide_ids=['s0','s1','s2'],text='完整配音'),
               dict(slide_ids=['s3','s4'],text='下一段配音')]
        valid=[dict(rows[0],slide_ids=['s0','s1','s2']),dict(rows[1],slide_ids=['s3','s4'])]
        calls=[]
        def ask(_system,payload):
            calls.append(payload)
            return {'shots':copy.deepcopy(rows if len(calls)==1 else valid)}
        result=plan_groups({},scenes,{'narration_groups':hints},ask=ask)
        self.assertEqual(len(calls),2)
        self.assertIn('validation_errors',calls[1])
        self.assertEqual([r['slide_ids'] for r in result],[['s0','s1','s2'],['s3','s4']])

    def test_repeated_short_chunk_split_is_locally_coalesced(self):
        scenes, rows = self.fixture(['提问','回答','补充回答','新问题','新回答'])
        hints=[dict(slide_ids=['s0','s1','s2'],text='完整配音'),
               dict(slide_ids=['s3','s4'],text='下一段配音')]
        invalid=[dict(rows[0],id='a',slide_ids=['s0','s1']),
                 dict(rows[0],id='b',slide_ids=['s2']),
                 dict(rows[1],id='c',slide_ids=['s3','s4'])]
        calls=[]
        def ask(system,payload):
            calls.append(payload)
            return {'shots':copy.deepcopy(invalid)}
        logs=[]
        result=plan_groups({},scenes,{'narration_groups':hints},ask=ask,progress=logs.append)
        self.assertEqual([r['slide_ids'] for r in result],[['s0','s1','s2'],['s3','s4']])
        self.assertEqual(result[0]['intent'],'旧左')
        self.assertEqual(len(calls),2)
        self.assertTrue(any('自动撤销非法切口' in line for line in logs))

    def test_late_repair_cannot_reinsert_short_chunk_cut(self):
        scenes, rows = self.fixture(['提问','回答','补充回答','新问题','新回答'])
        hints=[dict(slide_ids=['s0','s1','s2'],text='完整配音'),
               dict(slide_ids=['s3','s4'],text='下一段配音')]
        valid=[dict(rows[0],id='a',slide_ids=['s0','s1','s2']),
               dict(rows[1],id='c',slide_ids=['s3','s4'])]
        # Missing metadata sends the first row through Agent 1B. Its bad reply
        # splits the short narration chunk again; the final invariant must win.
        valid[0]['intent']=''
        def ask(system,payload):
            if '指定的一个父镜头' in system:
                semantic=dict(message='局部含义',source_basis='局部原文',fact_status='question',
                              progression='依次表达',continuity_requirement='')
                return {'shots':[dict(rows[0],id='x',slide_ids=['s0','s1'],semantic=semantic),
                                 dict(rows[0],id='y',slide_ids=['s2'],semantic=semantic)]}
            return {'shots':copy.deepcopy(valid)}
        result=plan_groups({},scenes,{'narration_groups':hints},ask=ask)
        self.assertEqual([r['slide_ids'] for r in result],[['s0','s1','s2'],['s3','s4']])

    def test_agent_json_accepts_common_compatible_provider_shapes(self):
        from unittest.mock import patch
        cases=[('[{"id":"a"}]',['a']),
               ('"{\\"shots\\":[{\\"id\\":\\"b\\"}]}"',['b'])]
        for raw, expected in cases:
            with self.subTest(raw=raw), patch('backend.app.video_agents.generate_gemini_text',return_value=raw):
                self.assertEqual([r['id'] for r in ask_json('',{})['shots']],expected)

    def test_narration_hints_require_complete_text_match_not_clock(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            path=root/'other'/'tts_segments'/'manifest.json'
            path.parent.mkdir(parents=True)
            scenes,_ = self.fixture(['去日韩？','标签。','炮火。','总结：','国内。'])
            manifest={'segments':[{'text':'去日韩？标签。炮火。','start':100,'end':200}, {'text':'总结：国内。'}]}
            path.write_text(json.dumps(manifest,ensure_ascii=False),encoding='utf-8')
            groups=narration_groups(root,scenes)
            self.assertEqual(groups[0]['slide_ids'],['s0','s1','s2'])
            scenes[2]['text']='用户已经改文案了'
            self.assertEqual(narration_groups(root,scenes),[])
