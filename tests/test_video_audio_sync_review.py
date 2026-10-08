import copy
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from backend.app import video_studio as studio


class AudioSyncReviewTests(unittest.TestCase):
    def test_new_snapshot_retimes_changed_shot_and_shifts_unchanged_shot(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            project, source = root / 'project', root / 'source'
            (project / 'assets').mkdir(parents=True)
            (source / 'input').mkdir(parents=True)
            (source / 'other').mkdir()
            def wav(path, data):
                with wave.open(str(path), 'wb') as f:
                    f.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
                    f.writeframes(data)
            old_pcm = b'\x01\x10' * 16000 + b'\x02\x20' * 16000
            wav(project / 'assets/audio.wav', old_pcm)
            wav(source / 'input/配音.wav', b'\x03\x30' * 8000 + b'\x02\x20' * 16000)
            (source / 'other/最终字幕.srt').write_text('1\n00:00:00,000 --> 00:00:01,000\n甲\n\n2\n00:00:01,000 --> 00:00:03,000\n乙\n', encoding='utf-8')
            scenes = [dict(slide_id='scene_001', start=0, end=2, text='甲'), dict(slide_id='scene_002', start=2, end=4, text='乙')]
            record = dict(id='p', revision=1, status='completed', scenes=scenes, audio='assets/audio.wav',
                          source_project={'id': 'j'}, logs=[], shots=[
                dict(id=str(i), slide_ids=[s['slide_id']], start=s['start'], end=s['end'], duration=2,
                     kind='video', video_status='completed', image_status='completed', video='keep.mp4')
                for i, s in enumerate(scenes)])
            job = SimpleNamespace(id='j', user_id=1, request={'dynamic_video': True})
            editor = Mock()
            editor.status.return_value = {'status': 'completed'}
            editor._project_dir.return_value = source
            with patch.object(studio, 'require_user', return_value={'id': 1}), \
                 patch.object(studio, 'directory', return_value=project), \
                 patch.object(studio, 'read', side_effect=lambda _: copy.deepcopy(record)), \
                 patch.object(studio, 'editable'), patch.object(studio, 'save'), \
                 patch.object(studio, 'narration_groups', return_value=[]), \
                 patch('backend.app.video_sources.dependencies', return_value=(SimpleNamespace(get=lambda _: job), None, None)), \
                 patch('backend.app.tts_editor.tts_editor', editor):
                result = studio.sync_edited_audio('p', studio.Review(revision=1), None)
            first, second = result['shots']
            self.assertTrue(first['audio_timing_adjustment']['needs_review'])
            self.assertNotIn('audio_timing_adjustment', second)
            self.assertEqual((first['end'], second['start'], second['end']), (1, 1, 3))
            self.assertEqual(second['video'], 'keep.mp4')
            self.assertEqual(first['source_subtitles'][0]['end'], 1)
            self.assertNotEqual(result['audio'], 'assets/audio.wav')
            self.assertTrue((project / result['subtitles']).is_file())
            with wave.open(str(project / 'assets/audio.wav')) as f:
                self.assertEqual(f.readframes(f.getnframes()), old_pcm)

    def test_shot_crossing_tts_chunks_retimes_as_one_visual_span(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            project, source = root / 'project', root / 'source'
            (project / 'assets').mkdir(parents=True)
            (source / 'input').mkdir(parents=True)
            (source / 'other').mkdir()
            old_pcm = b'\x01\x10' * 16000 + b'\x02\x20' * 16000 + b'\x04\x10' * 16000
            new_pcm = b'\x03\x30' * 8000 + b'\x02\x20' * 16000 + b'\x04\x10' * 16000
            for path, pcm in ((project / 'assets/audio.wav', old_pcm), (source / 'input/配音.wav', new_pcm)):
                with wave.open(str(path), 'wb') as f:
                    f.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
                    f.writeframes(pcm)
            (source / 'other/最终字幕.srt').write_text(
                '1\n00:00:00,000 --> 00:00:01,000\n问题\n\n'
                '2\n00:00:01,000 --> 00:00:03,000\n回答\n\n'
                '3\n00:00:03,000 --> 00:00:05,000\n下一话题\n', encoding='utf-8')
            scenes = [dict(slide_id=f'scene_00{i+1}', start=i*2, end=(i+1)*2, text=text)
                      for i, text in enumerate(('问题', '回答', '下一话题'))]
            narration = [{'slide_ids': ['scene_001']}, {'slide_ids': ['scene_002', 'scene_003']}]
            rows = [dict(id='cross', slide_ids=['scene_001', 'scene_002'], start=0, end=4, duration=4),
                    dict(id='next', slide_ids=['scene_003'], start=4, end=6, duration=2)]
            for row in rows:
                row.update(kind='video', video_status='completed', image_status='completed', video='keep.mp4')
            record = dict(id='p', revision=1, status='completed', scenes=scenes, audio='assets/audio.wav',
                          source_project={'id': 'j'}, logs=[], shots=rows, narration_groups=narration)
            job = SimpleNamespace(id='j', user_id=1, request={'dynamic_video': True})
            editor = Mock()
            editor.status.return_value = {'status': 'completed'}
            editor._project_dir.return_value = source
            with patch.object(studio, 'require_user', return_value={'id': 1}), \
                 patch.object(studio, 'directory', return_value=project), \
                 patch.object(studio, 'read', side_effect=lambda _: copy.deepcopy(record)), \
                 patch.object(studio, 'editable'), patch.object(studio, 'save'), \
                 patch.object(studio, 'narration_groups', return_value=narration), \
                 patch('backend.app.video_sources.dependencies', return_value=(SimpleNamespace(get=lambda _: job), None, None)), \
                 patch('backend.app.tts_editor.tts_editor', editor):
                result = studio.sync_edited_audio('p', studio.Review(revision=1), None)
            first, second = result['shots']
            self.assertEqual(first['slide_ids'], ['scene_001', 'scene_002'])
            self.assertEqual((first['start'], first['end'], first['duration']), (0, 3, 3))
            self.assertEqual((second['start'], second['end'], second['duration']), (3, 5, 2))
            self.assertTrue(first['audio_timing_adjustment']['needs_review'])
            self.assertNotIn('audio_timing_adjustment', second)
            self.assertEqual(first['video'], 'keep.mp4')
            self.assertEqual(second['video'], 'keep.mp4')
            with wave.open(str(project / result['audio'])) as f:
                self.assertEqual(f.readframes(f.getnframes()), new_pcm)
