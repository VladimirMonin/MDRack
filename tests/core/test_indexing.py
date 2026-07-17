from __future__ import annotations

import logging
import math
from dataclasses import replace

import pytest
from fakes.memory_store import MemoryCatalog

from mdrack_core.application.indexing import CoreIndexingService
from mdrack_core.domain import (
    CatalogExecutionError,
    CoreError,
    EmbeddingSpaceRecord,
    ErrorCategory,
    Facet,
    Locator,
    PreparedResourceBatch,
    RepresentationRecord,
    ResourceFacet,
    ResourceRecord,
    SearchScope,
    SearchUnitRecord,
    VectorRecord,
)
from mdrack_core.domain.common import canonical_json


class EncodeBypass(str):
    """Adversarial string whose instance encoder lies about the exact value."""

    def encode(self, encoding: str = "utf-8", errors: str = "strict") -> bytes:
        return b"bypass"


def _resource(
    resource_id: str = "resource-caller-supplied",
    *,
    content_hash: str = "sha256:shared-content",
    title: str = "PRIVATE_CONTENT_SENTINEL",
) -> ResourceRecord:
    return ResourceRecord(
        resource_id=resource_id,
        resource_kind="document",
        media_type="text/markdown",
        source_namespace="PRIVATE_ROOT_SENTINEL",
        locator=Locator(
            "file",
            {"relative_path": "/private/root/PRIVATE_PATH_SENTINEL.md"},
        ),
        content_hash=content_hash,
        title=title,
        metadata={"secret": "PRIVATE_METADATA_SENTINEL"},
    )


def _representation(
    representation_id: str = "representation-caller-supplied",
    *,
    resource_id: str = "resource-caller-supplied",
    text: str | None = "PRIVATE_CONTENT_SENTINEL",
    modality: str = "text",
) -> RepresentationRecord:
    return RepresentationRecord(
        representation_id=representation_id,
        resource_id=resource_id,
        representation_kind="retrieval_text",
        modality=modality,
        text=text,
        token_count=3,
        token_count_kind="exact",
    )


def _unit(
    unit_id: str = "unit-caller-supplied",
    *,
    resource_id: str = "resource-caller-supplied",
    representation_id: str = "representation-caller-supplied",
    text: str | None = "PRIVATE_CONTENT_SENTINEL",
    modality: str = "text",
    ordinal: int = 0,
) -> SearchUnitRecord:
    return SearchUnitRecord(
        unit_id=unit_id,
        resource_id=resource_id,
        representation_id=representation_id,
        unit_kind="whole_resource",
        modality=modality,
        text=text,
        evidence_locator=Locator("whole_resource", {"ordinal": ordinal}),
        ordinal=ordinal,
        token_count=3,
        token_count_kind="estimated",
    )


def _space(
    space_id: str = "space-caller-supplied",
    *,
    dimensions: int = 2,
) -> EmbeddingSpaceRecord:
    return EmbeddingSpaceRecord(
        space_id=space_id,
        dimensions=dimensions,
        metric="cosine",
        fingerprint="PRIVATE_PROVIDER_FINGERPRINT_SENTINEL",
    )


def _vector(
    unit_id: str = "unit-caller-supplied",
    *,
    space_id: str = "space-caller-supplied",
    vector: tuple[float, ...] = (0.1, 0.2),
) -> VectorRecord:
    return VectorRecord(unit_id=unit_id, space_id=space_id, vector=vector)


def _facet(
    *,
    resource_id: str = "resource-caller-supplied",
    value: str = "PRIVATE_FACET_SENTINEL",
) -> ResourceFacet:
    return ResourceFacet(
        resource_id=resource_id,
        facet=Facet("topic", value),
        origin="user",
    )


def _batch(**changes: object) -> PreparedResourceBatch:
    values: dict[str, object] = {
        "resource": _resource(),
        "representations": (_representation(),),
        "units": (_unit(),),
        "spaces": (_space(),),
        "vectors": (_vector(),),
        "facets": (_facet(),),
    }
    values.update(changes)
    return PreparedResourceBatch(**values)  # type: ignore[arg-type]


