import ast
import hashlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image, ImageChops
import requests

from backend.app import vibe_comfy_materials as adapter


def response(payload=None, data=None, status=200):
    result = Mock()
    result.status_code = status
    result.json.return_value = payload
    result.iter_content.return_value = [data] if data is not None else []
    return result


class ComfyMaterialTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'source.png'
        source = Image.new('RGBA', (8, 6))
        source.putdata([(x * 20, y * 30, 79, 255 if x else 64)
                        for y in range(6) for x in range(8)])
        source.save(self.source)
        self.output = self.root / 'out'
        self.base = 'http://127.0.0.1:8123'
        self.session = Mock()
        self.queue = {'queue_running': [], 'queue_pending': []}
        self.mask = Image.new('L', (4, 4), 0)
        self.mask.paste(255, (1, 1, 3, 3))
        self.history = {'p1': {'status': {'status_str': 'success'}, 'outputs': {
            '3': {'images': [{'filename': 'mask.png', 'subfolder': 'vibe_rmbg', 'type': 'output'}]}}}}
        self.session.get.side_effect = self.get
        self.session.post.side_effect = self.post

    def get(self, url, **kwargs):
        if url.endswith('/object_info'):
            return response({'LoadImage': {}, 'SaveImage': {}, adapter.NODE_CLASS: {}})
        if url.endswith('/queue'):
            return response(self.queue)
        if '/history/' in url:
            return response(self.history)
        if url.endswith('/view'):
            buffer = io.BytesIO()
            self.mask.convert('RGB').save(buffer, 'PNG')
            return response(data=buffer.getvalue())
        raise AssertionError('unexpected GET ' + url)

    def post(self, url, **kwargs):
        if url.endswith('/upload/image'):
            return response({'name': kwargs['files']['image'][0], 'subfolder': ''})
        if url.endswith('/prompt'):
            return response({'prompt_id': 'p1'})
        if url.endswith('/api/jobs/p1/cancel'):
            return response({'cancelled': True})
        if url.endswith('/queue'):
            return response({})
        raise AssertionError('unexpected POST ' + url)

    def run_cutout(self, **kwargs):
        return adapter._run_connected(self.source, self.output, base_url=self.base,
            bbox=(2, 1, 6, 5), session=self.session, poll_interval=0, **kwargs)

    def test_mask_only_workflow_unloads_its_model(self):
        graph = adapter.build_workflow('unique.png', 'abc')
        self.assertEqual(graph['3']['inputs']['images'], ['2', 2])
        self.assertTrue(graph['2']['inputs']['unload_model'])
        self.assertFalse(graph['2']['inputs']['refine_foreground'])
        self.assertEqual(graph['2']['inputs']['mask_offset'], 0)

    def test_original_rgb_and_bbox_mapping_preserved(self):
        before = self.source.read_bytes()
        result = self.run_cutout()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['crop_box_px'], [2, 1, 6, 5])
        self.assertEqual(result['alpha_bbox_px'], [1, 1, 3, 3])
        self.assertEqual(result['source_alpha_bbox_px'], [3, 2, 5, 4])
        with Image.open(result['path']) as output, Image.open(self.source) as source:
            expected = source.crop((2, 1, 6, 5)).convert('RGB')
            self.assertIsNone(ImageChops.difference(output.convert('RGB'), expected).getbbox())
            self.assertIsNone(ImageChops.difference(output.getchannel('A'), self.mask).getbbox())
        self.assertEqual(self.source.read_bytes(), before)
        self.assertTrue(result['requires_review'])
        self.assertFalse(result['may_charge'])

    def test_uploads_are_unique_and_never_overwrite(self):
        first = self.run_cutout()
        second = self.run_cutout()
        uploads = [c for c in self.session.post.call_args_list if c.args[0].endswith('/upload/image')]
        self.assertEqual(len(uploads), 2)
        self.assertNotEqual(uploads[0].kwargs['files']['image'][0], uploads[1].kwargs['files']['image'][0])
        self.assertEqual(uploads[0].kwargs['data']['overwrite'], 'false')
        self.assertNotEqual(first['path'], second['path'])

    def test_alpha_trim_retains_rgb_and_source_coordinates(self):
        result = self.run_cutout(trim_padding=0)
        self.assertEqual(result['crop_box_px'], [2, 1, 6, 5])
        self.assertEqual(result['trim_box_in_crop_px'], [1, 1, 3, 3])
        self.assertEqual(result['output_box_in_source_px'], [3, 2, 5, 4])
        self.assertEqual(result['source_alpha_bbox_px'], [3, 2, 5, 4])
        self.assertEqual(result['alpha_bbox_px'], [0, 0, 2, 2])
        self.assertEqual(result['output_size_px'], [2, 2])
        with Image.open(result['path']) as output, Image.open(self.source) as source:
            self.assertIsNone(ImageChops.difference(output.convert('RGB'),
                source.crop((3, 2, 5, 4)).convert('RGB')).getbbox())

    def test_trim_padding_is_clamped_to_original_crop(self):
        result = self.run_cutout(trim_padding=10)
        self.assertEqual(result['output_box_in_source_px'], [2, 1, 6, 5])
        self.assertEqual(result['output_size_px'], [4, 4])

    def test_invalid_trim_padding_is_rejected_before_submission(self):
        for padding in (-1, 257, True, 1.5):
            with self.assertRaises(adapter.ComfyMaterialError):
                self.run_cutout(trim_padding=padding)
        self.session.post.assert_not_called()

    def test_stop_only_cancels_own_prompt(self):
        state = {'stop': False}
        with self.assertRaises(adapter.ComfyMaterialStopped):
            self.run_cutout(should_stop=lambda: state['stop'],
                on_submitted=lambda prompt_id: state.update(stop=True))
        cancels = [c for c in self.session.post.call_args_list
                   if c.args[0].endswith(('/queue', '/interrupt', '/cancel'))]
        self.assertEqual([c.kwargs['json'] for c in cancels],
                         [{}])
        self.assertEqual(cancels[0].args[0], self.base + '/api/jobs/p1/cancel')
        self.assertFalse(cancels[0].kwargs['allow_redirects'])
        saved = json.loads(next(self.output.glob('*.json')).read_text(encoding='utf-8'))
        self.assertTrue(saved['targeted_cancel_confirmed'])
        self.assertEqual(saved['status'], 'stopped')

    def test_unsupported_atomic_cancel_only_deletes_own_pending_item(self):
        for status in (404, 405, 501):
            with self.subTest(status=status):
                self.session.post.reset_mock()
                self.session.post.side_effect = [response(status=status), response({})]
                result = adapter._cancel_prompt(self.session, self.base, 'p1')
                self.assertFalse(result['cancelled'])
                self.assertTrue(result['pending_delete_requested'])
                self.assertTrue(result['running_must_finish_naturally'])
                self.assertEqual(result['reason'], 'unsupported_endpoint')
                calls = self.session.post.call_args_list
                self.assertEqual([c.args[0] for c in calls],
                    [self.base + '/api/jobs/p1/cancel', self.base + '/queue'])
                self.assertEqual(calls[1].kwargs['json'], {'delete': ['p1']})
                self.assertTrue(all(c.kwargs['allow_redirects'] is False for c in calls))

    def test_failed_or_invalid_atomic_cancel_never_interrupts(self):
        invalid = response({'cancelled': 'true'})
        bad_json = response()
        bad_json.json.side_effect = ValueError('secret-not-to-be-returned')
        server_error = response(status=500)
        server_error.raise_for_status.side_effect = requests.HTTPError('secret-not-to-be-returned')
        redirect = response({'cancelled': True}, status=307)
        for first in (requests.Timeout('secret-not-to-be-returned'), invalid, bad_json, server_error, redirect):
            with self.subTest(first=type(first).__name__):
                self.session.post.reset_mock()
                self.session.post.side_effect = [first, response({})]
                result = adapter._cancel_prompt(self.session, self.base, 'p1')
                self.assertTrue(result['running_must_finish_naturally'])
                self.assertNotIn('secret', str(result))
                self.assertFalse(any(c.args[0].endswith('/interrupt')
                                     for c in self.session.post.call_args_list))

    def test_atomic_cancel_noop_is_not_reported_as_cancelled_or_retried(self):
        self.session.post.side_effect = [response({'cancelled': False})]
        result = adapter._cancel_prompt(self.session, self.base, 'p1')
        self.assertFalse(result['cancelled'])
        self.assertEqual(result['method'], 'atomic_job_cancel')
        self.assertEqual(self.session.post.call_count, 1)

    def test_failed_pending_delete_is_not_reported_as_confirmed(self):
        self.session.post.side_effect = [response(status=404), requests.Timeout('secret')]
        result = adapter._cancel_prompt(self.session, self.base, 'p1')
        self.assertFalse(result['cancelled'])
        self.assertFalse(result['pending_delete_requested'])
        self.assertTrue(result['running_must_finish_naturally'])

    def test_unsupported_cancel_reports_natural_completion_to_caller_and_receipt(self):
        state = {'stop': False}
        def legacy_post(url, **kwargs):
            if url.endswith('/cancel'):
                return response(status=404)
            return self.post(url, **kwargs)
        self.session.post.side_effect = legacy_post
        with self.assertRaisesRegex(adapter.ComfyMaterialStopped, '等待自然结束'):
            self.run_cutout(should_stop=lambda: state['stop'],
                on_submitted=lambda prompt_id: state.update(stop=True))
        saved = json.loads(next(self.output.glob('*.json')).read_text(encoding='utf-8'))
        self.assertFalse(saved['targeted_cancel_confirmed'])
        self.assertTrue(saved['cancellation']['running_must_finish_naturally'])
        self.assertIn('等待自然结束', saved['message'])
        self.assertFalse(any(c.args[0].endswith('/interrupt')
                             for c in self.session.post.call_args_list))

    def test_timeout_uses_safe_fallback_and_reports_running_task_may_continue(self):
        def legacy_post(url, **kwargs):
            if url.endswith('/cancel'):
                return response(status=405)
            return self.post(url, **kwargs)
        self.session.post.side_effect = legacy_post
        with patch.object(adapter.time, 'monotonic', side_effect=[0, 0, 2]):
            with self.assertRaisesRegex(TimeoutError, '等待自然结束'):
                self.run_cutout(timeout=1)
        saved = json.loads(next(self.output.glob('*.json')).read_text(encoding='utf-8'))
        self.assertEqual(saved['status'], 'failed')
        self.assertFalse(saved['targeted_cancel_confirmed'])
        self.assertTrue(saved['cancellation']['running_must_finish_naturally'])
        self.assertFalse(any(c.args[0].endswith('/interrupt')
                             for c in self.session.post.call_args_list))

    def test_uncertain_submission_never_retries_or_global_interrupts(self):
        def fail(url, **kwargs):
            if url.endswith('/prompt'):
                raise requests.Timeout('credential-in-error-must-not-leak')
            return self.post(url, **kwargs)
        self.session.post.side_effect = fail
        with self.assertRaises(adapter.ComfyMaterialError) as caught:
            self.run_cutout()
        self.assertNotIn('credential', str(caught.exception))
        calls = self.session.post.call_args_list
        self.assertEqual(sum(c.args[0].endswith('/prompt') for c in calls), 1)
        self.assertFalse(any(c.args[0].endswith('/interrupt') for c in calls))

    def test_busy_h3_engine_prevents_any_upload_or_submission(self):
        self.queue['queue_running'] = [[0, 'h3-other-prompt']]
        with self.assertRaisesRegex(adapter.ComfyMaterialError, 'H3'):
            self.run_cutout()
        self.session.post.assert_not_called()

    def test_unknown_queue_state_is_not_treated_as_idle(self):
        self.queue = {}
        with self.assertRaisesRegex(adapter.ComfyMaterialError, '无法确认'):
            self.run_cutout()
        self.session.post.assert_not_called()

    def test_mismatched_mask_is_rejected_without_resize(self):
        self.mask = Image.new('L', (10, 10), 128)
        with self.assertRaisesRegex(adapter.ComfyMaterialError, '尺寸不一致'):
            self.run_cutout()
        self.assertFalse(list(self.output.glob('*.png')))

    def test_crop_upload_does_not_expose_hidden_rgb(self):
        source = Image.new('RGBA', (2, 2), (255, 0, 0, 0))
        source.save(self.source)
        _, _, _, _, data = adapter._prepare_source(self.source, None)
        with Image.open(io.BytesIO(data)) as uploaded:
            self.assertEqual(uploaded.getpixel((0, 0)), (48, 48, 48))

    def test_original_alpha_is_intersected_not_replaced(self):
        source = Image.new('RGBA', (8, 6), (130, 50, 20, 128))
        source.save(self.source)
        result = self.run_cutout()
        with Image.open(result['path']) as output:
            self.assertEqual(output.getchannel('A').getextrema(), (0, 128))

    def test_remote_engine_is_rejected(self):
        for value in ('https://example.com', 'http://localhost:8188',
                      'http://user:pass@127.0.0.1:8188', 'http://127.0.0.1:8188/path'):
            with self.assertRaises(adapter.ComfyMaterialError):
                adapter._local_url(value)

    def test_independent_artifact_hash_check(self):
        file = self.root / 'model.safetensors'
        file.write_bytes(b'fake-test-model')
        expected = hashlib.sha256(file.read_bytes()).hexdigest()
        with patch.object(adapter, 'MODEL_FILES', {file.name: (15, expected)}):
            adapter.verify_models(self.root)
            file.write_bytes(b'wrong-test-data')
            with self.assertRaises(adapter.ComfyMaterialError):
                adapter.verify_models(self.root)

    def test_backend_entry_does_not_start_a_stopped_engine(self):
        from backend.app import managed_comfyui as engine
        with patch.object(adapter, 'verify_models'), patch.object(engine, 'status', return_value={'state': 'stopped'}), \
                patch.object(engine, 'ensure_ready') as start:
            with self.assertRaises(adapter.ComfyMaterialError):
                adapter.run_cutout(self.source, self.output)
        start.assert_not_called()


