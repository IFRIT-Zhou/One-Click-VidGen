import copy
import json
import tempfile
import unittest
from functools import partial
from pathlib import Path
from unittest.mock import Mock, patch
from types import SimpleNamespace

from backend.app.final_narration import final_narration_snapshot, remap_visual_groups, visual_playback_range
from backend.app.video_agents import _assemble_video_body, _finalize_prompts
from backend.app.video_audio_sync import sync_shot_timings


def scenes(texts):
    return [dict(slide_id=f'scene_{i+1:03d}', text=t, start=i*2, end=(i+1)*2) for i, t in enumerate(texts)]


class FinalNarrationTests(unittest.TestCase):
    def test_shared_commit_publishes_copy_in_both_video_modes(self):
        from backend.app.tts_editor import _commit_step_audio_edit
        from backend.app.pipeline import canonical_story_text
        for dynamic in (False, True):
            with self.subTest(dynamic=dynamic), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root/'other/tts_segments').mkdir(parents=True)
                (root/'input').mkdir()
                (root/'other/最终字幕.srt').write_text('1\n00:00:00,000 --> 00:00:02,000\n医生解说\n', encoding='utf-8')
                (root/'other/tts_segments/manifest.json').write_text(json.dumps({'segments':[{'text':'医生解说','tts_text':'yi1生解说'}]}), encoding='utf-8')
                job = SimpleNamespace(id='test', request={'dynamic_video':dynamic, 'script':'教授解说'})
                with patch('backend.app.tts_editor.store.update'), patch('backend.app.tts_editor.JOBS_DIR',root/'jobs'):
                    _commit_step_audio_edit(job, root)
                self.assertEqual(job.request['script'], '医生解说')
                self.assertEqual(canonical_story_text(job.request, []), '医生解说')
                self.assertEqual((root/'input/文案.txt').read_text(encoding='utf-8'), '医生解说')
                job.request['script'] = '用户新任务文案'
                self.assertEqual(canonical_story_text(job.request, []), '用户新任务文案')

    def test_illustrated_images_rebind_after_split_and_keep_paid_files(self):
        from backend.app.visual_editor import VisualEditor
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            (root/'other').mkdir()
            mapping = [dict(macro_scene_id='a',includes_slides=['scene_001'],image_prompt='keep a'),
                       dict(macro_scene_id='b',includes_slides=['scene_002'],image_prompt='keep b')]
            (root/'other/画面映射.json').write_text(json.dumps(mapping),encoding='utf-8')
            with patch.object(VisualEditor,'_write_timing_html') as html:
                VisualEditor.sync_final_narration(root,scenes(['甲乙','丙']),scenes(['甲','乙','丙']))
            updated = json.loads((root/'other/画面映射.json').read_text(encoding='utf-8'))
            self.assertEqual(updated[0]['includes_slides'],['scene_001','scene_002'])
            self.assertEqual(updated[1]['includes_slides'],['scene_003'])
            self.assertEqual(updated[0]['image_prompt'],'keep a')
            self.assertTrue(updated[0]['audio_timing_adjustment']['needs_review'])
            self.assertNotIn('audio_timing_adjustment',updated[1])
            self.assertEqual(html.call_args.args[2][-1]['text_content'],'丙')

    def test_pause_stays_in_visual_span_without_changing_subtitles(self):
        shots = [dict(start=.2,end=2),dict(start=2.6,end=4.6)]
        self.assertEqual(visual_playback_range(shots,0,5), dict(start=0,end=2.6,duration=2.6))
        self.assertEqual(visual_playback_range(shots,1,5), dict(start=2.6,end=5,duration=2.4))
        self.assertEqual(shots[0]['end'],2)

    def test_snapshot_uses_recorded_copy_not_phonetics_or_caption_notes(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            (root/'other/tts_segments').mkdir(parents=True)
            data = {'segments': [dict(text='医生（主治医师）', subtitle_independent=True,
                                      subtitle_source_text='医生', tts_text='yi1 sheng1'),
                                 dict(text='新的说明', tts_text='新的说ming2')]}
            (root/'other/tts_segments/manifest.json').write_text(json.dumps(data), encoding='utf-8')
            snapshot = final_narration_snapshot(root, scenes(['医生（主治医师）', '新的说明']))
            self.assertEqual(snapshot['script'], '医生\n新的说明')
            self.assertEqual(snapshot['scenes'][0]['text'], '医生（主治医师）')
            self.assertEqual(snapshot['duration'], 4)

    def test_stale_archive_cannot_override_final_timeline(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            (root/'other/tts_segments').mkdir(parents=True)
            (root/'other/tts_segments/manifest.json').write_text(json.dumps({'segments':[{'text':'旧文'}]}), encoding='utf-8')
            self.assertEqual(final_narration_snapshot(root, scenes(['新文']))['script'], '新文')

    def test_split_cue_preserves_shots_by_text_not_renumbered_ids(self):
        before, after = scenes(['甲乙', '丙丁']), scenes(['甲', '乙', '丙丁'])
        groups = [dict(id='a', slide_ids=['scene_001'], video='paid-a.mp4'), dict(id='b', slide_ids=['scene_002'], video='paid-b.mp4')]
        result, detached = remap_visual_groups(before, after, groups)
        self.assertEqual([s['slide_ids'] for s in result], [['scene_001','scene_002'], ['scene_003']])
        self.assertEqual([s['video'] for s in result], ['paid-a.mp4', 'paid-b.mp4'])
        self.assertFalse(detached)
        self.assertEqual(groups[1]['slide_ids'], ['scene_002'])

    def test_local_word_edit_and_split_keep_subtitle_unit_partition(self):
        before, after = scenes(['医生讲解内容', '后续内容']), scenes(['医生新增讲解', '内容', '后续内容'])
        groups = [dict(slide_ids=['scene_001']), dict(slide_ids=['scene_002'])]
        result, _ = remap_visual_groups(before, after, groups)
        self.assertEqual(result[1]['slide_ids'], ['scene_003'])
        self.assertEqual([sid for g in result for sid in g['slide_ids']], [s['slide_id'] for s in after])

    def test_merged_cues_retain_unused_assets_without_fake_timestamps(self):
        groups = [dict(id='a', slide_ids=['scene_001'], video='a.mp4'), dict(id='b', slide_ids=['scene_002'], video='b.mp4')]
        result, detached = remap_visual_groups(scenes(['甲','乙']), scenes(['甲乙']), groups)
        self.assertEqual(len(result), 1)
        self.assertEqual(len(detached), 1)
        self.assertEqual({g['video'] for g in result+detached}, {'a.mp4','b.mp4'})

    def test_invalid_old_partition_is_not_silently_reinterpreted(self):
        with self.assertRaisesRegex(ValueError, '对应关系不完整'):
            remap_visual_groups(scenes(['甲','乙']), scenes(['甲','乙']), [dict(slide_ids=['scene_002'])])

    def test_repeated_sync_keeps_unreviewed_semantic_flag(self):
        old = scenes(['原话'])
        shot = dict(id='a', slide_ids=['scene_001'], start=0, end=2, duration=2, kind='video', video='paid.mp4')
        record = dict(scenes=old, shots=[shot])
        new = scenes(['新话'])
        sync_shot_timings(record, new, Path('absent'), Path('absent'))
        record['scenes'] = new
        sync_shot_timings(record, new, Path('absent'), Path('absent'))
        self.assertTrue(record['shots'][0]['audio_timing_adjustment']['text_changed'])
        self.assertEqual(record['shots'][0]['video'], 'paid.mp4')


class FinalizerEmptyFieldTests(unittest.TestCase):
    def setUp(self):
        self.shot = dict(id='shot-3', kind='video', motion_plan=dict(version=2, scene_anchor='厨房', participants=['鸡蛋'],
            reference_beat=1, reference_visual='鸡蛋', reference_participants=['鸡蛋'], reference_texts=[],
            beats=[dict(action='鸡蛋出现裂纹', texts=[]), dict(action='鸡蛋裂开', texts=[])]))
        self.row = dict(id='shot-3', continuity_prompt='', beat_prompts=[dict(beat=1,prompt='鸡蛋出现裂纹'),
                         dict(beat=2,prompt='')], ending_prompt=None)

    def test_retry_reports_exact_missing_fields_then_uses_existing_plan(self):
        ask = Mock(side_effect=lambda *_: {'shots': [copy.deepcopy(self.row)]})
        rows = _finalize_prompts('', {'story_context': {}}, [self.shot], 'video_prompt', 'video', ['shot-3'],
                                 ask, assemble=_assemble_video_body,
                                 assemble_retry=partial(_assemble_video_body, allow_plan_fallback=True))
        self.assertEqual(ask.call_count, 2)
        error = ask.call_args.args[1]['validation_errors'][0]
        self.assertIn('continuity_prompt', error)
        self.assertIn('beat_prompts[1].prompt', error)
        self.assertIn('ending_prompt', error)
        self.assertIn('鸡蛋裂开', rows[0]['video_prompt'])
        self.assertIn('不重演动作', rows[0]['video_prompt'])

    def test_missing_phase_and_invalid_plan_remain_blocked(self):
        row = copy.deepcopy(self.row)
        row['beat_prompts'].pop()
        with self.assertRaisesRegex(ValueError, '阶段缺失'):
            _assemble_video_body(self.shot, row, allow_plan_fallback=True)
        shot = copy.deepcopy(self.shot)
        shot['motion_plan']['beats'][1]['action'] = ''
        with self.assertRaisesRegex(ValueError, '可见变化'):
            _assemble_video_body(shot, copy.deepcopy(self.row), allow_plan_fallback=True)


if __name__ == '__main__':
    unittest.main()
