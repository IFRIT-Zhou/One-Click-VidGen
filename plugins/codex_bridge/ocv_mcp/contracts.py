"""Bind only allowlisted operations to the running application's contracts."""
import copy
import re

from .catalog import OPERATIONS

# These are SDK tools, not native operation names. Tested against server.TOOLS.
MCP_TOOL_NAMES = frozenset(('ocv_guide', 'ocv_connection', 'ocv_upload_preflight',
    'ocv_operations', 'ocv_describe', 'ocv_read', 'ocv_prepare', 'ocv_commit',
    'ocv_receipt', 'ocv_discard', 'ocv_result', 'ocv_preview_asset'))
OPERATION_NAMES = frozenset(op.name for op in OPERATIONS)


class OperationError(ValueError):
    def __init__(self, operation, code, message, next_action, tool=None):
        self.detail = {'code': code, 'operation': operation, 'message': message,
                       'next_action': next_action}
        if tool:
            self.detail['tool'] = tool
        super().__init__(f'{code}: {message} {next_action}')


def check_operation_name(operation):
    if operation in MCP_TOOL_NAMES:
        raise OperationError(operation, 'STANDALONE_MCP_TOOL',
            f'{operation} 是独立 MCP 工具，不是 operation。',
            f'请直接调用 {operation}，参数以客户端的工具 inputSchema 为准；'
            '不要通过 ocv_describe、ocv_read 或 ocv_prepare 包装调用。', tool=operation)
    if operation not in OPERATION_NAMES:
        raise OperationError(operation, 'UNKNOWN_OPERATION', f'未知 operation：{operation}。',
            '请调用 ocv_operations 查找当前可用操作，再用 ocv_describe 获取契约；不要猜测名称。')


def is_credential_field(key):
    normalized = re.sub(r'[^a-z]', '', key.lower())
    return normalized in {'token', 'secret', 'authorization', 'cookie', 'cookies'} or normalized.endswith(
        ('apikey', 'apikeys', 'password', 'clientsecret', 'privatekey', 'accesstoken',
         'refreshtoken', 'sessiontoken', 'apitoken'))


def resolve(value, document, stack=()):
    if isinstance(value, list):
        return [resolve(v, document, stack) for v in value]
    if not isinstance(value, dict):
        return value
    if '$ref' in value:
        ref = value['$ref']
        if not ref.startswith('#/') or ref in stack or len(stack) > 24:
            raise ValueError('Unsupported recursive or external OpenAPI reference')
        target = document
        for part in ref[2:].split('/'):
            target = target[part.replace('~1', '/').replace('~0', '~')]
        return {**resolve(target, document, stack + (ref,)),
                **resolve({k: v for k, v in value.items() if k != '$ref'}, document, stack)}
    return {k: resolve(v, document, stack) for k, v in value.items()}


def object_schema(properties=None, required=None):
    return {'type': 'object', 'properties': properties or {},
            'required': required or [], 'additionalProperties': False}


