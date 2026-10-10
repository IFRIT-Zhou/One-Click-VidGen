"""RH workflow video transport; uploads and mapping are distinct from model API."""
from __future__ import annotations

import hashlib
import json
import mimetypes
import time
import requests
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from module6_dynamic_video import (
    RunningHubVideoProvider, DynamicVideoTaskFailed, DynamicVideoTaskUnknown,
    FAILURE, SUCCESS, validate_request, _stop_if_requested,
)


class WorkflowNode(BaseModel):
    node_id: str = Field(min_length=1, max_length=100, pattern=r'^[A-Za-z0-9_.:-]+$')
    field: str = Field(min_length=1, max_length=100, pattern=r'^[A-Za-z0-9_.:-]+$')


class WorkflowOverride(WorkflowNode):
    value: str | int | float | bool


def aiwood_workflow_preset():
    """Bindings verified against the owner's exported graph; no workflow assets."""
    return {
        'workflow_id': '2108955980238266370',
        'image_nodes': [{'node_id': '150', 'field': 'image'}],
        'prompt_node': {'node_id': '232', 'field': 'value'},
        'duration_node': {'node_id': '132', 'field': 'value'},
        'output_nodes': ['180'], 'preferred_output': '180',
        'instance_type': 'default', 'use_personal_queue': False,
        'overrides': [
            {'node_id': '115', 'field': 'aspect_ratio', 'value': '16:9 (Widescreen)'},
            {'node_id': '115', 'field': 'megapixels', 'value': 1.0},
            {'node_id': '115', 'field': 'multiple', 'value': 32},
            {'node_id': '180', 'field': 'frame_rate', 'value': 24},
            {'node_id': '180', 'field': 'trim_to_audio', 'value': False},
        ],
    }


class WorkflowConfig(BaseModel):
    workflow_id: str = Field(default='', max_length=100, pattern=r'^\d*$')
    image_nodes: list[WorkflowNode] = Field(default_factory=list, max_length=9)
    prompt_node: WorkflowNode | None = None
    duration_node: WorkflowNode | None = None
    output_nodes: list[str] = Field(default_factory=list, max_length=20)
    preferred_output: str = Field(default='', max_length=100)
    instance_type: Literal['default', 'plus', 'ultra'] = 'default'
    use_personal_queue: bool = False
    overrides: list[WorkflowOverride] = Field(default_factory=list, max_length=100)

    @model_validator(mode='after')
    def unique_targets(self):
        import re
        if len(set(self.output_nodes)) != len(self.output_nodes) or any(
                not re.fullmatch(r'[A-Za-z0-9_.:-]+', n) for n in self.output_nodes):
            raise ValueError('输出节点编号无效或重复')
        nodes = [*self.image_nodes, *self.overrides]
        nodes += [n for n in (self.prompt_node, self.duration_node) if n]
        targets = [(n.node_id, n.field) for n in nodes]
        targets += [(n, 'save_output') for n in self.output_nodes]
        if len(set(targets)) != len(targets):
            raise ValueError('节点映射重复，同一节点字段只能配置一次')
        return self

    def require_ready(self):
        if not self.workflow_id or not self.image_nodes or not self.prompt_node or not self.duration_node:
            raise ValueError('请填写工作流 ID、图片、提示词和时长节点映射')
        if not self.output_nodes or self.preferred_output not in self.output_nodes:
            raise ValueError('请填写输出保存节点，并从中指定成品输出节点')


