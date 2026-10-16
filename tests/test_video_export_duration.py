import copy
import json
import shutil
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from backend.app import video_export as export


class ExportResolutionTests(unittest.TestCase):
    def test_completed_2k_cloud_clip_controls_canvas_and_subtitle_scale(self):
        shot=dict(kind='video',video='clip.mp4',video_status='completed',
                  video_request=dict(backend='cloud_h3',width=2048,height=1152))
        for ratio,dimensions in [('16:9',(2048,1152)),('9:16',(1152,2048))]:
            with self.subTest(ratio=ratio):
                record=dict(settings=dict(ratio=ratio),shots=[shot])
                layout=export.presentation(record)
                self.assertEqual(export._size(record),dimensions)
                self.assertEqual((layout['width'],layout['height']),dimensions)
                self.assertAlmostEqual(layout['size'],(56 if ratio=='9:16' else 36)*16/15)
                self.assertAlmostEqual(layout['outline'],2*16/15)
                self.assertIn(f'color=c=black:s={dimensions[0]}x{dimensions[1]}',export.frame_filter(layout))

    def test_settings_or_unfinished_or_other_provider_do_not_upscale_legacy_output(self):
        base=dict(kind='video',video='clip.mp4',video_status='completed',
                  video_request=dict(backend='cloud_h3',width=2048,height=1152))
        variants=[]
        for field,value in [('video_status','unknown'),('video',''),('kind','static')]:
            shot=copy.deepcopy(base);shot[field]=value;variants.append(shot)
        for request in [dict(backend='cloud_h3',width=1280,height=720),
                        dict(backend='cloud_h3',width=640,height=384),
                        dict(backend='comfyui',width=2048,height=1152),{}]:
            shot=copy.deepcopy(base);shot['video_request']=request
            shot['video_generation_options']=dict(backend='cloud_h3',resolution='custom',width=2048,height=1152)
            variants.append(shot)
        for shot in variants:
            for ratio,dimensions in [('16:9',(1920,1080)),('9:16',(1080,1920))]:
                with self.subTest(shot=shot,ratio=ratio):
                    record=dict(settings=dict(ratio=ratio),creation_parameters=dict(video_generation_backend='cloud_h3'),shots=[shot])
                    layout=export.presentation(record)
                    self.assertEqual(export._size(record),dimensions)
                    self.assertEqual((layout['width'],layout['height']),dimensions)


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

    def test_real_2k_clip_exports_both_variants_without_downsampling(self):
        for ratio,width,height in [('16:9',2048,1152),('9:16',1152,2048)]:
            with self.subTest(ratio=ratio),tempfile.TemporaryDirectory() as directory:
                root=Path(directory)
                source=root/'source.mp4'
                subprocess.run([export.ffmpeg_binary(),'-y','-f','lavfi','-i',
                                f'color=c=green:s={width}x{height}:r=30:d=0.5',
                                '-c:v','libx264','-preset','ultrafast','-pix_fmt','yuv420p',str(source)],
                               check=True,capture_output=True)
                with wave.open(str(root/'audio.wav'),'wb') as stream:
                    stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(16000)
                    stream.writeframes(b'\0\0'*8000)
                (root/'subtitles.srt').write_text('1\n00:00:00,000 --> 00:00:00,500\nTest\n',encoding='utf-8')
                record=dict(settings=dict(name='2k-test',ratio=ratio),audio='audio.wav',subtitles='subtitles.srt',
                            scenes=[dict(end=.5)],shots=[dict(id='s',kind='video',video='source.mp4',
                            video_status='completed',start=0,end=.5,
                            video_request=dict(backend='cloud_h3',width=width,height=height))])
                with patch.object(export,'PROJECT_ROOT',root):
                    _,raw,subtitled=export._render(root,record)
                for output in (raw,subtitled):
                    probe=subprocess.run([export.ffprobe_binary(),'-v','error','-select_streams','v:0',
                                          '-show_entries','stream=width,height','-of','json',str(output)],
                                         check=True,capture_output=True,text=True)
                    stream=json.loads(probe.stdout)['streams'][0]
                    self.assertEqual((stream['width'],stream['height']),(width,height))
                ass=(root/'export_work'/'subtitles.ass').read_text(encoding='utf-8')
                self.assertIn(f'PlayResX: {width}',ass);self.assertIn(f'PlayResY: {height}',ass)


if __name__ == '__main__':
    unittest.main()
