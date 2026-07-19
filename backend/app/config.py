"""Small dependency-free loader for ignored local project configuration."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_ROOT / ".env"
ENV_EXAMPLE_PATH = PROJECT_ROOT / ".env.example"


def _parse_env_lines(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def ensure_project_env_file() -> Path:
    """Create a local config from the public template when first needed."""
    if not ENV_PATH.is_file() and ENV_EXAMPLE_PATH.is_file():
        shutil.copy2(ENV_EXAMPLE_PATH, ENV_PATH)
    return ENV_PATH


def save_project_env_values(values: Mapping[str, str]) -> None:
    """Update selected .env keys while preserving comments and unrelated settings."""
    safe_values: dict[str, str] = {}
    for key, value in values.items():
        cleaned_key = str(key).strip()
        cleaned_value = str(value).strip()
        if not cleaned_key or "\n" in cleaned_value or "\r" in cleaned_value:
            raise ValueError("配置项包含非法换行")
        safe_values[cleaned_key] = cleaned_value
    if not safe_values:
        return

    env_path = ensure_project_env_file()
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.is_file() else []
    updated: set[str] = set()
    output: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in safe_values:
                output.append(f"{key}={safe_values[key]}")
                updated.add(key)
                continue
        output.append(line)
    for key, value in safe_values.items():
        if key not in updated:
            output.append(f"{key}={value}")
    env_path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    os.environ.update(safe_values)


def load_project_env() -> None:
    """Load unset variables from the local `.env` file when it exists."""
    for key, value in _parse_env_lines(ENV_PATH).items():
        if key not in os.environ:
            os.environ[key] = value