def test_indexes_exact_caller_prepared_graph_once_and_emits_safe_lifecycle(
    caplog: pytest.LogCaptureFixture,
) -> None:
    catalog = MemoryCatalog()
    service = CoreIndexingService(catalog)
    batch = _batch()

    with caplog.at_level(logging.INFO, logger="mdrack_core.application.indexing"):
        service.index(batch)

    assert catalog.replace_calls == [batch]
    assert catalog.batch(batch.resource.resource_id) is batch
    assert [record.message.split(" ", 1)[0] for record in caplog.records] == [
        "core.index.started",
        "core.index.validated",
        "core.index.completed",
    ]
    output = caplog.text
    assert '"representation_count":1' in output
    assert '"unit_count":1' in output
    assert '"vector_count":1' in output
    assert '"space_count":1' in output
    assert '"facet_count":1' in output
    for sentinel in (
        "PRIVATE_CONTENT_SENTINEL",
        "PRIVATE_ROOT_SENTINEL",
        "PRIVATE_PATH_SENTINEL",
        "PRIVATE_METADATA_SENTINEL",
        "PRIVATE_FACET_SENTINEL",
        "PRIVATE_PROVIDER_FINGERPRINT_SENTINEL",
        "resource-caller-supplied",
        "representation-caller-supplied",
        "unit-caller-supplied",
        "space-caller-supplied",
        "[0.1, 0.2]",
    ):
        assert sentinel not in output


@pytest.mark.parametrize(
    "batch",
    [
        _batch(representations=()),
        _batch(units=()),
        _batch(
            representations=(
                _representation(),
                _representation("orphan-representation", text=None),
            ),
        ),
        _batch(units=(_unit(text="   "),), vectors=()),
    ],
    ids=(
        "resource-without-representation",
        "resource-without-unit",
        "representation-without-unit",
        "unit-without-text-or-vector",
    ),
)
def test_rejects_graphs_without_retrievable_representation_or_unit_content(
    batch: PreparedResourceBatch,
) -> None:
    catalog = MemoryCatalog()

    with pytest.raises(CoreError) as caught:
        CoreIndexingService(catalog).index(batch)

    assert caught.value.category is ErrorCategory.VALIDATION
    assert catalog.replace_calls == []


@pytest.mark.parametrize(
    "batch",
    [
        _batch(representations=(_representation(), _representation())),
        _batch(units=(_unit(), _unit())),
        _batch(spaces=(_space(), _space())),
        _batch(vectors=(_vector(), _vector())),
        _batch(facets=(_facet(), _facet())),
    ],
    ids=("representation-id", "unit-id", "space-id", "unit-space-vector", "facet-assignment"),
)
def test_rejects_every_duplicate_identity_before_storage(batch: PreparedResourceBatch) -> None:
    catalog = MemoryCatalog()

    with pytest.raises(CoreError) as caught:
        CoreIndexingService(catalog).index(batch)

    assert caught.value.category is ErrorCategory.VALIDATION
    assert catalog.replace_calls == []


@pytest.mark.parametrize(
    "batch",
    [
        _batch(representations=(_representation(resource_id="foreign-resource"),)),
        _batch(units=(_unit(resource_id="foreign-resource"),)),
        _batch(facets=(_facet(resource_id="foreign-resource"),)),
        _batch(units=(_unit(representation_id="missing-representation"),)),
        _batch(units=(_unit(modality="image"),)),
        _batch(vectors=(_vector("missing-unit"),)),
        _batch(vectors=(_vector(space_id="missing-space"),)),
    ],
    ids=(
        "representation-owner",
        "unit-owner",
        "facet-owner",
        "unit-representation-link",
        "unit-representation-modality",
        "vector-unit-link",
        "vector-space-link",
    ),
)
def test_rejects_foreign_ownership_and_broken_relationships(batch: PreparedResourceBatch) -> None:
    catalog = MemoryCatalog()

    with pytest.raises(CoreError) as caught:
        CoreIndexingService(catalog).index(batch)

    assert caught.value.category is ErrorCategory.VALIDATION
    assert catalog.replace_calls == []


