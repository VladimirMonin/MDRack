"""Private per-namespace selected-file lifecycle bookkeeping.

This is not a second catalog and it never grants filesystem access. It records the
last completed outcome for selected relative source references so a later complete
enumeration can distinguish a disappeared source from an unrelated or unseen scope.
The SQLite publication graph remains the only authority for ready resources.
"""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, cast

from mdrack.ingestion.raw_source_provenance import (
    RawSourceError,
    validate_source_ref,
)

FOLDER_LIFECYCLE_SCHEMA = "mdrack.folder-lifecycle.v1"
FOLDER_LIFECYCLE_FILENAME = "folder-lifecycle.json"
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_NAMESPACE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

Availability = Literal["available", "unavailable"]
FileKind = Literal["document", "image"]


class FolderLifecycleError(RuntimeError):
    """A fixed, payload-free lifecycle-state refusal."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _require_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _require_namespace(namespace: object) -> str:
    if not isinstance(namespace, str) or _NAMESPACE_PATTERN.fullmatch(namespace) is None:
        raise ValueError("invalid namespace")
    return namespace


def _require_source_ref(source_ref: object) -> str:
    if not isinstance(source_ref, str):
        raise ValueError("source_ref must be a string")
    try:
        validate_source_ref(source_ref)
    except RawSourceError:
        raise ValueError("invalid source_ref") from None
    return source_ref


def _is_link_like(path: Path) -> bool:
    """Refuse a linked state file before following it for read or replacement."""
    try:
        if path.is_symlink() or os.path.islink(path):
            return True
        is_junction = getattr(path, "is_junction", None)
        if callable(is_junction) and is_junction():
            return True
        path_is_junction = getattr(os.path, "isjunction", None)
        if callable(path_is_junction) and path_is_junction(path):
            return True
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
        if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
            return True
    except FileNotFoundError:
        return False
    except (OSError, ValueError):
        return True
    return False


@dataclass(frozen=True)
class LifecycleFile:
    """Last completed outcome for one selected relative source reference."""

    source_ref: str
    kind: FileKind
    media_type: str
    resource_id: str
    availability: Availability
    source_hash: str | None = None
    source_bytes: int | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_ref", _require_source_ref(self.source_ref))
        if self.kind not in {"document", "image"}:
            raise ValueError("invalid kind")
        object.__setattr__(self, "media_type", _require_text(self.media_type, "media_type"))
        object.__setattr__(self, "resource_id", _require_text(self.resource_id, "resource_id"))
        if self.availability not in {"available", "unavailable"}:
            raise ValueError("invalid availability")
        if self.source_hash is not None:
            if not isinstance(self.source_hash, str) or _SHA256_RE.fullmatch(self.source_hash) is None:
                raise ValueError("invalid source_hash")
        if self.source_bytes is not None:
            if type(self.source_bytes) is not int or self.source_bytes < 0:
                raise ValueError("invalid source_bytes")
        if self.reason is not None:
            object.__setattr__(self, "reason", _require_text(self.reason, "reason"))
        if self.availability == "available" and self.reason is not None:
            raise ValueError("available file cannot carry an unavailable reason")
        if self.availability == "unavailable" and self.reason is None:
            raise ValueError("unavailable file requires a reason")

    def to_dict(self) -> dict[str, object]:
        return {
            "source_ref": self.source_ref,
            "kind": self.kind,
            "media_type": self.media_type,
            "resource_id": self.resource_id,
            "availability": self.availability,
            "source_hash": self.source_hash,
            "source_bytes": self.source_bytes,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, raw: object) -> LifecycleFile:
        if not isinstance(raw, dict) or set(raw) != {
            "source_ref",
            "kind",
            "media_type",
            "resource_id",
            "availability",
            "source_hash",
            "source_bytes",
            "reason",
        }:
            raise ValueError("invalid lifecycle file")
        return cls(
            cast(str, raw["source_ref"]),
            cast(FileKind, raw["kind"]),
            cast(str, raw["media_type"]),
            cast(str, raw["resource_id"]),
            cast(Availability, raw["availability"]),
            cast(str | None, raw["source_hash"]),
            cast(int | None, raw["source_bytes"]),
            cast(str | None, raw["reason"]),
        )


def mark_unavailable(entry: LifecycleFile, *, reason: str = "source_missing") -> LifecycleFile:
    """Keep the last known identity/hash but make current availability explicit."""
    return replace(entry, availability="unavailable", reason=reason)


class FolderLifecycle:
    """Atomic, deterministic store-side state for selected relative references."""

    def __init__(
        self,
        path: Path,
        roots: Mapping[str, Mapping[str, LifecycleFile]] | None = None,
    ) -> None:
        self.path = path
        self._roots: dict[str, dict[str, LifecycleFile]] = {}
        for raw_namespace, files in (roots or {}).items():
            namespace = _require_namespace(raw_namespace)
            self._roots[namespace] = {}
            for raw_ref, entry in files.items():
                if not isinstance(entry, LifecycleFile):
                    raise TypeError("lifecycle entries must be LifecycleFile values")
                if raw_ref != entry.source_ref:
                    raise ValueError("lifecycle key and source_ref differ")
                if entry.source_ref in self._roots[namespace]:
                    raise ValueError("duplicate lifecycle source_ref")
                self._roots[namespace][entry.source_ref] = entry

    @classmethod
    def load(cls, path: Path) -> FolderLifecycle:
        if not path.exists():
            return cls(path)
        if _is_link_like(path):
            raise FolderLifecycleError("folder_lifecycle_link_refused")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            raise FolderLifecycleError("folder_lifecycle_invalid") from None
        if (
            not isinstance(raw, dict)
            or set(raw) != {"schema", "roots"}
            or raw.get("schema") != FOLDER_LIFECYCLE_SCHEMA
            or not isinstance(raw.get("roots"), list)
        ):
            raise FolderLifecycleError("folder_lifecycle_invalid")
        roots: dict[str, dict[str, LifecycleFile]] = {}
        try:
            for root_raw in cast(list[object], raw["roots"]):
                if not isinstance(root_raw, dict) or set(root_raw) != {"namespace", "files"}:
                    raise ValueError("invalid lifecycle root")
                namespace = _require_namespace(root_raw.get("namespace"))
                if namespace in roots:
                    raise ValueError("duplicate lifecycle namespace")
                files_raw = root_raw.get("files")
                if not isinstance(files_raw, list):
                    raise ValueError("invalid lifecycle files")
                files: dict[str, LifecycleFile] = {}
                for file_raw in cast(list[object], files_raw):
                    entry = LifecycleFile.from_dict(file_raw)
                    if entry.source_ref in files:
                        raise ValueError("duplicate lifecycle source_ref")
                    files[entry.source_ref] = entry
                roots[namespace] = files
        except (TypeError, ValueError):
            raise FolderLifecycleError("folder_lifecycle_invalid") from None
        return cls(path, roots)

    def files(self, namespace: str) -> tuple[LifecycleFile, ...]:
        _require_namespace(namespace)
        files = self._roots.get(namespace, {})
        return tuple(files[source_ref] for source_ref in sorted(files))

    def update(self, namespace: str, entry: LifecycleFile) -> bool:
        """Record one completed file state; return whether persisted bytes change."""
        _require_namespace(namespace)
        if not isinstance(entry, LifecycleFile):
            raise TypeError("entry must be LifecycleFile")
        files = self._roots.setdefault(namespace, {})
        if files.get(entry.source_ref) == entry:
            return False
        files[entry.source_ref] = entry
        return True

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": FOLDER_LIFECYCLE_SCHEMA,
            "roots": [
                {
                    "namespace": namespace,
                    "files": [
                        entry.to_dict()
                        for entry in (
                            self._roots[namespace][source_ref]
                            for source_ref in sorted(self._roots[namespace])
                        )
                    ],
                }
                for namespace in sorted(self._roots)
            ],
        }

    def save(self) -> None:
        encoded = (json.dumps(
            self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True,
        ) + "\n").encode("utf-8")
        temp_path: Path | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle = tempfile.NamedTemporaryFile(
                prefix="folder-lifecycle-", suffix=".tmp", dir=self.path.parent, delete=False,
            )
            temp_path = Path(handle.name)
            with handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.path)
        except OSError:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise FolderLifecycleError("folder_lifecycle_write_failed") from None


__all__ = [
    "FOLDER_LIFECYCLE_FILENAME",
    "FOLDER_LIFECYCLE_SCHEMA",
    "FolderLifecycle",
    "FolderLifecycleError",
    "LifecycleFile",
    "mark_unavailable",
]
