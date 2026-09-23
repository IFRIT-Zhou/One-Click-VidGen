#!/usr/bin/env python3
"""Publish the committed Launcher release to GitHub and ModelScope.

This script intentionally does not stage or commit files.  It starts from the
current committed HEAD, creates one deterministic release archive, uploads the
archive before the public channel, and verifies both public mirrors afterwards.
The ModelScope write token is requested with getpass and is never added to a
command line.  SDK-generated credentials are isolated in a temporary directory
and deleted immediately after upload.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from release_integrity import fingerprint, validate_launcher_integrity_files


ROOT = Path(__file__).resolve().parents[1]
CHANNEL_PATH = ROOT / "launcher" / "update-channel.json"
DEFAULT_REPO_ID = "IFRIT95/One-Click-VidGen-Update-Mirror"
MODELSCOPE_CHANNEL_URL = (
    "https://modelscope.cn/models/IFRIT95/One-Click-VidGen-Update-Mirror/"
    "resolve/master/launcher/update-channel.json"
)
GITHUB_CHANNEL_URL = (
    "https://raw.githubusercontent.com/IFRIT-Zhou/One-Click-VidGen/"
    "main/launcher/update-channel.json"
)


def run(*args: str, capture: bool = False) -> str:
    completed = subprocess.run(
        list(args),
        cwd=ROOT,
        check=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE if capture else None,
    )
    return completed.stdout.strip() if capture else ""


def read_channel(path: Path = CHANNEL_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, destination: Path, *, attempts: int = 8) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "OCV-Release-Verifier/1.0"})
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                with destination.open("wb") as output:
                    shutil.copyfileobj(response, output)
            return
        except Exception as exc:  # CDN propagation and transient network errors
            last_error = exc
            if attempt == attempts:
                break
            time.sleep(min(5 * attempt, 30))
    raise RuntimeError(f"下载失败：{url}: {last_error}")


def fetch_json(url: str, *, attempts: int = 8) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="ocv-channel-") as directory:
        path = Path(directory) / "channel.json"
        download(url, path, attempts=attempts)
        return read_channel(path)


def assert_channel_equal(label: str, actual: dict[str, Any], expected: dict[str, Any]) -> None:
    fields = ("release_id", "release_order", "content_fingerprint")
    differences = [
        f"{field}: {actual.get(field)!r} != {expected.get(field)!r}"
        for field in fields
        if actual.get(field) != expected.get(field)
    ]
    if differences:
        raise RuntimeError(f"{label} 更新清单不一致：" + "; ".join(differences))


def wait_for_channel(label: str, url: str, expected: dict[str, Any], *, attempts: int = 12) -> None:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            assert_channel_equal(label, fetch_json(url, attempts=2), expected)
            return
        except Exception as exc:  # public mirrors may need a short propagation window
            last_error = exc
            if attempt == attempts:
                break
            print(f"[验证] {label} 清单尚未同步，5 秒后重试（{attempt}/{attempts}）……", flush=True)
            time.sleep(5)
    raise RuntimeError(str(last_error))


def verify_archive(archive: Path, expected_fingerprint: str) -> None:
    with tempfile.TemporaryDirectory(prefix="ocv-release-verify-") as directory:
        target = Path(directory)
        with zipfile.ZipFile(archive) as package:
            package.extractall(target)
        roots = [path for path in target.iterdir() if path.is_dir()]
        if len(roots) != 1:
            raise RuntimeError("更新包应只包含一个顶层目录")
        actual, missing = fingerprint(roots[0])
        if missing:
            raise RuntimeError("更新包缺少关键文件：" + ", ".join(missing))
        if actual.lower() != expected_fingerprint.lower():
            raise RuntimeError(f"更新包内容指纹不一致：{actual} != {expected_fingerprint}")


def validate_committed_release(channel: dict[str, Any]) -> str:
    validate_launcher_integrity_files(ROOT)
    dirty = run("git", "status", "--porcelain", "--untracked-files=no", capture=True)
    if dirty:
        raise RuntimeError("仍有未提交的已跟踪文件，禁止发布：\n" + dirty)
    branch = run("git", "branch", "--show-current", capture=True)
    if branch != str(channel.get("branch") or "main"):
        raise RuntimeError(f"当前分支为 {branch!r}，清单要求 {channel.get('branch')!r}")
    actual, missing = fingerprint(ROOT)
    if missing:
        raise RuntimeError("本地缺少关键文件：" + ", ".join(missing))
    expected = str(channel.get("content_fingerprint") or "")
    if not expected or actual.lower() != expected.lower():
        raise RuntimeError(f"本地内容指纹与清单不一致：{actual} != {expected or '(空)'}")
    return run("git", "rev-parse", "HEAD", capture=True)


def sync_github() -> None:
    print("[GitHub] 核对远端 main……", flush=True)
    run("git", "fetch", "origin")
    counts = run("git", "rev-list", "--left-right", "--count", "origin/main...HEAD", capture=True)
    remote_ahead, local_ahead = (int(value) for value in counts.split())
    if remote_ahead:
        raise RuntimeError("origin/main 包含本地没有的提交，请先合并，禁止覆盖远端")
    if local_ahead:
        print(f"[GitHub] 本地领先 {local_ahead} 个提交，正在推送……", flush=True)
        run("git", "push", "origin", "main")
    else:
        print("[GitHub] 已与本地 HEAD 一致。", flush=True)


def build_archive(head: str, channel: dict[str, Any], output: Path) -> str:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    prefix = "One-Click-VidGen-main/"
    print(f"[制包] 从提交 {head[:7]} 生成固定 ZIP……", flush=True)
    run("git", "archive", "--format=zip", f"--prefix={prefix}", "-o", str(output), head)
    verify_archive(output, str(channel["content_fingerprint"]))
    archive_hash = sha256(output)
    print(f"[制包] SHA-256={archive_hash}", flush=True)
    return archive_hash


def upload_modelscope(channel: dict[str, Any], archive: Path, repo_id: str) -> None:
    try:
        from modelscope.hub.api import HubApi, ModelScopeConfig
    except ImportError as exc:
        raise RuntimeError("当前 Python 缺少 modelscope；请使用 OCV 便携 Python 运行") from exc

    token = getpass.getpass("请粘贴魔搭写权限令牌（输入不会显示）：").strip()
    if not token:
        raise RuntimeError("未输入魔搭令牌")
    release_id = str(channel["release_id"])
    original_credential_path = ModelScopeConfig.path_credential
    try:
        with tempfile.TemporaryDirectory(prefix="ocv-modelscope-credentials-") as credential_dir:
            # HubApi.login persists a derived Git token and cookies by default.
            # Redirect that SDK state to an ephemeral directory so release
            # credentials are removed as soon as both uploads complete.
            ModelScopeConfig.path_credential = credential_dir
            api = HubApi()
            print("[魔搭 1/2] 上传更新包……", flush=True)
            api.upload_file(
                path_or_fileobj=archive,
                path_in_repo=f"releases/{release_id}/One-Click-VidGen-main.zip",
                repo_id=repo_id,
                repo_type="model",
                token=token,
                commit_message=f"release: upload OCV {release_id} archive",
            )
            print("[魔搭 2/2] 更新包完成，最后发布更新清单……", flush=True)
            api.upload_file(
                path_or_fileobj=CHANNEL_PATH,
                path_in_repo="launcher/update-channel.json",
                repo_id=repo_id,
                repo_type="model",
                token=token,
                commit_message=f"release: publish OCV {release_id} channel",
            )
    finally:
        ModelScopeConfig.path_credential = original_credential_path
        token = ""


def verify_public_release(channel: dict[str, Any], local_hash: str, release_dir: Path) -> None:
    print("[验证] 等待并读取 GitHub、魔搭公网清单……", flush=True)
    wait_for_channel("GitHub", GITHUB_CHANNEL_URL, channel)
    wait_for_channel("魔搭", MODELSCOPE_CHANNEL_URL, channel)
    model_url = next(
        (str(url) for url in channel.get("archive_urls", []) if "modelscope.cn" in str(url)),
        "",
    )
    if not model_url:
        raise RuntimeError("更新清单中缺少魔搭下载地址")
    downloaded = release_dir / "modelscope-public-verify.zip"
    if downloaded.exists():
        downloaded.unlink()
    print("[验证] 从魔搭匿名回下载更新包……", flush=True)
    download(model_url, downloaded)
    remote_hash = sha256(downloaded)
    if remote_hash.lower() != local_hash.lower():
        raise RuntimeError(f"魔搭下载包哈希不一致：{remote_hash} != {local_hash}")
    verify_archive(downloaded, str(channel["content_fingerprint"]))
    print("[验证] 公网清单、ZIP 哈希和解压内容指纹全部通过。", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-only", action="store_true", help="只制包和本地校验，不联网发布")
    parser.add_argument("--repo-id", default=DEFAULT_REPO_ID, help="魔搭模型仓库 ID")
    args = parser.parse_args()

    channel = read_channel()
    release_id = str(channel.get("release_id") or "").strip()
    if not release_id:
        raise RuntimeError("更新清单缺少 release_id")
    head = validate_committed_release(channel)
    release_dir = ROOT / "runtime" / "temp" / "release_publish" / release_id
    archive = release_dir / "One-Click-VidGen-main.zip"
    archive_hash = build_archive(head, channel, archive)
    if args.prepare_only:
        print(f"准备完成：{archive}")
        return 0

    sync_github()
    upload_modelscope(channel, archive, args.repo_id)
    verify_public_release(channel, archive_hash, release_dir)
    result = {
        "ok": True,
        "release_id": release_id,
        "release_order": channel.get("release_order"),
        "head": head,
        "content_fingerprint": channel.get("content_fingerprint"),
        "archive_sha256": archive_hash,
    }
    result_path = release_dir / "publish-result.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("\n发布完成：" + json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(f"命令执行失败（{exc.returncode}）：{' '.join(exc.cmd)}", file=sys.stderr)
        raise SystemExit(exc.returncode)
    except Exception as exc:
        print(f"发布失败：{exc}", file=sys.stderr)
        raise SystemExit(1)