def test_rejects_wrong_vector_dimensions_and_revalidates_finite_values() -> None:
    catalog = MemoryCatalog()
    wrong_dimensions = _batch(vectors=(_vector(vector=(0.1,)),))

    with pytest.raises(CoreError) as dimensions_error:
        CoreIndexingService(catalog).index(wrong_dimensions)
    assert dimensions_error.value.category is ErrorCategory.VALIDATION

    non_finite = _vector()
    object.__setattr__(non_finite, "vector", (0.1, float("nan")))
    with pytest.raises(CoreError) as finite_error:
        CoreIndexingService(catalog).index(_batch(vectors=(non_finite,)))
    assert finite_error.value.category is ErrorCategory.VALIDATION
    assert catalog.replace_calls == []


def test_vector_only_unit_is_valid_and_text_counts_are_exact() -> None:
    catalog = MemoryCatalog()
    batch = _batch(
        representations=(_representation(text=None, modality="image"),),
        units=(_unit(text=None, modality="image"),),
    )

    CoreIndexingService(catalog).index(batch)

    assert catalog.batch(batch.resource.resource_id) is batch


def test_successful_replace_exposes_only_the_complete_new_graph() -> None:
    catalog = MemoryCatalog()
    service = CoreIndexingService(catalog)
    previous = _batch()
    replacement = _batch(
        representations=(_representation("representation-new"),),
        units=(_unit("unit-new", representation_id="representation-new"),),
        spaces=(_space("space-new", dimensions=3),),
        vectors=(_vector("unit-new", space_id="space-new", vector=(0.1, 0.2, 0.3)),),
    )
    service.index(previous)

    service.index(replacement)

    assert catalog.batch(previous.resource.resource_id) is replacement
    assert catalog.read_unit(previous.units[0].unit_id) is None
    assert catalog.read_vector(previous.units[0].unit_id, previous.spaces[0].space_id) is None
    assert catalog.read_unit("unit-new") == replacement.units[0]
    assert catalog.read_vector("unit-new", "space-new") == replacement.vectors[0]


def test_catalog_failure_is_classified_and_previous_graph_remains_fully_visible(
    caplog: pytest.LogCaptureFixture,
) -> None:
    catalog = MemoryCatalog()
    service = CoreIndexingService(catalog)
    previous = _batch()
    service.index(previous)
    replacement = _batch(
        resource=replace(previous.resource, title="replacement"),
        units=(replace(previous.units[0], text="replacement"),),
    )
    catalog.inject_replace_failure(RuntimeError("PRIVATE_EXCEPTION_SENTINEL"))
    caplog.clear()

    with caplog.at_level(logging.INFO, logger="mdrack_core.application.indexing"):
        with pytest.raises(CatalogExecutionError) as caught:
            service.index(replacement)

    assert caught.value.category is ErrorCategory.CATALOG_ERROR
    assert str(caught.value) == "catalog_error"
    assert catalog.batch(previous.resource.resource_id) is previous
    assert catalog.read_unit(previous.units[0].unit_id) == previous.units[0]
    assert catalog.read_vector(previous.units[0].unit_id, previous.spaces[0].space_id) == previous.vectors[0]
    assert "PRIVATE_EXCEPTION_SENTINEL" not in caplog.text
    assert "core.index.failed" in caplog.text
    assert '"category":"catalog_error"' in caplog.text


def test_timeout_is_classified_without_adapter_exception_text() -> None:
    catalog = MemoryCatalog()
    catalog.inject_replace_failure(TimeoutError("PRIVATE_EXCEPTION_SENTINEL"))

    with pytest.raises(CatalogExecutionError) as caught:
        CoreIndexingService(catalog).index(_batch())

    assert caught.value.category is ErrorCategory.ADAPTER_TIMEOUT
    assert "PRIVATE_EXCEPTION_SENTINEL" not in str(caught.value)


