import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend.app.subtitle_layout import normalize, presentation_env, apply_html, wrap, write_ass, styled_srt
from module5_video_render import build_direct_filter_script, build_subtitle_burn_command


class SubtitleLayoutTests(unittest.TestCase):
    def test_landscape_compatible_canvas(self):
        p = normalize()
        self.assertEqual((p['width'], p['height'], p['size']), (1920, 1080, 36))

    def test_portrait_defaults(self):
        p = normalize({'video_orientation': 'portrait'})
        self.assertEqual((p['width'],p['height'],p['size'],p['max_chars'],p['position']), (1080,1920,56,14,75))

    def test_layouts_independent(self):
        data = {'video_orientation':'portrait','subtitle_layouts':{'landscape':{'size':42},'portrait':{'size':60}}}
        self.assertEqual(normalize(data)['size'],60)
        data['video_orientation']='landscape'
        self.assertEqual(normalize(data)['size'],42)

    def test_untrusted_values_are_bounded(self):
        p = normalize({'subtitle_layouts':{'landscape':{'size':999,'color':'red;bad','position':float('nan'),'font':'bad</style>'}}})
        self.assertEqual(p['size'],100)
        self.assertEqual(p['color'],'#ffffff')
        self.assertEqual(p['position'],95)
        self.assertNotIn('<',p['font'])
        self.assertEqual(normalize({'subtitle_layouts': []})['size'],36)
        self.assertEqual(normalize({'subtitle_layouts': {'landscape': []}})['size'],36)

    def test_env_is_request_scoped(self):
        before = os.environ.copy()
        env = presentation_env({'video_orientation':'portrait'})
        self.assertEqual(env['RUNNINGHUB_TARGET_RATIO'],'9:16')
        self.assertEqual(dict(os.environ),before)

    def test_browser_canvas(self):
        page = apply_html('<head></head><div data-width="1920" data-height="1080"></div>',normalize({'video_orientation':'portrait'}))
        self.assertIn('data-width="1080"',page)
        self.assertIn('data-height="1920"',page)
        self.assertIn('top:75.0%',page)

    def test_short_lines(self):
        p=normalize({'video_orientation':'portrait'})
        self.assertTrue(all(len(s)<=14 for s in wrap('这是一段专门用来检查竖屏字幕换行规则的示例文案',p).splitlines()))

    def test_srt_preserved_and_ass_uses_canvas(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); source=root/'original.srt'
            text='1\n00:00:01,000 --> 00:00:03,500\n这是一段专门用来检查竖屏字幕换行规则的示例文案\n'
            source.write_text(text,encoding='utf8')
            p=normalize({'video_orientation':'portrait'})
            styled_srt(source,root/'browser.srt',p)
            ass=write_ass(source,root/'styled.ass',p).read_text(encoding='utf8')
            self.assertEqual(source.read_text(encoding='utf8'),text)
            self.assertIn('PlayResX: 1080',ass)
            self.assertIn('PlayResY: 1920',ass)
            self.assertIn('\\pos(540.0,1440.0)',ass)
            self.assertIn('00:00:01.00,00:00:03.50',ass)
            self.assertIn('\\N',ass)

    def test_portrait_ffmpeg_geometry(self):
        with patch.dict(os.environ,presentation_env({'video_orientation':'portrait'})):
            script,_=build_direct_filter_script([{'start':0}],total_duration=3,fps=30,fade_duration=0.8)
        self.assertIn('scale=1080:1920',script)
        self.assertNotIn('1836',script)

    def test_landscape_ffmpeg_geometry(self):
        with patch.dict(os.environ,{'OCV_PRESENTATION_JSON':'{}'}):
            script,_=build_direct_filter_script([{'start':0}],total_duration=3,fps=30,fade_duration=0.8)
        self.assertIn('pad=1920:1080:42:54',script)

    def test_burn_uses_shared_ass(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,presentation_env({'video_orientation':'portrait'})):
            root=Path(tmp);source=root/'a.srt'
            source.write_text('1\n00:00:00,000 --> 00:00:02,000\n测试字幕\n',encoding='utf8')
            cmd=build_subtitle_burn_command(root/'a.mp4',source,root/'result.mp4',use_nvenc=False,ffmpeg=Path('ffmpeg'))
            self.assertIn('ass=filename=',cmd[cmd.index('-vf')+1])
            self.assertTrue((root/'result.layout.ass').is_file())

    def test_image_provider_portrait_both_routes(self):
        from module4_video_render import _provider_configs
        env={**presentation_env({'video_orientation':'portrait'}),'USE_CLOUD_IMAGE_POOL':'true','CLOUD_IMAGE_POOL_BASE_URL':'https://example.invalid','CLOUD_IMAGE_POOL_ACCESS_TOKEN':'test'}
        with patch.dict(os.environ,env):
            self.assertEqual(_provider_configs()[0]['ratio'],'9:16')


if __name__=='__main__':
    unittest.main()
