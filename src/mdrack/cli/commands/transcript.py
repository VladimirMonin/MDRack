"""Timed transcript ingestion command over the application orchestration service."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Callable

import click

from mdrack.application.compatibility import StoreGenerationManagerError, create_application_storage
from mdrack.application.manifest import MAX_MANIFEST_BYTES
from mdrack.application.transcript_ingestion import TranscriptIngestionService
from mdrack.embeddings.runtime import (
    close_async_resource,
    create_embedding_provider,
    embedding_profile_from_config,
)
from mdrack.ingestion.transcripts import (
    TranscriptReadError,
    read_srt,
    read_timed_json,
    read_transcript,
    read_vtt,
    read_whisper_json,
)
from mdrack.output.envelope import error as envelope_error
from mdrack.output.envelope import success as envelope_success
from mdrack.output.json_output import emit_json
from mdrack_core import Locator
from mdrack_media import ProducerFingerprint
from mdrack_sqlite import SQLiteCatalog

logger = logging.getLogger(__name__)

_FORMAT_READERS: dict[str, Callable[..., object]] = {
    "auto": read_transcript,
    "whisper-json": read_whisper_json,
    "vtt": read_vtt,
    "srt": read_srt,
    "timed-json-v1": read_timed_json,
}


def _output(ctx: click.Context, payload: dict[str, Any]) -> None:
    json_flag: bool = ctx.obj.get("json_output", True) if ctx.obj else True
    emit_json(payload, pretty=not json_flag)


def _fail(ctx: click.Context, *, code: str, message: str, reason: str) -> None:
    logger.error(
        "cli.ingest.transcript.failed",
        extra={"status": "failed", "reason": reason},
    )
    _output(ctx, envelope_error(message, code, "ingest transcript"))
    ctx.exit(1)


def _read_bounded(path: str) -> bytes:
    try:
        with Path(path).open("rb") as stream:
            payload = stream.read(MAX_MANIFEST_BYTES + 1)
    except (OSError, TypeError, ValueError):
        raise ValueError("transcript_unavailable") from None
    if len(payload) > MAX_MANIFEST_BYTES:
        raise ValueError("transcript_too_large")
    return payload


def _open_catalog(ctx: click.Context, catalog_path: str | None) -> tuple[Any | None, Any]:
    if catalog_path is not None:
        return None, SQLiteCatalog.open(catalog_path)
    config = ctx.obj.get("config") if ctx.obj else None
    root = ctx.obj.get("root") if ctx.obj else None
    if config is None or not isinstance(root, Path):
        raise ValueError("config_unavailable")
    try:
        storage = create_application_storage(root, config)
    except StoreGenerationManagerError:
        raise ValueError("resource_generation_unavailable") from None
    catalog = getattr(storage, "resource_store", None)
    if catalog is None:
        storage.close()
        raise ValueError("resource_generation_unavailable")
    return storage, catalog


@click.group(name="ingest")
def ingest() -> None:
    """Ingest prepared source content into a resource catalog."""


@ingest.command(name="transcript")
@click.argument("transcript_path")
@click.option(
    "--format",
    "transcript_format",
    type=click.Choice(tuple(_FORMAT_READERS)),
    default="auto",
    show_default=True,
)
@click.option("--resource-id", required=True)
@click.option("--kind", "resource_kind", type=click.Choice(["audio", "video"]), required=True)
@click.option("--media-type", required=True)
@click.option("--namespace", "source_namespace", required=True)
@click.option("--source-ref", default=None, help="Portable source reference; defaults to resource ID.")
@click.option("--language", default=None)
@click.option("--producer", default="caller-supplied", show_default=True)
@click.option("--chunking-profile", type=click.Choice(["balanced"]), default="balanced")
@click.option("--embedding-profile", "profile", default="default", show_default=True)
@click.option("--provider", "provider_name", type=click.Choice(["lmstudio", "fake"]), default=None)
@click.option("--no-embeddings", is_flag=True, default=False)
@click.option("--dry-run", is_flag=True, default=False)
@click.option("--catalog", "catalog_path", default=None, metavar="PATH")
@click.pass_context
def ingest_transcript(
    ctx: click.Context,
    transcript_path: str,
    transcript_format: str,
    resource_id: str,
    resource_kind: str,
    media_type: str,
    source_namespace: str,
    source_ref: str | None,
    language: str | None,
    producer: str,
    chunking_profile: str,
    profile: str,
    provider_name: str | None,
    no_embeddings: bool,
    dry_run: bool,
    catalog_path: str | None,
) -> None:
    """Read, group, and atomically replace one audio/video transcript graph."""
    del chunking_profile
    config = ctx.obj.get("config") if ctx.obj else None
    embedding_provider = None
    storage = None
    catalog = None
    try:
        source = _read_bounded(transcript_path)
        producer_fingerprint = ProducerFingerprint.from_payload(
            {
                "adapter": "mdrack-transcript-import",
                "format": transcript_format,
                "producer": producer,
                "version": 1,
            }
        )
        reader = _FORMAT_READERS[transcript_format]
        read_result = reader(
            source,
            resource_id=resource_id,
            producer_fingerprint=producer_fingerprint,
            language=language,
            strict=True,
        )
        artifact = read_result.artifact  # type: ignore[attr-defined]
        storage, catalog = _open_catalog(ctx, catalog_path)
        embedding_fingerprint = None
        if not no_embeddings and not dry_run:
            if config is None:
                raise ValueError("config_unavailable")
            embedding_provider = create_embedding_provider(
                provider_name or config.embedding.provider,
                config,
            )
            embedding_fingerprint = embedding_profile_from_config(
                config,
                embedding_provider,
                profile,
            ).fingerprint
        service = TranscriptIngestionService(
            catalog,
            embedding_provider=embedding_provider,
            embedding_fingerprint=embedding_fingerprint,
            profile=profile,
        )
        locator = Locator(
            "external_record",
            {"source_ref": source_ref or resource_id},
        )
        if dry_run:
            batch = service.prepare(
                artifact,
                resource_kind=resource_kind,
                media_type=media_type,
                source_namespace=source_namespace,
                source_locator=locator,
            )
            data = {
                "resource_id": batch.resource.resource_id,
                "resource_kind": batch.resource.resource_kind,
                "representation_count": len(batch.representations),
                "unit_count": len(batch.units),
                "vector_count": 0,
                "space_id": None,
                "persisted": False,
            }
        else:
            result = asyncio.run(
                service.ingest(
                    artifact,
                    resource_kind=resource_kind,
                    media_type=media_type,
                    source_namespace=source_namespace,
                    source_locator=locator,
                    embeddings=not no_embeddings,
                )
            )
            data = {**result.to_dict(), "persisted": True}
        logger.info(
            "cli.ingest.transcript.completed",
            extra={
                "status": "completed",
                "unit_count": data["unit_count"],
                "vector_count": data["vector_count"],
            },
        )
        _output(ctx, envelope_success(data, command="ingest transcript"))
    except TranscriptReadError as error:
        _fail(
            ctx,
            code="TRANSCRIPT_INVALID",
            message="Transcript could not be read",
            reason=error.code,
        )
    except Exception as error:
        reason = str(error) if str(error) in {
            "transcript_unavailable",
            "transcript_too_large",
            "config_unavailable",
            "resource_generation_unavailable",
        } else "operation_failed"
        _fail(
            ctx,
            code="TRANSCRIPT_INGEST_ERROR",
            message="Transcript ingestion failed",
            reason=reason,
        )
    finally:
        if storage is not None:
            storage.close()
        elif catalog is not None:
            catalog.close()
        if embedding_provider is not None:
            try:
                asyncio.run(close_async_resource(embedding_provider))
            except Exception:
                logger.debug("transcript.provider.close_failed", extra={"reason": "cleanup_error"})


__all__ = ["ingest"]
