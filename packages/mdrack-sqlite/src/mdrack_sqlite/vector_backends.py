"""Explicit SQLite-local vector backend strategy and extension-schema contract."""

from __future__ import annotations

import hashlib
import sqlite3
import struct
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from mdrack_core.domain import EmbeddingSpaceRecord, VectorBranch, VectorRecord

_SCHEMA_OBJECT_TYPES = frozenset({"table", "view", "index", "trigger"})


@dataclass(frozen=True)
class SQLiteVectorCapabilities:
    """Declared vector-backend behavior used for explicit composition and routing."""

    backend_id: str
    exact: bool
    metrics: frozenset[str]
    supports_facets_any: bool
    supports_facets_all: bool
    supports_facets_none: bool
    supported_scope_fields: frozenset[str]
    supports_extensionless_open: bool
    supports_atomic_replace: bool
    supports_atomic_delete: bool

    def __post_init__(self) -> None:
        if not isinstance(self.backend_id, str) or not self.backend_id:
            raise ValueError("backend_id must be non-empty")
        if type(self.exact) is not bool:
            raise TypeError("exact must be bool")
        for name, values in (("metrics", self.metrics), ("supported_scope_fields", self.supported_scope_fields)):
            if not isinstance(values, frozenset) or any(not isinstance(value, str) or not value for value in values):
                raise TypeError(f"{name} must be a frozen set of non-empty strings")
        for name, value in (
            ("supports_facets_any", self.supports_facets_any),
            ("supports_facets_all", self.supports_facets_all),
            ("supports_facets_none", self.supports_facets_none),
            ("supports_extensionless_open", self.supports_extensionless_open),
            ("supports_atomic_replace", self.supports_atomic_replace),
            ("supports_atomic_delete", self.supports_atomic_delete),
        ):
            if type(value) is not bool:
                raise TypeError(f"{name} must be bool")


@dataclass(frozen=True)
class SQLiteVectorGenerationContext:
    """Catalog-owned lifecycle context; backends never own or commit its transaction."""

    schema_id: str
    create: bool

    def __post_init__(self) -> None:
        if not isinstance(self.schema_id, str) or not self.schema_id:
            raise ValueError("schema_id must be non-empty")
        if type(self.create) is not bool:
            raise TypeError("create must be bool")


@dataclass(frozen=True, order=True)
class SQLiteVectorSchemaObject:
    """One exact registered SQLite object, normalized for deterministic comparison."""

    object_type: str
    name: str
    table_name: str
    sql: str

    def __post_init__(self) -> None:
        if self.object_type not in _SCHEMA_OBJECT_TYPES:
            raise ValueError("object_type is unsupported")
        for field_name, value in (("name", self.name), ("table_name", self.table_name), ("sql", self.sql)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be non-empty")
        if self.name.startswith("sqlite_"):
            raise ValueError("registered objects must not use SQLite reserved names")
        object.__setattr__(self, "sql", " ".join(self.sql.split()))


def _manifest_digest(objects: Sequence[SQLiteVectorSchemaObject]) -> str:
    digest = hashlib.sha256()
    for item in objects:
        for field in (item.object_type, item.name, item.table_name, item.sql):
            encoded = field.encode("utf-8")
            digest.update(struct.pack(">Q", len(encoded)))
            digest.update(encoded)
    return digest.hexdigest()


@dataclass(frozen=True)
class SQLiteVectorSchemaExtension:
    """Exact namespace and object manifest owned by one explicitly injected backend."""

    namespace: str
    manifest: tuple[SQLiteVectorSchemaObject, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.namespace, str) or not self.namespace or self.namespace.startswith("sqlite_"):
            raise ValueError("namespace must be a non-reserved non-empty string")
        if not isinstance(self.manifest, tuple) or not self.manifest:
            raise ValueError("manifest must be a non-empty tuple")
        if any(not isinstance(item, SQLiteVectorSchemaObject) for item in self.manifest):
            raise TypeError("manifest entries must be SQLiteVectorSchemaObject")
        normalized = tuple(sorted(self.manifest))
        if len({item.name for item in normalized}) != len(normalized):
            raise ValueError("registered object names must be unique")
        if any(not item.name.startswith(self.namespace) for item in normalized):
            raise ValueError("registered object names must stay inside the extension namespace")
        object.__setattr__(self, "manifest", normalized)

    @property
    def manifest_digest(self) -> str:
        return _manifest_digest(self.manifest)


@runtime_checkable
class SQLiteVectorSearchResult(Protocol):
    """Search result shape shared by builtin and extension-backed exact search."""

    scored_rows: Sequence[tuple[float, sqlite3.Row]]
    all_candidates_zero_cosine: bool


@runtime_checkable
class SQLiteVectorBackend(Protocol):
    """SQLite-only vector strategy; the catalog retains transaction ownership."""

    backend_id: str

    def capabilities(self) -> SQLiteVectorCapabilities: ...

    def schema_extension(self) -> SQLiteVectorSchemaExtension | None: ...

    def initialize(self, connection: sqlite3.Connection, *, generation: SQLiteVectorGenerationContext) -> None: ...

    def replace_vectors(
        self,
        connection: sqlite3.Connection,
        *,
        resource_id: str,
        spaces: Sequence[EmbeddingSpaceRecord],
        vectors: Sequence[VectorRecord],
    ) -> None: ...

    def delete_vectors(self, connection: sqlite3.Connection, *, resource_id: str) -> None: ...

    def search(
        self,
        connection: sqlite3.Connection,
        *,
        branch: VectorBranch,
        dimensions: int,
        metric: str,
        metadata: Mapping[str, object],
        clauses: Sequence[str],
        params: Sequence[object],
    ) -> SQLiteVectorSearchResult: ...

    def verify(self, connection: sqlite3.Connection, *, generation: SQLiteVectorGenerationContext) -> None: ...


def require_sqlite_vector_backend(backend: object) -> SQLiteVectorBackend:
    """Validate an injected strategy without discovering or importing plugins."""
    if not isinstance(backend, SQLiteVectorBackend):
        raise TypeError("vector_backend must implement SQLiteVectorBackend")
    capabilities = backend.capabilities()
    if not isinstance(capabilities, SQLiteVectorCapabilities) or capabilities.backend_id != backend.backend_id:
        raise TypeError("vector_backend capabilities do not match backend_id")
    extension = backend.schema_extension()
    if extension is not None and not isinstance(extension, SQLiteVectorSchemaExtension):
        raise TypeError("vector_backend schema_extension must be SQLiteVectorSchemaExtension or None")
    return backend


def schema_extension_for_backend(backend: SQLiteVectorBackend | None) -> SQLiteVectorSchemaExtension | None:
    """Return the registered extension manifest for a supplied backend, if any."""
    return None if backend is None else require_sqlite_vector_backend(backend).schema_extension()


__all__ = [
    "SQLiteVectorBackend",
    "SQLiteVectorCapabilities",
    "SQLiteVectorGenerationContext",
    "SQLiteVectorSchemaExtension",
    "SQLiteVectorSchemaObject",
    "SQLiteVectorSearchResult",
    "require_sqlite_vector_backend",
    "schema_extension_for_backend",
]
