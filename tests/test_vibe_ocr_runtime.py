import hashlib
import json
import math
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from backend.app import vibe_ocr_runtime as ocr


class OcrRuntimeTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.image = self.folder/'source.png'
        Image.new('RGB', (200, 100), 'navy').save(self.image)
        self.cache = self.folder/'cache'
        self.runtime = dict(fingerprint='a'*64, spec={'timeout_seconds': 90}, models=self.folder/'models')
        self.resolve = self.enter_patch('resolve_runtime')
        self.resolve.return_value = self.runtime
        self.run = self.enter_patch('_run')
        self.run.side_effect = self.result

    def enter_patch(self, name):
        item = patch.object(ocr, name)
        self.addCleanup(item.stop)
        return item.start()

    def result(self, runtime, payload, cancelled):
        return dict(source_sha256=payload['source_sha256'], source_size=payload['source_size'],
            runtime_fingerprint=runtime['fingerprint'], inference_seconds=.01,
            texts=[dict(text='标题', box=[10, 20, 90, 50], confidence=.95)])

    def test_ready_result_and_cached_result_share_full_source_pixel_contract(self):
        result = ocr.analyze_image(self.image, self.cache)
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['texts'][0]['box'], [10., 20., 90., 50.])
        self.assertEqual(result['coordinate_space'], 'source_pixels')
        self.assertIn('not_coordinate_accuracy', result['confidence_kind'])
        self.assertFalse(result['cached'])
        again = ocr.analyze_image(self.image, self.cache)
        self.assertTrue(again['cached'])
        self.assertEqual(self.run.call_count, 1)

    def test_source_edit_invalidates_cache(self):
        first = ocr.analyze_image(self.image, self.cache)
        Image.new('RGB', (200, 100), 'red').save(self.image)
        second = ocr.analyze_image(self.image, self.cache)
        self.assertNotEqual(first['source_sha256'], second['source_sha256'])
        self.assertEqual(self.run.call_count, 2)

    def test_runtime_revision_invalidates_cache(self):
        ocr.analyze_image(self.image, self.cache)
        self.runtime['fingerprint'] = 'b'*64
        ocr.analyze_image(self.image, self.cache)
        self.assertEqual(self.run.call_count, 2)

    def test_roi_has_separate_cache_and_keeps_full_source_coordinates(self):
        ocr.analyze_image(self.image, self.cache)
        result = ocr.analyze_image(self.image, self.cache, region=[0, .1, .6, .7])
        self.assertEqual(result['source_size'], [200, 100])
        self.assertEqual(result['region'], [0., .1, .6, .7])
        self.assertEqual(result['texts'][0]['box'], [10., 20., 90., 50.])
        self.assertEqual(self.run.call_count, 2)

    def test_invalid_regions_rejected_without_process(self):
        for region in ([.5, 0, .2, 1], [0, 0, 2, 1], [0, 0, math.nan, 1], [False, 0, 1, 1], [], 'region'):
            with self.subTest(region=region), self.assertRaises(ValueError):
                ocr.analyze_image(self.image, self.cache, region=region)
        self.run.assert_not_called()

    def test_missing_runtime_is_explicit_unavailable_and_not_cached(self):
        self.resolve.side_effect = FileNotFoundError('not installed')
        result = ocr.analyze_image(self.image, self.cache)
        self.assertEqual(result['status'], 'unavailable')
        self.assertIn('--install', result['setup_hint'])
        self.assertEqual(result['texts'], [])
        self.assertFalse(self.cache.exists())
        self.run.assert_not_called()

    def test_cancelled_does_not_become_unavailable(self):
        with self.assertRaises(InterruptedError):
            ocr.analyze_image(self.image, self.cache, cancelled=lambda: True)
        self.run.assert_not_called()

    def test_empty_ready_result_not_a_claim_of_no_text(self):
        def empty(runtime, payload, cancelled):
            result = self.result(runtime, payload, cancelled)
            result['texts'] = []
            return result
        self.run.side_effect = empty
        result = ocr.analyze_image(self.image, self.cache)
        self.assertEqual(result['status'], 'ready')
        self.assertIn('空结果也不证明原图没有文字', result['note'])

    def test_invalid_worker_coordinates_or_scores_not_used(self):
        for bad in (dict(text='x', box=[20, 0, 10, 3], confidence=.9),
                    dict(text='x', box=[0, 0, 201, 3], confidence=.9),
                    dict(text='x', box=[0, 0, 2, 3], confidence=math.nan),
                    dict(text='x', box=[0, 0, 2, 3], confidence=2)):
            def invalid(runtime, payload, cancelled):
                result = self.result(runtime, payload, cancelled)
                result['texts'] = [bad]
                return result
            self.run.side_effect = invalid
            with self.subTest(bad=bad):
                result = ocr.analyze_image(self.image, self.cache)
                self.assertEqual(result['status'], 'unavailable')
                self.assertFalse(self.cache.exists())

    def test_worker_source_mismatch_is_not_cached(self):
        def changed(runtime, payload, cancelled):
            result = self.result(runtime, payload, cancelled)
            result['source_sha256'] = 'other'
            return result
        self.run.side_effect = changed
        result = ocr.analyze_image(self.image, self.cache)
        self.assertEqual(result['status'], 'unavailable')
        self.assertFalse(self.cache.exists())

    def test_damaged_cache_is_recomputed(self):
        ocr.analyze_image(self.image, self.cache)
        cache = next(self.cache.glob('*.json'))
        value = json.loads(cache.read_text(encoding='utf-8'))
        value['texts'][0]['box'] = [5, 0, 1, 10]
        cache.write_text(json.dumps(value), encoding='utf-8')
        result = ocr.analyze_image(self.image, self.cache)
        self.assertEqual(result['status'], 'ready')
        self.assertFalse(result['cached'])
        self.assertEqual(self.run.call_count, 2)

    def test_orientation_is_applied_before_source_dimensions(self):
        target = self.folder/'rotated.jpg'
        image = Image.new('RGB', (200, 100), 'navy')
        exif = image.getexif(); exif[274] = 6
        image.save(target, exif=exif)
        result = ocr.analyze_image(target, self.cache)
        self.assertEqual(result['source_size'], [100, 200])

    def test_bad_source_does_not_launch_worker(self):
        missing = self.folder/'missing.png'
        result = ocr.analyze_image(missing, self.cache)
        self.assertEqual(result['status'], 'unavailable')
        self.run.assert_not_called()

    def test_truncated_cache_json_is_recomputed(self):
        ocr.analyze_image(self.image, self.cache)
        next(self.cache.glob('*.json')).write_text('{', encoding='utf-8')
        self.assertEqual(ocr.analyze_image(self.image, self.cache)['status'], 'ready')
        self.assertEqual(self.run.call_count, 2)

    def test_wrong_cache_container_is_recomputed(self):
        ocr.analyze_image(self.image, self.cache)
        next(self.cache.glob('*.json')).write_text('[]', encoding='utf-8')
        self.assertEqual(ocr.analyze_image(self.image, self.cache)['status'], 'ready')
        self.assertEqual(self.run.call_count, 2)

    def test_wrong_cache_row_is_recomputed(self):
        ocr.analyze_image(self.image, self.cache)
        cache = next(self.cache.glob('*.json'))
        value = json.loads(cache.read_text(encoding='utf-8'))
        value['texts'] = ['not a detection']
        cache.write_text(json.dumps(value), encoding='utf-8')
        self.assertEqual(ocr.analyze_image(self.image, self.cache)['status'], 'ready')
        self.assertEqual(self.run.call_count, 2)

    def test_wrong_worker_container_is_unavailable(self):
        self.run.side_effect = None
        self.run.return_value = []
        self.assertEqual(ocr.analyze_image(self.image, self.cache)['status'], 'unavailable')

    def test_wrong_worker_row_is_unavailable(self):
        def invalid(runtime, payload, cancelled):
            result = self.result(runtime, payload, cancelled)
            result['texts'] = [None]
            return result
        self.run.side_effect = invalid
        self.assertEqual(ocr.analyze_image(self.image, self.cache)['status'], 'unavailable')


