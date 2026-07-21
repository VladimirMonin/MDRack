"""Bounded prepared-resource manifest v1 codec and explicit-catalog facade."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from enum import StrEnum
from typing import Any, NoReturn, cast

from mdrack_core.application.indexing import CoreIndexingService
from mdrack_core.domain import (
    CoreError,
    EmbeddingSpaceRecord,
    ErrorCategory,
    Facet,
    Locator,
    PreparedResourceBatch,
    RepresentationRecord,
    ResourceFacet,
    ResourceRecord,
    SearchUnitRecord,
    VectorRecord,
)
from mdrack_core.ports.catalog import ResourceWritePort

MANIFEST_CONTRACT = "mdrack.prepared-resource"
MANIFEST_VERSION = 1
MAX_MANIFEST_BYTES = 16_777_216
MAX_JSON_DEPTH = 32
MAX_COLLECTION_ITEMS = 100_000
MAX_VECTOR_DIMENSIONS = 8_192
MAX_METADATA_BYTES = 65_536
MAX_TEXT_BYTES = 8_388_608


class ManifestErrorCode(StrEnum):
    """Stable, payload-free manifest failure categories."""

    INVALID_ENCODING = "invalid_encoding"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    INVALID_JSON = "invalid_json"
    DUPLICATE_KEY = "duplicate_key"
    NON_FINITE_NUMBER = "non_finite_number"
    DEPTH_LIMIT_EXCEEDED = "depth_limit_exceeded"
    UNSUPPORTED_CONTRACT = "unsupported_contract"
    UNSUPPORTED_VERSION = "unsupported_version"
    UNKNOWN_FIELD = "unknown_field"
    COLLECTION_LIMIT_EXCEEDED = "collection_limit_exceeded"
    VECTOR_LIMIT_EXCEEDED = "vector_limit_exceeded"
    METADATA_LIMIT_EXCEEDED = "metadata_limit_exceeded"
    TEXT_LIMIT_EXCEEDED = "text_limit_exceeded"
    INVALID_MANIFEST = "invalid_manifest"
    INVALID_GRAPH = "invalid_graph"


class ManifestError(Exception):
    """A safe manifest failure that never includes untrusted input."""

    def __init__(self, code: ManifestErrorCode) -> None:
        if not isinstance(code, ManifestErrorCode):
            raise ValueError("code must be a ManifestErrorCode")
        self.code = code
        super().__init__(code.value)


class _DuplicateKeyError(ValueError):
    pass


class _NonFiniteNumberError(ValueError):
    pass


_TOP_LEVEL_FIELDS = frozenset(
    {"contract", "version", "resource", "representations", "units", "spaces", "vectors", "facets"}
)
_RESOURCE_FIELDS = frozenset(
    {
        "resource_id",
        "resource_kind",
        "media_type",
        "source_namespace",
        "locator",
        "content_hash",
        "title",
        "metadata",
    }
)
_LOCATOR_FIELDS = frozenset({"kind", "payload"})
_REPRESENTATION_FIELDS = frozenset(
    {
        "representation_id",
        "resource_id",
        "representation_kind",
        "modality",
        "text",
        "language",
        "producer_fingerprint",
        "token_count",
        "token_count_kind",
        "metadata",
    }
)
_UNIT_FIELDS = frozenset(
    {
        "unit_id",
        "resource_id",
        "representation_id",
        "unit_kind",
        "modality",
        "text",
        "evidence_locator",
        "ordinal",
        "token_count",
        "token_count_kind",
        "metadata",
    }
)
_SPACE_FIELDS = frozenset({"space_id", "dimensions", "metric", "fingerprint", "metadata"})
_VECTOR_FIELDS = frozenset({"unit_id", "space_id", "vector"})
_FACET_ASSIGNMENT_FIELDS = frozenset(
    {"resource_id", "facet", "origin", "producer_fingerprint", "confidence"}
)
_FACET_FIELDS = frozenset({"namespace", "value"})


def _reject_constant(_value: str) -> NoReturn:
    raise _NonFiniteNumberError


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateKeyError
        value[key] = item
    return value


def _parse_json(payload: bytes) -> object:
    if len(payload) > MAX_MANIFEST_BYTES:
        raise ManifestError(ManifestErrorCode.PAYLOAD_TOO_LARGE)
    try:
        text = payload.decode("utf-8", "strict")
    except UnicodeDecodeError:
        raise ManifestError(ManifestErrorCode.INVALID_ENCODING) from None
    try:
        value: object = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except _DuplicateKeyError:
        raise ManifestError(ManifestErrorCode.DUPLICATE_KEY) from None
    except _NonFiniteNumberError:
        raise ManifestError(ManifestErrorCode.NON_FINITE_NUMBER) from None
    except (json.JSONDecodeError, RecursionError):
        raise ManifestError(ManifestErrorCode.INVALID_JSON) from None
    _validate_json_tree(value)
    return value


def _validate_json_tree(root: object) -> None:
    stack: list[tuple[object, int]] = [(root, 1)]
    while stack:
        value, depth = stack.pop()
        if depth > MAX_JSON_DEPTH:
            raise ManifestError(ManifestErrorCode.DEPTH_LIMIT_EXCEEDED)
        if isinstance(value, str):
            try:
                value.encode("utf-8", "strict")
            except UnicodeEncodeError:
                raise ManifestError(ManifestErrorCode.INVALID_ENCODING) from None
            continue
        if isinstance(value, Mapping):
            for key, item in value.items():
                try:
                    key.encode("utf-8", "strict")
                except UnicodeEncodeError:
                    raise ManifestError(ManifestErrorCode.INVALID_ENCODING) from None
                stack.append((item, depth + 1))
        elif isinstance(value, list):
            stack.extend((item, depth + 1) for item in value)


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ManifestError(ManifestErrorCode.INVALID_MANIFEST)
    return value


def _sequence(value: object) -> Sequence[object]:
    if not isinstance(value, list):
        raise ManifestError(ManifestErrorCode.INVALID_MANIFEST)
    return value


def _exact_fields(value: Mapping[str, object], allowed: frozenset[str], required: frozenset[str]) -> None:
    fields = frozenset(value)
    if not fields <= allowed:
        raise ManifestError(ManifestErrorCode.UNKNOWN_FIELD)
    if not required <= fields:
        raise ManifestError(ManifestErrorCode.INVALID_MANIFEST)


def _metadata(value: object) -> Mapping[str, object]:
    metadata = _mapping(value)
    try:
        encoded = json.dumps(
            metadata,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8", "strict")
    except (TypeError, ValueError, UnicodeEncodeError):
        raise ManifestError(ManifestErrorCode.INVALID_MANIFEST) from None
    if len(encoded) > MAX_METADATA_BYTES:
        raise ManifestError(ManifestErrorCode.METADATA_LIMIT_EXCEEDED)
    return metadata


def _optional_metadata(value: Mapping[str, object]) -> Mapping[str, object]:
    return _metadata(value.get("metadata", {}))


def _text(value: object) -> object:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ManifestError(ManifestErrorCode.INVALID_MANIFEST)
    try:
        size = len(value.encode("utf-8", "strict"))
    except UnicodeEncodeError:
        raise ManifestError(ManifestErrorCode.INVALID_ENCODING) from None
    if size > MAX_TEXT_BYTES:
        raise ManifestError(ManifestErrorCode.TEXT_LIMIT_EXCEEDED)
    return value


def _locator(value: object) -> Locator:
    item = _mapping(value)
    _exact_fields(item, _LOCATOR_FIELDS, frozenset({"kind", "payload"}))
    payload = _mapping(item["payload"])
    return Locator(kind=item["kind"], payload=payload)  # type: ignore[arg-type]


def _resource(value: object) -> ResourceRecord:
    item = _mapping(value)
    _exact_fields(
        item,
        _RESOURCE_FIELDS,
        frozenset({"resource_id", "resource_kind", "media_type", "source_namespace", "locator"}),
    )
    return ResourceRecord(
        resource_id=item["resource_id"],  # type: ignore[arg-type]
        resource_kind=item["resource_kind"],  # type: ignore[arg-type]
        media_type=item["media_type"],  # type: ignore[arg-type]
        source_namespace=item["source_namespace"],  # type: ignore[arg-type]
        locator=_locator(item["locator"]),
        content_hash=item.get("content_hash"),  # type: ignore[arg-type]
        title=_text(item.get("title")),  # type: ignore[arg-type]
        metadata=_optional_metadata(item),  # type: ignore[arg-type]
    )


def _representation(value: object) -> RepresentationRecord:
    item = _mapping(value)
    _exact_fields(
        item,
        _REPRESENTATION_FIELDS,
        frozenset({"representation_id", "resource_id", "representation_kind", "modality"}),
    )
    return RepresentationRecord(
        representation_id=item["representation_id"],  # type: ignore[arg-type]
        resource_id=item["resource_id"],  # type: ignore[arg-type]
        representation_kind=item["representation_kind"],  # type: ignore[arg-type]
        modality=item["modality"],  # type: ignore[arg-type]
        text=_text(item.get("text")),  # type: ignore[arg-type]
        language=item.get("language"),  # type: ignore[arg-type]
        producer_fingerprint=item.get("producer_fingerprint"),  # type: ignore[arg-type]
        token_count=item.get("token_count"),  # type: ignore[arg-type]
        token_count_kind=item.get("token_count_kind"),  # type: ignore[arg-type]
        metadata=_optional_metadata(item),  # type: ignore[arg-type]
    )


def _unit(value: object) -> SearchUnitRecord:
    item = _mapping(value)
    _exact_fields(
        item,
        _UNIT_FIELDS,
        frozenset(
            {
                "unit_id",
                "resource_id",
                "representation_id",
                "unit_kind",
                "modality",
                "evidence_locator",
                "ordinal",
            }
        ),
    )
    return SearchUnitRecord(
        unit_id=item["unit_id"],  # type: ignore[arg-type]
        resource_id=item["resource_id"],  # type: ignore[arg-type]
        representation_id=item["representation_id"],  # type: ignore[arg-type]
        unit_kind=item["unit_kind"],  # type: ignore[arg-type]
        modality=item["modality"],  # type: ignore[arg-type]
        text=_text(item.get("text")),  # type: ignore[arg-type]
        evidence_locator=_locator(item["evidence_locator"]),
        ordinal=item["ordinal"],  # type: ignore[arg-type]
        token_count=item.get("token_count"),  # type: ignore[arg-type]
        token_count_kind=item.get("token_count_kind"),  # type: ignore[arg-type]
        metadata=_optional_metadata(item),  # type: ignore[arg-type]
    )


def _space(value: object) -> EmbeddingSpaceRecord:
    item = _mapping(value)
    _exact_fields(item, _SPACE_FIELDS, frozenset({"space_id", "dimensions", "metric", "fingerprint"}))
    dimensions = item["dimensions"]
    if type(dimensions) is int and dimensions > MAX_VECTOR_DIMENSIONS:
        raise ManifestError(ManifestErrorCode.VECTOR_LIMIT_EXCEEDED)
    return EmbeddingSpaceRecord(
        space_id=item["space_id"],  # type: ignore[arg-type]
        dimensions=dimensions,  # type: ignore[arg-type]
        metric=item["metric"],  # type: ignore[arg-type]
        fingerprint=item["fingerprint"],  # type: ignore[arg-type]
        metadata=_optional_metadata(item),  # type: ignore[arg-type]
    )


def _vector(value: object) -> VectorRecord:
    item = _mapping(value)
    _exact_fields(item, _VECTOR_FIELDS, _VECTOR_FIELDS)
    vector = _sequence(item["vector"])
    if len(vector) > MAX_VECTOR_DIMENSIONS:
        raise ManifestError(ManifestErrorCode.VECTOR_LIMIT_EXCEEDED)
    return VectorRecord(
        unit_id=item["unit_id"],  # type: ignore[arg-type]
        space_id=item["space_id"],  # type: ignore[arg-type]
        vector=vector,  # type: ignore[arg-type]
    )


def _facet_assignment(value: object) -> ResourceFacet:
    item = _mapping(value)
    _exact_fields(
        item,
        _FACET_ASSIGNMENT_FIELDS,
        frozenset({"resource_id", "facet", "origin"}),
    )
    facet_item = _mapping(item["facet"])
    _exact_fields(facet_item, _FACET_FIELDS, _FACET_FIELDS)
    return ResourceFacet(
        resource_id=item["resource_id"],  # type: ignore[arg-type]
        facet=Facet(
            namespace=facet_item["namespace"],  # type: ignore[arg-type]
            value=facet_item["value"],  # type: ignore[arg-type]
        ),
        origin=item["origin"],  # type: ignore[arg-type]
        producer_fingerprint=item.get("producer_fingerprint"),  # type: ignore[arg-type]
        confidence=item.get("confidence"),  # type: ignore[arg-type]
    )


def _collection(root: Mapping[str, object], name: str) -> Sequence[object]:
    values = _sequence(root[name])
    if len(values) > MAX_COLLECTION_ITEMS:
        raise ManifestError(ManifestErrorCode.COLLECTION_LIMIT_EXCEEDED)
    return values


def decode_prepared_resource_manifest(payload: bytes) -> PreparedResourceBatch:
    """Decode one bounded manifest without resolving any locator or source."""
    if not isinstance(payload, bytes):
        raise ManifestError(ManifestErrorCode.INVALID_MANIFEST)
    root = _mapping(_parse_json(payload))
    _exact_fields(root, _TOP_LEVEL_FIELDS, _TOP_LEVEL_FIELDS)
    if root["contract"] != MANIFEST_CONTRACT:
        raise ManifestError(ManifestErrorCode.UNSUPPORTED_CONTRACT)
    if type(root["version"]) is not int or root["version"] != MANIFEST_VERSION:
        raise ManifestError(ManifestErrorCode.UNSUPPORTED_VERSION)
    try:
        return PreparedResourceBatch(
            resource=_resource(root["resource"]),
            representations=tuple(_representation(item) for item in _collection(root, "representations")),
            units=tuple(_unit(item) for item in _collection(root, "units")),
            spaces=tuple(_space(item) for item in _collection(root, "spaces")),
            vectors=tuple(_vector(item) for item in _collection(root, "vectors")),
            facets=tuple(_facet_assignment(item) for item in _collection(root, "facets")),
        )
    except ManifestError:
        raise
    except (TypeError, ValueError, OverflowError):
        raise ManifestError(ManifestErrorCode.INVALID_MANIFEST) from None


class _ValidationCatalog:
    """Discarding write port used to run the canonical graph validator."""

    def replace_resource(self, batch: PreparedResourceBatch) -> None:
        del batch

    def delete_resource(self, resource_id: str) -> None:
        del resource_id


def _plain_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain_json(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain_json(item) for item in value]
    return value


def _project_export_batch(
    batch: PreparedResourceBatch,
    *,
    include_vectors: bool,
    include_text: bool,
    redact_source_metadata: bool,
) -> PreparedResourceBatch:
    if not isinstance(batch, PreparedResourceBatch):
        raise ManifestError(ManifestErrorCode.INVALID_MANIFEST)
    resource = batch.resource
    if not include_text or redact_source_metadata:
        metadata = cast(dict[str, Any], _plain_json(resource.metadata))
        if redact_source_metadata:
            metadata.pop("source", None)
        resource = replace(
            resource,
            title=resource.title if include_text else None,
            metadata=metadata,
        )
    representations = tuple(
        replace(item, text=item.text if include_text else None)
        for item in batch.representations
    )
    units = tuple(
        replace(item, text=item.text if include_text else None)
        for item in batch.units
    )
    return PreparedResourceBatch(
        resource=resource,
        representations=representations,
        units=units,
        spaces=batch.spaces if include_vectors else (),
        vectors=batch.vectors if include_vectors else (),
        facets=batch.facets,
    )


def _manifest_value(batch: PreparedResourceBatch) -> dict[str, object]:
    resource = batch.resource
    return {
        "contract": MANIFEST_CONTRACT,
        "version": MANIFEST_VERSION,
        "resource": {
            "resource_id": resource.resource_id,
            "resource_kind": resource.resource_kind,
            "media_type": resource.media_type,
            "source_namespace": resource.source_namespace,
            "locator": {
                "kind": resource.locator.kind,
                "payload": _plain_json(resource.locator.payload),
            },
            "content_hash": resource.content_hash,
            "title": resource.title,
            "metadata": _plain_json(resource.metadata),
        },
        "representations": [
            {
                "representation_id": item.representation_id,
                "resource_id": item.resource_id,
                "representation_kind": item.representation_kind,
                "modality": item.modality,
                "text": item.text,
                "language": item.language,
                "producer_fingerprint": item.producer_fingerprint,
                "token_count": item.token_count,
                "token_count_kind": item.token_count_kind,
                "metadata": _plain_json(item.metadata),
            }
            for item in sorted(batch.representations, key=lambda value: value.representation_id)
        ],
        "units": [
            {
                "unit_id": item.unit_id,
                "resource_id": item.resource_id,
                "representation_id": item.representation_id,
                "unit_kind": item.unit_kind,
                "modality": item.modality,
                "text": item.text,
                "evidence_locator": {
                    "kind": item.evidence_locator.kind,
                    "payload": _plain_json(item.evidence_locator.payload),
                },
                "ordinal": item.ordinal,
                "token_count": item.token_count,
                "token_count_kind": item.token_count_kind,
                "metadata": _plain_json(item.metadata),
            }
            for item in sorted(
                batch.units,
                key=lambda value: (value.representation_id, value.ordinal, value.unit_id),
            )
        ],
        "spaces": [
            {
                "space_id": item.space_id,
                "dimensions": item.dimensions,
                "metric": item.metric,
                "fingerprint": item.fingerprint,
                "metadata": _plain_json(item.metadata),
            }
            for item in sorted(batch.spaces, key=lambda value: value.space_id)
        ],
        "vectors": [
            {
                "unit_id": item.unit_id,
                "space_id": item.space_id,
                "vector": list(item.vector),
            }
            for item in sorted(batch.vectors, key=lambda value: (value.unit_id, value.space_id))
        ],
        "facets": [
            {
                "resource_id": item.resource_id,
                "facet": {
                    "namespace": item.facet.namespace,
                    "value": item.facet.value,
                },
                "origin": item.origin,
                "producer_fingerprint": item.producer_fingerprint,
                "confidence": item.confidence,
            }
            for item in sorted(
                batch.facets,
                key=lambda value: (
                    value.facet.namespace,
                    value.facet.value,
                    value.origin,
                    value.producer_fingerprint is not None,
                    value.producer_fingerprint or "",
                ),
            )
        ],
    }


def encode_prepared_resource_manifest(
    batch: PreparedResourceBatch,
    *,
    include_vectors: bool = True,
    include_text: bool = True,
    redact_source_metadata: bool = False,
) -> bytes:
    """Encode one graph into the existing deterministic manifest-v1 grammar."""
    projected = _project_export_batch(
        batch,
        include_vectors=include_vectors,
        include_text=include_text,
        redact_source_metadata=redact_source_metadata,
    )
    try:
        CoreIndexingService(_ValidationCatalog()).index(projected)
        payload = json.dumps(
            _manifest_value(projected),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8", "strict")
    except CoreError:
        raise ManifestError(ManifestErrorCode.INVALID_GRAPH) from None
    except (TypeError, ValueError, OverflowError, UnicodeEncodeError):
        raise ManifestError(ManifestErrorCode.INVALID_MANIFEST) from None
    if len(payload) > MAX_MANIFEST_BYTES:
        raise ManifestError(ManifestErrorCode.PAYLOAD_TOO_LARGE)
    return payload


class PreparedResourceFacade:
    """Click-free prepared-resource indexing against one explicit catalog."""

    def __init__(self, catalog: ResourceWritePort) -> None:
        self._indexing = CoreIndexingService(catalog)

    def index_prepared_resource(self, batch: PreparedResourceBatch) -> None:
        self._indexing.index(batch)

    def export_manifest(
        self,
        batch: PreparedResourceBatch,
        *,
        include_vectors: bool = True,
        include_text: bool = True,
        redact_source_metadata: bool = False,
    ) -> bytes:
        return encode_prepared_resource_manifest(
            batch,
            include_vectors=include_vectors,
            include_text=include_text,
            redact_source_metadata=redact_source_metadata,
        )

    def import_manifest(self, payload: bytes) -> PreparedResourceBatch:
        batch = decode_prepared_resource_manifest(payload)
        try:
            self._indexing.index(batch)
        except CoreError as error:
            if error.category is ErrorCategory.VALIDATION:
                raise ManifestError(ManifestErrorCode.INVALID_GRAPH) from None
            raise
        return batch


def index_prepared_resource(catalog: ResourceWritePort, batch: PreparedResourceBatch) -> None:
    """Validate and index one already prepared graph into an explicit catalog."""
    PreparedResourceFacade(catalog).index_prepared_resource(batch)


def import_manifest(catalog: ResourceWritePort, payload: bytes) -> PreparedResourceBatch:
    """Decode, graph-validate, and index one manifest into an explicit catalog."""
    return PreparedResourceFacade(catalog).import_manifest(payload)


__all__ = (
    "MANIFEST_CONTRACT",
    "MANIFEST_VERSION",
    "MAX_COLLECTION_ITEMS",
    "MAX_JSON_DEPTH",
    "MAX_MANIFEST_BYTES",
    "MAX_METADATA_BYTES",
    "MAX_TEXT_BYTES",
    "MAX_VECTOR_DIMENSIONS",
    "ManifestError",
    "ManifestErrorCode",
    "PreparedResourceFacade",
    "decode_prepared_resource_manifest",
    "encode_prepared_resource_manifest",
    "import_manifest",
    "index_prepared_resource",
)