def test_rejects_non_batch_and_invalid_delete_identity_without_calling_catalog() -> None:
    catalog = MemoryCatalog()
    service = CoreIndexingService(catalog)

    with pytest.raises(CoreError) as batch_error:
        service.index(object())  # type: ignore[arg-type]
    with pytest.raises(CoreError) as delete_error:
        service.delete("   ")
    with pytest.raises(CoreError) as surrogate_delete_error:
        service.delete("resource\ud800")
    bypass = EncodeBypass("resource\ud800")
    with pytest.raises(UnicodeEncodeError):
        str.encode(bypass, "utf-8", "strict")
    with pytest.raises(CoreError) as subclass_delete_error:
        service.delete(bypass)

    assert batch_error.value.category is ErrorCategory.VALIDATION
    assert delete_error.value.category is ErrorCategory.VALIDATION
    assert surrogate_delete_error.value.category is ErrorCategory.VALIDATION
    assert subclass_delete_error.value.category is ErrorCategory.VALIDATION
    assert catalog.replace_calls == []
    assert catalog.delete_calls == []


def test_validation_failure_emits_only_safe_started_and_failed_events(
    caplog: pytest.LogCaptureFixture,
) -> None:
    catalog = MemoryCatalog()
    invalid = _batch(units=(_unit(resource_id="PRIVATE_FOREIGN_OWNER_SENTINEL"),))

    with caplog.at_level(logging.INFO, logger="mdrack_core.application.indexing"):
        with pytest.raises(CoreError):
            CoreIndexingService(catalog).index(invalid)

    assert [record.message.split(" ", 1)[0] for record in caplog.records] == [
        "core.index.started",
        "core.index.failed",
    ]
    assert '"category":"validation"' in caplog.text
    assert "PRIVATE_FOREIGN_OWNER_SENTINEL" not in caplog.text


def test_lone_surrogate_text_fails_with_safe_validation_lifecycle(
    caplog: pytest.LogCaptureFixture,
) -> None:
    catalog = MemoryCatalog()
    private_invalid_text = "PRIVATE_SURROGATE_SENTINEL\ud800"
    invalid = _batch()
    object.__setattr__(invalid.representations[0], "text", private_invalid_text)
    object.__setattr__(invalid.units[0], "text", private_invalid_text)

    with caplog.at_level(logging.INFO, logger="mdrack_core.application.indexing"):
        with pytest.raises(CoreError) as caught:
            CoreIndexingService(catalog).index(invalid)

    assert caught.value.category is ErrorCategory.VALIDATION
    assert str(caught.value) == "validation"
    assert catalog.replace_calls == []
    assert [record.message.split(" ", 1)[0] for record in caplog.records] == [
        "core.index.started",
        "core.index.failed",
    ]
    assert '"category":"validation"' in caplog.text
    assert "PRIVATE_SURROGATE_SENTINEL" not in caplog.text
    assert "\ud800" not in caplog.text


@pytest.mark.parametrize(
    "forge",
    [
        lambda batch, value: object.__setattr__(batch.resource, "resource_id", value),
        lambda batch, value: object.__setattr__(batch.resource, "title", value),
        lambda batch, value: object.__setattr__(batch.resource.locator, "kind", value),
        lambda batch, value: object.__setattr__(batch.resource, "metadata", {value: "value"}),
        lambda batch, value: object.__setattr__(
            batch.resource,
            "metadata",
            {"nested": {"value": value}},
        ),
        lambda batch, value: object.__setattr__(batch.spaces[0], "fingerprint", value),
        lambda batch, value: object.__setattr__(batch.facets[0].facet, "value", value),
    ],
)
@pytest.mark.parametrize(
    "make_value",
    [
        lambda: "PRIVATE_SURROGATE_SENTINEL\ud800",
        lambda: EncodeBypass("PRIVATE_SURROGATE_SENTINEL\ud800"),
    ],
    ids=("built-in-str", "str-subclass-encode-override"),
)
def test_forged_non_utf8_persisted_strings_fail_before_catalog(
    forge: object,
    make_value: object,
) -> None:
    catalog = MemoryCatalog()
    batch = _batch()
    value = make_value()  # type: ignore[operator]
    with pytest.raises(UnicodeEncodeError):
        str.encode(value, "utf-8", "strict")  # type: ignore[arg-type]
    forge(batch, value)  # type: ignore[operator]

    with pytest.raises(CoreError) as caught:
        CoreIndexingService(catalog).index(batch)

    assert caught.value.category is ErrorCategory.VALIDATION
    assert catalog.replace_calls == []


