from __future__ import annotations

from dataclasses import dataclass

from mdrack.application.resources import (
    ResourceQueryService,
    TextualWholeResourceProjection,
    UnifiedTextSearchService,
    resolve_unified_text_scope,
)
from mdrack_core.domain import (
    EmbeddingSpaceRecord,
    Locator,
    PreparedResourceBatch,
    RepresentationRecord,
    ResourceRecord,
    SearchUnitRecord,
    VectorRecord,
)
from tests.core.fakes.memory_store import MemoryCatalog

_SPACE = EmbeddingSpaceRecord("text-space", 2, "cosine", "text-fingerprint", {})


class _QueryProvider:
    async def embed_query(self, text: str, profile: str = "default") -> list[float]:
        del text, profile
        return [1.0, 0.0]


@dataclass(frozen=True)
class _Resolver:
    projections: dict[str, tuple[TextualWholeResourceProjection, ...]]

    def resolve_textual_whole_resource_units(
        self,
        resource_id: str,
    ) -> tuple[TextualWholeResourceProjection, ...]:
        return self.projections.get(resource_id, ())


def _batch(
    *,
    resource_id: str,
    resource_kind: str,
    unit_id: str,
    text: str,
    vector: tuple[float, float] = (1.0, 0.0),
    representation_kind: str = "whole_resource_text",
    unit_kind: str = "whole_resource",
    similarity_basis: str | None = None,
    locator_kind: str = "fixture_unit",
    locator_payload: dict[str, object] | None = None,
    space: EmbeddingSpaceRecord = _SPACE,
) -> PreparedResourceBatch:
    representation_id = f"{unit_id}-representation"
    metadata = (
        {}
        if similarity_basis is None
        else {
            "aggregation": "token_weighted_centroid_v1",
            "similarity_basis": similarity_basis,
        }
    )
    return PreparedResourceBatch(
        ResourceRecord(
            resource_id,
            resource_kind,
            "text/plain",
            "fixture",
            Locator("fixture", {"resource_id": resource_id}),
        ),
        (
            RepresentationRecord(
                representation_id,
                resource_id,
                representation_kind,
                "text",
                text,
                metadata=metadata,
            ),
        ),
        (
            SearchUnitRecord(
                unit_id,
                resource_id,
                representation_id,
                unit_kind,
                "text",
                text,
                Locator(
                    locator_kind,
                    locator_payload or {"resource_id": resource_id, "unit_id": unit_id},
                ),
                0,
                metadata=metadata,
            ),
        ),
        (space,),
        (VectorRecord(unit_id, space.space_id, vector),),
    )


def _visual_batch(*, resource_id: str, unit_id: str) -> PreparedResourceBatch:
    representation_id = f"{unit_id}-representation"
    locator = Locator("whole_image", {"source_ref": "/PRIVATE_ROOT_SENTINEL/visual.png"})
    return PreparedResourceBatch(
        ResourceRecord(resource_id, "image", "image/png", "fixture", locator),
        (RepresentationRecord(representation_id, resource_id, "visual", "image", None),),
        (
            SearchUnitRecord(
                unit_id,
                resource_id,
                representation_id,
                "whole_resource",
                "image",
                None,
                locator,
                0,
            ),
        ),
        (_SPACE,),
        (VectorRecord(unit_id, _SPACE.space_id, (1.0, 0.0)),),
    )


def test_unified_scope_aliases_compile_to_existing_core_scope_fields() -> None:
    assert resolve_unified_text_scope("all").core().modalities == ("text",)
    assert resolve_unified_text_scope("notes").core().resource_kinds == ("document",)
    assert resolve_unified_text_scope("audio").core().resource_kinds == ("audio",)
    video_scope = resolve_unified_text_scope("video").core()
    assert video_scope.resource_kinds == ("video",)
    assert video_scope.representation_kinds == ("timed_passage",)
    assert video_scope.unit_kinds == ("time_segment",)
    frame_scope = resolve_unified_text_scope("frames").core()
    assert frame_scope.representation_kinds == ("frame_caption",)
    assert frame_scope.unit_kinds == ("frame",)
    assert resolve_unified_text_scope("images").core().resource_kinds == ("image",)


async def test_unified_text_search_filters_by_alias_and_degrades_hybrid_without_provider() -> None:
    catalog = MemoryCatalog()
    catalog.replace_resource(
        _batch(
            resource_id="document-1",
            resource_kind="document",
            unit_id="document-unit",
            text="note needle",
            representation_kind="retrieval_text",
            unit_kind="text_chunk",
        )
    )
    catalog.replace_resource(
        _batch(
            resource_id="image-1",
            resource_kind="image",
            unit_id="image-unit",
            text="image needle",
            similarity_basis="image_text_aggregate",
            locator_kind="whole_image",
            locator_payload={"source_ref": "/PRIVATE_ROOT_SENTINEL/image.png"},
        )
    )
    catalog.replace_resource(
        _batch(
            resource_id="video-1",
            resource_kind="video",
            unit_id="frame-unit",
            text="frame needle",
            representation_kind="frame_caption",
            unit_kind="frame",
        )
    )
    catalog.replace_resource(
        _batch(
            resource_id="video-transcript-1",
            resource_kind="video",
            unit_id="time-segment-unit",
            text="transcript needle",
            representation_kind="timed_passage",
            unit_kind="time_segment",
        )
    )

    service = UnifiedTextSearchService(catalog)
    images = await service.search("needle", scope="images", mode="text")
    video = await service.search("needle", scope="video", mode="text")
    frames = await service.search("needle", scope="frames", mode="text")
    hybrid = await service.search("needle", scope="notes", mode="hybrid")

    assert [item.resource_id for item in images.results] == ["image-1"]
    assert images.results[0].resource_kind == "image"
    assert images.results[0].evidence[0].unit_kind == "whole_resource"
    assert images.results[0].evidence[0].locator == {"kind": "whole_image", "payload": {}}
    assert "PRIVATE_ROOT_SENTINEL" not in repr(images.to_dict())
    assert [item.resource_id for item in video.results] == ["video-transcript-1"]
    assert video.results[0].evidence[0].unit_kind == "time_segment"
    assert [item.resource_id for item in frames.results] == ["video-1"]
    assert frames.results[0].evidence[0].unit_kind == "frame"
    assert [item.resource_id for item in hybrid.results] == ["document-1"]
    assert hybrid.degraded is True
    assert hybrid.degraded_reason == "embedding_provider_unavailable"


