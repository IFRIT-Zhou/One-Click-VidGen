import shutil
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from backend.app import video_export as export


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
class ExportDurationTests(unittest.TestCase):
    def test_packet_duration_fallback_ignores_audio(self):
        responses = [dict(streams=[{}]), dict(packets=[dict(pts_time='2', duration_time='.5'),
                                                      dict(pts_time='2.5', duration_time='.5')])]
        with patch.object(export.subprocess, 'run', side_effect=[
                subprocess.CompletedProcess([], 0, stdout=__import__('json').dumps(value)) for value in responses]):
            self.assertEqual(export._video_duration(Path('source.mkv')), 1)

    def test_audio_track_must_not_disguise_short_video_track(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.mp4'
            # Comfy output: 2 seconds of video but 4 seconds of audio/container.
            subprocess.run([export.ffmpeg_binary(), '-y', '-f', 'lavfi', '-i',
                            'testsrc2=s=160x90:r=30:d=2', '-f', 'lavfi', '-i',
                            'anullsrc=r=48000:cl=stereo', '-t', '4', '-c:v', 'libx264',
                            '-c:a', 'aac', str(source)], check=True, capture_output=True)
            with wave.open(str(root / 'audio.wav'), 'wb') as stream:
                stream.setnchannels(1)
                stream.setsampwidth(2)
                stream.setframerate(16000)
                stream.writeframes(b'\0\0' * 64000)
            (root / 'subtitles.srt').write_text('1\n00:00:00,000 --> 00:00:04,000\nTest\n', encoding='utf-8')
            record = dict(settings=dict(name='test', ratio='16:9'), audio='audio.wav',
                          subtitles='subtitles.srt', scenes=[dict(end=4)],
                          shots=[dict(id='s', kind='video', video='source.mp4',
                                      video_status='completed', start=0, end=4)])
            layout = export.presentation(record)
            layout.update(width=160, height=90)
            with patch.object(export, 'PROJECT_ROOT', root), patch.object(export, 'presentation', return_value=layout):
                _, raw, subtitled = export._render(root, record)
            for output in (raw, subtitled):
                self.assertAlmostEqual(export.probe_media_duration(output), 4, delta=.1)


if __name__ == '__main__':
    unittest.main()
