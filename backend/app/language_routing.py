"""Explicit task-local language-model route shared by video and image planning."""
from contextlib import contextmanager

from .cloud_client import cloud_client_for
from .gemini_client import language_environment, language_model


@contextmanager
def project_language_scope(user_id, parameters, progress=None):
    if not parameters.get('use_cloud_image_pool'):
        if progress:
            progress(f'语言模型路由：用户 API 配置 · {language_model()}')
        yield
        return
    if user_id is None:
        raise RuntimeError('使用云端语言模型号池需要先登录账户；未改用个人 API。')
    try:
        runtime = cloud_client_for(int(user_id)).image_pool_runtime()
        base = str(runtime.get('base_url') or '').strip().rstrip('/')
        token = str(runtime.get('access_token') or '').strip()
        if not base or not token:
            raise ValueError('missing runtime')
    except Exception as exc:
        # Do not echo credentials or upstream URLs into project diagnostics.
        raise RuntimeError('云端语言模型号池登录状态不可用，请检查云端连接并重新登录；未改用个人 API。') from exc
    with language_environment({
        'LANGUAGE_PROVIDER': 'gemini',
        'GEMINI_API_KEY': token,
        'GEMINI_API_BASE': base + '/model-pool/v1',
        'GEMINI_MODEL': 'auto',
        'GEMINI_FALLBACK_MODELS': '',
    }):
        if progress:
            progress('语言模型路由：云端号池 · 模型由服务端分配，费用按号池结算；不使用个人 API。')
        yield
