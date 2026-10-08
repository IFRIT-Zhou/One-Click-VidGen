"""Per-task image channel selection; credentials stay in the Cloud API."""
from typing import Literal

ImageMethod = Literal['running', 'ican']
ImageSize = Literal['1024x1024', '1536x1024', '1024x1536', '2048x1152', '1152x2048', '2560x1440', '1440x2560']
DEFAULT_IMAGE_SIZE = '2560x1440'
REGULAR_SIZES = ('1024x1024', '1536x1024', '1024x1536', '2048x1152', '1152x2048', '2560x1440', '1440x2560')


def image_provider(method: str = 'running') -> str:
    if method not in ('running', 'ican'):
        raise ValueError('图片生成渠道必须是 running 或 ican')
    return 'ican' if method == 'ican' else 'runninghub'


def configure_channel(config: dict, method: str = 'running', size: str = DEFAULT_IMAGE_SIZE) -> dict:
    provider = image_provider(method)
    result = {**config, 'method': method}
    if provider == 'ican':
        if size not in REGULAR_SIZES:
            raise ValueError('请选择受支持的 ICAN 常规像素尺寸')
        result['size'] = size
        base = config['cloud_base_url'].rstrip('/')
        result.update(
            upload_url=base + '/image-pool/media/upload?provider=ican',
            account_url=base + '/image-pool/account-status?provider=ican',
            account_label='ICAN · GPT Image 2.5',
        )
    return result


def generation_payload(payload: dict, config: dict) -> dict:
    if config.get('cloud_pool') != '1':
        return payload
    method = config.get('method', 'running')
    result = {**payload, 'method': method, 'provider': image_provider(method)}
    if method == 'ican':
        result.pop('aspectRatio', None)
        result.pop('resolution', None)
        result.update(model='gpt-image-2.5', size=config.get('size', DEFAULT_IMAGE_SIZE))
    return result
