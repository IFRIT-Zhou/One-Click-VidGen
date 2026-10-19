import copy
import unittest
from contextlib import ExitStack, nullcontext
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from backend.app import video_agents as agents, video_studio as studio
from backend.app.video_auto_split import split_window, plan_split, merge_split
from backend.app.video_plan import normalize_shots, edit_structure
from backend.app.video_duration_limits import validate_video_duration


def fixture():
    scenes, start = [], 0
    for i, seconds in enumerate([6, 8, 8, 6, 5]):
        scenes.append(dict(slide_id=f's{i}', start=start, end=start+seconds, text=f'阶段{i}的独立内容。'))
        start += seconds
    rows = [dict(id=key, slide_ids=ids, kind='video', reference_ids=[], intent='父镜旧意图',
                 user_intent='旧整段要求', image_prompt='旧全景', video_prompt='图1旧全景',
                 action='旧动作', visual_description='旧构图', visual_design={})
            for key, ids in [('before',['s0']),('target',['s1','s2']),('after',['s3']),('keep',['s4'])]]
    shots = normalize_shots(rows, scenes)
    for shot in shots:
        shot.update(image='old.jpg',image_status='completed',video_status='completed',video='old.mp4',
                    video_generation_options={'backend':'cloud_h3'})
    return dict(id='project',revision=1,status='video_review',scenes=scenes,shots=shots,references=[],logs=[],
                audio='audio.wav',context={},settings={'style':'统一风格'},creation_parameters={'dynamic_auto_advance':True})


def planners(stack):
    # Force deterministic semantic-boundary fallback; no remote calls.
    stack.enter_context(patch.object(agents,'ask_json',return_value={'shots':[]}))
    def core(context, scenes, rows, refs):
        for row in rows:
            assert '旧' not in str(row)
        return [dict(row,visual_description='新画面'+row['slide_ids'][0],visual_design={}) for row in rows]
    spy=stack.enter_context(patch.object(agents,'design_core_images',side_effect=core))
    plan=dict(version=1,scene_anchor='新场景',participants=[],reference_beat=1,reference_visual='新画面',beats=[dict(action='变化',texts=[])])
    stack.enter_context(patch.object(agents,'direct_motion',side_effect=lambda ctx,rows,refs:[dict(row,action='新动作'+row['slide_ids'][0],motion_plan=plan) for row in rows]))
    stack.enter_context(patch.object(agents,'write_image_prompts',side_effect=lambda ctx,style,rows,refs:[dict(row,image_prompt='新图'+row['slide_ids'][0]) for row in rows]))
    stack.enter_context(patch.object(agents,'write_video_prompts',side_effect=lambda ctx,rows,refs:[dict(row,video_prompt='图1核心图；新动作'+row['slide_ids'][0]) for row in rows]))
    return spy


