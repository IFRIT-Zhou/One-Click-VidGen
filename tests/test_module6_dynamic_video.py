import json
import struct
import tempfile
import unittest
from pathlib import Path

import requests

from module6_dynamic_video import (DynamicVideoStopped, DynamicVideoTaskFailed,
                                   DynamicVideoTaskUnknown, RunningHubVideoProvider,
                                   VideoGenerationRequest, request_fingerprint,
                                   validate_request, validate_video_file)


def box(kind, body):
    return struct.pack('>I4s', len(body) + 8, kind) + body


VIDEO_BYTES = (box(b'ftyp', b'isom\x00\x00\x00\x00isom') +
               box(b'moov', box(b'trak', box(b'mdia', box(b'hdlr', b'\x00' * 8 + b'vide')))) +
               box(b'mdat', b'encoded-video-sample'))


class Response:
    def __init__(self, body=None, *, content=b'', ok=True, code=200):
        self.body, self.content, self.ok, self.status_code = body, content, ok, code
    def json(self): return self.body
    def close(self): pass
    def raise_for_status(self):
        if not self.ok: raise requests.HTTPError(str(self.status_code))
    def iter_content(self, _size): return [self.content]
    def __enter__(self): return self
    def __exit__(self, *_args): self.close()


class Session:
    def __init__(self): self.calls = []
    def post(self, url, **kwargs):
        self.calls.append(('post', url, kwargs))
        if url.endswith('/media/upload/binary'):
            return Response({'fileUrl': 'https://assets.test/reference.png'})
        if url.endswith('/multimodal-video'):
            return Response({'taskId': 'paid-task-1', 'status': 'RUNNING'})
        if url.endswith('/query'):
            return Response({'status': 'SUCCESS', 'results': [{'videoUrl': 'https://assets.test/video.mp4'}]})
        raise AssertionError(url)
    def get(self, url, **kwargs):
        self.calls.append(('get', url, kwargs))
        return Response(content=VIDEO_BYTES)


class DynamicVideoModuleTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.image = root / 'image.png'; self.image.write_bytes(b'image')
        self.output = root / 'result.mp4'
        self.state = root / 'task.json'
        self.request = VideoGenerationRequest('参考图1，人物举手，静音。', (self.image,), self.output, 5, '16:9')
    def tearDown(self): self.temp.cleanup()

    def test_success_is_persisted_and_never_resubmitted(self):
        session = Session()
        provider = RunningHubVideoProvider('secret', base_url='https://provider.test', session=session)
        self.assertEqual(provider.run(self.request, self.state, poll_seconds=0), self.output)
        self.assertEqual(self.output.read_bytes(), VIDEO_BYTES)
        state = json.loads(self.state.read_text(encoding='utf-8'))
        self.assertEqual(state['task_id'], 'paid-task-1')
        self.assertEqual(state['status'], 'DOWNLOADED')
        self.assertTrue(state['sha256'])
        self.assertTrue(state['provider']['account_fingerprint'])
        self.assertNotIn('secret', self.state.read_text(encoding='utf-8'))
        submit_count = sum(url.endswith('/multimodal-video') for method, url, _ in session.calls if method == 'post')
        self.assertEqual(submit_count, 1)
        provider.run(self.request, self.state, poll_seconds=0)
        submit_count = sum(url.endswith('/multimodal-video') for method, url, _ in session.calls if method == 'post')
        self.assertEqual(submit_count, 1)
        submit = next(kwargs['json'] for method, url, kwargs in session.calls if url.endswith('/multimodal-video'))
        self.assertFalse(submit['generateAudio'])
        self.assertEqual(submit['duration'], '5')
        self.assertEqual(len(submit['imageUrls']), 1)

    def test_existing_state_with_other_request_is_rejected(self):
        provider = RunningHubVideoProvider('secret', session=Session())
        self.state.write_text(json.dumps({'task_id': 'old', 'fingerprint': 'other'}), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '避免重复扣费'):
            provider.run(self.request, self.state, poll_seconds=0)

    def test_unknown_query_preserves_identity(self):
        class Broken(Session):
            def post(self, url, **kwargs):
                if url.endswith('/query'): raise requests.ConnectionError('offline')
                return super().post(url, **kwargs)
        provider = RunningHubVideoProvider('secret', session=Broken())
        with self.assertRaises(DynamicVideoTaskUnknown):
            provider.run(self.request, self.state, poll_seconds=0)
        state = json.loads(self.state.read_text(encoding='utf-8'))
        self.assertEqual(state['task_id'], 'paid-task-1')
        self.assertEqual(state['status'], 'UNKNOWN')

    def test_lost_submit_response_is_frozen_before_retry(self):
        class Lost(Session):
            def post(self, url, **kwargs):
                if url.endswith('/multimodal-video'): raise requests.ConnectionError('lost response')
                return super().post(url, **kwargs)
        provider = RunningHubVideoProvider('secret', session=Lost())
        with self.assertRaisesRegex(DynamicVideoTaskUnknown, '禁止自动再次提交'):
            provider.run(self.request, self.state, poll_seconds=0)
        state = json.loads(self.state.read_text(encoding='utf-8'))
        self.assertEqual(state['status'], 'UNKNOWN')
        self.assertTrue(state['submit_response_lost'])
        with self.assertRaisesRegex(DynamicVideoTaskUnknown, '缺少 task_id'):
            provider.run(self.request, self.state, poll_seconds=0)

    def test_contract_validation(self):
        with self.assertRaises(ValueError):
            validate_request(VideoGenerationRequest('x', (self.image,), self.output, 3, '16:9'))
        with self.assertRaises(ValueError):
            validate_request(VideoGenerationRequest('x', (), self.output, 5, '16:9'))
        with self.assertRaises(ValueError):
            validate_request(VideoGenerationRequest('x', (self.image,), self.output, 5, '2:1'))

    def test_stop_before_start_records_safe_unpaid_identity_and_can_resume(self):
        session = Session()
        provider = RunningHubVideoProvider('secret', session=session)
        with self.assertRaises(DynamicVideoStopped):
            provider.run(self.request, self.state, should_stop=lambda: True)
        self.assertEqual(session.calls, [])
        state = json.loads(self.state.read_text(encoding='utf-8'))
        self.assertEqual(state['status'], 'PAUSED_BEFORE_SUBMIT')
        self.assertIs(state['paid_submission_started'], False)
        self.assertEqual(state['fingerprint'], request_fingerprint(self.request))
        self.assertEqual(state['provider'], provider.identity)
        self.assertNotIn('task_id', state)
        provider.run(self.request, self.state, poll_seconds=0)
        self.assertEqual(sum(url.endswith('/multimodal-video') for _, url, _ in session.calls), 1)

    def test_initial_stop_never_overwrites_existing_paid_or_corrupt_state(self):
        session = Session()
        provider = RunningHubVideoProvider('secret', session=session)
        originals = [json.dumps({'task_id': 'paid-original', 'status': 'RUNNING',
                                 'fingerprint': request_fingerprint(self.request),
                                 'provider': provider.identity, 'paid_submission_started': True}),
                     '{broken state preserved',
                     json.dumps({'status': 'UNKNOWN', 'submit_response_lost': True})]
        for original in originals:
            with self.subTest(original=original):
                self.state.write_text(original, encoding='utf-8')
                with self.assertRaises(DynamicVideoStopped):
                    provider.run(self.request, self.state, should_stop=lambda: True)
                self.assertEqual(self.state.read_text(encoding='utf-8'), original)
                self.assertEqual(session.calls, [])

    def test_stop_after_paid_post_preserves_identity_and_resume_queries_only(self):
        session = Session()
        provider = RunningHubVideoProvider('secret', session=session)
        stop = lambda: any(url.endswith('/multimodal-video') for _, url, _ in session.calls)
        with self.assertRaises(DynamicVideoStopped):
            provider.run(self.request, self.state, should_stop=stop)
        self.assertEqual(json.loads(self.state.read_text())['task_id'], 'paid-task-1')
        self.assertFalse(any(url.endswith('/query') for _, url, _ in session.calls))
        provider.run(self.request, self.state, poll_seconds=0)
        self.assertEqual(sum(url.endswith('/multimodal-video') for _, url, _ in session.calls), 1)

    def test_stop_during_upload_is_safe_to_resume_without_freezing(self):
        session = Session()
        provider = RunningHubVideoProvider('secret', session=session)
        with self.assertRaises(DynamicVideoStopped):
            provider.run(self.request, self.state, should_stop=lambda: bool(session.calls))
        state = json.loads(self.state.read_text())
        self.assertEqual(state['status'], 'PAUSED_BEFORE_SUBMIT')
        self.assertFalse(state['paid_submission_started'])
        provider.run(self.request, self.state, poll_seconds=0)
        self.assertEqual(sum(url.endswith('/multimodal-video') for _, url, _ in session.calls), 1)

    def test_confirmed_failure_with_result_url_is_not_downloaded_or_queried_again(self):
        class Failed(Session):
            def post(self, url, **kwargs):
                if url.endswith('/query'):
                    self.calls.append(('post', url, kwargs))
                    return Response({'status': 'FAILED', 'errorMessage': 'moderated',
                                     'results': [{'url': 'https://example.test/wrong.mp4'}]})
                return super().post(url, **kwargs)
        session = Failed()
        provider = RunningHubVideoProvider('secret', session=session)
        with self.assertRaises(DynamicVideoTaskFailed):
            provider.run(self.request, self.state, poll_seconds=0)
        calls = len(session.calls)
        with self.assertRaises(DynamicVideoTaskFailed):
            provider.run(self.request, self.state, poll_seconds=0)
        self.assertEqual(len(session.calls), calls)
        self.assertFalse(self.output.exists())
        self.assertTrue(json.loads(self.state.read_text())['terminal'])

    def test_explicit_rejection_without_task_id_is_terminal(self):
        class Rejected(Session):
            def post(self, url, **kwargs):
                if url.endswith('/multimodal-video'):
                    self.calls.append(('post', url, kwargs))
                    return Response({'errorCode': 1007, 'errorMessage': 'bad duration'})
                return super().post(url, **kwargs)
        session = Rejected()
        provider = RunningHubVideoProvider('secret', session=session)
        with self.assertRaises(DynamicVideoTaskFailed):
            provider.run(self.request, self.state, poll_seconds=0)
        self.assertTrue(json.loads(self.state.read_text())['terminal'])
        calls = len(session.calls)
        with self.assertRaises(DynamicVideoTaskFailed):
            provider.run(self.request, self.state, poll_seconds=0)
        self.assertEqual(len(session.calls), calls)

    def test_http_500_failure_body_is_still_unknown_not_safe_to_resubmit(self):
        class Ambiguous(Session):
            def post(self, url, **kwargs):
                if url.endswith('/multimodal-video'):
                    return Response({'status': 'FAILED', 'errorCode': 500, 'errorMessage': 'upstream'}, ok=False, code=500)
                return super().post(url, **kwargs)
        provider = RunningHubVideoProvider('secret', session=Ambiguous())
        with self.assertRaises(DynamicVideoTaskUnknown):
            provider.run(self.request, self.state, poll_seconds=0)
        state = json.loads(self.state.read_text())
        self.assertEqual(state['status'], 'UNKNOWN')
        self.assertFalse(state.get('terminal', False))

    def test_task_id_from_unusual_http_response_is_saved_and_queried(self):
        class Identity(Session):
            def post(self, url, **kwargs):
                if url.endswith('/multimodal-video'):
                    self.calls.append(('post', url, kwargs))
                    return Response({'taskId': 'accepted-identity', 'status': 'RUNNING'}, ok=False, code=502)
                return super().post(url, **kwargs)
        session = Identity()
        provider = RunningHubVideoProvider('secret', session=session)
        provider.run(self.request, self.state, poll_seconds=0)
        state = json.loads(self.state.read_text())
        self.assertEqual(state['task_id'], 'accepted-identity')
        self.assertEqual(sum(url.endswith('/multimodal-video') for _, url, _ in session.calls), 1)

    def test_nonterminal_result_url_is_not_treated_as_finished_video(self):
        class Running(Session):
            def post(self, url, **kwargs):
                if url.endswith('/query'):
                    self.calls.append(('post', url, kwargs))
                    return Response({'status': 'RUNNING', 'results': [{'url': 'https://example.test/preview.mp4'}]})
                return super().post(url, **kwargs)
        session = Running()
        provider = RunningHubVideoProvider('secret', session=session)
        stop = lambda: any(url.endswith('/query') for _, url, _ in session.calls)
        with self.assertRaises(DynamicVideoStopped):
            provider.run(self.request, self.state, poll_seconds=0, should_stop=stop)
        self.assertFalse(any(method == 'get' for method, _, _ in session.calls))

    def test_corrupt_state_never_starts_a_new_submission(self):
        self.state.write_text('{broken', encoding='utf-8')
        session = Session()
        provider = RunningHubVideoProvider('secret', session=session)
        with self.assertRaisesRegex(DynamicVideoTaskUnknown, '状态损坏'):
            provider.run(self.request, self.state)
        self.assertEqual(session.calls, [])

    def test_provider_or_account_change_blocks_resume(self):
        session = Session()
        provider = RunningHubVideoProvider('secret', base_url='https://a.test', session=session)
        provider.run(self.request, self.state, poll_seconds=0)
        for key, base, path in [('other', 'https://a.test', None),
                                ('secret', 'https://b.test', None),
                                ('secret', 'https://a.test', '/different')]:
            with self.subTest(key=key, base=base, path=path):
                other = RunningHubVideoProvider(key, base_url=base, submit_path=path, session=Session())
                with self.assertRaisesRegex(DynamicVideoTaskUnknown, '账号已变化'):
                    other.run(self.request, self.state, poll_seconds=0)

    def test_bad_download_does_not_replace_valid_output(self):
        class Html(Session):
            def get(self, url, **kwargs):
                return Response(content=b'<html>' + b'access denied' * 30)
        self.output.write_bytes(VIDEO_BYTES)
        provider = RunningHubVideoProvider('secret', session=Html())
        with self.assertRaises(DynamicVideoTaskUnknown):
            provider.run(self.request, self.state, poll_seconds=0)
        self.assertEqual(self.output.read_bytes(), VIDEO_BYTES)
        self.assertEqual(json.loads(self.state.read_text())['task_id'], 'paid-task-1')

    def test_corrupt_cache_redownloads_without_new_paid_submission(self):
        session = Session()
        provider = RunningHubVideoProvider('secret', session=session)
        provider.run(self.request, self.state, poll_seconds=0)
        self.output.write_bytes(VIDEO_BYTES[:-1] + b'X')  # same length, valid container, wrong hash
        provider.run(self.request, self.state, poll_seconds=0)
        self.assertEqual(self.output.read_bytes(), VIDEO_BYTES)
        self.assertEqual(sum(url.endswith('/multimodal-video') for _, url, _ in session.calls), 1)
        self.assertEqual(sum(method == 'get' for method, _, _ in session.calls), 2)

    def test_truncated_and_audio_only_container_fail_validation(self):
        for data in (VIDEO_BYTES[:-1], VIDEO_BYTES.replace(b'vide', b'soun'), b'not a video' * 30):
            self.output.write_bytes(data)
            with self.assertRaises(DynamicVideoTaskUnknown):
                validate_video_file(self.output)

    def test_legacy_state_allows_valid_local_cache_but_not_remote_guessing(self):
        state = {'fingerprint': request_fingerprint(self.request), 'task_id': 'legacy', 'status': 'DOWNLOADED'}
        self.state.write_text(json.dumps(state), encoding='utf-8')
        self.output.write_bytes(VIDEO_BYTES)
        session = Session()
        provider = RunningHubVideoProvider('secret', session=session)
        self.assertEqual(provider.run(self.request, self.state), self.output)
        self.assertEqual(session.calls, [])
        self.output.unlink()
        with self.assertRaisesRegex(DynamicVideoTaskUnknown, '旧视频任务'):
            provider.run(self.request, self.state)
        self.assertEqual(session.calls, [])

    def test_custom_same_site_submit_path(self):
        class Custom(Session):
            def post(self, url, **kwargs):
                if url.endswith('/custom-video'):
                    self.calls.append(('post', url, kwargs))
                    return Response({'taskId': 'custom', 'status': 'RUNNING'})
                return super().post(url, **kwargs)
        session = Custom()
        provider = RunningHubVideoProvider('secret', base_url='https://provider.test',
                                           submit_path='/custom-video', session=session)
        provider.run(self.request, self.state, poll_seconds=0)
        self.assertTrue(any(url == 'https://provider.test/custom-video' for _, url, _ in session.calls))
        with self.assertRaises(ValueError):
            RunningHubVideoProvider('secret', submit_path='https://other.test/submit')


if __name__ == '__main__': unittest.main()