def test_memory_catalog_preserves_vector_signed_zero_exactly() -> None:
    catalog = MemoryCatalog()
    batch = _batch(vectors=(_vector(vector=(0.0, -0.0)),))

    CoreIndexingService(catalog).index(batch)

    stored = catalog.read_vector("unit-caller-supplied", "space-caller-supplied")
    assert stored is not None
    assert [math.copysign(1.0, value) for value in stored.vector] == [1.0, -1.0]
    assert canonical_json(stored.vector) == "[0.0,-0.0]"


def test_delete_is_idempotent_and_uses_only_the_logical_resource_id() -> None:
    catalog = MemoryCatalog()
    service = CoreIndexingService(catalog)
    batch = _batch()
    service.index(batch)

    service.delete(batch.resource.resource_id)
    service.delete(batch.resource.resource_id)

    assert catalog.read_resource(batch.resource.resource_id) is None
    assert catalog.read_unit(batch.units[0].unit_id) is None
    assert catalog.read_vector(batch.units[0].unit_id, batch.spaces[0].space_id) is None
    assert catalog.delete_calls == [batch.resource.resource_id, batch.resource.resource_id]


@pytest.mark.parametrize(
    ("failure", "category"),
    [
        (RuntimeError("PRIVATE_EXCEPTION_SENTINEL"), ErrorCategory.CATALOG_ERROR),
        (TimeoutError("PRIVATE_EXCEPTION_SENTINEL"), ErrorCategory.ADAPTER_TIMEOUT),
    ],
)
def test_failed_delete_is_classified_and_preserves_the_complete_graph(
    failure: BaseException,
    category: ErrorCategory,
) -> None:
    catalog = MemoryCatalog()
    service = CoreIndexingService(catalog)
    batch = _batch()
    service.index(batch)
    catalog.inject_delete_failure(failure)

    with pytest.raises(CatalogExecutionError) as caught:
        service.delete(batch.resource.resource_id)

    assert caught.value.category is category
    assert "PRIVATE_EXCEPTION_SENTINEL" not in str(caught.value)
    assert catalog.batch(batch.resource.resource_id) is batch


def test_memory_catalog_logical_reads_hash_scope_and_stable_ordering() -> None:
    catalog = MemoryCatalog()
    first = _batch()
    second = _batch(
        resource=_resource("resource-a", title="second"),
        representations=(
            _representation(
                "representation-a",
                resource_id="resource-a",
                modality="image",
                text=None,
            ),
        ),
        units=(
            _unit(
                "unit-a",
                resource_id="resource-a",
                representation_id="representation-a",
                modality="image",
                text=None,
            ),
        ),
        spaces=(_space("visual-space"),),
        vectors=(_vector("unit-a", space_id="visual-space"),),
        facets=(_facet(resource_id="resource-a", value="images"),),
    )
    service = CoreIndexingService(catalog)
    service.index(first)
    service.index(second)

    assert catalog.read_resource(first.resource.resource_id) == first.resource
    assert catalog.read_unit(second.units[0].unit_id) == second.units[0]
    assert catalog.read_vector(second.units[0].unit_id, "visual-space") == second.vectors[0]
    assert [item.resource_id for item in catalog.find_by_content_hash(
        "sha256:shared-content",
        scope=SearchScope(),
    )] == ["resource-a", "resource-caller-supplied"]
    assert catalog.find_by_content_hash(
        "sha256:shared-content",
        scope=SearchScope(
            resource_kinds=("document",),
            modalities=("image",),
            representation_kinds=("retrieval_text",),
            unit_kinds=("whole_resource",),
            facets_all=(Facet("topic", "images"),),
            facets_none=(Facet("topic", "PRIVATE_FACET_SENTINEL"),),
        ),
    ) == [second.resource]
    assert catalog.find_by_content_hash(
        "sha256:shared-content",
        scope=SearchScope(facets_any=(Facet("missing", "value"),)),
    ) == []
