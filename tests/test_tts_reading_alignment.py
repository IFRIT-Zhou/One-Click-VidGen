import unittest
import json
import tempfile
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend.app.tts_alignment import align_starts, spoken_cues


def words(*values):
    return [dict(word=value, start=index, end=index + .8) for index, value in enumerate(values)]


class ReadingAlignmentTests(unittest.TestCase):
    def test_reading_override_not_display_is_verified(self):
        display = ['这是一只小鼠', '结果非常明显']
        reading = '这是一只实验小白鼠，实验结果显著不同'
        self.assertEqual(align_starts(display, words('这是一只实验小白鼠', '实验结果显著不同'), 2, reading), [0, 1])

    def test_pronunciation_hint_in_long_sentence(self):
        text = '被吓过的小鼠，觉睡得稀碎，深睡片段明显变短，醒来次数大幅增加'
        reading = text.replace('觉', 'jiao4')
        self.assertEqual(align_starts([text], words(text), 1, reading), [0])

    def test_unchanged_and_legacy_input(self):
        texts = ['第一句话', '第二句话']
        self.assertEqual(align_starts(texts, words(*texts), 2), [0, 1])
        self.assertEqual(spoken_cues(texts, '第一句话，第二句话'), texts)

    def test_real_omission_still_rejected(self):
        with self.assertRaises(ValueError):
            align_starts(['第一句话', '第二句话'], words('第一句话'), 2, '第一句话，第二句话')

    def test_long_silence_still_rejected(self):
        recognized = words('第一句话', '第二句话')
        recognized[1].update(start=10, end=11)
        with self.assertRaises(ValueError):
            align_starts(['第一句话', '第二句话'], recognized, 11)

    def test_deleted_cue_not_given_old_timestamp(self):
        with self.assertRaises(ValueError):
            spoken_cues(['第一句话', '完全不同的结尾'], '第一句话')

    def test_editor_passes_current_override_to_asr(self):
        from backend.app import tts_editor as editor
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / 'other').mkdir()
            (project / 'other' / editor.SUBTITLE_FILENAME).write_text(
                '1\n00:00:00,000 --> 00:00:01,000\n原字幕\n', encoding='utf-8')
            audio = project / 'new.wav'
            with wave.open(str(audio), 'wb') as stream:
                stream.setnchannels(1)
                stream.setsampwidth(2)
                stream.setframerate(16000)
                stream.writeframes(b'\0\0' * 16000)

            def recognize(command, **kwargs):
                request = json.loads(Path(command[-2]).read_text(encoding='utf-8'))
                self.assertEqual(request[0]['reading_text'], '当前朗读修正')
                self.assertEqual(request[0]['texts'], ['原字幕'])
                Path(command[-1]).write_text('[[0]]', encoding='utf-8')
                return SimpleNamespace(returncode=0)

            with patch.object(editor.subprocess, 'run', side_effect=recognize), \
                    patch('backend.app.pipeline.resolve_asr_python', return_value='python'):
                anchors = editor._speech_alignment(project, [dict(index=1, start=0, end=1,
                                                   tts_text='旧朗读')], {1: audio},
                                                   reading_texts={1: '当前朗读修正'})
            self.assertEqual(anchors, {1: [(0.0, 0.0), (1.0, 1.0)]})


if __name__ == '__main__':
    unittest.main()
