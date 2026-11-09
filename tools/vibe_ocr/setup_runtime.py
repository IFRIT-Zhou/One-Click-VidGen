"""Explicit optional OCR setup. --check is offline; only --install downloads.

Code lives under tools/vibe_ocr, Python under runtime/vibe_ocr, and weights
under models/vibe_ocr. No OCV/ComfyUI Python environment is modified.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import urllib.request
from urllib.parse import urlparse
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.app.vibe_ocr_runtime import read_spec, fingerprint, resolve_runtime, _run

FLAGS = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
PYPI = 'https://pypi.org/simple'


def touchable(root, relative):
    path = root.resolve()
    for part in Path(relative).parts:
        if part in ('', '.', '..') or Path(part).name != part:
            raise ValueError('无效的组件路径')
        path = path/part
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 1024):
            raise ValueError('为保护用户文件，不通过目录链接安装 OCR：'+str(path))
    return path


@contextmanager
def install_lock(folder):
    with (folder/'setup.lock').open('a+b') as stream:
        if stream.tell() == 0:
            stream.write(b'x'); stream.flush()
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def check(root=ROOT):
    runtime = resolve_runtime(root)
    result = _run(runtime, dict(check_only=True, models=str(runtime['models']),
        runtime_fingerprint=runtime['fingerprint']), None)
    if result.get('ready') is not True:
        raise RuntimeError('OCR 离线自检未通过')
    return {'ready': True, 'version': runtime['spec']['version'],
            'backend': 'RapidOCR/ONNXRuntime-CPU', 'network_used': False}


def download_wheel(spec, destination):
    """The executable OCR package itself is also bound to an audited SHA256."""
    url = spec['rapidocr_wheel_url']
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.hostname != 'files.pythonhosted.org':
        raise ValueError('OCR 只从固定的官方 PyPI 文件地址下载')
    wheel = destination/Path(parsed.path).name
    with urllib.request.urlopen(url, timeout=60) as response, wheel.open('xb') as output:
        digest, size = hashlib.sha256(), 0
        while True:
            chunk = response.read(1024*1024)
            if not chunk:
                break
            size += len(chunk)
            if size > 64*1024*1024:
                raise ValueError('OCR 安装包大小异常')
            digest.update(chunk); output.write(chunk)
    if digest.hexdigest() != spec['rapidocr_wheel_sha256']:
        raise ValueError('OCR 官方安装包哈希校验失败，未安装')
    return wheel


def install(root=ROOT):
    root = Path(root).resolve()
    spec = read_spec(root)
    managed = touchable(root, 'runtime/vibe_ocr')
    versions = touchable(root, 'runtime/vibe_ocr/versions')
    pointer = touchable(root, 'runtime/vibe_ocr/active.json')
    touchable(root, 'runtime/vibe_ocr/setup.lock')
    versions.mkdir(parents=True, exist_ok=True)
    with install_lock(managed):
        try:
            check(root)
            return {'ready': True, 'already_installed': True}
        except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError):
            pass
        base = root/'runtime/python'/('python.exe' if os.name == 'nt' else 'bin/python')
        if not base.is_file():
            raise RuntimeError('未找到 OCV 便携 Python；不会改用系统 Python 或修改 ComfyUI 环境')
        runtime_id = 'ocr-'+fingerprint(spec)[:12]+'-'+uuid.uuid4().hex[:12]
        dest = touchable(root, 'runtime/vibe_ocr/versions/'+runtime_id)
        python = dest/('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith(('PIP_', 'UV_', 'PYTHON'))}
        env.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PIP_CONFIG_FILE=os.devnull,
                   PYTHONNOUSERSITE='1', UV_NO_PYTHON_DOWNLOADS='1', UV_NO_CONFIG='1')

        def run(args, *, capture=False, timeout=900):
            return subprocess.run(list(map(str, args)), cwd=root, env=env, check=True,
                stdin=subprocess.DEVNULL, capture_output=capture, timeout=timeout,
                creationflags=FLAGS)

        version = run([base, '-I', '-B', '-c', "import sys;print('%d.%d'%sys.version_info[:2])"], capture=True).stdout.decode().strip()
        if version != spec['python']:
            raise RuntimeError('此 OCR 组件需要随包 Python '+spec['python']+'，未修改任何已有环境')
        # venv --without-pip plus the base pip --python explicitly targets only
        # the new environment; the shared application's packages stay untouched.
        stdlib_tools = subprocess.run([str(base), '-I', '-B', '-c',
            "import venv,pip;from pip._internal.cli.main_parser import create_main_parser;assert create_main_parser().has_option('--python')"],
            capture_output=True, timeout=20, creationflags=FLAGS).returncode == 0
        if stdlib_tools:
            run([base, '-I', '-B', '-m', 'venv', '--without-pip', dest])
            pip = [base, '-I', '-B', '-m', 'pip', '--isolated', '--python', python]
        else:
            uv = root/'tools/uv/uv.exe'
            if not uv.is_file():
                raise RuntimeError('便携 Python 缺少 venv/pip 且没有 tools/uv/uv.exe；请修复整合包')
            run([uv, '--no-config', 'venv', '--python', base, '--no-python-downloads', dest])
            pip = None
        if python.resolve() == base.resolve() or not (dest/'pyvenv.cfg').is_file():
            raise RuntimeError('OCR 独立环境创建失败，未安装依赖')
        wheel = download_wheel(spec, dest)
        packages = [name+'=='+version for name, version in spec['packages'].items() if name != 'rapidocr']
        if pip:
            # antlr4-python3-runtime 4.9.3 is a pure-Python source distribution.
            # All execution is an explicit PyPI install in the new private venv.
            run([*pip, 'install', '--no-input', '--disable-pip-version-check', '--index-url', PYPI,
                 wheel, *packages])
        else:
            run([uv, '--no-config', 'pip', 'install', '--python', python, '--no-python-downloads',
                 '--default-index', PYPI, wheel, *packages])
        # The selected official package bundles fixed models. Copy them into the
        # separate, hash-named weight folder and check every byte before activation.
        package = json.loads(run([python, '-I', '-B', '-c',
            "import importlib.util,json;from pathlib import Path;print(json.dumps(str(Path(importlib.util.find_spec('rapidocr').origin).parent/'models')))"],
            capture=True).stdout)
        model_dir = touchable(root, 'models/vibe_ocr/'+fingerprint(spec)[:16])
        model_dir.mkdir(parents=True, exist_ok=True)
        for model in spec['models'].values():
            source = Path(package)/model['file']
            if source.stat().st_size != model['bytes'] or hashlib.sha256(source.read_bytes()).hexdigest() != model['sha256']:
                raise RuntimeError('官方 OCR 包中的权重与固定清单不符，未激活环境')
            target = touchable(root, str(model_dir.relative_to(root)/model['file']))
            temp = target.with_name(target.name+'.'+uuid.uuid4().hex+'.part')
            try:
                shutil.copyfile(source, temp)
                temp.replace(target)
            finally:
                temp.unlink(missing_ok=True)
        runtime = dict(python=python, worker=root/'tools/vibe_ocr/worker.py', models=model_dir,
                       spec=spec, fingerprint=fingerprint(spec))
        result = _run(runtime, dict(check_only=True, models=str(model_dir),
            runtime_fingerprint=fingerprint(spec)), None)
        if result.get('ready') is not True:
            raise RuntimeError('新 OCR 环境离线自检失败，保留此前环境')
        tmp = pointer.with_name('active-'+uuid.uuid4().hex+'.json')
        try:
            tmp.write_text(json.dumps({'version': runtime_id, 'fingerprint': fingerprint(spec)}), encoding='utf-8')
            tmp.replace(pointer)
        finally:
            tmp.unlink(missing_ok=True)
        # Do not remove old environments: in-flight read-only probes may use them.
        return {'ready': True, 'already_installed': False, 'backend': 'RapidOCR/ONNXRuntime-CPU'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true')
    mode.add_argument('--install', action='store_true')
    args = parser.parse_args()
    try:
        print(json.dumps(check() if args.check else install(), ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({'ready': False, 'error': str(exc)[:800]}, ensure_ascii=False))
        raise SystemExit(1)
