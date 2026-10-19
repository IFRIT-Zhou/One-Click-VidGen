"""Repeated edits must use the last committed subtitle words, not old metadata."""
import json
import tempfile
import time
import unittest
import wave
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from backend.app.tts_editor import TtsEditor, _srt_entries, _write_srt_entries


def write_wav(path, duration):
    with wave.open(str(path), 'wb') as audio:
        audio.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        audio.writeframes(b'\0\0' * int(duration * 16000))


class ReeditConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'
        self.other = self.project / 'other'
        self.archive = self.other / 'tts_segments'
        self.archive.mkdir(parents=True)
        (self.project / 'input').mkdir()
        self.job = SimpleNamespace(id='job', request={}, user_id=1)
        self.editor = TtsEditor()
        self.manifest_path = self.archive / 'manifest.json'
        self.timeline_path = self.other / '画面时间线.json'
        self.srt_path = self.other / '最终字幕.srt'
        for name in ('one.wav', 'next.wav'):
            write_wav(self.archive / name, 2)
        write_wav(self.project / 'input' / '配音.wav', 4)
        self.manifest_path.write_text(json.dumps(dict(engine='qwen', revision=1, segments=[
            dict(index=1, text='成为厨房搭档，开始使用。', filename='one.wav', start=0, end=2),
            dict(index=2, text='下一句。', filename='next.wav', start=2, end=4),
        ]), ensure_ascii=False), encoding='utf-8')
        self.write_rows([
            dict(slide_id='s1', text_content='成为厨房搭档，', start=0, end=1),
            dict(slide_id='s2', text_content='开始使用。', start=1, end=2),
            dict(slide_id='s3', text_content='下一句。', start=2, end=4),
        ])
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for target, kwargs in [
            ('_project_dir', dict(return_value=self.project)),
            ('_ensure_module1_layout', {}), ('_migrate_legacy_archive', {}),
            ('_sync_module1_flat_outputs', {}),
        ]:
            self.stack.enter_context(patch.object(self.editor, target, **kwargs))
        for target in ('store.update', 'store.log'):
            self.stack.enter_context(patch('backend.app.tts_editor.' + target))
        self.stack.enter_context(patch('backend.app.tts_editor.JOBS_DIR', self.root / 'jobs'))
        self.stack.enter_context(patch('backend.app.tts_editor.VisualEditor.save_subtitle_texts', side_effect=self.save_subtitles))

    def write_rows(self, rows):
        self.timeline_path.write_text(json.dumps(rows, ensure_ascii=False), encoding='utf-8')
        _write_srt_entries(self.srt_path, [dict(text=r['text_content'], start=r['start'], end=r['end']) for r in rows])

    def save_subtitles(self, *, updates, **_):
        rows = json.loads(self.timeline_path.read_text(encoding='utf-8'))
        for row in rows:
            row['text_content'] = updates.get(row['slide_id'], row['text_content'])
        self.write_rows(rows)

    def manifest(self):
        # Check the persisted file, not the compatibility view, so a missed
        # commit cannot be hidden by automatic legacy reconciliation.
        return json.loads(self.manifest_path.read_text(encoding='utf-8'))

    def wait(self, expected='completed'):
        deadline = time.monotonic() + 5
        while self.editor.status('job')['status'] == 'running' and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(self.editor.status('job')['status'], expected, self.editor.status('job'))

    def regenerate(self, index, reading, display=None):
        def synth(job, user_id, indices, settings, readings, updates, *, subtitle_text_overrides=None):
            pending = self.root / 'new.wav'
            write_wav(pending, 1.5)
            return self.editor._commit_regenerated(job, user_id, indices, settings, readings, updates,
                self.editor._load_manifest(self.project), {index: pending}, {},
                subtitle_text_overrides=subtitle_text_overrides)
        with patch.object(self.editor, '_regenerate_sync_impl', side_effect=synth):
            self.editor.regenerate(job=self.job, user_id=1, indices=[index], text_overrides={index: reading},
                                   subtitle_text_overrides={index: display} if display is not None else {})
            self.wait()

    def test_independent_subtitle_survives_revoice_until_explicit_opt_in(self):
        annotation = '成为厨房搭档（注释），开始使用。'
        self.editor.save_subtitle_only(job=self.job, user_id=1, updates={1: annotation})
        self.regenerate(1, '成为厨房搭档，开始使用。')
        item = self.manifest()['segments'][0]
        self.assertEqual(item['text'], annotation)
        self.assertTrue(item['subtitle_independent'])
        self.assertEqual(''.join(row['text'] for row in _srt_entries(self.srt_path)), annotation + '下一句。')
        self.regenerate(1, '是厨房搭档，开始使用。', '是厨房搭档，开始使用。')
        item = self.manifest()['segments'][0]
        self.assertEqual(item['text'], '是厨房搭档，开始使用。')
        self.assertNotIn('subtitle_independent', item)

    def split(self, index, texts, expected='completed'):
        def synth(**kwargs):
            paths = []
            for n, _ in enumerate(kwargs['texts']):
                path = kwargs['work_dir'] / f'{n}.wav'
                write_wav(path, .5)
                paths.append(path)
            return paths
        with patch.object(self.editor, '_synthesize_parts', side_effect=synth) as mocked:
            self.editor.resegment(job=self.job, user_id=1, start_index=index, replace_count=1,
                                  parts=[dict(text=text, tts_text=text) for text in texts])
            self.wait(expected)
            return mocked.call_count

    def test_sync_revoice_then_split_then_revoice_and_split_again(self):
        untouched_audio = (self.archive / 'next.wav').read_bytes()
        self.regenerate(1, '是厨房搭档，开始使用。', '是厨房搭档，开始使用。')
        self.assertEqual(self.manifest()['segments'][0]['text'], '是厨房搭档，开始使用。')
        self.assertIn('是厨房搭档', self.job.request['script'])
        self.assertNotIn('成为厨房搭档', self.job.request['_final_narration_script'])
        self.assertEqual(self.split(1, ['是厨房搭档，', '开始使用。']), 1)
        self.regenerate(2, '请开始使用。', '请开始使用。')
        self.assertEqual(self.manifest()['segments'][1]['text'], '请开始使用。')
        self.assertEqual(self.split(2, ['请开始', '使用。']), 1)
        result = self.manifest()['segments']
        self.assertEqual([s['text'] for s in result], ['是厨房搭档，', '请开始', '使用。', '下一句。'])
        self.assertEqual([r['text'] for r in _srt_entries(self.srt_path)], [s['text'] for s in result])
        self.assertAlmostEqual(result[-1]['start'], 1.5)
        self.assertEqual((self.archive / 'next.wav').read_bytes(), untouched_audio)
        self.editor.undo(job=self.job, user_id=1)
        self.assertEqual([s['text'] for s in self.manifest()['segments']], ['是厨房搭档，', '请开始使用。', '下一句。'])
        self.assertAlmostEqual(self.manifest()['segments'][-1]['start'], 2)

    def test_pronunciation_only_does_not_modify_display(self):
        self.regenerate(1, 'cheng2为厨房搭档，开始使用。')
        item = self.manifest()['segments'][0]
        self.assertEqual(item['text'], '成为厨房搭档，开始使用。')
        self.assertEqual(item['tts_text'], 'cheng2为厨房搭档，开始使用。')
        self.assertEqual(''.join(r['text'] for r in _srt_entries(self.srt_path)), '成为厨房搭档，开始使用。下一句。')
        self.assertEqual(self.editor._load_manifest(self.project)['segments'][0]['text'], item['text'])

    def test_explicit_display_is_not_replaced_by_pronunciation(self):
        self.regenerate(1, 'shi4厨房搭档，开始使用。', '是厨房搭档，开始使用。')
        item = self.manifest()['segments'][0]
        self.assertEqual(item['text'], '是厨房搭档，开始使用。')
        self.assertEqual(item['tts_text'], 'shi4厨房搭档，开始使用。')

    def make_legacy_mismatch(self):
        manifest = self.manifest()
        manifest['segments'][0]['tts_text'] = '是厨房搭档，开始使用。'
        self.manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
        rows = json.loads(self.timeline_path.read_text(encoding='utf-8'))
        rows[0]['text_content'] = '是厨房搭档，'
        self.write_rows(rows)

    def test_legacy_mismatch_read_is_nonmutating_and_next_split_works(self):
        self.make_legacy_mismatch()
        before = {path: path.read_bytes() for path in (self.manifest_path, self.timeline_path, self.srt_path, self.archive/'one.wav')}
        loaded = self.editor._load_manifest(self.project)
        self.assertEqual(loaded['segments'][0]['text'], '是厨房搭档，开始使用。')
        for path, data in before.items():
            self.assertEqual(path.read_bytes(), data)
        self.assertEqual(self.split(1, ['是厨房搭档，', '开始使用。']), 1)

    def test_different_words_are_not_guessed_from_timing(self):
        self.make_legacy_mismatch()
        rows = json.loads(self.timeline_path.read_text(encoding='utf-8'))
        rows[0]['text_content'] = '完全不同的字幕，'
        self.write_rows(rows)
        self.assertEqual(self.editor._load_manifest(self.project)['segments'][0]['text'], '成为厨房搭档，开始使用。')
        self.assertEqual(self.split(1, ['成为厨房搭档，', '开始使用。'], 'failed'), 0)
        self.assertIn('第 1 句', self.editor.status('job')['message'])

    def test_matching_words_at_other_time_do_not_repair_wrong_segment(self):
        self.make_legacy_mismatch()
        self.write_rows([dict(slide_id='elsewhere', text_content='是厨房搭档，开始使用。', start=2, end=4)])
        self.assertEqual(self.editor._load_manifest(self.project)['segments'][0]['text'], '成为厨房搭档，开始使用。')

    def test_recovery_keeps_subtitle_sync_intent(self):
        pending = self.root / 'pending.wav'
        write_wav(pending, 1.5)
        text = '是厨房搭档，开始使用。'
        updates = self.editor._subtitle_updates_for_segments(self.project, self.manifest()['segments'], {1: text})
        data = dict(manifest=self.manifest(), reading_texts={'1': text}, subtitle_text_overrides={'1': text})
        self.editor._regenerate_sync(self.job, 1, [1], {}, {}, updates, recovery=(data, {1: pending}))
        self.assertEqual(self.manifest()['segments'][0]['text'], text)
        self.assertEqual(self.split(1, ['是厨房搭档，', '开始使用。']), 1)

    def test_real_synthesis_path_records_display_edit_for_recovery(self):
        manifest = self.manifest()
        manifest['engine'] = 'cluster'
        self.manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        text = '是厨房搭档，开始使用。'
        updates = self.editor._subtitle_updates_for_segments(self.project, manifest['segments'], {1: text})
        def synth(**kwargs):
            directory = kwargs['segment_archive_dir']
            directory.mkdir(parents=True)
            generated = []
            for i, _ in enumerate(kwargs['chunks_override']):
                path = directory / f'part_{i}.wav'
                write_wav(path, 1.5)
                generated.append(dict(filename=path.name))
            (directory / 'manifest.json').write_text(json.dumps(dict(segments=generated)), encoding='utf-8')
        with (patch('backend.app.cloud_client.cloud_client_for', return_value=Mock()),
              patch('backend.app.cloud_tts.synthesize_cloud_tts', side_effect=synth),
              patch('backend.app.tts_editor._speech_alignment', side_effect=RuntimeError('needs review'))):
            with self.assertRaisesRegex(RuntimeError, 'needs review'):
                self.editor._regenerate_sync(self.job, 1, [1], {}, {1: text}, updates,
                                             subtitle_text_overrides={1: text})
        data = json.loads((self.archive / '.regenerate' / 'pending_alignment.json').read_text(encoding='utf-8'))
        self.assertEqual(data['subtitle_text_overrides'], {'1': text})
        self.assertEqual(self.manifest()['segments'][0]['text'], '成为厨房搭档，开始使用。')
        self.editor._set_task(self.job.id, status='failed', message='needs review')
        self.editor.adopt_estimated(self.job, 1, data['token'])
        self.assertEqual(self.manifest()['segments'][0]['text'], text)

    def test_old_recovery_without_display_metadata_remains_compatible(self):
        pending = self.root / 'pending.wav'
        write_wav(pending, 1.5)
        text = '是厨房搭档，开始使用。'
        updates = self.editor._subtitle_updates_for_segments(self.project, self.manifest()['segments'], {1: text})
        data = dict(manifest=self.manifest(), reading_texts={'1': text})
        self.editor._regenerate_sync(self.job, 1, [1], {}, {}, updates, recovery=(data, {1: pending}))
        self.assertEqual(self.manifest()['segments'][0]['text'], text)

    def test_failed_subtitle_commit_rolls_back_audio_and_manifest(self):
        originals = {path: path.read_bytes() for path in (self.manifest_path, self.timeline_path, self.srt_path, self.archive/'one.wav')}
        pending = self.root / 'pending.wav'
        write_wav(pending, 1.5)
        text = '是厨房搭档，开始使用。'
        updates = self.editor._subtitle_updates_for_segments(self.project, self.manifest()['segments'], {1: text})
        data = dict(manifest=self.manifest(), reading_texts={'1': text}, subtitle_text_overrides={'1': text})
        with patch('backend.app.tts_editor.VisualEditor.save_subtitle_texts', side_effect=RuntimeError('save failed')):
            with self.assertRaisesRegex(RuntimeError, 'save failed'):
                self.editor._regenerate_sync(self.job, 1, [1], {}, {}, updates, recovery=(data, {1: pending}))
        for path, content in originals.items():
            self.assertEqual(path.read_bytes(), content)


if __name__ == '__main__':
    unittest.main()
