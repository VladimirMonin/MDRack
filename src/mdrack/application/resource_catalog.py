"""Explicit clean-catalog facade for prepared-resource lifecycle operations."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import TracebackType
from typing import Literal

from mdrack.application.manifest import MAX_MANIFEST_BYTES, PreparedResourceFacade
from mdrack_core.domain import PreparedResourceBatch
from mdrack_sqlite import SQLITE_CATALOG_SCHEMA_ID, SQLiteCatalog


class ResourceCatalogErrorCode(StrEnum):
    """Stable, payload-free failures owned by the explicit catalog facade."""

    CATALOG_NOT_CLEAN = "catalog_not_clean"
    MANIFEST_UNAVAILABLE = "manifest_unavailable"
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

    def inspect(self, resource_id: str) -> ResourceInspection:
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
    "PreparedResourceCatalog",
    "ResourceCatalogError",
    "ResourceCatalogErrorCode",
    "ResourceDeleteResult",
    "ResourceImportResult",
    "ResourceInspection",
]
