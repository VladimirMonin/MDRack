"""Public embedded API exercises hybrid RRF and reranker fail-open offline."""

from __future__ import annotations

import asyncio

from mdrack.adapters.lmstudio.fakes import DeterministicReranker
from mdrack.public_api import HybridRetrievalService, RetrievalCandidate


def _candidate(candidate_id: str) -> RetrievalCandidate:
    return RetrievalCandidate(candidate_id, 1.0, f"safe-{candidate_id}")


def test_public_api_exposes_exact_unsupported_fail_open_contract() -> None:
    result = asyncio.run(
        HybridRetrievalService(
            reranker=DeterministicReranker(mode="unsupported")
        ).retrieve(
            "private query",
            [_candidate("text")],
            [_candidate("semantic")],
            limit=10,
            rerank_requested=True,
        )
    )

    assert [item.candidate_id for item in result.results] == ["text", "semantic"]
    assert [(item.text_rank, item.semantic_rank, item.rrf_rank) for item in result.results] == [
        (1, None, 1),
        (None, 1, 2),
    ]
    assert result.reranking.to_dict() == {
        "requested": True,
        "applied": False,
        "degraded": True,
        "reason": "unsupported_by_runtime",
    }
