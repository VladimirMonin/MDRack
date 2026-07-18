from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Protocol

import pytest
from fakes.memory_store import MemoryCatalog

from mdrack.adapters.sqlite.resource_store import SQLiteResourceStore
from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.migrations import apply_candidate_migrations, get_migrations_dir
from mdrack_core.application.retrieval import ResourceDiscoveryService
from mdrack_core.domain import (
    CatalogExecutionError,
    DegradationCategory,
    EmbeddingSpaceRecord,
    ErrorCategory,
    Facet,
    LexicalBranch,
    Locator,
    PreparedResourceBatch,
    RankedCandidate,
    RepresentationRecord,
    ResourceFacet,
    ResourceRecord,
    SearchScope,
    SearchUnitRecord,
    SimilarityRequest,
    VectorBranch,
    VectorRecord,
)


class DiscoveryStore(Protocol):
    def replace_resource(self, batch: PreparedResourceBatch) -> None: ...

    def search_lexical(
        self, branch: LexicalBranch, *, scope: SearchScope
    ) -> list[RankedCandidate]: ...

    def search_vector(
        self, branch: VectorBranch, *, scope: SearchScope
    ) -> list[RankedCandidate]: ...


@pytest.fixture(params=("memory", "sqlite"))
def discovery_store(request: pytest.FixtureRequest, tmp_path: Path):
    connection: sqlite3.Connection | None = None
    if request.param == "memory":
        store: DiscoveryStore = MemoryCatalog(enforce_resource_contract=True)
    else:
        connection = get_connection(tmp_path / "discovery.db")
        apply_candidate_migrations(connection, get_migrations_dir())
        store = SQLiteResourceStore(connection)
    yield store
    if connection is not None:
        connection.close()


def _batch(
    resource_id: str,
    *,
    resource_kind: str = "document",
    content_hash: str | None = "sha256:shared",
    vector: tuple[float, ...] = (1.0, 0.0),
    facets: tuple[Facet, ...] = (),
    space_id: str = "shared-space",
    fingerprint: str = "shared-fingerprint",
) -> PreparedResourceBatch:
    representation_id = f"representation-{resource_id}"
    unit_id = f"unit-{resource_id}"
    modality = "image" if resource_kind == "image" else "text"
    representation_kind = "visual" if resource_kind == "image" else "retrieval_text"
    return PreparedResourceBatch(
        ResourceRecord(
            resource_id,
            resource_kind,
            "image/png" if resource_kind == "image" else "text/markdown",
            "fixture",
            Locator("logical", {"id": resource_id}),
            content_hash,
        ),
        (
            RepresentationRecord(
                representation_id,
                resource_id,
                representation_kind,
                modality,
                None if resource_kind == "image" else f"text for {resource_id}",
                producer_fingerprint=fingerprint,
            ),
        ),
        (
            SearchUnitRecord(
                unit_id,
                resource_id,
                representation_id,
                "whole_resource",
                modality,
                None if resource_kind == "image" else f"text for {resource_id}",
                Locator("whole", {"id": resource_id}),
                0,
            ),
        ),
        (EmbeddingSpaceRecord(space_id, len(vector), "cosine", fingerprint),),
        (VectorRecord(unit_id, space_id, vector),),
        tuple(ResourceFacet(resource_id, facet, "user") for facet in facets),
    )


def _query_batch_with_sibling(facets: tuple[Facet, ...]) -> PreparedResourceBatch:
    resource_id = "query"
    return PreparedResourceBatch(
        ResourceRecord(
            resource_id,
            "document",
            "text/markdown",
            "fixture",
            Locator("logical", {"id": resource_id}),
            "sha256:shared",
        ),
        (
            RepresentationRecord("representation-query", resource_id, "retrieval_text", "text", "query"),
            RepresentationRecord(
                "representation-query-sibling",
                resource_id,
                "retrieval_text",
                "text",
                "query sibling",
            ),
        ),
        (
            SearchUnitRecord(
                "unit-query",
                resource_id,
                "representation-query",
                "whole_resource",
                "text",
                "query",
                Locator("whole", {"id": resource_id}),
                0,
            ),
            SearchUnitRecord(
                "unit-query-sibling",
                resource_id,
                "representation-query-sibling",
                "whole_resource",
                "text",
                "query sibling",
                Locator("whole", {"id": resource_id}),
                0,
            ),
        ),
        (EmbeddingSpaceRecord("shared-space", 2, "cosine", "shared-fingerprint"),),
        (
            VectorRecord("unit-query", "shared-space", (1.0, 0.0)),
            VectorRecord("unit-query-sibling", "shared-space", (1.0, 0.0)),
        ),
        tuple(ResourceFacet(resource_id, facet, "user") for facet in facets),
    )


