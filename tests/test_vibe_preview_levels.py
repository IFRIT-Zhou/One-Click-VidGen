"""General preview-tier contract; no production assets or model calls."""
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from PIL import Image

from backend.app import vibe_code_renderer as renderer


class PreviewLevelTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='ocv-preview-levels-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.background = self.root/'background.png'
        Image.new('RGB', (640, 360), '#102030').save(self.background)
        self.inputs = []
        self.override = {}
        self.measured_override = {}

    def fake_run(self, command, **kwargs):
        source = json.loads(Path(command[command.index('--input')+1]).read_text(encoding='utf-8'))
        self.inputs.append(source)
        output = Path(command[command.index('--output')+1])
        full = source['preview_level'] == 'full'
        frames = [dict(id=name, file=f'frame_{name}.png', frame=frame, time=frame/30)
                  for name, frame in [('early',15),('middle',30),('hold',51),('end',57)]] if full else []
        measured = dict(texts=[], assets=[], diagnostics=[], frames=frames)
        measured.update(self.measured_override)
        (output/'measurements.json').write_text(json.dumps(measured), encoding='utf-8')
        Image.new('RGB', (640, 360), '#102030').save(output/'poster.png')
        for frame in frames:
            Image.new('RGB', (640, 360), '#102030').save(output/frame['file'])
        meta = dict(renderer=renderer.RENDERER, plan_version=renderer.VERSION,
                    mode=source['mode'], width=640, height=360, fps=30, frames=60,
                    preview_level=source['preview_level'], proof_complete=full,
                    checks=dict(animation='checked' if full else 'not_checked'), stage_frames=frames)
        meta.update(self.override)
        (output/'metadata.json').write_text(json.dumps(meta), encoding='utf-8')
        if source['mode'] == 'video':
            (output/'silent.mp4').write_bytes(b'offline-fake-video')

    def render(self, **options):
        output = self.root/f'render-{len(self.inputs)}'
        output.mkdir()
        params = dict(mode='preview', code='function Scene(ctx) { return <Text id="title">A</Text>; }',
                      canvas=[640,360],duration=2,assets={'background':self.background},
                      geometry={},output_dir=output)
        params.update(options)
        with patch.object(renderer.host, '_runtime_paths', return_value=(Path('node'),Path('browser'))), \
                patch.object(renderer.host, '_run', side_effect=self.fake_run), \
                patch.object(renderer.host, '_proof', return_value=[.5,1,1.7,1.9]) as proof:
            result = renderer.run(**params)
        return result, output, proof

    def test_default_preserves_full_proof(self):
        result, output, proof = self.render()
        self.assertEqual(self.inputs[-1]['preview_level'], 'full')
        self.assertTrue(result['proof_complete'])
        self.assertEqual(len(result['stage_frames']), 4)
        self.assertEqual(len(list(output.glob('frame_*.png'))), 4)
        proof.assert_called_once()

    def test_layout_preserves_canvas_measurement_without_temporal_claim(self):
        result, output, proof = self.render(preview_level='layout')
        self.assertEqual(result['preview_level'], 'layout')
        self.assertFalse(result['proof_complete'])
        self.assertEqual(result['stage_frames'], [])
        self.assertEqual(result['checks']['animation'], 'not_checked')
        with Image.open(output/'poster.png') as image:
            self.assertEqual(image.size, (640,360))
        self.assertFalse(list(output.glob('frame_*.png')))
        proof.assert_not_called()

    def test_video_always_upgrades_layout_to_full(self):
        result, output, proof = self.render(mode='video', preview_level='layout')
        self.assertEqual(self.inputs[-1]['preview_level'], 'full')
        self.assertTrue(result['proof_complete'])
        self.assertTrue((output/'silent.mp4').is_file())
        proof.assert_called_once()

    def test_invalid_level_rejected_before_runtime(self):
        with patch.object(renderer.host, '_runtime_paths') as runtime:
            with self.assertRaisesRegex(ValueError, '预览级别'):
                renderer.run(mode='preview',code='',canvas=[640,360],duration=2,
                    assets={},geometry={},output_dir=self.root,preview_level='turbo')
        runtime.assert_not_called()

    def test_renderer_cannot_claim_layout_is_full_proof(self):
        self.override = dict(proof_complete=True)
        with self.assertRaisesRegex(ValueError, '快速校样'):
            self.render(preview_level='layout')

    def test_full_rejects_missing_temporal_frame(self):
        self.override = dict(stage_frames=[])
        with self.assertRaisesRegex(ValueError, '关键帧'):
            self.render()

    def test_full_rejects_metadata_with_missing_real_measurements(self):
        self.measured_override = dict(frames=[])
        with self.assertRaisesRegex(ValueError, '关键帧测量'):
            self.render()


@unittest.skipUnless(os.environ.get('OCV_TEST_REAL_PREVIEW_LEVELS') == '1', 'requires local Node/Chrome')
class RealPreviewLevelTests(unittest.TestCase):
    def test_layout_and_full_outputs_keep_native_canvas(self):
        with tempfile.TemporaryDirectory(prefix='ocv-real-preview-levels-') as temporary:
            root = Path(temporary)
            background = root/'background.png'
            Image.new('RGB', (640,360), '#102030').save(background)
            code = '''function Scene(ctx) {
              const p = ctx.progress(0, 0.4);
              return <div style={{position:'absolute',inset:0}}>
                <Text id="title" style={{position:'absolute',left:40,top:30,fontSize:32,opacity:p}}>Preview tiers</Text>
                <div style={{position:'absolute',left:40,top:120,width:400*p,height:25,background:'#55DDFF'}} />
              </div>;
            }'''
            for attempt, level in enumerate(('layout','full','layout','full')):
                output = root/f'{attempt}-{level}'
                output.mkdir()
                began = time.monotonic()
                result = renderer.run(mode='preview', preview_level=level,code=code,canvas=[640,360],duration=2,
                    assets={'background':background},geometry={},output_dir=output)
                print(json.dumps(dict(preview_test=level,attempt=attempt+1,
                    wall_seconds=round(time.monotonic()-began,3),
                    renderer_seconds=result['render_seconds']),ensure_ascii=False),flush=True)
                with Image.open(output/'poster.png') as image:
                    self.assertEqual(image.size,(640,360))
                self.assertTrue(result['measurements']['texts'][0]['visible'])
                self.assertEqual(result['proof_complete'],level=='full')
                self.assertEqual(len(result['stage_frames']),4 if level=='full' else 0)
                self.assertEqual(len(list(output.glob('frame_*.png'))),4 if level=='full' else 0)


if __name__ == '__main__':
    unittest.main()
