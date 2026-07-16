"""Public compatibility API exercises production RRF offline."""

from __future__ import annotations

import asyncio

from mdrack.domain.indexing import SourceLocator
from mdrack.public_api import HybridRetrievalService, RetrievalCandidate


def _candidate(logical_id: str) -> RetrievalCandidate:
    locator = SourceLocator("root", f"docs/{logical_id}.md", 1, 1, (), f"block_{logical_id}", logical_id)
    return RetrievalCandidate(logical_id, 1.0, f"safe-{logical_id}", locator)


def test_public_api_exposes_rrf_only_contract() -> None:
    result = asyncio.run(
        HybridRetrievalService(reranker=None).retrieve(
            "private query",
            [_candidate("text")],
            [_candidate("semantic")],
            limit=10,
        )
    )

    assert [item.logical_id for item in result.results] == ["text", "semantic"]
    assert [(item.text_rank, item.semantic_rank, item.rrf_rank) for item in result.results] == [
        (1, None, 1),
        (None, 1, 2),
    ]
    assert all(item.rerank_rank is None and item.rerank_score is None for item in result.results)
