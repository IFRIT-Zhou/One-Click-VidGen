"""Offline Remotion boundary tests: no npm, browser, paid API or project writes."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import wave

from PIL import Image

from backend.app import vibe_remotion as renderer
from backend.app.vibe_motion_plan import MotionPlan


class RemotionBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.out = self.root/'out'; self.out.mkdir()
        self.audio = self.root/'voice.wav'
        with wave.open(str(self.audio), 'wb') as out:
            out.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            out.writeframes(b'\0\0' * 32000)
        self.image = self.root/'original.png'; Image.new('RGB', (640, 360), 'white').save(self.image)
        self.plan = MotionPlan(title='结果', canvas=(640, 360), stages=[
            dict(id='results', layout='evidence', cue='a', headline='原始证据',
                 items=[dict(id='image', title='原图', asset_id='core')])])

    def invoke(self):
        return renderer.render_motion(plan=self.plan, assets={'core': self.image}, cues={'a': 0},
            audio=self.audio, start=0, duration=2, output_dir=self.out, ffmpeg='test-ffmpeg', timeout=30)

    def fake_process(self, command, **kwargs):
        if '--input' in command:
            payload = json.loads(Path(command[command.index('--input')+1]).read_text(encoding='utf-8'))
            self.assertEqual(payload['plan']['stages'][0]['start'], 0)
            self.assertEqual(payload['plan']['stages'][0]['end'], 2)
            self.assertEqual(payload['assets']['core'], str(self.image.resolve()))
            self.assertNotIn('cue', payload['plan']['stages'][0])
            (self.out/'silent.mp4').write_bytes(b'mocked-video')
            for name in ('detail_early.png', 'detail_middle.png', 'detail_end.png', 'stage_00.png'):
                Image.new('RGB', (640, 360), '#315678').save(self.out/name)
            (self.out/'metadata.json').write_text(json.dumps({
                'renderer': 'ocv-remotion-v1', 'width': 640, 'height': 360, 'fps': 30,
                'frames': 60, 'duration_seconds': 2,
                'detail_times': {'early': .5, 'middle': 1, 'end': 1.7},
                'stage_frames': [{'id': 'results', 'file': 'stage_00.png', 'time': 1}],
                'checks': {'layout': 'passed'}}), encoding='utf-8')
        else:
            self.assertEqual(command[0], 'test-ffmpeg')
            self.assertIn('-c:v', command)
            self.assertIn('copy', command)
            self.assertEqual(command[command.index('-t')+1], '2.000000')
            self.assertIn(str(self.audio), command)
            (self.out/'preview.mp4').write_bytes(b'mocked-with-original-narration')
        return ''

    def test_valid_semantic_plan_uses_real_engine_entry_and_original_audio_mux(self):
        before = self.audio.read_bytes()
        with patch.object(renderer, '_runtime_paths', return_value=(Path('test-node'), Path('test-browser'))), \
             patch.object(renderer, '_run', side_effect=self.fake_process) as run:
            metrics = self.invoke()
        self.assertEqual(run.call_count, 2)
        self.assertTrue(run.call_args_list[0].args[0][1].endswith('render.mjs'))
        self.assertEqual(metrics['renderer'], 'ocv-remotion-v1')
        self.assertEqual(metrics['frames'], 60)
        self.assertTrue((self.out/'proof.jpg').is_file())
        self.assertEqual(self.audio.read_bytes(), before)

    def test_wrong_frame_count_cannot_mux_or_publish(self):
        def wrong(command, **kwargs):
            self.fake_process(command, **kwargs)
            metadata = json.loads((self.out/'metadata.json').read_text(encoding='utf-8'))
            metadata['frames'] = 65
            (self.out/'metadata.json').write_text(json.dumps(metadata), encoding='utf-8')
        with patch.object(renderer, '_runtime_paths', return_value=(Path('node'), Path('browser'))), \
             patch.object(renderer, '_run', side_effect=wrong) as run:
            with self.assertRaisesRegex(ValueError, '帧数'): self.invoke()
        self.assertEqual(run.call_count, 1)
        self.assertFalse((self.out/'preview.mp4').exists())

    def test_empty_or_unbound_asset_never_starts_renderer(self):
        self.image.unlink()
        with patch.object(renderer, '_run') as run:
            with self.assertRaisesRegex(ValueError, '文件不存在'): self.invoke()
        run.assert_not_called()

    def test_preflight_must_return_ready_and_never_install(self):
        with patch.object(renderer, '_runtime_paths', return_value=(Path('node'), Path('browser'))), \
             patch.object(renderer, '_run', return_value='{"ready":true,"renderer":"ocv-remotion-v1"}\n') as run:
            result = renderer.preflight_remotion()
        command = run.call_args.args[0]
        self.assertIn('--check', command)
        self.assertNotIn('npm', ' '.join(command))
        self.assertEqual(result['renderer'], 'ocv-remotion-v1')
        with patch.object(renderer, '_runtime_paths', return_value=(Path('node'), Path('browser'))), \
             patch.object(renderer, '_run', return_value='{"ready":false}'):
            with self.assertRaisesRegex(RuntimeError, '未调用云端'): renderer.preflight_remotion()

    def test_preflight_legacy_does_not_require_capability_lists(self):
        payload = {'ready': True, 'renderer': 'ocv-remotion-v1'}
        with patch.object(renderer, '_runtime_paths', return_value=(Path('node'), Path('browser'))), \
             patch.object(renderer, '_run', return_value=json.dumps(payload)):
            self.assertEqual(renderer.preflight_remotion(),
                             {'node': 'node', 'browser': 'browser', 'renderer': 'ocv-remotion-v1'})

    def test_preflight_new_capabilities_fail_closed_before_cloud(self):
        required_renderer = 'ocv-remotion-shotcraft-v1'
        required_plan = 'ocv-shotcraft-v1'
        valid = {'ready': True, 'renderer': 'ocv-remotion-v1',
                 'renderers': ['ocv-remotion-v1', required_renderer],
                 'plan_versions': ['ocv-motion-v1', required_plan]}
        cases = [
            {'ready': True, 'renderer': 'ocv-remotion-v1'},
            {**valid, 'renderers': ['ocv-remotion-v1']},
            {**valid, 'plan_versions': ['ocv-motion-v1']},
        ]
        for field, capability in (('renderers', required_renderer), ('plan_versions', required_plan)):
            for malformed in (None, [], capability, {capability: True}, [capability, None], [capability, '']):
                cases.append({**valid, field: malformed})
        for payload in cases:
            with self.subTest(payload=payload), \
                 patch.object(renderer, '_runtime_paths', return_value=(Path('node'), Path('browser'))), \
                 patch.object(renderer, '_run', return_value=json.dumps(payload)) as run:
                with self.assertRaisesRegex(RuntimeError, '未调用云端') as raised:
                    renderer.preflight_remotion(required_renderer=required_renderer,
                                                required_plan_version=required_plan)
                self.assertIn('请更新渲染组件', str(raised.exception))
                run.assert_called_once()
                self.assertIn('--check', run.call_args.args[0])

    def test_preflight_new_capabilities_pass_when_both_lists_advertise_them(self):
        payload = {'ready': True, 'renderer': 'ocv-remotion-v1',
                   'renderers': ['ocv-remotion-v1', 'ocv-remotion-shotcraft-v1'],
                   'plan_versions': ['ocv-motion-v1', 'ocv-shotcraft-v1']}
        with patch.object(renderer, '_runtime_paths', return_value=(Path('node'), Path('browser'))), \
             patch.object(renderer, '_run', return_value=json.dumps(payload)) as run:
            result = renderer.preflight_remotion(required_renderer='ocv-remotion-shotcraft-v1',
                                                 required_plan_version='ocv-shotcraft-v1')
        self.assertEqual(result['renderer'], 'ocv-remotion-shotcraft-v1')
        run.assert_called_once()
        self.assertIn('--check', run.call_args.args[0])

    def test_cancellation_before_spawn_does_not_start_process(self):
        with patch.object(renderer.subprocess, 'Popen') as popen:
            with self.assertRaises(InterruptedError):
                renderer._run(['node'], timeout=2, cancelled=lambda: True)
        popen.assert_not_called()

    def test_layout_failure_is_repairable_but_dependency_failure_is_not(self):
        for output, expected in [('{"kind":"validation","error":"text overflow"}', ValueError),
                                 ('Cannot find module remotion', RuntimeError)]:
            fake = MagicMock(); fake.__enter__.return_value = fake
            fake.communicate.return_value = (output, None); fake.returncode = 1; fake.poll.return_value = 1
            with patch.object(renderer.subprocess, 'Popen', return_value=fake):
                with self.subTest(output=output), self.assertRaises(expected):
                    renderer._run(['node'], timeout=2)

    def test_layout_error_ui_omits_react_stack_but_log_keeps_it(self):
        stack = 'Error: OCV_LAYOUT_OVERFLOW:stage2.title[600x250/600x140]\n    at ReactComponent (http://localhost/private.js:12:9)'
        output = stack+'\n'+json.dumps({'kind': 'validation', 'error': stack})
        fake = MagicMock(); fake.__enter__.return_value = fake
        fake.communicate.return_value = (output, None); fake.returncode = 1; fake.poll.return_value = 1
        log = self.root/'run.log'
        with patch.object(renderer.subprocess, 'Popen', return_value=fake):
            with self.assertRaises(ValueError) as raised: renderer._run(['node'], timeout=2, log=log)
        self.assertIn('stage2.title[600x250/600x140]', str(raised.exception))
        self.assertNotIn('ReactComponent', str(raised.exception))
        self.assertNotIn('http://', str(raised.exception))
        self.assertIn('ReactComponent', log.read_text(encoding='utf-8'))

    def test_cancellation_stops_only_the_owned_process_tree(self):
        fake = MagicMock(); fake.__enter__.return_value = fake
        fake.communicate.side_effect = subprocess.TimeoutExpired(['node'], .2)
        fake.poll.return_value = None
        flags = iter((False, False, True))
        with patch.object(renderer.subprocess, 'Popen', return_value=fake), \
             patch.object(renderer, '_terminate') as stop:
            with self.assertRaises(InterruptedError):
                renderer._run(['node'], timeout=2, cancelled=lambda: next(flags))
        stop.assert_called_once_with(fake)


if __name__ == '__main__': unittest.main()
