"""Private filesystem operations for the artifact cache; no cache policy lives here."""
from __future__ import annotations

import errno
import os
import stat
import sys
from pathlib import Path
from typing import TYPE_CHECKING, TypeAlias

_DIRECTORY_FLAGS = (
    os.O_RDONLY | getattr(os, "O_DIRECTORY") | getattr(os, "O_NOFOLLOW") | getattr(os, "O_CLOEXEC")
    if os.name != "nt" else 0
)
_FILE_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)


def component(name: str) -> None:
    if not name or name in {".", ".."} or any(c in name for c in "/\\:\0"):
        raise ValueError("artifact cache component invalid")



def _posix_close(fd: int) -> None:
    os.close(fd)

def _posix_flush(fd: int) -> None:
    os.fsync(fd)

def _posix_open_directory(parent: int, name: str, *, kind: str) -> int:
    component(name)
    try:
        fd = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise ValueError(f"artifact cache {kind} must be a real directory") from exc
        raise
    try:
        getattr(os, "fchmod")(fd, 0o700)
        return fd
    except BaseException:
        os.close(fd)
        raise

def _posix_open_root(path: Path, *, create: bool) -> int | None:
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
        getattr(os, "fchmod")(fd, 0o700)
        return fd
    except BaseException:
        os.close(fd)
        raise

def _posix_mkdir(parent: int, name: str) -> None:
    component(name)
    os.mkdir(name, mode=0o700, dir_fd=parent)

def _posix_listdir(fd: int) -> list[str]:
    return os.listdir(fd)

def _posix_file_info(parent: int, name: str) -> os.stat_result:
    component(name)
    return os.stat(name, dir_fd=parent, follow_symlinks=False)

def _posix_regular_file_info(parent: int, name: str) -> os.stat_result:
    component(name)
    fd = os.open(name, _FILE_FLAGS | getattr(os, "O_NONBLOCK"), dir_fd=parent)
    try:
        return os.fstat(fd)
    finally:
        os.close(fd)

def _posix_write_file(parent: int, name: str, payload: bytes) -> None:
    component(name)
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW"), 0o600, dir_fd=parent)
    try:
        stream = os.fdopen(fd, "wb")
    except BaseException:
        os.close(fd)
        raise
    with stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())

def _posix_read_file(parent: int, name: str, *, limit: int) -> bytes:
    component(name)
    fd = os.open(name, _FILE_FLAGS | getattr(os, "O_NONBLOCK"), dir_fd=parent)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("artifact cache file is not a bounded regular file")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            return stream.read(limit + 1)
    finally:
        os.close(fd)

def _posix_publish(parent: int, source: str, target: str) -> None:
    component(source)
    component(target)
    os.rename(source, target, src_dir_fd=parent, dst_dir_fd=parent)

def _posix_remove_tree(parent: int, name: str) -> None:
    info = _posix_file_info(parent, name)
    if not stat.S_ISDIR(info.st_mode):
        os.unlink(name, dir_fd=parent)
        return
    fd = _posix_open_directory(parent, name, kind="entry")
    try:
        for child in _posix_listdir(fd):
            _posix_remove_tree(fd, child)
    finally:
        _posix_close(fd)
    os.rmdir(name, dir_fd=parent)

def _posix_remove_empty(parent: int, child: int, name: str) -> None:
    if _posix_listdir(child):
        return
    try:
        os.rmdir(name, dir_fd=parent)
    except OSError as exc:
        if exc.errno not in {errno.ENOTEMPTY, errno.ENOENT}:
            raise

if TYPE_CHECKING:
    # Match the static target ABI; this does not skip either implementation.
    if sys.platform == "win32":
        from ._artifact_cache_windows import Handle as _WindowsDirectory
        Directory: TypeAlias = _WindowsDirectory
        from ._artifact_cache_windows import (
            close as close,
        )
        from ._artifact_cache_windows import (
            file_info as file_info,
        )
        from ._artifact_cache_windows import (
            flush as flush,
        )
        from ._artifact_cache_windows import (
            listdir as listdir,
        )
        from ._artifact_cache_windows import (
            mkdir as mkdir,
        )
        from ._artifact_cache_windows import (
            open_directory as open_directory,
        )
        from ._artifact_cache_windows import (
            open_root as open_root,
        )
        from ._artifact_cache_windows import (
            publish as publish,
        )
        from ._artifact_cache_windows import (
            read_file as read_file,
        )
        from ._artifact_cache_windows import (
            regular_file_info as regular_file_info,
        )
        from ._artifact_cache_windows import (
            remove_empty as remove_empty,
        )
        from ._artifact_cache_windows import (
            remove_tree as remove_tree,
        )
        from ._artifact_cache_windows import (
            write_file as write_file,
        )
    else:
        Directory = int
        close = _posix_close
        flush = _posix_flush
        open_directory = _posix_open_directory
        open_root = _posix_open_root
        mkdir = _posix_mkdir
        listdir = _posix_listdir
        file_info = _posix_file_info
        regular_file_info = _posix_regular_file_info
        write_file = _posix_write_file
        read_file = _posix_read_file
        publish = _posix_publish
        remove_tree = _posix_remove_tree
        remove_empty = _posix_remove_empty
else:
    # Keep the original runtime platform selector unchanged.
    if os.name == "nt":
        from ._artifact_cache_windows import Handle as Directory
        from ._artifact_cache_windows import (
            close as close,
        )
        from ._artifact_cache_windows import (
            file_info as file_info,
        )
        from ._artifact_cache_windows import (
            flush as flush,
        )
        from ._artifact_cache_windows import (
            listdir as listdir,
        )
        from ._artifact_cache_windows import (
            mkdir as mkdir,
        )
        from ._artifact_cache_windows import (
            open_directory as open_directory,
        )
        from ._artifact_cache_windows import (
            open_root as open_root,
        )
        from ._artifact_cache_windows import (
            publish as publish,
        )
        from ._artifact_cache_windows import (
            read_file as read_file,
        )
        from ._artifact_cache_windows import (
            regular_file_info as regular_file_info,
        )
        from ._artifact_cache_windows import (
            remove_empty as remove_empty,
        )
        from ._artifact_cache_windows import (
            remove_tree as remove_tree,
        )
        from ._artifact_cache_windows import (
            write_file as write_file,
        )
    else:
        Directory = int
        close = _posix_close
        flush = _posix_flush
        open_directory = _posix_open_directory
        open_root = _posix_open_root
        mkdir = _posix_mkdir
        listdir = _posix_listdir
        file_info = _posix_file_info
        regular_file_info = _posix_regular_file_info
        write_file = _posix_write_file
        read_file = _posix_read_file
        publish = _posix_publish
        remove_tree = _posix_remove_tree
        remove_empty = _posix_remove_empty
