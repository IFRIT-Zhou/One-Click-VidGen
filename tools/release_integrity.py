#!/usr/bin/env python3
"""Generate or verify the portable release content fingerprint.

The launcher uses the same fixed file list.  A release is considered current only
when both its release_order and this fingerprint match the public update channel.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


INTEGRITY_FILES = (
    "OCV_Launcher.exe",
    "start_windows.bat",
    "frontend/src/App.vue",
    "frontend/src/api.js",
    "frontend/src/style.css",
    "frontend/src/Studio.vue",
    "frontend/src/studio.css",
    "frontend/src/useStudio.js",
    "frontend/src/useWorkspace.js",
    "frontend/src/videoPresentation.js",
    "frontend/src/components/ImageProfileSelector.vue",
    "frontend/src/components/ImageStudio.vue",
    "frontend/src/components/ComfyUIVideoSelector.vue",
    "frontend/src/components/ComfyUIWorkbench.vue",
    "frontend/src/components/DynamicTextModeSelector.vue",
    "frontend/src/components/ParameterReview.vue",
    "frontend/src/components/ReferenceMaterials.vue",
    "frontend/src/components/SceneAssets.vue",
    "frontend/src/components/SubtitleStyleEditor.vue",
    "frontend/src/components/VideoModelSettings.vue",
    "frontend/src/components/VideoShotNavigator.vue",
    "frontend/src/components/VideoStudio.vue",
    "frontend/src/dynamicTextMode.js",
    "frontend/src/videoPromptWarnings.js",
    "backend/app/comfyui_bridge.py",
    "backend/app/h3_prompt_agent.py",
    "backend/app/main.py",
    "backend/app/gemini_client.py",
    "backend/app/language_routing.py",
    "backend/app/image_profiles.py",
    "backend/app/indextts25_local.py",
    "backend/app/local_tts_component.py",
    "backend/app/image_studio.py",
    "backend/app/pipeline.py",
    "backend/app/reference_materials.py",
    "backend/app/subtitle_layout.py",
    "backend/app/subtitle_preview.cjs",
    "backend/app/subtitle_preview.py",
    "backend/app/tts_editor.py",
    "backend/app/tts_segmentation.py",
    "backend/app/tts_text_normalization.py",
    "backend/app/visual_editor.py",
    "backend/app/video_agents.py",
    "backend/app/video_boundary_review.py",
    "backend/app/video_director_contracts.py",
    "backend/app/video_director_examples.py",
    "backend/app/video_export.py",
    "backend/app/video_generation.py",
    "backend/app/video_group_repair.py",
    "backend/app/video_image_prompt.py",
    "backend/app/video_model_config.py",
    "backend/app/video_motion_plan.py",
    "backend/app/video_plan.py",
    "backend/app/video_prompt_notices.py",
    "backend/app/video_prompt_refresh.py",
    "backend/app/video_scene_references.py",
    "backend/app/video_sources.py",
    "backend/app/video_studio.py",
    "backend/app/video_text_policy.py",
    "director_prompt_editor.py",
    "scene_reference_coordinator.py",
    "story_agents.py",
    "module1_agent_director.py",
    "module2_5_text_corrector.py",
    "module2_scene_director.py",
    "module4_video_render.py",
    "module5_video_render.py",
    "module6_dynamic_video.py",
    "launcher/safe_update_helper.ps1",
    "launcher/update-sources.json",
    "tools/deploy_indextts25.ps1",
    "tools/portable_preflight.py",
)

TEXT_EXTENSIONS = {".bat", ".cjs", ".css", ".js", ".json", ".ps1", ".py", ".vue"}


def validate_launcher_integrity_files(root: Path) -> None:
    """Fail a release if the launcher and packaging tool hash different files."""
    source = (root / "launcher" / "src" / "LauncherRuntime.cs").read_text(encoding="utf-8-sig")
    match = re.search(r"ReleaseIntegrityFiles\s*=\s*\{(.*?)\};", source, re.DOTALL)
    if not match:
        raise ValueError("启动器关键文件清单未找到")
    launcher_files = tuple(re.findall(r'"([^"\r\n]+)"', match.group(1)))
    if launcher_files != INTEGRITY_FILES:
        missing = [path for path in INTEGRITY_FILES if path not in launcher_files]
        extra = [path for path in launcher_files if path not in INTEGRITY_FILES]
        raise ValueError(
            "启动器与发布工具的关键文件清单不一致（顺序也必须一致）："
            f"启动器缺少 {missing}；启动器多出 {extra}"
        )


def release_file_bytes(path: Path) -> bytes:
    """Make text fingerprints independent from Git/Windows line endings."""
    data = path.read_bytes()
    if path.suffix.lower() in TEXT_EXTENSIONS:
        if data.startswith(b"\xef\xbb\xbf"):
            data = data[3:]
        data = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return data


def fingerprint(root: Path) -> tuple[str, list[str]]:
    lines: list[str] = []
    missing: list[str] = []
    for relative in INTEGRITY_FILES:
        path = root / Path(relative)
        if path.is_file():
            file_hash = hashlib.sha256(release_file_bytes(path)).hexdigest()
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
    try:
        validate_launcher_integrity_files(root)
    except (OSError, ValueError) as exc:
        print(exc)
        return 2
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
