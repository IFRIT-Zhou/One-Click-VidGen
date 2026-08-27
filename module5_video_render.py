# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Zhou Ruoyu and He Yun
# AGPL-3.0 Section 7 terms: ADDITIONAL_TERMS.md

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

from bgm_mixer import mix_bgm_into_videos, tracks_from_env


PROJECT_ROOT = Path(__file__).resolve().parent
IS_WINDOWS = os.name == "nt"


def configured_path(name: str, default: Path) -> Path:
    """Resolve an optional portable render path without changing normal defaults."""
    value = str(os.getenv(name) or "").strip()
    if not value:
        return default.resolve()
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


WORKSPACE_DIR = configured_path("OCV_RENDER_WORKSPACE_DIR", PROJECT_ROOT / "workspace")
VISUAL_DIR = WORKSPACE_DIR / "3_visual_template"
AUDIO_DIR = WORKSPACE_DIR / "2_audio_srt"
FINAL_DIR = WORKSPACE_DIR / "4_final_video"
PORTABLE_FFMPEG_DIR = PROJECT_ROOT / "tools" / "ffmpeg" / "bin"
PORTABLE_FFMPEG = PORTABLE_FFMPEG_DIR / "ffmpeg.exe"
PORTABLE_HYPERFRAMES_BROWSER_DIR = (
    PROJECT_ROOT / "runtime" / "hyperframes" / ".cache" / "hyperframes" / "chrome"
)
POSTER_MAPPING_PATH = VISUAL_DIR / "poster_mapping.json"
FINE_TIMELINE_PATH = VISUAL_DIR / "fine_grained_timeline.json"
ASSETS_DIR = VISUAL_DIR / "assets"


def env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def find_ffmpeg_binary() -> Path | None:
    """Resolve FFmpeg without changing the Windows portable-package priority."""
    configured = str(os.getenv("FFMPEG_BINARY") or "").strip()
    if configured:
        candidate = Path(configured).expanduser()
        if not candidate.is_absolute():
            candidate = PROJECT_ROOT / candidate
        if candidate.is_file():
            return candidate.resolve()

    portable_names = ("ffmpeg.exe", "ffmpeg") if IS_WINDOWS else ("ffmpeg",)
    for name in portable_names:
        candidate = PORTABLE_FFMPEG_DIR / name
        if candidate.is_file() and (IS_WINDOWS or os.access(candidate, os.X_OK)):
            return candidate.resolve()
    system_ffmpeg = shutil.which("ffmpeg")
    return Path(system_ffmpeg).resolve() if system_ffmpeg else None


def require_ffmpeg_binary() -> Path:
    binary = find_ffmpeg_binary()
    if binary is not None:
        return binary
    raise RuntimeError(
        "未找到 FFmpeg。Windows 整合包应包含 tools/ffmpeg/bin/ffmpeg.exe；"
        "Linux/macOS 请安装 ffmpeg 或设置 FFMPEG_BINARY。"
    )


