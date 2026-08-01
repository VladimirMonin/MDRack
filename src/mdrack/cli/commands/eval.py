"""Canonical retrieval evaluation command without legacy SQLite readers."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import click

from mdrack.embeddings.runtime import close_async_resource, create_embedding_provider
from mdrack.eval.metrics import mrr, ndcg_at_k, precision_at_k, recall_at_k
from mdrack.eval.queries import EvalQuery, EvalQuerySet, load_queries
from mdrack.eval.reporting import build_safe_eval_results, build_safe_eval_summary
from mdrack.eval.retrieval import EvalQueryResult, EvalReport
from mdrack.output.envelope import error as envelope_error
from mdrack.output.envelope import success as envelope_success
from mdrack.output.json_output import emit_json
from mdrack.public_api import MDRackEngine

logger = logging.getLogger(__name__)


def _output(ctx: click.Context, payload: dict[str, Any]) -> None:
    json_flag: bool = ctx.obj.get("json_output", True) if ctx.obj else True
    emit_json(payload, pretty=not json_flag)


def _effective_k(query: EvalQuery, default_k: int) -> int:
    recall_at = query.metrics.get("recall_at")
    return recall_at if isinstance(recall_at, int) and recall_at > 0 else default_k


def _expected_ids(engine: MDRackEngine, expected: Mapping[str, str]) -> list[str]:
    """Resolve query predicates against the fixed catalog's canonical units."""
    storage = engine.storage
    connection = getattr(storage, "connection", None)
    if connection is None:
        raise RuntimeError("catalog_unavailable")
    rows = connection.execute(
        "SELECT u.unit_id,u.text_content,u.evidence_locator_json,r.metadata_json "
        "FROM core_search_units u JOIN core_resources r ON r.resource_id=u.resource_id "
        "WHERE u.unit_kind='text_chunk' ORDER BY u.unit_id"
    ).fetchall()
    matching: list[str] = []
    for row in rows:
        text = str(row["text_content"] or "")
        locator = json.loads(str(row["evidence_locator_json"]))
        resource_metadata = json.loads(str(row["metadata_json"]))
        if not isinstance(locator, Mapping) or not isinstance(resource_metadata, Mapping):
            continue
        relative_path = resource_metadata.get("relative_path")
        heading_path = locator.get("heading_path")
        heading_text = " / ".join(heading_path) if isinstance(heading_path, list) else ""
        if (
            (value := expected.get("content_contains")) is not None and value not in text
        ) or (
            (value := expected.get("file_path_contains")) is not None
            and (not isinstance(relative_path, str) or value not in relative_path)
        ) or ((value := expected.get("heading_contains")) is not None and value not in heading_text):
            continue
        matching.append(str(row["unit_id"]))
    return matching


async def _run_query(engine: MDRackEngine, query: EvalQuery, default_k: int) -> EvalQueryResult:
    k = _effective_k(query, default_k)
    conditions_met = True
    error: str | None = None
    try:
        expected_ids = _expected_ids(engine, query.expected)
    except Exception:
        expected_ids = []
        conditions_met = False
        error = "Expected clauses could not be resolved"
    if not expected_ids and error is None:
        conditions_met = False
        error = "Expected clauses matched zero chunks"

    retrieved_ids: list[str] = []
    try:
        if query.mode == "text":
            result = engine.search_text(query.query, limit=k)
        elif query.mode == "semantic":
            result = await engine.search_semantic(query.query, limit=k)
        else:
            result = await engine.search_hybrid(query.query, limit=k)
        retrieved_ids = [item.logical_id for item in result.results]
        if result.degraded:
            conditions_met = False
            error = "Search execution failed"
    except Exception:
        conditions_met = False
        error = "Search execution failed"

    expected_set = set(expected_ids)
    return EvalQueryResult(
        query_id=query.id,
        query=query.query,
        mode=query.mode,
        retrieved_ids=retrieved_ids,
        expected_ids=expected_ids,
        k=k,
        recall_at_k=recall_at_k(expected_set, retrieved_ids, k),
        mrr=mrr(expected_set, retrieved_ids),
        precision_at_k=precision_at_k(expected_set, retrieved_ids, k),
        ndcg_at_k=ndcg_at_k({item_id: 1.0 for item_id in expected_set}, retrieved_ids, k),
        conditions_met=conditions_met,
        error=error,
    )


