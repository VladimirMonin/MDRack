"""Search command routed through the canonical application retrieval service."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import click

from mdrack.application.compatibility import (
    StoreGenerationManagerError,
    create_application_storage,
)
from mdrack.application.retrieval import InvalidTextSearchError, RetrievalService
from mdrack.embeddings.protocol import EmbeddingProvider
from mdrack.embeddings.runtime import (
    close_async_resource,
    create_embedding_provider,
    embedding_profile_from_config,
)
from mdrack.output.envelope import error as envelope_error
from mdrack.output.envelope import success as envelope_success
from mdrack.output.errors import StorageError
from mdrack.output.json_output import emit_json
from mdrack.storage.sqlite.fts import FTSQueryError

logger = logging.getLogger(__name__)


def _open_storage(root: Path, config: Any, db_path: Path) -> Any:
    configured = Path(config.paths.store)
    store_dir = configured if configured.is_absolute() else root.resolve() / configured
    if not db_path.is_file() and not (store_dir / "active-generation.json").is_file():
        raise StorageError("Database not found. Run 'mdrack scan' first.")
    try:
        return create_application_storage(root, config)
    except StoreGenerationManagerError:
        raise StorageError("Active index generation is not ready.") from None


def _output(ctx: click.Context, payload: dict[str, Any]) -> None:
    json_flag: bool = ctx.obj.get("json_output", True) if ctx.obj else True
    emit_json(payload, pretty=not json_flag)


@click.command()
@click.argument("query")
@click.option(
    "--mode",
    "search_mode",
    type=click.Choice(["text", "semantic", "hybrid"]),
    default=None,
    help="Search mode: text, semantic, or hybrid (default from config).",
)
@click.option("--limit", type=int, default=None, help="Maximum number of results (default from config).")
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
    command = "search"
    config = ctx.obj.get("config") if ctx.obj else None
    db_path = ctx.obj.get("db_path") if ctx.obj else None
    if config is None or db_path is None:
        _output(ctx, envelope_error("Configuration not available", "CONFIG_ERROR", command))
        ctx.exit(1)
        return

    mode: str = search_mode or config.search.default_mode
    limit_value: int = limit or config.search.top_k
    provider: EmbeddingProvider | None = None
    try:
        storage = _open_storage(ctx.obj.get("root", Path(".")), config, db_path)
    except StorageError as exc:
        _output(ctx, envelope_error(str(exc), exc.code, command))
        ctx.exit(1)
        return

    logger.info(
        "cli.search.started mode=%s query_length=%d limit=%d",
        mode,
        len(query),
        limit_value,
    )
    try:
        if mode != "text":
            provider_name: str = embedding_provider or config.embedding.provider
            provider = create_embedding_provider(provider_name, config)
        service = RetrievalService(
            storage,
            embedding_provider=provider,
            profile="default",
            profile_fingerprint=(
                embedding_profile_from_config(config, provider, "default").fingerprint
                if provider is not None
                else None
            ),
            rrf_k=config.search.rrf_k,
        )
        if mode == "text":
            _run_text_search(service, query, limit_value, ctx, command)
        elif mode == "semantic":
            asyncio.run(_run_semantic_search(service, query, limit_value, ctx, command))
        else:
            asyncio.run(_run_hybrid_search(service, query, limit_value, ctx, command))
    except (FTSQueryError, InvalidTextSearchError):
        _output(ctx, envelope_error("Invalid text search query", "FTS_ERROR", command))
    except Exception:
        logger.error("cli.search.failed status=failed reason=internal_error")
        _output(ctx, envelope_error("Search failed", "INTERNAL_ERROR", command))
    finally:
        if provider is not None:
            try:
                asyncio.run(close_async_resource(provider))
            except Exception:
                logger.debug("embedding.provider.close_failed reason=cleanup_error")
        storage.close()


def _run_text_search(
    service: RetrievalService,
    query: str,
    limit_value: int,
    ctx: click.Context,
    command: str,
) -> None:
    result = service.search_text(query, limit=limit_value)
    _emit_success(ctx, result.to_dict(), command)


async def _run_semantic_search(
    service: RetrievalService,
    query: str,
    limit_value: int,
    ctx: click.Context,
    command: str,
) -> None:
    result = await service.search_semantic(query, limit=limit_value)
    if result.degraded:
        details = {"reason": result.degraded_reason} if result.degraded_reason else None
        _output(
            ctx,
            envelope_error("Semantic search failed", "EMBEDDING_ERROR", command, details),
        )
        return
    _emit_success(ctx, result.to_dict(), command)


async def _run_hybrid_search(
    service: RetrievalService,
    query: str,
    limit_value: int,
    ctx: click.Context,
    command: str,
) -> None:
    result = await service.search_hybrid(query, limit=limit_value, reranker=None)
    if result.degraded and not result.results:
        details = {"reason": result.degraded_reason} if result.degraded_reason else None
        _output(
            ctx,
            envelope_error("Hybrid search failed", "EMBEDDING_ERROR", command, details),
        )
        return
    _emit_success(ctx, result.to_dict(), command)


def _emit_success(ctx: click.Context, data: dict[str, object], command: str) -> None:
    results = data["results"]
    result_count = len(results) if isinstance(results, list) else 0
    logger.info("cli.search.finished status=success result_count=%d", result_count)
    _output(ctx, envelope_success(data, command=command))
