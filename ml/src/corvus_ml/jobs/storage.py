"""Object storage for jobs: a bucket (or a local directory in tests) behind a pyarrow filesystem.

Production uses :func:`s3_store` (path-style addressing, the variables the services use); tests
inject a ``pyarrow.fs.LocalFileSystem`` rooted in ``tmp_path``.
"""

from __future__ import annotations

import posixpath
import shutil
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

import pyarrow.fs as pafs

from corvus_ml.jobs.context import S3Settings

_CHUNK = 1 << 20


class ObjectStore:
    """Keys below ``root`` of a pyarrow filesystem (``root`` is the bucket for S3)."""

    def __init__(self, filesystem: pafs.FileSystem, root: str) -> None:
        self.filesystem = filesystem
        self.root = root.rstrip("/")

    def path(self, key: str) -> str:
        return posixpath.join(self.root, key.lstrip("/"))

    def put_bytes(self, key: str, data: bytes) -> None:
        self._ensure_parent(key)
        with self.filesystem.open_output_stream(self.path(key)) as out:
            out.write(data)

    def put_file(self, key: str, source: Path) -> None:
        self._ensure_parent(key)
        with open(source, "rb") as src, self.filesystem.open_output_stream(self.path(key)) as out:
            shutil.copyfileobj(src, out, _CHUNK)

    def get_bytes(self, key: str) -> bytes:
        with self.filesystem.open_input_stream(self.path(key)) as stream:
            return stream.read()

    def exists(self, key: str) -> bool:
        return self.filesystem.get_file_info(self.path(key)).type == pafs.FileType.File

    def list_keys(self, prefix: str) -> list[str]:
        """Keys of all files below ``prefix`` (sorted, relative to the store root)."""
        base = self.path(prefix.rstrip("/"))
        infos = self.filesystem.get_file_info(
            pafs.FileSelector(base, allow_not_found=True, recursive=True)
        )
        keys = [posixpath.relpath(i.path, self.root) for i in infos if i.type == pafs.FileType.File]
        return sorted(keys)

    def delete_prefix(self, prefix: str) -> None:
        base = self.path(prefix.rstrip("/"))
        if self.filesystem.get_file_info(base).type == pafs.FileType.Directory:
            self.filesystem.delete_dir_contents(base)

    def upload_dir(self, prefix: str, source: Path) -> list[str]:
        """Copy every file below ``source`` to ``prefix/<relative path>``; returns the keys."""
        keys = []
        for file in sorted(p for p in source.rglob("*") if p.is_file()):
            key = posixpath.join(prefix.rstrip("/"), file.relative_to(source).as_posix())
            self.put_file(key, file)
            keys.append(key)
        return keys

    def download_dir(
        self, prefix: str, dest: Path, skip: Callable[[str], bool] = lambda rel: False
    ) -> list[Path]:
        """Copy files below ``prefix`` into ``dest``; ``skip(relative path)`` leaves some out."""
        written = []
        for key in self.list_keys(prefix):
            rel = posixpath.relpath(key, prefix.rstrip("/"))
            if skip(rel):
                continue
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with (
                self.filesystem.open_input_stream(self.path(key)) as src,
                open(target, "wb") as out,
            ):
                shutil.copyfileobj(src, out, _CHUNK)
            written.append(target)
        return written

    def _ensure_parent(self, key: str) -> None:
        parent = posixpath.dirname(self.path(key))
        if parent:
            self.filesystem.create_dir(parent, recursive=True)


def s3_filesystem(settings: S3Settings) -> pafs.S3FileSystem:
    """Path-style S3 (MinIO) or AWS S3 from the ``CORVUS_S3_*`` variables."""
    kwargs: dict[str, object] = {
        "region": settings.region,
        "access_key": settings.access_key,
        "secret_key": settings.secret_key,
    }
    if settings.endpoint_url:
        parsed = urlparse(settings.endpoint_url)
        kwargs["endpoint_override"] = parsed.netloc or settings.endpoint_url
        kwargs["scheme"] = parsed.scheme or "https"
        kwargs["force_virtual_addressing"] = False
    return pafs.S3FileSystem(**kwargs)


def s3_store(settings: S3Settings) -> ObjectStore:
    return ObjectStore(s3_filesystem(settings), settings.bucket)


def local_store(root: Path) -> ObjectStore:
    """A store in a local directory (tests)."""
    root.mkdir(parents=True, exist_ok=True)
    return ObjectStore(pafs.LocalFileSystem(), str(root))
