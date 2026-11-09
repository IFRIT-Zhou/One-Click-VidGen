"""Bounded, explicit Vibe-only multimodal calls. Never uses the language pool."""
import base64
import io
import json
import time

import requests
from PIL import Image, ImageDraw, ImageOps
from urllib3 import exceptions as urllib3_errors


# requests/urllib3 uses this budget for BOTH connecting and sending the full
# JSON body, switching to READ_TIMEOUT only after upload. Multimodal requests
# can be several MB, so 15s could abort a healthy but slower upload.
CONNECT_TIMEOUT = 60
READ_TIMEOUT = 45
MAX_FORMAT_REPAIR_CHARS = 64_000
_FINISH_REASONS = {'stop', 'length', 'content_filter', 'tool_calls', 'function_call',
                   'insufficient_system_resource'}

TRANSPARENCY_VIEW_NOTE = (
    '透明素材查看说明：仅实际含透明像素的图片会显示浅灰棋盘检查底；'
    '棋盘用于显示真实透明度和半透明边缘，不是素材内容，也不是需要保留或重建的背景。'
    '不透明原图不会添加棋盘，请勿把原图自身的纹理当作检查底。'
)


def _exception_chain(exc):
    pending, seen, errors = [exc], set(), []
    while pending and len(seen) < 32:
        error = pending.pop()
        if not isinstance(error, BaseException) or id(error) in seen:
            continue
        seen.add(id(error)); errors.append(error)
        pending.extend((error.__cause__, error.__context__, getattr(error, 'reason', None)))
        pending.extend(arg for arg in error.args if isinstance(arg, BaseException))
    return errors


def _transport_failure(exc, *, connect_timeout=CONNECT_TIMEOUT, read_timeout=READ_TIMEOUT):
    """Classify typed causes, including requests' wrapped read/write timeouts.

    Never serialize exceptions/URLs/credentials. A socket upload TimeoutError
    is wrapped in ProtocolError, unlike the typed urllib3 ReadTimeoutError.
    """
    errors = _exception_chain(exc)
    has = lambda *types: any(isinstance(error, types) for error in errors)
    if has(requests.exceptions.ProxyError, urllib3_errors.ProxyError):
        return 'proxy_error', '代理连接失败，请检查代理或 VPN 的连通性'
    if has(requests.exceptions.SSLError, urllib3_errors.SSLError):
        return 'tls_error', '安全连接失败，请检查网络、系统时间或证书'
    if has(requests.exceptions.ReadTimeout, urllib3_errors.ReadTimeoutError):
        return 'read_timeout', f'等待上游响应超时（读取等待上限 {read_timeout:g} 秒），可稍后手动重试'
    if has(requests.exceptions.ConnectTimeout):
        return 'connect_timeout', f'连接上游超时（连接等待上限 {connect_timeout:g} 秒），请检查网络或稍后重试'
    # NewConnectionError is a subclass of urllib3 ConnectTimeoutError, but does
    # not establish that a timeout occurred (DNS/refused connections included).
    if has(urllib3_errors.NewConnectionError):
        return 'connection_error', '无法建立上游连接，请检查网络或服务地址'
    if has(urllib3_errors.ConnectTimeoutError):
        return 'connect_timeout', f'连接上游超时（连接等待上限 {connect_timeout:g} 秒），请检查网络或稍后重试'
    if has(requests.exceptions.Timeout):
        return 'network_timeout', '上游请求等待超时，可稍后手动重试'
    if has(TimeoutError):
        return 'network_timeout', '网络收发等待超时（可能在发送请求期间），可稍后手动重试'
    if has(requests.exceptions.ChunkedEncodingError, requests.exceptions.ContentDecodingError,
           urllib3_errors.ProtocolError):
        return 'stream_interrupted', '上游响应传输中断，未收到完整方案，可稍后手动重试'
    if has(requests.exceptions.ConnectionError):
        return 'connection_error', '与上游的连接失败或中断，请检查网络或稍后重试'
    return 'request_error', '上游请求发生网络异常，请检查网络或稍后重试'


def _transport_details(exc):
    errors=_exception_chain(exc)
    # Only structural diagnostics; raw exception text can include credentials,
    # request bodies and proxy URLs. errno/winerror are numeric OS codes only.
    types=list(dict.fromkeys(type(error).__name__ for error in errors))
    codes=list(dict.fromkeys(code for error in errors for key in ('errno','winerror')
                            if type(code:=getattr(error,key,None)) is int))
    return {'exception_types':types,'network_errnos':codes}


class CloudCallError(RuntimeError):
    def __init__(self,message,usage=None):
        super().__init__(message)
        self.usage=usage


