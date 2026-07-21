"""Search command routed through the canonical application retrieval service."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, cast

import click

from mdrack.application.compatibility import (
    StoreGenerationManagerError,
    create_application_storage,
)
from mdrack.application.metadata_filters import MetadataFilters, metadata_filters_from_cli
from mdrack.application.metadata_projection import metadata_projection_policy_from_config
from mdrack.application.resource_catalog import MetadataCatalogService
from mdrack.application.retrieval import InvalidTextSearchError, RetrievalService
from mdrack.application.transcript_ingestion import (
    TimedRetrievalMode,
    TimedRetrievalService,
)
from mdrack.embeddings.runtime import (
    close_async_resource,
    create_embedding_provider,
    embedding_profile_from_config,
)
from mdrack.output.envelope import error as envelope_error
from mdrack.output.envelope import success as envelope_success
from mdrack.output.errors import StorageError
from mdrack.output.json_output import emit_json
from mdrack.ports.embeddings import EmbeddingProvider
from mdrack.storage.sqlite.fts import FTSQueryError
from mdrack_core import SearchScope
from mdrack_sqlite import SQLiteCatalog

logger = logging.getLogger(__name__)


class MetadataSearchInputError(ValueError):
    """One fixed, payload-free metadata search usage failure."""


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
    "--target",
    type=click.Choice(["unit", "resource"]),
    default="unit",
    show_default=True,
)
@click.option("--tag", "tags", multiple=True, help="Exact projected tag value.")
@click.option(
    "--meta",
    "metadata_all",
    multiple=True,
    metavar="PATH=JSON_SCALAR",
    help="Require an exact value from a configured facet projection.",
)
@click.option("--meta-any", "metadata_any", multiple=True, metavar="PATH=JSON_SCALAR")
@click.option("--meta-none", "metadata_none", multiple=True, metavar="PATH=JSON_SCALAR")
@click.option(
    "--metadata-weight",
    type=click.FloatRange(min=0.0),
    default=0.2,
    show_default=True,
    help="Resource-target lexical weight for allowlisted metadata text; no embedding is used.",
)
@click.option(
    "--provider",
    "embedding_provider",
    type=click.Choice(["lmstudio", "fake"]),
    default=None,
    help="Embedding provider for semantic/hybrid search (default from config).",
)
@click.option("--catalog", "catalog_path", default=None, metavar="PATH")
@click.option("--kind", "resource_kinds", multiple=True)
@click.option("--media-type", "media_types", multiple=True)
@click.option("--namespace", "source_namespaces", multiple=True)
@click.option("--representation", "representation_kinds", multiple=True)
@click.option("--unit-kind", "unit_kinds", multiple=True)
@click.pass_context
def cli_search(
    ctx: click.Context,
    query: str,
    search_mode: str | None,
    limit: int | None,
    target: str,
    tags: tuple[str, ...],
    metadata_all: tuple[str, ...],
    metadata_any: tuple[str, ...],
    metadata_none: tuple[str, ...],
    metadata_weight: float,
    embedding_provider: str | None,
    catalog_path: str | None,
    resource_kinds: tuple[str, ...],
    media_types: tuple[str, ...],
    source_namespaces: tuple[str, ...],
    representation_kinds: tuple[str, ...],
    unit_kinds: tuple[str, ...],
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
    storage = None
    timed_catalog = None
    if catalog_path is None:
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
        if catalog_path is not None:
            timed_catalog = SQLiteCatalog.open(catalog_path)
            if mode == "semantic" or (
                mode == "hybrid" and config.search.semantic_weight > 0.0
            ):
                timed_provider_name = embedding_provider or config.embedding.provider
                provider = create_embedding_provider(timed_provider_name, config)
            fingerprint = (
                embedding_profile_from_config(config, provider, "default").fingerprint
                if provider is not None
                else None
            )
            timed_service = TimedRetrievalService(
                timed_catalog,
                embedding_provider=provider,
                embedding_fingerprint=fingerprint,
                profile="default",
                rrf_k=config.search.rrf_k,
                text_weight=config.search.text_weight,
                semantic_weight=config.search.semantic_weight,
            )
            timed_result = asyncio.run(
                timed_service.search(
                    query,
                    mode=cast(TimedRetrievalMode, mode),
                    target=target,
                    scope=SearchScope(
                        resource_kinds=resource_kinds,
                        media_types=media_types,
                        source_namespaces=source_namespaces,
                        representation_kinds=representation_kinds,
                        unit_kinds=unit_kinds,
                    ),
                    limit=limit_value,
                )
            )
            _emit_success(ctx, timed_result.to_dict(), command)
            return
        try:
            metadata_filters = metadata_filters_from_cli(
                metadata_projection_policy_from_config(config.metadata),
                tags=tags,
                all_values=metadata_all,
                any_values=metadata_any,
                none_values=metadata_none,
            )
        except ValueError:
            raise MetadataSearchInputError from None
        application_filters = (
            metadata_filters
            if metadata_filters.any or metadata_filters.all or metadata_filters.none
            else None
        )
        if target == "resource":
            if mode != "text":
                raise MetadataSearchInputError
            catalog = getattr(storage, "resource_store", None)
            if catalog is None:
                raise ValueError("resource target requires an active resource-core generation")
            result = MetadataCatalogService(catalog).search(
                query,
                metadata_filters=metadata_filters,
                metadata_weight=metadata_weight,
                limit=limit_value,
            )
            _emit_success(ctx, result.to_dict(), command)
            return
        if mode == "semantic" or (mode == "hybrid" and config.search.semantic_weight > 0.0):
            provider_name: str = embedding_provider or config.embedding.provider
            provider = create_embedding_provider(provider_name, config)
        assert storage is not None
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
            text_weight=config.search.text_weight,
            semantic_weight=config.search.semantic_weight,
        )
        if mode == "text":
            _run_text_search(service, query, limit_value, application_filters, ctx, command)
        elif mode == "semantic":
            asyncio.run(
                _run_semantic_search(
                    service,
                    query,
                    limit_value,
                    application_filters,
                    ctx,
                    command,
                )
            )
        else:
            asyncio.run(
                _run_hybrid_search(
                    service,
                    query,
                    limit_value,
                    application_filters,
                    ctx,
                    command,
                )
            )
    except MetadataSearchInputError:
        logger.error("cli.search.failed status=failed reason=metadata_search_options_invalid")
        _output(
            ctx,
            envelope_error(
                "Metadata search options are invalid",
                "VALIDATION_ERROR",
                command,
            ),
        )
        ctx.exit(1)
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
        if timed_catalog is not None:
            timed_catalog.close()
        if storage is not None:
            storage.close()


def _run_text_search(
    service: RetrievalService,
    query: str,
    limit_value: int,
    metadata_filters: MetadataFilters | None,
    ctx: click.Context,
    command: str,
) -> None:
    result = service.search_text(
        query,
        limit=limit_value,
        metadata_filters=metadata_filters,
    )
    _emit_success(ctx, result.to_dict(), command)


async def _run_semantic_search(
    service: RetrievalService,
    query: str,
    limit_value: int,
    metadata_filters: MetadataFilters | None,
    ctx: click.Context,
    command: str,
) -> None:
    result = await service.search_semantic(
        query,
        limit=limit_value,
        metadata_filters=metadata_filters,
    )
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
    metadata_filters: MetadataFilters | None,
    ctx: click.Context,
    command: str,
) -> None:
    result = await service.search_hybrid(
        query,
        limit=limit_value,
        reranker=None,
        metadata_filters=metadata_filters,
    )
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
