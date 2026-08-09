from __future__ import annotations

import argparse
import os
import subprocess
import time
from pathlib import Path


def _append_log(path: Path | None, message: str) -> None:
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
    except OSError:
        pass


def _terminate_process_tree(pid: int, log_path: Path | None) -> None:
    if pid <= 0:
        return
    if os.name == "nt":
        completed = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        _append_log(log_path, f"taskkill pid={pid} exit={completed.returncode}")
        return
    try:
        os.kill(pid, 15)
        _append_log(log_path, f"terminated pid={pid}")
    except ProcessLookupError:
        pass


def _has_live_peer_heartbeat(heartbeat: Path, stale_after: float) -> bool:
    """Return whether another launcher is still actively using shared services."""
    now = time.time()
    try:
        own_path = heartbeat.resolve(strict=False)
        candidates = heartbeat.parent.glob("launcher_*.heartbeat")
    except OSError:
        return False

    for candidate in candidates:
        try:
            if candidate.resolve(strict=False) == own_path:
                continue
            age = now - candidate.stat().st_mtime
        except OSError:
            continue
        if age <= stale_after:
            return True
    return False


def monitor(
    heartbeat: Path,
    pids: list[int],
    *,
    stale_after: float = 6.0,
    poll_interval: float = 0.5,
    log_path: Path | None = None,
) -> None:
    """Stop only this launcher's service PIDs after its heartbeat disappears."""
    last_mtime_ns: int | None = None
    last_change = time.monotonic()
    _append_log(log_path, f"watching heartbeat={heartbeat} pids={pids}")
    while True:
        try:
            mtime_ns = heartbeat.stat().st_mtime_ns
        except OSError:
            mtime_ns = None
        if mtime_ns is not None and mtime_ns != last_mtime_ns:
            last_mtime_ns = mtime_ns
            last_change = time.monotonic()
        if time.monotonic() - last_change >= stale_after:
            break
        time.sleep(poll_interval)

    if _has_live_peer_heartbeat(heartbeat, stale_after):
        _append_log(
            log_path,
            "launcher heartbeat stopped; another launcher is active, leaving shared services running",
        )
        try:
            heartbeat.unlink(missing_ok=True)
        except OSError:
            pass
        return

    _append_log(log_path, "launcher heartbeat stopped; no active launcher remains, cleaning shared services")
    for pid in pids:
        _terminate_process_tree(pid, log_path)
    try:
        heartbeat.unlink(missing_ok=True)
    except OSError:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="One-Click VidGen local service lifetime watchdog")
    parser.add_argument("--heartbeat", required=True, type=Path)
    parser.add_argument("--pid", action="append", type=int, default=[])
    parser.add_argument("--log", type=Path)
    parser.add_argument("--stale-after", type=float, default=6.0)
    args = parser.parse_args()
    pids = list(dict.fromkeys(pid for pid in args.pid if pid > 0))
    if not pids:
        return 2
    monitor(
        args.heartbeat,
        pids,
        stale_after=max(3.0, args.stale_after),
        log_path=args.log,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
