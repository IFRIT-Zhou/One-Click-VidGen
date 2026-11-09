"""User-scoped Vibe designer settings, independent of narration/planning pools.

Secrets follow OCV's private .env convention, never project records or API responses.
"""
from __future__ import annotations

import base64
import hashlib
from io import BytesIO
import json
from pathlib import Path
import threading
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, field_validator
import requests

from .auth import require_user
from .config import ENV_PATH, _parse_env_lines, save_project_env_values

router = APIRouter(prefix='/api/vibe-model')
SETTINGS_PATH = Path(__file__).resolve().parents[2]/'workspace'/'settings'/'vibe_models.json'
LOCK = threading.RLock()
DEFAULTS = {'base_url':'https://api.deepseek.com','model':'deepseek-flash','max_image_requests':4}


class Settings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    base_url: str = Field(default=DEFAULTS['base_url'], min_length=1,max_length=2048)
    model: str = Field(default=DEFAULTS['model'],min_length=1,max_length=128)
    max_image_requests: int = Field(default=4,strict=True,ge=1,le=12)
    api_key: str | None = Field(default=None,max_length=2048)
    clear_key: bool = Field(default=False,strict=True)

    @field_validator('base_url')
    @classmethod
    def url(cls,value):
        value=value.strip().rstrip('/')
        p=urlparse(value)
        if p.scheme not in ('http','https') or not p.hostname or p.username or p.password or p.query or p.fragment or any(c.isspace() for c in value):
            raise ValueError('请输入不含账号、参数或空白的 HTTP(S) 服务地址')
        if p.scheme=='http' and p.hostname not in {'127.0.0.1','localhost','::1'}:
            raise ValueError('远程服务必须使用 HTTPS，以免明文传输密钥')
        return value

    @field_validator('model','api_key')
    @classmethod
    def single_line(cls,value):
        if value is None:return None
        if any(c in value for c in '\r\n\x00'):
            raise ValueError('配置不能包含换行或控制字符')
        return value.strip()

    @field_validator('model')
    @classmethod
    def nonempty_model(cls,value):
        if not value:raise ValueError('模型名称不能为空')
        return value


class TestRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    confirm_cloud: bool = Field(strict=True)


def _user(user_id):
    if isinstance(user_id,bool):raise ValueError('无效用户')
    value=str(user_id)
    if not value.isdigit() or int(value)<1:raise ValueError('无效用户')
    return str(int(value))


def _secret(user_id,base_url):
    # Bind each secret to its destination. If the metadata write fails after a
    # key save, a new provider's credential cannot be sent to the old provider.
    destination=hashlib.sha256(base_url.encode('utf-8')).hexdigest()[:20].upper()
    return 'OCV_VIBE_API_KEY_USER_'+_user(user_id)+'_'+destination


def _read():
    if not SETTINGS_PATH.exists():return {}
    try:
        data=json.loads(SETTINGS_PATH.read_text(encoding='utf-8'))
        if not isinstance(data,dict) or not isinstance(data.get('users',{}),dict):raise ValueError()
        return data.get('users',{})
    except (OSError,ValueError) as exc:
        raise HTTPException(409,'Vibe 接口配置文件无法读取，请检查本机配置备份') from exc


def get_config(user_id):
    uid=_user(user_id)
    with LOCK:
        saved=_read().get(uid,{})
        try:
            if not isinstance(saved,dict):raise ValueError()
            validated=Settings.model_validate({k:saved[k] for k in DEFAULTS if k in saved})
            config={k:getattr(validated,k) for k in DEFAULTS}
        except ValueError as exc:
            raise HTTPException(409,'Vibe 接口配置无效，请重新保存专用接口设置') from exc
        config['api_key']=_parse_env_lines(ENV_PATH).get(_secret(uid,config['base_url']),'')
        return config


def public_config(user_id):
    data=get_config(user_id)
    return {k:data[k] for k in DEFAULTS}|{'configured':bool(data['api_key']),'has_api_key':bool(data['api_key']),'source':'vibe_dedicated',
        'generation_policy':'source_style_code_only','image_generation_enabled':False,'effective_max_image_requests':0}


def save_config(user_id, data:Settings):
    uid=_user(user_id)
    with LOCK:
        before=get_config(uid)
        if before['api_key'] and before['base_url']!=data.base_url and not data.api_key and not data.clear_key:
            raise HTTPException(422,'更换服务地址时请重新填写 API Key，旧密钥不会发送到新地址')
        users=_read()
        users[uid]={k:getattr(data,k) for k in DEFAULTS}
        SETTINGS_PATH.parent.mkdir(parents=True,exist_ok=True)
        if data.clear_key or data.api_key:
            save_project_env_values({_secret(uid,data.base_url):'' if data.clear_key else data.api_key})
        temp=SETTINGS_PATH.with_suffix('.tmp')
        temp.write_text(json.dumps({'version':1,'users':users},ensure_ascii=False,indent=2),encoding='utf-8')
        temp.replace(SETTINGS_PATH)
        return public_config(uid)


@router.get('')
def read_settings(request:Request):return public_config(require_user(request)['id'])


@router.put('')
def write_settings(data:Settings,request:Request):return save_config(require_user(request)['id'],data)


@router.post('/test')
def test_settings(data:TestRequest,request:Request):
    user=require_user(request)['id']
    if not data.confirm_cloud:raise HTTPException(422,'连接测试会发送一张程序生成的小图，可能产生少量模型费用，请先确认')
    config=get_config(user)
    if not config['api_key']:raise HTTPException(422,'请先保存 Vibe 专用 API Key')
    image=BytesIO();Image.new('RGB',(32,32),(230,20,20)).save(image,format='PNG')
    content=[{'type':'text','text':'只回答这张图片的主要颜色，不要解释。'},
             {'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(image.getvalue()).decode('ascii')}}]
    try:
        response=requests.post(config['base_url'].rstrip('/')+'/chat/completions',
            headers={'Authorization':'Bearer '+config['api_key'],'Content-Type':'application/json'},
            json={'model':config['model'],'messages':[{'role':'user','content':content}],
                  'max_tokens':128,'thinking':{'type':'disabled'},'stream':False},
            timeout=(10,45),allow_redirects=False)
        if response.status_code!=200:
            raise HTTPException(502,f'Vibe 连接测试失败：HTTP {response.status_code}。请核对地址、密钥与模型的看图能力。')
        result=response.json()
        answer=result['choices'][0]['message'].get('content') or ''
        if not isinstance(answer,str) or not ('红' in answer or 'red' in answer.lower()):
            raise HTTPException(502,'接口有返回，但未通过小图识别检查；请确认所选模型支持图片输入。')
        usage=result.get('usage') or {}
        usage={k:v for k,v in usage.items() if k in {'prompt_tokens','completion_tokens','total_tokens'} and isinstance(v,int)}
        return {'connected':True,'vision':True,'model':config['model'],'usage':usage,'message':'连接与看图测试通过；未发送任何项目素材。'}
    except HTTPException:raise
    except requests.Timeout as exc:
        raise HTTPException(504,'Vibe 连接测试超时，未自动重试；已保存配置保留') from exc
    except (requests.RequestException,ValueError,KeyError,IndexError,TypeError) as exc:
        raise HTTPException(502,'Vibe 连接测试失败，网络异常或响应格式不兼容；未自动重试') from exc
