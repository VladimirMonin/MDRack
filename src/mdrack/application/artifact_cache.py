"""Immutable content-addressed cache for expensive application artifacts.

The cache is deliberately independent from the retrieval catalog. Keys and
metadata contain only opaque SHA-256 fingerprints; payloads may contain private
provider output and are stored with owner-only permissions.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

_CACHE_SCHEMA = "mdrack.artifact-cache.v1"
_CACHE_MARKER = ".mdrack-artifact-cache-v1"
_CACHE_MARKER_BYTES = (_CACHE_SCHEMA + "\n").encode("ascii")
_FINGERPRINT_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_KIND_RE = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}\Z")
_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_SHARD_RE = re.compile(r"[0-9a-f]{2}\Z")
_MAX_METADATA_BYTES = 16_384
_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
_FILE_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)


@dataclass(frozen=True)
class ArtifactCacheKey:
    """Complete immutable identity for one derived artifact."""

    artifact_kind: str
    source_fingerprint: str
    producer_fingerprint: str
    model_fingerprint: str
    prompt_fingerprint: str
    config_fingerprint: str
    preprocessing_fingerprint: str

    def __post_init__(self) -> None:
        if not _KIND_RE.fullmatch(self.artifact_kind):
            raise ValueError("artifact_kind must be a safe lowercase identifier")
        for field_name in (
            "source_fingerprint",
            "producer_fingerprint",
            "model_fingerprint",
            "prompt_fingerprint",
            "config_fingerprint",
            "preprocessing_fingerprint",
        ):
            if not _FINGERPRINT_RE.fullmatch(getattr(self, field_name)):
                raise ValueError(f"{field_name} must be an opaque sha256 fingerprint")

    def to_dict(self) -> dict[str, str]:
        """Return the canonical privacy-safe key mapping."""
        return {
            "artifact_kind": self.artifact_kind,
            "source_fingerprint": self.source_fingerprint,
            "producer_fingerprint": self.producer_fingerprint,
            "model_fingerprint": self.model_fingerprint,
            "prompt_fingerprint": self.prompt_fingerprint,
            "config_fingerprint": self.config_fingerprint,
            "preprocessing_fingerprint": self.preprocessing_fingerprint,
        }

    @property
    def digest(self) -> str:
        canonical = json.dumps(
            self.to_dict(),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        return hashlib.sha256(canonical).hexdigest()


@dataclass(frozen=True)
class ArtifactCacheLookup:
    state: Literal["hit", "miss", "corrupt"]
    payload: bytes | None = None


@dataclass(frozen=True)
class ArtifactCacheStoreResult:
    state: Literal["stored", "exists"]


@dataclass(frozen=True)
class ArtifactCacheReport:
    entry_count: int
    valid_entries: int
    corrupt_entries: int
    payload_bytes: int

    @property
    def ok(self) -> bool:
        return self.corrupt_entries == 0


class ArtifactCache:
    """Filesystem-backed immutable cache with root-anchored no-follow I/O."""

    def __init__(self, root: Path, *, max_entry_bytes: int = 100 * 1024 * 1024) -> None:
        if max_entry_bytes < 1:
            raise ValueError("max_entry_bytes must be positive")
        self.root = Path(root)
        self.max_entry_bytes = max_entry_bytes
        if self.root.is_symlink():
            raise ValueError("artifact cache root must not be a symlink")

    def entry_path(self, key: ArtifactCacheKey) -> Path:
        """Return the logical entry path without using it for cache I/O."""
        digest = key.digest
        return self.root / digest[:2] / digest

    def lookup(self, key: ArtifactCacheKey, *, recover_corrupt: bool = True) -> ArtifactCacheLookup:
        root_fd = self._open_root(create=False)
        if root_fd is None:
            return ArtifactCacheLookup("miss")
        try:
            if not self._validate_root(root_fd, require_marker=False):
                return ArtifactCacheLookup("miss")
            try:
                shard_fd = self._open_directory_at(root_fd, key.digest[:2], kind="shard")
            except FileNotFoundError:
                return ArtifactCacheLookup("miss")
            except ValueError:
                return ArtifactCacheLookup("corrupt")
            try:
                try:
                    entry_info = os.stat(key.digest, dir_fd=shard_fd, follow_symlinks=False)
                except FileNotFoundError:
                    return ArtifactCacheLookup("miss")
                payload = self._read_valid_entry_at(shard_fd, key.digest, key)
                if payload is not None:
                    return ArtifactCacheLookup("hit", payload)
                if recover_corrupt and (stat.S_ISDIR(entry_info.st_mode) or stat.S_ISLNK(entry_info.st_mode)):
                    self._remove_tree_at(shard_fd, key.digest)
                    self._remove_empty_shard(root_fd, shard_fd, key.digest[:2])
                return ArtifactCacheLookup("corrupt")
            finally:
                os.close(shard_fd)
        finally:
            os.close(root_fd)

    def store(self, key: ArtifactCacheKey, payload: bytes) -> ArtifactCacheStoreResult:
        if not isinstance(payload, bytes):
            raise TypeError("artifact payload must be bytes")
        if len(payload) > self.max_entry_bytes:
            raise ValueError("artifact payload exceeds max_entry_bytes")

        existing = self.lookup(key)
        if existing.state == "hit":
            return ArtifactCacheStoreResult("exists")

        root_fd = self._open_root(create=True)
        if root_fd is None:  # pragma: no cover - create=True either opens or raises
            raise OSError("artifact cache root could not be opened")
        try:
            self._ensure_owned_root(root_fd)
            shard_fd = self._open_or_create_shard(root_fd, key.digest[:2])
            temp_name: str | None = None
            try:
                temp_name = f".tmp-{uuid.uuid4().hex}"
                os.mkdir(temp_name, mode=0o700, dir_fd=shard_fd)
                temp_fd = self._open_directory_at(shard_fd, temp_name, kind="temporary entry")
                try:
                    metadata = {
                        "schema": _CACHE_SCHEMA,
                        "key": key.to_dict(),
                        "key_digest": key.digest,
                        "payload_sha256": hashlib.sha256(payload).hexdigest(),
                        "payload_bytes": len(payload),
                    }
                    metadata_bytes = json.dumps(
                        metadata,
                        ensure_ascii=True,
                        separators=(",", ":"),
                        sort_keys=True,
                    ).encode("ascii")
                    self._write_private_file_at(temp_fd, "payload.bin", payload)
                    self._write_private_file_at(temp_fd, "metadata.json", metadata_bytes)
                    os.fsync(temp_fd)
                finally:
                    os.close(temp_fd)
                try:
                    os.rename(
                        temp_name,
                        key.digest,
                        src_dir_fd=shard_fd,
                        dst_dir_fd=shard_fd,
                    )
                except OSError:
                    if self._read_valid_entry_at(shard_fd, key.digest, key) is not None:
                        return ArtifactCacheStoreResult("exists")
                    raise
                os.fsync(shard_fd)
                return ArtifactCacheStoreResult("stored")
            finally:
                if temp_name is not None:
                    try:
                        self._remove_tree_at(shard_fd, temp_name)
                    except FileNotFoundError:
                        pass
                os.close(shard_fd)
        finally:
            os.close(root_fd)

    def discard(self, key: ArtifactCacheKey) -> None:
        """Discard one invalid semantic artifact without touching source/catalog data."""
        root_fd = self._open_root(create=False)
        if root_fd is None:
            return
        try:
            if not self._validate_root(root_fd, require_marker=False):
                return
            shard_fd = self._open_directory_at(root_fd, key.digest[:2], kind="shard")
            try:
                self._remove_tree_at(shard_fd, key.digest)
                self._remove_empty_shard(root_fd, shard_fd, key.digest[:2])
            except FileNotFoundError:
                return
            finally:
                os.close(shard_fd)
        finally:
            os.close(root_fd)

    def status(self) -> ArtifactCacheReport:
        """Return count-only status without hashing payloads."""
        return self._scan(verify_payload=False)

    def verify(self) -> ArtifactCacheReport:
        """Read and hash every entry without repairing or deleting it."""
        return self._scan(verify_payload=True)

    def purge(self, *, confirm: bool = False) -> int:
        """Remove validated cache-owned entries after explicit confirmation."""
        if not confirm:
            raise ValueError("cache purge requires confirmation")
        root_fd = self._open_root(create=False)
        if root_fd is None:
            return 0
        try:
            self._validate_root(root_fd, require_marker=True)
            self._validate_purge_structure(root_fd)
            entry_count = self._scan_at(root_fd, verify_payload=False).entry_count
            for shard_name in os.listdir(root_fd):
                if shard_name == _CACHE_MARKER:
                    continue
                self._remove_tree_at(root_fd, shard_name)
            os.fsync(root_fd)
            return entry_count
        finally:
            os.close(root_fd)

    def _open_root(self, *, create: bool) -> int | None:
        if create:
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            descriptor = os.open(self.root, _DIRECTORY_FLAGS)
        except FileNotFoundError:
            return None
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise ValueError("artifact cache root must be a real directory") from exc
            raise
        os.fchmod(descriptor, 0o700)
        return descriptor

    def _validate_root(self, root_fd: int, *, require_marker: bool) -> bool:
        names = os.listdir(root_fd)
        if _CACHE_MARKER not in names:
            if require_marker or names:
                raise ValueError("artifact cache marker missing")
            return False
        marker = self._read_file_at(root_fd, _CACHE_MARKER, limit=len(_CACHE_MARKER_BYTES))
        if marker != _CACHE_MARKER_BYTES:
            raise ValueError("artifact cache marker invalid")
        foreign = [name for name in names if name != _CACHE_MARKER and not _SHARD_RE.fullmatch(name)]
        if foreign:
            raise ValueError("artifact cache root contains foreign content")
        return True

    def _ensure_owned_root(self, root_fd: int) -> None:
        if self._validate_root(root_fd, require_marker=False):
            return
        self._write_private_file_at(root_fd, _CACHE_MARKER, _CACHE_MARKER_BYTES)
        os.fsync(root_fd)

    def _open_or_create_shard(self, root_fd: int, name: str) -> int:
        try:
            os.mkdir(name, mode=0o700, dir_fd=root_fd)
        except FileExistsError:
            pass
        descriptor = self._open_directory_at(root_fd, name, kind="shard")
        os.fchmod(descriptor, 0o700)
        return descriptor

    @staticmethod
    def _open_directory_at(parent_fd: int, name: str, *, kind: str) -> int:
        try:
            return os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
        except FileNotFoundError:
            raise
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise ValueError(f"artifact cache {kind} must be a real directory") from exc
            raise

    @staticmethod
    def _write_private_file_at(parent_fd: int, name: str, payload: bytes) -> None:
        descriptor = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent_fd,
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise

    @staticmethod
    def _read_file_at(parent_fd: int, name: str, *, limit: int) -> bytes:
        descriptor = os.open(name, _FILE_FLAGS, dir_fd=parent_fd)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                raise ValueError("artifact cache file is not a bounded regular file")
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                return stream.read(limit + 1)
        finally:
            os.close(descriptor)

    def _read_valid_entry_at(
        self,
        shard_fd: int,
        entry_name: str,
        expected_key: ArtifactCacheKey,
    ) -> bytes | None:
        try:
            entry_fd = self._open_directory_at(shard_fd, entry_name, kind="entry")
        except (FileNotFoundError, ValueError):
            return None
        try:
            try:
                metadata_raw = self._read_file_at(
                    entry_fd,
                    "metadata.json",
                    limit=_MAX_METADATA_BYTES,
                )
                metadata = json.loads(metadata_raw)
                if not isinstance(metadata, dict) or metadata.get("schema") != _CACHE_SCHEMA:
                    return None
                if metadata.get("key") != expected_key.to_dict():
                    return None
                if metadata.get("key_digest") != expected_key.digest:
                    return None
                payload_size = metadata.get("payload_bytes")
                payload_digest = metadata.get("payload_sha256")
                if (
                    not isinstance(payload_size, int)
                    or payload_size < 0
                    or payload_size > self.max_entry_bytes
                    or not isinstance(payload_digest, str)
                    or not _DIGEST_RE.fullmatch(payload_digest)
                ):
                    return None
                payload = self._read_file_at(entry_fd, "payload.bin", limit=self.max_entry_bytes)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                return None
            if len(payload) != payload_size or hashlib.sha256(payload).hexdigest() != payload_digest:
                return None
            return payload
        finally:
            os.close(entry_fd)

    def _scan(self, *, verify_payload: bool) -> ArtifactCacheReport:
        root_fd = self._open_root(create=False)
        if root_fd is None:
            return ArtifactCacheReport(0, 0, 0, 0)
        try:
            if not self._validate_root(root_fd, require_marker=False):
                return ArtifactCacheReport(0, 0, 0, 0)
            return self._scan_at(root_fd, verify_payload=verify_payload)
        finally:
            os.close(root_fd)

    def _scan_at(self, root_fd: int, *, verify_payload: bool) -> ArtifactCacheReport:
        entry_count = 0
        valid_entries = 0
        corrupt_entries = 0
        payload_bytes = 0
        for shard_name in os.listdir(root_fd):
            if shard_name == _CACHE_MARKER:
                continue
            shard_fd = self._open_directory_at(root_fd, shard_name, kind="shard")
            try:
                for entry_name in os.listdir(shard_fd):
                    if entry_name.startswith(".") or not _DIGEST_RE.fullmatch(entry_name):
                        continue
                    entry_count += 1
                    key = self._key_from_metadata_at(shard_fd, entry_name)
                    if key is None or key.digest != entry_name:
                        corrupt_entries += 1
                        continue
                    if verify_payload:
                        payload = self._read_valid_entry_at(shard_fd, entry_name, key)
                        if payload is None:
                            corrupt_entries += 1
                            continue
                        payload_size = len(payload)
                    else:
                        declared_size = self._declared_payload_size_at(shard_fd, entry_name)
                        if declared_size is None:
                            corrupt_entries += 1
                            continue
                        payload_size = declared_size
                    valid_entries += 1
                    payload_bytes += payload_size
            finally:
                os.close(shard_fd)
        return ArtifactCacheReport(entry_count, valid_entries, corrupt_entries, payload_bytes)

    def _key_from_metadata_at(self, shard_fd: int, entry_name: str) -> ArtifactCacheKey | None:
        try:
            entry_fd = self._open_directory_at(shard_fd, entry_name, kind="entry")
        except (FileNotFoundError, ValueError):
            return None
        try:
            try:
                metadata = json.loads(
                    self._read_file_at(entry_fd, "metadata.json", limit=_MAX_METADATA_BYTES)
                )
                if not isinstance(metadata, dict) or metadata.get("schema") != _CACHE_SCHEMA:
                    return None
                key_data = metadata.get("key")
                if not isinstance(key_data, dict):
                    return None
                return ArtifactCacheKey(**key_data)
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                return None
        finally:
            os.close(entry_fd)

    def _declared_payload_size_at(self, shard_fd: int, entry_name: str) -> int | None:
        try:
            entry_fd = self._open_directory_at(shard_fd, entry_name, kind="entry")
        except (FileNotFoundError, ValueError):
            return None
        try:
            try:
                metadata = json.loads(
                    self._read_file_at(entry_fd, "metadata.json", limit=_MAX_METADATA_BYTES)
                )
                payload_size = metadata.get("payload_bytes")
                if (
                    not isinstance(payload_size, int)
                    or payload_size < 0
                    or payload_size > self.max_entry_bytes
                ):
                    return None
                payload_fd = os.open("payload.bin", _FILE_FLAGS, dir_fd=entry_fd)
                try:
                    payload_info = os.fstat(payload_fd)
                finally:
                    os.close(payload_fd)
                if not stat.S_ISREG(payload_info.st_mode) or payload_info.st_size != payload_size:
                    return None
                return payload_size
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                return None
        finally:
            os.close(entry_fd)

    def _validate_purge_structure(self, root_fd: int) -> None:
        for shard_name in os.listdir(root_fd):
            if shard_name == _CACHE_MARKER:
                continue
            shard_fd = self._open_directory_at(root_fd, shard_name, kind="shard")
            try:
                for entry_name in os.listdir(shard_fd):
                    if not (_DIGEST_RE.fullmatch(entry_name) or entry_name.startswith(".tmp-")):
                        raise ValueError("artifact cache shard contains foreign content")
                    entry_fd = self._open_directory_at(shard_fd, entry_name, kind="entry")
                    try:
                        names = set(os.listdir(entry_fd))
                        if not names.issubset({"metadata.json", "payload.bin"}):
                            raise ValueError("artifact cache entry contains foreign content")
                        for name in names:
                            info = os.stat(name, dir_fd=entry_fd, follow_symlinks=False)
                            if not stat.S_ISREG(info.st_mode):
                                raise ValueError("artifact cache entry contains unsafe content")
                    finally:
                        os.close(entry_fd)
            finally:
                os.close(shard_fd)

    def _remove_tree_at(self, parent_fd: int, name: str) -> None:
        info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode):
            os.unlink(name, dir_fd=parent_fd)
            return
        child_fd = self._open_directory_at(parent_fd, name, kind="entry")
        try:
            for child_name in os.listdir(child_fd):
                self._remove_tree_at(child_fd, child_name)
        finally:
            os.close(child_fd)
        os.rmdir(name, dir_fd=parent_fd)

    @staticmethod
    def _remove_empty_shard(root_fd: int, shard_fd: int, shard_name: str) -> None:
        if os.listdir(shard_fd):
            return
        try:
            os.rmdir(shard_name, dir_fd=root_fd)
        except OSError as exc:
            if exc.errno not in {errno.ENOTEMPTY, errno.ENOENT}:
                raise


__all__ = [
    "ArtifactCache",
    "ArtifactCacheKey",
    "ArtifactCacheLookup",
    "ArtifactCacheReport",
    "ArtifactCacheStoreResult",
]
