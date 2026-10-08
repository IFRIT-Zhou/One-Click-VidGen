from __future__ import annotations

import hashlib
import os
import re
import tempfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from .config import settings


_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_KINDS = {"image", "audio", "subtitle", "intermediate", "final_video"}


@dataclass(frozen=True)
class StoredVideoAsset:
    object_key: str
    path: Path
    size_bytes: int
    sha256: str


class VideoStorageError(ValueError):
    pass


class LocalVideoStorage:
    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root or settings.video_storage_dir).expanduser().resolve()

    @staticmethod
    def _identifier(value: str, label: str) -> str:
        if not _IDENTIFIER.fullmatch(value):
            raise VideoStorageError(f"Invalid {label}")
        return value

    @staticmethod
    def _filename(value: str) -> str:
        filename = Path(value).name
        if filename != value or not filename or filename in {".", ".."}:
            raise VideoStorageError("Invalid asset filename")
        return filename

    def job_dir(self, *, user_id: str, job_id: str, create: bool = False) -> Path:
        user = self._identifier(user_id, "user id")
        job = self._identifier(job_id, "video job id")
        path = (self.root / user / job).resolve()
        if self.root not in path.parents:
            raise VideoStorageError("Video job path escapes storage root")
        if create:
            path.mkdir(mode=0o750, parents=True, exist_ok=True)
        return path

    def asset_path(
        self,
        *,
        user_id: str,
        job_id: str,
        kind: str,
        filename: str,
        create: bool = False,
    ) -> Path:
        if kind not in _KINDS:
            raise VideoStorageError("Invalid asset kind")
        directory = self.job_dir(user_id=user_id, job_id=job_id, create=create) / kind
        if create:
            directory.mkdir(mode=0o750, exist_ok=True)
        path = (directory / self._filename(filename)).resolve()
        if directory.resolve() not in path.parents:
            raise VideoStorageError("Asset path escapes job directory")
        return path

    def write_chunks(
        self,
        *,
        user_id: str,
        job_id: str,
        kind: str,
        filename: str,
        chunks: Iterable[bytes],
        max_bytes: int | None = None,
    ) -> StoredVideoAsset:
        limit = max_bytes or (
            settings.video_result_max_bytes if kind == "final_video" else settings.video_asset_max_bytes
        )
        if limit <= 0:
            raise VideoStorageError("Asset byte limit must be positive")
        destination = self.asset_path(
            user_id=user_id,
            job_id=job_id,
            kind=kind,
            filename=filename,
            create=True,
        )
        digest = hashlib.sha256()
        size = 0
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=destination.parent, prefix=".incoming-", delete=False
            ) as output:
                temporary = Path(output.name)
                os.chmod(temporary, 0o640)
                for chunk in chunks:
                    if not isinstance(chunk, bytes):
                        raise VideoStorageError("Asset chunks must be bytes")
                    size += len(chunk)
                    if size > limit:
                        raise VideoStorageError("Asset exceeds configured byte limit")
                    output.write(chunk)
                    digest.update(chunk)
                output.flush()
                os.fsync(output.fileno())
            if size == 0:
                raise VideoStorageError("Asset is empty")
            os.replace(temporary, destination)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        object_key = destination.relative_to(self.root).as_posix()
        return StoredVideoAsset(object_key, destination, size, digest.hexdigest())

    def resolve_object(self, object_key: str) -> Path:
        if object_key.startswith(("/", "\\")) or "\\" in object_key:
            raise VideoStorageError("Invalid object key")
        path = (self.root / object_key).resolve()
        if self.root not in path.parents or not path.is_file():
            raise VideoStorageError("Stored asset does not exist")
        return path

    def stream_object(self, object_key: str, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
        if chunk_size <= 0:
            raise VideoStorageError("Chunk size must be positive")
        with self.resolve_object(object_key).open("rb") as source:
            while chunk := source.read(chunk_size):
                yield chunk


video_storage = LocalVideoStorage()
