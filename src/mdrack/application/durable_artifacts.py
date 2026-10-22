"""Explicit immutable host archive, not the clearable artifact cache.

Uses the existing no-follow, root-anchored private-filesystem adapter, but no
cache keys, cache inventory or cache lifecycle policy. File/directory flushing
and atomic publication support process/reopen durability, not a power-loss SLA.
"""

from __future__ import annotations

import errno
import hashlib
import json
import re
from pathlib import Path
from uuid import uuid4

from mdrack.application import _durable_artifacts_fs as fs
from mdrack.application.ready_ocr import MAX_READY_OCR_BYTES, _pairs

_MARKER = ".mdrack-durable-artifacts-v1"
_MAGIC = b"mdrack.durable-artifacts.v1\n"


class DurableArtifactStore:
    """Explicitly selected host archive. Original caller files are never deleted."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _open(self, *, create: bool) -> fs.Directory:
        path = self.root.absolute()
        for part in (path, *path.parents):
            if (part / ".mdrack-artifact-cache-v1").exists():
                raise ValueError("artifact_owner_is_cache")
            if part.is_symlink() or getattr(part, "is_junction", lambda: False)():
                raise ValueError("artifact_owner_unavailable")
        directory = fs.open_root(path, create=create)
        if directory is None:
            raise ValueError("artifact_owner_unavailable")
        try:
            names = fs.listdir(directory)
            if _MARKER not in names:
                if not create or names:
                    raise ValueError("artifact_owner_unrecognized")
                try:
                    fs.write_file(directory, _MARKER, _MAGIC)
                    fs.flush(directory)
                except FileExistsError:
                    pass
            if fs.read_file(directory, _MARKER, limit=len(_MAGIC)) != _MAGIC:
                raise ValueError("artifact_owner_unrecognized")
            return directory
        except BaseException:
            fs.close(directory)
            raise

    def initialize(self) -> None:
        """Provision an empty, explicit archive before sharing it with workers."""
        try:
            fs.close(self._open(create=True))
        except (OSError, ValueError):
            raise ValueError("artifact_owner_unavailable") from None

    @staticmethod
    def _read(directory: fs.Directory, digest: str) -> bytes:
        entry = fs.open_directory(directory, digest, kind="durable entry")
        try:
            data = fs.read_file(entry, "payload.json", limit=MAX_READY_OCR_BYTES)
            if len(data) > MAX_READY_OCR_BYTES or hashlib.sha256(data).hexdigest() != digest:
                raise ValueError("artifact_unavailable")
            return data
        finally:
            fs.close(entry)

    def retain(self, data: bytes) -> str:
        if not isinstance(data, bytes) or len(data) > MAX_READY_OCR_BYTES:
            raise ValueError("artifact_invalid")
        digest = hashlib.sha256(data).hexdigest()
        directory: fs.Directory | None = None
        stage: str | None = None
        try:
            directory = self._open(create=True)
            if digest in fs.listdir(directory):
                if self._read(directory, digest) != data:
                    raise ValueError("artifact_owner_collision")
                return "artifact:sha256:" + digest
            stage = ".stage-" + uuid4().hex
            fs.mkdir(directory, stage)
            temporary = fs.open_directory(directory, stage, kind="durable staging")
            try:
                fs.write_file(temporary, "payload.json", data)
                fs.flush(temporary)
            finally:
                fs.close(temporary)
            try:
                # A complete nonempty entry directory is the atomic publication.
                # Existing entries are not overwritten, even on POSIX rename.
                fs.publish(directory, stage, digest)
                stage = None
                fs.flush(directory)
            except OSError as error:
                if not isinstance(error, FileExistsError) and error.errno not in {errno.EEXIST, errno.ENOTEMPTY}:
                    raise
            if self._read(directory, digest) != data:
                raise ValueError("artifact_owner_collision")
            return "artifact:sha256:" + digest
        except (OSError, ValueError):
            raise ValueError("artifact_retention_failed") from None
        finally:
            if directory is not None:
                try:
                    if stage is not None:
                        fs.remove_tree(directory, stage)  # only this call's private staging name
                finally:
                    fs.close(directory)

    def read_bytes(self, reference: str) -> bytes:
        if not isinstance(reference, str) or not re.fullmatch(r"artifact:sha256:[a-f0-9]{64}", reference):
            raise ValueError("artifact_reference_invalid")
        directory: fs.Directory | None = None
        try:
            directory = self._open(create=False)
            return self._read(directory, reference.rsplit(":", 1)[1])
        except (OSError, ValueError):
            raise ValueError("artifact_unavailable") from None
        finally:
            if directory is not None:
                fs.close(directory)

    def read(self, reference: str) -> dict[str, object]:
        try:
            value = json.loads(self.read_bytes(reference).decode("utf-8"), object_pairs_hook=_pairs)
            if type(value) is not dict:
                raise ValueError
            return value
        except (ValueError, UnicodeError):
            raise ValueError("artifact_unavailable") from None
