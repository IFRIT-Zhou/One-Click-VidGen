"""Repair small, machine-specific runtime state before the portable app starts."""

from __future__ import annotations

import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = PROJECT_ROOT / "tools" / "IndexTTS2" / "checkpoints"
INDEXTTS_CONFIG = PROJECT_ROOT / "runtime" / "data" / "indextts2" / "appdata" / "IndexTTS" / "config.toml"
HYPERFRAMES_BROWSER_DIR = PROJECT_ROOT / "runtime" / "hyperframes" / ".cache" / "hyperframes" / "chrome"
PORTABLE_ENV_KEYS = ("INDEXTTS2_ROOT", "INDEXTTS2_MODEL_DIR")


def is_inside_project(path: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(PROJECT_ROOT)
        return True
    except ValueError:
        return False


def repair_indextts_config() -> None:
    """The official CLI persists this path and reads it before CLI arguments."""
    INDEXTTS_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    model_path = MODEL_DIR.resolve(strict=False).as_posix().replace('"', '\\"')
    INDEXTTS_CONFIG.write_text(f'model_dir = "{model_path}"\n', encoding="utf-8")
    print(f"[portable] IndexTTS2 runtime config: {INDEXTTS_CONFIG}")


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
    repair_indextts_config()
    problems = validate_portable_env()
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
