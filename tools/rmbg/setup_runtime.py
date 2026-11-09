"""Launcher entry point for optional Windows RMBG dependencies, never model weights.

--check is offline/read-only; --install must be selected explicitly in the Launcher.
Only a new, private environment may be written. Existing OCV/Comfy Python
installations may supply compatible packages but are never pip targets.
"""
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
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.app.vibe_rmbg_runtime import (
    RmbgRuntimeError, _probe, installed_versions, matches, read_spec, ready_interpreter,
)

PYPI = 'https://pypi.org/simple'
TORCH_INDEX = 'https://download.pytorch.org/whl/cu128'
_FLAGS = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
def compatible(root, spec):
    try:
        return ready_interpreter(root, spec)[0]
    except RmbgRuntimeError:
        return None


def touchable(root, relative):
    """Reject reparse-point parents (including Windows directory junctions)."""
    path = root.resolve()
    for part in Path(relative).parts:
        if part in ('.', '..') or not part or not Path(part).name == part:
            raise ValueError('无效的组件路径')
        path = path / part
        try:
            s = path.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(s.st_mode) or getattr(s, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError(f'为保护用户文件，不通过目录链接安装：{path}')
    return path


@contextmanager
def install_lock(folder):
    import msvcrt
    with (folder/'setup.lock').open('a+b') as f:
        if not f.tell():
            f.write(b'x'); f.flush()
        f.seek(0)
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise RuntimeError('另一份 Launcher 正在安装 RMBG；本次未开始') from None
        try:
            yield
        finally:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)


def install_environment(root, dest, target, *, base=None):
    base = base or root/'runtime/python/python.exe'
    pins = target['packages']
    base_versions = installed_versions(base, ['torch'])
    if base_versions['python'] != target['python']:
        raise RuntimeError(f"此组件安装器需要 OCV 便携 Python {target['python']}；请使用对应的整合包，不会改动系统 Python。")
    inherit = base_versions['packages'].get('torch') == pins['torch']
    env = {k:v for k,v in os.environ.items() if not k.upper().startswith(('PIP_', 'UV_', 'PYTHON'))}
    env.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PIP_CONFIG_FILE=os.devnull,
               PYTHONNOUSERSITE='1', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
               UV_NO_PYTHON_DOWNLOADS='1', UV_NO_CONFIG='1')

    def run(args):
        subprocess.run(list(map(str, args)), cwd=root, env=env, check=True, stdin=subprocess.DEVNULL,
                       timeout=2400, creationflags=_FLAGS)

    windows_py = dest/'Scripts/python.exe'
    # --system-site-packages only reuses the read-only base stack. pip/uv's install
    # destination is always the private venv. Never pass a shared interpreter as target.
    inherited = ['--system-site-packages'] if inherit else []
    has_stdlib_tools = subprocess.run([str(base), '-I', '-B', '-c',
        "import venv,pip;from pip._internal.cli.main_parser import create_main_parser;"
        "assert create_main_parser().has_option('--python')"], capture_output=True,
        timeout=20, creationflags=_FLAGS).returncode == 0
    if has_stdlib_tools:
        run([base, '-I', '-B', '-m', 'venv', '--without-pip', *inherited, dest])
        def pip(index, args):
            run([base, '-I', '-B', '-m', 'pip', '--isolated', '--python', windows_py, 'install',
                 '--no-input', '--disable-pip-version-check', '--only-binary=:all:', '--index-url', index, *args])
    else:
        uv = root/'tools/uv/uv.exe'
        if not uv.is_file():
            raise RuntimeError('此旧整合包缺少便携 venv/pip 和 tools/uv/uv.exe；请修复 OCV 便携 Python 或使用含 uv 的整合包。')
        run([uv, '--no-config', 'venv', '--python', base, '--no-python-downloads', *inherited, dest])
        def pip(index, args):
            run([uv, '--no-config', 'pip', 'install', '--python', windows_py, '--no-python-downloads',
                 '--only-binary', ':all:', '--default-index', index, *args])

    if windows_py.resolve() == base.resolve() or not (dest/'pyvenv.cfg').is_file():
        raise RuntimeError('独立环境创建失败，未修改依赖')
    # Install CUDA wheels separately, then resolve their ordinary dependencies
    # through PyPI with the exact installed CUDA package versions retained.
    tensor = [name+'=='+pins[name] for name in ('torch', 'torchvision')]
    pip(TORCH_INDEX, ['--no-deps', *tensor])
    pip(PYPI, [name+'=='+val for name,val in pins.items()])
    if not matches(installed_versions(windows_py, sorted(pins)), target):
        raise RuntimeError('依赖版本验证失败；保留原运行环境')
    _probe(windows_py, windows_py.stat().st_mtime_ns)


def install(root, spec):
    base = touchable(root, 'runtime/rmbg')
    versions = touchable(root, 'runtime/rmbg/versions')
    pointer = touchable(root, 'runtime/rmbg/active.json')
    touchable(root, 'runtime/rmbg/setup.lock')
    versions.mkdir(parents=True, exist_ok=True)
    with install_lock(base):
        if compatible(root, spec):
            print('已有兼容的 RMBG 依赖，不需下载。', flush=True)
            return
        fingerprint = hashlib.sha256(json.dumps(spec['install'], sort_keys=True).encode()).hexdigest()[:12]
        version = f'rmbg-{fingerprint}-{uuid.uuid4().hex[:12]}'
        new = versions/version
        new.mkdir()
        try:
            install_environment(root, new, spec['install'])
            # Old environment remains active until every import and pin has passed;
            # keep old versions for in-flight renders rather than deleting them.
            tmp = base/(version+'.json')
            with tmp.open('x', encoding='utf-8') as f:
                json.dump({'version': version}, f)
            tmp.replace(pointer)
        except BaseException:
            if new.resolve().parent == versions.resolve() and new.parent == versions:
                shutil.rmtree(new, ignore_errors=True)
            raise
    print('RMBG 推理依赖已就绪；没有下载权重，不需要启动 ComfyUI。', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true')
    mode.add_argument('--install', action='store_true')
    args = parser.parse_args()
    spec = read_spec(ROOT)
    if args.check:
        ready = compatible(ROOT, spec)
        if ready:
            print(f'RMBG 推理依赖符合当前版本（{ready}），无需下载。')
            return 0
        print('需要安装或更新 RMBG 推理依赖；离线检查未修改任何文件。')
        return 3
    if os.name != 'nt':
        raise RuntimeError('此依赖安装器仅用于 Windows OCV Launcher')
    install(ROOT, spec)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as e:
        print(f'RMBG 组件处理失败，原有激活环境未切换：{e}', file=sys.stderr)
        sys.exit(1)
