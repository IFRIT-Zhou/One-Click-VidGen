"""Prepare a relocatable, selectable models folder from installed local weights.

Default only writes an installation manifest. --copy copies model files without
modifying their source; no downloads and no model execution are performed.
"""
import argparse
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.model_library import installation
from backend.app.indextts25_local import load_indextts25_config


def prepare(destination, copy=False):
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    video = installation()
    tts = installation("tts")
    manifest = {"schema": 1, "packs": {}, "files": []}
    for profile in video["profiles"]:
        pack = installation(profile_id=profile["id"])
        paths = ["comfyui/" + x["path"] for x in pack["items"]]
        manifest["packs"][profile["id"]] = {"name": profile["name"], "paths": paths,
                                                   "bytes": sum(x["bytes"] for x in pack["items"])}
    for item in video["items"]:
        relative = "comfyui/" + item["path"]
        target = destination / relative
        if copy and not item["ready"]:
            raise RuntimeError("源模型缺失或不完整：" + item["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        if copy:
            source = Path(item["found_path"])
            if source.resolve() != target.resolve():
                if target.exists():
                    raise RuntimeError("目标已存在，不覆盖：" + str(target))
                temporary = target.with_suffix(target.suffix + ".partial")
                shutil.copy2(source, temporary)
                temporary.replace(target)
        manifest["files"].append({key: value for key, value in {**item, "path": relative}.items()
                                  if key in {"path", "bytes", "download_url", "sha256", "used_by"}})
    tts_root = load_indextts25_config().model_dir.resolve()
    tts_paths, total = [], 0
    if copy and not tts["ready"]:
        raise RuntimeError("TTS 源模型未齐备，请先补齐")
    for source in sorted(tts_root.rglob("*")):
        relative = source.relative_to(tts_root)
        if not source.is_file() or any(p.startswith(".") or p in {"__pycache__", "locks"} for p in relative.parts):
            continue
        target = destination / "tts" / "indextts25" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if copy and source.resolve() != target.resolve():
            if target.exists():
                raise RuntimeError("目标已存在，不覆盖：" + str(target))
            temporary = target.with_suffix(target.suffix + ".partial")
            shutil.copy2(source, temporary)
            temporary.replace(target)
        path = target.relative_to(destination).as_posix()
        size = source.stat().st_size
        tts_paths.append(path)
        total += size
        manifest["files"].append({"path": path, "bytes": size})
    manifest["packs"]["indextts25"] = {"name": "IndexTTS-2.5 本地配音（含辅助模型）", "paths": tts_paths, "bytes": total}
    (destination / "model-packs.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.copy2(ROOT / "docs" / "MODEL_INSTALL_GUIDE.md", destination / "安装说明.md")
    print(json.dumps({"destination": str(destination), "copied": copy,
                      "packs": {k: {"name": v["name"], "bytes": v["bytes"], "files": len(v["paths"])} for k, v in manifest["packs"].items()}}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=ROOT / "models")
    parser.add_argument("--copy", action="store_true")
    args = parser.parse_args()
    prepare(args.destination, args.copy)
