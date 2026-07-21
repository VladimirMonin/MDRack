from __future__ import annotations

import pytest

from mdrack.application.retrieval import (
    SEARCH_PRESETS,
    ResourcePresetSearchService,
    build_resource_search_request,
)
from mdrack.ports.embeddings import EmbeddingError
from mdrack_core import (
    TARGET_RESOURCE,
    CatalogExecutionError,
    EmbeddingSpaceRecord,
    ErrorCategory,
    Locator,
    RankedCandidate,
    SearchScope,
)
from mdrack_core.application.retrieval import RetrievalService as CoreRetrievalService


@pytest.mark.parametrize(
    ("preset", "expected"),
    (
        (
            "speech_first",
            {
                "transcript_text": 0.4,
                "transcript_semantic": 0.6,
                "frame_caption_text": 0.14,
                "frame_caption_semantic": 0.21,
                "metadata_text": 0.15,
            },
        ),
        (
            "balanced",
            {
                "transcript_text": 0.4,
                "transcript_semantic": 0.6,
                "frame_caption_text": 0.4,
                "frame_caption_semantic": 0.6,
                "metadata_text": 0.2,
            },
        ),
        (
            "frames_first",
            {
                "transcript_text": 0.24,
                "transcript_semantic": 0.36,
                "frame_caption_text": 0.4,
                "frame_caption_semantic": 0.6,
                "metadata_text": 0.15,
            },
        ),
    ),
)
def test_preset_branch_matrix_is_exact_and_resource_targeted(
    preset: str,
    expected: dict[str, float],
) -> None:
    request = build_resource_search_request(
        "queue architecture",
        preset=preset,
        mode="hybrid",
        query_vector=(1.0, 0.0),
        space_id="text-space",
        expected_fingerprint="text-fingerprint",
        scope=SearchScope(resource_kinds=("video",)),
        limit=7,
        rrf_k=61,
    )

    branches = (*request.lexical_branches, *request.vector_branches)
    assert {branch.branch_id: branch.weight for branch in branches} == expected
    assert request.target == TARGET_RESOURCE
    assert request.limit == 7
    assert request.rrf_k == 61
    assert request.scope.resource_kinds == ("video",)
    assert all(branch.candidate_limit == 100 for branch in branches)
    assert {branch.branch_id: branch.scope_override.representation_kinds for branch in branches} == {
        "transcript_text": ("timed_passage",),
        "transcript_semantic": ("timed_passage",),
        "frame_caption_text": ("frame_caption",),
        "frame_caption_semantic": ("frame_caption",),
        "metadata_text": ("metadata_text",),
    }
    assert {branch.branch_id: branch.scope_override.unit_kinds for branch in branches} == {
        "transcript_text": ("time_segment",),
        "transcript_semantic": ("time_segment",),
        "frame_caption_text": ("frame",),
        "frame_caption_semantic": ("frame",),
        "metadata_text": ("whole_resource",),
    }
    assert all(
        branch.expected_fingerprint == "text-fingerprint"
        for branch in request.vector_branches
    )


def test_preset_modes_select_only_supported_branches() -> None:
    text = build_resource_search_request("query", preset="balanced", mode="text", limit=20)
    semantic = build_resource_search_request(
        "query",
        preset="balanced",
        mode="semantic",
        query_vector=(1.0,),
        space_id="space",
        expected_fingerprint="fingerprint",
        limit=20,
    )

    assert [branch.branch_id for branch in text.lexical_branches] == [
        "transcript_text",
        "frame_caption_text",
        "metadata_text",
    ]
    assert text.vector_branches == ()
    assert semantic.lexical_branches == ()
    assert [branch.branch_id for branch in semantic.vector_branches] == [
        "transcript_semantic",
        "frame_caption_semantic",
    ]
    assert SEARCH_PRESETS["balanced"].metadata_weight == 0.2


@pytest.mark.parametrize("preset", ("automatic", "", "BALANCED"))
def test_unknown_or_automatic_preset_fails_closed(preset: str) -> None:
    with pytest.raises(ValueError, match="preset"):
        build_resource_search_request("query", preset=preset, mode="text")


def test_frame_crowding_contributes_once_per_resource_and_branch() -> None:
    class CrowdedSearchPort:
        def search_lexical(self, branch, *, scope):
            del scope
            if branch.branch_id == "transcript_text":
                resources = ("video-b", "video-c")
            elif branch.branch_id == "frame_caption_text":
                resources = (*("video-a" for _ in range(90)), "video-b", "video-b", "video-c")
            else:
                resources = ()
            return [
                RankedCandidate(
                    f"{branch.branch_id}-{index}",
                    resource_id,
                    f"representation-{branch.branch_id}",
                    index,
                    float(len(resources) - index + 1),
                    branch.branch_id,
                    Locator("fixture", {"index": index}),
                )
                for index, resource_id in enumerate(resources, start=1)
            ]

        def search_vector(self, branch, *, scope):
            del branch, scope
            return []

    request = build_resource_search_request(
        "query",
        preset="balanced",
        mode="text",
        limit=3,
    )
    result = CoreRetrievalService(CrowdedSearchPort()).search(request)

    assert [item.resource_id for item in result.items] == ["video-b", "video-c", "video-a"]
    assert len({item.resource_id for item in result.items}) == 3
    assert len(result.items[-1].evidence) == request.evidence_limit_per_resource
    assert {candidate.branch_id for candidate in result.items[-1].evidence} == {
        "frame_caption_text"
    }