def bind(document):
    bound, unavailable = {}, []
    for op in OPERATIONS:
        route = document.get('paths', {}).get(op.path, {})
        spec = route.get(op.method.lower())
        if spec is None:
            unavailable.append(op.name)
            continue
        properties, required = {}, []
        for location in ('path', 'query'):
            fields, needed = {}, []
            for raw in route.get('parameters', []) + spec.get('parameters', []):
                param = resolve(raw, document)
                if param.get('in') != location:
                    continue
                fields[param['name']] = param.get('schema', {})
                if param.get('required'):
                    needed.append(param['name'])
            if fields:
                properties[location] = object_schema(fields, needed)
                if needed:
                    required.append(location)
        body = resolve(spec.get('requestBody', {}), document)
        content = body.get('content', {})
        if op.transfer == 'upload':
            form = copy.deepcopy(content.get('multipart/form-data', {}).get('schema', {}))
            binary = [k for k, v in form.get('properties', {}).items() if v.get('format') == 'binary']
            if binary != ['file']:
                unavailable.append(op.name)
                continue
            form['properties'].pop('file')
            form['required'] = [k for k in form.get('required', []) if k != 'file']
            if form['properties']:
                form['additionalProperties'] = False
                properties['form'] = form
                if form['required']:
                    required.append('form')
            properties['local_path'] = {'type': 'string', 'minLength': 1,
                                        'description': '授权上传目录中的本地文件绝对路径。'}
            required.append('local_path')
        elif content:
            if 'application/json' not in content:
                unavailable.append(op.name)
                continue
            properties['body'] = copy.deepcopy(content['application/json'].get('schema', {}))
            # Prevent unnoticed misspelled fields at the top level. Nested maps
            # remain maps (e.g. indexed subtitle overrides).
            if properties['body'].get('type') == 'object' and 'properties' in properties['body']:
                properties['body']['additionalProperties'] = False
            if body.get('required'):
                required.append('body')
            props = properties['body'].get('properties', {})
            if op.name == 'ocv_bridge_draft_save':
                parameters = props.get('parameters', {})
                if not parameters.get('properties') or parameters.get('additionalProperties') is not False:
                    # A new adapter against an old server must not approve a
                    # write under the old opaque/free-form draft contract.
                    unavailable.append(op.name)
                    continue
            for key in list(props):
                if is_credential_field(key):
                    props.pop(key)
            if op.access != 'read' and 'expected_revision' in props:
                props['expected_revision'] = {'type': 'integer', 'minimum': 0}
                properties['body'].setdefault('required', []).append('expected_revision')
            if op.name == 'ocv_tts_resegment' and 'sync_text' in props:
                props['sync_text'] = {'type': 'boolean', 'const': True}
                properties['body'].setdefault('required', []).append('sync_text')
            if op.name == 'ocv_video_vibe_cloud_continue_review':
                needed_fields={'confirm_model','additional_model_calls','expected_model_calls','expected_model_limit','request_id'}
                if not needed_fields <= props.keys():
                    unavailable.append(op.name)
                    continue
                props['confirm_model']={'type':'boolean','const':True}
                needed=properties['body'].setdefault('required',[])
                needed.extend(key for key in sorted(needed_fields) if key not in needed)
            if op.name == 'ocv_video_vibe_cloud_continue_steps':
                needed_fields={'confirm_model','additional_model_calls','expected_model_calls','expected_model_limit',
                    'request_id','confirm_remaining_images','expected_image_requests','expected_image_limit'}
                if not needed_fields <= props.keys():
                    # Never silently degrade an explicit checkpoint/image
                    # permission contract against an older running backend.
                    unavailable.append(op.name)
                    continue
                props['confirm_model']={'type':'boolean','const':True}
                for name in ('expected_image_requests','expected_image_limit'):
                    props[name]={'allOf':[copy.deepcopy(props[name]),{'type':'integer','minimum':0}],
                        'description':'从本次 latest 的图片用量/原授权填写；即使禁止新增图也绑定原记录，不接受空值。'}
                needed=properties['body'].setdefault('required',[])
                needed.extend(key for key in sorted(needed_fields) if key not in needed)
            if op.name == 'ocv_video_vibe_cloud_create':
                # Consent must bind a concrete per-shot budget, not a mutable
                # UI default. A client talking to an incomplete old contract
                # must not approve a possibly billed request.
                if not {'confirm_cloud', 'max_image_requests'} <= props.keys():
                    unavailable.append(op.name)
                    continue
                budget_variants=props['max_image_requests'].get('anyOf',[props['max_image_requests']])
                if not any(v.get('type')=='integer' and v.get('minimum',1)<=0 for v in budget_variants):
                    unavailable.append(op.name)
                    continue
                props['confirm_cloud'] = {'type': 'boolean', 'const': True}
                template_mode = props.get('template_mode', {})
                layered = template_mode.get('type') == 'string' and 'layered' in template_mode.get('enum', [])
                if 'advanced_subject_separation' in props:
                    # Keep the live field/default, but expose the backend's
                    # cross-field rule before commit rather than at HTTP 422.
                    properties['body'].setdefault('allOf', []).append({
                        'if': {'properties': {'template_mode': {'const': 'layered'}}, 'required': ['template_mode']},
                        'else': {'properties': {'advanced_subject_separation': {'const': False}}},
                    })
                if layered:
                    # Intersect with the live limit; never manufacture image
                    # authority absent from this application's own contract.
                    native_budget = copy.deepcopy(props['max_image_requests'])
                    props['max_image_requests'] = {
                        'allOf': [native_budget, {'type': 'integer', 'enum': [0, 1]}],
                        'description': '必须显式填写：layered 原图分层为0或1（最多一次背景提取，沿用本项目图像模型）；semantic/shotcraft必须为0。不会静默改写预算。'}
                    properties['body'].setdefault('allOf', []).append({
                        'if': {'properties': {'template_mode': {'const': 'layered'}}, 'required': ['template_mode']},
                        'then': {'properties': {'max_image_requests': {'enum': [0, 1]}}},
                        'else': {'properties': {'max_image_requests': {'const': 0}}},
                    })
                else:
                    props['max_image_requests'] = {
                        'type': 'integer', 'const': 0,
                        'description': '当前服务未开放原图分层，代码动效必须显式填0；不自动抠图、不调用图像模型。'}
                if template_mode.get('type') == 'string' and 'shotcraft' in template_mode.get('enum', []):
                    # New MCP preparations select verified templates; the
                    # backend keeps its semantic default for older clients.
                    template_mode['default'] = 'shotcraft'
                    template_mode['description'] = (
                        '新计划默认 shotcraft：专用 API 选择已验证镜头模板，按原图风格编排并在本机渲染；'
                        'semantic 使用旧代码动效。' +
                        (('layered 为原图分层：默认稳定保留主体于底板；仅显式 advanced_subject_separation=true 才启用独立主体分离，可用本地 RMBG。背景编辑沿用项目图像模型；检查预览后再采用。'
                          if 'advanced_subject_separation' in props else
                          'layered 为旧版原图分层：本地 RMBG 提取主体、沿用项目图像模型提取背景；当前服务未提供主体策略开关。') if layered else '') +
                        '无需 GPT 会员；均只生成预览，不自动采用。')
                needed = properties['body'].setdefault('required', [])
                needed.extend(k for k in ('confirm_cloud', 'max_image_requests') if k not in needed)
        for placeholder in re.findall(r'{(\w+)}', op.path):
            if placeholder not in properties.get('path', {}).get('properties', {}):
                raise ValueError(f'Missing path contract: {op.name}/{placeholder}')
        bound[op.name] = (op, object_schema(properties, required))
    return bound, unavailable
