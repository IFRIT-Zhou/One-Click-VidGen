"""Project-local adapter for the official IndexTTS2 repository and CLI."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .config import load_project_env


PROJECT_ROOT = Path(__file__).resolve().parents[2]
VOICE_IDS = tuple(f"voice_{index:02d}.wav" for index in (*range(1, 10), 11, 12))
EMOTIONS = (
    "happy",
    "angry",
    "sad",
    "afraid",
    "disgusted",
    "melancholic",
    "surprised",
    "calm",
)
EMOTION_VECTORS = {
    emotion: tuple(0.8 if position == index else 0.0 for position in range(8))
    for index, emotion in enumerate(EMOTIONS)
}
REQUIRED_MODEL_FILES = (
    "config.yaml",
    "bpe.model",
    "gpt.pth",
    "s2mel.pth",
    "wav2vec2bert_stats.pt",
    "feat1.pt",
    "feat2.pt",
    "hf_cache/semantic_codec_model.safetensors",
    "hf_cache/campplus_cn_common.bin",
    "hf_cache/bigvgan/config.json",
    "hf_cache/bigvgan/bigvgan_generator.pt",
)
REQUIRED_MODEL_DIRS = (
    "qwen0.6bemo4-merge",
    "hf_cache/w2v-bert-2.0",
)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _project_path(value: str, default: Path) -> Path:
    path = Path(value.strip()) if value.strip() else default
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve(strict=False)


@dataclass(frozen=True)
class IndexTTS2Config:
    root: Path
    model_dir: Path
    python: Path
    examples_dir: Path
    runtime_dir: Path
    default_voice: str
    device: str
    use_fp16: bool
    emotion_weight: float

    def available_voices(self) -> tuple[str, ...]:
        return tuple(voice for voice in VOICE_IDS if (self.examples_dir / voice).is_file())

    def missing_resources(self) -> list[str]:
        missing: list[str] = []
        for label, path in (
            ("官方 IndexTTS2 源码", self.root / "indextts"),
            ("便携 Python 3.10", self.python),
            (
                "便携 IndexTTS2 包",
                self.python.parent / "Lib" / "site-packages" / "indextts",
            ),
        ):
            if not path.exists():
                missing.append(label)
        for relative in REQUIRED_MODEL_FILES:
            if not (self.model_dir / relative).is_file():
                missing.append(f"模型文件 {relative}")
        for relative in REQUIRED_MODEL_DIRS:
            if not (self.model_dir / relative).is_dir():
                missing.append(f"模型目录 {relative}")
        if not self.available_voices():
            missing.append("官方示例参考音频")
        return missing

    @property
    def ready(self) -> bool:
        return not self.missing_resources()

    def runtime_environment(self) -> dict[str, str]:
        cache_dir = PROJECT_ROOT / "runtime" / "cache"
        temp_dir = PROJECT_ROOT / "runtime" / "temp"
        for path in (self.runtime_dir, cache_dir, temp_dir):
            path.mkdir(parents=True, exist_ok=True)
        torch_lib = self.python.parent / "Lib" / "site-packages" / "torch" / "lib"
        ffmpeg_bin = PROJECT_ROOT / "tools" / "ffmpeg" / "bin"
        return {
            "APPDATA": str(self.runtime_dir / "appdata"),
            "LOCALAPPDATA": str(self.runtime_dir / "localappdata"),
            "HF_HOME": str(self.model_dir / "hf_cache"),
            "HF_HUB_CACHE": str(self.model_dir / "hf_cache"),
            "TORCH_HOME": str(self.model_dir / "hf_cache"),
            "XDG_CACHE_HOME": str(cache_dir),
            "NUMBA_CACHE_DIR": str(cache_dir / "numba"),
            "MPLCONFIGDIR": str(cache_dir / "matplotlib"),
            "CUDA_CACHE_PATH": str(cache_dir / "cuda"),
            "TEMP": str(temp_dir),
            "TMP": str(temp_dir),
            "PATH": os.pathsep.join((str(torch_lib), str(ffmpeg_bin), os.environ.get("PATH", ""))),
            "INDEXTTS2_MODEL_DIR": str(self.model_dir),
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONNOUSERSITE": "1",
        }


def load_indextts2_config() -> IndexTTS2Config:
    load_project_env()
    root = _project_path(
        os.getenv("INDEXTTS2_ROOT", ""),
        PROJECT_ROOT / "tools" / "IndexTTS2",
    )
    model_dir = _project_path(
        os.getenv("INDEXTTS2_MODEL_DIR", ""),
        root / "checkpoints",
    )
    default_voice = os.getenv("INDEXTTS2_DEFAULT_VOICE", "voice_05.wav").strip()
    if default_voice not in VOICE_IDS:
        default_voice = "voice_05.wav"
    try:
        emotion_weight = min(1.0, max(0.0, float(os.getenv("INDEXTTS2_EMOTION_WEIGHT", "0.65"))))
    except ValueError:
        emotion_weight = 0.65
    return IndexTTS2Config(
        root=root,
        model_dir=model_dir,
        python=PROJECT_ROOT / "runtime" / "python" / "python.exe",
        examples_dir=root / "examples",
        runtime_dir=PROJECT_ROOT / "runtime" / "data" / "indextts2",
        default_voice=default_voice,
        device=os.getenv("INDEXTTS2_DEVICE", "cuda:0").strip() or "cuda:0",
        use_fp16=_env_bool("INDEXTTS2_FP16", True),
        emotion_weight=emotion_weight,
    )


def resolve_voice_reference(
    config: IndexTTS2Config,
    voice_id: str | None,
    *,
    user_id: int | None = None,
) -> Path:
    candidate = (voice_id or config.default_voice).strip()
    if candidate.startswith("upload:"):
        if user_id is None:
            raise ValueError("使用上传参考音频时缺少用户信息")
        filename = candidate.removeprefix("upload:").strip()
        if not filename or Path(filename).name != filename:
            raise ValueError("上传参考音频 ID 无效")
        root = (PROJECT_ROOT / "workspace" / "editor" / f"user_{user_id}" / "uploads").resolve()
        path = (root / filename).resolve()
        if root not in path.parents or path.suffix.lower() not in {".wav", ".mp3", ".flac"}:
            raise ValueError("上传参考音频只支持 WAV、MP3 或 FLAC")
        if not path.is_file():
            raise FileNotFoundError(f"找不到上传参考音频: {path}")
        return path
    if candidate not in VOICE_IDS:
        raise ValueError(f"不支持的官方参考音频: {candidate}")
    path = config.examples_dir / candidate
    if not path.is_file():
        raise FileNotFoundError(f"找不到官方参考音频: {path}")
    return path


def emotion_vector_text(emotion: str | None) -> str | None:
    if not emotion:
        return None
    vector = EMOTION_VECTORS.get(emotion.strip().lower())
    if vector is None:
        raise ValueError(f"不支持的 IndexTTS2 情绪: {emotion}")
    return ",".join(f"{value:g}" for value in vector)
