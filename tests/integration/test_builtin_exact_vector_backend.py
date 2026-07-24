"""Conformance tests for the default compact stdlib vector backend."""

from __future__ import annotations

import struct
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest

from mdrack_core.domain import (
    BranchExecutionError,
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
    VectorBranch,
    VectorRecord,
)
from mdrack_sqlite import SQLiteCatalog
from mdrack_sqlite.builtin_exact import BuiltinExactVectorBackend

_F32_METADATA: Mapping[str, str] = {
    "vector_codec": "ieee754-f32-le-v1",
    "vector_value_policy": "ieee754-f32-canonical-v1",
}
_F64_METADATA: Mapping[str, str] = {"vector_codec": "ieee754-f64-le-v1"}


def _batch(
    resource_id: str,
    *,
    vector: tuple[float, float],
    space_id: str,
    metric: str,
    fingerprint: str,
    metadata: Mapping[str, str],
    namespace: str = "vault",
    facet: Facet | None = Facet("tag", "include"),
) -> PreparedResourceBatch:
    representation_id = f"representation-{resource_id}"
    unit_id = f"unit-{resource_id}"
    resource = ResourceRecord(
        resource_id,
        "document",
        "text/plain",
        namespace,
        Locator("logical", {"id": resource_id}),
        f"sha256:{resource_id}",
    )
    representation = RepresentationRecord(representation_id, resource_id, "retrieval_text", "text", resource_id)
    unit = SearchUnitRecord(
        unit_id,
        resource_id,
        representation_id,
        "text_chunk",
        "text",
        resource_id,
        Locator("whole", {}),
        0,
    )
    facets = () if facet is None else (ResourceFacet(resource_id, facet, "fixture"),)
    return PreparedResourceBatch(
        resource,
        (representation,),
        (unit,),
        (EmbeddingSpaceRecord(space_id, 2, metric, fingerprint, metadata),),
        (VectorRecord(unit_id, space_id, vector),),
        facets,
    )


@pytest.fixture
def catalog(tmp_path: Path) -> Iterator[SQLiteCatalog]:
    with SQLiteCatalog.create_v2(tmp_path / "compact.db") as created:
        yield created


@pytest.mark.parametrize("metric", ("cosine", "dot", "l2"))
@pytest.mark.parametrize("metadata", (_F32_METADATA, _F64_METADATA), ids=("f32", "f64"))
def test_builtin_exact_backend_preserves_binary_metric_scope_ties_and_dense_ranks(
    catalog: SQLiteCatalog,
    metric: str,
    metadata: Mapping[str, str],
) -> None:
    catalog.replace_resource(
        _batch(
            "ignored",
            vector=(100.0, 0.0),
            space_id="primary",
            metric=metric,
            fingerprint="primary-fingerprint",
            metadata=metadata,
            namespace="other",
        )
    )
    for resource_id in ("alpha", "beta"):
        catalog.replace_resource(
            _batch(
                resource_id,
                vector=(1.0, 0.0),
                space_id="primary",
                metric=metric,
                fingerprint="primary-fingerprint",
                metadata=metadata,
            )
        )
    catalog.replace_resource(
        _batch(
            "secondary",
            vector=(0.0, 1.0),
            space_id="secondary",
            metric=metric,
            fingerprint="secondary-fingerprint",
            metadata=metadata,
        )
    )

    results = catalog.search_vector(
        VectorBranch(
            "primary-branch",
            "primary",
            (1.0, 0.0),
            expected_fingerprint="primary-fingerprint",
            candidate_limit=2,
        ),
        scope=SearchScope(source_namespaces=("vault",), facets_all=(Facet("tag", "include"),)),
    )

    assert isinstance(catalog.vector_backend, BuiltinExactVectorBackend)
    assert catalog.vector_backend.backend_id == "builtin-exact-v1"
    assert [(item.unit_id, item.rank) for item in results] == [("unit-alpha", 1), ("unit-beta", 2)]
    assert [item.branch_id for item in results] == ["primary-branch", "primary-branch"]
    counters = catalog.last_vector_search_counters
    assert counters is not None
    assert counters.candidate_rows == 2
    assert counters.decoded_vectors == 2
    assert counters.skipped_vectors == 0
    assert counters.decode_cpu_seconds >= 0.0
    assert counters.score_seconds >= 0.0
    assert counters.sort_seconds >= 0.0

    secondary = catalog.search_vector(
        VectorBranch(
            "secondary-branch",
            "secondary",
            (0.0, 1.0),
            expected_fingerprint="secondary-fingerprint",
        ),
        scope=SearchScope(),
    )
    assert [item.unit_id for item in secondary] == ["unit-secondary"]

    with pytest.raises(BranchExecutionError) as mismatch:
        catalog.search_vector(
            VectorBranch("wrong-fingerprint", "primary", (1.0, 0.0), expected_fingerprint="other"),
            scope=SearchScope(),
        )
    assert mismatch.value.category is ErrorCategory.INCOMPATIBLE_VECTOR_SPACE


def test_builtin_exact_backend_skips_zero_cosine_vectors_and_fails_closed_on_corruption(
    catalog: SQLiteCatalog,
) -> None:
    catalog.replace_resource(
        _batch(
            "valid",
            vector=(1.0, 0.0),
            space_id="cosine",
            metric="cosine",
            fingerprint="cosine-fingerprint",
            metadata=_F32_METADATA,
        )
    )
    catalog.replace_resource(
        _batch(
            "zero",
            vector=(1.0, 0.0),
            space_id="cosine",
            metric="cosine",
            fingerprint="cosine-fingerprint",
            metadata=_F32_METADATA,
        )
    )
    catalog.connection.execute(
        "UPDATE core_unit_embeddings SET embedding=? WHERE unit_id='unit-zero'",
        (struct.pack("<2f", 0.0, -0.0),),
    )
    catalog.connection.commit()

    results = catalog.search_vector(
        VectorBranch("cosine-branch", "cosine", (1.0, 0.0), expected_fingerprint="cosine-fingerprint"),
        scope=SearchScope(),
    )

    assert [item.unit_id for item in results] == ["unit-valid"]
    counters = catalog.last_vector_search_counters
    assert counters is not None
    assert counters.candidate_rows == 2
    assert counters.decoded_vectors == 2
    assert counters.skipped_vectors == 1
    assert counters.decode_cpu_seconds >= 0.0

    catalog.connection.execute(
        "UPDATE core_unit_embeddings SET embedding=? WHERE unit_id='unit-valid'",
        (b"\x00\x00\x00",),
    )
    catalog.connection.commit()
    with pytest.raises(BranchExecutionError) as corrupt:
        catalog.search_vector(
            VectorBranch("corrupt-branch", "cosine", (1.0, 0.0), expected_fingerprint="cosine-fingerprint"),
            scope=SearchScope(),
        )
    assert corrupt.value.category is ErrorCategory.ADAPTER_ERROR
