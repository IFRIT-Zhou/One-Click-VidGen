"""Offline, single-job BiRefNet worker. Only the returned mask is authoritative.

Launched as a script with an ML-capable Python, NOT imported into the OCV
server. No ComfyUI server, third-party node scripts, hub downloads, or model
directory writes. The caller owns the private per-job staging directory and
checks all pinned model artifacts (including the weights) before launching.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys


ARCHITECTURE = Path(__file__).resolve().parent / "vendor" / "birefnet"
SOURCE_DIGESTS = {
    "BiRefNet_config.py": "e7b8c2a74f6cea6a59553d517f71d47f2c1d90e670a13416af17c25fe2f3dc52",
    "birefnet.py": "a9566611aa07a6fbb68ddb6ac8e19e62c879a428fbfc2f44efa53d233f2e302f",
}
_ATTN = "attn = attn.view(B_ // nW, nW, self.num_heads, N, N) + mask"
_SUFFIX = ".unsqueeze(1).unsqueeze(0)"
_RAW = _ATTN + _SUFFIX
_FIXED = _ATTN + ".to(attn.dtype)" + _SUFFIX


def checked_source(path):
    # Executable architecture/config come only from this Launcher-managed
    # component, never from the user's (or a legacy Comfy node's) weights folder.
    # Verify canonical source *before* executing it; never import a neighbor .pyc.
    name = path.name
    if path.is_symlink() or path.stat().st_size > 128_000:
        raise ValueError(f"不安全的 RMBG 模型代码：{name}")
    source = path.read_text(encoding="utf-8")
    if hashlib.sha256(source.encode("utf-8")).hexdigest() != SOURCE_DIGESTS[name]:
        raise ValueError(f"RMBG 模型代码校验失败：{name}")
    if name == "birefnet.py":
        source = source.replace("from .BiRefNet_config", "from BiRefNet_config").replace(_RAW, _FIXED)
    return source


def load_model(directory, device_choice):
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1")
    import torch
    from safetensors.torch import load_file
    from torchvision import transforms
    from PIL import Image

    if device_choice == "cuda" and not torch.cuda.is_available():
        raise ValueError("指定的独立抠图运行时无法使用 CUDA")
    device = ("cuda" if torch.cuda.is_available() else "cpu") if device_choice == "auto" else device_choice
    if device == "cpu":
        torch.set_num_threads(min(4, torch.get_num_threads()))
    # The verified architecture module imports the verified config by name.
    modules = {}
    for filename in SOURCE_DIGESTS:
        path = ARCHITECTURE / filename
        source = checked_source(path)
        spec = importlib.util.spec_from_file_location(path.stem, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[path.stem] = modules[path.stem] = module
        exec(compile(source, str(path), "exec"), module.__dict__)
    config = modules["BiRefNet_config"].BiRefNetConfig(bb_pretrained=False)
    model = modules["birefnet"].BiRefNet(config=config)
    model.load_state_dict(load_file(str(directory / "BiRefNet-general.safetensors"), device="cpu"), strict=True)
    model.eval()
    dtype = torch.float16 if device == "cuda" else torch.float32
    torch.set_float32_matmul_precision("high")
    model.to(device=device, dtype=dtype)
    transform = transforms.Compose([
        transforms.Resize((1024, 1024), interpolation=transforms.InterpolationMode.BICUBIC),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
    ])

    def infer(image):
        with torch.inference_mode():
            tensor = transform(image).unsqueeze(0).to(device=device, dtype=dtype)
            mask = model(tensor)[-1].float().sigmoid().cpu()[0].squeeze()
            return transforms.ToPILImage()(mask).resize(image.size, Image.Resampling.BICUBIC)

    return device, infer


def manifest_items(manifest):
    root = manifest.resolve(strict=True).parent
    if manifest.stat().st_size > 128_000:
        raise ValueError("独立抠图清单过大")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list) or not 1 <= len(items) <= 8:
        raise ValueError("独立抠图清单应为 1 至 8 张图片")
    checked, outputs = [], set()
    for item in items:
        if not isinstance(item, dict) or set(item) != {"input", "output"}:
            raise ValueError("独立抠图清单项目无效")
        source, dest = (Path(item[key]) for key in ("input", "output"))
        if any(not p.is_absolute() or p.is_symlink() or p.suffix.lower() != ".png" for p in (source, dest)):
            raise ValueError("独立抠图仅接受任务目录中的 PNG 路径")
        source, dest = source.resolve(strict=True), dest.resolve()
        if (not source.is_relative_to(root) or not dest.is_relative_to(root) or not source.is_file()
                or dest in outputs or dest.exists() or not dest.parent.is_dir()):
            raise ValueError("抠图输入或输出路径无效（不会覆盖已有图片）")
        outputs.add(dest)
        checked.append((source, dest))
    return checked


def run(manifest, model_dir, device_choice="auto", loader=load_model):
    from PIL import Image

    items = manifest_items(manifest)
    device, infer = loader(model_dir, device_choice)
    for src, dest in items:
        with Image.open(src) as image:
            if (image.format != "PNG" or image.mode != "RGB" or image.n_frames != 1
                    or image.width * image.height > 40_000_000):
                raise ValueError("抠图输入必须为有限尺寸的单帧 RGB PNG")
            mask = infer(image)
            if mask.mode != "L" or mask.size != image.size:
                raise ValueError("独立抠图未返回与原图等大的灰度蒙版")
        with dest.open("xb") as output:
            mask.save(output, format="PNG")
    return {"status": "completed", "device": device, "count": len(items)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--device", choices=["auto", "cuda", "cpu"], default="auto")
    args = parser.parse_args()
    try:
        receipt = run(args.manifest, args.model_dir, args.device)
    except Exception as exc:
        receipt = {"status": "failed", "message": f"{type(exc).__name__}: {str(exc)[:1000]}"}
    print(json.dumps(receipt), flush=True)
    return 0 if receipt["status"] == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
