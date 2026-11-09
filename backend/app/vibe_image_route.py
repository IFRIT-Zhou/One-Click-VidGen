"""Bind auxiliary images to the saved project route, never a Vibe default pool."""
import hashlib
import json


def fingerprint(config):
    keys = ('protocol', 'endpoint', 'reference_endpoint', 'query_url', 'model',
            'cloud_pool', 'method', 'size', 'resolution', 'ratio', 'quality',
            'response_format', 'request_parameters', 'reference_images')
    return hashlib.sha256(json.dumps({k: config.get(k) for k in keys},
        sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def resolve(project, user):
    from . import video_studio, vibe_preview
    import module4_video_render as visual
    record = vibe_preview._read_json(project / 'record.json', 32 * 1024 * 1024)
    parameters = record.get('creation_parameters', {})
    config = dict(video_studio._image_configs(record, user)[0])
    pool = config.get('cloud_pool') == '1'
    if pool != bool(parameters.get('use_cloud_image_pool')):
        raise ValueError('项目图像渠道与接口默认配置冲突，请在“本项目图像模型”保存配置；未切换渠道')
    if not config.get('api_key'):
        raise ValueError('本项目图像模型缺少可用凭据；未切换到号池或其他 API')
    if config.get('reference_images') is False:
        raise ValueError('本项目图像模型不支持参考图，无法提取背景；请先修改本项目图像模型')
    if not pool and not isinstance(parameters.get('image_profile_snapshot'), dict):
        config['resolution'] = parameters.get('image_resolution') or config.get('resolution') or '2k'
    if config.get('protocol') != 'openai_sync':
        config['endpoint'] = visual._runninghub_generate_url(config)
        config['query_url'] = config.get('query_url') or visual._runninghub_url('/openapi/v2/query')
    summary = dict(key=fingerprint(config), label=video_studio._image_route_label([config]),
                   provider='pool' if pool else 'custom', method=config.get('method') or 'running',
                   profile_id=parameters.get('image_profile_id'),
                   profile_name=(parameters.get('image_profile_snapshot') or {}).get('name', ''),
                   resolution=config.get('resolution') or '2k', size=config.get('size') or '2560x1440',
                   ratio=config.get('ratio') or '16:9')
    return config, summary


def same_pending_route(project, task, user, route):
    """Legacy background jobs were pinned to ICAN; changing route is explicit."""
    previous = task.get('image_route')
    if previous:
        return previous.get('key') == route.get('key')
    from . import image_studio
    ids = task.get('usage', {}).get('image_task_ids', [])
    if len(ids) != 1:
        return True
    path = image_studio.folder(int(user), ids[0]) / 'record.json'
    record = json.loads(path.read_text(encoding='utf-8'))
    return (record.get('provider'), record.get('method')) == (route.get('provider'), route.get('method'))