class RunningHubWorkflowVideoProvider(RunningHubVideoProvider):
    def __init__(self, api_key, *, workflow, **kwargs):
        self.workflow = WorkflowConfig.model_validate(workflow)
        self.workflow.require_ready()
        kwargs['submit_path'] = '/openapi/v2/run/workflow/' + self.workflow.workflow_id
        super().__init__(api_key, **kwargs)

    @property
    def identity(self):
        encoded = json.dumps(self.workflow.model_dump(), sort_keys=True).encode()
        return {**super().identity, 'protocol': 'runninghub_workflow',
                'workflow_fingerprint': hashlib.sha256(encoded).hexdigest()}

    def upload_image(self, path):
        try:
            return self._upload_file(path)
        except (requests.RequestException, ValueError, OSError) as exc:
            raise DynamicVideoTaskFailed('RH 素材上传未完成，尚未提交视频任务；请检查网络与上传接口后重试') from exc

    def _upload_file(self, path):
        with path.open('rb') as stream:
            response = self.session.post(self.url(self.upload_path),
                headers={'Authorization': f'Bearer {self.api_key}'},
                files={'file': (path.name, stream, mimetypes.guess_type(path.name)[0] or 'application/octet-stream')},
                timeout=120)
            try:
                body = response.json()
                data = body.get('data') if isinstance(body, dict) else None
                filename = data.get('fileName') if isinstance(data, dict) else None
                if response.ok and body.get('code', 0) in (0, '0', 200, '200') and filename:
                    return str(filename)
                raise DynamicVideoTaskFailed(f'RH 工作流素材上传失败（HTTP {response.status_code}），未返回 fileName；尚未提交视频任务')
            finally:
                response.close()

    def submit(self, request, *, should_stop=None, before_submit=None):
        validate_request(request)
        if len(request.image_paths) > len(self.workflow.image_nodes):
            raise ValueError('参考图数量超过工作流已配置的图片槽位，请补充映射或减少所选参考图')
        rows = []
        def add(node, value):
            rows.append({'nodeId': node.node_id, 'fieldName': node.field, 'fieldValue': value})
        for node, path in zip(self.workflow.image_nodes, request.image_paths):
            _stop_if_requested(should_stop)
            add(node, self.upload_image(path))
        add(self.workflow.prompt_node, request.prompt.strip())
        add(self.workflow.duration_node, request.duration)
        for node in self.workflow.overrides:
            add(node, node.value)
        rows.extend({'nodeId': n, 'fieldName': 'save_output', 'fieldValue': True}
                    for n in self.workflow.output_nodes)
        payload = {'nodeInfoList': rows, 'addMetadata': True,
                   'instanceType': self.workflow.instance_type,
                   'usePersonalQueue': str(self.workflow.use_personal_queue).lower()}
        _stop_if_requested(should_stop)
        if before_submit:
            before_submit()
        response = self.session.post(self.url(self.submit_path), headers=self.headers, json=payload, timeout=120)
        try:
            body = response.json()
            body = body if isinstance(body, dict) else {}
            task = str(body.get('taskId') or '')
            if task:
                return {'task_id': task, 'status': str(body.get('status') or 'SUBMITTED').upper(),
                        'submitted_at': time.time()}
            code = body.get('errorCode', body.get('code'))
            message = str(body.get('errorMessage') or body.get('message') or '未返回任务编号')
            message = message.replace(self.api_key, '[redacted]')[:1000]
            rejected = response.status_code < 500 and response.status_code not in (408, 425, 429) and (
                code not in (None, '', 0, '0', 200, '200') or
                str(body.get('status', '')).upper() in FAILURE or response.status_code >= 400)
            if rejected:
                raise DynamicVideoTaskFailed(f'RH 工作流提交被拒绝（HTTP {response.status_code}，代码 {code}）：{message}')
            raise DynamicVideoTaskUnknown(f'RH 工作流提交结果无法确认（HTTP {response.status_code}）：{message}')
        finally:
            response.close()

    def query(self, task_id):
        result = super().query(task_id)
        if result['status'] in SUCCESS:
            rows = result['raw'].get('results') or []
            rows = [r for r in rows if isinstance(r, dict) and
                    str(r.get('nodeId')) == self.workflow.preferred_output and
                    (str(r.get('outputType', '')).lower() in ('mp4', 'mov', 'video') or
                     '.mp4' in str(r.get('url', '')).lower()) and
                    str(r.get('url', '')).startswith(('https://', 'http://'))]
            if not rows:
                raise DynamicVideoTaskUnknown(
                    f'RH 任务 {task_id} 已完成，但成品节点 {self.workflow.preferred_output} 未返回视频。'
                    '请检查输出节点编号与 save_output；已保留任务编号，请勿重复提交。')
            result['result_url'] = rows[0]['url']
        return result