def portable_subprocess_env(
    ffmpeg: Path,
    base_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Expose portable FFmpeg reliably to child processes on Windows.

    Windows environment names are case-insensitive, but a copied Python dict
    can contain an inherited ``Path`` and a newly-added ``PATH`` at the same
    time.  Node/Hyperframes may then observe the stale value and fail its
    ``where ffmpeg`` probe.  Keep exactly one PATH entry and also publish the
    resolved binary through common explicit environment names.
    """
    source = os.environ if base_env is None else base_env
    render_env = {str(key): str(value) for key, value in source.items()}
    path_value = next(
        (value for key, value in render_env.items() if key.lower() == "path"),
        "",
    )
    for key in [key for key in render_env if key.lower() == "path"]:
        render_env.pop(key, None)
    render_env["PATH"] = f"{ffmpeg.parent}{os.pathsep}{path_value}"
    render_env["FFMPEG_BINARY"] = str(ffmpeg)
    render_env["FFMPEG_PATH"] = str(ffmpeg)
    return render_env


@contextmanager
def writable_render_temp_directory() -> Iterator[Path]:
    """Create a verified writable staging directory for direct FFmpeg render.

    Some extracted portable packages live on drives whose inherited ACLs allow
    writing ordinary workspace files but reject newly-created temporary child
    directories.  Keep the portable workspace as the normal location, but
    verify it with a real write and fall back to the user's system temp when
    its inherited permissions are broken. Cleanup errors must never hide the
    actual render result.
    """
    configured = str(os.getenv("OCV_RENDER_TEMP_DIR") or "").strip()
    candidates: list[tuple[str, Path | None]] = []
    if configured:
        configured_path = Path(configured).expanduser()
        if not configured_path.is_absolute():
            configured_path = PROJECT_ROOT / configured_path
        candidates.append(("configured", configured_path.resolve(strict=False)))
    candidates.extend((
        ("workspace", WORKSPACE_DIR),
        ("system", None),
    ))

    errors: list[str] = []
    seen: set[str] = set()
    for label, directory in candidates:
        identity = str(directory.resolve(strict=False)) if directory is not None else "<system-temp>"
        if identity in seen:
            continue
        seen.add(identity)
        temp_dir: Path | None = None
        try:
            if directory is not None:
                directory.mkdir(parents=True, exist_ok=True)
            temp_dir = Path(tempfile.mkdtemp(prefix="ocv_ffmpeg_", dir=str(directory) if directory else None))
            probe = temp_dir / ".ocv_write_probe"
            probe.write_bytes(b"ok")
            probe.unlink()
        except OSError as exc:
            errors.append(f"{label}: {type(exc).__name__} {exc}")
            if temp_dir is not None:
                shutil.rmtree(temp_dir, ignore_errors=True)
            continue

        print(f"FFmpeg 临时工作区: {label}", flush=True)
        try:
            yield temp_dir
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
        return

    raise RuntimeError("无法创建可写的 FFmpeg 临时工作区：" + " | ".join(errors))


def find_portable_hyperframes_browser() -> Path | None:
    """Prefer the portable browser, then use an installed Chromium browser."""
    configured = str(os.getenv("HYPERFRAMES_BROWSER_PATH") or "").strip()
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_file():
            return candidate.resolve()

    names = ("chrome-headless-shell.exe",) if IS_WINDOWS else (
        "chrome-headless-shell", "chrome", "chromium",
    )
    if PORTABLE_HYPERFRAMES_BROWSER_DIR.is_dir():
        for name in names:
            candidates = sorted(PORTABLE_HYPERFRAMES_BROWSER_DIR.rglob(name))
            if candidates:
                return candidates[-1].resolve()

    executable_names = (
        ("chrome.exe", "msedge.exe", "chromium.exe")
        if IS_WINDOWS
        else ("google-chrome", "chromium", "chromium-browser", "chrome")
    )
    for name in executable_names:
        system_browser = shutil.which(name)
        if system_browser:
            return Path(system_browser).resolve()

    if IS_WINDOWS:
        installed_candidates: list[Path] = []
        local_app_data = str(os.getenv("LOCALAPPDATA") or "").strip()
        program_files = str(os.getenv("PROGRAMFILES") or "").strip()
        program_files_x86 = str(os.getenv("PROGRAMFILES(X86)") or "").strip()
        if local_app_data:
            installed_candidates.append(
                Path(local_app_data) / "Google" / "Chrome" / "Application" / "chrome.exe"
            )
        for root in (program_files, program_files_x86):
            if not root:
                continue
            installed_candidates.extend(
                (
                    Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe",
                    Path(root) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
                )
            )
        for candidate in installed_candidates:
            if candidate.is_file():
                return candidate.resolve()
    return None


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


def _subtitle_filter_path(path: Path) -> str:
    """Escape an absolute Windows path for FFmpeg's subtitles filter."""
    return path.resolve().as_posix().replace(":", r"\:").replace("'", r"\'")


def build_subtitle_burn_command(
    source: Path,
    srt_path: Path,
    output: Path,
    *,
    use_nvenc: bool,
    ffmpeg: Path | None = None,
) -> list[str]:
    """Build the much cheaper second pass used for the subtitle variant.

    The main story renderer uses the same visual language: 36px bold white
    subtitles, deep-blue card and bottom-centre placement.  Rendering the
    picture timeline once and burning this overlay afterwards avoids a second
    Chrome frame-capture pass when the user asks for both variants.
    """
    # libass scales SRT force-style values from its own script resolution. 12/10
    # corresponds visually to the HTML renderer's 36px type and ~30px bottom gap
    # at 1920x1080; using the CSS values verbatim makes text roughly 2.5x larger.
    force_style = (
        "FontName=Microsoft YaHei,FontSize=12,Bold=1,Alignment=2,MarginV=10,"
        "PrimaryColour=&H00FFFFFF,BackColour=&H2B341807,BorderStyle=3,Outline=3,Shadow=0"
    )
    subtitle_filter = (
        f"subtitles=filename='{_subtitle_filter_path(srt_path)}':charenc=UTF-8:"
        f"force_style='{force_style}'"
    )
    command = [
        str(ffmpeg or require_ffmpeg_binary()), "-y", "-i", str(source), "-vf", subtitle_filter,
        "-c:a", "copy", "-movflags", "+faststart",
    ]
    if use_nvenc:
        command.extend([
            "-c:v", "h264_nvenc", "-preset", "p4", "-cq", "19", "-b:v", "0",
            "-pix_fmt", "yuv420p",
        ])
    else:
        command.extend([
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-pix_fmt", "yuv420p",
        ])
    command.append(str(output))
    return command


def render_subtitle_variant_from_raw(source: Path, srt_path: Path, output: Path) -> None:
    """Create the subtitle edition from an already rendered clean video.

    NVENC is attempted first where enabled.  Software x264 remains an automatic
    fallback so portable packages continue to work on machines without a usable
    NVIDIA encoder.
    """
    ffmpeg = require_ffmpeg_binary()
    output.unlink(missing_ok=True)
    attempts = [env_flag("VIDEO_RENDER_GPU_ENCODING", True), False]
    errors: list[str] = []
    for use_nvenc in dict.fromkeys(attempts):
        label = "NVENC" if use_nvenc else "x264"
        print(f"开始: 字幕版（复用纯净版画面，FFmpeg {label}）", flush=True)
        process = subprocess.run(
            build_subtitle_burn_command(
                source, srt_path, output, use_nvenc=use_nvenc, ffmpeg=ffmpeg
            ),
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if process.returncode == 0 and output.is_file() and output.stat().st_size > 0:
            print(f"完成: 字幕版（FFmpeg {label}）", flush=True)
            return
        errors.append(f"{label} 退出码 {process.returncode}: {process.stdout[-1200:]}")
        output.unlink(missing_ok=True)
        if use_nvenc:
            print("字幕版 NVENC 不可用，自动回退 x264 编码。", flush=True)
    raise RuntimeError("字幕版 FFmpeg 合成失败：" + " | ".join(errors))


def load_direct_poster_timeline() -> tuple[list[dict[str, Any]], float]:
    """Resolve the editable poster mapping without depending on rendered HTML."""
    try:
        mapping = json.loads(POSTER_MAPPING_PATH.read_text(encoding="utf-8"))
        scenes = json.loads(FINE_TIMELINE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"FFmpeg 直出无法读取画面时间轴：{exc}") from exc
    if not isinstance(mapping, list) or not mapping or not isinstance(scenes, list) or not scenes:
        raise RuntimeError("FFmpeg 直出发现画面映射或字幕时间轴为空")
    scenes_by_id = {
        str(scene.get("slide_id") or ""): scene
        for scene in scenes
        if isinstance(scene, dict) and scene.get("slide_id")
    }
    total_duration = max(float(scene.get("end") or 0) for scene in scenes if isinstance(scene, dict))
    result: list[dict[str, Any]] = []
    for index, item in enumerate(mapping):
        if not isinstance(item, dict):
            raise RuntimeError("FFmpeg 直出发现无效画面映射")
        slide_ids = [str(value) for value in item.get("includes_slides", [])]
        included = [scenes_by_id[value] for value in slide_ids if value in scenes_by_id]
        if not included:
            raise RuntimeError(f"FFmpeg 直出找不到第 {index + 1} 张图对应的字幕范围")
        macro_id = str(item.get("macro_scene_id") or f"poster_{index + 1:03d}")
        configured_asset = str(item.get("asset_filename") or "").strip()
        candidates = [ASSETS_DIR / configured_asset] if configured_asset else []
        candidates.extend(sorted(ASSETS_DIR.glob(f"{macro_id}_*"), key=lambda path: path.stat().st_mtime_ns, reverse=True))
        asset = next((path for path in candidates if path.is_file()), None)
        if asset is None:
            raise RuntimeError(f"FFmpeg 直出找不到画面文件：{macro_id}")
        result.append({
            "macro_scene_id": macro_id,
            "path": asset.resolve(),
            "start": min(float(scene.get("start") or 0) for scene in included),
            "end": max(float(scene.get("end") or 0) for scene in included),
        })
    result.sort(key=lambda item: float(item["start"]))
    # The HTML renderer shows the first poster from t=0 even if the first spoken
    # subtitle starts a fraction later. Preserve that behavior exactly.
    result[0]["start"] = 0.0
    return result, total_duration


def build_direct_filter_script(
    timeline: list[dict[str, Any]],
    total_duration: float,
    *,
    fps: int,
    fade_duration: float,
) -> tuple[str, str]:
    """Build a 1920x1080 poster timeline equivalent to the current HTML CSS."""
    if not timeline or total_duration <= 0:
        raise ValueError("画面时间轴为空")
    lines: list[str] = []
    for index, item in enumerate(timeline):
        start = float(item["start"])
        next_start = float(timeline[index + 1]["start"]) if index + 1 < len(timeline) else total_duration
        duration = max(1 / fps, next_start - start + (fade_duration if index + 1 < len(timeline) else 0))
        lines.append(
            f"[{index}:v]"
            "scale=1836:918:force_original_aspect_ratio=decrease,"
            "pad=1836:918:(ow-iw)/2:(oh-ih)/2:color=0x050a12,"
            "pad=1920:1080:42:54:color=0x050a12,"
            f"fps={fps},format=yuv420p,settb=AVTB,trim=duration={duration:.6f},setpts=PTS-STARTPTS[v{index}]"
        )
    if len(timeline) == 1:
        return ";\n".join(lines), "v0"
    previous = "v0"
    for index in range(1, len(timeline)):
        output = f"x{index}"
        offset = max(0.0, float(timeline[index]["start"]))
        lines.append(
            f"[{previous}][v{index}]xfade=transition=fade:duration={fade_duration:.6f}:"
            f"offset={offset:.6f}[{output}]"
        )
        previous = output
    return ";\n".join(lines), previous


def _run_ffmpeg_with_progress(
    command: list[str],
    total_duration: float,
    phase_label: str,
    *,
    cwd: Path | None = None,
) -> tuple[int, str]:
    process = subprocess.Popen(
        command,
        cwd=cwd or PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert process.stdout is not None
    output_tail: list[str] = []
    last_reported = -10
    for raw_line in process.stdout:
        line = raw_line.strip()
        if not line:
            continue
        output_tail.append(line)
        output_tail = output_tail[-40:]
        if line.startswith("out_time_ms="):
            try:
                seconds = int(line.split("=", 1)[1]) / 1_000_000
            except ValueError:
                continue
            percent = max(0, min(99, round(seconds / max(total_duration, 0.01) * 100)))
            if percent >= last_reported + 10:
                last_reported = percent
                print(f"[{phase_label}] {percent}% Encoding video", flush=True)
    return process.wait(), "\n".join(output_tail)


def render_direct_raw_video(
    output: Path,
    *,
    timeline: list[dict[str, Any]] | None = None,
    total_duration: float | None = None,
) -> None:
    """Render posters, transitions and audio directly with FFmpeg."""
    ffmpeg = require_ffmpeg_binary()
    audio_path = AUDIO_DIR / "final_output.wav"
    if not audio_path.is_file():
        raise FileNotFoundError(f"找不到配音文件: {audio_path}")
    if timeline is None or total_duration is None:
        timeline, total_duration = load_direct_poster_timeline()
    fps = max(12, min(60, int(float(os.getenv("VIDEO_RENDER_FPS", "30")))))
    fade_duration = max(0.05, min(2.0, float(os.getenv("VIDEO_RENDER_CROSSFADE_SECONDS", "0.8"))))
    filter_script, output_label = build_direct_filter_script(
        timeline,
        total_duration,
        fps=fps,
        fade_duration=fade_duration,
    )
    output.unlink(missing_ok=True)
    attempts = [env_flag("VIDEO_RENDER_GPU_ENCODING", True), False]
    errors: list[str] = []
    with writable_render_temp_directory() as temp_dir:
        staged_inputs: list[Path] = []
        for index, item in enumerate(timeline):
            source = Path(item["path"])
            staged = temp_dir / f"i{index:03d}{source.suffix.lower()}"
            try:
                os.link(source, staged)
            except OSError:
                shutil.copy2(source, staged)
            staged_inputs.append(staged)
        staged_audio = temp_dir / "audio.wav"
        try:
            os.link(audio_path, staged_audio)
        except OSError:
            shutil.copy2(audio_path, staged_audio)
        script_path = temp_dir / "filter.txt"
        script_path.write_text(filter_script, encoding="utf-8")
        staged_output = temp_dir / "rendered.mp4"
        for use_nvenc in dict.fromkeys(attempts):
            label = "NVENC" if use_nvenc else "x264"
            command = [str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error"]
            for index, staged in enumerate(staged_inputs):
                start = float(timeline[index]["start"])
                next_start = float(timeline[index + 1]["start"]) if index + 1 < len(timeline) else total_duration
                duration = max(1 / fps, next_start - start + (fade_duration if index + 1 < len(timeline) else 0))
                # Keep every FFmpeg input relative to temp_dir.  On Windows the
                # complete CreateProcess command line is limited to 32,767
                # characters.  Repeating the absolute render workspace path for
                # hundreds of posters used to trigger WinError 206 on long videos.
                command.extend(["-loop", "1", "-framerate", str(fps), "-t", f"{duration:.6f}", "-i", staged.name])
            audio_index = len(staged_inputs)
            command.extend([
                "-i", staged_audio.name, "-filter_complex_script", script_path.name,
                "-map", f"[{output_label}]", "-map", f"{audio_index}:a:0",
                "-c:a", "aac", "-b:a", "192k", "-t", f"{total_duration:.6f}",
                "-movflags", "+faststart", "-progress", "pipe:1", "-nostats",
            ])
            if use_nvenc:
                command.extend([
                    "-c:v", "h264_nvenc", "-preset", "p4", "-cq", "19", "-b:v", "0",
                    "-pix_fmt", "yuv420p",
                ])
            else:
                command.extend([
                    "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                    "-pix_fmt", "yuv420p",
                ])
            staged_output.unlink(missing_ok=True)
            command.append(staged_output.name)
            print(f"开始: 纯净版（FFmpeg 直出，{label}）", flush=True)
            return_code, output_tail = _run_ffmpeg_with_progress(
                command,
                total_duration,
                "纯净版",
                cwd=temp_dir,
            )
            if return_code == 0 and staged_output.is_file() and staged_output.stat().st_size > 0:
                output.parent.mkdir(parents=True, exist_ok=True)
                output.unlink(missing_ok=True)
                shutil.move(str(staged_output), str(output))
                print(f"[纯净版] 100% Render complete", flush=True)
                print(f"完成: 纯净版（FFmpeg 直出，{label}）", flush=True)
                return
            errors.append(f"{label} 退出码 {return_code}: {output_tail[-1200:]}")
            output.unlink(missing_ok=True)
            if use_nvenc:
                print("FFmpeg 直出 NVENC 不可用，自动回退 x264 编码。", flush=True)
    raise RuntimeError("FFmpeg 直出失败：" + " | ".join(errors))


def render(composition: Path, output: Path, phase_label: str) -> None:
    node = shutil.which("node")
    cli = PROJECT_ROOT / "node_modules" / "hyperframes" / "dist" / "cli.js"
    if not node or not cli.exists():
        raise RuntimeError("未找到 Hyperframes CLI，请先安装 Node 依赖")
    ffmpeg = require_ffmpeg_binary()

    command = build_render_command(node, cli, composition, output)
    # Hyperframes launches ffmpeg by name. Keep the resolved portable binary
    # first in a case-normalized PATH so Node's `where ffmpeg` can always find it.
    render_env = portable_subprocess_env(ffmpeg)
    browser = find_portable_hyperframes_browser()
    if browser is None:
        raise RuntimeError(
            "未找到 Hyperframes 可用浏览器。Windows 整合包应包含 Chrome Headless Shell；"
            "源码部署请安装 Chrome、Edge 或 Chromium，或设置 HYPERFRAMES_BROWSER_PATH。"
        )
    # Do not trust a stale value inherited from an already-running backend.
    # Hyperframes otherwise falls back to C:\\Users\\<name>\\.cache and fails
    # on machines that never downloaded its browser cache.
    render_env["HYPERFRAMES_BROWSER_PATH"] = str(browser)
    print(f"Hyperframes 浏览器: {browser}", flush=True)
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
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
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
    if render_mode not in {"subtitles", "both"}:
        (FINAL_DIR / "final_with_subtitles.mp4").unlink(missing_ok=True)
    if render_mode not in {"raw", "both"}:
        (FINAL_DIR / "final_raw_presentation.mp4").unlink(missing_ok=True)
    try:
        render_engine = str(os.getenv("VIDEO_RENDER_ENGINE", "ffmpeg")).strip().lower()
        if render_engine not in {"ffmpeg", "hyperframes"}:
            render_engine = "ffmpeg"
        if render_engine == "ffmpeg":
            temporary_raw = FINAL_DIR / ".subtitle_source.mp4"
            raw_output = FINAL_DIR / "final_raw_presentation.mp4" if render_mode in {"raw", "both"} else temporary_raw
            try:
                render_direct_raw_video(raw_output)
            except Exception as exc:
                print(f"[WARN] FFmpeg 直出不可用，自动回退 Hyperframes：{exc}", flush=True)
                raw_output.unlink(missing_ok=True)
                render(raw_html, raw_output, "纯净版")
            if render_mode in {"subtitles", "both"}:
                try:
                    render_subtitle_variant_from_raw(
                        raw_output,
                        srt_path,
                        FINAL_DIR / "final_with_subtitles.mp4",
                    )
                except Exception as exc:
                    print(f"[WARN] FFmpeg 字幕压制不可用，自动回退 Hyperframes 字幕版：{exc}", flush=True)
                    render(subtitle_html, FINAL_DIR / "final_with_subtitles.mp4", "字幕版")
            temporary_raw.unlink(missing_ok=True)
        else:
            print("渲染引擎：兼容模式 Hyperframes", flush=True)
            if render_mode == "subtitles":
                render(subtitle_html, FINAL_DIR / "final_with_subtitles.mp4", "字幕版")
            if render_mode in {"raw", "both"}:
                render(raw_html, FINAL_DIR / "final_raw_presentation.mp4", "纯净版")
            if render_mode == "both":
                render_subtitle_variant_from_raw(
                    FINAL_DIR / "final_raw_presentation.mp4",
                    srt_path,
                    FINAL_DIR / "final_with_subtitles.mp4",
                )
    finally:
        subtitle_html.unlink(missing_ok=True)
        raw_html.unlink(missing_ok=True)

    bgm_tracks = tracks_from_env()
    if bgm_tracks:
        bgm_outputs = [
            path for path in (
                FINAL_DIR / "final_with_subtitles.mp4",
                FINAL_DIR / "final_raw_presentation.mp4",
            ) if path.is_file()
        ]
        print(f"[BGM] 开始按顺序添加 {len(bgm_tracks)} 首背景音乐", flush=True)
        mix_bgm_into_videos(
            bgm_outputs,
            bgm_tracks,
            fade_enabled=env_flag("BGM_FADE_ENABLED", False),
            fade_duration=float(os.getenv("BGM_FADE_DURATION", "1") or 1),
        )
    print(f"视频压制完毕: {FINAL_DIR}", flush=True)


if __name__ == "__main__":
    main()