@unittest.skipUnless((Path(__file__).resolve().parents[1] / 'tools/vibe_rmbg_node/__init__.py').is_file(),
                     'Legacy private ComfyUI trial wrapper is not distributed; standalone RMBG has separate tests')
class RMBGPrecisionIsolationTests(unittest.TestCase):
    """Exercise the actual wrapper class with fake torch; never import GPU code."""

    def wrapper(self, process, original='highest'):
        root = Path(__file__).resolve().parents[1]
        path = root / 'tools/vibe_rmbg_node/__init__.py'
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        definition = next(node for node in tree.body
                          if isinstance(node, ast.ClassDef) and node.name == 'OCVBiRefNetRMBG')
        state = {'precision': original}
        fake_torch = SimpleNamespace(
            get_float32_matmul_precision=lambda: state['precision'],
            set_float32_matmul_precision=lambda value: state.update(precision=value))
        class FakeOfficialNode:
            def process_image(self, image, model, **params):
                fake_torch.set_float32_matmul_precision('high')
                return process(image, model, params)
        namespace = {'official': SimpleNamespace(BiRefNetRMBG=FakeOfficialNode, torch=fake_torch),
                     'LocalOnlyModel': lambda: None}
        exec(compile(ast.Module(body=[definition], type_ignores=[]), str(path), 'exec'), namespace)
        return namespace['OCVBiRefNetRMBG'](), state

    def test_success_restores_prior_precision_and_forwards_arguments(self):
        call = Mock(return_value=('rgba', 'mask', 'mask_image'))
        for previous in ('highest', 'high', 'medium'):
            with self.subTest(previous=previous):
                node, state = self.wrapper(call, previous)
                self.assertEqual(node.process_image('image', 'model', unload_model=True),
                                 ('rgba', 'mask', 'mask_image'))
                self.assertEqual(state['precision'], previous)
        call.assert_called_with('image', 'model', {'unload_model': True})

    def test_model_error_and_cancellation_restore_precision(self):
        for error in (RuntimeError('model failure'), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                node, state = self.wrapper(Mock(side_effect=error))
                with self.assertRaises(type(error)):
                    node.process_image('image', 'model')
                self.assertEqual(state['precision'], 'highest')

    def test_installed_trial_wrapper_matches_source_when_present(self):
        root = Path(__file__).resolve().parents[1]
        installed = root / 'comfyui/custom_nodes/OCV_RMBG_Trial/__init__.py'
        if not installed.is_file():
            self.skipTest('Trial node not installed in this checkout')
        self.assertEqual(installed.read_bytes(), (root / 'tools/vibe_rmbg_node/__init__.py').read_bytes())


if __name__ == '__main__':
    unittest.main()
