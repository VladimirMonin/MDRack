"""Complete provider-free or text-embedding video manifest ingestion command."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import click

from mdrack.application.manifest import MAX_MANIFEST_BYTES
from mdrack.application.video_composition import VideoCompositionService
from mdrack.embeddings.runtime import (
    close_async_resource,
    create_embedding_provider,
    embedding_profile_from_config,
)
from mdrack.ingestion.media_manifests import MediaManifestError, read_video_resource_manifest
from mdrack.output.envelope import error as envelope_error
from mdrack.output.envelope import success as envelope_success
from mdrack.output.json_output import emit_json
from mdrack_sqlite import SQLiteCatalog

logger = logging.getLogger(__name__)


def _output(ctx: click.Context, payload: dict[str, Any]) -> None:
    emit_json(payload, pretty=not bool(ctx.obj.get("json_output", True) if ctx.obj else True))


def _fail(ctx: click.Context, *, code: str, message: str, reason: str) -> None:
    logger.error("cli.ingest.video.failed", extra={"status": "failed", "reason": reason})
    _output(ctx, envelope_error(message, code, "ingest video"))
    ctx.exit(1)


def _read_bounded(path: str) -> bytes:
    try:
        with Path(path).open("rb") as stream:
            payload = stream.read(MAX_MANIFEST_BYTES + 1)
    except (OSError, TypeError, ValueError):
        raise ValueError("manifest_unavailable") from None
    if len(payload) > MAX_MANIFEST_BYTES:
        raise ValueError("manifest_too_large")
    return payload


@click.command(name="video")
@click.argument("manifest_path")
@click.option("--embedding-profile", "profile", default="default", show_default=True)
@click.option("--provider", "provider_name", type=click.Choice(["lmstudio", "fake"]), default=None)
@click.option("--no-embeddings", is_flag=True, default=False)
@click.option("--dry-run", is_flag=True, default=False)
@click.option("--catalog", "catalog_path", required=True, metavar="PATH")
@click.pass_context
def ingest_video(
    ctx: click.Context,
    manifest_path: str,
    profile: str,
    provider_name: str | None,
    no_embeddings: bool,
    dry_run: bool,
    catalog_path: str,
) -> None:
    """Atomically replace one complete transcript + frame-caption video graph."""
    config = ctx.obj.get("config") if ctx.obj else None
    provider = None
    catalog = None
    try:
        manifest = read_video_resource_manifest(_read_bounded(manifest_path))
        catalog = SQLiteCatalog.open(catalog_path)
        embedding_fingerprint = None
        if not no_embeddings and not dry_run:
            if config is None:
                raise ValueError("config_unavailable")
            provider = create_embedding_provider(provider_name or config.embedding.provider, config)
            embedding_fingerprint = embedding_profile_from_config(
                config,
                provider,
                profile,
            ).fingerprint
        service = VideoCompositionService(
            catalog,
            embedding_provider=provider,
            embedding_fingerprint=embedding_fingerprint,
            profile=profile,
        )
        if dry_run:
            batch = service.prepare(
                manifest.transcript,
                manifest.frame_captions,
                media_type=manifest.media_type,
                source_namespace=manifest.source_namespace,
                source_locator=manifest.source_locator,
                source_metadata=manifest.source_metadata,
                title=manifest.title,
            )
            transcript_count = sum(unit.unit_kind == "time_segment" for unit in batch.units)
            frame_count = sum(unit.unit_kind == "frame" for unit in batch.units)
            data = {
                "resource_id": batch.resource.resource_id,
                "representation_count": len(batch.representations),
                "transcript_unit_count": transcript_count,
                "frame_unit_count": frame_count,
                "unit_count": transcript_count + frame_count,
                "vector_count": 0,
                "space_id": None,
                "persisted": False,
            }
        else:
            result = asyncio.run(
                service.ingest(
                    manifest.transcript,
                    manifest.frame_captions,
                    media_type=manifest.media_type,
                    source_namespace=manifest.source_namespace,
                    source_locator=manifest.source_locator,
                    source_metadata=manifest.source_metadata,
                    title=manifest.title,
                    embeddings=not no_embeddings,
                )
            )
            data = {**result.to_dict(), "persisted": True}
        logger.info(
            "cli.ingest.video.completed",
            extra={
                "status": "completed",
                "transcript_unit_count": data["transcript_unit_count"],
                "frame_unit_count": data["frame_unit_count"],
                "vector_count": data["vector_count"],
            },
        )
        _output(ctx, envelope_success(data, command="ingest video"))
    except MediaManifestError as error:
        _fail(
            ctx,
            code="VIDEO_MANIFEST_INVALID",
            message="Video manifest could not be read",
            reason=str(error),
        )
    except Exception as error:
        reason = str(error) if str(error) in {
            "manifest_unavailable",
            "manifest_too_large",
            "config_unavailable",
        } else "operation_failed"
        _fail(
            ctx,
            code="VIDEO_INGEST_ERROR",
            message="Video ingestion failed",
            reason=reason,
        )
    finally:
        if catalog is not None:
            catalog.close()
        if provider is not None:
            try:
                asyncio.run(close_async_resource(provider))
            except Exception:
                logger.debug("video.provider.close_failed", extra={"reason": "cleanup_error"})


__all__ = ["ingest_video"]