def test_duplicates_and_similarity_are_provider_free_scoped_and_deterministic(
    discovery_store: DiscoveryStore,
) -> None:
    required = Facet("topic", "python")
    optional = Facet("status", "reviewed")
    forbidden = Facet("visibility", "private")
    batches = (
        _query_batch_with_sibling((required, optional)),
        _batch("duplicate", facets=(required, optional), vector=(0.9, 0.1)),
        _batch(
            "similar-image",
            resource_kind="image",
            content_hash="sha256:image",
            facets=(required, optional),
            vector=(0.8, 0.2),
        ),
        _batch("excluded-high", facets=(required, forbidden), vector=(1.0, 0.0)),
    )
    for batch in batches:
        discovery_store.replace_resource(batch)

    scope = SearchScope(
        facets_any=(Facet("status", "draft"), optional),
        facets_all=(required, optional),
        facets_none=(forbidden,),
    )
    service = ResourceDiscoveryService(discovery_store)  # type: ignore[arg-type]

    duplicates = service.find_duplicates("query", scope=scope, limit=1)
    assert [item.resource_id for item in duplicates] == ["duplicate"]

    request = SimilarityRequest("unit-query", "shared-space", scope, 2)
    first = service.similar(request)
    second = service.similar(request)
    assert first == second
    assert [(item.resource_id, item.unit_id, item.rank) for item in first.items] == [
        ("duplicate", "unit-duplicate", 1),
        ("similar-image", "unit-similar-image", 2),
    ]
    assert first.items[0].score > first.items[1].score
    assert all(item.score == item.evidence[0].raw_score for item in first.items)
    assert "query" not in {item.resource_id for item in first.items}
    assert "excluded-high" not in {item.resource_id for item in first.items}


def test_similarity_missing_unit_or_vector_returns_safe_degradation(
    discovery_store: DiscoveryStore,
) -> None:
    service = ResourceDiscoveryService(discovery_store)  # type: ignore[arg-type]
    missing_unit = service.similar(
        SimilarityRequest("missing-unit", "shared-space", SearchScope(), 5)
    )
    assert missing_unit.items == ()
    assert [item.category for item in missing_unit.degradations] == [
        DegradationCategory.BRANCH_UNAVAILABLE
    ]

    without_requested_space = _batch("query", space_id="other-space")
    discovery_store.replace_resource(without_requested_space)
    missing_vector = service.similar(
        SimilarityRequest("unit-query", "shared-space", SearchScope(), 5)
    )
    assert missing_vector.items == ()
    assert [item.category for item in missing_vector.degradations] == [
        DegradationCategory.BRANCH_UNAVAILABLE
    ]


@pytest.mark.parametrize(
    ("method_name", "failure_kind", "expected"),
    (
        ("read_unit", "catalog_error", DegradationCategory.ADAPTER_ERROR),
        ("read_unit", "catalog_timeout", DegradationCategory.ADAPTER_TIMEOUT),
        ("read_unit", "raw_error", DegradationCategory.ADAPTER_ERROR),
        ("read_unit", "raw_timeout", DegradationCategory.ADAPTER_TIMEOUT),
        ("read_vector", "catalog_error", DegradationCategory.ADAPTER_ERROR),
        ("read_vector", "catalog_timeout", DegradationCategory.ADAPTER_TIMEOUT),
        ("read_vector", "raw_error", DegradationCategory.ADAPTER_ERROR),
        ("read_vector", "raw_timeout", DegradationCategory.ADAPTER_TIMEOUT),
    ),
)
def test_similarity_catalog_reads_map_to_safe_degradation(
    caplog: pytest.LogCaptureFixture,
    method_name: str,
    failure_kind: str,
    expected: DegradationCategory,
) -> None:
    def fail() -> None:
        if failure_kind == "catalog_error":
            raise CatalogExecutionError(ErrorCategory.CATALOG_ERROR)
        if failure_kind == "catalog_timeout":
            raise CatalogExecutionError(ErrorCategory.ADAPTER_TIMEOUT)
        if failure_kind == "raw_timeout":
            raise TimeoutError("PRIVATE_EXCEPTION_SENTINEL")
        raise RuntimeError("PRIVATE_EXCEPTION_SENTINEL")

    class FailingCatalog(MemoryCatalog):
        def read_unit(self, unit_id: str):
            if method_name == "read_unit":
                fail()
            return super().read_unit(unit_id)

        def read_vector(self, unit_id: str, space_id: str):
            if method_name == "read_vector":
                fail()
            return super().read_vector(unit_id, space_id)

    catalog = FailingCatalog(enforce_resource_contract=True)
    catalog.replace_resource(_batch("query"))

    with caplog.at_level(logging.INFO, logger="mdrack_core.application.retrieval"):
        result = ResourceDiscoveryService(catalog).similar(
            SimilarityRequest("unit-query", "shared-space", SearchScope(), 5)
        )

    assert result.items == ()
    assert [item.category for item in result.degradations] == [expected]
    assert "PRIVATE_EXCEPTION_SENTINEL" not in repr(result)
    assert "PRIVATE_EXCEPTION_SENTINEL" not in caplog.text


