"""Model bookmarks referencing existing credentials; never store secrets in presets."""
import json
import os
import uuid
import threading
from pydantic import BaseModel, Field
from .config import save_project_env_values
from .gemini_client import LANGUAGE_PROVIDER_OPTIONS, language_base_url, language_model_allowed

LOCK = threading.RLock()


class LanguagePreset(BaseModel):
    id: str = Field(default='', max_length=80)
    name: str = Field(min_length=1, max_length=100)
    provider: str = Field(max_length=60)
    model: str = Field(min_length=1, max_length=256)
    thinking: str = 'follow'


def presets():
    try:
        rows = json.loads(os.getenv('OCV_LANGUAGE_PRESETS', '[]'))
        return rows if isinstance(rows, list) else []
    except ValueError:
        return []


def save_preset(data):
    config = LANGUAGE_PROVIDER_OPTIONS.get(data.provider)
    if not config or config.get('disabled'):
        raise ValueError('请选择已适配的接口来源')
    if config.get('source') == 'official' and not language_model_allowed(data.provider, data.model):
        raise ValueError('官方接口请选择该接口支持的模型')
    if data.thinking not in {'follow', 'disabled', 'enabled'}:
        raise ValueError('思考模式无效')
    if any(char in data.model + data.name for char in '\r\n'):
        raise ValueError('名称和模型 ID 不能包含换行')
    row = data.model_dump()
    row.update(id=data.id or uuid.uuid4().hex, name=data.name.strip(), model=data.model.strip(),
               base_url=language_base_url(data.provider))
    if not row['model'] or not row['name'] or not row['base_url']:
        raise ValueError('请先保存接口地址，并填写名称与模型 ID')
    rows = presets()
    if data.id and not any(item['id'] == data.id for item in rows):
        raise ValueError('预设不存在，请刷新')
    rows = [item for item in rows if item['id'] != row['id']] + [row]
    if len(rows) > 50:
        raise ValueError('最多保存 50 个语言模型预设')
    save_project_env_values({'OCV_LANGUAGE_PRESETS': json.dumps(rows, ensure_ascii=False)})
    return row


def activate_preset(identity):
    row = next((item for item in presets() if item['id'] == identity), None)
    if not row:
        raise ValueError('模型预设不存在')
    provider = row['provider']
    config = LANGUAGE_PROVIDER_OPTIONS[provider]
    if language_base_url(provider) != row['base_url']:
        raise ValueError('接口地址已改变，请在设置中重新核对并保存预设，避免密钥被用于不同地址')
    updates = {'LANGUAGE_PROVIDER': provider, config['model_env']: row['model']}
    if provider == 'custom':
        updates['CUSTOM_LLM_THINKING_MODE'] = row['thinking']
    save_project_env_values(updates)


def delete_preset(identity):
    save_project_env_values({'OCV_LANGUAGE_PRESETS': json.dumps(
        [row for row in presets() if row['id'] != identity], ensure_ascii=False)})
