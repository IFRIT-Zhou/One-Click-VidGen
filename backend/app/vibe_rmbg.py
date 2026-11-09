"""In-process orchestration only; heavy imports and weights live in a short-lived child."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

from PIL import Image, ImageChops, ImageOps


def _check(cancelled):
    if cancelled and cancelled():
        raise InterruptedError('已停止本镜的独立抠图')


def _invoke(runtime, folder, manifest, cancelled, timeout):
    _check(cancelled)
    args = [str(runtime.python), '-I', '-B', '-X', 'utf8', str(runtime.worker),
            '--model-dir', str(runtime.model_dir), '--manifest', str(manifest)]
    env = {k:v for k,v in os.environ.items() if k.upper() not in ('PYTHONPATH','PYTHONHOME')}
    env.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', PYTHONNOUSERSITE='1',
               PYTHONIOENCODING='utf-8', HF_HUB_DISABLE_TELEMETRY='1')
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    # Worker is a single process (no image server, data loader or child pool).
    with (folder/'stderr.log').open('wb') as log, subprocess.Popen(
            args, cwd=folder, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=log, creationflags=flags) as proc:
        try:
            deadline=time.monotonic()+timeout
            while True:
                _check(cancelled)
                left=deadline-time.monotonic()
                if left<=0: raise TimeoutError('本地 RMBG 抠图超时；已结束本镜的工作进程')
                try:
                    stdout=proc.communicate(timeout=min(left,.25))[0]
                    break
                except subprocess.TimeoutExpired:
                    continue
            try:
                status=json.loads(stdout.decode('utf-8',errors='replace').strip().splitlines()[-1])
            except (ValueError,IndexError):
                status={}
            if not isinstance(status,dict) or proc.returncode or status.get('status')!='completed':
                reason=status.get('message','工作进程异常退出，请检查 RMBG 依赖和可用显存') if isinstance(status,dict) else '无效的推理回执'
                raise RuntimeError('独立 RMBG 未完成：'+str(reason)[:500])
            return status
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)


def run_cutouts(sources, output_dir, *, should_stop=None, trim_padding=4, timeout=600):
    """Process 1–8 EXIF-oriented local crops with one model load. Retain only original RGB."""
    from .vibe_rmbg_runtime import resolve_runtime
    from .managed_comfyui import standalone_gpu_lease
    if not 1<=len(sources)<=8 or any(not isinstance(k,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',k) for k in sources):
        raise ValueError('无效的 RMBG 批次或素材编号')
    if (trim_padding is not None and (type(trim_padding) is not int or not 0<=trim_padding<=256)) or not 0<timeout<=1800:
        raise ValueError('无效的 RMBG 裁切参数')
    _check(should_stop)
    output_dir=Path(output_dir).resolve()
    output_dir.mkdir(parents=True,exist_ok=True)
    runtime=resolve_runtime()
    result={}
    # Layered jobs have a unique materials directory; do not overwrite any earlier file.
    with tempfile.TemporaryDirectory(dir=output_dir,prefix='rmbg_') as tmp:
        folder=Path(tmp); items=[]
        for name,source in sources.items():
            _check(should_stop)
            with Image.open(source) as im:
                if im.width*im.height>40_000_000 or getattr(im,'n_frames',1)!=1:
                    raise ValueError('RMBG 输入尺寸过大或不是单帧图片')
                rgba=ImageOps.exif_transpose(im).convert('RGBA')
                Image.alpha_composite(Image.new('RGBA',rgba.size,(48,48,48,255)),rgba).convert('RGB').save(folder/(name+'.png'))
            items.append({'input':str(folder/(name+'.png')),'output':str(folder/(name+'-mask.png'))})
        manifest=folder/'manifest.json'
        manifest.write_text(json.dumps({'items':items},ensure_ascii=False),encoding='utf-8')
        with standalone_gpu_lease(cancelled=should_stop,timeout=120):
            status=_invoke(runtime,folder,manifest,should_stop,timeout)
        if status.get('count')!=len(items):raise RuntimeError('RMBG 未返回完整批次')
        for (name,source),item in zip(sources.items(),items):
            _check(should_stop)
            with Image.open(source) as im, Image.open(item['output']) as mask:
                rgba=ImageOps.exif_transpose(im).convert('RGBA')
                if mask.mode!='L' or mask.size!=rgba.size:raise ValueError('RMBG 蒙版格式或尺寸不正确')
                alpha=ImageChops.multiply(mask,rgba.getchannel('A'))
            box=alpha.getbbox()
            lo,hi=alpha.getextrema()
            if not box or lo==hi or lo>=250:
                raise ValueError('RMBG 未能分离主体，请补充透明参考图')
            rgba.putalpha(alpha); x,y,r,b=box
            crop=rgba if trim_padding is None else rgba.crop((max(0,x-trim_padding),max(0,y-trim_padding),
                            min(rgba.width,r+trim_padding), min(rgba.height,b+trim_padding)))
            dest=output_dir/(name+'-rgba.png')
            if dest.exists():
                raise FileExistsError(f'RMBG 不会覆盖已有素材：{dest.name}')
            crop.save(folder/dest.name,format='PNG')
            result[name]=dest
        _check(should_stop)
        # Only publish once every mask has been validated. The enclosing job owns this directory.
        for dest in result.values():
            (folder/dest.name).rename(dest)
    return result
