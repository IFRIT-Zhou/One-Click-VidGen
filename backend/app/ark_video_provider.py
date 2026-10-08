"""Ark video protocol using the existing durable paid-task lifecycle."""
import base64
import mimetypes
import time
import requests
from urllib.parse import quote

from module6_dynamic_video import (RunningHubVideoProvider, validate_request,
    _stop_if_requested, DynamicVideoTaskFailed, DynamicVideoTaskUnknown)


class ArkVideoProvider(RunningHubVideoProvider):
    def __init__(self, *args, model='doubao-seedance-2-0-260128', **kwargs):
        super().__init__(*args, **kwargs)
        self.model = model

    @property
    def identity(self):
        return {**super().identity, 'protocol': 'ark', 'model': self.model}

    def submit(self, request, *, should_stop=None, before_submit=None):
        validate_request(request)
        content = [{'type': 'text', 'text': request.prompt.strip()}]
        for path in request.image_paths:
            _stop_if_requested(should_stop)
            mime = mimetypes.guess_type(path.name)[0] or 'image/png'
            url = 'data:' + mime + ';base64,' + base64.b64encode(path.read_bytes()).decode('ascii')
            content.append({'type': 'image_url', 'image_url': {'url': url}, 'role': 'reference_image'})
        payload = dict(model=self.model, content=content, duration=request.duration,
                       ratio=request.ratio, resolution=request.resolution,
                       generate_audio=request.generate_audio, watermark=False, seed=request.seed)
        _stop_if_requested(should_stop)
        if before_submit:
            before_submit()
        response = self.session.post(self.url(self.submit_path), headers=self.headers,
                                     json=payload, timeout=120)
        try:
            body = response.json()
            task_id = body.get('id') if isinstance(body, dict) else None
            if task_id:
                return dict(task_id=str(task_id), status='SUBMITTED', submitted_at=time.time())
            error = body.get('error', {}) if isinstance(body, dict) else {}
            message = str(error.get('message') or '未返回任务编号').replace(self.api_key, '[redacted]')[:1000]
            if 400 <= response.status_code < 500 and response.status_code not in {408, 425, 429}:
                raise DynamicVideoTaskFailed(f'火山方舟拒绝提交（HTTP {response.status_code}）：{message}', status='REJECTED')
            raise DynamicVideoTaskUnknown(f'火山方舟提交结果无法确认（HTTP {response.status_code}）：{message}')
        finally:
            response.close()

    def query(self, task_id):
        response = self.session.get(self.url(self.query_path.rstrip('/') + '/' + quote(task_id, safe='')),
                                    headers=self.headers, timeout=60)
        try:
            body = response.json()
            if not response.ok or not isinstance(body, dict):
                raise DynamicVideoTaskUnknown(f'火山方舟任务查询失败（HTTP {response.status_code}）')
            status = str(body.get('status') or '').lower()
            mapped = {'succeeded': 'SUCCESS', 'failed': 'FAILED', 'cancelled': 'FAILED',
                      'expired': 'FAILED', 'queued': 'QUEUED', 'running': 'RUNNING'}.get(status, 'UNKNOWN')
            return dict(status=mapped, result_url=(body.get('content') or {}).get('video_url'),
                        raw={**body, 'errorMessage': (body.get('error') or {}).get('message')})
        finally:
            response.close()

    def download(self, url, output, *, should_stop=None):
        try:
            return super().download(url, output, should_stop=should_stop)
        except requests.exceptions.ProxyError:
            # Signed TOS URLs need no API credentials. Retry only the download,
            # without the broken environment proxy, never the paid generation.
            _stop_if_requested(should_stop)
            original = self.session
            with requests.Session() as direct:
                direct.trust_env = False
                self.session = direct
                try:
                    return super().download(url, output, should_stop=should_stop)
                finally:
                    self.session = original
