import tempfile
import unittest
import subprocess
from pathlib import Path
from unittest.mock import patch
from backend.app import video_bookends as bookends
from backend.app.pipeline import ffmpeg_binary


class BookendsTests(unittest.TestCase):
    def test_no_selection_leaves_output_untouched(self):
        with patch.object(bookends, 'probe') as probe:
            bookends.apply_bookends(Path('not-created.mp4'), 1, {})
            probe.assert_not_called()

    def test_non_video_rejected(self):
        with patch('backend.app.editor.upload_path', return_value=Path('a.png')):
            with self.assertRaisesRegex(ValueError, '视频'):
                bookends.resolve_clips(1, {'intro_video_id':'a.png'})

    def test_real_join_mixed_sizes_audio_and_silent_clips(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            intro,body,outro=[root/name for name in ('intro.mp4','body.mp4','outro.mp4')]
            for path,color,size,audio in [(intro,'red','80x120',False),(body,'green','160x90',True),(outro,'blue','128x72',True)]:
                args=[ffmpeg_binary(),'-y','-f','lavfi','-i',f'color={color}:s={size}:r=24:d=0.5']
                if audio: args+=['-f','lavfi','-i','sine=frequency=440:duration=0.5']
                args+=['-c:v','libx264','-pix_fmt','yuv420p','-t','0.5',str(path)]
                subprocess.run(args,check=True,capture_output=True,timeout=30)
            with patch.object(bookends,'resolve_clips',return_value=[intro,outro]):
                bookends.apply_bookends(body,1,{})
            video,duration,audio=bookends.probe(body)
            self.assertEqual((video['width'],video['height']),(160,90))
            self.assertTrue(audio)
            self.assertAlmostEqual(duration,1.5,delta=0.15)
            self.assertEqual(list(root.glob('bookends-*')),[])
            # Joining keeps the correct order, including letterboxed first clip.
            for time,channel in [(0.2,0),(1.2,2)]:
                p=subprocess.run([ffmpeg_binary(),'-ss',str(time),'-i',str(body),'-vf','crop=2:2:79:44',
                                  '-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','-'],capture_output=True,check=True)
                pixel=p.stdout[:3]
                self.assertGreater(pixel[channel],150)

    def test_failure_does_not_replace_body(self):
        with tempfile.TemporaryDirectory() as directory:
            body=Path(directory)/'body.mp4';body.write_bytes(b'original')
            with patch.object(bookends,'resolve_clips',return_value=[Path('intro.mp4'),None]),patch.object(bookends,'probe',side_effect=ValueError('bad media')):
                with self.assertRaises(ValueError):bookends.apply_bookends(body,1,{})
            self.assertEqual(body.read_bytes(),b'original')
