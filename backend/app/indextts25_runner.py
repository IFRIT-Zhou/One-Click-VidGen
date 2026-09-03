"""Isolated command-line batch runner for official IndexTTS-2.5.

The OCV portable Python remains unchanged.  This runner prepends the official
2.5 source and its small dependency overlay only inside the child process, so
IndexTTS2 2.0 imports used by the stable engine cannot be overwritten.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import wave
from pathlib import Path

try:  # package import in tests; direct import when launched as an isolated script
    from .tts_text_normalization import normalize_tts_text
except ImportError:  # pragma: no cover - exercised by the real child process
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from tts_text_normalization import normalize_tts_text


def _force_utf8_stdio() -> None:
    """Keep third-party diagnostic prints independent of the Windows code page."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="backslashreplace")


def _bootstrap() -> tuple[Path, Path]:
    root = Path(os.environ["INDEXTTS25_ROOT"]).resolve()
    packages = Path(os.environ["INDEXTTS25_PACKAGES_DIR"]).resolve()
    sys.path.insert(0, str(packages))
    sys.path.insert(0, str(root))
    return root, Path(os.environ["INDEXTTS25_MODEL_DIR"]).resolve()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OCV IndexTTS-2.5 batch runner")
    parser.add_argument("--batch-file", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--output-prefix", required=True)
    parser.add_argument("--voice", required=True)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--lang", default="ZH", choices=("ZH", "EN", "JA", "ES", "AR"))
    parser.add_argument("--duration-factor", type=float, default=1.0)
    parser.add_argument("--emotion-vector", default="")
    parser.add_argument("--emotion-weight", type=float, default=0.65)
    parser.add_argument("--bf16", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--accel", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--torch-compile", action=argparse.BooleanOptionalAction, default=False)
    return parser.parse_args()


def main() -> int:
    _force_utf8_stdio()
    _, model_dir = _bootstrap()
    args = _parse_args()
    from indextts.infer_v2_5 import IndexTTS2

    tasks: list[str] = []
    for line_number, raw in enumerate(Path(args.batch_file).read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        payload = json.loads(raw)
        text = normalize_tts_text(str(payload.get("text") or ""))
        if not text:
            raise ValueError(f"batch file line {line_number} text is empty")
        tasks.append(text)
    if not tasks:
        raise ValueError("batch file contains no synthesis tasks")

    model_load_started = time.perf_counter()
    tts = IndexTTS2(
        cfg_path=str(model_dir / "config.yaml"),
        model_dir=str(model_dir),
        use_bf16=bool(args.bf16),
        device=args.device,
        use_accel=bool(args.accel),
        use_torch_compile=bool(args.torch_compile),
        use_qwen_emo=False,
    )
    model_load_seconds = time.perf_counter() - model_load_started
    print(f"[TTS25_METRIC] model_load_seconds={model_load_seconds:.3f}", flush=True)
    try:
        import torch

        if str(args.device).startswith("cuda") and torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats(args.device)
        else:
            torch = None
    except Exception:
        torch = None
    emotion_vector = None
    if args.emotion_vector.strip():
        emotion_vector = [float(item.strip()) for item in args.emotion_vector.split(",")]
        if len(emotion_vector) != 8:
            raise ValueError("emotion vector must contain exactly 8 numbers")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    duration_factor = min(2.0, max(0.5, float(args.duration_factor)))
    for index, text in enumerate(tasks, 1):
        output_path = output_dir / f"{args.output_prefix}-{index:04d}.wav"
        kwargs = {
            "spk_audio_prompt": str(Path(args.voice).resolve()),
            "text": text,
            "output_path": str(output_path),
            "lang": args.lang,
            "verbose": False,
            "duration_factor": duration_factor,
        }
        if emotion_vector is not None:
            kwargs.update(emo_vector=emotion_vector, emo_alpha=min(1.0, max(0.0, args.emotion_weight)))
        inference_started = time.perf_counter()
        result = tts.infer(**kwargs)
        inference_seconds = time.perf_counter() - inference_started
        if not output_path.is_file():
            raise RuntimeError(f"IndexTTS-2.5 did not create output: {output_path} ({result!r})")
        audio_seconds = 0.0
        try:
            with wave.open(str(output_path), "rb") as audio:
                audio_seconds = audio.getnframes() / max(1, audio.getframerate())
        except (OSError, EOFError, wave.Error):
            pass
        peak_vram_gib = 0.0
        if torch is not None:
            try:
                peak_vram_gib = torch.cuda.max_memory_allocated(args.device) / (1024 ** 3)
            except Exception:
                pass
        real_time_factor = inference_seconds / audio_seconds if audio_seconds > 0 else 0.0
        print(
            "[TTS25_METRIC] "
            f"task={index}/{len(tasks)} characters={len(text)} "
            f"inference_seconds={inference_seconds:.3f} audio_seconds={audio_seconds:.3f} "
            f"rtf={real_time_factor:.4f} peak_vram_gib={peak_vram_gib:.3f}",
            flush=True,
        )
        print(f"Generated: {output_path}", flush=True)
    print(f"Batch complete: {len(tasks)} tasks generated", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