class ModelResponseFormatError(CloudCallError):
    """Completed model output, eligible for a caller's bounded format repair.

    The raw answer is deliberately separate from public usage/error text. It
    contains only final-answer content, never reasoning, prompts or credentials.
    This class does not itself retry or make another request.
    """
    def __init__(self,message,usage=None,*,response_text):
        super().__init__(message,usage)
        self.response_text=response_text


def image_block(path):
    with Image.open(path) as im:
        if im.width*im.height > 40_000_000:
            raise CloudCallError('参考图像素过大，请先提供较小副本')
        oriented=ImageOps.exif_transpose(im)
        # Dropping alpha with convert('RGB') exposes arbitrary hidden RGB
        # pixels left by segmentation. Flatten real transparency first, before
        # resampling, so neither hidden pixels nor their edge colours leak into
        # the visual review. Palette/tRNS transparency follows the same path.
        if 'A' in oriented.getbands() or 'transparency' in oriented.info:
            rgba=oriented if oriented.mode == 'RGBA' else oriented.convert('RGBA')
            alpha=rgba.getchannel('A')
            if alpha.getextrema()[0] < 255:
                rgb=Image.new('RGB',rgba.size,(236,236,236))
                draw=ImageDraw.Draw(rgb)
                cell=max(16,round(max(rgba.size)/2000*24))
                for y in range(0,rgba.height,cell):
                    for x in range(0,rgba.width,cell):
                        if (x//cell+y//cell)%2:
                            draw.rectangle((x,y,min(x+cell-1,rgba.width-1),
                                            min(y+cell-1,rgba.height-1)),fill=(216,216,216))
                rgb.paste(rgba,mask=alpha)
            else:
                rgb=oriented.convert('RGB')
        else:
            rgb=oriented.convert('RGB')
        rgb.thumbnail((2000,2000),Image.Resampling.LANCZOS)
        buf=io.BytesIO();rgb.save(buf,format='JPEG',quality=91)
    return {'type':'image_url','image_url':{'url':'data:image/jpeg;base64,'+base64.b64encode(buf.getvalue()).decode(),'detail':'high'}}


def chat_json(config, system, payload, images, cancelled, *, max_tokens=20000, timeout=180, thinking=False):
    """No fallback or hidden retries. Stream to enforce stop and a wall-clock limit."""
    if len(images)>32:raise CloudCallError('参考素材超过32张，请缩小本镜范围；未静默省略图片')
    content=[{'type':'text','text':json.dumps(payload,ensure_ascii=False)}]
    if images:
        content.append({'type':'text','text':TRANSPARENCY_VIEW_NOTE})
    for name,path in images.items():
        content.append({'type':'text','text':'素材标识：'+name})
        content.append(image_block(path))
    body={'model':config['model'],'messages':[{'role':'system','content':system},{'role':'user','content':content}],
          'max_tokens':max_tokens,'stream':True,'stream_options':{'include_usage':True},
          'response_format':{'type':'json_object'}}
    if config['model'].startswith('deepseek'):
        # Keep structured scene emission bounded: recursive deliberation over a
        # large JSON schema can consume the entire output window before any plan.
        body.update(thinking={'type':'enabled' if thinking else 'disabled'})
        if thinking:body['reasoning_effort']='low'
    # Same JSON encoding as requests' json=body; persist the byte count only.
    request_bytes=len(json.dumps(body,allow_nan=False).encode('utf-8'))
    connect_timeout=min(CONNECT_TIMEOUT,timeout)
    read_timeout=min(READ_TIMEOUT,timeout)
    parts=[];size=0;reasoning_size=0;usage={};finish=None;started=time.monotonic()
    phase='waiting_response'; received_events=0; http_status=None; stream_done=False

    def diagnostics(error_code=None):
        counts={k:v for k,v in usage.items() if k in {'prompt_tokens','completion_tokens','total_tokens','prompt_cache_hit_tokens','prompt_cache_miss_tokens'} and type(v) is int}
        result={'model':config['model'],'seconds':round(time.monotonic()-started,3),
                'answer_chars':size,'reasoning_chars':reasoning_size,'received_events':received_events,
                'transport_phase':phase,'request_bytes':request_bytes,'image_count':len(images),
                'connect_send_timeout_seconds':connect_timeout,'read_timeout_seconds':read_timeout,
                'total_budget_seconds':timeout,
                'finish_reason':finish if isinstance(finish,str) and finish in _FINISH_REASONS else 'unknown' if finish is not None else None,
                'stream_done':stream_done,**counts}
        if http_status is not None:result['http_status']=http_status
        if error_code:result['error_code']=error_code
        return result

    def failure(code, message, extra=None):
        info=diagnostics(code)
        if extra:info.update(extra)
        where='等待响应' if phase=='waiting_response' else '接收响应'
        progress='尚未收到方案正文' if size==0 else f'已收到 {size} 字符，方案未完成'
        return CloudCallError(f'Vibe 模型{message}；{where}，累计 {info["seconds"]:.1f} 秒，{progress}；未自动重试、未切换渠道',info)

    if cancelled(): raise InterruptedError('已停止 Vibe 任务')
    try:
        with requests.post(config['base_url'].rstrip('/')+'/chat/completions',
                headers={'Authorization':'Bearer '+config['api_key'],'Content-Type':'application/json'},
                json=body,stream=True,timeout=(connect_timeout,read_timeout),allow_redirects=False) as response:
            http_status=response.status_code
            if response.status_code!=200:
                hint='请核对专用 API 的密钥与权限' if response.status_code in (401,403) else '请检查服务状态或稍后手动重试'
                raise failure('http_error',f'返回 HTTP {response.status_code}，{hint}')
            phase='streaming'
            for line in response.iter_lines(chunk_size=1024):
                if cancelled(): raise InterruptedError('已停止本地等待；上游已发生的调用可能仍计费')
                if time.monotonic()-started>timeout: raise failure('total_timeout',f'调用超过本次 {timeout:g} 秒时间预算')
                if not line or not line.startswith(b'data:'):continue
                raw=line[5:].strip()
                if raw==b'[DONE]':
                    stream_done=True
                    break
                try:
                    event=json.loads(raw)
                    if not isinstance(event,dict):raise ValueError()
                except (ValueError,UnicodeError):
                    raise failure('invalid_stream','返回了无法解析的流式数据，请检查接口兼容性') from None
                received_events+=1
                if isinstance(event.get('usage'),dict): usage=event['usage']
                choices=event.get('choices') or []
                if not choices:continue
                delta=choices[0].get('delta') or {}
                # Reasoning is neither persisted nor exposed in logs.
                reasoning_size+=len(delta.get('reasoning_content') or '')
                value=delta.get('content') or ''
                size+=len(value)
                if size>500_000:raise failure('response_too_large','返回的动效方案超过安全长度限制')
                parts.append(value)
                finish=choices[0].get('finish_reason') or finish
    except requests.RequestException as exc:
        code,message=_transport_failure(exc,connect_timeout=connect_timeout,read_timeout=read_timeout)
        raise failure(code,message,_transport_details(exc)) from None
    safe_usage=diagnostics()
    if finish=='length':
        raise CloudCallError('动效方案被模型长度上限截断，请缩小设计范围后重试',
                             {**safe_usage,'error_code':'model_output_truncated'})
    response_text=''.join(parts)
    stripped_response=response_text.strip()
    # Character counts are safe to expose even when no usable JSON was returned;
    # answer_chars alone also counts whitespace and can be misleading.
    safe_usage.update(non_whitespace_chars=sum(not char.isspace() for char in response_text),
                      empty_after_strip=not stripped_response)
    parse_details={}
    try:
        raw=stripped_response
        if raw.startswith('```'):raw=raw.split('\n',1)[1].rsplit('```',1)[0]
        result=json.loads(raw)
        if not isinstance(result,dict):
            parse_details={'json_error_kind':'top_level_not_object'}
            raise ValueError()
    except (ValueError,IndexError) as exc:
        if isinstance(exc,json.JSONDecodeError):
            parse_details={'json_error_kind':'syntax','json_error_line':exc.lineno,
                           'json_error_column':exc.colno}
        elif isinstance(exc,IndexError):
            parse_details={'json_error_kind':'invalid_markdown_fence'}
        info={**safe_usage,**parse_details}
        if finish=='stop' and stream_done and not stripped_response:
            # A cleanly completed but empty answer is not a transport failure.
            # Keep it non-repairable: classification must not authorize another
            # paid model call or change the existing caller retry policy.
            info['error_code']='model_empty_completed'
            raise CloudCallError('上游已结束响应但正文为空或只有空白，未返回动效方案；请稍后手动重试；未自动重试、未切换渠道',
                                 info) from None
        # A malformed answer is not proof of interrupted transport. Only an
        # explicit stop and a bounded, nonempty final answer allow the caller
        # to spend its existing format-repair budget. Unknown completion,
        # filtering, truncation and transport errors are never auto-repaired.
        if finish=='stop' and stripped_response:
            info['error_code']='model_json_invalid'
            if len(response_text)<=MAX_FORMAT_REPAIR_CHARS:
                raise ModelResponseFormatError('模型已结束响应，但返回内容不符合 JSON 对象格式',
                    info,response_text=response_text) from None
            info['error_code']='model_json_repair_too_large'
            raise CloudCallError('模型已结束响应，但 JSON 格式错误且正文超过格式修正长度上限；未自动重试',info) from None
        info['error_code']='model_response_incomplete'
        raise CloudCallError('模型未返回可验证的完整 JSON 对象；未自动重试、未切换渠道',info) from None
    return result,safe_usage
