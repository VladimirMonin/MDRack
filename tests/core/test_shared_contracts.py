from __future__ import annotations

import inspect
from dataclasses import FrozenInstanceError, replace
from typing import get_type_hints

import pytest

import mdrack_core
import mdrack_core.domain as core_domain
from mdrack_core.domain import (
    BranchExecutionError,
    CatalogExecutionError,
    Degradation,
    DegradationCategory,
    EmbeddingSpaceRecord,
    ErrorCategory,
    Facet,
    LexicalBranch,
    Locator,
    PreparedResourceBatch,
    RankedCandidate,
    RankKind,
    RepresentationRecord,
    ResourceFacet,
    ResourceRecord,
    ScoreKind,
    SearchResult,
    SearchResultItem,
    SearchScope,
    SearchUnitRecord,
    SimilarityRequest,
    SimilarityResult,
    VectorBranch,
    VectorRecord,
)
from mdrack_core.ports import (
    CatalogPort,
    LexicalSearchPort,
    ResourceReadPort,
    ResourceWritePort,
    SearchPort,
    VectorSearchPort,
)


def resource() -> ResourceRecord:
    return ResourceRecord(
        "resource-1",
        "document",
        "text/markdown",
        "vault",
        Locator("file", {"root_id": "root", "relative_path": "note.md"}),
        "sha256:content",
    )


def representation() -> RepresentationRecord:
    return RepresentationRecord(
        "representation-1",
        "resource-1",
        "retrieval_text",
        "text",
        "content",
    )


def unit() -> SearchUnitRecord:
    return SearchUnitRecord(
        "unit-1",
        "resource-1",
        "representation-1",
        "whole_resource",
        "text",
        "content",
        Locator("whole_resource", {}),
        0,
    )


def candidate() -> RankedCandidate:
    return RankedCandidate(
        "unit-1",
        "resource-1",
        "representation-1",
        1,
        0.75,
        "lexical",
        Locator("whole_resource", {}),
    )


def prepared_batch(**changes: object) -> PreparedResourceBatch:
    values: dict[str, object] = {
        "resource": resource(),
        "representations": [representation()],
        "units": [unit()],
        "spaces": [EmbeddingSpaceRecord("space-1", 2, "cosine", "fingerprint")],
        "vectors": [VectorRecord("unit-1", "space-1", [0.1, 0.2])],  # type: ignore[arg-type]
        "facets": [ResourceFacet("resource-1", Facet("tag", "python"), "user")],
    }
    values.update(changes)
    return PreparedResourceBatch(**values)  # type: ignore[arg-type]


def result_item(**changes: object) -> SearchResultItem:
    values: dict[str, object] = {
        "logical_id": "unit-1",
        "resource_id": "resource-1",
        "unit_id": "unit-1",
        "score": 0.5,
        "rank": 1,
        "evidence": [candidate()],
        "metadata": {"safe": [1]},
    }
    values.update(changes)
    return SearchResultItem(**values)  # type: ignore[arg-type]


def test_prepared_batch_freezes_every_ordered_typed_collection() -> None:
    batch = prepared_batch()

    assert batch.representations == (representation(),)
    assert batch.units == (unit(),)
    assert batch.spaces[0].space_id == "space-1"
    assert batch.vectors[0].vector == (0.1, 0.2)
    assert batch.facets[0].facet == Facet("tag", "python")
    with pytest.raises(FrozenInstanceError):
        batch.resource = resource()  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field_name", "item"),
    [
        ("representations", representation()),
        ("units", unit()),
        ("spaces", EmbeddingSpaceRecord("space-1", 2, "cosine", "fingerprint")),
        ("vectors", VectorRecord("unit-1", "space-1", (0.1, 0.2))),
        ("facets", ResourceFacet("resource-1", Facet("tag", "python"), "user")),
    ],
)
@pytest.mark.parametrize(
    "make_value",
    [
        lambda _item: {object()},
        lambda _item: frozenset({object()}),
        lambda item: {"item": item},
        lambda item: (entry for entry in (item,)),
        lambda item: "not-a-sequence",
        lambda item: b"not-a-sequence",
    ],
)
def test_prepared_batch_rejects_unordered_and_non_sequence_fields(
    field_name: str,
    item: object,
    make_value: object,
) -> None:
    with pytest.raises(ValueError, match=field_name):
        prepared_batch(**{field_name: make_value(item)})  # type: ignore[operator]


