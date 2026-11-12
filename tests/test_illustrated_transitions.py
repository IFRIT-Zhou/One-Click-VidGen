import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from backend.app.illustrated_transitions import normalize, xfade, KINDS
from module5_video_render import build_direct_filter_script, require_ffmpeg_binary

class IllustratedTransitionsTests(unittest.TestCase):
    def test_default_none_preserves_durations(self):
        with patch.dict(os.environ,{'OCV_IMAGE_TRANSITION':'none'}):
            script,label=build_direct_filter_script([{'start':0},{'start':3}],6,fps=30,fade_duration=.8)
        self.assertEqual(label,'out')
        self.assertNotIn('xfade',script)
        self.assertIn('concat=n=2',script)
        self.assertIn('trim=duration=3.000000',script)

    def test_all_effects_compile_in_bundled_ffmpeg(self):
        ffmpeg=require_ffmpeg_binary()
        for kind in sorted(KINDS-{'none'}):
            with self.subTest(kind=kind):
                # Small synthetic inputs, no app service, assets or paid API.
                effect=xfade(kind)
                result=subprocess.run([str(ffmpeg),'-v','error','-f','lavfi','-i','color=red:s=96x64:r=30:d=2',
                    '-f','lavfi','-i','color=blue:s=96x64:r=30:d=2',
                    '-filter_complex',f'[0:v][1:v]xfade={effect}:duration=0.8:offset=0.5[out]',
                    '-map','[out]','-t','2','-f','null','-'],capture_output=True,timeout=20,
                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))

    def test_reject_unknown(self):
        with self.assertRaises(ValueError):normalize('untrusted')

if __name__=='__main__':unittest.main()
