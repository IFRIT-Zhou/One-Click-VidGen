"""Offline resolution of the standalone BiRefNet runner and pinned model.

Neither a ComfyUI service, a workflow, nor model/node discovery over HTTP is
needed. An already installed OCV component may supply *just* the interpreter.
All candidate paths are installation-scoped; never use a project/media path,
the system PATH, or a Python chosen by a model response.
"""
from dataclasses import dataclass
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import subprocess
import time

from .vibe_rmbg_model import MODEL_FILES, RmbgModelError, verify_models

ROOT = Path(__file__).resolve().parents[2]


class RmbgRuntimeError(RuntimeError):
    pass


@dataclass(frozen=True)
class RmbgRuntime:
    python: Path
    model_dir: Path
    origin: str
    worker: Path


def _interpreters(root):
    """Prefer independent installations, then known portable Python bundles."""
    exe = 'python.exe' if os.name == 'nt' else 'bin/python'
    managed = root/'runtime'/'rmbg'
    try:
        version = json.loads((managed/'active.json').read_text(encoding='utf-8-sig'))['version']
    except (OSError, KeyError, ValueError, TypeError):
        version = None
    if isinstance(version, str) and re.fullmatch(r'rmbg-[0-9a-f]{12}-[0-9a-f]{12}', version):
        yield 'launcher-managed', managed/'versions'/version/('Scripts/python.exe' if os.name == 'nt' else exe)
    yield 'standalone', root/'tools'/'rmbg'/'python'/exe
    yield 'legacy-python', managed/'python'/exe
    yield 'ocv', root/'runtime'/'python'/exe
    for component in (root/'comfyui'/'engine', root/'runtime'/'comfyui'):
        try:
            version = json.loads((component/'active.json').read_text(encoding='utf-8-sig'))['version']
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if not isinstance(version, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', version):
            continue
        yield 'shared-python', component/'releases'/version/'python'/exe


_CHECK = """import importlib.util
names = ['torch', 'torchvision', 'safetensors', 'numpy', 'PIL', 'transformers', 'timm', 'einops', 'kornia']
missing = [name for name in names if importlib.util.find_spec(name) is None]
if missing: raise SystemExit('缺少：'+', '.join(missing))
from torch import inference_mode
from torchvision.ops import deform_conv2d
from torchvision.extension import _has_ops
if not _has_ops(): raise SystemExit('torchvision 本机运算组件不可用')
from safetensors.torch import load_file
from transformers import PreTrainedModel
from timm.models.layers import DropPath
from kornia.filters import laplacian
from einops import rearrange
print('OCV_RMBG_READY')
"""


@lru_cache(maxsize=8)
def _probe(exe, modified_ns):
    # No weights, GPU allocation, inference, installs or Hugging Face requests.
    # Only cache success. A missing dependency can be installed in-place and
    # the next attempt then picks it up without restarting OCV.
    try:
        probe = subprocess.run([str(exe), '-I', '-B', '-X', 'utf8', '-c', _CHECK],
            cwd=str(exe.parent), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace',
            timeout=60, check=False, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            env={**os.environ,'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','HF_HUB_DISABLE_TELEMETRY':'1'})
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RmbgRuntimeError('解释器无法启动或依赖自检超时') from exc
    output = probe.stdout.strip().splitlines()
    if probe.returncode or not output or output[-1] != 'OCV_RMBG_READY':
        detail = output[-1][:220] if output else '没有收到依赖自检回执'
        raise RmbgRuntimeError(detail)


_METADATA = """import sys,importlib.metadata as m,json
packages={}
for key in sys.argv[1:]:
    try: packages[key]=m.version(key)
    except m.PackageNotFoundError: pass
print(json.dumps(dict(python='%d.%d'%sys.version_info[:2],packages=packages)))
"""


def read_spec(root):
    try:
        spec=json.loads((Path(root)/'tools'/'rmbg'/'runtime-spec.json').read_text(encoding='utf-8-sig'))
        for row in [spec['install'], *spec['also_compatible']]:
            if (not isinstance(row['python'],str) or not isinstance(row['packages'],dict) or
                    not row['packages'] or not all(isinstance(k,str) and isinstance(v,str)
                                                   for k,v in row['packages'].items())):
                raise ValueError('版本清单内容无效')
        return spec
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RmbgRuntimeError('RMBG 依赖版本清单缺失或损坏；请通过 Launcher 更新或修复程序。') from exc


def matches(actual, target):
    return actual.get('python') == target['python'] and all(
        actual.get('packages', {}).get(name) == value for name, value in target['packages'].items())


def installed_versions(python, names):
    proc=subprocess.run([str(python),'-I','-B','-X','utf8','-c',_METADATA,*names],
        stdin=subprocess.DEVNULL, capture_output=True, timeout=30, check=True,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    return json.loads(proc.stdout)


@lru_cache(maxsize=16)
def _runtime_versions(exe, modified_ns, names, freshness):
    # Versioned Launcher environments normally never change in place. Briefly
    # cache metadata for existing shared bundles without masking an upgrade.
    return installed_versions(exe,names)


def ready_interpreter(root, spec):
    """Used by both the Launcher and the actual worker; never reads weights."""
    options = [spec['install'], *spec['also_compatible']]
    names = tuple(sorted({n for target in options for n in target['packages']}))
    issues = []
    for origin, exe in _interpreters(Path(root)):
        if not exe.is_file():
            continue
        try:
            modified=exe.stat().st_mtime_ns
            actual=_runtime_versions(exe, modified, names, int(time.monotonic()//30))
            if not any(matches(actual,target) for target in options):
                issues.append(f'{origin}: 依赖版本不符合 RMBG 清单')
                continue
            _probe(exe, modified)
            return origin,exe.resolve()
        except (OSError, ValueError, subprocess.SubprocessError, RmbgRuntimeError) as exc:
            detail=str(exc)[:220] if not isinstance(exc,subprocess.SubprocessError) else '解释器依赖检查失败'
            issues.append(f'{origin}: {detail}')
    detail = '；'.join(issues) if issues else '未找到随包 Python'
    raise RmbgRuntimeError('独立 RMBG 没有可用的 Python 依赖。请通过 Launcher 安装或修复 '
                           f'RMBG 运行环境；无需启动 ComfyUI。{detail}')


_WEIGHT = 'BiRefNet-general.safetensors'


def resolve_program(root):
    plugin = root/'tools'/'rmbg'
    entry = plugin/'worker.py'
    if not entry.is_file() or entry.is_symlink():
        raise RmbgRuntimeError('独立 RMBG 插件缺失：tools/rmbg/worker.py；请通过 Launcher 更新或修复程序。')
    # The upstream JSON omits a final newline; the bundled copy adds one.
    manifest = {name: spec for name,spec in MODEL_FILES.items() if name != _WEIGHT}
    manifest['config.json'] = (403, '373ac3cf2c200fc7698d9fd36bd1889298ae4b0e2ce77df1fe296e0eb5adf1b3')
    try:
        verify_models(plugin/'vendor'/'birefnet', files=manifest, legacy_node=False)
    except (OSError, ValueError, RmbgModelError) as exc:
        raise RmbgRuntimeError(f'{exc}；请通过 Launcher 更新或修复 RMBG 插件。') from exc
    return entry


def resolve_model(root=None):
    """Optional offline preflight: only for shots that actually need a cutout."""
    root = Path(root or ROOT).resolve()
    # A broken installed pack must not silently fall back to a different one.
    locations = [root/'tools'/'rmbg'/'models'/'BiRefNet', root/'models'/'rmbg'/'BiRefNet',
                 root/'workspace'/'vibe_rmbg_trial'/'models'/'RMBG'/'BiRefNet']
    model = next((p for p in locations if p.exists()), None)
    if model is None:
        raise RmbgRuntimeError(f'未安装本地 RMBG 权重；请将 {_WEIGHT} 放到 '
                               'tools/rmbg/models/BiRefNet。无需启动 ComfyUI，不自动下载。')
    try:
        verify_models(model, files={_WEIGHT: MODEL_FILES[_WEIGHT]})
    except (OSError, ValueError, RmbgModelError) as exc:
        raise RmbgRuntimeError(f'{exc}；请修复本地 BiRefNet-general 权重（Launcher 不覆盖权重）') from exc
    return model.resolve()


def resolve_runtime(root=None):
    root = Path(root or ROOT).resolve()
    worker = resolve_program(root)
    model = resolve_model(root)
    origin, exe = ready_interpreter(root,read_spec(root))
    return RmbgRuntime(exe, model, origin, worker)
