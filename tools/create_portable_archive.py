"""Build a ZIP64 portable package while excluding private runtime artifacts."""

from __future__ import annotations

import argparse
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
}


def is_excluded(relative: Path) -> bool:
    parts = relative.parts
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
    return parts[:3] in {
        ("tools", "IndexTTS25", "outputs"),
        ("tools", "IndexTTS25", "archive"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--prefix", required=True, help="Top-level folder name inside the ZIP")
    args = parser.parse_args()

    source = args.source.resolve()
    archive = args.archive.resolve()
    if not source.is_dir():
        raise SystemExit(f"Source directory does not exist: {source}")
    if archive.exists():
        raise SystemExit(f"Archive already exists: {archive}")

    files = [
        path
        for path in source.rglob("*")
        if path.is_file() and not is_excluded(path.relative_to(source))
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
