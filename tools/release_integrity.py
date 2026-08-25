#!/usr/bin/env python3
"""Generate or verify the portable release content fingerprint.

The launcher uses the same fixed file list.  A release is considered current only
when both its release_order and this fingerprint match the public update channel.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


INTEGRITY_FILES = (
    "OCV_Launcher.exe",
    "start_windows.bat",
    "frontend/src/App.vue",
    "frontend/src/api.js",
    "frontend/src/style.css",
    "backend/app/main.py",
    "story_agents.py",
    "module1_agent_director.py",
    "module2_5_text_corrector.py",
    "module2_scene_director.py",
    "module4_video_render.py",
    "module5_video_render.py",
    "launcher/safe_update_helper.ps1",
    "launcher/update-sources.json",
)


def fingerprint(root: Path) -> tuple[str, list[str]]:
    lines: list[str] = []
    missing: list[str] = []
    for relative in INTEGRITY_FILES:
        path = root / Path(relative)
        if path.is_file():
            file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        else:
            file_hash = "MISSING"
            missing.append(relative)
        lines.append(f"{relative}|{file_hash}\n")
    digest = hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()
    return digest, missing


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--write-channel", action="store_true")
    args = parser.parse_args()

    root = args.root.resolve()
    digest, missing = fingerprint(root)
    if missing:
        print("Missing release files:")
        for relative in missing:
            print(f"- {relative}")
        return 2

    channel_path = root / "launcher" / "update-channel.json"
    if not channel_path.is_file():
        print(f"Missing update channel: {channel_path}")
        return 2
    channel = json.loads(channel_path.read_text(encoding="utf-8-sig"))

    if args.write_channel:
        channel["content_fingerprint"] = digest
        channel_path.write_text(
            json.dumps(channel, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"Updated {channel_path}")
        print(digest)
        return 0

    expected = str(channel.get("content_fingerprint") or "")
    print(f"actual={digest}")
    print(f"expected={expected or '(missing)'}")
    if not expected:
        return 3
    return 0 if digest.lower() == expected.lower() else 1


if __name__ == "__main__":
    raise SystemExit(main())
