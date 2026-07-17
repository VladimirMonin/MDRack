from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from mdrack_core.domain.common import (
    MAX_JSON_BYTES,
    MAX_JSON_DEPTH,
    canonical_json,
    freeze_json_mapping,
    require_finite_number,
    require_integer,
)
from mdrack_core.domain.resources import (
    MODALITY_AUDIO,
    MODALITY_IMAGE,
    MODALITY_TEXT,
    MODALITY_VIDEO,
    RESOURCE_AUDIO,
    RESOURCE_DOCUMENT,
    RESOURCE_IMAGE,
    RESOURCE_VIDEO,
    TOKEN_COUNT_ESTIMATED,
    Facet,
    Locator,
    RepresentationRecord,
    ResourceFacet,
    ResourceRecord,
    SearchUnitRecord,
)
from mdrack_core.domain.search import (
    LexicalBranch,
    RankedCandidate,
    SearchRequest,
    SearchScope,
    VectorBranch,
)
from mdrack_core.domain.vectors import EmbeddingSpaceRecord, VectorRecord


def resource_record(**changes: object) -> ResourceRecord:
    values: dict[str, object] = {
        "resource_id": "resource-1",
        "resource_kind": "custom_resource",
        "media_type": "application/x-custom",
        "source_namespace": "test",
        "locator": Locator("custom", {"sequence": [1, 2]}),
        "content_hash": "sha256:abc",
        "title": "Title",
        "metadata": {"nested": {"enabled": True}},
    }
    values.update(changes)
    return ResourceRecord(**values)  # type: ignore[arg-type]


def representation_record(**changes: object) -> RepresentationRecord:
    values: dict[str, object] = {
        "representation_id": "representation-1",
        "resource_id": "resource-1",
        "representation_kind": "custom_representation",
        "modality": "custom_modality",
        "text": "full text",
        "language": "ru",
        "producer_fingerprint": "fingerprint-1",
        "token_count": 2,
        "token_count_kind": TOKEN_COUNT_ESTIMATED,
        "metadata": {},
    }
    values.update(changes)
    return RepresentationRecord(**values)  # type: ignore[arg-type]


def search_unit_record(**changes: object) -> SearchUnitRecord:
    values: dict[str, object] = {
        "unit_id": "unit-1",
        "resource_id": "resource-1",
        "representation_id": "representation-1",
        "unit_kind": "custom_unit",
        "modality": "custom_modality",
        "text": "unit text",
        "evidence_locator": Locator("whole_resource", {}),
        "ordinal": 0,
        "token_count": None,
        "token_count_kind": None,
        "metadata": {},
    }
    values.update(changes)
    return SearchUnitRecord(**values)  # type: ignore[arg-type]


def test_standard_resource_and_modality_vocabulary_is_available() -> None:
    assert {RESOURCE_DOCUMENT, RESOURCE_IMAGE, RESOURCE_AUDIO, RESOURCE_VIDEO} == {
        "document",
        "image",
        "audio",
        "video",
    }
    assert {MODALITY_TEXT, MODALITY_IMAGE, MODALITY_AUDIO, MODALITY_VIDEO} == {
        "text",
        "image",
        "audio",
        "video",
    }


def test_records_are_frozen_and_json_containers_are_deeply_immutable() -> None:
    supplied = {"z": [1, {"b": "Б", "a": True}]}
    record = resource_record(metadata=supplied)

    supplied["z"].append(3)  # type: ignore[union-attr]
    assert record.metadata["z"] == (1, {"a": True, "b": "Б"})
    with pytest.raises(FrozenInstanceError):
        record.title = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        record.metadata["new"] = "value"  # type: ignore[index]
    with pytest.raises(TypeError):
        record.metadata["z"][1]["a"] = False  # type: ignore[index]


def test_json_serialization_is_deterministic_and_unicode_preserving() -> None:
    first = freeze_json_mapping({"z": [3, 2], "a": {"я": "да"}}, "metadata")
    second = freeze_json_mapping({"a": {"я": "да"}, "z": (3, 2)}, "metadata")

    assert canonical_json(first) == canonical_json(second)
    assert canonical_json(first) == '{"a":{"я":"да"},"z":[3,2]}'