async def _evaluate(engine: MDRackEngine, query_set: EvalQuerySet, k: int) -> EvalReport:
    results = [await _run_query(engine, query, k) for query in query_set.queries]
    total = len(results)
    successful = sum(result.conditions_met for result in results)
    return EvalReport(
        results=results,
        summary={
            "queries_total": total,
            "queries_successful": successful,
            "queries_failed": total - successful,
            "queries_with_zero_gold": sum(not result.expected_ids for result in results),
            "avg_recall_at_k": sum(result.recall_at_k for result in results) / total if total else 0.0,
            "avg_mrr": sum(result.mrr for result in results) / total if total else 0.0,
            "avg_precision_at_k": (
                sum(result.precision_at_k for result in results) / total if total else 0.0
            ),
            "avg_ndcg_at_k": sum(result.ndcg_at_k for result in results) / total if total else 0.0,
        },
    )


@click.group(name="eval")
def evaluation() -> None:
    """Evaluate fixed-catalog retrieval without alternate catalog selection."""


@evaluation.command(name="retrieval")
@click.option("--queries", required=True, type=click.Path(dir_okay=False), help="YAML evaluation query set.")
@click.option("--k", type=click.IntRange(min=1), default=5, show_default=True)
@click.option(
    "--provider",
    "embedding_provider",
    type=click.Choice(["lmstudio", "fake"]),
    default=None,
    help="Embedding provider for semantic or hybrid evaluation.",
)
@click.pass_context
def retrieval(
    ctx: click.Context,
    queries: str,
    k: int,
    embedding_provider: str | None,
) -> None:
    """Run validated retrieval cases against the configured catalog."""
    command = "eval retrieval"
    config = ctx.obj.get("config") if ctx.obj else None
    root = ctx.obj.get("root") if ctx.obj else None
    if config is None or not isinstance(root, Path):
        _output(ctx, envelope_error("Configuration not available", "CONFIG_ERROR", command))
        ctx.exit(1)
        return
    try:
        query_set = load_queries(Path(queries))
    except Exception:
        logger.warning("cli.eval.load_failed reason=invalid_query_set")
        _output(ctx, envelope_error("Evaluation query set could not be loaded", "EVAL_LOAD_ERROR", command))
        ctx.exit(1)
        return

    db_path = ctx.obj.get("db_path") if ctx.obj else None
    if not isinstance(db_path, Path) or not db_path.is_file():
        _output(ctx, envelope_error("Evaluation store is unavailable", "STORAGE_ERROR", command))
        ctx.exit(1)
        return

    provider = None
    engine = None
    try:
        requires_embeddings = any(query.mode in {"semantic", "hybrid"} for query in query_set.queries)
        if requires_embeddings:
            provider = create_embedding_provider(embedding_provider or config.embedding.provider, config)
        engine = MDRackEngine(root=root, config=config, embedding_provider=provider)
        report = asyncio.run(_evaluate(engine, query_set, k))
        _output(
            ctx,
            envelope_success(
                {
                    "query_set": {"kind": "file", "query_count": len(query_set.queries)},
                    "k": k,
                    "results": build_safe_eval_results(report),
                    "summary": build_safe_eval_summary(report),
                },
                command=command,
            ),
        )
    except Exception:
        logger.error("cli.eval.failed reason=evaluation_failed")
        _output(ctx, envelope_error("Evaluation failed", "EVAL_ERROR", command))
        ctx.exit(1)
    finally:
        if engine is not None:
            engine.close()
        if provider is not None:
            try:
                asyncio.run(close_async_resource(provider))
            except Exception:
                logger.warning("cli.eval.cleanup_failed reason=provider_close_failed")
