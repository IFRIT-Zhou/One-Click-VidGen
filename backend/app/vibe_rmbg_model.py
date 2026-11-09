"""Offline asset verification shared by RMBG consumers; never import a runner."""
from functools import lru_cache
import hashlib
from pathlib import Path


MODEL_REVISION = '4d000788a9698c7f8d67c8c6ce2b40c768f5b909'
MODEL_NAME = 'BiRefNet-general'
MODEL_FILES = {
    'BiRefNet-general.safetensors': (884878856, '77277264c0e8c74149d3ff2fade4fd8176965b7108f3c5fc3b8c9c811edb4519'),
    'birefnet.py': (92068, 'a9566611aa07a6fbb68ddb6ac8e19e62c879a428fbfc2f44efa53d233f2e302f'),
    'BiRefNet_config.py': (298, 'e7b8c2a74f6cea6a59553d517f71d47f2c1d90e670a13416af17c25fe2f3dc52'),
    'config.json': (402, '966a1f0165b072d2d1309756e71907750e535ba5c3790d8cd0e69713ff3a56cd'),
}


class RmbgModelError(RuntimeError):
    pass


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


@lru_cache(maxsize=64)
def _verify_file(path, size, expected, actual_size, inode, modified_ns, changed_ns, legacy_node):
    if actual_size == size and digest(path) == expected:
        return
    # The legacy pinned node locally normalizes one import and one dtype
    # expression. Only accept content which normalizes back to the pinned SHA.
    if legacy_node and path.name == 'birefnet.py' and actual_size <= 2 * size:
        content = path.read_text(encoding='utf-8')
        original = content.replace('from BiRefNet_config', 'from .BiRefNet_config').replace(
            'attn = attn.view(B_ // nW, nW, self.num_heads, N, N) + mask.to(attn.dtype).unsqueeze(1).unsqueeze(0)',
            'attn = attn.view(B_ // nW, nW, self.num_heads, N, N) + mask.unsqueeze(1).unsqueeze(0)')
        if hashlib.sha256(original.encode('utf-8')).hexdigest() == expected:
            return
    raise RmbgModelError(f'本地 RMBG 文件校验失败：{path.name}')


def verify_models(model_dir, *, files=None, legacy_node=True):
    """Check pinned assets, smallest first; cache only successful digests."""
    root = Path(model_dir).resolve()
    manifest = MODEL_FILES if files is None else files
    for name, (size, expected) in sorted(manifest.items(), key=lambda entry: entry[1][0]):
        path = root / name
        if not path.is_file() or path.is_symlink():
            raise RmbgModelError(f'本地 RMBG 模型文件缺失：{name}；不会自动下载')
        stat = path.stat()
        _verify_file(path, size, expected, stat.st_size, stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns, legacy_node)
    return {'model': MODEL_NAME, 'revision': MODEL_REVISION, 'model_dir': str(root)}