@pytest.mark.parametrize(
    "bad_value",
    [
        {1: "non-string key"},
        {"value": float("nan")},
        {"value": float("inf")},
        {"value": object()},
        {"value": {1, 2}},
    ],
)
def test_json_grammar_rejects_invalid_values(bad_value: object) -> None:
    with pytest.raises(ValueError):
        freeze_json_mapping(bad_value, "metadata")


def test_json_grammar_enforces_depth_and_encoded_size_limits() -> None:
    nested: object = "leaf"
    for _ in range(MAX_JSON_DEPTH + 1):
        nested = [nested]
    with pytest.raises(ValueError, match="maximum depth"):
        freeze_json_mapping({"nested": nested}, "metadata")

    with pytest.raises(ValueError, match="maximum encoded size"):
        freeze_json_mapping({"value": "x" * MAX_JSON_BYTES}, "metadata")


@pytest.mark.parametrize("value", [True, False, float("nan"), float("inf"), "1"])
def test_numeric_validators_reject_booleans_non_finite_and_text(value: object) -> None:
    with pytest.raises(ValueError):
        require_finite_number(value, "number")
    with pytest.raises(ValueError):
        require_integer(value, "integer", minimum=0)


@pytest.mark.parametrize("field_name", ["resource_id", "resource_kind", "media_type", "source_namespace"])
def test_resource_requires_non_blank_identity_and_open_vocabulary(field_name: str) -> None:
    with pytest.raises(ValueError, match=field_name):
        resource_record(**{field_name: "  "})

    assert resource_record(resource_kind="future-kind").resource_kind == "future-kind"


def test_locator_is_generic_json_safe_and_does_not_apply_adapter_semantics() -> None:
    locator = Locator(
        "future_locator",
        {"relative_path": "../adapter-owned-semantics", "ranges": [1, 2]},
    )
    assert locator.kind == "future_locator"
    assert locator.payload["ranges"] == (1, 2)

    with pytest.raises(ValueError, match="kind"):
        Locator(" ", {})
    with pytest.raises(ValueError, match="JSON-safe"):
        Locator("future", {"bad": object()})


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"representation_id": ""}, "representation_id"),
        ({"resource_id": ""}, "resource_id"),
        ({"representation_kind": ""}, "representation_kind"),
        ({"modality": ""}, "modality"),
        ({"language": ""}, "language"),
        ({"producer_fingerprint": ""}, "producer_fingerprint"),
        ({"token_count": -1}, "token_count"),
        ({"token_count": True}, "token_count"),
        ({"token_count_kind": "approximate"}, "token_count_kind"),
    ],
)
def test_representation_validation_matrix(changes: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        representation_record(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"token_count": None, "token_count_kind": "exact"},
        {"token_count": 1, "token_count_kind": None},
    ],
)
def test_token_count_and_kind_are_an_atomic_pair(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="supplied together"):
        representation_record(**changes)


def test_search_unit_validates_ids_ordinal_and_count_rules() -> None:
    assert search_unit_record(unit_kind="future_segment", ordinal=2).ordinal == 2
    for changes in (
        {"unit_id": ""},
        {"resource_id": ""},
        {"representation_id": ""},
        {"unit_kind": ""},
        {"modality": ""},
        {"ordinal": -1},
        {"ordinal": True},
    ):
        with pytest.raises(ValueError):
            search_unit_record(**changes)


def test_embedding_space_and_vector_validation() -> None:
    space = EmbeddingSpaceRecord("space-1", 3, "cosine", "fingerprint-1", {"kind": "text"})
    vector = VectorRecord("unit-1", space.space_id, [1, 2.5, -3])  # type: ignore[arg-type]

    assert vector.vector == (1.0, 2.5, -3.0)
    assert space.metadata["kind"] == "text"

    for dimensions in (0, -1, True):
        with pytest.raises(ValueError, match="dimensions"):
            EmbeddingSpaceRecord("space-1", dimensions, "cosine", "fingerprint-1")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="metric"):
        EmbeddingSpaceRecord("space-1", 3, "manhattan", "fingerprint-1")
    for bad_vector in ((), (1, float("nan")), (True, 1), "123"):
        with pytest.raises(ValueError):
            VectorRecord("unit-1", "space-1", bad_vector)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "make_value",
    [
        lambda: {1.0, 2.0},
        lambda: frozenset({1.0, 2.0}),
        lambda: {"first": 1.0, "second": 2.0},
        lambda: "12",
        lambda: b"12",
        lambda: (value for value in (1.0, 2.0)),
    ],
)
def test_vector_values_reject_unordered_and_non_sequence_inputs(make_value: object) -> None:
    with pytest.raises(ValueError, match="sequence"):
        VectorRecord("unit-1", "space-1", make_value())  # type: ignore[arg-type,operator]
    with pytest.raises(ValueError, match="sequence"):
        VectorBranch("semantic", "space-1", make_value())  # type: ignore[arg-type,operator]