class AutoSplitTests(unittest.TestCase):
    def test_long_static_can_become_dynamic_then_split_without_regenerating_audio_or_images(self):
        record=fixture();record['status']='image_review'
        target=record['shots'][1]
        target.update(kind='static',generation_duration=None,action='',video_prompt='')
        target.pop('video',None)
        before=copy.deepcopy(record)
        with ExitStack() as stack:
            planners(stack)
            stack.enter_context(patch.object(studio,'require_user',return_value={'id':1}))
            stack.enter_context(patch.object(studio,'directory',return_value=Path('project')))
            stack.enter_context(patch.object(studio,'read',side_effect=lambda p:copy.deepcopy(record)))
            def persist(path,value):
                record.clear();record.update(copy.deepcopy(value))
            stack.enter_context(patch.object(studio,'save',side_effect=persist))
            stack.enter_context(patch.object(studio,'project_language_scope',return_value=nullcontext()))
            render=stack.enter_context(patch('module4_video_render._render_poster_with_retry'))
            edited=studio.edit_shot_motion('project','target',studio.ShotMotionEdit(revision=1,kind='video'),None)
            self.assertEqual(edited['shots'][1]['kind'],'video')
            self.assertEqual(edited['shots'][1]['image'],before['shots'][1]['image'])
            self.assertIn('14',edited['shots'][1]['warning'])
            # Saving a draft is allowed; sending the unsplit draft is still not.
            with self.assertRaisesRegex(ValueError,'14 秒'):
                validate_video_duration(edited['shots'][1]['duration'],{'video_generation_backend':'cloud_h3'})
            result=studio.auto_split_shot('project','target',studio.AutoSplit(revision=edited['revision']),None)
            render.assert_not_called()
        children=[s for s in result['shots'] if s['id'] in result['auto_split_result']['shot_ids']]
        self.assertGreater(len(children),1)
        self.assertTrue(all(s['kind']=='video' and s['generation_duration']<=14 for s in children))
        self.assertTrue(all(s['image_status']=='pending' and not s.get('image') for s in children))
        self.assertEqual(result['scenes'],before['scenes']);self.assertEqual(result['audio'],before['audio'])
        self.assertEqual(result['shots'][0],before['shots'][0]);self.assertEqual(result['shots'][-1],before['shots'][-1])

    def test_long_motion_edit_warns_only_for_real_cluster_route_and_static_clears_warning(self):
        for backend in ['cloud_h3','api','comfyui']:
            record=fixture();record['status']='image_review'
            shot=record['shots'][1];shot.update(kind='static',duration=17.243,generation_duration=None,action='',video_prompt='')
            shot['video_generation_options']={'backend':backend}
            with self.subTest(backend=backend),patch.object(studio,'require_user',return_value={'id':1}),patch.object(studio,'directory',return_value=Path('project')),patch.object(studio,'read',return_value=record),patch.object(studio,'save'):
                result=studio.edit_shot_motion('project','target',studio.ShotMotionEdit(revision=1,kind='video'),None)
                self.assertEqual(result['shots'][1]['generation_duration'],18)
                self.assertEqual(bool(result['shots'][1]['warning']),backend=='cloud_h3')
                result=studio.edit_shot_motion('project','target',studio.ShotMotionEdit(revision=result['revision'],kind='static'),None)
                self.assertEqual(result['shots'][1]['warning'],'')

    def test_request_limit_14_and_rounding(self):
        self.assertEqual(validate_video_duration(13.999,{'video_generation_backend':'cloud_h3'}),14)
        self.assertEqual(validate_video_duration(14,{'video_generation_backend':'cloud_h3'}),14)
        with self.assertRaisesRegex(ValueError,'14 秒'):
            validate_video_duration(14.001,{'video_generation_backend':'cloud_h3'})

    def test_both_scopes_replan_siblings_without_old_design_and_preserve_timeline(self):
        for neighbors, expected in [(False,['s1','s2']),(True,['s0','s1','s2','s3'])]:
            record=fixture();before=copy.deepcopy(record)
            with self.subTest(neighbors=neighbors),ExitStack() as stack:
                spy=planners(stack)
                children=plan_split(record,'target',neighbors)
                self.assertEqual([sid for row in children for sid in row['slide_ids']],expected)
                self.assertTrue(all(row['generation_duration']<=14 and row['image_status']=='pending' for row in children))
                self.assertTrue(all('image' not in row and 'video' not in row for row in children))
                self.assertEqual(len({row['video_prompt'] for row in children}),len(children))
                self.assertEqual(spy.call_count,1)
                merged=merge_split(record,before,'target',neighbors,children)
                self.assertEqual(merged[-1],record['shots'][-1])
                self.assertEqual(record,before)

    def test_static_neighbors_and_single_caption_are_not_silently_converted(self):
        record=fixture();record['shots'][0]['kind']='static'
        self.assertEqual(split_window(record,'target',True)[:2],(1,3))
        record['scenes'][1]['end']=23
        with self.assertRaisesRegex(ValueError,'单条超过14秒'):
            split_window(record,'target')

    def test_scope_conflicts_rejected_but_unrelated_completion_preserved(self):
        snapshot=fixture()
        with ExitStack() as stack:
            planners(stack);children=plan_split(snapshot,'target')
        current=copy.deepcopy(snapshot);current['shots'][-1]['video']='new.mp4';current['revision']+=1
        self.assertEqual(merge_split(current,snapshot,'target',False,children)[-1]['video'],'new.mp4')
        current['shots'][1]['intent']='用户新修改'
        with self.assertRaisesRegex(ValueError,'已改变'):
            merge_split(current,snapshot,'target',False,children)

    def test_manual_split_does_not_clone_old_user_intent(self):
        record=fixture()
        rows=edit_structure(record['shots'],record['scenes'],'split',1,1,[])
        self.assertEqual([row['source_subtitles'][0]['slide_id'] for row in rows[1:3]],['s1','s2'])
        self.assertTrue(all(not row.get('user_intent') for row in rows[1:3]))
        self.assertEqual(rows[0]['user_intent'],'旧整段要求')

    def test_manual_split_then_full_refresh_uses_each_child_subtitles(self):
        from backend.app.video_prompt_refresh import refresh
        from backend.app import video_prompt_refresh as refresh_module
        record=fixture()
        children=edit_structure(record['shots'],record['scenes'],'split',1,1,[])[1:3]
        seen=[]
        def core(ctx,scenes,rows,refs):
            row=rows[0];seen.append(copy.deepcopy(row))
            return [dict(id=row['id'],intent=row['source_subtitles'][0]['text'],reference_ids=[],visual_description='新画面')]
        with patch.object(refresh_module,'design_core_images',side_effect=core),patch.object(refresh_module,'direct_motion',return_value=[dict(action='新动态',motion_plan={})]),patch.object(refresh_module,'write_image_prompts',side_effect=lambda ctx,style,rows,refs:[dict(image_prompt=rows[0]['intent'])]),patch.object(refresh_module,'write_video_prompts',side_effect=lambda ctx,rows,refs:[dict(video_prompt=rows[0]['intent'])]):
            updated=[refresh({},'',child,[],basis='full',action='',image_prompt='',scenes=record['scenes'])[0] for child in children]
        self.assertNotEqual(updated[0]['video_prompt'],updated[1]['video_prompt'])
        self.assertEqual([[s['slide_id'] for s in row['source_subtitles']] for row in seen],[['s1'],['s2']])
        self.assertTrue(all(not row.get('user_intent') and '旧构图' not in str(row) for row in seen))

    def test_busy_neighbor_rejected_before_planning(self):
        record=fixture();record['shots'][0]['video_queued']=True
        with patch.object(studio,'require_user',return_value={'id':1}),patch.object(studio,'directory',return_value=Path('project')),patch.object(studio,'read',return_value=record),patch('backend.app.video_auto_split.plan_split') as planner:
            with self.assertRaises(HTTPException):
                studio.auto_split_shot('project','target',studio.AutoSplit(revision=1,include_neighbors=True),None)
            planner.assert_not_called()

    def test_route_success_atomic_backup_and_failure_no_write(self):
        for fail in (False,True):
            record=fixture();saved=[]
            with self.subTest(fail=fail),ExitStack() as stack:
                planners(stack)
                stack.enter_context(patch.object(studio,'require_user',return_value={'id':1}))
                stack.enter_context(patch.object(studio,'directory',return_value=Path('project')))
                stack.enter_context(patch.object(studio,'read',side_effect=lambda p:copy.deepcopy(record)))
                stack.enter_context(patch.object(studio,'save',side_effect=lambda p,r:saved.append(copy.deepcopy(r))))
                stack.enter_context(patch.object(studio,'project_language_scope',return_value=nullcontext()))
                if fail:
                    stack.enter_context(patch.object(agents,'write_video_prompts',side_effect=ValueError('规划失败')))
                    with self.assertRaises(HTTPException):
                        studio.auto_split_shot('project','target',studio.AutoSplit(revision=1),None)
                    self.assertEqual(saved,[])
                else:
                    result=studio.auto_split_shot('project','target',studio.AutoSplit(revision=1),None)
                    self.assertEqual(result['structure_history'][-1]['shots'],record['shots'])
                    self.assertEqual(result['scenes'],record['scenes'])
                    self.assertEqual(result['audio'],'audio.wav')
                    self.assertFalse(result['creation_parameters']['dynamic_auto_advance'])
                    self.assertEqual(len(saved),1)
                self.assertNotIn(studio.image_edit_key('project','target'),studio.IMAGE_EDITS)


if __name__=='__main__':unittest.main()
