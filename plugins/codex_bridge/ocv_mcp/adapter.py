"""Local-only MCP/application adapter; no imports of live OCV application state."""
import hashlib
import http.cookiejar
import json
import mimetypes
import re
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx
from jsonschema import Draft202012Validator

from .contracts import bind, is_credential_field
from .state import State

UPLOAD_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.wav', '.mp3', '.m4a', '.aac',
                     '.flac', '.ogg', '.mp4', '.mov', '.webm', '.mkv', '.avi', '.m4v',
                     '.srt', '.ass', '.vtt'}
MAX_BYTES = 256 * 1024 * 1024


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def clean(value):
    if isinstance(value, dict):
        return {k: ('[REDACTED]' if is_credential_field(k) else clean(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [clean(v) for v in value]
    if isinstance(value, str):
        value = re.sub(r'(?i)\bBearer\s+[\w.\-]+', 'Bearer [REDACTED]', value)
        value = re.sub(r'\bsk-[A-Za-z0-9_-]{12,}', '[REDACTED]', value)
        value = re.sub(r'(?i)((?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|authorization)\s*[=:]\s*)[^\s,;]+',
                       r'\1[REDACTED]', value)
    return value


def no_credentials(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if is_credential_field(key):
                raise ValueError('MCP 不接收密钥或账号凭据，请在 OCV 设置中配置后选择对应渠道')
            no_credentials(item)
    elif isinstance(value, list):
        for item in value:
            no_credentials(item)


class ApiError(Exception):
    def __init__(self, status, detail):
        self.status, self.detail = status, clean(detail)
        super().__init__(f'OCV HTTP {status}: {self.detail}')


class HttpSession:
    def __init__(self, base_url, cookie_file=None):
        parsed = urlsplit(base_url)
        if (parsed.scheme not in {'http', 'https'} or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}
                or parsed.username or parsed.password or parsed.path not in {'', '/'} or parsed.query or parsed.fragment):
            raise ValueError('本版只连接本机 OCV origin，不接受远程地址、路径或 URL 凭据')
        jar = http.cookiejar.MozillaCookieJar(str(cookie_file)) if cookie_file else http.cookiejar.CookieJar()
        if cookie_file:
            jar.load(ignore_discard=True, ignore_expires=False)
        self.base_url = base_url.rstrip('/')
        self.client = httpx.Client(base_url=self.base_url, cookies=jar, follow_redirects=False,
                                   trust_env=False, timeout=httpx.Timeout(300, connect=5))

    def close(self):
        self.client.close()

    def call(self, method, path, **kwargs):
        if not path.startswith('/api/') and path != '/openapi.json':
            raise ValueError('禁止访问非 OCV 接口')
        if path in {'/api/session', '/api/codex-bridge/info', '/openapi.json'}:
            kwargs['timeout'] = 15
        with self.client.stream(method, path, **kwargs) as response:
            data = bytearray()
            for block in response.iter_bytes():
                data.extend(block)
                if len(data) > 16 * 1024 * 1024:
                    raise ValueError('OCV JSON 响应过大，请读取单镜资料或分页日志')
            try:
                payload = json.loads(data)
            except (UnicodeError, ValueError):
                payload = {'detail': 'OCV 返回非 JSON 内容'}
            if not 200 <= response.status_code < 300:
                raise ApiError(response.status_code, payload.get('detail', payload))
            return payload

    def download(self, method, path, folder, **kwargs):
        temporary = folder / (uuid.uuid4().hex + '.part')
        try:
            with self.client.stream(method, path, **kwargs) as response:
                if not 200 <= response.status_code < 300:
                    response.read()
                    try:
                        detail = response.json().get('detail')
                    except ValueError:
                        detail = '素材下载失败'
                    raise ApiError(response.status_code, detail)
                mime = response.headers.get('content-type', 'application/octet-stream').split(';')[0]
                # Preview endpoints sometimes return JSON/base64 instead of a file.
                if mime == 'application/json':
                    payload = bytearray()
                    for block in response.iter_bytes():
                        payload.extend(block)
                        if len(payload) > 16 * 1024 * 1024:
                            raise ValueError('预览 JSON 超过 16 MB')
                    return None, clean(json.loads(payload))
                extension = { 'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp',
                              'audio/wav': '.wav', 'audio/x-wav': '.wav', 'audio/mpeg': '.mp3',
                              'video/mp4': '.mp4', 'text/plain': '.txt',
                              'application/x-subrip': '.srt' }.get(mime, '.bin')
                size = 0
                with temporary.open('xb') as handle:
                    for block in response.iter_bytes():
                        size += len(block)
                        if size > MAX_BYTES:
                            raise ValueError('文件超过 256 MB 下载上限，请使用 OCV 成果目录')
                        handle.write(block)
                final = temporary.with_suffix(extension)
                temporary.replace(final)
                return final, mime
        finally:
            temporary.unlink(missing_ok=True)


class Adapter:
    def __init__(self, base_url, state_dir, *, cookie_file=None, upload_roots=(), upload_files=(),
                 read_only=False, allow_generation=False, allow_delete=False, transport_factory=None):
        self.base_url = base_url.rstrip('/')
        self.transport_factory = transport_factory or (lambda: HttpSession(base_url, cookie_file))
        self.state = State(state_dir)
        self.upload_roots = tuple(Path(p).resolve(strict=True) for p in upload_roots)
        self.upload_files = frozenset(Path(p).resolve(strict=True) for p in upload_files)
        if any(not p.is_file() or p.suffix.lower() not in UPLOAD_EXTENSIONS for p in self.upload_files):
            raise ValueError('单文件上传授权必须指向支持的素材文件')
        if any(not p.is_dir() for p in self.upload_roots):
            raise ValueError('上传授权根必须是目录')
        self.read_only, self.allow_generation, self.allow_delete = read_only, allow_generation, allow_delete
        self.bound, self.unavailable = {}, []
        self.schema_lock = threading.Lock()
        self.slots = threading.BoundedSemaphore(4)
        self.dispatch_lock = threading.Lock()
        self.active = set()
        self.active_lock = threading.Lock()

    def authenticate(self, client):
        session = client.call('GET', '/api/session')
        user = session.get('user')
        if not user or user.get('id') is None:
            raise ValueError('请先登录 OCV；账号模式需提供有效 cookie-file，本地模式自动使用本地用户')
        client.call('GET', '/api/codex-bridge/info')  # Honor native plugin enable/auth gate.
        return digest([self.base_url, user['id'], session.get('auth_mode')])

    def ensure_catalog(self, client, refresh=False):
        with self.schema_lock:
            if not self.bound or refresh:
                self.bound, self.unavailable = bind(client.call('GET', '/openapi.json'))

    def connect(self, refresh=False):
        client = self.transport_factory()
        try:
            owner = self.authenticate(client)
            self.ensure_catalog(client, refresh)
            return client, owner
        except Exception:
            client.close()
            raise

    def allowed(self, op):
        if op.access == 'read':
            return True
        return not self.read_only and (op.access != 'generate' or self.allow_generation) and (
            op.access != 'delete' or self.allow_delete)

    def status(self):
        client, _ = self.connect(refresh=True)
        client.close()
        return {'connected': True, 'base_url': self.base_url, 'operations': len(self.bound),
                'read_only': self.read_only, 'generation_enabled': self.allow_generation,
                'delete_enabled': self.allow_delete, 'unavailable_operations': self.unavailable,
                'ui_automation': False, 'remote_connections': False,
                'upload_roots': [str(p) for p in self.upload_roots],
                'upload_files': [str(p) for p in sorted(self.upload_files)]}

    def operations(self, group='', offset=0, limit=30):
        client, _ = self.connect()
        client.close()
        rows = [{'name': op.name, 'description': op.description, 'access': op.access,
                 'enabled': self.allowed(op), 'transfer': op.transfer}
                for op, _ in self.bound.values() if not group or op.name.startswith('ocv_' + group)]
        return {'total': len(rows), 'items': rows[offset:offset+limit],
                'next_offset': offset+limit if offset+limit < len(rows) else None}

    def describe(self, operation):
        client, _ = self.connect()
        client.close()
        op, schema = self.bound[operation]
        return {'name': op.name, 'description': op.description, 'access': op.access,
                'enabled': self.allowed(op), 'input_schema': schema,
                'method': op.method, 'endpoint': op.path,
                'workflow': 'read' if op.access == 'read' else 'prepare -> review -> commit -> receipt -> native task status',
                'warning': '保留原生权限、revision、时间轴和素材校验；无法得知尚未保存的页面编辑。'}

    def validate(self, operation, arguments):
        if operation not in self.bound:
            raise ValueError('操作不存在或此 OCV 版本不支持；请先列出操作')
        op, schema = self.bound[operation]
        no_credentials(arguments)
        errors = sorted(Draft202012Validator(schema).iter_errors(arguments), key=lambda e: str(e.path))
        if errors:
            raise ValueError('参数不符合 OCV 契约：' + '; '.join(f'{list(e.path)}: {e.message}' for e in errors[:5]))
        for value in arguments.get('path', {}).values():
            if not isinstance(value, (str, int)) or str(value) in {'.', '..'} or re.search(r'[/\\%?#\x00-\x1f]', str(value)):
                raise ValueError('路径参数包含非法字符')
        if not self.allowed(op):
            raise ValueError('此操作未获 MCP 启动配置授权；请由用户开启写入/生成/删除权限')
        return op

    def route(self, op, args):
        path = op.path
        for key, value in args.get('path', {}).items():
            path = path.replace('{' + key + '}', quote(str(value), safe=''))
        if '{' in path:
            raise ValueError('缺少路径参数')
        return path

    def upload_file(self, arguments):
        raw = Path(arguments['local_path'])
        if not raw.is_absolute():
            raise ValueError('上传路径必须是绝对路径')
        file = raw.resolve(strict=True)
        if file not in self.upload_files and not any(root in file.parents for root in self.upload_roots):
            raise ValueError('文件不在用户授权的上传目录内')
        if not file.is_file() or file.suffix.lower() not in UPLOAD_EXTENSIONS:
            raise ValueError('不支持该上传文件类型')
        if file.stat().st_size > 100 * 1024 * 1024:
            raise ValueError('单次上传上限 100 MB；具体入口可能有更低限制')
        return file

    def guard(self, client, op, arguments):
        path = arguments.get('path', {})
        if 'identity' in path and ('/video-studio/' in op.path or '/codex-bridge/projects/' in op.path):
            record = client.call('GET', '/api/codex-bridge/projects/' + quote(str(path['identity']), safe='') + '/pack')
            result = {k: record.get(k) for k in ('id', 'revision', 'status', 'timeline_token')}
        elif 'job_id' in path:
            record = client.call('GET', '/api/jobs/' + quote(str(path['job_id']), safe=''))
            # Guard between preparation and submission. Atomic endpoint guards
            # still apply; this is not a replacement for a backend transaction.
            result = {k: record.get(k) for k in ('id', 'status', 'updated_at', 'step')}
        else:
            result = {}
        if op.transfer == 'upload':
            file = self.upload_file(arguments)
            result['upload_sha256'] = hashlib.sha256(file.read_bytes()).hexdigest()
        return result

    def compact(self, owner, payload):
        payload = clean(payload)
        text = json.dumps(payload, ensure_ascii=False)
        if len(text) <= 18000:
            return {'data': payload}
        ident = self.state.put_result(owner, payload)
        summary = {k: payload[k] for k in ('id', 'status', 'revision', 'message', 'error') if isinstance(payload, dict) and k in payload}
        return {'result_id': ident, 'summary': summary, 'characters': len(text),
                'root_keys': list(payload)[:80] if isinstance(payload, dict) else None,
                'instruction': '使用 ocv_result 按 JSON pointer 读取字段或分页列表；完整结果未丢弃。'}

    def execute(self, client, owner, op, arguments, upload_sha256=None):
        kwargs = {'params': arguments.get('query', {})}
        if 'body' in arguments:
            kwargs['json'] = arguments['body']
        path = self.route(op, arguments)
        if op.transfer == 'upload':
            file = self.upload_file(arguments)
            # Freeze the exact bytes inspected in prepare, rather than streaming
            # a mutable source file after the guard check.
            with file.open('rb') as handle:
                data = handle.read(100 * 1024 * 1024 + 1)
            if len(data) > 100 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != upload_sha256:
                raise ValueError('上传内容在确认后变化，未发送文件')
            kwargs.update(data={k: (json.dumps(v) if not isinstance(v, str) else v)
                                for k, v in arguments.get('form', {}).items()},
                          files={'file': ('upload' + file.suffix.lower(), data,
                                          mimetypes.guess_type(file.name)[0] or 'application/octet-stream')})
            return self.compact(owner, client.call(op.method, path, **kwargs))
        if op.transfer == 'download':
            folder = self.state.folder / 'assets' / owner
            folder.mkdir(parents=True, exist_ok=True)
            file, mime_or_payload = client.download(op.method, path, folder, **kwargs)
            return {'asset': self.state.add_asset(owner, file, mime_or_payload)} if file else self.compact(owner, mime_or_payload)
        payload = client.call(op.method, path, **kwargs)
        if op.name == 'ocv_creation_options':
            # /settings historically includes recent cross-project text and
            # local script names: expose only public creation choices.
            payload = {'tts': payload.get('tts', {}), 'model_options': payload.get('model_options', []),
                       'visual_modes': payload.get('visual_prompt', {}).get('modes', {})}
        return self.compact(owner, payload)

    def read(self, operation, arguments):
        client, owner = self.connect()
        try:
            op = self.validate(operation, arguments)
            if op.access != 'read':
                raise ValueError('写操作必须先 prepare，再 commit')
            return self.execute(client, owner, op, arguments)
        finally:
            client.close()

    def prepare(self, operation, arguments, request_id):
        if not re.fullmatch(r'[A-Za-z0-9_-]{8,100}', request_id):
            raise ValueError('request_id 必须为 8～100 位字母、数字、下划线或连字符')
        client, owner = self.connect()
        try:
            op = self.validate(operation, arguments)
            if op.access == 'read':
                raise ValueError('读取操作直接使用 ocv_read')
            guard = self.guard(client, op, arguments)
            plan = self.state.prepare(owner, request_id, digest([operation, arguments]), operation, arguments, guard)
            return {**self.receipt_data(owner, plan), 'description': op.description, 'arguments': clean(arguments),
                    'guard': json.loads(plan['guard']),
                    'state_changed_since_prepare': guard != json.loads(plan['guard']),
                    'requires_confirmation': op.access in {'generate', 'delete'},
                    'cost': '未报价；生成前可使用原生 quote/validate 操作核对，不能据此视为免费',
                    'native_validation': '尚未提交原生接口；业务条件仍以提交时的 OCV 校验为准',
                    'expires_at': plan['created'] + 600}
        finally:
            client.close()

    def receipt_data(self, owner, plan):
        with self.active_lock:
            active = plan['id'] in self.active
        result = {'plan_id': plan['id'], 'request_id': plan['request_id'], 'operation': plan['operation'],
                  'status': plan['status'], 'adapter_request_running': active}
        if plan['result']:
            result['result'] = json.loads(plan['result'])
        if plan['status'] == 'submitting' and not active:
            result['warning'] = '提交结果尚未确认（可能在另一连接执行或连接已中断）；请查原生任务状态，不会自动重发。'
        if plan['status'] == 'submitted':
            result['note'] = '这是原生接口提交回执，不代表配音、图片、视频或渲染任务已经完成；继续查询相应 status/get/logs。'
        return result

    def commit(self, plan_id, confirmed=False):
        client, owner = self.connect()
        transferred = False
        try:
            plan = self.state.get(owner, plan_id)
            if plan['status'] != 'prepared':
                return self.receipt_data(owner, plan)
            args = json.loads(plan['arguments'])
            op = self.validate(plan['operation'], args)
            if time.time() > plan['created'] + 600:
                raise ValueError('预览已过期，请核对当前状态后使用新的 request_id 准备')
            if op.access in {'generate', 'delete'} and not confirmed:
                raise ValueError('生成或删除必须取得用户对本计划的明确授权后 confirmed=true')
            guard = self.guard(client, op, args)
            with self.dispatch_lock:
                latest = self.state.get(owner, plan_id)
                if latest['status'] != 'prepared':
                    return self.receipt_data(owner, latest)
                if guard != json.loads(plan['guard']):
                    raise ValueError('项目/最终配音/上传文件在预览后已变化，请重新读取并准备；未提交')
                if not self.slots.acquire(blocking=False):
                    raise ValueError('MCP 同时提交请求已达 4 个，请稍后查询并重试同一计划')
                if not self.state.claim(owner, plan_id):
                    self.slots.release()
                    return self.receipt_data(owner, self.state.get(owner, plan_id))
            with self.active_lock:
                self.active.add(plan_id)

            def submit():
                try:
                    result = self.execute(client, owner, op, args, guard.get('upload_sha256'))
                    self.state.finish(owner, plan_id, 'submitted', result)
                except ApiError as exc:
                    # Server 5xx can occur after side effects; never blindly retry.
                    self.state.finish(owner, plan_id, 'failed' if 400 <= exc.status < 500 else 'unknown',
                                      {'http_status': exc.status, 'error': exc.detail})
                except Exception as exc:
                    self.state.finish(owner, plan_id, 'unknown',
                                      {'error': clean(str(exc)), 'advice': '查询原生任务/素材状态；禁止把网络超时当作未提交而自动重试'})
                finally:
                    client.close()
                    with self.active_lock:
                        self.active.discard(plan_id)
                    self.slots.release()

            worker = threading.Thread(target=submit, name='ocv-mcp-submit', daemon=True)
            try:
                worker.start()
            except Exception:
                with self.active_lock:
                    self.active.discard(plan_id)
                self.slots.release()
                self.state.finish(owner, plan_id, 'failed', {'error': '提交工作线程未启动；没有发送请求'})
                raise
            transferred = True
            return self.receipt_data(owner, self.state.get(owner, plan_id))
        finally:
            if not transferred:
                client.close()

    def receipt(self, plan_id):
        client, owner = self.connect()
        client.close()
        return self.receipt_data(owner, self.state.get(owner, plan_id))

    def discard(self, plan_id):
        client, owner = self.connect()
        client.close()
        return {'discarded': self.state.discard(owner, plan_id),
                'note': '只丢弃尚未提交的 MCP 计划。运行任务请使用原生 cancel/stop 操作。'}

    def result(self, result_id, pointer='', offset=0, limit=20):
        client, owner = self.connect()
        client.close()
        value = self.state.get_result(owner, result_id)
        if pointer:
            if not pointer.startswith('/'):
                raise ValueError('pointer 必须是 JSON pointer，如 /shots/0 或 /logs')
            for raw in pointer[1:].split('/'):
                key = raw.replace('~1', '/').replace('~0', '~')
                value = value[int(key)] if isinstance(value, list) else value[key]
        if isinstance(value, list):
            items, size = [], 0
            for item in value[offset:offset+limit]:
                count = len(json.dumps(item, ensure_ascii=False))
                if size + count > 18000:
                    if not items:
                        return {'total': len(value), 'oversized_item_pointer': pointer + '/' + str(offset),
                                'keys': list(item)[:80] if isinstance(item, dict) else None,
                                'instruction': '单项较大，请继续读取该 pointer 下的具体字段'}
                    break
                items.append(item)
                size += count
            next_offset = offset + len(items)
            return {'items': items, 'total': len(value),
                    'next_offset': next_offset if next_offset < len(value) else None}
        if isinstance(value, str) and len(value) > 18000:
            return {'text': value[offset:offset+18000], 'characters': len(value),
                    'next_offset': offset+18000 if offset+18000 < len(value) else None}
        if len(json.dumps(value, ensure_ascii=False)) > 18000:
            return {'keys': list(value), 'instruction': '结果仍较大，请使用更具体的 JSON pointer'}
        return {'data': value}

    def asset(self, asset_id):
        client, owner = self.connect()
        client.close()
        item = self.state.asset(owner, asset_id)
        path = Path(item['filename']).resolve()
        if self.state.folder / 'assets' / owner not in path.parents or not path.is_file():
            raise ValueError('素材不可用或路径无效')
        if path.stat().st_size > 8 * 1024 * 1024:
            raise ValueError('内联预览上限 8 MB，请通过下载回执中的本地文件预览')
        return path, item['mime']
