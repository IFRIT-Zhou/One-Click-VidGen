"""Dynamic layout regression: render geometry, shared captions and persistence."""
import copy
import base64
import io
import subprocess
import tempfile
import unittest
import wave
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from fastapi import HTTPException
from backend.app import video_export as export
from backend.app.subtitle_layout import normalize, write_ass


class DynamicExportLayoutTests(unittest.TestCase):
    def test_legacy_defaults_and_safe_bounds(self):
        old = export.presentation({'settings': {'ratio': '9:16'}})
        self.assertEqual((old['width'], old['height'], old['frame_scale'], old['frame_x']), (1080, 1920, 1, 50))
        p = normalize({'subtitle_layouts': {'landscape': {'frame_scale': 99, 'frame_x': -40, 'frame_y': float('nan'), 'x_position': 100}}})
        self.assertEqual((p['frame_scale'], p['frame_x'], p['frame_y'], p['x_position']), (2, 0, 50, 95))

    def test_canvas_ratio_is_authoritative_and_separate(self):
        layouts = {'portrait': {'size': 62, 'frame_scale': .8}, 'landscape': {'size': 32, 'frame_scale': .9}}
        p = export.presentation({'settings': {'ratio': '9:16'}, 'creation_parameters': {'video_orientation': 'landscape', 'subtitle_layouts': layouts}})
        self.assertEqual((p['size'], p['frame_scale']), (62, .8))

    def test_subtitle_position_has_explicit_canvas(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); srt = root / 's.srt'
            srt.write_text('1\n00:00:00,000 --> 00:00:01,000\n测试字幕\n', encoding='utf8')
            p = normalize({'subtitle_layouts': {'landscape': {'size': 36, 'x_position': 40, 'position': 88}}})
            result = write_ass(srt, root / 's.ass', p).read_text(encoding='utf8')
            self.assertIn('PlayResX: 1920', result)
            self.assertIn('PlayResY: 1080', result)
            self.assertIn(r'\pos(768.0,950.4)', result)

    def test_export_saves_layout_without_changing_shots(self):
        record = {'id': 'layout', 'status': 'video_review', 'revision': 3, 'logs': [], 'creation_parameters': {},
                  'shots': [{'id': 's', 'kind': 'video', 'video_status': 'completed'}]}
        shots = copy.deepcopy(record['shots'])
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            path = Path(tmp)
            for name, result in [('require_user', {'id': 1}), ('directory', path), ('read', record), ('editable', None), ('save', None)]:
                stack.enter_context(patch.object(export.studio, name, return_value=result))
            stack.enter_context(patch.object(export.threading, 'Thread'))
            layouts = {'landscape': {'frame_scale': .8, 'position': 90}}
            try:
                export.start_export('layout', export.ExportRequest(revision=3, subtitle_layouts=layouts), None)
                self.assertEqual(record['creation_parameters']['subtitle_layouts'], layouts)
                self.assertEqual(record['shots'], shots)
            finally:
                export.studio.ACTIVE.discard(str(path))

    def test_ffmpeg_frame_geometry_and_clipping(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root / 'source.png'
            Image.new('RGB', (320, 180), 'red').save(source)
            for scale, x, expected in [(1, 50, (True, True)), (.5, 50, (False, True)), (.5, 75, (False, False)), (2, 50, (True, True))]:
                p = normalize(); p.update(width=320, height=180, frame_scale=scale, frame_x=x)
                out = root / f'{scale}-{x}.png'
                result = subprocess.run([export.ffmpeg_binary(), '-y', '-loop', '1', '-i', str(source), '-vf', export.frame_filter(p), '-frames:v', '1', str(out)], capture_output=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
                with Image.open(out) as im:
                    self.assertEqual(im.size, (320, 180))
                    self.assertEqual((im.getpixel((10, 90))[0] > 200, im.getpixel((120, 90))[0] > 200), expected)

    def test_real_preview_uses_saved_canvas_and_requested_layout(self):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            root = Path(tmp)
            Image.new('RGB', (320, 180), 'red').save(root / 'image.png')
            record = {'settings': {'ratio': '9:16'}, 'shots': [{'id': 's', 'image': 'image.png'}]}
            for name, value in [('require_user', {'id': 1}), ('directory', root), ('read', record)]:
                stack.enter_context(patch.object(export.studio, name, return_value=value))
            before = copy.deepcopy(record)
            data = export.LayoutPreviewRequest(shot_id='s', subtitle_layouts={'portrait': {'frame_scale': .8}}, video_render_variant='raw')
            result = export.layout_preview('p', data, None)
            with Image.open(io.BytesIO(base64.b64decode(result['image'].split(',')[1]))) as im:
                self.assertEqual(im.size, (1080, 1920))
                self.assertLess(im.getpixel((50, 50))[0], 10)
                self.assertGreater(im.getpixel((540, 960))[0], 200)
            self.assertEqual(record, before, 'Preview must not save edits or mutate shots')

    def test_missing_preview_asset_is_actionable(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(export.studio, 'require_user', return_value={'id': 1}), patch.object(export.studio, 'directory', return_value=Path(tmp)), patch.object(export.studio, 'read', return_value={'shots': [{'id': 's'}]}):
            with self.assertRaises(HTTPException) as caught:
                export.layout_preview('p', export.LayoutPreviewRequest(shot_id='s', subtitle_layouts={}), None)
            self.assertEqual(caught.exception.status_code, 409)

    def test_complete_render_keeps_duration_and_both_editions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); project = root / 'project'; project.mkdir()
            Image.new('RGB', (320, 180), 'red').save(project / 'image.png')
            with wave.open(str(project / 'audio.wav'), 'wb') as wav:
                wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(24000); wav.writeframes(b'\0\0' * 24000)
            (project / 'subtitles.srt').write_text('1\n00:00:00,000 --> 00:00:01,000\n字幕布局测试\n', encoding='utf8')
            record = {'settings': {'name': 'test', 'ratio': '16:9'}, 'audio': 'audio.wav', 'subtitles': 'subtitles.srt',
                      'creation_parameters': {'subtitle_layouts': {'landscape': {'frame_scale': .8, 'frame_y': 45, 'position': 93}}},
                      'export_settings': {'use_video_audio': False}, 'scenes': [{'end': 1}],
                      'shots': [{'id': 's', 'kind': 'static', 'image': 'image.png', 'start': 0, 'end': 1}]}
            with patch.object(export, 'PROJECT_ROOT', root):
                _, raw, sub = export._render(project, record)
            for asset in [raw, sub]:
                self.assertTrue(asset.is_file())
                self.assertAlmostEqual(export.probe_media_duration(asset), 1, delta=.1)
            self.assertIn('PlayResX: 1920', (project/'export_work/subtitles.ass').read_text(encoding='utf8'))


if __name__ == '__main__':
    unittest.main()
