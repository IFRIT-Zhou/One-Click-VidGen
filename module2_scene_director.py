# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Zhou Ruoyu and He Yun

from __future__ import annotations

import ctypes
import importlib.util
import io
import json
import os
import sys
import warnings
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent


warnings.filterwarnings(
    "ignore",
    message="The pynvml package is deprecated.*",
    category=FutureWarning,
    module=r"torch\.cuda",
)


def configure_cuda12_runtime() -> list[Path]:
    """Expose CTranslate2's CUDA 12 libraries without affecting the TTS process."""
    site_packages = (
        Path(sys.prefix)
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
    )
    library_dirs: list[Path] = [
        path
        for path in (
            site_packages / "nvidia" / "cublas" / "lib",
            site_packages / "nvidia" / "cudnn" / "lib",
        )
        if path.is_dir()
    ]

    # Some NVIDIA wheels expose namespace directories without importable
    # parent packages. Fall back to import discovery only when direct paths
    # were not found, and never let an optional parent import abort CPU ASR.
    for module_name in ("nvidia.cublas.lib", "nvidia.cudnn.lib"):
        try:
            spec = importlib.util.find_spec(module_name)
        except (ImportError, ModuleNotFoundError, AttributeError):
            spec = None
        if not spec:
            continue
        if spec.origin:
            candidates = [Path(spec.origin).parent]
        else:
            candidates = [Path(path) for path in (spec.submodule_search_locations or [])]
        library_dirs.extend(path for path in candidates if path.is_dir())

    library_dirs = list(dict.fromkeys(library_dirs))

    if not library_dirs:
        return []

    old_path = os.environ.get("LD_LIBRARY_PATH", "")
    os.environ["LD_LIBRARY_PATH"] = os.pathsep.join(
        [*(str(path) for path in library_dirs), old_path]
    )
    mode = getattr(ctypes, "RTLD_GLOBAL", 0)
    for name in (
        "libcublas.so.12",
        "libcublasLt.so.12",
        "libcudnn.so.9",
        "libcudnn_ops.so.9",
        "libcudnn_cnn.so.9",
    ):
        target = next((path / name for path in library_dirs if (path / name).exists()), None)
        if target:
            try:
                ctypes.CDLL(str(target), mode=mode)
            except OSError as exc:
                print(f"CUDA 动态库预加载失败，将由 ASR 自动回退判断: {target.name}: {exc}")
    return library_dirs


def transcribe_audio(audio_path: Path) -> tuple[list[Any], Any, str]:
    model_size = os.getenv("ASR_MODEL", "base")
    language = os.getenv("ASR_LANGUAGE", "zh") or None
    requested_device = os.getenv("ASR_DEVICE", "auto").lower().strip()
    if requested_device not in {"auto", "cuda", "cpu"}:
        raise ValueError("ASR_DEVICE 只支持 auto、cuda 或 cpu")

    if requested_device != "cpu":
        library_dirs = configure_cuda12_runtime()
        if library_dirs:
            print(f"已加载 CTranslate2 CUDA 运行库: {', '.join(str(path) for path in library_dirs)}")

    try:
        from faster_whisper import WhisperModel
        from faster_whisper.utils import download_model
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "当前 Python 环境未安装 faster-whisper；请设置 ASR_PYTHON "
            "或执行 pip install -r requirements.txt"
        ) from exc

    # Keep ASR assets independent from either local TTS engine. Older portable
    # builds accidentally stored this cache below tools/IndexTTS2, so removing
    # the obsolete TTS engine also removed subtitle recognition resources.
    bundled_model = PROJECT_ROOT / "tools" / "whisper_models" / "faster-whisper-base"
    model_path = (
        bundled_model
        if model_size.strip().lower() == "base" and bundled_model.is_dir()
        else Path(model_size).expanduser()
    )
    if model_path.exists():
        model_source = str(model_path.resolve())
    else:
        try:
            model_source = download_model(model_size, local_files_only=True)
            print(f"使用本地 Whisper 模型缓存: {model_source}")
        except Exception:
            print(f"本地未缓存 Whisper [{model_size}]，正在下载模型...")
            try:
                model_source = download_model(model_size)
            except Exception as exc:
                raise RuntimeError(
                    f"Whisper [{model_size}] 模型不可用，请检查网络或预先下载模型"
                ) from exc

    def run(device: str, compute_type: str) -> tuple[list[Any], Any]:
        print(f"正在初始化 Whisper [{model_size}] 模型驱动 ({device.upper()} / {compute_type})...")
        model = WhisperModel(model_source, device=device, compute_type=compute_type)
        segments, info = model.transcribe(
            str(audio_path), beam_size=5, vad_filter=True, language=language
        )
        return list(segments), info

    if requested_device != "cpu":
        try:
            segments, info = run("cuda", "float16")
            return segments, info, "cuda"
        except (OSError, RuntimeError, ValueError) as exc:
            if requested_device == "cuda":
                raise
            print(f"GPU ASR 不可用，切换 CPU 容灾模式: {exc}")

    segments, info = run("cpu", "int8")
    return segments, info, "cpu"


