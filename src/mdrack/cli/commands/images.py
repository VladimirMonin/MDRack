"""Explicit direct local-image lifecycle commands."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import click

from mdrack.application.compatibility import (
    StoreGenerationManagerError,
    create_application_storage,
    embedding_space_id,
)
from mdrack.embeddings.runtime import (
    close_async_resource,
    create_embedding_provider,
    embedding_profile_from_config,
)
from mdrack.ingestion.images import (
    ExtractedImageText,
    ImageEmbeddingSpace,
    ImageIngestionService,
    StaticImageExtractor,
)
from mdrack.output.envelope import error as envelope_error
from mdrack.output.envelope import success as envelope_success
from mdrack.output.json_output import emit_json
from mdrack.ports.embeddings import EmbeddingProvider

logger = logging.getLogger(__name__)


def _output(ctx: click.Context, payload: dict[str, Any]) -> None:
    json_flag: bool = ctx.obj.get("json_output", True) if ctx.obj else True
    emit_json(payload, pretty=not json_flag)


def _open_catalog(ctx: click.Context) -> tuple[Any, Any]:
    config = ctx.obj.get("config") if ctx.obj else None
    root = ctx.obj.get("root") if ctx.obj else None
    if config is None or not isinstance(root, Path):
        raise RuntimeError("config_unavailable")
    try:
        storage = create_application_storage(root, config)
    except StoreGenerationManagerError:
        raise RuntimeError("resource_generation_unavailable") from None
    catalog = getattr(storage, "resource_store", None)
    if catalog is None:
        storage.close()
        raise RuntimeError("resource_generation_unavailable")
    return storage, catalog


def _text_space(config: Any, provider: EmbeddingProvider, profile_name: str) -> ImageEmbeddingSpace:
    profile = embedding_profile_from_config(config, provider, profile_name)
    return ImageEmbeddingSpace(
        embedding_space_id(
            profile.name,
            profile.fingerprint,
            profile.vector_value_policy,
        ),
        profile.output_dimensions,
        profile.fingerprint,
        profile_name=profile.name,
        vector_value_policy=profile.vector_value_policy,
    )


@click.group(name="image")
def image() -> None:
    """Explicitly ingest, search, or delete local image resources."""


@image.command(name="ingest")
@click.argument("path", type=click.Path(path_type=Path))
@click.option("--resource-id", required=True, help="Caller-owned logical image resource ID.")
@click.option("--source-namespace", required=True, help="Caller-owned source namespace.")
@click.option("--source-ref", required=True, help="Portable caller-owned source reference.")
@click.option("--caption", default=None, help="Complete caller-provided caption text.")
@click.option("--ocr", default=None, help="Complete caller-provided OCR text.")
@click.option("--title", default=None, help="Optional public resource title.")
@click.option(
    "--provider",
    "provider_name",
    type=click.Choice(["fake", "lmstudio"]),
    default="fake",
    show_default=True,
    help="Text embedding provider. Live LM Studio use is opt-in.",
)
@click.pass_context
def ingest_image(
    ctx: click.Context,
    path: Path,
    resource_id: str,
    source_namespace: str,
    source_ref: str,
    caption: str | None,
    ocr: str | None,
    title: str | None,
    provider_name: str,
) -> None:
    """Create or atomically replace one explicitly selected local image."""
    command = "image ingest"
    outputs = []
    if caption:
        outputs.append(ExtractedImageText("caption_text", caption, "caller-caption-v1"))
    if ocr:
        outputs.append(ExtractedImageText("ocr_text", ocr, "caller-ocr-v1"))
    if not outputs:
        _output(ctx, envelope_error("Caption or OCR text is required", "IMAGE_INPUT_ERROR", command))
        ctx.exit(1)
        return

    storage = None
    provider: EmbeddingProvider | None = None
    try:
        if not path.is_file():
            raise ValueError("image_source_unavailable")
        storage, catalog = _open_catalog(ctx)
        config = ctx.obj["config"]
        provider = create_embedding_provider(provider_name, config)
        service = ImageIngestionService(
            catalog,
            extractor=StaticImageExtractor(outputs),
            text_embedding_provider=provider,
            text_space=_text_space(config, provider, "default"),
        )
        logger.info(
            "cli.image.ingest.started",
            extra={"representation_count": len(outputs)},
        )
        result = asyncio.run(
            service.ingest(
                path,
                resource_id=resource_id,
                source_namespace=source_namespace,
                source_ref=source_ref,
                title=title,
            )
        )
        logger.info(
            "cli.image.ingest.completed",
            extra={"representation_count": len(result.representation_ids)},
        )
        _output(ctx, envelope_success(result.to_dict(), command=command))
    except Exception:
        logger.error("cli.image.ingest.failed", extra={"reason": "image_ingest_error"})
        _output(ctx, envelope_error("Image ingestion failed", "IMAGE_INGEST_ERROR", command))
        ctx.exit(1)
    finally:
        if provider is not None:
            try:
                asyncio.run(close_async_resource(provider))
            except Exception:
                logger.debug("image.embedding.cleanup_failed", extra={"reason": "cleanup_error"})
        if storage is not None:
            storage.close()


@image.command(name="search")
@click.argument("query")
@click.option(
    "--mode",
    type=click.Choice(["text", "semantic", "hybrid"]),
    default="hybrid",
    show_default=True,
)
@click.option("--limit", type=click.IntRange(min=1), default=20, show_default=True)
@click.option(
    "--provider",
    "provider_name",
    type=click.Choice(["fake", "lmstudio"]),
    default="fake",
    show_default=True,
)
@click.pass_context
def search_images(
    ctx: click.Context,
    query: str,
    mode: str,
    limit: int,
    provider_name: str,
) -> None:
    """Search only explicitly ingested image resources."""
    command = "image search"
    storage = None
    provider: EmbeddingProvider | None = None
    try:
        storage, catalog = _open_catalog(ctx)
        config = ctx.obj["config"]
        if mode != "text":
            provider = create_embedding_provider(provider_name, config)
        service = ImageIngestionService(
            catalog,
            text_embedding_provider=provider,
            text_space=_text_space(config, provider, "default") if provider is not None else None,
        )
        logger.info("cli.image.search.started", extra={"mode": mode, "limit": limit})
        if mode == "text":
            result = service.search_text(query, limit=limit)
        elif mode == "semantic":
            result = asyncio.run(service.search_semantic(query, limit=limit))
        else:
            result = asyncio.run(service.search_hybrid(query, limit=limit))
        logger.info(
            "cli.image.search.completed",
            extra={"mode": mode, "result_count": len(result.results)},
        )
        _output(ctx, envelope_success(result.to_dict(), command=command))
    except Exception:
        logger.error("cli.image.search.failed", extra={"reason": "image_search_error"})
        _output(ctx, envelope_error("Image search failed", "IMAGE_SEARCH_ERROR", command))
        ctx.exit(1)
    finally:
        if provider is not None:
            try:
                asyncio.run(close_async_resource(provider))
            except Exception:
                logger.debug("image.embedding.cleanup_failed", extra={"reason": "cleanup_error"})
        if storage is not None:
            storage.close()


@image.command(name="delete")
@click.argument("resource_id")
@click.pass_context
def delete_image(ctx: click.Context, resource_id: str) -> None:
    """Idempotently delete one explicitly ingested image resource graph."""
    command = "image delete"
    storage = None
    try:
        storage, catalog = _open_catalog(ctx)
        ImageIngestionService(catalog).delete(resource_id)
        logger.info("cli.image.delete.completed", extra={"status": "success"})
        _output(ctx, envelope_success({"resource_id": resource_id, "status": "deleted"}, command=command))
    except Exception:
        logger.error("cli.image.delete.failed", extra={"reason": "image_delete_error"})
        _output(ctx, envelope_error("Image deletion failed", "IMAGE_DELETE_ERROR", command))
        ctx.exit(1)
    finally:
        if storage is not None:
            storage.close()


__all__ = ["image"]
