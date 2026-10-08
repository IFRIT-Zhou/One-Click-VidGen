import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from backend.app.tts_alignment import align_starts


class SpeechAlignmentTests(unittest.TestCase):
    def test_new_speech_boundaries_not_old_proportions(self):
        words = [dict(word='第一句话', start=0, end=1), dict(word='第二句话', start=1.5, end=4)]
        self.assertEqual(align_starts(['第一句话', '第二句话'], words, 4), [0, 1.5])

    def test_long_non_speech_gap_is_not_certified(self):
        words = [dict(word='第一句话', start=0, end=1), dict(word='第二句话', start=10, end=12)]
        with self.assertRaisesRegex(ValueError, '无语音'):
            align_starts(['第一句话', '第二句话'], words, 12)

    def test_missing_sentence_is_not_silently_warped(self):
        with self.assertRaises(ValueError):
            align_starts(['第一句话', '完全不同的内容'], [dict(word='第一句话', start=0, end=1)], 3)

    def test_failed_edit_restores_published_audio(self):
        from backend.app.tts_editor import TtsEditor
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'input').mkdir()
            (root/'other/tts_segments').mkdir(parents=True)
            audio = root/'input/配音.wav'
            audio.write_bytes(b'old audio')
            editor = TtsEditor()
            def fail(*args):
                audio.write_bytes(b'bad replacement')
                raise RuntimeError('alignment failed')
            with patch.object(editor, '_project_dir', return_value=root), \
                 patch.object(editor, '_load_manifest', return_value={'segments': []}), \
                 patch.object(editor, '_regenerate_sync_impl', side_effect=fail), \
                 patch('backend.app.tts_editor.JOBS_DIR', root/'jobs'):
                with self.assertRaises(RuntimeError):
                    editor._regenerate_sync(SimpleNamespace(id='j'), 1, [], {}, {}, {})
            self.assertEqual(audio.read_bytes(), b'old audio')
