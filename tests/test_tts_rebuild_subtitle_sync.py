import json
import tempfile
import unittest
import wave
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend.app import main
from backend.app.tts_editor import TtsEditor, _srt_entries, _write_srt_entries


def wav(path, duration):
    with wave.open(str(path), 'wb') as audio:
        audio.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        audio.writeframes(b'\0\0' * int(duration * 16000))


class RebuildSubtitleSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'
        self.other = self.project / 'other'
        self.archive = self.other / 'tts_segments'
        self.archive.mkdir(parents=True)
        (self.project / 'input').mkdir()
        self.jobs = self.root / 'jobs'
        (self.jobs / 'job').mkdir(parents=True)
        self.rows = [
            dict(slide_id='s1', text_content='第一句原文，', start=0, end=1),
            dict(slide_id='s2', text_content='继续说明。', start=1, end=2),
            dict(slide_id='s3', text_content='第二句原文。', start=2, end=4),
            dict(slide_id='s4', text_content='最后一句原文。', start=4, end=6),
        ]
        self.new_first = '第一句新文，补充后继续说明。'
        self.new_last = '最后一句新文，补了一句。'
        segments = [
            dict(index=1, text='第一句原文，继续说明。', tts_text=self.new_first, filename='1.wav', start=0, end=2),
            dict(index=2, text='第二句原文。', tts_text='di4二句原文。', filename='2.wav', start=2, end=4),
            dict(index=3, text='最后一句原文。', tts_text=self.new_last, filename='3.wav', start=4, end=6),
        ]
        for item in segments:
            wav(self.archive / item['filename'], 2)
        wav(self.project / 'input' / '配音.wav', 6)
        self.manifest_path = self.archive / 'manifest.json'
        self.timeline_path = self.other / '画面时间线.json'
        self.srt_path = self.other / '最终字幕.srt'
        self.manifest_path.write_text(json.dumps(dict(engine='qwen', revision=7, segments=segments)), encoding='utf-8')
        self.write_rows(self.rows)
        self.script = ''.join(row['text_content'] for row in self.rows)
        (self.project / 'input' / '文案.txt').write_text(self.script, encoding='utf-8')
        (self.jobs / 'job' / 'script.txt').write_text(self.script, encoding='utf-8')
        self.job = SimpleNamespace(id='job', user_id=1, request={'script': self.script})
        self.editor = TtsEditor()
        stack = ExitStack()
        self.addCleanup(stack.close)
        for name, kwargs in [('_project_dir', dict(return_value=self.project)), ('_ensure_module1_layout', {}),
                             ('_migrate_legacy_archive', {}), ('_sync_module1_flat_outputs', {})]:
            stack.enter_context(patch.object(self.editor, name, **kwargs))
        stack.enter_context(patch('backend.app.tts_editor.JOBS_DIR', self.jobs))
        stack.enter_context(patch('backend.app.tts_editor.store.update'))
        stack.enter_context(patch('backend.app.tts_editor.store.log'))
        stack.enter_context(patch('backend.app.tts_editor.VisualEditor.save_subtitle_texts', side_effect=self.save_subtitles))
        self.synthesis = stack.enter_context(patch.object(self.editor, '_synthesize_parts', side_effect=AssertionError('must not synthesize')))
        self.revoice = stack.enter_context(patch.object(self.editor, '_regenerate_sync', side_effect=AssertionError('must not revoice')))
        self.alignment = stack.enter_context(patch('backend.app.tts_editor._speech_alignment', side_effect=AssertionError('must not run ASR')))

    def write_rows(self, rows):
        self.timeline_path.write_text(json.dumps(rows, ensure_ascii=False), encoding='utf-8')
        _write_srt_entries(self.srt_path, [dict(text=r['text_content'], start=r['start'], end=r['end']) for r in rows])

    def save_subtitles(self, *, updates, **_):
        rows = json.loads(self.timeline_path.read_text(encoding='utf-8'))
        for row in rows:
            row['text_content'] = updates.get(row['slide_id'], row['text_content'])
        self.write_rows(rows)

    def read_manifest(self):
        return json.loads(self.manifest_path.read_text(encoding='utf-8'))

    def rebuild(self, **kwargs):
        return self.editor.rebuild_audio(job=self.job, user_id=1, **kwargs)

    def test_rebuild_syncs_only_checked_generated_words_and_preserves_audio(self):
        originals = {path: path.read_bytes() for path in self.archive.glob('*.wav')}
        result = self.rebuild(pauses={1: .5, 2: .2}, subtitle_text_overrides={1: self.new_first, 3: self.new_last}, expected_revision=7)
        manifest = self.read_manifest()
        self.assertEqual(result['synced_subtitle_indices'], [1, 3])
        self.assertIn('未调用 TTS', result['message'])
        self.assertEqual([s['text'] for s in manifest['segments']], [self.new_first, '第二句原文。', self.new_last])
        self.assertEqual(manifest['segments'][1]['tts_text'], 'di4二句原文。')
        self.assertAlmostEqual(manifest['total_duration'], 6.7)
        self.assertAlmostEqual(manifest['segments'][2]['start'], 4.7)
        rows = _srt_entries(self.srt_path)
        self.assertEqual(len(rows), 4)  # Existing subtitle cue boundaries stay intact.
        self.assertEqual(''.join(row['text'] for row in rows), self.new_first + '第二句原文。' + self.new_last)
        self.assertAlmostEqual(rows[-1]['end'], 6.7)

        self.assertEqual(''.join(self.job.request['script'].splitlines()), self.new_first + '第二句原文。' + self.new_last)
        self.assertEqual(len(self.editor._history_entries(self.project)), 1)
        for path, data in originals.items():
            self.assertEqual(path.read_bytes(), data)
        self.synthesis.assert_not_called()
        self.revoice.assert_not_called()
        self.alignment.assert_not_called()
        self.editor.undo(job=self.job, user_id=1)
        self.assertEqual(self.read_manifest()['revision'], 7)
        self.assertEqual(self.job.request['script'], self.script)
        self.assertEqual((self.project / 'input' / '文案.txt').read_text(encoding='utf-8'), self.script)
        self.assertEqual((self.jobs / 'job' / 'script.txt').read_text(encoding='utf-8'), self.script)
        self.assertEqual(''.join(row['text'] for row in _srt_entries(self.srt_path)), self.script)

    def test_rebuild_after_cancel_creates_fresh_edit_attempt(self):
        task = self.editor._start_edit('job', revision=7)
        self.editor.cancel('job', task['task_id'])
        self.editor._edit_failed('job', RuntimeError(), 'stopped')
        self.rebuild(pauses={}, expected_revision=7)
        current = self.editor.status('job')
        self.assertEqual(current['status'], 'completed')
        self.assertFalse(current['cancel_requested'])
        self.assertEqual(current['adopted_version'], 8)
        self.assertNotEqual(current['task_id'], task['task_id'])

    def test_unchecked_rebuild_only_applies_pauses(self):
        result = self.rebuild(pauses={1: .5})
        self.assertEqual(result['synced_subtitle_indices'], [])
        self.assertEqual(self.read_manifest()['segments'][0]['text'], '第一句原文，继续说明。')
        self.assertEqual(self.job.request['script'], self.script)

    def test_ungenerated_draft_is_rejected_before_any_write(self):
        before = self.manifest_path.read_bytes(), self.srt_path.read_bytes()
        with self.assertRaisesRegex(ValueError, '第 1 句当前文字尚未生成'):
            self.rebuild(pauses={1: 2}, subtitle_text_overrides={1: '刚改但还未配音的草稿'})
        self.assertEqual((self.manifest_path.read_bytes(), self.srt_path.read_bytes()), before)
        self.assertEqual(self.editor._history_entries(self.project), [])

    def test_stale_revision_cannot_target_different_audio(self):
        with self.assertRaisesRegex(RuntimeError, '配音已被更新'):
            self.rebuild(pauses={}, subtitle_text_overrides={1: self.new_first}, expected_revision=6)
        self.assertEqual(self.editor._history_entries(self.project), [])

    def test_last_sentence_and_repeated_rebuild(self):
        self.rebuild(pauses={}, subtitle_text_overrides={3: self.new_last})
        result = self.rebuild(pauses={}, subtitle_text_overrides={3: self.new_last})
        self.assertEqual(result['synced_subtitle_indices'], [])
        self.assertEqual(self.read_manifest()['segments'][-1]['text'], self.new_last)

    def test_failed_commit_restores_text_pause_script_and_audio(self):
        paths = [self.manifest_path, self.srt_path, self.timeline_path,
                 self.project / 'input' / '文案.txt', self.jobs / 'job' / 'script.txt', self.project / 'input' / '配音.wav']
        before = {path: path.read_bytes() for path in paths}
        with patch('backend.app.tts_editor._commit_step_audio_edit', side_effect=RuntimeError('test rollback')):
            with self.assertRaisesRegex(RuntimeError, 'test rollback'):
                self.rebuild(pauses={1: .5}, subtitle_text_overrides={1: self.new_first})
        for path, data in before.items():
            self.assertEqual(path.read_bytes(), data)
        self.assertEqual(self.job.request['script'], self.script)
        self.assertEqual(self.editor._history_entries(self.project), [])

    def test_api_forwards_checked_subtitles_and_revision(self):
        payload = main.TtsRebuildAudioRequest.model_validate(dict(pauses={'1': .5}, subtitle_text_overrides={'3': self.new_last}, expected_revision=7))
        with (patch.object(main, '_owned_completed_job', return_value=(self.job, 1)),
              patch.object(main.visual_editor, 'status', return_value={}),
              patch.object(main.tts_editor, 'rebuild_audio', return_value={'ok': True}) as rebuild):
            main.rebuild_tts_audio('job', payload, None)
        rebuild.assert_called_once_with(job=self.job, user_id=1, pauses={1: .5},
                                        subtitle_text_overrides={3: self.new_last}, expected_revision=7, subtitle_only_overrides={})

    def test_subtitle_only_keeps_audio_reading_script_and_timing_and_can_undo(self):
        original = self.read_manifest()['segments']
        audio_path = self.project / 'input' / '配音.wav'
        audio = audio_path.read_bytes(), audio_path.stat().st_mtime_ns
        annotation = '第二句原文（补充注释）。'
        with patch('backend.app.tts_editor._concat_segment_wavs', side_effect=AssertionError('must not rebuild audio')):
            result = self.editor.save_subtitle_only(job=self.job, user_id=1, updates={2: annotation}, expected_revision=7)
            self.assertEqual(result['subtitle_only_indices'], [2])
            current = self.editor._load_manifest(self.project)['segments']
            self.assertEqual(current[1]['text'], annotation)
            self.assertTrue(current[1]['subtitle_independent'])
            self.assertEqual(current[1]['tts_text'], original[1]['tts_text'])
            self.assertEqual([(s['start'], s['end']) for s in current], [(s['start'], s['end']) for s in original])
            self.assertEqual(self.job.request['script'], self.script)
            self.assertEqual((audio_path.read_bytes(), audio_path.stat().st_mtime_ns), audio)
            self.editor.undo(job=self.job, user_id=1)
        self.assertEqual(self.read_manifest()['segments'], original)
        self.assertEqual((audio_path.read_bytes(), audio_path.stat().st_mtime_ns), audio)

    def test_rebuild_saves_unspoken_annotation_without_putting_it_into_script(self):
        annotation = '第二句原文（这里不朗读）。'
        self.rebuild(pauses={1: .5}, subtitle_text_overrides={1: self.new_first},
                     subtitle_only_overrides={2: annotation}, expected_revision=7)
        current = self.read_manifest()['segments']
        self.assertEqual(current[1]['text'], annotation)
        self.assertEqual(current[1]['tts_text'], 'di4二句原文。')
        self.assertNotIn('这里不朗读', self.job.request['script'])
        self.assertIn(self.new_first, self.job.request['script'])
        self.rebuild(pauses={})
        self.assertEqual(self.read_manifest()['segments'][1]['text'], annotation)
        self.assertTrue(self.read_manifest()['segments'][1]['subtitle_independent'])

    def test_explicit_sync_restores_following_reading(self):
        self.editor.save_subtitle_only(job=self.job, user_id=1, updates={2: '第二句原文（注释）。'})
        self.rebuild(pauses={}, subtitle_text_overrides={2: 'di4二句原文。'})
        item = self.read_manifest()['segments'][1]
        self.assertEqual(item['text'], item['tts_text'])
        self.assertNotIn('subtitle_independent', item)
        self.assertNotIn('subtitle_source_text', item)

    def test_subtitle_only_validates_all_sentences_before_writing(self):
        before = self.manifest_path.read_bytes(), self.srt_path.read_bytes()
        for updates in ({2: ''}, {2: '注释', 99: '失效句子'}):
            with self.assertRaises(ValueError):
                self.editor.save_subtitle_only(job=self.job, user_id=1, updates=updates)
        with self.assertRaises(RuntimeError):
            self.editor.save_subtitle_only(job=self.job, user_id=1, updates={2: '注释'}, expected_revision=6)
        self.assertEqual((self.manifest_path.read_bytes(), self.srt_path.read_bytes()), before)
        self.assertEqual(self.editor._history_entries(self.project), [])

    def test_subtitle_only_failed_commit_restores_without_rebuilding_audio(self):
        paths = [self.manifest_path, self.srt_path, self.timeline_path, self.project / 'input' / '配音.wav']
        before = {path: path.read_bytes() for path in paths}
        with (patch('backend.app.tts_editor._commit_step_audio_edit', side_effect=RuntimeError('test rollback')),
              patch('backend.app.tts_editor._concat_segment_wavs', side_effect=AssertionError('must not rebuild audio'))):
            with self.assertRaisesRegex(RuntimeError, 'test rollback'):
                self.editor.save_subtitle_only(job=self.job, user_id=1, updates={2: '第二句（注释）。'})
        for path, data in before.items():
            self.assertEqual(path.read_bytes(), data)
        self.assertEqual(self.editor._history_entries(self.project), [])

    def test_subtitle_only_api_forwarding(self):
        payload = main.TtsSubtitleOnlyRequest.model_validate(dict(updates={'2': '字幕注释'}, expected_revision=7))
        with (patch.object(main, '_owned_completed_job', return_value=(self.job, 1)),
              patch.object(main.visual_editor, 'status', return_value={}),
              patch.object(main.tts_editor, 'save_subtitle_only', return_value={'ok': True}) as save):
            main.save_tts_subtitle_only('job', payload, None)
        save.assert_called_once_with(job=self.job, user_id=1, updates={2: '字幕注释'}, expected_revision=7)

    def test_subtitle_only_on_original_audio_preserves_missing_reading_field(self):
        manifest = self.read_manifest()
        manifest['segments'][1].pop('tts_text')
        self.manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        self.editor.save_subtitle_only(job=self.job, user_id=1, updates={2: '第二句（字幕注释）。'})
        item = self.editor._load_manifest(self.project)['segments'][1]
        self.assertEqual(item['tts_text'], '第二句原文。')
        self.assertEqual(item['text'], '第二句（字幕注释）。')

    def test_independent_words_are_not_legacy_reconciled(self):
        item = dict(index=1, text='独立字幕', tts_text='旧朗读', start=0, end=2, subtitle_independent=True)
        self.write_rows([dict(slide_id='s1', text_content='旧朗读', start=0, end=2)])
        self.editor._reconcile_legacy_display_texts(self.project, [item])
        self.assertEqual(item['text'], '独立字幕')


if __name__ == '__main__':
    unittest.main()
