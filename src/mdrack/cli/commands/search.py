"""Search command for MDRack CLI.

Provides mdrack search <query> with --mode text|semantic|hybrid,
--limit, and --provider options.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from inspect import isawaitable
from pathlib import Path
from typing import Any

import click

from mdrack.embeddings.fake import FakeEmbeddingProvider
from mdrack.embeddings.lmstudio import LMStudioProvider
from mdrack.embeddings.protocol import EmbeddingProvider
from mdrack.output.envelope import error as envelope_error
from mdrack.output.envelope import success as envelope_success
from mdrack.output.errors import StorageError
from mdrack.search.hybrid import hybrid_search
from mdrack.search.semantic import semantic_search
from mdrack.search.text import text_search
from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.fts import FTSQueryError

logger = logging.getLogger(__name__)


def _open_connection(db_path: Path) -> sqlite3.Connection:
    if not db_path.is_file():
        raise StorageError(
            f"Database not found at {db_path}. Run 'mdrack scan' first.",
        )
    return get_connection(db_path)


def _output(ctx: click.Context, payload: dict[str, Any]) -> None:
    json_flag: bool = ctx.obj.get("json_output", True) if ctx.obj else True
    if json_flag:
        click.echo(json.dumps(payload, ensure_ascii=False))
    else:
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))


def _create_provider(provider_name: str, config: Any) -> EmbeddingProvider:
    if provider_name == "fake":
        return FakeEmbeddingProvider(
            dimensions=config.embedding.dimensions,
            provider_name="fake",
        )
    return LMStudioProvider(
        endpoint=config.embedding.endpoint,
        model=config.embedding.model,
        dimensions=config.embedding.dimensions,
        timeout=config.embedding.timeout_secs,
    )


async def _close_provider(provider: EmbeddingProvider | None) -> None:
    if provider is None:
        return
    close = getattr(provider, "close", None)
    if close is None:
        return
    result = close()
    if isawaitable(result):
        await result


@click.command()
@click.argument("query")
@click.option(
    "--mode",
    "search_mode",
    type=click.Choice(["text", "semantic", "hybrid"]),
    default=None,
    help="Search mode: text, semantic, or hybrid (default from config).",
)
@click.option(
    "--limit",
    type=int,
    default=None,
    help="Maximum number of results (default from config).",
)
@click.option(
    "--provider",
    "embedding_provider",
    type=click.Choice(["lmstudio", "fake"]),
    default=None,
    help="Embedding provider for semantic/hybrid search (default from config).",
)
@click.pass_context
def cli_search(
    ctx: click.Context,
    query: str,
    search_mode: str | None,
    limit: int | None,
    embedding_provider: str | None,
) -> None:
    """Search indexed chunks by text, semantic meaning, or hybrid."""
    cmd = "search"
    config = ctx.obj.get("config") if ctx.obj else None
    db_path = ctx.obj.get("db_path") if ctx.obj else None

    if config is None or db_path is None:
        _output(ctx, envelope_error("Configuration not available", "CONFIG_ERROR", cmd))
        ctx.exit(1)
        return

    mode: str = search_mode or config.search.default_mode
    limit_val: int = limit or config.search.top_k
    provider: EmbeddingProvider | None = None

    try:
        conn = _open_connection(db_path)
    except StorageError as exc:
        _output(ctx, envelope_error(str(exc), exc.code, cmd))
        ctx.exit(1)
        return

    try:
        if mode == "text":
            _run_text_search(conn, query, limit_val, ctx, cmd)
        elif mode == "semantic":
            provider_name: str = embedding_provider or config.embedding.provider
            provider = _create_provider(provider_name, config)
            asyncio.run(_run_semantic_search(conn, query, provider, limit_val, ctx, cmd))
        else:
            provider_name = embedding_provider or config.embedding.provider
            provider = _create_provider(provider_name, config)
            asyncio.run(_run_hybrid_search(conn, query, provider, config, limit_val, ctx, cmd))
    except FTSQueryError as exc:
        _output(ctx, envelope_error(str(exc), "FTS_ERROR", cmd))
    except Exception as exc:
        logger.exception("Search command failed")
        _output(ctx, envelope_error(str(exc), "INTERNAL_ERROR", cmd))
    finally:
        if provider is not None:
            try:
                asyncio.run(_close_provider(provider))
            except Exception:
                logger.debug("Failed to close embedding provider", exc_info=True)
        conn.close()


def _run_text_search(
    conn: sqlite3.Connection,
    query: str,
    limit_val: int,
    ctx: click.Context,
    cmd: str,
) -> None:
    result = text_search(conn, query, limit=limit_val)
    items: list[dict[str, Any]] = [
        {
            "chunk_id": r.chunk_id,
            "score": r.score,
            "snippet": r.snippet,
            "file": r.file_relative_path,
            "section_title": r.section_title,
            "heading_path": r.heading_path,
        }
        for r in result.results
    ]
    data: dict[str, Any] = {
        "query": query,
        "mode": "text",
        "results": items,
        "total_count": result.total_count,
    }
    _output(ctx, envelope_success(data, command=cmd))


async def _run_semantic_search(
    conn: sqlite3.Connection,
    query: str,
    provider: EmbeddingProvider,
    limit_val: int,
    ctx: click.Context,
    cmd: str,
) -> None:
    result = await semantic_search(conn, query, provider, limit=limit_val)
    if result.error:
        _output(ctx, envelope_error(result.error, "EMBEDDING_ERROR", cmd))
        return
    items: list[dict[str, Any]] = [
        {
            "chunk_id": r.chunk_id,
            "score": r.score,
            "content_preview": r.content_preview,
            "file": r.file_relative_path,
            "section_title": r.section_title,
            "heading_path": r.heading_path,
        }
        for r in result.results
    ]
    data: dict[str, Any] = {
        "query": query,
        "mode": "semantic",
        "results": items,
        "total_count": result.total_count,
    }
    _output(ctx, envelope_success(data, command=cmd))


async def _run_hybrid_search(
    conn: sqlite3.Connection,
    query: str,
    provider: EmbeddingProvider,
    config: Any,
    limit_val: int,
    ctx: click.Context,
    cmd: str,
) -> None:
    result = await hybrid_search(conn, query, provider, config, limit=limit_val)
    if result.error and not result.results:
        _output(ctx, envelope_error(result.error, "EMBEDDING_ERROR", cmd))
        return
    items: list[dict[str, Any]] = [
        {
            "chunk_id": r.chunk_id,
            "combined_score": r.combined_score,
            "text_score": r.text_score,
            "semantic_score": r.semantic_score,
            "text_rank": r.text_rank,
            "semantic_rank": r.semantic_rank,
            "content_preview": r.content_preview,
            "file": r.file_relative_path,
            "section_title": r.section_title,
            "heading_path": r.heading_path,
        }
        for r in result.results
    ]
    data: dict[str, Any] = {
        "query": query,
        "mode": "hybrid",
        "results": items,
        "total_count": result.total_count,
    }
    if result.degraded:
        data["degraded"] = True
        data["degraded_reason"] = result.error
    _output(ctx, envelope_success(data, command=cmd))
