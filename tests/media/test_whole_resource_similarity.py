from __future__ import annotations

import pytest

from mdrack.application.resources import ResourceQueryService
from mdrack_core import (
    EmbeddingSpaceRecord,
    Locator,
    PreparedResourceBatch,
    RepresentationRecord,
    ResourceRecord,
    SearchUnitRecord,
    VectorRecord,
)
from mdrack_media import weighted_centroid
from tests.core.fakes.memory_store import MemoryCatalog


def test_weighted_centroid_is_order_independent_and_normalized() -> None:
    first = weighted_centroid({"b": (0.0, 2.0), "a": (2.0, 0.0)}, {"a": 1, "b": 3})
    second = weighted_centroid({"a": (2.0, 0.0), "b": (0.0, 2.0)}, {"b": 3, "a": 1})
    assert first == second
    assert first[0] == pytest.approx(0.316227766)
    assert first[1] == pytest.approx(0.948683298)


def test_weighted_centroid_rejects_zero_norm() -> None:
    with pytest.raises(ValueError, match="zero norm"):
        weighted_centroid({"a": (1.0, 0.0), "b": (-1.0, 0.0)}, {"a": 1, "b": 1})


class Catalog(MemoryCatalog):
    def resolve_embedding_space(self, *, fingerprint: str, dimensions: int):
        return next(
            (
                space
                for space in self._spaces.values()
                if space.fingerprint == fingerprint and space.dimensions == dimensions
            ),
            None,
        )


def _resource(resource_id: str, vector: tuple[float, float]) -> PreparedResourceBatch:
    representation_id = f"representation-{resource_id}"
    unit_id = f"whole-{resource_id}"
    return PreparedResourceBatch(
        ResourceRecord(
            resource_id,
            "video",
            "video/mp4",
            "fixture",
            Locator("video", {"id": resource_id}),
        ),
        (
            RepresentationRecord(
                representation_id,
                resource_id,
                "transcript_text",
                "text",
                "spoken transcript",
            ),
        ),
        (
            SearchUnitRecord(
                unit_id,
                resource_id,
                representation_id,
                "whole_resource",
                "text",
                "spoken transcript",
                Locator("whole_media", {}),
                0,
                metadata={
                    "similarity_basis": "textual_content",
                    "aggregation": "token_weighted_centroid_v1",
                },
            ),
        ),
        (EmbeddingSpaceRecord("text-space", 2, "cosine", "text-fingerprint"),),
        (VectorRecord(unit_id, "text-space", vector),),
    )


def test_whole_video_similarity_is_text_only_and_selects_stable_evidence() -> None:
    catalog = Catalog(enforce_resource_contract=True)
    for batch in (
        _resource("query", (1.0, 0.0)),
        _resource("near", (0.9, 0.1)),
        _resource("far", (0.1, 0.9)),
    ):
        catalog.replace_resource(batch)

    first = ResourceQueryService(catalog).find_textual_similarity(
        "whole-query",
        "text-space",
        aggregation="token_weighted_centroid_v1",
        expected_fingerprint="text-fingerprint",
        limit=2,
    )
    second = ResourceQueryService(catalog).find_textual_similarity(
        "whole-query",
        "text-space",
        aggregation="token_weighted_centroid_v1",
        expected_fingerprint="text-fingerprint",
        limit=2,
    )

    assert first == second
    assert [item.resource_id for item in first.results] == ["near", "far"]
    assert first.results[0].evidence[0].unit_id == "whole-near"
    assert first.similarity_basis == "textual_content"
    assert first.aggregation == "token_weighted_centroid_v1"