@pytest.mark.parametrize(
    ("field_name", "bad_value"),
    [
        ("resource", object()),
        ("representations", [object()]),
        ("units", [object()]),
        ("spaces", [object()]),
        ("vectors", [object()]),
        ("facets", [object()]),
    ],
)
def test_prepared_batch_rejects_wrong_record_types(field_name: str, bad_value: object) -> None:
    with pytest.raises(ValueError, match=field_name):
        prepared_batch(**{field_name: bad_value})


def test_result_records_are_frozen_typed_and_json_safe() -> None:
    degradation = Degradation("semantic", DegradationCategory.ADAPTER_TIMEOUT)
    item = result_item()
    result = SearchResult(
        "unit",
        [item],  # type: ignore[arg-type]
        [degradation],  # type: ignore[arg-type]
        "123e4567-e89b-12d3-a456-426614174000",
    )
    similarity = SimilarityResult(
        "unit-query",
        "space-1",
        "retrieval_text",
        [item],  # type: ignore[arg-type]
        [degradation],  # type: ignore[arg-type]
    )

    assert result.items == (item,)
    assert result.degradations == (degradation,)
    assert similarity.items == (item,)
    assert similarity.similarity_basis == "retrieval_text"
    assert item.score_kind is ScoreKind.RRF
    assert item.rank_kind is RankKind.RESULT
    assert item.evidence == (candidate(),)
    assert item.metadata["safe"] == (1,)
    with pytest.raises(TypeError):
        item.metadata["unsafe"] = "value"  # type: ignore[index]


@pytest.mark.parametrize(
    "make_value",
    [
        lambda _item: {object()},
        lambda _item: frozenset({object()}),
        lambda item: {"item": item},
        lambda item: (entry for entry in (item,)),
        lambda item: "not-a-sequence",
        lambda item: b"not-a-sequence",
    ],
)
@pytest.mark.parametrize("field_name", ["items", "degradations"])
def test_result_collections_reject_unordered_and_non_sequence_inputs(
    make_value: object,
    field_name: str,
) -> None:
    item: object = result_item() if field_name == "items" else Degradation(
        "lexical", DegradationCategory.BRANCH_UNAVAILABLE
    )
    values: dict[str, object] = {
        "target": "unit",
        "items": [result_item()],
        "degradations": [Degradation("lexical", DegradationCategory.BRANCH_UNAVAILABLE)],
    }
    values[field_name] = make_value(item)  # type: ignore[operator]
    with pytest.raises(ValueError, match=field_name):
        SearchResult(**values)  # type: ignore[arg-type]


def test_search_result_item_and_similarity_request_validation_matrix() -> None:
    for changes in (
        {"logical_id": ""},
        {"resource_id": ""},
        {"unit_id": ""},
        {"score": float("nan")},
        {"score": True},
        {"rank": 0},
        {"rank": True},
        {"score_kind": "rrf"},
        {"rank_kind": "result"},
        {"rank_kind": RankKind.ADAPTER_CANDIDATE},
        {"evidence": [object()]},
    ):
        with pytest.raises(ValueError):
            result_item(**changes)

    request = SimilarityRequest("unit-1", "space-1", "retrieval_text", SearchScope(), 10)
    assert request.exclude_same_resource is True
    for changes in (
        {"query_unit_id": ""},
        {"space_id": ""},
        {"similarity_basis": ""},
        {"scope": object()},
        {"limit": 0},
        {"limit": True},
        {"exclude_same_resource": 1},
    ):
        with pytest.raises(ValueError):
            replace(request, **changes)


