"""Isolated CPU transcription worker; executed by the dedicated ASR Python."""
from __future__ import annotations

import json
import math
import os
import signal
import subprocess
import sys
import traceback
import wave
from pathlib import Path


def write_state(directory: Path, state: dict) -> None:
    temporary = directory / "state.worker.tmp"
    temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    temporary.replace(directory / "state.json")


def transcribe(directory: Path, model_path: str, timeout: int = 900) -> None:
    state = json.loads((directory / "state.json").read_text(encoding="utf-8"))
    source, audio = directory / "source", directory / "audio.wav"
    def expired(_signum, _frame):
        raise TimeoutError("识别超过时间限制，请缩短音视频后重试。")
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(timeout)
    try:
        if hasattr(os, "nice"):
            os.nice(10)
        probe = subprocess.run([
            "ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe", "-f", state["demuxer"],
            "-show_entries", "format=duration", "-of", "json", str(source),
        ], check=True, capture_output=True, text=True, timeout=30)
        duration = float(json.loads(probe.stdout)["format"]["duration"])
        if not math.isfinite(duration) or not 0 < duration <= 600:
            raise ValueError("音视频时长须在 0–600 秒之间。")
        subprocess.run([
            "ffmpeg", "-nostdin", "-v", "error", "-y", "-threads", "1",
            "-protocol_whitelist", "file,pipe", "-f", state["demuxer"], "-i", str(source),
            "-map", "0:a:0", "-vn", "-t", "600", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(audio),
        ], check=True, capture_output=True, timeout=60)
        from faster_whisper import WhisperModel
        import numpy as np
        model = WhisperModel(model_path, device="cpu", compute_type="int8", cpu_threads=1, num_workers=1, local_files_only=True)
        with wave.open(str(audio), "rb") as stream:
            samples = np.frombuffer(stream.readframes(stream.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
        segments, info = model.transcribe(samples, language=state.get("language") or None, beam_size=3, vad_filter=True, condition_on_previous_text=False)
        cues = []
        for segment in segments:
            text = str(segment.text).strip()
            start, end = max(0.0, float(segment.start)), min(duration, float(segment.end))
            if text and math.isfinite(start) and math.isfinite(end) and end > start:
                cues.append({"start": round(start, 3), "end": round(end, 3), "text": text, "hidden": False})
        if not cues:
            raise ValueError("未识别到清晰语音，请检查音轨或使用更清晰的文件。")
        state.update(status="completed", cues=cues, duration=duration, detected_language=info.language, error=None)
    except (ValueError, TimeoutError) as exc:
        state.update(status="failed", error=str(exc), cues=[])
    except Exception:
        (directory / "error.log").write_text(traceback.format_exc(), encoding="utf-8")
        state.update(status="failed", error="语音识别失败，请检查音轨格式；管理员可检查独立 ASR 运行环境。", cues=[])
    finally:
        signal.alarm(0)
        source.unlink(missing_ok=True)
        audio.unlink(missing_ok=True)
        write_state(directory, state)


if __name__ == "__main__":
    transcribe(Path(sys.argv[1]).resolve(), sys.argv[2])
