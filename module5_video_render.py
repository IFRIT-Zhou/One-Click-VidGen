from __future__ import annotations

import base64
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


def render(composition: Path, output: Path) -> None:
    node = shutil.which("node")
    cli = PROJECT_ROOT / "node_modules" / "hyperframes" / "dist" / "cli.js"
    if not node or not cli.exists():
        raise RuntimeError("未找到 Hyperframes CLI，请先安装 Node 依赖")

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
        "30",
        "--quality",
        "standard",
        "--workers",
        "1",
        "--no-browser-gpu",
    ]
    print(f"开始 Hyperframes 渲染: {composition.name}", flush=True)
    completed = subprocess.run(command, cwd=PROJECT_ROOT, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"Hyperframes 渲染失败，退出码 {completed.returncode}")
    if not output.exists() or output.stat().st_size == 0:
        raise RuntimeError(f"未生成视频文件: {output}")


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

    try:
        render(subtitle_html, FINAL_DIR / "final_with_subtitles.mp4")
        render(raw_html, FINAL_DIR / "final_raw_presentation.mp4")
    finally:
        subtitle_html.unlink(missing_ok=True)
        raw_html.unlink(missing_ok=True)

    print(f"视频压制完毕: {FINAL_DIR}", flush=True)


if __name__ == "__main__":
    main()