async def test_unified_semantic_search_fuses_all_compatible_embedding_spaces() -> None:
    catalog = MemoryCatalog()
    fingerprint = "a" * 64
    first_space = EmbeddingSpaceRecord(
        "first-text-space",
        2,
        "cosine",
        fingerprint,
        {},
    )
    second_space = EmbeddingSpaceRecord(
        "second-text-space",
        2,
        "cosine",
        f"sha256:{fingerprint}",
        {},
    )
    catalog.replace_resource(
        _batch(
            resource_id="document-1",
            resource_kind="document",
            unit_id="document-unit",
            text="document",
            space=first_space,
        )
    )
    catalog.replace_resource(
        _batch(
            resource_id="video-1",
            resource_kind="video",
            unit_id="video-unit",
            text="video",
            representation_kind="timed_passage",
            unit_kind="time_segment",
            space=second_space,
        )
    )

    result = await UnifiedTextSearchService(
        catalog,
        embedding_provider=_QueryProvider(),  # type: ignore[arg-type]
        embedding_fingerprint=fingerprint,
    ).search("meaning", scope="all", mode="semantic")

    assert result.degraded is False
    assert {item.resource_id for item in result.results} == {"document-1", "video-1"}


def test_resource_similarity_resolves_only_one_textual_whole_unit_and_rejects_frames() -> None:
    catalog = MemoryCatalog()
    catalog.replace_resource(
        _batch(
            resource_id="document-a",
            resource_kind="document",
            unit_id="query-unit",
            text="query",
            similarity_basis="markdown_retrieval_text",
        )
    )
    catalog.replace_resource(
        _batch(
            resource_id="document-b",
            resource_kind="document",
            unit_id="candidate-unit",
            text="candidate",
            similarity_basis="markdown_retrieval_text",
        )
    )
    catalog.replace_resource(
        _batch(
            resource_id="video-a",
            resource_kind="video",
            unit_id="video-a-whole-unit",
            text="video transcript a",
            similarity_basis="transcript_text",
        )
    )
    catalog.replace_resource(
        _batch(
            resource_id="video-b",
            resource_kind="video",
            unit_id="video-b-whole-unit",
            text="video transcript b",
            vector=(0.99, 0.01),
            similarity_basis="transcript_text",
        )
    )
    catalog.replace_resource(
        _batch(
            resource_id="video-frame-only",
            resource_kind="video",
            unit_id="a-frame-whole-unit",
            text="frame caption",
            similarity_basis="frame_caption_text",
        )
    )
    catalog.replace_resource(_visual_batch(resource_id="image-visual-only", unit_id="a-visual-unit"))
    resolver = _Resolver(
        {
            "document-a": (TextualWholeResourceProjection("document-a", "query-unit", _SPACE),),
            "video-a": (TextualWholeResourceProjection("video-a", "video-a-whole-unit", _SPACE),),
        }
    )
    service = ResourceQueryService(catalog, whole_resource_resolver=resolver)
    ambiguous_service = ResourceQueryService(
        catalog,
        whole_resource_resolver=_Resolver(
            {
                "document-a": (
                    TextualWholeResourceProjection("document-a", "query-unit", _SPACE),
                    TextualWholeResourceProjection(
                        "document-a",
                        "query-unit",
                        EmbeddingSpaceRecord("second-space", 2, "cosine", "fixture-space-v2", {}),
                    ),
                ),
            }
        ),
    )

    result = service.find_similar_resource("document-a", scope="all", limit=1)
    video_result = service.find_similar_resource("video-a", scope="video", limit=1)
    ambiguous = ambiguous_service.find_similar_resource("document-a", scope="all")
    rejected = service.find_similar_resource("video-frame-only", scope="frames")
    visual_only = service.find_similar_resource("image-visual-only", scope="images")

    assert result.degraded is False
    assert [item.resource_id for item in result.results] == ["document-b"]
    assert video_result.degraded is False
    assert [item.resource_id for item in video_result.results] == ["video-b"]
    assert ambiguous.results == ()
    assert ambiguous.degraded_reason == "textual_similarity_identity_ambiguous"
    assert rejected.results == ()
    assert rejected.degraded is True
    assert rejected.degraded_reason == "scope_not_similarity_compatible"
    assert visual_only.results == ()
    assert visual_only.degraded_reason == "textual_similarity_identity_unavailable"
