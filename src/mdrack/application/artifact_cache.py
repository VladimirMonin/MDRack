"""Immutable content-addressed cache for expensive application artifacts.

The cache is deliberately independent from the retrieval catalog. Keys and
metadata contain only opaque SHA-256 fingerprints; payloads may contain private
provider output and are stored with owner-only permissions.
"""

from __future__ import annotations

import hashlib
import json
import re
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from . import _artifact_cache_fs as fs
from ._artifact_cache_fs import close, flush, listdir

_CACHE_SCHEMA = "mdrack.artifact-cache.v1"
_CACHE_MARKER = ".mdrack-artifact-cache-v1"
_CACHE_MARKER_BYTES = (_CACHE_SCHEMA + "\n").encode("ascii")
_FINGERPRINT_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_KIND_RE = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}\Z")
_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_SHARD_RE = re.compile(r"[0-9a-f]{2}\Z")
_MAX_METADATA_BYTES = 16_384


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
                    entry_info = fs.file_info(shard_fd, key.digest)
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
                close(shard_fd)
        finally:
            close(root_fd)

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
                proposed_name = f".tmp-{uuid.uuid4().hex}"
                fs.mkdir(shard_fd, proposed_name)
                temp_name = proposed_name  # cleanup authority starts only after exclusive creation
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
                    flush(temp_fd)
                finally:
                    close(temp_fd)
                try:
                    fs.publish(shard_fd, temp_name, key.digest)
                    temp_name = None
                except OSError:
                    if self._read_valid_entry_at(shard_fd, key.digest, key) is not None:
                        return ArtifactCacheStoreResult("exists")
                    raise
                flush(shard_fd)
                return ArtifactCacheStoreResult("stored")
            finally:
                try:
                    if temp_name is not None:
                        try:
                            self._remove_tree_at(shard_fd, temp_name)
                        except FileNotFoundError:
                            pass
                finally:
                    close(shard_fd)
        finally:
            close(root_fd)

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
                close(shard_fd)
        finally:
            close(root_fd)

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
            for shard_name in listdir(root_fd):
                if shard_name == _CACHE_MARKER:
                    continue
                self._remove_tree_at(root_fd, shard_name)
            flush(root_fd)
            return entry_count
        finally:
            close(root_fd)

    def _open_root(self, *, create: bool) -> fs.Directory | None:
        return fs.open_root(self.root, create=create)

    def _validate_root(self, root_fd: fs.Directory, *, require_marker: bool) -> bool:
        names = listdir(root_fd)
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

    def _ensure_owned_root(self, root_fd: fs.Directory) -> None:
        if self._validate_root(root_fd, require_marker=False):
            return
        self._write_private_file_at(root_fd, _CACHE_MARKER, _CACHE_MARKER_BYTES)
        flush(root_fd)

    def _open_or_create_shard(self, root_fd: fs.Directory, name: str) -> fs.Directory:
        try:
            fs.mkdir(root_fd, name)
        except FileExistsError:
            pass
        return self._open_directory_at(root_fd, name, kind="shard")

    _open_directory_at = staticmethod(fs.open_directory)
    _write_private_file_at = staticmethod(fs.write_file)
    _read_file_at = staticmethod(fs.read_file)

    def _read_valid_entry_at(
        self,
        shard_fd: fs.Directory,
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
            close(entry_fd)

    def _scan(self, *, verify_payload: bool) -> ArtifactCacheReport:
        root_fd = self._open_root(create=False)
        if root_fd is None:
            return ArtifactCacheReport(0, 0, 0, 0)
        try:
            if not self._validate_root(root_fd, require_marker=False):
                return ArtifactCacheReport(0, 0, 0, 0)
            return self._scan_at(root_fd, verify_payload=verify_payload)
        finally:
            close(root_fd)

    def _scan_at(self, root_fd: fs.Directory, *, verify_payload: bool) -> ArtifactCacheReport:
        entry_count = 0
        valid_entries = 0
        corrupt_entries = 0
        payload_bytes = 0
        for shard_name in listdir(root_fd):
            if shard_name == _CACHE_MARKER:
                continue
            shard_fd = self._open_directory_at(root_fd, shard_name, kind="shard")
            try:
                for entry_name in listdir(shard_fd):
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
                close(shard_fd)
        return ArtifactCacheReport(entry_count, valid_entries, corrupt_entries, payload_bytes)

    def _key_from_metadata_at(self, shard_fd: fs.Directory, entry_name: str) -> ArtifactCacheKey | None:
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
            close(entry_fd)

    def _declared_payload_size_at(self, shard_fd: fs.Directory, entry_name: str) -> int | None:
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
                payload_info = fs.regular_file_info(entry_fd, "payload.bin")
                if not stat.S_ISREG(payload_info.st_mode) or payload_info.st_size != payload_size:
                    return None
                return payload_size
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                return None
        finally:
            close(entry_fd)

    def _validate_purge_structure(self, root_fd: fs.Directory) -> None:
        for shard_name in listdir(root_fd):
            if shard_name == _CACHE_MARKER:
                continue
            shard_fd = self._open_directory_at(root_fd, shard_name, kind="shard")
            try:
                for entry_name in listdir(shard_fd):
                    if not (_DIGEST_RE.fullmatch(entry_name) or entry_name.startswith(".tmp-")):
                        raise ValueError("artifact cache shard contains foreign content")
                    entry_fd = self._open_directory_at(shard_fd, entry_name, kind="entry")
                    try:
                        names = set(listdir(entry_fd))
                        if not names.issubset({"metadata.json", "payload.bin"}):
                            raise ValueError("artifact cache entry contains foreign content")
                        for name in names:
                            info = fs.file_info(entry_fd, name)
                            if not stat.S_ISREG(info.st_mode):
                                raise ValueError("artifact cache entry contains unsafe content")
                    finally:
                        close(entry_fd)
            finally:
                close(shard_fd)

    _remove_tree_at = staticmethod(fs.remove_tree)
    _remove_empty_shard = staticmethod(fs.remove_empty)


__all__ = [
    "ArtifactCache",
    "ArtifactCacheKey",
    "ArtifactCacheLookup",
    "ArtifactCacheReport",
    "ArtifactCacheStoreResult",
]