def test_stable_errors_never_accept_or_render_adapter_exception_text() -> None:
    private = "PRIVATE_EXCEPTION_SENTINEL"
    branch_error = BranchExecutionError(ErrorCategory.ADAPTER_ERROR, branch_id="semantic")
    catalog_error = CatalogExecutionError(ErrorCategory.CATALOG_ERROR)

    assert str(branch_error) == "adapter_error"
    assert str(catalog_error) == "catalog_error"
    assert private not in str(branch_error)
    with pytest.raises(TypeError):
        BranchExecutionError(ErrorCategory.ADAPTER_ERROR, branch_id="semantic", message=private)  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        BranchExecutionError(ErrorCategory.VALIDATION, branch_id="semantic")


def test_port_signatures_freeze_scope_before_adapter_limits_and_core_types_only() -> None:
    lexical = inspect.signature(LexicalSearchPort.search_lexical)
    vector = inspect.signature(VectorSearchPort.search_vector)
    assert tuple(lexical.parameters) == ("self", "branch", "scope")
    assert tuple(vector.parameters) == ("self", "branch", "scope")
    assert lexical.parameters["scope"].kind is inspect.Parameter.KEYWORD_ONLY
    assert vector.parameters["scope"].kind is inspect.Parameter.KEYWORD_ONLY
    assert "limit" not in lexical.parameters
    assert "limit" not in vector.parameters
    assert get_type_hints(LexicalSearchPort.search_lexical)["branch"] is LexicalBranch
    assert get_type_hints(VectorSearchPort.search_vector)["branch"] is VectorBranch
    assert get_type_hints(LexicalSearchPort.search_lexical)["scope"] is SearchScope
    assert get_type_hints(VectorSearchPort.search_vector)["scope"] is SearchScope

    expected_catalog = {
        "replace_resource": PreparedResourceBatch,
        "delete_resource": str,
        "read_resource": str,
        "read_unit": str,
        "read_vector": str,
        "find_by_content_hash": str,
    }
    owners: tuple[type[object], ...] = (ResourceWritePort, ResourceReadPort)
    for method_name, first_type in expected_catalog.items():
        owner = next(owner for owner in owners if hasattr(owner, method_name))
        hints = get_type_hints(getattr(owner, method_name))
        parameter_name = next(name for name in hints if name != "return")
        assert hints[parameter_name] is first_type


def test_protocol_composites_and_public_export_inventory_are_frozen() -> None:
    assert CatalogPort.replace_resource is ResourceWritePort.replace_resource
    assert CatalogPort.read_resource is ResourceReadPort.read_resource
    assert SearchPort.search_lexical is LexicalSearchPort.search_lexical
    assert SearchPort.search_vector is VectorSearchPort.search_vector

    expected = {
        "BranchExecutionError",
        "BranchScopeOverride",
        "CORE_CONTRACT_VERSION",
        "CORE_EVENT_NAMES",
        "CatalogExecutionError",
        "CatalogPort",
        "CoreError",
        "Degradation",
        "DegradationCategory",
        "EmbeddingSpaceRecord",
        "ErrorCategory",
        "Facet",
        "LexicalBranch",
        "LexicalSearchPort",
        "LifecycleStatus",
        "Locator",
        "PreparedResourceBatch",
        "RankKind",
        "REDACTED",
        "RankedCandidate",
        "RepresentationRecord",
        "ResourceFacet",
        "ResourceReadPort",
        "ResourceRecord",
        "ResourceWritePort",
        "SafeEvent",
        "SafeFingerprint",
        "SearchPort",
        "SearchRequest",
        "SearchResult",
        "SearchResultItem",
        "SearchScope",
        "SearchUnitRecord",
        "ScoreKind",
        "SimilarityRequest",
        "SimilarityResult",
        "VectorBranch",
        "VectorRecord",
        "VectorSearchPort",
        "emit_event",
        "safe_fingerprint",
    }
    expected.update(core_domain.__all__)
    assert set(mdrack_core.__all__) == expected
    assert all(hasattr(mdrack_core, name) for name in expected)