@pytest.mark.parametrize("confidence", [0.0, 0.5, 1.0])
def test_facet_accepts_closed_confidence_range_and_open_origin(confidence: float) -> None:
    value = ResourceFacet("resource-1", Facet("tag", "python"), "future_origin", None, confidence)
    assert value.confidence == confidence


@pytest.mark.parametrize("confidence", [-0.01, 1.01, float("nan"), True])
def test_facet_rejects_invalid_confidence(confidence: object) -> None:
    with pytest.raises(ValueError, match="confidence"):
        ResourceFacet("resource-1", Facet("tag", "python"), "user", None, confidence)  # type: ignore[arg-type]


def test_search_scope_freezes_open_vocabulary_and_facets() -> None:
    scope = SearchScope(
        resource_kinds=["future_kind"],  # type: ignore[arg-type]
        modalities=["future_modality"],  # type: ignore[arg-type]
        facets_all=[Facet("project", "mdrack")],  # type: ignore[arg-type]
    )
    assert scope.resource_kinds == ("future_kind",)
    assert scope.modalities == ("future_modality",)
    assert scope.facets_all == (Facet("project", "mdrack"),)

    with pytest.raises(ValueError):
        SearchScope(resource_kinds=[""])  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        SearchScope(facets_any=["not-a-facet"])  # type: ignore[list-item]


@pytest.mark.parametrize(
    "field_name",
    [
        "resource_kinds",
        "media_types",
        "source_namespaces",
        "representation_kinds",
        "modalities",
        "unit_kinds",
        "facets_any",
        "facets_all",
        "facets_none",
    ],
)
@pytest.mark.parametrize(
    "make_value",
    [
        lambda item: {item},
        lambda item: frozenset({item}),
        lambda item: {"item": item},
        lambda item: "item",
        lambda item: b"item",
        lambda item: (value for value in (item,)),
    ],
)
def test_search_scope_fields_reject_unordered_and_non_sequence_inputs(
    field_name: str,
    make_value: object,
) -> None:
    item: object = Facet("project", "mdrack") if field_name.startswith("facets_") else "item"
    value = make_value(item)  # type: ignore[operator]
    with pytest.raises(ValueError, match="sequence"):
        SearchScope(**{field_name: value})  # type: ignore[arg-type]


@pytest.mark.parametrize("weight", [0, -1, float("nan"), True])
def test_search_branches_require_positive_finite_weight(weight: object) -> None:
    with pytest.raises(ValueError, match="weight"):
        LexicalBranch("lexical", "query", weight=weight)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="weight"):
        VectorBranch("semantic", "space-1", (1.0,), weight=weight)  # type: ignore[arg-type]


def test_search_branch_candidate_limits_and_query_vector_are_validated() -> None:
    with pytest.raises(ValueError, match="query"):
        LexicalBranch("lexical", " ")
    with pytest.raises(ValueError, match="candidate_limit"):
        LexicalBranch("lexical", "query", candidate_limit=0)
    with pytest.raises(ValueError, match="vector"):
        VectorBranch("semantic", "space-1", ())
    with pytest.raises(ValueError, match="candidate_limit"):
        VectorBranch("semantic", "space-1", (1.0,), candidate_limit=True)  # type: ignore[arg-type]


def test_search_request_validates_branches_targets_ranges_and_unique_ids() -> None:
    lexical = LexicalBranch("lexical", "query")
    semantic = VectorBranch("semantic", "space-1", (1.0, 2.0))
    request = SearchRequest([lexical], [semantic], SearchScope(), "resource", 10)  # type: ignore[arg-type]
    assert request.lexical_branches == (lexical,)
    assert request.vector_branches == (semantic,)

    invalid_requests = (
        lambda: SearchRequest((), (), SearchScope(), "unit", 10),
        lambda: SearchRequest((lexical,), (), SearchScope(), "unknown", 10),
        lambda: SearchRequest((lexical,), (), SearchScope(), "unit", 0),
        lambda: SearchRequest((lexical,), (), SearchScope(), "unit", 1, rrf_k=True),
        lambda: SearchRequest(
            (lexical,),
            (VectorBranch("lexical", "space-1", (1.0,)),),
            SearchScope(),
            "unit",
            1,
        ),
        lambda: SearchRequest((lexical,), (), SearchScope(), "unit", 1, allow_partial=1),  # type: ignore[arg-type]
        lambda: SearchRequest((lexical,), (), SearchScope(), "unit", 1, request_id=""),
    )
    for create in invalid_requests:
        with pytest.raises(ValueError):
            create()


