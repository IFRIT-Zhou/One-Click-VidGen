"""Snapshot a tested local engine into a separate, relocatable OCV component.

This developer tool does not modify the source installation or download models.
Public distribution requires a separate license and clean-machine audit.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def closure(graph, output):
    selected = set()

    def visit(key):
        if key in selected:
            return
        selected.add(key)
        for value in graph[key].get("inputs", {}).values():
            if isinstance(value, list) and len(value) == 2 and str(value[0]) in graph and isinstance(value[1], int):
                visit(str(value[0]))

    visit(output)
    return {key: value for key, value in graph.items() if key in selected}


def git_value(path, *args):
    return subprocess.check_output(["git", "-C", str(path), *args], text=True, encoding="utf-8").strip()


def inherit_component_permissions(destination):
    """Extracted sources must remain readable by the normal OCV Windows user.

    Enable parent ACL inheritance only on the newly created component subtree;
    never grant broad new rights or change the original installation.
    """
    if os.name == "nt":
        subprocess.run(["icacls", str(destination.resolve()), "/inheritance:e", "/T", "/C", "/Q"],
                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def copy_source(source, destination):
    if (source / ".git").exists():
        commit = git_value(source, "rev-parse", "HEAD")
        if git_value(source, "status", "--porcelain", "--untracked-files=no"):
            raise ValueError(f"节点有未提交的修改，不能建立可重现基线：{source.name}")
        destination.mkdir(parents=True)
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "source.tar"
            subprocess.run(["git", "-C", str(source), "archive", "HEAD", "--output", str(archive)], check=True)
            with tarfile.open(archive) as stream:
                for entry in stream.getmembers():
                    if entry.issym() or entry.islnk() or not (destination / entry.name).resolve().is_relative_to(destination.resolve()):
                        raise ValueError("源码归档包含链接或越界路径")
                stream.extractall(destination)
        inherit_component_permissions(destination)
        return {"commit": commit, "url": git_value(source, "remote", "get-url", "origin")}
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns("__pycache__", ".git", "*.pyc", "*.log", ".env", "models", "output", "input", "user", "node_modules"))
    inherit_component_permissions(destination)
    digest = hashlib.sha256()
    for path in sorted(destination.rglob("*")):
        if path.is_file():
            digest.update(path.relative_to(destination).as_posix().encode())
            digest.update(path.read_bytes())
    return {"tree_sha256": digest.hexdigest(), "url": "", "needs_source_audit": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--base-python", type=Path, required=True)
    parser.add_argument("--site-packages", type=Path, required=True)
    parser.add_argument("--profiles", type=Path, required=True)
    parser.add_argument("--profile-id", required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--nodes-destination", type=Path, help="Shared comfyui/custom_nodes directory")
    parser.add_argument("--version", required=True)
    parser.add_argument("--object-info-url", default="http://127.0.0.1:8188/object_info")
    args = parser.parse_args()
    destination = args.destination.resolve()
    nodes_destination = (args.nodes_destination.resolve() if args.nodes_destination else
                         destination.parent.parent.parent / "custom_nodes")
    if destination.exists():
        raise ValueError("目标已存在；请使用新的组件版本目录，不覆盖正在使用的版本。")
    source = args.source.resolve()
    profile = next(item for item in json.loads(args.profiles.read_text(encoding="utf-8")) if item["id"] == args.profile_id)
    graph = closure(profile["workflow"], profile["mappings"]["output_node_id"])
    with urllib.request.urlopen(args.object_info_url, timeout=30) as response:
        object_info = json.load(response)
    node_dirs = set()
    for node in graph.values():
        module = object_info[node["class_type"]].get("python_module", "")
        if module.startswith("custom_nodes."):
            node_dirs.add(module.split(".", 2)[1])
    destination.mkdir(parents=True)
    print("Copying pinned ComfyUI source", flush=True)
    core = copy_source(source, destination / "ComfyUI")
    nodes = []
    for name in sorted(node_dirs):
        print("Copying node: " + name, flush=True)
        node_source = source / "custom_nodes" / name
        entry = {"name": name, **copy_source(node_source, nodes_destination / name)}
        entry["license_files"] = [p.name for p in node_source.glob("*") if p.name.lower().startswith(("license", "copying"))]
        nodes.append(entry)
    # A full standalone interpreter, not a venv redirecting to the user's old path.
    print("Copying independent Python runtime and tested dependencies", flush=True)
    shutil.copytree(args.base_python, destination / "python", ignore=shutil.ignore_patterns("site-packages", "__pycache__", "*.pyc", "*.log"))
    shutil.copytree(args.site_packages, destination / "python" / "Lib" / "site-packages", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.log", "direct_url.json"))
    # Reject editable-install links back into the original installation.
    for pth in (destination / "python" / "Lib" / "site-packages").glob("*.pth"):
        for line in pth.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip() and not line.startswith(("#", "import ")) and Path(line.strip()).is_absolute():
                raise ValueError(f"不可移植的 Python 路径：{pth.name}")
            if "__editable__" in line:
                raise ValueError(f"不可移植的可编辑安装：{pth.name}")
    binding = profile["mappings"]
    graph[binding["prompt"]["node_id"]]["inputs"][binding["prompt"]["input_name"]] = ""
    graph[binding["image"]["node_id"]]["inputs"][binding["image"]["input_name"]] = "ocv-reference.png"
    loaders = {"UNETLoader": ("unet_name", "diffusion_models"), "CLIPLoader": ("clip_name", "text_encoders"),
               "VAELoader": ("vae_name", "vae"), "LoraLoaderModelOnly": ("lora_name", "loras")}
    models = []
    for node in graph.values():
        node.pop("_meta", None)
        if "filename_prefix" in node.get("inputs", {}):
            node["inputs"]["filename_prefix"] = "OCV/H3"
        loader = loaders.get(node["class_type"])
        if loader:
            field, folder = loader
            relative = folder + "/" + node["inputs"][field].replace("\\", "/")
            path = source / "models" / relative
            models.append({"path": relative, "bytes": path.stat().st_size})
    profile = {key: profile[key] for key in ("kind", "mappings", "resolution_preset", "default_width", "default_height", "default_seed")}
    profile.update(id="ocv-h3-managed-v1", name="OCV H3 · 高速采样", engine="managed", workflow=graph)
    dump(destination / "profile.json", profile)
    manifest = {"schema": 1, "version": args.version, "platform": "windows-nvidia", "core": core, "nodes": nodes,
                "models": models, "profile": "profile.json", "redistribution_audited": False}
    dump(destination / "component.json", manifest)
    python = destination / "python" / "python.exe"
    print("Checking independent interpreter", flush=True)
    subprocess.run([str(python), "-I", "-c", "import torch,sys,json; print(json.dumps({'python':sys.version,'prefix':sys.prefix,'torch':torch.__version__,'cuda':torch.version.cuda}))"], check=True)
    inventory = subprocess.check_output([str(python), "-I", "-c", "import importlib.metadata,json; print(json.dumps(sorted((d.metadata['Name'],d.version) for d in importlib.metadata.distributions())))"], text=True)
    dump(destination / "python-packages.json", json.loads(inventory))
    print("Component staged; activation and generation validation still required.", flush=True)


if __name__ == "__main__":
    main()
