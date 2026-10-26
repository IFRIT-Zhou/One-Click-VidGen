"""Bind only allowlisted operations to the running application's contracts."""
import copy
import re

from .catalog import OPERATIONS


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
            for key in list(props):
                if is_credential_field(key):
                    props.pop(key)
            if op.access != 'read' and 'expected_revision' in props:
                props['expected_revision'] = {'type': 'integer', 'minimum': 0}
                properties['body'].setdefault('required', []).append('expected_revision')
            if op.name == 'ocv_tts_resegment' and 'sync_text' in props:
                props['sync_text'] = {'type': 'boolean', 'const': True}
                properties['body'].setdefault('required', []).append('sync_text')
        for placeholder in re.findall(r'{(\w+)}', op.path):
            if placeholder not in properties.get('path', {}).get('properties', {}):
                raise ValueError(f'Missing path contract: {op.name}/{placeholder}')
        bound[op.name] = (op, object_schema(properties, required))
    return bound, unavailable
