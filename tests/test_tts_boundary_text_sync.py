import json
import tempfile
import time
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend.app.tts_editor import TtsEditor, _srt_entries


def wav(path, duration):
    with wave.open(str(path), 'wb') as out:
        out.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        out.writeframes(b'\0\0' * int(duration * 16000))


class BoundaryTextSyncTests(unittest.TestCase):
    def test_partial_cues_shift_both_directions_and_preserve_hidden(self):
        rows = [dict(text='前句成为搭档', start=0, end=3),
                dict(text='继续后句', start=3, end=6, subtitle_hidden=True)]
        for end in (3, 5):
            result = TtsEditor._resegment_rows(rows, 1, 4,
                [dict(start=1, end=end)], ['是搭档继续'], '成为搭档继续')
            self.assertEqual([r['text'] for r in result], ['前句', '是搭档', '继续', '后句'])
            self.assertEqual(result[-1]['start'], end)
            self.assertEqual(result[-1]['end'], 6 + end - 4)
            self.assertTrue(result[-1]['subtitle_hidden'])
            self.assertTrue(result[-2]['subtitle_hidden'])

    def test_missing_or_ambiguous_original_is_rejected(self):
        for text in ('不存在', '相同'):
            with self.assertRaises(ValueError):
                TtsEditor._resegment_rows([
                    dict(text='相同', start=0, end=1), dict(text='相同', start=2, end=3),
                ], 1, 2, [dict(start=1, end=2)], ['新词'], text)

    def test_confirmed_edit_commits_audio_script_subtitles_and_undo(self):
        self.run_edit(confirmed=True)

    def test_initial_audio_review_also_syncs_step_assets(self):
        self.run_edit(confirmed=True, initial=True)

    def test_unconfirmed_edit_does_not_call_tts(self):
        self.run_edit(confirmed=False)

    def test_failed_commit_keeps_generated_audio_and_restores_original(self):
        self.run_edit(confirmed=True, fail=True)

    def run_edit(self, confirmed, fail=False, initial=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / 'project'
            segments = project / 'other' / 'tts_segments'
            segments.mkdir(parents=True)
            (project / 'input').mkdir()
            jobs = root / 'jobs'
            (jobs / 'job').mkdir(parents=True)
            (jobs / 'job' / 'script.txt').write_text('成为搭档后句', encoding='utf-8')
            (project / 'input' / '文案.txt').write_text('成为搭档后句', encoding='utf-8')
            for name in ('one.wav', 'two.wav'):
                wav(segments / name, 2)
            manifest = dict(engine='qwen', segments=[
                dict(index=1, text='成为搭档', tts_text='成为搭档', filename='one.wav', start=0, end=2),
                dict(index=2, text='后句', tts_text='后句', filename='two.wav', start=2, end=4),
            ])
            (segments / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
            srt = project / 'other' / '最终字幕.srt'
            srt.write_text('1\n00:00:00,000 --> 00:00:02,000\n成为搭档\n\n2\n00:00:02,000 --> 00:00:04,000\n后句\n', encoding='utf-8')
            timeline = project / 'other' / '画面时间线.json'
            timeline.write_text(json.dumps([dict(text_content='成为搭档', start=0, end=2), dict(text_content='后句', start=2, end=4)]), encoding='utf-8')
            job = SimpleNamespace(id='job', request={'script': '成为搭档后句'}, user_id=1)
            if initial:
                from backend.app.pipeline import STEP_WORKFLOW_VERSION
                job.request.update(step_mode=True, _step_workflow_version=STEP_WORKFLOW_VERSION, _step_mode_stage='audio_review')
                job.message = '配音待确认'
            editor = TtsEditor()
            def synth(**kwargs):
                target = kwargs['work_dir'] / 'new.wav'
                wav(target, 1)
                return [target]
            with (patch.object(editor, '_project_dir', return_value=project),
                  patch.object(editor, '_ensure_module1_layout'),
                  patch.object(editor, '_synthesize_parts', side_effect=synth) as synthesis,
                  patch.object(editor, '_sync_module1_flat_outputs'),
                  patch('backend.app.tts_editor.JOBS_DIR', jobs),
                  patch('backend.app.tts_editor.sync_refined_step_audio_assets') as sync_assets,
                  patch('backend.app.tts_editor.persist_step_workflow_state'),
                  patch('backend.app.tts_editor.store.update'),
                  patch('backend.app.tts_editor.store.log')):
                from backend.app.tts_editor import _commit_step_audio_edit
                with patch('backend.app.tts_editor._commit_step_audio_edit',
                           side_effect=RuntimeError('test failure') if fail else _commit_step_audio_edit):
                    editor.resegment(job=job, user_id=1, start_index=1, replace_count=1,
                        parts=[dict(text='是搭档', tts_text='是搭档')], sync_text=confirmed)
                    deadline = time.monotonic() + 5
                    while editor.status('job')['status'] == 'running' and time.monotonic() < deadline:
                        time.sleep(.01)
                if not confirmed or fail:
                    self.assertEqual(editor.status('job')['status'], 'failed')
                    self.assertEqual(_srt_entries(srt)[0]['text'], '成为搭档')
                    self.assertEqual(job.request['script'], '成为搭档后句')
                    if not confirmed:
                        synthesis.assert_not_called()
                    else:
                        self.assertEqual(len(list(segments.glob('.boundary-*/new.wav'))), 1)
                    return
                self.assertEqual(editor.status('job')['status'], 'completed', editor.status('job'))
                self.assertEqual(_srt_entries(srt)[0]['text'], '是搭档')
                self.assertEqual(_srt_entries(srt)[1]['start'], 1)
                self.assertEqual(json.loads(timeline.read_text(encoding='utf-8'))[1]['end'], 3)
                self.assertEqual(job.request['script'], '是搭档\n后句')
                if initial:
                    sync_assets.assert_called_once_with(job, project)
                editor.undo(job=job, user_id=1)
                self.assertEqual(job.request['script'], '成为搭档后句')
                self.assertEqual((jobs / 'job' / 'script.txt').read_text(encoding='utf-8'), '成为搭档后句')
                self.assertEqual(_srt_entries(srt)[1]['start'], 2)


if __name__ == '__main__':
    unittest.main()
