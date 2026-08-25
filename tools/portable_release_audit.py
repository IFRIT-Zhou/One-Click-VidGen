#!/usr/bin/env python3
"""Audit a prepared OCV portable directory before the user compresses it."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from release_integrity import fingerprint


REQUIRED_FILES = (
    "OCV_Launcher.exe",
    "start_windows.bat",
    ".env.example",
    "runtime/python/python.exe",
    "runtime/node/node.exe",
    "tools/IndexTTS25/checkpoints/gpt.pth",
    "tools/IndexTTS25/checkpoints/codec.pth",
    "tools/IndexTTS25/checkpoints/s2mel.pth",
    "tools/IndexTTS25/examples/voice_05.wav",
    "launcher/update-channel.json",
    "launcher/update-sources.json",
    "launcher/safe_update_helper.ps1",
    "frontend/package.json",
    "frontend/node_modules/vite/bin/vite.js",
    "node_modules/hyperframes/package.json",
    "runtime/hyperframes/.cache/hyperframes/chrome/chrome-headless-shell/win64-131.0.6778.85/chrome-headless-shell-win64/chrome-headless-shell.exe",
)

FORBIDDEN_PATHS = (
    ".env",
    ".git",
    ".agents",
    ".codex_doc_review",
    "Archives",
    "output",
    "workspace",
    "TTS_Output",
    "runtime_logs",
    "tts_voices",
    "saved_parameters",
    "Sound Material",
    "测试文案",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    errors: list[str] = []
    notes: list[str] = []

    if not (root / "start_windows.bat").is_file():
        print(f"Not an OCV project root: {root}")
        return 2

    for relative in REQUIRED_FILES:
        if not (root / Path(relative)).is_file():
            errors.append(f"缺少必需文件：{relative}")

    for relative in FORBIDDEN_PATHS:
        if (root / Path(relative)).exists():
            errors.append(f"发布目录仍含用户或开发数据：{relative}")

    nested_git = [path for path in root.rglob(".git") if path.exists()]
    for path in nested_git:
        errors.append(f"发布目录仍含 Git 元数据：{path.relative_to(root)}")

    pycache = []
    for path in root.rglob("__pycache__"):
        if not path.is_dir():
            continue
        relative = path.relative_to(root).as_posix()
        if relative.startswith("runtime/python/"):
            continue
        if relative.startswith("tools/IndexTTS25/python_packages/"):
            continue
        pycache.append(path)
    if pycache:
        errors.append(f"发布目录仍含 {len(pycache)} 个 __pycache__ 目录")

    diagnostics = list(root.glob(".tmp_diagnostic_*"))
    if diagnostics:
        errors.append(f"发布目录仍含 {len(diagnostics)} 个诊断包解压目录")

    personal_presets = root / "saved_agent_prompts" / "1"
    if personal_presets.exists():
        errors.append("发布目录仍含个人 Agent 提示词：saved_agent_prompts/1")

    channel_path = root / "launcher" / "update-channel.json"
    if channel_path.is_file():
        try:
            channel = json.loads(channel_path.read_text(encoding="utf-8-sig"))
            expected = str(channel.get("content_fingerprint") or "")
            actual, missing = fingerprint(root)
            if missing:
                errors.append("关键文件缺失：" + ", ".join(missing))
            elif not expected:
                errors.append("更新通道缺少 content_fingerprint")
            elif actual.lower() != expected.lower():
                errors.append(f"关键文件指纹不匹配：expected={expected}, actual={actual}")
            notes.append(f"release_id={channel.get('release_id', '')}")
            notes.append(f"content_fingerprint={actual}")
        except Exception as exc:  # noqa: BLE001 - audit must report malformed metadata
            errors.append(f"无法读取更新通道：{exc}")

    print(f"OCV portable release audit: {root}")
    for note in notes:
        print(f"[INFO] {note}")
    if errors:
        for error in errors:
            print(f"[FAIL] {error}")
        print(f"RESULT: FAILED ({len(errors)} problems)")
        return 1
    print("RESULT: PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
