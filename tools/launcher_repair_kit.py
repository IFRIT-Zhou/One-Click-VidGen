"""Offline, reversible repair kit; run from the extracted kit under OCV root."""
import hashlib
import json
import re
import shutil
import socket
import sys
import time
import zipfile
from pathlib import Path

FILES = ('start_windows.bat', 'tools/launcher_service_probe.py', 'launcher/safe_update_helper.ps1')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stopped():
    for port in (8010, 5173, 8030):
        with socket.socket() as sock:
            sock.settimeout(0.5)
            if sock.connect_ex(('127.0.0.1', port)) == 0:
                raise RuntimeError(f'端口 {port} 仍在使用。请先正常关闭 OCV，不能在任务运行时安装或回退。')


def restore(root, backup):
    record = json.loads((backup / 'record.json').read_text('utf-8'))
    # Refuse restoring over a subsequent update or manually changed program.
    for name in FILES:
        current = root / name
        if current.exists() and sha(current) not in (record[name]['new'], record[name]['old']):
            raise RuntimeError(f'{name} 在补丁之后发生变化，停止回退以免覆盖新版本。')
    for name in FILES:
        if record[name]['old'] is None:
            (root / name).unlink(missing_ok=True)
        else:
            shutil.copy2(backup / name, root / name)


def install(root, kit):
    stopped()
    manifest = json.loads((kit / 'manifest.json').read_text('utf-8'))
    for name in FILES:
        if sha(kit / 'payload' / name) != manifest[name]:
            raise RuntimeError('补丁文件校验失败：' + name)
    backup = root / 'Archives/launcher_patch' / str(time.time_ns())
    backup.mkdir(parents=True)
    record = {}
    for name in FILES:
        old = root / name
        record[name] = dict(old=sha(old) if old.exists() else None, new=manifest[name])
        if old.exists():
            dest = backup / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(old, dest)
    (backup / 'record.json').write_text(json.dumps(record), 'utf-8')
    try:
        for name in FILES:
            dest = root / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(kit / 'payload' / name, dest)
            if sha(dest) != manifest[name]:
                raise RuntimeError('安装后校验失败：' + name)
    except Exception:
        # Only this fixed allowlist is restored. No user data is touched.
        for name in FILES:
            if record[name]['old'] is not None:
                shutil.copy2(backup / name, root / name)
            else:
                (root / name).unlink(missing_ok=True)
        raise
    (kit / 'last_backup.txt').write_text(str(backup), 'utf-8')
    print('安装完成。原文件备份：' + str(backup))
    print('请正常打开 Launcher。安全软件若仍拦截，需核实拦截记录；本补丁不会修改安全软件。')


def redact(text):
    text = re.sub(r'(?i)(bearer\s+)[\w.\-]+', r'\1[REDACTED]', text)
    text = re.sub(r'(?i)((?:api[_-]?key|token|password|secret|authorization)\s*[=:]\s*)[^\s,;]+', r'\1[REDACTED]', text)
    return text


def diagnose(root, kit):
    # No .env, projects, media, full configuration or task logs are collected.
    output = kit / ('launcher_diagnostics_' + time.strftime('%Y%m%d_%H%M%S') + '.zip')
    summary = {'files': {}, 'ports': {}}
    for name in FILES + ('OCV_Launcher.exe', 'runtime/python/python.exe', 'launcher/update-channel.json'):
        p = root / name
        summary['files'][name] = sha(p) if p.is_file() else 'MISSING'
    for port in (8010, 5173, 8030):
        with socket.socket() as sock:
            sock.settimeout(0.5)
            summary['ports'][port] = sock.connect_ex(('127.0.0.1', port)) == 0
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('summary.json', json.dumps(summary, indent=2))
        for name in ('launcher_update.log', 'launcher_update_result.txt', 'backend.stderr.log', 'frontend.stderr.log', 'service_watchdog.log'):
            path = root / 'runtime_logs' / name
            if path.is_file():
                with path.open('rb') as stream:
                    stream.seek(max(0, path.stat().st_size - 128000))
                    content = stream.read().decode('utf-8', errors='replace')
                archive.writestr(name, redact(content))
    print('诊断包：' + str(output))
    print('已做常见凭证脱敏，但日志可能仍含本地路径或业务文字，请检查后再发送。')


def main():
    kit = Path(__file__).resolve().parent
    root = kit.parent
    if not (root / 'start_windows.bat').is_file() or not (root / 'backend/app').is_dir():
        raise RuntimeError('请将整个 OCV_Launcher_Repair 文件夹放到 OCV_Launcher.exe 同级目录。')
    mode = sys.argv[1] if len(sys.argv) > 1 else ''
    if mode == 'install':
        install(root, kit)
    elif mode == 'diagnose':
        diagnose(root, kit)
    elif mode == 'restore':
        stopped()
        backup = Path((kit / 'last_backup.txt').read_text('utf-8')).resolve()
        backup.relative_to((root / 'Archives/launcher_patch').resolve())
        restore(root, backup)
        print('已回退本补丁。')
    else:
        raise RuntimeError('请选择 install / diagnose / restore')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('操作未完成：' + str(exc))
        sys.exit(1)

