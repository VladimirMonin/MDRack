"""Private filesystem operations for the artifact cache; no cache policy lives here."""
from __future__ import annotations

import errno
import os
import stat
from pathlib import Path

_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC if os.name != "nt" else 0
_FILE_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)


def component(name: str) -> None:
    if not name or name in {".", ".."} or any(c in name for c in "/\\:\0"):
        raise ValueError("artifact cache component invalid")


if os.name == "nt":
    from ._artifact_cache_windows import Handle as Directory
    from ._artifact_cache_windows import (  # noqa: F401
        close,
        file_info,
        flush,
        listdir,
        mkdir,
        open_directory,
        open_root,
        publish,
        read_file,
        regular_file_info,
        remove_empty,
        remove_tree,
        write_file,
    )
else:
    Directory = int

    def close(fd: int) -> None:
        os.close(fd)

    def flush(fd: int) -> None:
        os.fsync(fd)

    def open_directory(parent: int, name: str, *, kind: str) -> int:
        component(name)
        try:
            fd = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent)
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise ValueError(f"artifact cache {kind} must be a real directory") from exc
            raise
        try:
            os.fchmod(fd, 0o700)
            return fd
        except BaseException:
            os.close(fd)
            raise

    def open_root(path: Path, *, create: bool) -> int | None:
        if create:
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            fd = os.open(path, _DIRECTORY_FLAGS)
        except FileNotFoundError:
            return None
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise ValueError("artifact cache root must be a real directory") from exc
            raise
        try:
            os.fchmod(fd, 0o700)
            return fd
        except BaseException:
            os.close(fd)
            raise

    def mkdir(parent: int, name: str) -> None:
        component(name)
        os.mkdir(name, mode=0o700, dir_fd=parent)

    def listdir(fd: int) -> list[str]:
        return os.listdir(fd)

    def file_info(parent: int, name: str) -> os.stat_result:
        component(name)
        return os.stat(name, dir_fd=parent, follow_symlinks=False)

    def regular_file_info(parent: int, name: str) -> os.stat_result:
        component(name)
        fd = os.open(name, _FILE_FLAGS | os.O_NONBLOCK, dir_fd=parent)
        try:
            return os.fstat(fd)
        finally:
            os.close(fd)

    def write_file(parent: int, name: str, payload: bytes) -> None:
        component(name)
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        try:
            stream = os.fdopen(fd, "wb")
        except BaseException:
            os.close(fd)
            raise
        with stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())

    def read_file(parent: int, name: str, *, limit: int) -> bytes:
        component(name)
        fd = os.open(name, _FILE_FLAGS | os.O_NONBLOCK, dir_fd=parent)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                raise ValueError("artifact cache file is not a bounded regular file")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                return stream.read(limit + 1)
        finally:
            os.close(fd)

    def publish(parent: int, source: str, target: str) -> None:
        component(source)
        component(target)
        os.rename(source, target, src_dir_fd=parent, dst_dir_fd=parent)

    def remove_tree(parent: int, name: str) -> None:
        info = file_info(parent, name)
        if not stat.S_ISDIR(info.st_mode):
            os.unlink(name, dir_fd=parent)
            return
        fd = open_directory(parent, name, kind="entry")
        try:
            for child in listdir(fd):
                remove_tree(fd, child)
        finally:
            close(fd)
        os.rmdir(name, dir_fd=parent)

    def remove_empty(parent: int, child: int, name: str) -> None:
        if listdir(child):
            return
        try:
            os.rmdir(name, dir_fd=parent)
        except OSError as exc:
            if exc.errno not in {errno.ENOTEMPTY, errno.ENOENT}:
                raise
