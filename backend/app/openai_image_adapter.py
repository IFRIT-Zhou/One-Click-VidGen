"""OpenAI-compatible synchronous images, without asynchronous provider endpoints."""
import base64
import io
import json
import mimetypes
from contextlib import ExitStack
from pathlib import Path
from urllib.parse import urlsplit

import requests
from PIL import Image


def render(macro, config, session, output):
    refs = [Path(p) for p in macro.get('reference_image_paths', []) if str(p).strip()]
    if refs and config.get('reference_images') is False:
        raise RuntimeError('所选同步图像接口未启用参考图能力')
    if len(refs) > 4 or any(not p.is_file() for p in refs):
        raise RuntimeError('参考图数量超过4张或文件缺失，未提交生成')
    payload = dict(config.get('request_parameters') or {})
    for reserved in ('image', 'image[]', 'api_key', 'headers', 'stream'):
        if reserved in payload:
            raise RuntimeError('额外参数包含不允许覆盖的图片、密钥或流式设置，未提交生成')
    payload.update(model=config['model'], prompt=macro['image_prompt'], n=1,
                   size=config.get('size') or 'auto')
    for key in ('quality', 'response_format'):
        if config.get(key):
            payload[key] = config[key]
    url = config['reference_endpoint' if refs else 'endpoint']
    if urlsplit(url).scheme not in ('http', 'https'):
        raise RuntimeError('同步图像接口缺少有效提交路径，未提交生成')
    headers = {'Authorization': 'Bearer ' + config['api_key']}
    try:
        with ExitStack() as stack:
            if refs:
                files = [('image[]' if len(refs) > 1 else 'image',
                          (p.name, stack.enter_context(p.open('rb')), mimetypes.guess_type(p.name)[0] or 'application/octet-stream'))
                         for p in refs]
                fields = {k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list, bool)) else str(v)
                          for k, v in payload.items()}
                response = session.post(url, headers=headers, data=fields, files=files,
                                        timeout=(30, config.get('timeout_seconds') or 600), allow_redirects=False)
            else:
                response = session.post(url, headers=headers, json=payload,
                                        timeout=(30, config.get('timeout_seconds') or 600), allow_redirects=False)
    except requests.RequestException as exc:
        raise RuntimeError('同步图像请求连接中断或超时，服务端可能已生成；请核查账单后手动重试，未自动重复提交') from exc
    if not response.ok or 300 <= response.status_code < 400:
        hints = {400: '请求参数或模型不支持', 401: 'API Key 无效', 403: '账号权限不足',
                 404: '接口路径或模型不存在，请检查协议及路径', 429: '请求过快或额度不足'}
        raise RuntimeError(f'图像接口 HTTP {response.status_code}：{hints.get(response.status_code, "服务商响应异常，请核查后重试")}；未自动重复提交')
    try:
        result = response.json()
        item = result['data'][0]
        if item.get('b64_json'):
            content = base64.b64decode(item['b64_json'], validate=True)
        elif item.get('url'):
            location = item['url']
            parsed = urlsplit(location)
            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError('无效图片 URL')
            # Never forward the API credential to an image storage host.
            image_response = session.get(location, timeout=(30, 120))
            image_response.raise_for_status()
            content = image_response.content
        else:
            raise ValueError('没有图片')
        with Image.open(io.BytesIO(content)) as image:
            image.load()
            output.parent.mkdir(parents=True, exist_ok=True)
            buffer = io.BytesIO()
            image.convert('RGB').save(buffer, 'JPEG', quality=95)
        temporary = output.with_suffix(output.suffix + '.tmp')
        temporary.write_bytes(buffer.getvalue())
        temporary.replace(output)
    except (KeyError, IndexError, TypeError, ValueError, OSError, requests.RequestException, Image.DecompressionBombError) as exc:
        raise RuntimeError('同步接口已响应，但图片解析或下载失败；支持 data[0].b64_json/url，未自动重新生成') from exc
    return output
