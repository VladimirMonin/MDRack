from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from mdrack.application.resources import ResourceQueryScope, ResourceQueryService
from mdrack_core import (
    EmbeddingSpaceRecord,
    Locator,
    PreparedResourceBatch,
    RepresentationRecord,
    ResourceRecord,
    SearchUnitRecord,
    VectorRecord,
)
from mdrack_sqlite import SQLiteCatalog
from tests.core.fakes.memory_store import MemoryCatalog


def _batch(
    resource_id: str,
    vector: tuple[float, ...],
    *,
    resource_kind: str = "document",
    aggregation: str | None = "direct_text_v1",
    fingerprint: str = "text-fingerprint",
    modality: str = "text",
    similarity_basis: str = "textual_content",
) -> PreparedResourceBatch:
    representation_id = f"representation-{resource_id}"
    unit_id = f"whole-{resource_id}"
    metadata = {"similarity_basis": similarity_basis}
    if aggregation is not None:
        metadata["aggregation"] = aggregation
    return PreparedResourceBatch(
        ResourceRecord(
            resource_id,
            resource_kind,
            "text/markdown" if resource_kind == "document" else f"{resource_kind}/fixture",
            "fixture",
            Locator("resource", {"id": resource_id}),
        ),
        (
            RepresentationRecord(
                representation_id,
                resource_id,
                "whole_resource_text",
                modality,
                f"text for {resource_id}",
                producer_fingerprint=f"aggregation:{aggregation or 'absent'}",
                metadata=metadata,
            ),
        ),
        (
            SearchUnitRecord(
                unit_id,
                resource_id,
                representation_id,
                "whole_resource",
                modality,
                f"text for {resource_id}",
                Locator("whole_text", {"resource_id": resource_id}),
                0,
                metadata=metadata,
            ),
        ),
        (EmbeddingSpaceRecord("text-space", 2, "cosine", fingerprint),),
        (VectorRecord(unit_id, "text-space", vector),),
    )


class SimilarityCatalog(MemoryCatalog):
    def resolve_embedding_space(self, *, fingerprint: str, dimensions: int):
        matches = [
            space
            for space in self._spaces.values()
            if space.fingerprint == fingerprint and space.dimensions == dimensions
        ]
        return matches[0] if len(matches) == 1 else None


def test_textual_similarity_is_explicit_cross_kind_and_evidence_bearing() -> None:
    catalog = SimilarityCatalog(enforce_resource_contract=True)
    for batch in (
        _batch("query", (1.0, 0.0), aggregation="direct_text_v1"),
        _batch("audio", (0.9, 0.1), resource_kind="audio", aggregation="token_weighted_centroid_v1"),
        _batch("video", (0.8, 0.2), resource_kind="video", aggregation="token_weighted_centroid_v1"),
    ):
        catalog.replace_resource(batch)

    result = ResourceQueryService(catalog).find_textual_similarity(
        "whole-query",
        "text-space",
        aggregation="direct_text_v1",
        expected_fingerprint="text-fingerprint",
        scope=ResourceQueryScope(resource_kinds=("audio", "video")),
        limit=5,
    )

    payload = result.to_dict()
    assert payload["query_resource_id"] == "query"
    assert payload["similarity_basis"] == "textual_content"
    assert payload["aggregation"] == "direct_text_v1"
    assert [item["resource_id"] for item in payload["results"]] == ["audio", "video"]
    assert all(item["resource_id"] != "query" for item in payload["results"])
    assert payload["results"][0]["evidence"] == [
        {
            "branch_id": "similarity",
            "unit_id": "whole-audio",
            "representation_id": "representation-audio",
            "locator": {"kind": "whole_text", "payload": {"resource_id": "audio"}},
        }
    ]
    assert "visual" not in repr(payload).lower()
    assert "acoustic" not in repr(payload).lower()


def test_textual_similarity_fingerprint_mismatch_fails_closed() -> None:
    catalog = SimilarityCatalog(enforce_resource_contract=True)
    catalog.replace_resource(_batch("query", (1.0, 0.0)))
    catalog.replace_resource(_batch("candidate", (0.9, 0.1)))

    result = ResourceQueryService(catalog).find_textual_similarity(
        "whole-query",
        "text-space",
        aggregation="direct_text_v1",
        expected_fingerprint="different-fingerprint",
    )

    assert result.results == ()
    assert result.degraded is True
    assert result.degraded_reason == "incompatible_vector_space"
    assert result.similarity_basis == "textual_content"


def test_textual_similarity_rejects_non_textual_or_ambiguous_source_identity() -> None:
    catalog = SimilarityCatalog(enforce_resource_contract=True)
    ambiguous = _batch("query", (1.0, 0.0))
    ambiguous = replace(
        ambiguous,
        units=(replace(ambiguous.units[0], modality="image"),),
    )
    catalog.replace_resource(ambiguous)

    result = ResourceQueryService(catalog).find_textual_similarity(
        "whole-query",
        "text-space",
        aggregation="direct_text_v1",
        expected_fingerprint="text-fingerprint",
    )

    assert result.results == ()
    assert result.degraded_reason == "textual_similarity_identity_unavailable"


@pytest.mark.parametrize(
    "requested_aggregation",
    ("direct_text_v1", "token_weighted_centroid_v1"),
)
def test_textual_similarity_requires_exact_persisted_query_aggregation(
    requested_aggregation: str,
) -> None:
    catalog = SimilarityCatalog(enforce_resource_contract=True)
    catalog.replace_resource(_batch("query", (1.0, 0.0), aggregation=None))
    catalog.replace_resource(_batch("candidate", (0.9, 0.1)))

    result = ResourceQueryService(catalog).find_textual_similarity(
        "whole-query",
        "text-space",
        aggregation=requested_aggregation,
        expected_fingerprint="text-fingerprint",
    )

    assert result.results == ()
    assert result.degraded is True
    assert result.degraded_reason == "textual_similarity_identity_unavailable"
    assert result.aggregation is None


def _assert_non_text_and_unrecognized_candidates_are_prelimited(catalog: object) -> None:
    for batch in (
        _batch("query", (1.0, 0.0)),
        _batch(
            "visual",
            (1.0, 0.0),
            modality="image",
            similarity_basis="visual_content",
        ),
        _batch(
            "acoustic",
            (0.999, 0.001),
            modality="audio",
            similarity_basis="acoustic_content",
        ),
        _batch("unrecognized", (0.998, 0.002), aggregation=None),
        _batch("valid", (0.8, 0.2), aggregation="token_weighted_centroid_v1"),
    ):
        catalog.replace_resource(batch)  # type: ignore[attr-defined]

    result = ResourceQueryService(catalog).find_textual_similarity(  # type: ignore[arg-type]
        "whole-query",
        "text-space",
        aggregation="direct_text_v1",
        expected_fingerprint="text-fingerprint",
        limit=1,
    )

    assert result.degraded is False
    assert [item.resource_id for item in result.results] == ["valid"]


def test_memory_textual_similarity_prefilters_candidate_identity() -> None:
    _assert_non_text_and_unrecognized_candidates_are_prelimited(
        SimilarityCatalog(enforce_resource_contract=True)
    )


def test_sqlite_textual_similarity_prefilters_candidate_identity(tmp_path: Path) -> None:
    with SQLiteCatalog.create(tmp_path / "textual-scope.sqlite3") as catalog:
        _assert_non_text_and_unrecognized_candidates_are_prelimited(catalog)