def format_srt_time(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    whole_seconds = int(seconds % 60)
    milliseconds = int((seconds - int(seconds)) * 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{milliseconds:03d}"


def close_short_subtitle_gaps(
    scenes: list[dict[str, float | str]], *, max_gap_seconds: float = 0.35
) -> int:
    """Keep a subtitle visible across tiny VAD pauses between spoken chunks.

    Cloud TTS often has a short leading pause at an independently synthesized
    chunk boundary.  Those pauses are not meaningful scene breaks, and leaving
    them empty makes a transparent subtitle layer flash against the renderer's
    default white canvas.  Longer pauses stay untouched as intentional silence.
    """
    closed = 0
    for previous, current in zip(scenes, scenes[1:]):
        previous_end = float(previous.get("end") or 0.0)
        current_start = float(current.get("start") or 0.0)
        gap = current_start - previous_end
        if 0 < gap <= max_gap_seconds:
            previous["end"] = round(current_start, 3)
            closed += 1
    return closed


def build_srt_entries(scenes: list[dict[str, float | str]]) -> list[str]:
    return [
        f"{index}\n{format_srt_time(float(item['start']))} --> {format_srt_time(float(item['end']))}\n{str(item['text_content']).strip()}\n"
        for index, item in enumerate(scenes, 1)
        if str(item.get("text_content") or "").strip()
    ]


def run_asr_pipeline() -> None:
    if sys.stdout.encoding != "utf-8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    print("[ASR 算力接管] 正在拉起 Faster-Whisper 字幕引擎...")
    base_dir = Path(__file__).resolve().parent
    audio_path = base_dir / "workspace" / "2_audio_srt" / "final_output.wav"
    srt_output_path = base_dir / "workspace" / "2_audio_srt" / "final_short.srt"
    json_output_path = base_dir / "workspace" / "3_visual_template" / "scene_timeline.json"
    if not audio_path.exists():
        raise FileNotFoundError(f"找不到配音资产文件: {audio_path}")

    print("正在进行语音活动检测（VAD）与确定性断句识别...")
    segments, info, device = transcribe_audio(audio_path)
    print(
        f"音频分析完毕！设备: {device.upper()}，语种: {info.language} "
        f"(置信度: {info.language_probability:.2f})"
    )

    scene_segments: list[dict[str, float | str]] = []
    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue
        index = len(scene_segments) + 1
        start = round(float(segment.start), 3)
        end = round(float(segment.end), 3)
        scene_segments.append(
            {
                "id": f"segment_{index:03d}",
                "start": start,
                "end": end,
                "text_content": text,
            }
        )

    if not scene_segments:
        raise RuntimeError("ASR 未识别出有效字幕，请检查 final_output.wav")

    closed_gaps = close_short_subtitle_gaps(scene_segments)
    srt_entries = build_srt_entries(scene_segments)
    srt_output_path.parent.mkdir(parents=True, exist_ok=True)
    srt_output_path.write_text("\n".join(srt_entries), encoding="utf-8")
    json_output_path.parent.mkdir(parents=True, exist_ok=True)
    json_output_path.write_text(
        json.dumps(scene_segments, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if closed_gaps:
        print(f"[字幕连续性] 已填补 {closed_gaps} 处不超过 0.35 秒的微小语音空档")
    print(f"[资产] 高频短字幕已写入: {srt_output_path} (共 {len(srt_entries)} 条)")
    print(f"[资产] 原生分镜骨架已写入: {json_output_path}")


if __name__ == "__main__":
    run_asr_pipeline()