class _PresetProvider:
    def __init__(self, value: object) -> None:
        self.value = value

    async def embed_query(self, text: str, profile: str = "default"):  # type: ignore[no-untyped-def]
        del text, profile
        if isinstance(self.value, BaseException):
            raise self.value
        return self.value


class _PresetPort:
    def __init__(
        self,
        *,
        resolver: object = EmbeddingSpaceRecord(
            "text-space",
            2,
            "cosine",
            "text-fingerprint",
        ),
        vector_error: BaseException | None = None,
    ) -> None:
        self.resolver = resolver
        self.vector_error = vector_error

    def resolve_embedding_space(self, *, fingerprint: str, dimensions: int):  # type: ignore[no-untyped-def]
        del fingerprint, dimensions
        if isinstance(self.resolver, BaseException):
            raise self.resolver
        return self.resolver

    def search_lexical(self, branch, *, scope):  # type: ignore[no-untyped-def]
        del scope
        return [self._candidate(branch.branch_id)]

    def search_vector(self, branch, *, scope):  # type: ignore[no-untyped-def]
        del scope
        if self.vector_error is not None:
            raise self.vector_error
        return [self._candidate(branch.branch_id)]

    @staticmethod
    def _candidate(branch_id: str) -> RankedCandidate:
        return RankedCandidate(
            "unit",
            "resource",
            "representation",
            1,
            1.0,
            branch_id,
            Locator("fixture", {}),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_value", "reason"),
    (
        ([], "embedding_provider_error"),
        ([float("nan"), 0.0], "embedding_provider_error"),
        (EmbeddingError("private"), "embedding_provider_error"),
        (RuntimeError("private"), "semantic_search_error"),
    ),
)
async def test_hybrid_preset_falls_back_for_invalid_or_failed_provider_vectors(
    provider_value: object,
    reason: str,
) -> None:
    result = await ResourcePresetSearchService(
        _PresetPort(),
        embedding_provider=_PresetProvider(provider_value),  # type: ignore[arg-type]
        embedding_fingerprint="text-fingerprint",
    ).search("query", mode="hybrid")

    assert [item.resource_id for item in result.results] == ["resource"]
    assert result.degraded is True
    assert result.degraded_reason == reason


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("resolver", "reason"),
    (
        (None, "incompatible_embedding_profile"),
        (
            EmbeddingSpaceRecord("other", 2, "cosine", "other-fingerprint"),
            "incompatible_embedding_profile",
        ),
        (CatalogExecutionError(ErrorCategory.CATALOG_ERROR), "adapter_error"),
        (CatalogExecutionError(ErrorCategory.ADAPTER_TIMEOUT), "adapter_timeout"),
        (TimeoutError("private"), "adapter_timeout"),
        (RuntimeError("private"), "adapter_error"),
    ),
)
async def test_hybrid_preset_falls_back_for_missing_ambiguous_or_failed_resolver(
    resolver: object,
    reason: str,
) -> None:
    result = await ResourcePresetSearchService(
        _PresetPort(resolver=resolver),
        embedding_provider=_PresetProvider([1.0, 0.0]),  # type: ignore[arg-type]
        embedding_fingerprint="text-fingerprint",
    ).search("query", mode="hybrid")

    assert [item.resource_id for item in result.results] == ["resource"]
    assert result.degraded is True
    assert result.degraded_reason == reason


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "vector_error",
    (
        CatalogExecutionError(ErrorCategory.CATALOG_ERROR),
        TimeoutError("private"),
        RuntimeError("private"),
    ),
)
async def test_preset_search_normalizes_adapter_failures_by_mode(
    vector_error: BaseException,
) -> None:
    service = ResourcePresetSearchService(
        _PresetPort(vector_error=vector_error),
        embedding_provider=_PresetProvider([1.0, 0.0]),  # type: ignore[arg-type]
        embedding_fingerprint="text-fingerprint",
    )

    hybrid = await service.search("query", mode="hybrid")
    semantic = await service.search("query", mode="semantic")

    assert [item.resource_id for item in hybrid.results] == ["resource"]
    assert hybrid.degraded is True
    assert semantic.results == ()
    assert semantic.degraded is True
