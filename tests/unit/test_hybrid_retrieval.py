"""Deterministic offline RRF contracts for production v0.2 retrieval."""

from __future__ import annotations

import pytest

from mdrack.application.retrieval import HybridRetrievalService
from mdrack.domain.indexing import SourceLocator
from mdrack.domain.retrieval import RetrievalCandidate


def _candidate(logical_id: str, score: float = 1.0) -> RetrievalCandidate:
    locator = SourceLocator(
        "root",
        f"docs/{logical_id}.md",
        1,
        1,
        (),
        f"block_{logical_id}",
        logical_id,
    )
    return RetrievalCandidate(
        logical_id=logical_id,
        score=score,
        content_preview="safe preview",
        source_locator=locator,
    )


def test_canonical_candidate_exposes_public_logical_identity_and_locator() -> None:
    candidate = _candidate("chunk_1", 0.75)

    assert candidate.logical_id == "chunk_1"
    assert candidate.source_locator.chunk_id == "chunk_1"
    assert candidate.score == 0.75


@pytest.mark.asyncio
async def test_rrf_is_deterministic_deduplicates_and_preserves_rank_history() -> None:
    service = HybridRetrievalService(rrf_k=60, reranker=None)
    text = [_candidate("shared"), _candidate("text"), _candidate("shared")]
    semantic = [_candidate("semantic"), _candidate("shared")]

    first = await service.retrieve("private query", text, semantic, limit=10)
    second = await service.retrieve("private query", text, semantic, limit=10)

    assert first == second
    assert [item.logical_id for item in first.results] == ["shared", "semantic", "text"]
    shared = first.results[0]
    assert shared.text_rank == 1
    assert shared.semantic_rank == 2
    assert shared.rrf_rank == 1
    assert shared.rrf_score == pytest.approx(1 / 61 + 1 / 62)
    assert all(item.rerank_rank is None and item.rerank_score is None for item in first.results)


@pytest.mark.asyncio
async def test_rrf_handles_empty_branches() -> None:
    service = HybridRetrievalService(rrf_k=60)

    text_only = await service.retrieve("query", [_candidate("a")], [], limit=10)
    semantic_only = await service.retrieve("query", [], [_candidate("b")], limit=10)
    empty = await service.retrieve("query", [], [], limit=10)

    assert text_only.results[0].text_rank == 1
    assert text_only.results[0].semantic_rank is None
    assert semantic_only.results[0].semantic_rank == 1
    assert semantic_only.results[0].text_rank is None
    assert empty.results == ()


def test_runtime_reranker_configuration_is_rejected() -> None:
    with pytest.raises(ValueError, match="not supported"):
        HybridRetrievalService(reranker=object())
