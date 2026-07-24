"""Explicit clean-catalog facade for prepared-resource lifecycle operations."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import TracebackType
from typing import Literal, cast

from mdrack.application.manifest import (
    MAX_MANIFEST_BYTES,
    PreparedResourceFacade,
    decode_prepared_resource_manifest,
    encode_prepared_resource_manifest,
)
from mdrack.application.metadata_filters import MetadataFilters, compile_metadata_filters
from mdrack.application.metadata_projection import FACET_SCALAR_CODEC, MetadataScalar
from mdrack.application.vector_values import canonicalize_for_space
from mdrack_core.application.retrieval import RetrievalService
from mdrack_core.domain import (
    TARGET_RESOURCE,
    TARGET_UNIT,
    BranchScopeOverride,
    EmbeddingSpaceRecord,
    Facet,
    JSONValue,
    LexicalBranch,
    Locator,
    PreparedResourceBatch,
    RepresentationRecord,
    ResourceFacet,
    SearchRequest,
    SearchScope,
    SearchUnitRecord,
    VectorBranch,
    VectorRecord,
)
from mdrack_sqlite import SQLITE_CATALOG_SCHEMA_ID, SQLiteCatalog
from mdrack_sqlite.vector_codecs import decode_vector_payload


class ResourceCatalogErrorCode(StrEnum):
    """Stable, payload-free failures owned by the explicit catalog facade."""

    CATALOG_NOT_CLEAN = "catalog_not_clean"
    MANIFEST_UNAVAILABLE = "manifest_unavailable"
    MANIFEST_OUTPUT_UNAVAILABLE = "manifest_output_unavailable"
    RESOURCE_NOT_FOUND = "resource_not_found"
    OPERATION_FAILED = "operation_failed"


class ResourceCatalogError(RuntimeError):
    """A public catalog failure that never includes caller-controlled values."""

    def __init__(self, code: ResourceCatalogErrorCode) -> None:
        if not isinstance(code, ResourceCatalogErrorCode):
            raise TypeError("code must be a ResourceCatalogErrorCode")
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True)
class ResourceImportResult:
    resource_id: str
    resource_kind: str
    counts: dict[str, int]

    def to_dict(self) -> dict[str, object]:
        return {
            "resource_id": self.resource_id,
            "resource_kind": self.resource_kind,
            "counts": dict(self.counts),
        }


@dataclass(frozen=True)
class ResourceExportResult:
    resource_id: str
    counts: dict[str, int]
    byte_size: int
    digest: str

    def to_dict(self) -> dict[str, object]:
        return {
            "resource_id": self.resource_id,
            "counts": dict(self.counts),
            "byte_size": self.byte_size,
            "digest": self.digest,
        }


@dataclass(frozen=True)
class ResourceInspection:
    resource_id: str
    resource_kind: str
    media_type: str
    locator: dict[str, str]
    counts: dict[str, int]
    kinds: dict[str, list[str]]
    fingerprints: dict[str, str | list[str] | None]

    def to_dict(self) -> dict[str, object]:
        return {
            "resource_id": self.resource_id,
            "resource_kind": self.resource_kind,
            "media_type": self.media_type,
            "locator": dict(self.locator),
            "counts": dict(self.counts),
            "kinds": {key: list(values) for key, values in self.kinds.items()},
            "fingerprints": {
                key: list(value) if isinstance(value, list) else value
                for key, value in self.fingerprints.items()
            },
        }


@dataclass(frozen=True)
class ResourceDeleteResult:
    resource_id: str
    deleted: bool

    def to_dict(self) -> dict[str, object]:
        return {"resource_id": self.resource_id, "deleted": self.deleted}


@dataclass(frozen=True)
class ResourceSearchResult:
    """Safe provider-free result projection for the standalone catalog."""

    query: str | None
    target: str
    results: tuple[dict[str, object], ...]
    degraded: bool = False
    degraded_reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "query": self.query,
            "target": self.target,
            "results": [dict(item) for item in self.results],
            "total_count": len(self.results),
            "degraded": self.degraded,
            "degraded_reason": self.degraded_reason,
        }


@dataclass(frozen=True)
class FacetValue:
    """One explicitly requested catalog facet value."""

    namespace: str
    value: str
    resource_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "namespace": self.namespace,
            "value": self.value,
            "resource_count": self.resource_count,
        }


@dataclass(frozen=True)
class MetadataFacetValue:
    """One decoded source-projection facet in an intentional public payload."""

    namespace: str
    value: MetadataScalar
    value_type: str
    resource_count: int | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "namespace": self.namespace,
            "value": self.value,
            "value_type": self.value_type,
        }
        if self.resource_count is not None:
            payload["resource_count"] = self.resource_count
        return payload


@dataclass(frozen=True)
class MetadataInspection:
    """Exact metadata returned only by an explicit resource inspection call."""

    resource_id: str
    title: str | None
    source: dict[str, object]
    facets: tuple[MetadataFacetValue, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "resource_id": self.resource_id,
            "title": self.title,
            "source": _plain_json(self.source),
            "facets": [item.to_dict() for item in self.facets],
        }


def _safe_fingerprint(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8", "strict")).hexdigest()


def _plain_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain_json(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain_json(item) for item in value]
    return value


def _locator_fingerprint(payload: object) -> str:
    encoded = json.dumps(
        _plain_json(payload),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8", "strict")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _batch_counts(batch: PreparedResourceBatch) -> dict[str, int]:
    return {
        "representations": len(batch.representations),
        "units": len(batch.units),
        "spaces": len(batch.spaces),
        "vectors": len(batch.vectors),
        "facets": len(batch.facets),
    }


def _metadata_type(value: MetadataScalar) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if type(value) is int:
        return "integer"
    if isinstance(value, float):
        return "float"
    return "string"


class MetadataCatalogService:
    """Provider-free metadata inspection and resource-target lexical search."""

    def __init__(self, catalog: object) -> None:
        if not callable(getattr(catalog, "read_resource", None)):
            raise TypeError("catalog must support resource reads")
        if not callable(getattr(catalog, "search_lexical", None)):
            raise TypeError("catalog must support lexical search")
        if getattr(catalog, "connection", None) is None:
            raise TypeError("catalog must expose its verified SQLite connection")
        self._catalog = catalog

    def inspect(self, resource_id: str) -> MetadataInspection:
        try:
            resource = self._catalog.read_resource(resource_id)  # type: ignore[attr-defined]
            if resource is None:
                raise ResourceCatalogError(ResourceCatalogErrorCode.RESOURCE_NOT_FOUND)
            source = resource.metadata.get("source", {})
            if not isinstance(source, Mapping):
                raise ResourceCatalogError(ResourceCatalogErrorCode.OPERATION_FAILED)
            rows = self._catalog.connection.execute(  # type: ignore[attr-defined]
                "SELECT f.namespace,f.value FROM core_facets f "
                "JOIN core_resource_facets rf ON rf.facet_id=f.facet_id "
                "WHERE rf.resource_id=? AND rf.origin='source' "
                "ORDER BY f.namespace,f.value",
                (resource_id,),
            ).fetchall()
            facets = []
            for row in rows:
                value = FACET_SCALAR_CODEC.decode(str(row["value"]))
                facets.append(
                    MetadataFacetValue(
                        str(row["namespace"]),
                        value,
                        _metadata_type(value),
                    )
                )
            return MetadataInspection(
                resource_id=resource.resource_id,
                title=resource.title,
                source={str(key): _plain_json(value) for key, value in source.items()},
                facets=tuple(facets),
            )
        except ResourceCatalogError:
            raise
        except Exception:
            raise ResourceCatalogError(ResourceCatalogErrorCode.OPERATION_FAILED) from None

    def facets(self, *, namespace: str | None = None) -> tuple[MetadataFacetValue, ...]:
        if namespace is not None and (not isinstance(namespace, str) or not namespace):
            raise ValueError("namespace must be a non-empty string or None")
        query = (
            "SELECT f.namespace,f.value,COUNT(DISTINCT rf.resource_id) AS resource_count "
            "FROM core_facets f JOIN core_resource_facets rf ON rf.facet_id=f.facet_id "
            "WHERE rf.origin='source'"
        )
        params: tuple[object, ...] = ()
        if namespace is not None:
            query += " AND f.namespace=?"
            params = (namespace,)
        query += " GROUP BY f.namespace,f.value ORDER BY f.namespace,f.value"
        try:
            rows = self._catalog.connection.execute(query, params).fetchall()  # type: ignore[attr-defined]
            values = []
            for row in rows:
                value = FACET_SCALAR_CODEC.decode(str(row["value"]))
                values.append(
                    MetadataFacetValue(
                        str(row["namespace"]),
                        value,
                        _metadata_type(value),
                        int(row["resource_count"]),
                    )
                )
            return tuple(values)
        except Exception:
            raise ResourceCatalogError(ResourceCatalogErrorCode.OPERATION_FAILED) from None

    def search(
        self,
        query: str,
        *,
        metadata_filters: MetadataFilters | None = None,
        body_weight: float = 1.0,
        metadata_weight: float = 0.2,
        limit: int = 20,
    ) -> ResourceSearchResult:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be a positive integer")
        for value in (body_weight, metadata_weight):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("search weights must be finite non-negative numbers")
            if not math.isfinite(float(value)) or value < 0.0:
                raise ValueError("search weights must be finite non-negative numbers")
        if body_weight == 0.0 and metadata_weight == 0.0:
            raise ValueError("at least one search weight must be positive")

        branches = []
        candidate_limit = max(limit * 10, 100)
        if body_weight > 0.0:
            branches.append(
                LexicalBranch(
                    "body",
                    query,
                    weight=float(body_weight),
                    candidate_limit=candidate_limit,
                    scope_override=BranchScopeOverride(
                        representation_kinds=("retrieval_text",),
                        unit_kinds=("text_chunk",),
                    ),
                )
            )
        if metadata_weight > 0.0:
            branches.append(
                LexicalBranch(
                    "metadata",
                    query,
                    weight=float(metadata_weight),
                    candidate_limit=candidate_limit,
                    scope_override=BranchScopeOverride(
                        representation_kinds=("metadata_text",),
                        unit_kinds=("whole_resource",),
                    ),
                )
            )
        scope = compile_metadata_filters(metadata_filters or MetadataFilters())
        result = RetrievalService(self._catalog).search(  # type: ignore[arg-type]
            SearchRequest(
                lexical_branches=tuple(branches),
                vector_branches=(),
                scope=scope,
                target=TARGET_RESOURCE,
                limit=limit,
            )
        )
        reason = result.degradations[0].category.value if result.degradations else None
        return ResourceSearchResult(
            query=query,
            target=TARGET_RESOURCE,
            results=tuple(
                {
                    "logical_id": item.logical_id,
                    "resource_id": item.resource_id,
                    "unit_id": item.unit_id,
                    "score": item.score,
                    "rank": item.rank,
                }
                for item in result.items
            ),
            degraded=reason is not None,
            degraded_reason=reason,
        )


def _decode_json_mapping(value: object) -> Mapping[str, JSONValue]:
    if not isinstance(value, str):
        raise ValueError("stored metadata must be text")
    decoded = json.loads(value)
    if not isinstance(decoded, dict):
        raise ValueError("stored metadata must be an object")
    return cast(dict[str, JSONValue], decoded)


def _decode_json_vector(value: object) -> tuple[float, ...]:
    if not isinstance(value, bytes):
        raise ValueError("stored vector must be bytes")
    decoded = json.loads(value.decode("utf-8", "strict"))
    if not isinstance(decoded, list):
        raise ValueError("stored vector must be an array")
    return tuple(decoded)


def _write_complete_payload(stream: object, payload: bytes) -> None:
    """Write all payload bytes, including when the raw stream short-writes."""
    view = memoryview(payload)
    offset = 0
    while offset < len(view):
        written = stream.write(view[offset:])  # type: ignore[attr-defined]
        if type(written) is not int or written <= 0 or written > len(view) - offset:
            raise OSError("manifest write did not make progress")
        offset += written
    stream.flush()  # type: ignore[attr-defined]
    os.fsync(stream.fileno())  # type: ignore[attr-defined]


def _remove_owned_temporary(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


class PreparedResourceExportService:
    """Reconstruct one persisted graph and encode the existing manifest-v1 contract."""

    def __init__(self, catalog: object) -> None:
        if not callable(getattr(catalog, "read_resource", None)):
            raise TypeError("catalog must support resource reads")
        if getattr(catalog, "connection", None) is None:
            raise TypeError("catalog must expose its verified SQLite connection")
        self._catalog = catalog

    def export_batch(self, resource_id: str) -> PreparedResourceBatch:
        connection = self._catalog.connection  # type: ignore[attr-defined]
        owns_transaction = not connection.in_transaction
        try:
            if owns_transaction:
                connection.execute("BEGIN")
            resource = self._catalog.read_resource(resource_id)  # type: ignore[attr-defined]
            if resource is None:
                raise ResourceCatalogError(ResourceCatalogErrorCode.RESOURCE_NOT_FOUND)
            representation_rows = connection.execute(
                "SELECT * FROM core_representations WHERE resource_id=? "
                "ORDER BY representation_id",
                (resource_id,),
            ).fetchall()
            unit_rows = connection.execute(
                "SELECT * FROM core_search_units WHERE resource_id=? "
                "ORDER BY representation_id,ordinal,unit_id",
                (resource_id,),
            ).fetchall()
            space_rows = connection.execute(
                "SELECT DISTINCT s.* FROM core_embedding_spaces s "
                "JOIN core_unit_embeddings e USING(space_id) "
                "JOIN core_search_units u USING(unit_id) "
                "WHERE u.resource_id=? ORDER BY s.space_id",
                (resource_id,),
            ).fetchall()
            vector_rows = connection.execute(
                "SELECT e.unit_id,e.space_id,e.embedding,s.dimensions,s.metadata_json "
                "FROM core_unit_embeddings e JOIN core_embedding_spaces s USING(space_id) "
                "JOIN core_search_units u USING(unit_id) "
                "WHERE u.resource_id=? ORDER BY e.unit_id,e.space_id",
                (resource_id,),
            ).fetchall()
            facet_rows = connection.execute(
                "SELECT f.namespace,f.value,rf.origin,rf.producer_is_null,"
                "rf.producer_value,rf.confidence_json "
                "FROM core_resource_facets rf JOIN core_facets f USING(facet_id) "
                "WHERE rf.resource_id=? "
                "ORDER BY f.namespace,f.value,rf.origin,rf.producer_is_null,rf.producer_value",
                (resource_id,),
            ).fetchall()
            batch = PreparedResourceBatch(
                resource=resource,
                representations=tuple(
                    RepresentationRecord(
                        representation_id=row["representation_id"],
                        resource_id=row["resource_id"],
                        representation_kind=row["representation_kind"],
                        modality=row["modality"],
                        text=row["text_content"],
                        language=row["language"],
                        producer_fingerprint=row["producer_fingerprint"],
                        token_count=row["token_count"],
                        token_count_kind=row["token_count_kind"],
                        metadata=_decode_json_mapping(row["metadata_json"]),
                    )
                    for row in representation_rows
                ),
                units=tuple(
                    SearchUnitRecord(
                        unit_id=row["unit_id"],
                        resource_id=row["resource_id"],
                        representation_id=row["representation_id"],
                        unit_kind=row["unit_kind"],
                        modality=row["modality"],
                        text=row["text_content"],
                        evidence_locator=Locator(
                            row["evidence_locator_kind"],
                            _decode_json_mapping(row["evidence_locator_json"]),
                        ),
                        ordinal=row["ordinal"],
                        token_count=row["token_count"],
                        token_count_kind=row["token_count_kind"],
                        metadata=_decode_json_mapping(row["metadata_json"]),
                    )
                    for row in unit_rows
                ),
                spaces=tuple(
                    EmbeddingSpaceRecord(
                        space_id=row["space_id"],
                        dimensions=row["dimensions"],
                        metric=row["metric"],
                        fingerprint=row["fingerprint"],
                        metadata=_decode_json_mapping(row["metadata_json"]),
                    )
                    for row in space_rows
                ),
                vectors=tuple(
                    VectorRecord(
                        unit_id=row["unit_id"],
                        space_id=row["space_id"],
                        vector=decode_vector_payload(
                            row["embedding"],
                            dimensions=row["dimensions"],
                            metadata=_decode_json_mapping(row["metadata_json"]),
                        ),
                    )
                    for row in vector_rows
                ),
                facets=tuple(
                    ResourceFacet(
                        resource_id=resource_id,
                        facet=Facet(row["namespace"], row["value"]),
                        origin=row["origin"],
                        producer_fingerprint=(
                            None if row["producer_is_null"] == 1 else row["producer_value"]
                        ),
                        confidence=(
                            None
                            if row["confidence_json"] is None
                            else float(json.loads(row["confidence_json"].decode("utf-8", "strict")))
                        ),
                    )
                    for row in facet_rows
                ),
            )
            if owns_transaction:
                connection.rollback()
            return batch
        except ResourceCatalogError:
            if owns_transaction and connection.in_transaction:
                connection.rollback()
            raise
        except Exception:
            if owns_transaction and connection.in_transaction:
                connection.rollback()
            raise ResourceCatalogError(ResourceCatalogErrorCode.OPERATION_FAILED) from None

    def export_bytes(
        self,
        resource_id: str,
        *,
        include_vectors: bool = True,
        include_text: bool = True,
        redact_source_metadata: bool = False,
    ) -> bytes:
        return encode_prepared_resource_manifest(
            self.export_batch(resource_id),
            include_vectors=include_vectors,
            include_text=include_text,
            redact_source_metadata=redact_source_metadata,
        )

    def export_file(
        self,
        resource_id: str,
        output_path: str | Path,
        *,
        include_vectors: bool = True,
        include_text: bool = True,
        redact_source_metadata: bool = False,
    ) -> ResourceExportResult:
        batch = self.export_batch(resource_id)
        payload = encode_prepared_resource_manifest(
            batch,
            include_vectors=include_vectors,
            include_text=include_text,
            redact_source_metadata=redact_source_metadata,
        )
        exported_batch = decode_prepared_resource_manifest(payload)
        file_descriptor: int | None = None
        temporary_path: Path | None = None
        try:
            destination = Path(output_path)
            file_descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{destination.name}.",
                suffix=".tmp",
                dir=destination.parent,
            )
            temporary_path = Path(temporary_name)
            with os.fdopen(file_descriptor, "wb") as stream:
                _write_complete_payload(stream, payload)
            file_descriptor = None
            os.link(temporary_path, destination)
            temporary_path.unlink()
            temporary_path = None
        except (OSError, TypeError, ValueError):
            if file_descriptor is not None:
                try:
                    os.close(file_descriptor)
                except OSError:
                    pass
            _remove_owned_temporary(temporary_path)
            raise ResourceCatalogError(
                ResourceCatalogErrorCode.MANIFEST_OUTPUT_UNAVAILABLE
            ) from None
        except BaseException:
            if file_descriptor is not None:
                try:
                    os.close(file_descriptor)
                except OSError:
                    pass
            _remove_owned_temporary(temporary_path)
            raise
        return ResourceExportResult(
            resource_id=resource_id,
            counts=_batch_counts(exported_batch),
            byte_size=len(payload),
            digest="sha256:" + hashlib.sha256(payload).hexdigest(),
        )


class PreparedResourceCatalog:
    """Provider-free lifecycle facade for one explicit clean SQLite catalog path."""

    def __init__(self, catalog: SQLiteCatalog) -> None:
        if not isinstance(catalog, SQLiteCatalog):
            raise TypeError("catalog must be a SQLiteCatalog")
        if catalog.schema_id != SQLITE_CATALOG_SCHEMA_ID:
            raise ResourceCatalogError(ResourceCatalogErrorCode.CATALOG_NOT_CLEAN)
        self._catalog = catalog
        self._manifest = PreparedResourceFacade(catalog)

    @classmethod
    def open(cls, database_path: str | Path) -> PreparedResourceCatalog:
        catalog = SQLiteCatalog.open(database_path)
        try:
            return cls(catalog)
        except Exception:
            catalog.close()
            raise

    def import_bytes(self, payload: bytes) -> ResourceImportResult:
        batch = self._manifest.import_manifest(payload)
        return ResourceImportResult(
            resource_id=batch.resource.resource_id,
            resource_kind=batch.resource.resource_kind,
            counts=_batch_counts(batch),
        )

    def import_file(self, manifest_path: str | Path) -> ResourceImportResult:
        try:
            with Path(manifest_path).open("rb") as stream:
                payload = stream.read(MAX_MANIFEST_BYTES + 1)
        except (OSError, TypeError, ValueError):
            raise ResourceCatalogError(ResourceCatalogErrorCode.MANIFEST_UNAVAILABLE) from None
        return self.import_bytes(payload)

    def export_batch(self, resource_id: str) -> PreparedResourceBatch:
        return PreparedResourceExportService(self._catalog).export_batch(resource_id)

    def export_bytes(
        self,
        resource_id: str,
        *,
        include_vectors: bool = True,
        include_text: bool = True,
        redact_source_metadata: bool = False,
    ) -> bytes:
        return PreparedResourceExportService(self._catalog).export_bytes(
            resource_id,
            include_vectors=include_vectors,
            include_text=include_text,
            redact_source_metadata=redact_source_metadata,
        )

    def export_file(
        self,
        resource_id: str,
        output_path: str | Path,
        *,
        include_vectors: bool = True,
        include_text: bool = True,
        redact_source_metadata: bool = False,
    ) -> ResourceExportResult:
        return PreparedResourceExportService(self._catalog).export_file(
            resource_id,
            output_path,
            include_vectors=include_vectors,
            include_text=include_text,
            redact_source_metadata=redact_source_metadata,
        )

    def inspect(self, resource_id: str) -> ResourceInspection:
        """Inspect redacted aggregate details for one resource."""
        try:
            resource = self._catalog.read_resource(resource_id)
            if resource is None:
                raise ResourceCatalogError(ResourceCatalogErrorCode.RESOURCE_NOT_FOUND)
            connection = self._catalog.connection
            representations = connection.execute(
                "SELECT representation_kind,modality,producer_fingerprint "
                "FROM core_representations WHERE resource_id=? ORDER BY representation_id",
                (resource_id,),
            ).fetchall()
            units = connection.execute(
                "SELECT unit_kind,modality FROM core_search_units "
                "WHERE resource_id=? ORDER BY unit_id",
                (resource_id,),
            ).fetchall()
            spaces = connection.execute(
                "SELECT DISTINCT s.fingerprint FROM core_embedding_spaces s "
                "JOIN core_unit_embeddings e ON e.space_id=s.space_id "
                "JOIN core_search_units u ON u.unit_id=e.unit_id "
                "WHERE u.resource_id=? ORDER BY s.fingerprint",
                (resource_id,),
            ).fetchall()
            vector_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM core_unit_embeddings e "
                    "JOIN core_search_units u ON u.unit_id=e.unit_id WHERE u.resource_id=?",
                    (resource_id,),
                ).fetchone()[0]
            )
            facet_rows = connection.execute(
                "SELECT producer_value FROM core_resource_facets "
                "WHERE resource_id=? ORDER BY facet_id,origin,producer_value",
                (resource_id,),
            ).fetchall()
            producers = {
                row["producer_fingerprint"]
                for row in representations
                if row["producer_fingerprint"] is not None
            }
            producers.update(
                row["producer_value"] for row in facet_rows if row["producer_value"] is not None
            )
            content_fingerprint = (
                _safe_fingerprint(resource.content_hash)
                if resource.content_hash is not None
                else None
            )
            return ResourceInspection(
                resource_id=resource.resource_id,
                resource_kind=resource.resource_kind,
                media_type=resource.media_type,
                locator={
                    "kind": resource.locator.kind,
                    "fingerprint": _locator_fingerprint(resource.locator.payload),
                },
                counts={
                    "representations": len(representations),
                    "units": len(units),
                    "spaces": len(spaces),
                    "vectors": vector_count,
                    "facets": len(facet_rows),
                },
                kinds={
                    "representations": sorted({row["representation_kind"] for row in representations}),
                    "modalities": sorted(
                        {row["modality"] for row in representations}
                        | {row["modality"] for row in units}
                    ),
                    "units": sorted({row["unit_kind"] for row in units}),
                },
                fingerprints={
                    "content": content_fingerprint,
                    "producers": sorted(_safe_fingerprint(value) for value in producers),
                    "spaces": sorted(_safe_fingerprint(row["fingerprint"]) for row in spaces),
                },
            )
        except ResourceCatalogError:
            raise
        except Exception:
            raise ResourceCatalogError(ResourceCatalogErrorCode.OPERATION_FAILED) from None

    def delete(self, resource_id: str) -> ResourceDeleteResult:
        try:
            existed = self._catalog.read_resource(resource_id) is not None
            if existed:
                self._catalog.delete_resource(resource_id)
            return ResourceDeleteResult(resource_id=resource_id, deleted=existed)
        except Exception:
            raise ResourceCatalogError(ResourceCatalogErrorCode.OPERATION_FAILED) from None

    def search_text(
        self,
        query: str,
        *,
        scope: SearchScope | None = None,
        target: str = TARGET_UNIT,
        limit: int = 20,
    ) -> ResourceSearchResult:
        """Search indexed text without a provider or source access."""
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        if target not in {TARGET_UNIT, TARGET_RESOURCE}:
            raise ValueError("target must be unit or resource")
        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be a positive integer")
        request = SearchRequest(
            lexical_branches=(LexicalBranch("text", query, candidate_limit=max(limit, 100)),),
            vector_branches=(),
            scope=scope or SearchScope(),
            target=target,
            limit=limit,
        )
        result = RetrievalService(self._catalog).search(request)
        reason = result.degradations[0].category.value if result.degradations else None
        return ResourceSearchResult(
            query=query,
            target=target,
            results=tuple(
                {
                    "logical_id": item.logical_id,
                    "resource_id": item.resource_id,
                    "unit_id": item.unit_id,
                    "score": item.score,
                    "rank": item.rank,
                }
                for item in result.items
            ),
            degraded=reason is not None,
            degraded_reason=reason,
        )

    def search_vector(
        self,
        vector: tuple[float, ...],
        space_id: str,
        *,
        scope: SearchScope | None = None,
        target: str = TARGET_UNIT,
        limit: int = 20,
    ) -> ResourceSearchResult:
        """Search using a caller-owned vector; no embedding provider is called."""
        if target not in {TARGET_UNIT, TARGET_RESOURCE}:
            raise ValueError("target must be unit or resource")
        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be a positive integer")
        row = self._catalog.connection.execute(
            "SELECT space_id,dimensions,metric,fingerprint,metadata_json "
            "FROM core_embedding_spaces WHERE space_id=?",
            (space_id,),
        ).fetchone()
        if row is not None:
            space = EmbeddingSpaceRecord(
                space_id=str(row["space_id"]),
                dimensions=int(row["dimensions"]),
                metric=str(row["metric"]),
                fingerprint=str(row["fingerprint"]),
                metadata=_decode_json_mapping(row["metadata_json"]),
            )
            vector = canonicalize_for_space(vector, space)
        request = SearchRequest(
            lexical_branches=(),
            vector_branches=(VectorBranch("semantic", space_id, vector, candidate_limit=max(limit, 100)),),
            scope=scope or SearchScope(),
            target=target,
            limit=limit,
        )
        result = RetrievalService(self._catalog).search(request)
        reason = result.degradations[0].category.value if result.degradations else None
        return ResourceSearchResult(
            query=None,
            target=target,
            results=tuple(
                {
                    "logical_id": item.logical_id,
                    "resource_id": item.resource_id,
                    "unit_id": item.unit_id,
                    "score": item.score,
                    "rank": item.rank,
                }
                for item in result.items
            ),
            degraded=reason is not None,
            degraded_reason=reason,
        )

    def facets(self, *, namespace: str | None = None) -> tuple[FacetValue, ...]:
        """List explicit catalog facets in deterministic order."""
        if namespace is not None and (not isinstance(namespace, str) or not namespace):
            raise ValueError("namespace must be a non-empty string or None")
        query = (
            "SELECT f.namespace, f.value, COUNT(DISTINCT rf.resource_id) AS resource_count "
            "FROM core_facets f JOIN core_resource_facets rf ON rf.facet_id=f.facet_id"
        )
        params: tuple[object, ...] = ()
        if namespace is not None:
            query += " WHERE f.namespace=?"
            params = (namespace,)
        query += " GROUP BY f.namespace, f.value ORDER BY f.namespace, f.value"
        rows = self._catalog.connection.execute(query, params).fetchall()
        return tuple(FacetValue(row["namespace"], row["value"], int(row["resource_count"])) for row in rows)

    def metadata(self, resource_id: str) -> MetadataInspection:
        return MetadataCatalogService(self._catalog).inspect(resource_id)

    def metadata_facets(
        self,
        *,
        namespace: str | None = None,
    ) -> tuple[MetadataFacetValue, ...]:
        return MetadataCatalogService(self._catalog).facets(namespace=namespace)

    def search_metadata(
        self,
        query: str,
        *,
        metadata_filters: MetadataFilters | None = None,
        body_weight: float = 1.0,
        metadata_weight: float = 0.2,
        limit: int = 20,
    ) -> ResourceSearchResult:
        return MetadataCatalogService(self._catalog).search(
            query,
            metadata_filters=metadata_filters,
            body_weight=body_weight,
            metadata_weight=metadata_weight,
            limit=limit,
        )

    def close(self) -> None:
        self._catalog.close()

    def __enter__(self) -> PreparedResourceCatalog:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        self.close()
        return False


__all__ = [
    "FacetValue",
    "MetadataCatalogService",
    "MetadataFacetValue",
    "MetadataInspection",
    "PreparedResourceCatalog",
    "PreparedResourceExportService",
    "ResourceCatalogError",
    "ResourceCatalogErrorCode",
    "ResourceDeleteResult",
    "ResourceExportResult",
    "ResourceImportResult",
    "ResourceInspection",
    "ResourceSearchResult",
]
