"""Retrieval evaluation runner — runs queries and computes metrics."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from mdrack.config.models import MDRackConfig
from mdrack.eval.metrics import mrr, ndcg_at_k, precision_at_k, recall_at_k
from mdrack.eval.queries import EvalQuery, EvalQuerySet
from mdrack.ports.embeddings import EmbeddingProvider
from mdrack.search.hybrid import hybrid_search
from mdrack.search.semantic import semantic_search
from mdrack.search.text import text_search

logger = logging.getLogger(__name__)


@dataclass
class EvalQueryResult:
    """Result for a single eval query."""

    query_id: str
    query: str
    mode: str
    retrieved_ids: list[str]
    expected_ids: list[str]
    k: int
    recall_at_k: float
    mrr: float
    precision_at_k: float
    ndcg_at_k: float = 0.0
    conditions_met: bool = True
    error: str | None = None


@dataclass
class EvalReport:
    """Aggregated retrieval evaluation report."""

    results: list[EvalQueryResult] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)


def _effective_k(query: EvalQuery, default_k: int) -> int:
    recall_at = query.metrics.get("recall_at")
    if isinstance(recall_at, int) and recall_at > 0:
        return recall_at
    return default_k


def _find_relevant_chunks(
    conn: sqlite3.Connection,
    expected: dict[str, str],
) -> list[str]:
    """Find chunk IDs matching the expected conditions in the database.

    Args:
        conn: SQLite connection.
        expected: Dict with optional keys: content_contains, file_path_contains,
                  heading_contains.

    Returns:
        List of chunk IDs satisfying all specified conditions.
    """
    conditions: list[str] = []
    params: list[str] = []

    content_contains = expected.get("content_contains")
    if content_contains:
        conditions.append("c.content LIKE ?")
        params.append(f"%{content_contains}%")

    file_contains = expected.get("file_path_contains")
    if file_contains:
        conditions.append("f.relative_path LIKE ?")
        params.append(f"%{file_contains}%")

    heading_contains = expected.get("heading_contains")
    if heading_contains:
        conditions.append("c.heading_path LIKE ?")
        params.append(f"%{heading_contains}%")

    if not conditions:
        return []

    where_clause = " AND ".join(conditions)
    rows = conn.execute(
        f"""
        SELECT c.id
        FROM chunks c
        JOIN files f ON c.file_id = f.id
        WHERE {where_clause}
        """,
        params,
    ).fetchall()

    return [row["id"] for row in rows]


async def _run_single_query(
    conn: sqlite3.Connection,
    query: EvalQuery,
    provider: EmbeddingProvider,
    config: MDRackConfig | None,
    profile: str,
    k: int,
) -> EvalQueryResult:
    """Run a single eval query and compute metrics.

    Args:
        conn: SQLite connection.
        query: The eval query to run.
        provider: Embedding provider for semantic/hybrid modes.
        config: MDRackConfig for hybrid search (can be None for text mode).
        profile: Embedding profile name.
        k: Number of top results to consider.

    Returns:
        EvalQueryResult with metrics.
    """
    query_k = _effective_k(query, k)
    expected_ids: list[str] = []
    conditions_met = True
    error: str | None = None
    try:
        expected_ids = _find_relevant_chunks(conn, query.expected)
    except Exception:
        logger.error("eval.expected_resolution.failed reason=storage_error")
        conditions_met = False
        error = "Failed to resolve expected clauses against the indexed store"

    if not error and not expected_ids:
        conditions_met = False
        error = "Expected clauses matched zero chunks"

    retrieved_ids: list[str] = []
    try:
        if query.mode == "text":
            result = text_search(conn, query.query, limit=query_k)
            retrieved_ids = [r.chunk_id for r in result.results]

        elif query.mode == "semantic":
            result = await semantic_search(
                conn, query.query, provider, profile=profile, limit=query_k,
            )
            retrieved_ids = [r.chunk_id for r in result.results]
            if result.error:
                conditions_met = False
                error = result.error

        elif query.mode == "hybrid":
            if config is None:
                config = MDRackConfig()
            result = await hybrid_search(
                conn, query.query, provider, config, limit=query_k,
            )
            retrieved_ids = [r.chunk_id for r in result.results]
            if result.error:
                conditions_met = False
                error = result.error
    except Exception:
        logger.error("eval.search.failed reason=search_error mode=%s", query.mode)
        conditions_met = False
        error = "Search execution failed"

    expected_set = set(expected_ids)
    rec_k = recall_at_k(expected_set, retrieved_ids, query_k)
    mr = mrr(expected_set, retrieved_ids)
    prec_k = precision_at_k(expected_set, retrieved_ids, query_k)
    ndcg_k = ndcg_at_k(
        {item_id: 1.0 for item_id in expected_set}, retrieved_ids, query_k
    )

    return EvalQueryResult(
        query_id=query.id,
        query=query.query,
        mode=query.mode,
        retrieved_ids=retrieved_ids,
        expected_ids=expected_ids,
        k=query_k,
        recall_at_k=rec_k,
        mrr=mr,
        precision_at_k=prec_k,
        ndcg_at_k=ndcg_k,
        conditions_met=conditions_met,
        error=error,
    )


def run_retrieval_eval(
    conn: sqlite3.Connection,
    queries: EvalQuerySet,
    provider: EmbeddingProvider,
    config: MDRackConfig | None = None,
    profile: str = "default",
    k: int = 5,
) -> EvalReport:
    """Run retrieval evaluation for a set of queries.

    For each query, runs the search in the specified mode, identifies
    the true relevant chunks via the expected conditions, and computes
    Recall@K, MRR, and Precision@K.

    Args:
        conn: SQLite connection.
        queries: Set of eval queries to run.
        provider: Embedding provider for semantic/hybrid modes.
        config: MDRackConfig for hybrid search. Uses defaults if None.
        profile: Embedding profile name.
        k: Number of top results to consider for Recall@K and Precision@K.

    Returns:
        EvalReport with per-query results and aggregate summary.
    """
    async_results: list[EvalQueryResult] = []

    async def _run_all() -> None:
        tasks = []
        for q in queries.queries:
            tasks.append(
                _run_single_query(conn, q, provider, config, profile, k)
            )
        gathered = await asyncio.gather(*tasks)
        async_results.extend(gathered)

    asyncio.run(_run_all())

    report = EvalReport(results=async_results)

    if async_results:
        recall_values = [r.recall_at_k for r in async_results]
        mrr_values = [r.mrr for r in async_results]
        precision_values = [r.precision_at_k for r in async_results]
        ndcg_values = [r.ndcg_at_k for r in async_results]
        n = len(async_results)
        n_success = sum(1 for r in async_results if r.conditions_met)
        n_failed = n - n_success
        n_zero_gold = sum(1 for r in async_results if not r.expected_ids)

        report.summary = {
            "queries_total": n,
            "queries_successful": n_success,
            "queries_failed": n_failed,
            "queries_with_zero_gold": n_zero_gold,
            "avg_recall_at_k": sum(recall_values) / n if n else 0.0,
            "avg_mrr": sum(mrr_values) / n if n else 0.0,
            "avg_precision_at_k": sum(precision_values) / n if n else 0.0,
            "avg_ndcg_at_k": sum(ndcg_values) / n if n else 0.0,
        }
    else:
        report.summary = {
            "queries_total": 0,
            "queries_successful": 0,
            "queries_failed": 0,
            "queries_with_zero_gold": 0,
            "avg_recall_at_k": 0.0,
            "avg_mrr": 0.0,
            "avg_precision_at_k": 0.0,
            "avg_ndcg_at_k": 0.0,
        }

    return report
