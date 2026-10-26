import copy
import json
import shutil
import tempfile
import unittest
import wave
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend.app.tts_editor import TtsEditor, _concat_segment_wavs, _write_srt_entries
from backend.app.tts_structure_edit import prepare, execute, preview, start
from backend.app.narration_structure import cues_by_segment
from backend.app.final_narration import remap_visual_groups, final_narration_snapshot
from backend.app.video_audio_sync import sync_shot_timings


def wav(path, seconds=2, value=1):
    with wave.open(str(path), 'wb') as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(8000)
        out.writeframes(int(value).to_bytes(2, 'little')*round(seconds*8000))


class TtsStructureTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.seg = self.root/'other/tts_segments'
        self.seg.mkdir(parents=True)
        (self.root/'input').mkdir()
        self.editor = TtsEditor()
        self.job = SimpleNamespace(id='job', user_id=1, request={'script':'甲。乙。丙。','dynamic_video': True})
        self.stack.enter_context(patch.object(self.editor, '_project_dir', return_value=self.root))
        self.stack.enter_context(patch('backend.app.tts_editor.store.update'))
        self.stack.enter_context(patch('backend.app.tts_editor.store.log'))
        self.stack.enter_context(patch('backend.app.tts_editor.JOBS_DIR', self.root/'jobs'))
        self.stack.enter_context(patch('backend.app.tts_editor.is_step_workflow_v2', return_value=False))
        self.stack.enter_context(patch('backend.app.tts_editor._refine_subtitle_timing', side_effect=lambda p,r,*_: (r,0)))
        self.rows = [dict(slide_id=f'scene_{i:03d}',text_content=t,start=(i-1)*2,end=i*2)
                     for i,t in enumerate(['甲。','乙。','丙。'],1)]
        self.segments = []
        for i,row in enumerate(self.rows,1):
            wav(self.seg/f'{i}.wav', value=i)
            self.segments.append(dict(index=i,text=row['text_content'],tts_text=row['text_content'],
                                      filename=f'{i}.wav',start=row['start'],end=row['end'],duration=2,pause_after=0))
        self.manifest = dict(revision=1,engine='indextts25',segments=self.segments)
        (self.seg/'manifest.json').write_text(json.dumps(self.manifest),encoding='utf-8')
        (self.root/'other/画面时间线.json').write_text(json.dumps(self.rows),encoding='utf-8')
        _write_srt_entries(self.root/'other/最终字幕.srt',[dict(r,text=r['text_content']) for r in self.rows])
        _concat_segment_wavs(self.segments,self.seg,self.root/'input/配音.wav')
        (self.root/'input/文案.txt').write_text('甲。乙。丙。',encoding='utf-8')

    def run_edit(self, **kwargs):
        data = dict(action='delete',indices=[2],expected_revision=self.editor.revision('job',1), **kwargs)
        execute(self.editor,self.job,1,data,prepare(self.editor,self.job,1,data))
        self.assertEqual(self.editor.status('job')['status'],'completed',self.editor.status('job'))
        return json.loads((self.root/'other/画面时间线.json').read_text(encoding='utf-8'))

    def test_delete_no_tts_and_preserve_files_with_exact_remapping(self):
        with patch.object(self.editor,'_synthesize_parts') as synth:
            result=self.run_edit()
        synth.assert_not_called()
        self.assertTrue((self.seg/'2.wav').is_file())
        self.assertEqual([r['text_content'] for r in result],['甲。','丙。'])
        self.assertEqual([(r['start'],r['end']) for r in result],[(0,2),(2,4)])
        groups=[dict(id=f's{i}',slide_ids=[r['slide_id']],video=f'paid{i}.mp4') for i,r in enumerate(self.rows)]
        retained,detached=remap_visual_groups(self.rows,result,groups)
        self.assertEqual([r['id'] for r in retained],['s0','s2'])
        self.assertEqual(detached[0]['id'],'s1')
        self.assertEqual(self.job.request['script'],'甲。\n丙。')

    def test_delete_non_contiguous_and_all_rejected(self):
        data=dict(action='delete',indices=[1,2,3],expected_revision=1)
        with self.assertRaisesRegex(ValueError,'至少保留'):
            prepare(self.editor,self.job,1,data)
        data['indices']=[1,3]
        execute(self.editor,self.job,1,data,prepare(self.editor,self.job,1,data))
        self.assertEqual(self.editor.status('job')['status'],'completed')
        rows=json.loads((self.root/'other/画面时间线.json').read_text(encoding='utf-8'))
        self.assertEqual(rows[0]['text_content'],'乙。')
        self.assertEqual(rows[0]['start'],0)

    def test_insert_only_new_text_and_placeholder_inside_old_shot(self):
        generated=self.root/'new.wav'
        wav(generated,1)
        data=dict(action='insert',anchor_index=1,position='after',text='新增，说明。',pause_after=.2,expected_revision=1)
        with patch.object(self.editor,'_synthesize_parts',return_value=[generated]) as synth:
            execute(self.editor,self.job,1,data,prepare(self.editor,self.job,1,data))
        self.assertEqual(self.editor.status('job')['status'],'completed',self.editor.status('job'))
        self.assertEqual(synth.call_args.kwargs['texts'],['新增，说明。'])
        rows=json.loads((self.root/'other/画面时间线.json').read_text(encoding='utf-8'))
        self.assertAlmostEqual(rows[-1]['end'],7.2)
        groups=[dict(id='a',slide_ids=[r['slide_id'] for r in self.rows],video='paid.mp4')]
        result,detached=remap_visual_groups(self.rows,rows,groups)
        self.assertEqual(len(result),3)
        self.assertTrue(result[1]['placeholder'])
        self.assertEqual(result[0]['video'],'paid.mp4')
        self.assertEqual(result[2]['narration_parent_visual_id'],'a')
        self.assertFalse(detached)

    def test_undo_restores_manifest_audio_script_and_monotonic_revision(self):
        original=(self.root/'input/配音.wav').read_bytes()
        self.run_edit()
        self.editor.undo(job=self.job,user_id=1)
        self.assertEqual((self.root/'input/配音.wav').read_bytes(),original)
        self.assertEqual(self.editor.revision('job',1),3)
        self.assertEqual(self.job.request['script'],'甲。乙。丙。')

    def test_failed_commit_rolls_back_all_audio_and_script(self):
        original=(self.root/'input/配音.wav').read_bytes()
        data=dict(action='delete',indices=[2],expected_revision=1)
        with patch('backend.app.tts_editor._commit_step_audio_edit',side_effect=OSError('模拟写入失败')):
            execute(self.editor,self.job,1,data,prepare(self.editor,self.job,1,data))
        self.assertEqual(self.editor.status('job')['status'],'failed')
        self.assertEqual((self.root/'input/配音.wav').read_bytes(),original)
        self.assertEqual(self.editor.revision('job',1),1)
        self.assertEqual(self.job.request['script'],'甲。乙。丙。')

    def test_stale_revision_and_running_edit_rejected_before_synthesis(self):
        data=dict(action='delete',indices=[2],expected_revision=0)
        with self.assertRaisesRegex(RuntimeError,'刷新'):
            prepare(self.editor,self.job,1,data)
        self.editor._set_task('job',status='running')
        data['expected_revision']=1
        with self.assertRaisesRegex(RuntimeError,'等待'):
            start(self.editor,self.job,1,data)

    def test_legacy_cue_crossing_tts_boundary_is_split_not_removed_whole(self):
        rows=[dict(slide_id='scene_001',text_content='甲。乙。',start=0,end=4),self.rows[2]]
        grouped=cues_by_segment(rows,self.segments)
        self.assertEqual(grouped[1][0]['text'],'甲。')
        self.assertEqual(grouped[2][0]['text'],'乙。')
        self.assertEqual(grouped[1][0]['end'],2)
        self.assertEqual(grouped[2][0]['start'],2)

    def test_two_deletions_still_map_to_original_subtitle_identity(self):
        self.run_edit()
        data=dict(action='delete',indices=[1],expected_revision=2)
        execute(self.editor,self.job,1,data,prepare(self.editor,self.job,1,data))
        rows=json.loads((self.root/'other/画面时间线.json').read_text(encoding='utf-8'))
        groups=[dict(id=f's{i}',slide_ids=[r['slide_id']]) for i,r in enumerate(self.rows)]
        retained,detached=remap_visual_groups(self.rows,rows,groups)
        self.assertEqual([r['id'] for r in retained],['s2'])
        self.assertEqual(len(detached),2)

    def test_shifted_neighbor_is_not_marked_as_semantically_changed(self):
        rows=self.run_edit()
        record=dict(scenes=[dict(r,text=r['text_content']) for r in self.rows],shots=[
            dict(id=f's{i}',kind='video',start=r['start'],end=r['end'],slide_ids=[r['slide_id']],video='paid.mp4')
            for i,r in enumerate(self.rows)])
        with patch('backend.app.video_audio_sync._signature',return_value=(1,2,8000,b'same')):
            sync_shot_timings(record,[dict(r,text=r['text_content']) for r in rows],Path('old'),Path('new'))
        self.assertEqual([s['id'] for s in record['shots']],['s0','s2'])
        self.assertNotIn('audio_timing_adjustment',record['shots'][1])

    def test_illustrated_delete_and_undo_restore_images_mapping_and_html(self):
        from backend.app.visual_editor import VisualEditor, HTML_FILENAME
        self.job.request['dynamic_video'] = False
        (self.root/'image').mkdir()
        mapping = [dict(macro_scene_id=f'm{i}', includes_slides=[r['slide_id']], image_prompt='原图')
                   for i,r in enumerate(self.rows)]
        target = self.root/'other/画面映射.json'
        target.write_text(json.dumps(mapping), encoding='utf-8')
        for item in mapping:
            VisualEditor._write_black_placeholder(self.root/'image'/f"{item['macro_scene_id']}.png")
        self.run_edit()
        updated = json.loads(target.read_text(encoding='utf-8'))
        self.assertEqual([m['macro_scene_id'] for m in updated], ['m0','m2'])
        self.assertTrue((self.root/'image/m1.png').is_file())
        self.assertTrue((self.root/'other'/HTML_FILENAME).is_file())
        self.editor.undo(job=self.job,user_id=1)
        self.assertEqual(json.loads(target.read_text(encoding='utf-8')),mapping)

    def test_illustrated_insert_preserves_existing_picture_and_new_placeholder(self):
        from backend.app.visual_editor import VisualEditor
        self.job.request['dynamic_video'] = False
        (self.root/'image').mkdir()
        mapping = [dict(macro_scene_id='whole', includes_slides=[r['slide_id'] for r in self.rows], image_prompt='原图')]
        target = self.root/'other/画面映射.json'
        target.write_text(json.dumps(mapping), encoding='utf-8')
        VisualEditor._write_black_placeholder(self.root/'image/whole.png')
        generated = self.root/'new.wav'
        wav(generated,1)
        data=dict(action='insert',anchor_index=1,position='after',text='新内容。',expected_revision=1)
        with patch.object(self.editor,'_synthesize_parts',return_value=[generated]):
            execute(self.editor,self.job,1,data,prepare(self.editor,self.job,1,data))
        self.assertEqual(self.editor.status('job')['status'],'completed',self.editor.status('job'))
        updated = json.loads(target.read_text(encoding='utf-8'))
        self.assertEqual(len(updated),3)
        placeholder = self.root/'image'/f"{updated[1]['macro_scene_id']}.png"
        self.assertTrue(placeholder.is_file())
        self.assertTrue(updated[1]['placeholder'])
        self.assertEqual((self.root/'image'/f"{updated[2]['macro_scene_id']}.png").read_bytes(),
                         (self.root/'image/whole.png').read_bytes())
        # A later save must not overwrite a newly redrawn inserted image.
        VisualEditor._write_black_placeholder(placeholder,64,36)
        redrawn=placeholder.read_bytes()
        snapshot=final_narration_snapshot(self.root)
        VisualEditor.sync_final_narration(self.root,snapshot['scenes'],snapshot['scenes'])
        self.assertEqual(placeholder.read_bytes(),redrawn)

    def test_dynamic_sync_and_undo_restore_removed_shot_without_regeneration(self):
        from backend.app import video_studio as studio
        project=self.root/'video'
        (project/'assets').mkdir(parents=True)
        shutil.copy2(self.root/'input/配音.wav',project/'assets/audio.wav')
        snapshot=final_narration_snapshot(self.root)
        record=dict(id='v',revision=1,status='completed',scenes=snapshot['scenes'],
                    audio='assets/audio.wav',source_project={'id':'job'},logs=[],final_narration=snapshot,
                    shots=[dict(id=f's{i}',kind='video',slide_ids=[r['slide_id']],start=r['start'],end=r['end'],
                                image_status='completed',video_status='completed',video=f'paid{i}.mp4')
                           for i,r in enumerate(self.rows)])
        self.run_edit()
        with patch.object(studio,'require_user',return_value={'id':1}), \
             patch.object(studio,'directory',return_value=project), \
             patch.object(studio,'read',side_effect=lambda _:copy.deepcopy(record)), \
             patch.object(studio,'editable'), patch.object(studio,'save'), \
             patch.object(studio,'narration_groups',return_value=[]), \
             patch.object(self.editor,'commit_step_review'), \
             patch('backend.app.video_sources.dependencies',return_value=(SimpleNamespace(get=lambda _:self.job),None,None)), \
             patch('backend.app.tts_editor.tts_editor',self.editor):
            record=studio.sync_edited_audio('v',studio.Review(revision=1),None)
            self.assertEqual([s['id'] for s in record['shots']],['s0','s2'])
            self.editor.undo(job=self.job,user_id=1)
            record=studio.sync_edited_audio('v',studio.Review(revision=record['revision']),None)
        self.assertEqual([s['id'] for s in record['shots']],['s0','s1','s2'])
        self.assertEqual([s['video'] for s in record['shots']],['paid0.mp4','paid1.mp4','paid2.mp4'])
        self.assertEqual(record['scenes'],snapshot['scenes'])

    def test_insert_failure_keeps_original_audio_and_segments(self):
        original=(self.root/'input/配音.wav').read_bytes()
        data=dict(action='insert',anchor_index=1,position='before',text='新增。',expected_revision=1)
        with patch.object(self.editor,'_synthesize_parts',side_effect=RuntimeError('上游失败')):
            execute(self.editor,self.job,1,data,prepare(self.editor,self.job,1,data))
        self.assertEqual(self.editor.status('job')['status'],'failed')
        self.assertEqual(self.editor.revision('job',1),1)
        self.assertEqual((self.root/'input/配音.wav').read_bytes(),original)

    def test_independent_captions_hidden_cues_and_pause_are_preserved(self):
        self.rows[0]['text_content']='甲（注释）。'
        self.rows[0]['subtitle_hidden']=True
        self.segments[0].update(text='甲（注释）。',subtitle_independent=True,subtitle_source_text='甲。')
        self.segments[1]['pause_after']=.5
        self.segments[2].update(start=4.5,end=6.5)
        self.rows[2].update(start=4.5,end=6.5)
        (self.seg/'manifest.json').write_text(json.dumps(self.manifest),encoding='utf-8')
        (self.root/'other/画面时间线.json').write_text(json.dumps(self.rows),encoding='utf-8')
        result=self.run_edit()
        self.assertEqual(result[-1]['start'],2)
        self.assertTrue(result[0]['subtitle_hidden'])
        self.assertEqual(final_narration_snapshot(self.root)['script'],'甲。\n丙。')

    def test_delete_then_boundary_edit_remaps_by_original_identity(self):
        old=self.run_edit()
        new=self.editor._resegment_rows(old,0,2,[dict(start=0,end=1),dict(start=1,end=2)],['甲','。'],'甲。')
        for i,row in enumerate(new,1):
            row['slide_id']=f'scene_{i:03d}'
        groups=[dict(id=f's{i}',slide_ids=[r['slide_id']]) for i,r in enumerate(old)]
        retained,_=remap_visual_groups(old,new,groups)
        self.assertEqual(retained[0]['slide_ids'],['scene_001','scene_002'])
        self.assertEqual(retained[1]['slide_ids'],['scene_003'])

    def test_current_identity_takes_precedence_over_shared_ancestor(self):
        rows=[dict(slide_id='a',text='甲',start=0,end=1,narration_unit_id='parent',narration_structure=True),
              dict(slide_id='b',text='乙',start=1,end=2,narration_unit_id='child',narration_structure=True,
                   narration_ancestors=['parent'])]
        groups=[dict(id='first',slide_ids=['a']),dict(id='second',slide_ids=['b'])]
        retained,_=remap_visual_groups(rows,copy.deepcopy(rows),groups)
        self.assertEqual(retained,groups)


if __name__=='__main__':
    unittest.main()
