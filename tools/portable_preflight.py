"""Repair small, machine-specific runtime state before the portable app starts."""

from __future__ import annotations

import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = PROJECT_ROOT / "tools" / "IndexTTS25" / "checkpoints"
WHISPER_MODEL_DIR = PROJECT_ROOT / "tools" / "whisper_models" / "faster-whisper-base"
HYPERFRAMES_BROWSER_DIR = PROJECT_ROOT / "runtime" / "hyperframes" / ".cache" / "hyperframes" / "chrome"
PORTABLE_ENV_KEYS = ("INDEXTTS25_ROOT", "INDEXTTS25_MODEL_DIR", "INDEXTTS25_PACKAGES_DIR")


def is_inside_project(path: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(PROJECT_ROOT)
        return True
    except ValueError:
        return False


def find_hyperframes_browser() -> Path | None:
    """Return the bundled Chrome Headless Shell, if this package contains it."""
    if not HYPERFRAMES_BROWSER_DIR.is_dir():
        return None
    candidates = sorted(HYPERFRAMES_BROWSER_DIR.rglob("chrome-headless-shell.exe"))
    return candidates[-1] if candidates else None


def validate_portable_env() -> list[str]:
    dotenv: dict[str, str] = {}
    dotenv_path = PROJECT_ROOT / ".env"
    if dotenv_path.is_file():
        for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            dotenv[key.strip()] = value.strip().strip('"').strip("'")
    problems: list[str] = []
    for key in PORTABLE_ENV_KEYS:
        value = (os.getenv(key) or dotenv.get(key) or "").strip()
        if not value:
            continue
        path = Path(value)
        if path.is_absolute() and not is_inside_project(path):
            problems.append(f"{key} points outside this package: {value}")
    return problems


def main() -> int:
    problems = validate_portable_env()
    required_25 = (
        MODEL_DIR / "config.yaml",
        MODEL_DIR / "gpt.pth",
        MODEL_DIR / "multilingual_zh_ja_yue_char_del.tiktoken",
        PROJECT_ROOT / "tools" / "IndexTTS25" / "python_packages" / "tiktoken",
    )
    missing_25 = [str(path.relative_to(PROJECT_ROOT)) for path in required_25 if not path.exists()]
    if missing_25:
        problems.append("IndexTTS-2.5 runtime is incomplete: " + ", ".join(missing_25))
    else:
        print(f"[portable] IndexTTS-2.5 model: {MODEL_DIR}")
    required_whisper = (
        WHISPER_MODEL_DIR / "config.json",
        WHISPER_MODEL_DIR / "model.bin",
        WHISPER_MODEL_DIR / "tokenizer.json",
        WHISPER_MODEL_DIR / "vocabulary.txt",
    )
    missing_whisper = [
        str(path.relative_to(PROJECT_ROOT)) for path in required_whisper if not path.is_file()
    ]
    if missing_whisper:
        problems.append("Faster-Whisper Base runtime is incomplete: " + ", ".join(missing_whisper))
    else:
        print(f"[portable] Faster-Whisper model: {WHISPER_MODEL_DIR}")
    browser = find_hyperframes_browser()
    if browser is None:
        problems.append(
            "Bundled Hyperframes Chrome Headless Shell is missing. "
            "Re-download the complete portable package."
        )
    else:
        print(f"[portable] Hyperframes browser: {browser}")
    if problems:
        print("[portable] Configuration warning:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