@pytest.mark.parametrize(
    "make_value",
    [
        lambda item: {item},
        lambda item: frozenset({item}),
        lambda item: {"item": item},
        lambda item: "item",
        lambda item: b"item",
        lambda item: (value for value in (item,)),
    ],
)
@pytest.mark.parametrize("branch_field", ["lexical_branches", "vector_branches"])
def test_search_request_branches_reject_unordered_and_non_sequence_inputs(
    make_value: object,
    branch_field: str,
) -> None:
    lexical = LexicalBranch("lexical", "query")
    semantic = VectorBranch("semantic", "space-1", (1.0,))
    values: dict[str, object] = {
        "lexical_branches": [lexical],
        "vector_branches": [semantic],
    }
    item = lexical if branch_field == "lexical_branches" else semantic
    values[branch_field] = make_value(item)  # type: ignore[operator]

    with pytest.raises(ValueError, match=branch_field):
        SearchRequest(
            values["lexical_branches"],  # type: ignore[arg-type]
            values["vector_branches"],  # type: ignore[arg-type]
            SearchScope(),
            "unit",
            1,
        )


def test_ordered_list_and_tuple_inputs_preserve_order_and_freeze_to_tuples() -> None:
    first_facet = Facet("project", "mdrack")
    second_facet = Facet("language", "ru")
    scope = SearchScope(
        resource_kinds=["document", "image"],  # type: ignore[arg-type]
        media_types=("text/markdown", "image/png"),
        source_namespaces=["vault", "upload"],  # type: ignore[arg-type]
        representation_kinds=("source_text", "ocr_text"),
        modalities=["text", "image"],  # type: ignore[arg-type]
        unit_kinds=("chunk", "whole_resource"),
        facets_any=[first_facet, second_facet],  # type: ignore[arg-type]
        facets_all=(second_facet, first_facet),
        facets_none=[first_facet],  # type: ignore[arg-type]
    )
    first_lexical = LexicalBranch("lexical-first", "first")
    second_lexical = LexicalBranch("lexical-second", "second")
    first_vector = VectorBranch("vector-first", "space-1", [1.0, 2.0])  # type: ignore[arg-type]
    second_vector = VectorBranch("vector-second", "space-1", (3.0, 4.0))
    request = SearchRequest(
        [first_lexical, second_lexical],  # type: ignore[arg-type]
        (first_vector, second_vector),
        scope,
        "resource",
        10,
    )

    assert scope.resource_kinds == ("document", "image")
    assert scope.media_types == ("text/markdown", "image/png")
    assert scope.source_namespaces == ("vault", "upload")
    assert scope.representation_kinds == ("source_text", "ocr_text")
    assert scope.modalities == ("text", "image")
    assert scope.unit_kinds == ("chunk", "whole_resource")
    assert scope.facets_any == (first_facet, second_facet)
    assert scope.facets_all == (second_facet, first_facet)
    assert scope.facets_none == (first_facet,)
    assert request.lexical_branches == (first_lexical, second_lexical)
    assert request.vector_branches == (first_vector, second_vector)
    assert first_vector.vector == (1.0, 2.0)
    assert second_vector.vector == (3.0, 4.0)


def test_ranked_candidate_validates_ids_rank_score_locator_and_metadata() -> None:
    candidate = RankedCandidate(
        "unit-1",
        "resource-1",
        "representation-1",
        1,
        0.25,
        "lexical",
        Locator("whole_resource", {}),
        {"safe": ["result"]},
    )
    assert candidate.raw_score == 0.25
    assert candidate.metadata["safe"] == ("result",)

    for changes in (
        {"rank": 0},
        {"rank": True},
        {"raw_score": float("inf")},
        {"branch_id": ""},
    ):
        with pytest.raises(ValueError):
            replace(candidate, **changes)
