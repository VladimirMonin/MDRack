"""Deterministic offline hybrid retrieval and reranker contracts."""

from __future__ import annotations

import pytest

from mdrack.adapters.lmstudio.fakes import DeterministicReranker
from mdrack.application.retrieval import HybridRetrievalService
from mdrack.domain.retrieval import RetrievalCandidate
from mdrack.ports.reranker import RerankDocument, RerankerUnavailable, RerankScore


class _StaticReranker:
    def __init__(self, scores: list[RerankScore]) -> None:
        self.scores = scores

    async def rerank(
        self, query: str, documents: list[RerankDocument], *, top_n: int
    ) -> list[RerankScore]:
        del query, documents, top_n
        return self.scores


class _UnavailableReranker:
    def __init__(self, reason: str) -> None:
        self.reason = reason

    async def rerank(
        self, query: str, documents: list[RerankDocument], *, top_n: int
    ) -> list[RerankScore]:
        del query, documents, top_n
        raise RerankerUnavailable(self.reason)


def _candidate(candidate_id: str, text: str, score: float = 1.0) -> RetrievalCandidate:
    return RetrievalCandidate(candidate_id=candidate_id, score=score, rerank_text=text)


@pytest.mark.asyncio
async def test_rrf_is_deterministic_deduplicates_and_preserves_rank_history() -> None:
    service = HybridRetrievalService(rrf_k=60)
    text = [_candidate("shared", "shared"), _candidate("text", "text"), _candidate("shared", "duplicate")]
    semantic = [_candidate("semantic", "semantic"), _candidate("shared", "shared")]

    first = await service.retrieve("private query", text, semantic, limit=10)
    second = await service.retrieve("private query", text, semantic, limit=10)

    assert first == second
    assert [item.candidate_id for item in first.results] == ["shared", "semantic", "text"]
    shared = first.results[0]
    assert shared.text_rank == 1
    assert shared.semantic_rank == 2
    assert shared.rrf_rank == 1
    assert shared.rrf_score == pytest.approx(1 / 61 + 1 / 62)
    assert len({item.candidate_id for item in first.results}) == 3


@pytest.mark.asyncio
async def test_rrf_handles_empty_branches() -> None:
    service = HybridRetrievalService(rrf_k=60)

    text_only = await service.retrieve("query", [_candidate("a", "a")], [], limit=10)
    semantic_only = await service.retrieve("query", [], [_candidate("b", "b")], limit=10)
    empty = await service.retrieve("query", [], [], limit=10)

    assert text_only.results[0].text_rank == 1
    assert text_only.results[0].semantic_rank is None
    assert semantic_only.results[0].semantic_rank == 1
    assert semantic_only.results[0].text_rank is None
    assert empty.results == ()


@pytest.mark.asyncio
async def test_deterministic_reranker_records_rank_and_score() -> None:
    service = HybridRetrievalService(
        rrf_k=60,
        reranker=DeterministicReranker(score_by_candidate={"b": 0.9, "a": 0.2}),
    )

    result = await service.retrieve(
        "query",
        [_candidate("a", "a"), _candidate("b", "b")],
        [],
        limit=10,
        rerank_requested=True,
    )

    assert [item.candidate_id for item in result.results] == ["b", "a"]
    assert [(item.rerank_rank, item.rerank_score) for item in result.results] == [(1, 0.9), (2, 0.2)]
    assert result.reranking.to_dict() == {
        "requested": True,
        "applied": True,
        "degraded": False,
        "reason": None,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,reason", [("unsupported", "unsupported_by_runtime"), ("failure", "provider_error")])
async def test_reranker_fail_open_returns_rrf_results(mode: str, reason: str) -> None:
    service = HybridRetrievalService(rrf_k=60, reranker=DeterministicReranker(mode=mode))

    result = await service.retrieve(
        "query",
        [_candidate("a", "a"), _candidate("b", "b")],
        [],
        limit=10,
        rerank_requested=True,
    )

    assert [item.candidate_id for item in result.results] == ["a", "b"]
    assert all(item.rerank_rank is None for item in result.results)
    assert result.reranking.to_dict() == {
        "requested": True,
        "applied": False,
        "degraded": True,
        "reason": reason,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scores",
    [
        [],
        [RerankScore("a", 0.9)],
        [RerankScore("a", 0.9), RerankScore("a", 0.8)],
        [RerankScore("a", 0.9), RerankScore("unknown", 0.8)],
        [RerankScore("a", float("nan")), RerankScore("b", 0.8)],
        [RerankScore("a", float("inf")), RerankScore("b", 0.8)],
        [RerankScore("a", float("-inf")), RerankScore("b", 0.8)],
    ],
    ids=["empty", "partial", "duplicate", "unknown", "nan", "positive-infinity", "negative-infinity"],
)
async def test_malformed_reranker_response_fails_open_without_mutating_rrf_history(
    scores: list[RerankScore],
) -> None:
    result = await HybridRetrievalService(reranker=_StaticReranker(scores)).retrieve(
        "private query",
        [_candidate("a", "a"), _candidate("b", "b")],
        [],
        limit=10,
        rerank_requested=True,
    )

    assert [item.candidate_id for item in result.results] == ["a", "b"]
    assert [(item.rrf_rank, item.rerank_rank, item.rerank_score) for item in result.results] == [
        (1, None, None),
        (2, None, None),
    ]
    assert result.reranking.to_dict() == {
        "requested": True,
        "applied": False,
        "degraded": True,
        "reason": "provider_error",
    }


@pytest.mark.asyncio
async def test_private_unavailable_reason_is_sanitized_in_status_and_logs(caplog: pytest.LogCaptureFixture) -> None:
    private_marker = "private-query=customer-payroll"
    service = HybridRetrievalService(reranker=_UnavailableReranker(private_marker))

    with caplog.at_level("WARNING", logger="mdrack.application.retrieval"):
        result = await service.retrieve(
            "private query",
            [_candidate("a", "a")],
            [],
            limit=10,
            rerank_requested=True,
        )

    assert result.reranking.to_dict() == {
        "requested": True,
        "applied": False,
        "degraded": True,
        "reason": "provider_error",
    }
    assert private_marker not in caplog.text
    assert [getattr(record, "reason") for record in caplog.records] == ["provider_error"]


@pytest.mark.asyncio
async def test_missing_reranker_is_honest_unsupported_fail_open() -> None:
    result = await HybridRetrievalService().retrieve(
        "query",
        [_candidate("a", "a")],
        [],
        limit=10,
        rerank_requested=True,
    )

    assert result.results[0].candidate_id == "a"
    assert result.reranking.reason == "unsupported_by_runtime"
