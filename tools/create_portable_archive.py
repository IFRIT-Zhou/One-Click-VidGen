"""Build a ZIP64 portable package while excluding private runtime artifacts."""

from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path


ROOT_EXCLUDES = {
    ".agents",
    ".git",
    "Archives",
    "Sound Material",
    "indexTTS2_独立整合包",
    "output",
    "runtime_logs",
    "tts_voices",
    "workspace",
    "测试文案",
    ".release_staging",
    "dev",
    "saved_parameters",
    "saved_agent_prompts",
    "TTS_Output",
}


def is_excluded(relative: Path, *, without_local_tts: bool = False, without_local_video: bool = False, without_models: bool = False) -> bool:
    parts = relative.parts
    if parts and (parts[0].startswith('local_integration_bundle_') or parts[0] == 'optional_packages'):
        return True
    if without_local_video and parts[:1] == ('comfyui',):
        return relative.as_posix() != 'comfyui/README.md'
    if without_local_tts and parts[:1] == ('tts',):
        return relative.as_posix() != 'tts/README.md'
    if parts[:1] == ('comfyui_plugins',):
        return relative.as_posix() not in {'comfyui_plugins/.gitignore', 'comfyui_plugins/README.md'}
    if parts[:2] == ('plugins', 'codex_bridge'):
        public = {'plugin.json', 'ocv_bridge.py', 'README.md',
                  'skills/ocv-production-bridge/SKILL.md',
                  'skills/ocv-production-bridge/agents/openai.yaml'}
        return Path(*parts[2:]).as_posix() not in public
    if without_models and (parts[:1] == ("models",) or parts[:3] in {("tools", "IndexTTS25", "checkpoints"), ("runtime", "comfyui", "models")}):
        return True
    if without_local_tts and parts[:2] == ("models", "tts"):
        return True
    if without_local_video and parts[:2] == ("models", "comfyui"):
        return True
    if len(parts) >= 2 and parts[0] == 'plugins' and parts[1].startswith('private_'):
        return True
    if without_local_video and parts[:2] == ("runtime", "comfyui"):
        return True
    if without_local_tts and parts[:2] == ("tools", "IndexTTS25"):
        return True
    if not parts or parts[0] in ROOT_EXCLUDES or ".git" in parts or "__pycache__" in parts:
        return True
    if relative.name == ".env" or relative.suffix.lower() in {".pyc", ".pyo", ".log"}:
        return True
    if len(parts) == 1 and relative.suffix.lower() == ".txt":
        return True
    if parts[:2] in {
        ("frontend", "dist"),
        ("launcher", "bin"),
        ("runtime", "cache"),
        ("runtime", "data"),
        ("runtime", "npm-cache"),
        ("runtime", "temp"),
    }:
        return True
    if parts[:2] == ("launcher", "ui-preview.png"):
        return True
    if parts[:2] == ('tts', 'IndexTTS25') and len(parts) > 2 and parts[2] in {'checkpoints', 'outputs', 'archive'}:
        return True
    return parts[:3] in {
        ("tools", "IndexTTS25", "outputs"),
        ("tools", "IndexTTS25", "archive"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--prefix", required=True, help="Top-level folder name inside the ZIP")
    parser.add_argument(
        "--without-local-tts",
        action="store_true",
        help="Omit IndexTTS-2.5 checkpoints while retaining PyTorch and all other runtimes.",
    )
    parser.add_argument("--without-local-video", action="store_true", help="Omit the optional managed ComfyUI runtime and weights.")
    parser.add_argument("--without-models", action="store_true", help="Omit optional TTS and ComfyUI model weights, retaining both engine environments.")
    args = parser.parse_args()

    source = args.source.resolve()
    archive = args.archive.resolve()
    if not source.is_dir():
        raise SystemExit(f"Source directory does not exist: {source}")
    if archive.exists():
        raise SystemExit(f"Archive already exists: {archive}")

    # A local development snapshot may include unaudited third-party packages.
    # Never accidentally publish it merely because it lives under runtime/.
    for components in (source / "comfyui" / "engine" / "releases", source / "runtime" / "comfyui" / "releases"):
      if components.is_dir() and not args.without_local_video:
        for release in components.iterdir():
            if not release.is_dir():
                continue
            manifest_path = release / "component.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
            if not manifest.get("redistribution_audited"):
                raise SystemExit("Managed video component is not approved for redistribution: " + release.name + "; use --without-local-video for a base package.")

    files = [
        path
        for path in source.rglob("*")
        if path.is_file() and not is_excluded(
            path.relative_to(source), without_local_tts=args.without_local_tts, without_local_video=args.without_local_video, without_models=args.without_models
        )
    ]
    print(f"[zip] Preparing {len(files)} files", flush=True)
    with zipfile.ZipFile(
        archive,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=1,
        allowZip64=True,
    ) as package:
        for index, file_path in enumerate(files, start=1):
            relative = file_path.relative_to(source)
            package.write(file_path, Path(args.prefix) / relative)
            if index % 500 == 0 or index == len(files):
                print(f"[zip] {index}/{len(files)}", flush=True)
    print(f"[zip] Complete: {archive}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
