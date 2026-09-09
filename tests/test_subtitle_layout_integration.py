"""Opt-in local render smoke test. No API, TTS, image generation or billing calls."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import wave
from unittest.mock import patch

from backend.app.subtitle_layout import presentation_env


@unittest.skipUnless(os.getenv('OCV_RENDER_SMOKE') == '1', 'set OCV_RENDER_SMOKE=1 for local FFmpeg rendering')
class RenderSmokeTests(unittest.TestCase):
    def test_landscape_and_portrait_both_editions(self):
        from module4_video_render import write_html
        from module5_video_render import require_ffmpeg_binary
        project=Path(__file__).resolve().parents[1]
        ffprobe=require_ffmpeg_binary().with_name('ffprobe.exe' if os.name=='nt' else 'ffprobe')
        for orientation,dimensions in [('landscape',(1920,1080)),('portrait',(1080,1920))]:
            with self.subTest(orientation=orientation), tempfile.TemporaryDirectory(prefix='ocv-layout-smoke-') as temp:
                root=Path(temp);audio=root/'2_audio_srt';visual=root/'3_visual_template';assets=visual/'assets'
                audio.mkdir();assets.mkdir(parents=True)
                with wave.open(str(audio/'final_output.wav'),'wb') as wav:
                    wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(24000);wav.writeframes(b'\0\0'*24000)
                # Tiny, generated test pattern; not a user asset.
                (assets/'poster_001_fixture.ppm').write_bytes(b'P6\n8 8\n255\n'+bytes([40,70,90])*64)
                srt='1\n00:00:00,000 --> 00:00:01,000\n这是本地字幕渲染验证，不调用任何付费服务。\n'
                (audio/'final_short.srt').write_text(srt,encoding='utf8')
                scenes=[{'slide_id':'scene_001','start':0,'end':1}]
                mapping=[{'macro_scene_id':'poster_001','includes_slides':['scene_001'],'asset_filename':'poster_001_fixture.ppm'}]
                (visual/'fine_grained_timeline.json').write_text(json.dumps(scenes),encoding='utf8')
                (visual/'poster_mapping.json').write_text(json.dumps(mapping),encoding='utf8')
                env={**os.environ,**presentation_env({'video_orientation':orientation}),
                     'OCV_RENDER_WORKSPACE_DIR':str(root),'VIDEO_RENDER_VARIANT':'both',
                     'VIDEO_RENDER_ENGINE':'ffmpeg','VIDEO_RENDER_GPU_ENCODING':'0','VIDEO_RENDER_FPS':'12',
                     'BGM_TRACKS_JSON':'[]','PYTHONIOENCODING':'utf-8'}
                with patch.dict(os.environ,env):
                    write_html(scenes,[{'start':0,'end':1,'url':'./assets/poster_001_fixture.ppm'}],visual/'index.html')
                result=subprocess.run([sys.executable,'-B',str(project/'module5_video_render.py')],env=env,cwd=project,capture_output=True,text=True,encoding='utf8',timeout=90)
                self.assertEqual(result.returncode,0,result.stdout[-2000:]+result.stderr[-2000:])
                for name in ('final_raw_presentation.mp4','final_with_subtitles.mp4'):
                    output=root/'4_final_video'/name
                    self.assertTrue(output.is_file())
                    data=json.loads(subprocess.check_output([str(ffprobe),'-v','error','-select_streams','v:0','-show_entries','stream=width,height','-of','json',str(output)]))
                    self.assertEqual((data['streams'][0]['width'],data['streams'][0]['height']),dimensions)
                self.assertEqual((audio/'final_short.srt').read_text(encoding='utf8'),srt)


if __name__=='__main__':
    unittest.main()