@pytest.mark.parametrize("method_name", ("read_resource", "find_by_content_hash"))
@pytest.mark.parametrize(
    ("failure_kind", "expected"),
    (
        ("catalog_error", ErrorCategory.CATALOG_ERROR),
        ("catalog_timeout", ErrorCategory.ADAPTER_TIMEOUT),
        ("raw_timeout", ErrorCategory.ADAPTER_TIMEOUT),
        ("raw_error", ErrorCategory.CATALOG_ERROR),
    ),
)
def test_duplicate_catalog_reads_normalize_safe_errors(
    method_name: str,
    failure_kind: str,
    expected: ErrorCategory,
) -> None:
    def fail() -> None:
        if failure_kind == "catalog_error":
            raise CatalogExecutionError(ErrorCategory.CATALOG_ERROR)
        if failure_kind == "catalog_timeout":
            raise CatalogExecutionError(ErrorCategory.ADAPTER_TIMEOUT)
        if failure_kind == "raw_timeout":
            raise TimeoutError("PRIVATE_EXCEPTION_SENTINEL")
        raise RuntimeError("PRIVATE_EXCEPTION_SENTINEL")

    class FailingCatalog(MemoryCatalog):
        def read_resource(self, resource_id: str):
            if method_name == "read_resource":
                fail()
            return super().read_resource(resource_id)

        def find_by_content_hash(self, content_hash: str, *, scope: SearchScope):
            if method_name == "find_by_content_hash":
                fail()
            return super().find_by_content_hash(content_hash, scope=scope)

    catalog = FailingCatalog(enforce_resource_contract=True)
    catalog.replace_resource(_batch("query"))

    with pytest.raises(CatalogExecutionError) as caught:
        ResourceDiscoveryService(catalog).find_duplicates("query", scope=SearchScope(), limit=5)

    assert caught.value.category is expected
    assert "PRIVATE_EXCEPTION_SENTINEL" not in str(caught.value)


def test_similarity_dimension_mismatch_degrades_without_exposing_vectors() -> None:
    class MismatchedVectorCatalog(MemoryCatalog):
        def read_vector(self, unit_id: str, space_id: str) -> VectorRecord | None:
            vector = super().read_vector(unit_id, space_id)
            if vector is None:
                return None
            return VectorRecord(vector.unit_id, vector.space_id, (1.0,))

    catalog = MismatchedVectorCatalog(enforce_resource_contract=True)
    catalog.replace_resource(_batch("query"))
    catalog.replace_resource(_batch("candidate", vector=(0.8, 0.2)))

    result = ResourceDiscoveryService(catalog).similar(
        SimilarityRequest("unit-query", "shared-space", SearchScope(), 5)
    )

    assert result.items == ()
    assert [item.category for item in result.degradations] == [
        DegradationCategory.INCOMPATIBLE_VECTOR_SPACE
    ]
    assert "1.0" not in repr(result)


def test_facet_any_all_none_are_applied_before_limits_for_both_searches(
    discovery_store: DiscoveryStore,
) -> None:
    required = Facet("topic", "python")
    optional = Facet("status", "reviewed")
    forbidden = Facet("visibility", "private")
    discovery_store.replace_resource(
        _batch("excluded", facets=(required, optional, forbidden), vector=(1.0, 0.0))
    )
    discovery_store.replace_resource(
        _batch("included", facets=(required, optional), vector=(0.5, 0.5))
    )
    scope = SearchScope(
        facets_any=(Facet("status", "draft"), optional),
        facets_all=(required, optional),
        facets_none=(forbidden,),
    )
    lexical = discovery_store.search_lexical(
        LexicalBranch("lexical", "text", candidate_limit=1),
        scope=scope,
    )
    vector = discovery_store.search_vector(
        VectorBranch("vector", "shared-space", (1.0, 0.0), candidate_limit=1),
        scope=scope,
    )
    assert [item.resource_id for item in lexical] == ["included"]
    assert [item.resource_id for item in vector] == ["included"]