class OcrSpecTests(unittest.TestCase):
    def test_spec_is_pinned_and_models_have_hashes(self):
        spec = ocr.read_spec()
        self.assertEqual(spec['packages']['rapidocr'], '3.10.0')
        self.assertEqual(spec['packages']['onnxruntime'], '1.23.2')
        self.assertEqual(len(ocr.fingerprint(spec)), 64)
        self.assertEqual(set(spec['models']), {'det', 'rec', 'cls'})
        for model in spec['models'].values():
            self.assertEqual(len(model['sha256']), 64)

    def test_normalization_preserves_explicit_full_region(self):
        self.assertIsNone(ocr.normalized_region(None))
        self.assertEqual(ocr.normalized_region([0, 0, 1, 1]), [0., 0., 1., 1.])


class OcrProcessTests(unittest.TestCase):
    def runtime(self):
        return dict(python=Path('private/python.exe'), worker=Path('tools/vibe_ocr/worker.py'),
                    spec={'timeout_seconds': 90})

    def test_cancel_kills_only_private_worker(self):
        process = Mock()
        process.poll.return_value = None
        process.communicate.return_value = (b'', b'')
        with patch.object(ocr.subprocess, 'Popen', return_value=process), self.assertRaises(InterruptedError):
            ocr._run(self.runtime(), {}, lambda: True)
        process.kill.assert_called_once()

    def test_timeout_kills_only_private_worker(self):
        process = Mock()
        process.poll.return_value = None
        process.communicate.return_value = (b'', b'')
        with patch.object(ocr.subprocess, 'Popen', return_value=process), \
                patch.object(ocr.time, 'monotonic', side_effect=[0, 91]), self.assertRaises(TimeoutError):
            ocr._run(self.runtime(), {}, None)
        process.kill.assert_called_once()

    def test_failed_worker_does_not_become_empty_ocr(self):
        process = Mock(returncode=1)
        process.poll.return_value = 1
        process.communicate.return_value = (b'', b'dependency not installed')
        with patch.object(ocr.subprocess, 'Popen', return_value=process), self.assertRaises(RuntimeError):
            ocr._run(self.runtime(), {}, None)
        process.kill.assert_not_called()

    def test_worker_read_waits_without_resending_request(self):
        process = Mock(returncode=0)
        process.poll.return_value = 0
        process.communicate.side_effect = [subprocess.TimeoutExpired('worker', .2), (b'{"texts":[]}', b'')]
        with patch.object(ocr.subprocess, 'Popen', return_value=process):
            self.assertEqual(ocr._run(self.runtime(), {'x': 1}, None), {'texts': []})
        self.assertIsNotNone(process.communicate.call_args_list[0].kwargs['input'])
        self.assertIsNone(process.communicate.call_args_list[1].kwargs['input'])


if __name__ == '__main__':
    unittest.main()
