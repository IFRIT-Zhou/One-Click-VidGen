"""Isolated Remotion process runner and final-narration mux for cloud Vibe.

No dependency/browser downloads, model-authored JavaScript, network assets or
global process kills. Missing runtime fails before an upstream model is billed.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import time

from PIL import Image, ImageDraw

from .vibe_motion_plan import MotionPlan, RENDERER_VERSION, compile_motion_plan
from .vibe_shotcraft_plan import ShotcraftPlan, compile_shotcraft_plan, RENDERER_VERSION as SHOTCRAFT_RENDERER
from .vibe_render import FPS, _audio_length

ROOT = Path(__file__).resolve().parents[2]
ENGINE = ROOT / 'tools' / 'vibe_remotion'


def _runtime_paths():
    node_options = [os.environ.get('OCV_VIBE_REMOTION_NODE', ''), str(ROOT/'runtime'/'node'/'node.exe'),
                    shutil.which('node') or '', 'D:/nodejs/node.exe']
    node = next((Path(p).resolve() for p in node_options if p and Path(p).is_file()), None)
    browser_options = [os.environ.get('OCV_VIBE_REMOTION_BROWSER', '')]
    for parent in (ROOT/'runtime'/'hyperframes'/'.cache'/'hyperframes', ENGINE/'node_modules'/'.remotion'):
        if parent.is_dir():
            browser_options.extend(str(p) for p in parent.rglob('chrome-headless-shell.exe'))
            browser_options.extend(str(p) for p in parent.rglob('chrome-headless-shell') if p.is_file())
    if os.name != 'nt':
        browser_options.extend(shutil.which(name) or '' for name in ('chromium', 'chromium-browser', 'google-chrome'))
    browser = next((Path(p).resolve() for p in browser_options if p and Path(p).is_file()), None)
    if node is None or browser is None or not (ENGINE/'render.mjs').is_file():
        raise RuntimeError('Remotion 本地组件未就绪：需要 Node、渲染依赖及本地 Chrome。请先安装 Vibe 渲染组件；本次未调用云端模型或退回旧绘图器')
    return node, browser


def _terminate(proc):
    if proc.poll() is not None:
        return
    if os.name == 'nt':
        command = Path(os.environ.get('SystemRoot', 'C:/Windows'))/'System32'/'taskkill.exe'
        try:
            subprocess.run([str(command), '/PID', str(proc.pid), '/T', '/F'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=8, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try: os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError: pass
    if proc.poll() is None: proc.kill()
    proc.wait(timeout=8)


def _run(command, *, timeout, cancelled=None, log=None):
    if cancelled and cancelled(): raise InterruptedError('已停止 Remotion 本地渲染')
    began = time.monotonic()
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    # Popen.__exit__ waits without a deadline. Use explicit, bounded cleanup so
    # a failed tree termination or an inherited stdout pipe cannot hang again.
    proc = subprocess.Popen(command, cwd=str(ENGINE), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding='utf-8', errors='replace', creationflags=flags,
            start_new_session=os.name != 'nt')
    output = ''
    communicated = False
    timed_out = False

    def remember(value):
        nonlocal output
        # TimeoutExpired.output is a cumulative snapshot (often bytes even in
        # text mode), not a new chunk. Appending duplicates every polling cycle.
        if isinstance(value, bytes): value = value.decode('utf-8', errors='replace')
        if isinstance(value, str) and (value or not output): output = value

    def cleanup():
        nonlocal communicated
        try:
            if proc.poll() is None:
                try: _terminate(proc)
                except Exception:
                    # Preserve the primary timeout/cancellation/execution error.
                    # This fallback still targets only the owned process.
                    try:
                        proc.kill()
                        proc.wait(timeout=2)
                    except Exception: pass
            if not communicated:
                try:
                    drained, _ = proc.communicate(timeout=2)
                    remember(drained)
                    communicated = True
                except subprocess.TimeoutExpired as exc:
                    remember(exc.output)
                except Exception: pass
        except Exception:
            # Even polling or best-effort process cleanup must not replace the
            # original execution failure, nor prevent saving its diagnostics.
            pass
        finally:
            if log is not None:
                try: log.write_text(output[-100_000:], encoding='utf-8')
                except Exception: pass
            # A Windows communicate reader can still own the pipe lock after a
            # drain timeout. Closing that pipe synchronously can block forever.
            if communicated:
                for stream in (proc.stdout, proc.stderr, proc.stdin):
                    if stream is not None:
                        try: stream.close()
                        except Exception: pass

    def timeout_message():
        stages = {'browser_launch': '启动浏览器', 'bundle': '准备渲染组件',
                  'composition': '加载画布', 'poster': '海报截图',
                  'frame_early': '开场帧截图', 'frame_middle': '中段帧截图',
                  'frame_hold': '停留帧截图', 'frame_end': '结尾帧截图',
                  'video': '视频渲染', 'browser_close': '关闭浏览器'}
        for line in reversed(output[-100_000:].splitlines()):
            if not line.startswith('OCV_RENDER_STAGE:'): continue
            try: item = json.loads(line[len('OCV_RENDER_STAGE:'):])
            except (ValueError, TypeError): continue
            if not isinstance(item, dict): continue
            stage, status = item.get('stage'), item.get('status')
            if isinstance(stage, str) and stage in stages and status in ('started', 'completed', 'failed'):
                label = '最近完成阶段' if status == 'completed' else '当前阶段'
                return f'Remotion 本地渲染超时（{label}：{stages[stage]}）；未修改正式镜头'
        return 'Remotion 本地渲染超时；未修改正式镜头'

    try:
        try:
            while True:
                if cancelled and cancelled(): raise InterruptedError('已停止 Remotion 本地渲染；未修改正式镜头')
                remaining = timeout-(time.monotonic()-began)
                if remaining <= 0:
                    timed_out = True
                    raise TimeoutError('Remotion 本地渲染超时；未修改正式镜头')
                try:
                    received, _ = proc.communicate(timeout=min(.2, remaining))
                    remember(received)
                    communicated = True
                    break
                except subprocess.TimeoutExpired as exc: remember(exc.output)
            if proc.returncode:
                # Output contains no API key; still avoid leaking arbitrary logs to UI.
                try:
                    failure = json.loads(output.strip().splitlines()[-1])
                except (ValueError, IndexError):
                    failure = {}
                if failure.get('kind') == 'validation':
                    detail = str(failure.get('error') or '未知排版问题')
                    match = re.search(r'OCV_LAYOUT_(?:OVERFLOW|OVERLAP):[^\r\n]*', detail)
                    concise = match.group(0) if match else detail.splitlines()[0]
                    raise ValueError('Remotion 排版校验失败：'+concise[:1200])
                tail = output[-1800:]
                raise RuntimeError('Remotion 组件执行失败：'+tail)
            return output
        finally:
            cleanup()
    except TimeoutError:
        if timed_out: raise TimeoutError(timeout_message()) from None
        raise


def preflight_remotion(*, cancelled=None, timeout=40,
                       required_renderer: str | None = None,
                       required_plan_version: str | None = None):
    """Open the local browser and optionally require explicit runtime capabilities."""
    node, browser = _runtime_paths()
    try:
        output = _run([str(node), str(ENGINE/'render.mjs'), '--check', '--browser', str(browser)],
                      timeout=timeout, cancelled=cancelled)
        result = json.loads(output.strip().splitlines()[-1])
        if not isinstance(result, dict) or result.get('ready') is not True:
            raise ValueError('not ready')
        if required_renderer is not None or required_plan_version is not None:
            for field, required in (('renderers', required_renderer),
                                    ('plan_versions', required_plan_version)):
                supported = result.get(field)
                if not isinstance(supported, list) or not supported or any(
                    not isinstance(value, str) or not value.strip() for value in supported
                ):
                    raise ValueError(f'本地组件未声明有效的 {field} 能力列表；请更新渲染组件')
                if required is not None and required not in supported:
                    raise ValueError(f'本地组件不支持所需能力 {field}={required}；请更新渲染组件')
    except (ValueError, IndexError, RuntimeError, TimeoutError) as exc:
        raise RuntimeError('Remotion 渲染依赖或浏览器检查未通过；请先修复本地 Vibe 渲染组件。本次未调用云端模型。'+str(exc)[-500:]) from None
    return {'node': str(node), 'browser': str(browser),
            'renderer': required_renderer if required_renderer is not None else RENDERER_VERSION}


def _proof(output_dir, metrics):
    rows = metrics.get('stage_frames') or []
    if not rows:
        rows = [{'file': 'detail_'+label+'.png', 'time': time}
                for label, time in metrics.get('detail_times', {}).items()]
    tiles = []
    times = []
    for row in rows[:8]:
        name = row.get('file', '')
        if Path(name).name != name or not name.endswith('.png'):
            raise ValueError('Remotion 核查帧文件名无效')
        with Image.open(output_dir/name) as image:
            image = image.convert('RGB'); image.thumbnail((480, 270))
            tile = Image.new('RGB', (480, 300), '#e7e9ed')
            tile.paste(image, ((480-image.width)//2, (270-image.height)//2))
            ImageDraw.Draw(tile).text((12, 280), f"{float(row['time']):.2f}s", fill='#182b3e')
            tiles.append(tile); times.append(float(row['time']))
    if not tiles: raise ValueError('Remotion 没有输出核查帧')
    cols = min(2, len(tiles)); sheet = Image.new('RGB', (cols*480, math.ceil(len(tiles)/cols)*300), '#e7e9ed')
    for index, tile in enumerate(tiles): sheet.paste(tile, (index%cols*480, index//cols*300))
    sheet.save(output_dir/'proof.jpg', quality=92)
    return times


def render_motion(*, plan: MotionPlan | ShotcraftPlan, assets: dict[str, Path], cues: dict[str, float],
                  audio: Path, start: float, duration: float, output_dir: Path,
                  ffmpeg: str, timeout: float = 240, cancelled=None) -> dict:
    is_shotcraft = isinstance(plan, ShotcraftPlan)
    compiled = (compile_shotcraft_plan if is_shotcraft else compile_motion_plan)(plan, cues, duration, assets)
    return render_compiled(compiled, plan.canvas, SHOTCRAFT_RENDERER if is_shotcraft else RENDERER_VERSION,
        assets, audio, start, duration, output_dir, ffmpeg, timeout, cancelled)


def render_compiled(compiled, canvas, renderer, assets, audio, start, duration, output_dir, ffmpeg, timeout, cancelled):
    if not math.isfinite(start) or start < 0 or not .1 <= duration <= 60 or not 0 < timeout <= 300:
        raise ValueError('单镜时段或渲染时限不合法')
    if not audio.is_file() or start+duration > _audio_length(audio)+.001:
        raise ValueError('最终配音未覆盖整个镜头')
    if not output_dir.is_dir() or any(output_dir.iterdir()):
        raise ValueError('目标必须是独立空目录')
    for identity, path in assets.items():
        if not path.is_file(): raise ValueError(f'素材 {identity} 文件不存在')
    node, browser = _runtime_paths()
    started = time.monotonic()
    input_path = output_dir.parent/(output_dir.name+'-remotion-input.json')
    input_path.write_text(json.dumps({'plan': compiled, 'assets': {k: str(p.resolve()) for k, p in assets.items()},
                                     'duration': duration, 'fps': FPS}, ensure_ascii=False), encoding='utf-8')
    _run([str(node), str(ENGINE/'render.mjs'), '--input', str(input_path), '--output', str(output_dir),
          '--browser', str(browser), '--concurrency', '2'], timeout=timeout, cancelled=cancelled,
          log=output_dir.parent/(output_dir.name+'-remotion.log'))
    metadata = output_dir/'metadata.json'
    if not metadata.is_file() or metadata.stat().st_size > 512*1024:
        raise RuntimeError('Remotion 未输出有效渲染回执')
    metrics = json.loads(metadata.read_text(encoding='utf-8'))
    if metrics.get('frames') != math.floor(duration*FPS+.5) or metrics.get('fps') != FPS:
        raise ValueError('Remotion 帧数与最终配音时间轴不一致')
    if (metrics.get('width'), metrics.get('height')) != tuple(canvas) or metrics.get('renderer') != renderer:
        raise ValueError('Remotion 输出画布尺寸不一致')
    required = [output_dir/name for name in ('silent.mp4', 'detail_early.png', 'detail_middle.png', 'detail_end.png')]
    if not all(p.is_file() and p.stat().st_size for p in required):
        raise RuntimeError('Remotion 未输出完整预览文件')
    metrics['proof_times'] = _proof(output_dir, metrics)
    remaining = timeout-(time.monotonic()-started)
    if remaining <= 0: raise TimeoutError('Remotion 配音封装超时')
    preview = output_dir/'preview.mp4'
    _run([str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-nostdin', '-n',
          '-i', str(output_dir/'silent.mp4'), '-ss', f'{start:.6f}', '-t', f'{duration:.6f}',
          '-protocol_whitelist', 'file', '-f', 'wav', '-i', str(audio),
          '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
          '-movflags', '+faststart', str(preview)], timeout=remaining, cancelled=cancelled)
    if not preview.is_file() or not preview.stat().st_size:
        raise RuntimeError('Remotion 原配音封装失败')
    metrics.update(renderer=renderer, duration_seconds=duration,
                   render_seconds=round(time.monotonic()-started, 3))
    return metrics


def render_layered_still(compiled, canvas, assets, output_dir, timeout=120, cancelled=None) -> Path:
    """Render a complete poster for visual review, without audio or video encoding.

    The caller supplies an already static plan (all at/enter/exit = 0). Inputs
    and process diagnostics live next to the private output directory, matching
    render_compiled; only validated poster pixels and metadata are inside it.
    """
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 300:
        raise ValueError('静态海报渲染时限不合法')
    if not isinstance(compiled, dict) or compiled.get('version') != 'ocv-layered-v1':
        raise ValueError('静态海报需要有效的原图分层方案')
    elements = compiled.get('elements')
    if not isinstance(elements, list) or not elements or any(not isinstance(e, dict) for e in elements):
        raise ValueError('静态海报缺少可用图层')
    if list(canvas) != compiled.get('canvas'):
        raise ValueError('静态海报画布与方案不一致')
    if any(any(e.get(key, 0) != 0 for key in ('at', 'enter', 'exit')) for e in elements):
        raise ValueError('静态海报必须显示所有图层且不含入场、退场动画')
    ends = [e.get('end', 1) for e in elements]
    if any(type(end) not in (int, float) or not math.isfinite(end) or not .1 <= end <= 60 for end in ends):
        raise ValueError('静态海报图层时段不合法')
    duration = max(ends)
    output_dir = Path(output_dir)
    if not output_dir.is_dir() or any(output_dir.iterdir()):
        raise ValueError('目标必须是独立空目录')
    paths = {identity: Path(path) for identity, path in assets.items()}
    for identity, path in paths.items():
        if not path.is_file():
            raise ValueError(f'素材 {identity} 文件不存在')
    if cancelled and cancelled():
        raise InterruptedError('已停止静态海报渲染')
    node, browser = _runtime_paths()
    input_path = output_dir.parent/(output_dir.name+'-remotion-input.json')
    input_path.write_text(json.dumps({'plan': compiled, 'assets': {k: str(p.resolve()) for k, p in paths.items()},
                                     'duration': duration, 'fps': FPS}, ensure_ascii=False), encoding='utf-8')
    _run([str(node), str(ENGINE/'render.mjs'), '--still-only', '--input', str(input_path),
          '--output', str(output_dir), '--browser', str(browser), '--concurrency', '1'],
         timeout=timeout, cancelled=cancelled, log=output_dir.parent/(output_dir.name+'-remotion.log'))
    if cancelled and cancelled():
        raise InterruptedError('已停止静态海报渲染')
    metadata = output_dir/'metadata.json'
    if not metadata.is_file() or metadata.stat().st_size > 512*1024:
        raise RuntimeError('Remotion 未输出有效静态海报回执')
    metrics = json.loads(metadata.read_text(encoding='utf-8'))
    if (metrics.get('renderer') != 'ocv-remotion-layered-v1' or metrics.get('still_only') is not True
            or metrics.get('frames') != 1 or (metrics.get('width'), metrics.get('height')) != tuple(canvas)
            or metrics.get('checks', {}).get('layout') != 'passed'):
        raise ValueError('Remotion 静态海报回执或画布尺寸不一致')
    poster = output_dir/'poster.png'
    if not poster.is_file() or not poster.stat().st_size:
        raise RuntimeError('Remotion 未输出完整静态海报')
    with Image.open(poster) as image:
        if image.format != 'PNG' or image.size != tuple(canvas):
            raise ValueError('Remotion 静态海报图片尺寸或格式不一致')
        image.verify()
    return poster
