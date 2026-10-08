"""Data-only optional director profiles. Never execute plugin code."""
import json
from pathlib import Path

PLUGINS_DIR = Path(__file__).resolve().parents[2] / 'plugins'

def read_profile(folder):
    folder = Path(folder).resolve()
    manifest = folder / 'plugin.json'
    manifest.resolve().relative_to(folder)
    if manifest.stat().st_size > 256 * 1024:
        raise ValueError('插件清单过大')
    meta = json.loads(manifest.read_text(encoding='utf-8-sig'))
    if meta.get('manifest_version') != 1 or meta.get('type') != 'director_profile':
        raise ValueError('不是受支持的导演配置插件')
    entry = (folder / str(meta.get('entry', 'director.json'))).resolve()
    entry.relative_to(folder)
    if entry.suffix != '.json' or entry.stat().st_size > 512 * 1024:
        raise ValueError('导演插件必须是有限大小的JSON配置')
    data = json.loads(entry.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or data.get('schema_version') != 1:
        raise ValueError('导演配置版本无效')
    # Existing project compatibility slot; arbitrary new mode IDs are not implemented.
    if data.get('id') != 'medical_paper':
        raise ValueError('此导演配置槽尚未接入当前版本')
    for key in ('label', 'description', 'common'):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ValueError('导演配置缺少 ' + key)
    if not isinstance(data.get('stages'), dict) or not all(isinstance(v,str) for v in data['stages'].values()):
        raise ValueError('导演阶段规则无效')
    if not isinstance(data.get('design_fields'), list) or not all(isinstance(x,str) for x in data['design_fields']):
        raise ValueError('导演设计字段无效')
    for key in ('core_example','motion_example'):
        if not isinstance(data.get(key),dict):
            raise ValueError('导演示例无效')
    return data

def profiles(root=None):
    root = Path(root or PLUGINS_DIR).resolve()
    result = {}
    if not root.is_dir():
        return result
    for folder in sorted(root.iterdir()):
        try:
            folder.resolve().relative_to(root)
            if not folder.is_dir() or folder.name.startswith('.') or (folder / 'disabled').exists():
                continue
            data = read_profile(folder)
            result[data['id']] = None if data['id'] in result else data
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    return {k:v for k,v in result.items() if v is not None}

def get_profile(identity):
    return profiles().get(identity)
