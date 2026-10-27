import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from backend.app.tts_editor import TtsEditor


class PendingRecoveryTests(unittest.TestCase):
    def test_explicit_adoption_uses_saved_files_without_synthesis(self):
        with tempfile.TemporaryDirectory() as root:
            project=Path(root);segment=project/'other'/'tts_segments'
            work=segment/'.regenerate';(work/'segments').mkdir(parents=True)
            (work/'segments'/'aligned_segment_0003.wav').write_bytes(b'pending')
            data=dict(token='token',indices=[3],fingerprint='same',settings={},subtitles={},manifest={},reading_texts={'3':'text'})
            (work/'pending_alignment.json').write_text(json.dumps(data),encoding='utf-8')
            editor=TtsEditor();job=SimpleNamespace(id='job')
            old = editor._start_edit('job')
            editor.cancel('job', old['task_id'])
            editor._edit_failed('job', RuntimeError(), 'stopped')
            with patch.object(editor,'_project_dir',return_value=project),patch.object(editor,'_alignment_fingerprint',return_value='same'),patch.object(editor,'_regenerate_sync') as commit:
                editor.adopt_estimated(job,1,'token')
                self.assertEqual(commit.call_count,1)
                self.assertIn('recovery',commit.call_args.kwargs)
                self.assertEqual(editor.status('job')['status'],'completed')
                self.assertFalse(editor._edit_cancelled('job'))

    def test_changed_project_rejects_old_pending_audio(self):
        with tempfile.TemporaryDirectory() as root:
            project=Path(root);work=project/'other'/'tts_segments'/'.regenerate';work.mkdir(parents=True)
            (work/'pending_alignment.json').write_text(json.dumps(dict(token='old',fingerprint='before')),encoding='utf-8')
            editor=TtsEditor()
            with patch.object(editor,'_project_dir',return_value=project),patch.object(editor,'_alignment_fingerprint',return_value='after'),patch.object(editor,'_regenerate_sync') as commit:
                with self.assertRaises(RuntimeError):editor.adopt_estimated(SimpleNamespace(id='job'),1,'old')
                commit.assert_not_called()

    def test_running_job_cannot_be_overwritten(self):
        editor=TtsEditor();editor._set_task('job',status='running')
        with self.assertRaises(RuntimeError):editor.adopt_estimated(SimpleNamespace(id='job'),1,'token')
