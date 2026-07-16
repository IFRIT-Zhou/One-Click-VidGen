from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
WORKSPACE_DIR = PROJECT_ROOT / "workspace"
VISUAL_DIR = WORKSPACE_DIR / "3_visual_template"
AUDIO_DIR = WORKSPACE_DIR / "2_audio_srt"
FINAL_DIR = WORKSPACE_DIR / "4_final_video"
PORTABLE_FFMPEG_DIR = PROJECT_ROOT / "tools" / "ffmpeg" / "bin"
PORTABLE_FFMPEG = PORTABLE_FFMPEG_DIR / "ffmpeg.exe"


def env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def render_workers() -> str:
    value = str(os.getenv("VIDEO_RENDER_WORKERS", "auto")).strip().lower()
    if value == "auto":
        return value
    try:
        workers = int(value)
    except ValueError:
        print(f"无效的 VIDEO_RENDER_WORKERS={value!r}，已回退为 auto。", flush=True)
        return "auto"
    return str(max(1, min(workers, 16)))


def build_render_command(node: str, cli: Path, composition: Path, output: Path) -> list[str]:
    command = [
        node,
        str(cli),
        "render",
        str(VISUAL_DIR),
        "--composition",
        composition.name,
        "--output",
        str(output),
        "--fps",
        str(os.getenv("VIDEO_RENDER_FPS", "30")),
        "--quality",
        str(os.getenv("VIDEO_RENDER_QUALITY", "standard")),
        "--workers",
        render_workers(),
    ]
    # Hyperframes probes the encoder before use and falls back to CPU encoding
    # when NVENC/QSV/AMF is unavailable, so this default remains portable.
    if env_flag("VIDEO_RENDER_GPU_ENCODING", True):
        command.append("--gpu")

    browser_gpu = str(os.getenv("VIDEO_RENDER_BROWSER_GPU", "auto")).strip().lower()
    if browser_gpu in {"1", "true", "on", "hardware", "force"}:
        command.append("--browser-gpu")
    elif browser_gpu in {"0", "false", "off", "software", "disable"}:
        command.append("--no-browser-gpu")
    # auto intentionally adds no flag: Hyperframes probes Chrome GPU and safely
    # falls back to SwiftShader if the current driver cannot capture reliably.
    return command


def ensure_media_bridge() -> None:
    source = AUDIO_DIR / "final_output.wav"
    if not source.exists():
        raise FileNotFoundError(f"找不到配音文件: {source}")
    target = VISUAL_DIR / "2_audio_srt" / source.name
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists() or target.stat().st_mtime_ns < source.stat().st_mtime_ns:
        shutil.copy2(source, target)


def with_subtitles(html: str, srt_path: Path) -> str:
    encoded = base64.b64encode(srt_path.read_bytes()).decode("ascii")
    replacement = f'window.base64Subtitle = "{encoded}";'
    rendered, count = re.subn(
        r"window\.base64Subtitle\s*=\s*(['\"]).*?\1\s*;",
        replacement,
        html,
        count=1,
        flags=re.S,
    )
    if count != 1:
        raise RuntimeError("HTML 缺少 window.base64Subtitle 注入槽")
    return rendered


def without_subtitles(html: str) -> str:
    return html.replace(
        "</head>",
        "<style>#subtitle-overlay { display: none !important; }</style></head>",
        1,
    )


def render(composition: Path, output: Path, phase_label: str) -> None:
    node = shutil.which("node")
    cli = PROJECT_ROOT / "node_modules" / "hyperframes" / "dist" / "cli.js"
    if not node or not cli.exists():
        raise RuntimeError("未找到 Hyperframes CLI，请先安装 Node 依赖")
    if not PORTABLE_FFMPEG.is_file():
        raise RuntimeError(f"未找到项目内便携 FFmpeg: {PORTABLE_FFMPEG}")

    command = build_render_command(node, cli, composition, output)
    render_env = os.environ.copy()
    # Hyperframes launches ffmpeg by name. Put the portable copy first so a
    # broken system/Chocolatey shim can never affect an exported project.
    render_env["PATH"] = f"{PORTABLE_FFMPEG_DIR}{os.pathsep}{render_env.get('PATH', '')}"
    print(
        "渲染加速配置: "
        f"workers={render_workers()}, "
        f"GPU编码={'开启' if '--gpu' in command else '关闭'}, "
        f"浏览器GPU={os.getenv('VIDEO_RENDER_BROWSER_GPU', 'auto')}",
        flush=True,
    )
    print(f"开始: {phase_label}", flush=True)
    process = subprocess.Popen(
        command,
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=render_env,
    )
    assert process.stdout is not None
    for line in process.stdout:
        text = line.rstrip()
        if text:
            print(f"[{phase_label}] {text}", flush=True)
    return_code = process.wait()
    if return_code != 0:
        raise RuntimeError(f"{phase_label}失败，退出码 {return_code}")
    if not output.exists() or output.stat().st_size == 0:
        raise RuntimeError(f"未生成视频文件: {output}")
    print(f"完成: {phase_label}", flush=True)


def main() -> None:
    html_path = VISUAL_DIR / "index.html"
    srt_path = AUDIO_DIR / "final_short.srt"
    if not html_path.exists():
        raise FileNotFoundError(f"找不到模块 4 HTML: {html_path}")
    if not srt_path.exists():
        raise FileNotFoundError(f"找不到模块 2 字幕: {srt_path}")

    ensure_media_bridge()
    FINAL_DIR.mkdir(parents=True, exist_ok=True)
    source_html = html_path.read_text(encoding="utf-8")
    subtitle_html = VISUAL_DIR / "index.with_subtitles.html"
    raw_html = VISUAL_DIR / "index.raw.html"
    subtitle_html.write_text(with_subtitles(source_html, srt_path), encoding="utf-8")
    raw_html.write_text(without_subtitles(source_html), encoding="utf-8")

    render_mode = str(os.getenv("VIDEO_RENDER_VARIANT", "both")).strip().lower()
    render_mode = render_mode if render_mode in {"subtitles", "raw", "both"} else "both"
    try:
        if render_mode in {"subtitles", "both"}:
            render(subtitle_html, FINAL_DIR / "final_with_subtitles.mp4", "字幕版")
        if render_mode in {"raw", "both"}:
            render(raw_html, FINAL_DIR / "final_raw_presentation.mp4", "纯净版")
    finally:
        subtitle_html.unlink(missing_ok=True)
        raw_html.unlink(missing_ok=True)

    print(f"视频压制完毕: {FINAL_DIR}", flush=True)


if __name__ == "__main__":
    main()
