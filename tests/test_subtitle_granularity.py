import copy
import json
import tempfile
import unittest
import wave
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend.app.subtitle_granularity import punctuation_cuts, resegment_cues, restore_coarse_cues
from backend.app.tts_editor import TtsEditor, _refine_subtitle_timing, _srt_entries, _write_srt_entries


def cue(text, start, end, **extra):
    return dict(text=text, text_content=text, start=start, end=end, **extra)


class SubtitleGranularityTests(unittest.TestCase):
    def test_long_wav_retains_original_small_cues(self):
        rows = [cue('甲，', 0, 3), cue('乙，', 3, 9), cue('丙。', 9, 15)]
        result = TtsEditor._resegment_rows(rows, 0, 15, [dict(start=0, end=16)], ['甲，乙，丙。'], '甲，乙，丙。')
        self.assertEqual([r['text'] for r in result], ['甲，', '乙，', '丙。'])
        self.assertAlmostEqual(result[1]['start'], 3.2)
        self.assertEqual(result[-1]['end'], 16)

    def test_merge_wavs_does_not_merge_small_subtitles(self):
        rows = [cue('甲，', 0, 3), cue('乙。', 3, 8), cue('后文。', 8, 10)]
        result = TtsEditor._resegment_rows(rows, 0, 8, [dict(start=0, end=6)], ['甲，乙。'], '甲，乙。')
        self.assertEqual([r['text'] for r in result], ['甲，', '乙。', '后文。'])
        self.assertEqual(result[-1]['start'], 6)
        self.assertEqual(result[-1]['end'], 8)

    def test_new_wav_boundary_and_pause_both_survive(self):
        result = resegment_cues([cue('甲乙，丙丁。', 0, 10)],
                               [dict(start=0, end=4), dict(start=4.6, end=11)], ['甲乙，丙', '丁。'])
        self.assertEqual([r['text'] for r in result], ['甲乙，丙', '丁。'])
        self.assertEqual(result[0]['end'], 4)
        self.assertEqual(result[1]['start'], 4.6)

    def test_changed_word_retains_unchanged_cue(self):
        result = resegment_cues([cue('成为搭档，', 0, 4), cue('继续使用。', 4, 8)],
                               [dict(start=0, end=7)], ['是搭档，继续使用。'])
        self.assertEqual([r['text'] for r in result], ['是搭档，', '继续使用。'])
        self.assertEqual(result[-1]['end'], 7)

    def test_fine_boundaries_survive_second_edit(self):
        rows = [cue('第一句，', 0, 4), cue('第二句，', 4, 7), cue('第三句。', 7, 10)]
        first = resegment_cues(rows, [dict(start=0, end=5), dict(start=5.2, end=11)], ['第一句，第二', '句，第三句。'])
        second = resegment_cues(first, [dict(start=0, end=12)], ['第一句，第二句，第三句。'])
        self.assertEqual(''.join(r['text'] for r in second), '第一句，第二句，第三句。')
        self.assertGreaterEqual(len(second), 3)
        self.assertEqual(second[-1]['end'], 12)

    def test_legacy_repair_is_idempotent_and_respects_independent_captions(self):
        rows = [cue('第一句，第二句，第三句。', 0, 15), cue('下一句。', 15, 18)]
        segments = [dict(index=1, text=rows[0]['text'], start=0, end=15)]
        before = copy.deepcopy(rows)
        fixed, affected = restore_coarse_cues(rows, segments)
        self.assertEqual(rows, before)
        self.assertEqual(affected, {1})
        self.assertEqual(len(fixed), 4)
        self.assertEqual(fixed[-1], rows[-1])
        self.assertEqual(restore_coarse_cues(fixed, segments), (fixed, set()))
        segments[0]['subtitle_independent'] = True
        self.assertEqual(restore_coarse_cues(rows, segments), (rows, set()))

    def test_unbroken_long_cue_not_artificially_sliced(self):
        row = cue('没有任何断句证据的长字幕', 0, 20)
        rows, affected = restore_coarse_cues([row], [dict(index=1, text=row['text'], start=0, end=20)])
        self.assertEqual((rows, affected), ([row], set()))
        self.assertEqual(punctuation_cuts('3.14米。后续'), [6])
        self.assertEqual(punctuation_cuts('你好？！再见。'), [4])

    def test_spaces_and_punctuation_not_dropped(self):
        rows = [cue('Hello world, ', 0, 4), cue('thank you.', 4, 8)]
        result = resegment_cues(rows, [dict(start=0, end=8)], ['Hello world, thank you.'])
        self.assertEqual(''.join(r['text'] for r in result), 'Hello world, thank you.')

    def test_alignment_uses_new_sound_and_keeps_fallback_nonblocking(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            rows = [cue('甲，', 10, 12), cue('乙。', 12, 15.2)]
            segments = [dict(index=1, start=10, end=15, pause_after=.2, text='甲，乙。')]
            for aligned in (True, False):
                def run(command, **kwargs):
                    self.assertEqual(kwargs['env']['ASR_DEVICE'], 'cpu')
                    request = json.loads(Path(command[-2]).read_text(encoding='utf-8'))
                    self.assertEqual(request[0]['texts'], ['甲，', '乙。'])
                    Path(command[-1]).write_text(json.dumps([dict(starts=[0, 3.5], aligned=aligned)]), encoding='utf-8')
                    return SimpleNamespace(returncode=0)
                with patch('backend.app.tts_editor.subprocess.run', side_effect=run), patch('backend.app.pipeline.resolve_asr_python', return_value='python'):
                    result, fallbacks = _refine_subtitle_timing(project, rows, segments, {1: project/'speech.wav'})
                self.assertEqual(result[1]['start'], 13.5 if aligned else 12)
                self.assertEqual(result[-1]['end'], 15.2)
                self.assertEqual(fallbacks, 0 if aligned else 1)
            self.assertEqual(list(project.glob('other/tts_segments/.subtitle-timing/*.json')), [])

    def test_alignment_cli_fallback_is_reported_and_model_cache_shared(self):
        from backend.app.tts_alignment import main
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root/'request.json', root/'result.json'
            source.write_text(json.dumps([dict(audio='a.wav', texts=['甲', '乙'], duration=4, fallback_starts=[0, 2])]*2), encoding='utf-8')
            caches = []
            def recognize(path, **kwargs):
                caches.append(kwargs['model_cache'])
                raise RuntimeError('ASR unavailable')
            with patch('module2_scene_director.transcribe_audio', side_effect=recognize), patch('sys.argv', ['alignment', str(source), str(target)]):
                main()
            result = json.loads(target.read_text(encoding='utf-8'))
            self.assertEqual(len(result), 2)
            self.assertTrue(all(not r['aligned'] and r['starts'] == [0, 2] for r in result))
            self.assertIs(caches[0], caches[1])

    def test_actual_transcriber_reuses_model_in_one_batch(self):
        import sys
        import types
        from module2_scene_director import transcribe_audio
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as directory:
            factory = Mock()
            factory.return_value.transcribe.return_value = ([], SimpleNamespace(language='zh'))
            whisper = types.ModuleType('faster_whisper')
            whisper.WhisperModel = factory
            utils = types.ModuleType('faster_whisper.utils')
            utils.download_model = Mock(return_value=directory)
            with patch.dict(sys.modules, {'faster_whisper': whisper, 'faster_whisper.utils': utils}), patch.dict('os.environ', ASR_DEVICE='cpu', ASR_MODEL=directory):
                cache = {}
                transcribe_audio(Path('first.wav'), model_cache=cache)
                transcribe_audio(Path('second.wav'), model_cache=cache)
            factory.assert_called_once()
            self.assertEqual(factory.return_value.transcribe.call_count, 2)

    def test_rebuild_repairs_without_tts_or_changed_audio_and_can_undo(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            archive = root/'other/tts_segments'
            archive.mkdir(parents=True)
            (root/'input').mkdir()
            text = '第一小句，第二小句，第三小句。'
            with wave.open(str(archive/'one.wav'), 'wb') as out:
                out.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
                out.writeframes(b'\0\0' * 16 * 16000)
            original_audio = (archive/'one.wav').read_bytes()
            (root/'input/配音.wav').write_bytes(original_audio)
            manifest = dict(revision=3, engine='cluster', segments=[dict(index=1, text=text, tts_text=text,
                            start=0, end=16, duration=16, filename='one.wav', pause_after=0)])
            (archive/'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
            rows = [cue(text, 0, 16, slide_id='scene_001')]
            timeline = root/'other/画面时间线.json'
            timeline.write_text(json.dumps(rows), encoding='utf-8')
            srt = root/'other/最终字幕.srt'
            _write_srt_entries(srt, rows)
            editor = TtsEditor()
            job = SimpleNamespace(id='job', user_id=1, request=dict(script=text, dynamic_video=True))
            for name, kwargs in [('_project_dir', dict(return_value=root)), ('_ensure_module1_layout', {}),
                                 ('_migrate_legacy_archive', {}), ('_sync_module1_flat_outputs', {})]:
                stack.enter_context(patch.object(editor, name, **kwargs))
            synthesis = stack.enter_context(patch.object(editor, '_synthesize_parts'))
            stack.enter_context(patch('backend.app.tts_editor.JOBS_DIR', root/'jobs'))
            for name in ('store.update', 'store.log'):
                stack.enter_context(patch('backend.app.tts_editor.'+name))
            stack.enter_context(patch('backend.app.tts_editor._refine_subtitle_timing', side_effect=lambda p, r, s, f: (r, 1)))
            result = editor.rebuild_audio(job=job, user_id=1, pauses={})
            self.assertEqual(result['restored_subtitle_boundaries'], 2)
            self.assertIn('估算时间', result['message'])
            self.assertEqual(''.join(r['text'] for r in _srt_entries(srt)), text)
            self.assertEqual(len(_srt_entries(srt)), 3)
            self.assertEqual((archive/'one.wav').read_bytes(), original_audio)
            self.assertEqual((root/'input/配音.wav').read_bytes(), original_audio)
            synthesis.assert_not_called()
            editor.undo(job=job, user_id=1)
            self.assertEqual(len(_srt_entries(srt)), 1)
            self.assertEqual((archive/'one.wav').read_bytes(), original_audio)

    def test_repaired_subtitles_allow_split_without_touching_other_shots(self):
        from backend.app.video_auto_split import split_window
        from backend.app.final_narration import remap_visual_groups
        scenes = [dict(slide_id='a', text='第一句，第二句，第三句。', start=0, end=15),
                  dict(slide_id='b', text='旁边已经完成。', start=15, end=20)]
        shots = [dict(id='long', kind='video', slide_ids=['a']),
                 dict(id='done', kind='image', slide_ids=['b'], image='paid.png', image_status='completed')]
        record = dict(scenes=scenes, shots=shots, creation_parameters=dict(video_generation_backend='cloud_h3'))
        with self.assertRaisesRegex(ValueError, '单条超过'):
            split_window(record, 'long')
        fixed, _ = restore_coarse_cues(scenes, [dict(index=1, text=scenes[0]['text'], start=0, end=15)])
        for index, row in enumerate(fixed):
            row['slide_id'] = str(index)
        mapped, detached = remap_visual_groups(scenes, fixed, shots)
        _, _, available = split_window(dict(record, scenes=fixed, shots=mapped), 'long')
        self.assertEqual(len(available), 3)
        self.assertEqual(detached, [])
        self.assertEqual(mapped[-1]['id'], 'done')
        self.assertEqual(mapped[-1]['image'], 'paid.png')


if __name__ == '__main__':
    unittest.main()
