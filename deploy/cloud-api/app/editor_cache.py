"""Disposable, owner-scoped scene cache. Every failure is a normal cache miss."""
from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import time
from contextlib import contextmanager
from uuid import uuid4

ENCODING_VERSION = "timeline-h264-720p30-aac160k-v1"
TTL_SECONDS = 86400
GLOBAL_BYTES = 1024 ** 3
USER_BYTES = 256 * 1024 ** 2
_HASH = re.compile(r"^[0-9a-f]{64}$")


def _file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def owner_key(user_id: str) -> str:
    return hashlib.sha256(user_id.encode("utf-8")).hexdigest()


def scene_key(directory: Path, scene: dict, orientation: str, formats: dict, hashes: dict) -> str:
    def asset(index):
        if index not in hashes:
            digest = hashlib.sha256()
            with (directory / f"asset_{index}").open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            hashes[index] = digest.hexdigest()
        return {"bytes": hashes[index], "format": formats[index]}

    visual_kind = "image" if "image_index" in scene else "video"
    payload = {"version": ENCODING_VERSION, "orientation": orientation, "kind": visual_kind,
               "visual": asset(scene[f"{visual_kind}_index"]), "audio": [asset(index) for index in scene["audio_indices"]],
               "duration": float(scene["duration"]), "pause": float(scene["pause"]),
               "audio_pauses": [float(value) for value in scene["audio_pauses"]]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class SceneCache:
    def __init__(self, root: Path):
        self.root = root

    @contextmanager
    def _lock(self):
        self.root.mkdir(mode=0o750, parents=True, exist_ok=True)
        with (self.root / "cache.lock").open("a+b") as lock:
            # Cache contention must never queue or block an otherwise valid export.
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield

    def _entry(self, owner: str, key: str) -> Path:
        if not _HASH.fullmatch(owner) or not _HASH.fullmatch(key):
            raise ValueError("Invalid cache key")
        path = self.root / owner / key
        if path.parent.is_symlink() or path.is_symlink():
            raise ValueError("Symlink cache entry")
        return path

    def _collect(self):
        entries = []
        now = time.time()
        for owner in self.root.iterdir():
            if not _HASH.fullmatch(owner.name) or not owner.is_dir() or owner.is_symlink():
                continue
            for entry in owner.iterdir():
                if entry.is_symlink() or not entry.is_dir():
                    continue
                if entry.name.startswith(".pending-"):
                    # Exclusive lock implies there is no active writer; these are
                    # incomplete directories abandoned by a crashed process.
                    shutil.rmtree(entry)
                    continue
                if not _HASH.fullmatch(entry.name):
                    continue
                try:
                    meta = json.loads((entry / "meta.json").read_text())
                    size = (entry / "scene.mp4").stat().st_size
                    if not all(type(meta[field]) in (int, float) and math.isfinite(meta[field]) and meta[field] > 0 for field in ("created_at", "expires_at")):
                        raise ValueError
                    if type(meta["size"]) is not int or not isinstance(meta["sha256"], str) or not _HASH.fullmatch(meta["sha256"]):
                        raise ValueError
                    if meta["owner"] != owner.name or meta["key"] != entry.name or meta["version"] != ENCODING_VERSION or meta["expires_at"] <= now or size != meta["size"] or size <= 0 or size > USER_BYTES:
                        raise ValueError
                    entries.append((entry, size, meta["created_at"]))
                except (OSError, ValueError, KeyError, TypeError):
                    shutil.rmtree(entry)
        return entries

    def sweep(self):
        try:
            with self._lock():
                entries = self._collect()
                self._budget(entries, None, 0)
        except (OSError, ValueError, TypeError, KeyError):
            pass

    def _budget(self, entries, owner, incoming):
        # Enforce all owners' limits as well as the total, evicting oldest first.
        entries.sort(key=lambda item: item[2])
        totals = {}
        for path, size, _ in entries:
            totals[path.parent.name] = totals.get(path.parent.name, 0) + size
        total = sum(totals.values())
        if owner:
            totals[owner] = totals.get(owner, 0) + incoming
            total += incoming
        for path, size, _ in entries:
            if totals[path.parent.name] > USER_BYTES or total > GLOBAL_BYTES:
                shutil.rmtree(path)
                totals[path.parent.name] -= size
                total -= size

    def restore(self, owner: str, key: str, destination: Path, validate) -> bool:
        copied = False
        try:
            with self._lock():
                entries = self._collect()
                self._budget(entries, None, 0)
                entry = self._entry(owner, key)
                if not entry.is_dir():
                    return False
                expected_hash = json.loads((entry / "meta.json").read_text())["sha256"]
                shutil.copyfile(entry / "scene.mp4", destination)
                copied = True
            # The private copy survives cache eviction by another worker. Verify it
            # outside the lock so other users can access or evict their own entries.
            if _file_hash(destination) != expected_hash:
                raise ValueError("Cache content checksum mismatch")
            validate(destination)
            return True
        except (OSError, ValueError, TypeError, KeyError):
            try:
                destination.unlink(missing_ok=True)
                if copied:
                    with self._lock():
                        entry = self._entry(owner, key)
                        if entry.is_dir():
                            shutil.rmtree(entry)
            except (OSError, ValueError):
                pass
            return False

    def store(self, owner: str, key: str, source: Path) -> bool:
        """Caller supplies a successfully encoded, duration-verified scene only."""
        pending = None
        try:
            size = source.stat().st_size
            if size <= 0 or size > min(USER_BYTES, GLOBAL_BYTES):
                return False
            with self._lock():
                entry = self._entry(owner, key)
                entries = self._collect()
                if entry.is_dir():
                    return True
                self._budget(entries, owner, size)
                entry.parent.mkdir(mode=0o750, exist_ok=True)
                pending = entry.parent / f".pending-{uuid4().hex}"
                pending.mkdir(mode=0o750)
                shutil.copyfile(source, pending / "scene.mp4")
                with (pending / "scene.mp4").open("rb") as output:
                    os.fsync(output.fileno())
                now = time.time()
                with (pending / "meta.json").open("w") as output:
                    json.dump({"owner": owner, "key": key, "size": size, "sha256": _file_hash(pending / "scene.mp4"), "created_at": now,
                               "expires_at": now + TTL_SECONDS, "version": ENCODING_VERSION}, output)
                    output.flush()
                    os.fsync(output.fileno())
                pending.replace(entry)
            return True
        except (OSError, ValueError, TypeError, KeyError):
            return False
        finally:
            # Remove only this writer's own unpublished directory; never another
            # worker's files. It is harmless if the next locked sweep got here first.
            if pending is not None and pending.exists():
                shutil.rmtree(pending, ignore_errors=True)
