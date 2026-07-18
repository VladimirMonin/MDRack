"""Eval retrieval subcommand for MDRack CLI."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import click

from mdrack.embeddings.runtime import close_async_resource, create_embedding_provider
from mdrack.eval.queries import load_queries
from mdrack.eval.reporting import build_safe_eval_results, build_safe_eval_summary
from mdrack.eval.retrieval import run_retrieval_eval
from mdrack.output.envelope import error as envelope_error
from mdrack.output.envelope import success as envelope_success
from mdrack.output.errors import StorageError
from mdrack.output.json_output import emit_json
from mdrack.ports.embeddings import EmbeddingProvider
from mdrack.storage.sqlite.connection import get_connection

logger = logging.getLogger(__name__)


def _output(ctx: click.Context, payload: dict[str, Any]) -> None:
    json_flag: bool = ctx.obj.get("json_output", True) if ctx.obj else True
    emit_json(payload, pretty=not json_flag)


def _open_connection(db_path: Path) -> Any:
    if not db_path.is_file():
        raise StorageError(
            f"Database not found at {db_path}. Run 'mdrack scan' first.",
        )
    return get_connection(db_path)


@click.command()
@click.option(
    "--queries",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    help="Path to YAML file with eval queries.",
)
@click.option(
    "--k",
    type=int,
    default=5,
    help="Number of top results for Recall@K / Precision@K (default: 5).",
)
@click.option(
    "--provider",
    "embedding_provider",
    type=click.Choice(["lmstudio", "fake"]),
    default=None,
    help="Embedding provider for semantic/hybrid eval (default from config).",
)
@click.pass_context
def retrieval(
    ctx: click.Context,
    queries: str,
    k: int,
    embedding_provider: str | None,
) -> None:
    """Run retrieval evaluation against the indexed store."""
    cmd = "eval retrieval"
    config = ctx.obj.get("config") if ctx.obj else None
    db_path = ctx.obj.get("db_path") if ctx.obj else None

    if config is None or db_path is None:
        _output(ctx, envelope_error("Configuration not available", "CONFIG_ERROR", cmd))
        ctx.exit(1)
        return

    queries_path = Path(queries)

    try:
        query_set = load_queries(queries_path)
    except Exception:
        logger.warning("cli.eval.failed code=EVAL_LOAD_ERROR reason=invalid_query_set")
        _output(
            ctx,
            envelope_error(
                "Evaluation query set could not be loaded",
                "EVAL_LOAD_ERROR",
                cmd,
            ),
        )
        ctx.exit(1)
        return

    try:
        conn = _open_connection(db_path)
    except StorageError as exc:
        logger.warning("cli.eval.failed code=%s reason=store_unavailable", exc.code)
        _output(ctx, envelope_error("Evaluation store is unavailable", exc.code, cmd))
        ctx.exit(1)
        return

    try:
        provider: EmbeddingProvider | None = None
        provider_name: str = embedding_provider or config.embedding.provider
        provider = create_embedding_provider(provider_name, config)

        report = run_retrieval_eval(
            conn, query_set, provider, config, profile="default", k=k,
        )

        data: dict[str, Any] = {
            "query_set": {"kind": "file", "query_count": len(query_set.queries)},
            "k": k,
            "results": build_safe_eval_results(report),
            "summary": build_safe_eval_summary(report),
        }
        _output(ctx, envelope_success(data, command=cmd))
    except Exception:
        logger.error("cli.eval.failed code=INTERNAL_ERROR reason=unexpected_failure")
        _output(ctx, envelope_error("Evaluation failed", "INTERNAL_ERROR", cmd))
        ctx.exit(1)
    finally:
        conn.close()
        try:
            import asyncio

            asyncio.run(close_async_resource(provider))
        except Exception:
            logger.warning("cli.eval.cleanup.failed reason=provider_close_failed")
