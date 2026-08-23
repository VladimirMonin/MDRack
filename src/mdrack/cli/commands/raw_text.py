"""CLI composition for explicit provider-free raw text ingestion."""

from __future__ import annotations

import logging
from pathlib import Path

import click

from mdrack.application.compatibility import ApplicationStoreError, create_application_storage
from mdrack.ingestion.raw_source_provenance import RawSourceError
from mdrack.ingestion.raw_text import RawTextIngestionService
from mdrack.output.envelope import error as envelope_error
from mdrack.output.envelope import success as envelope_success
from mdrack.output.json_output import emit_json

logger = logging.getLogger(__name__)


@click.command(name="text")
@click.argument("source_path", type=click.Path(dir_okay=False, path_type=Path))
@click.option("--source-ref", required=True)
@click.option("--media-type", type=click.Choice(["text/plain", "text/markdown"]), required=True)
@click.pass_context
def ingest_text(ctx: click.Context, source_path: Path, source_ref: str, media_type: str) -> None:
    """Ingest one explicitly selected local plain-text or Markdown source."""
    root = ctx.obj.get("root") if ctx.obj else None
    config = ctx.obj.get("config") if ctx.obj else None
    if not isinstance(root, Path) or config is None:
        emit_json(envelope_error("Configuration could not be loaded", "CONFIG_ERROR", "ingest text"))
        ctx.exit(1)
    storage = None
    try:
        storage = create_application_storage(root, config, create=True)
        result = RawTextIngestionService(storage.resource_store).ingest(
            source_path,
            source_ref=source_ref,
            media_type=media_type,
            root=root,
        )
        emit_json(envelope_success({**result.to_dict(), "persisted": True}, command="ingest text"))
    except RawSourceError as exc:
        logger.error("cli.ingest.raw_text.failed", extra={"status": "failed", "reason": exc.code.value})
        emit_json(envelope_error("Text source could not be ingested", "RAW_TEXT_INGEST_ERROR", "ingest text"))
        ctx.exit(1)
    except (ApplicationStoreError, ValueError, OSError):
        logger.error("cli.ingest.raw_text.failed", extra={"status": "failed", "reason": "operation_failed"})
        emit_json(envelope_error("Text ingestion failed", "RAW_TEXT_INGEST_ERROR", "ingest text"))
        ctx.exit(1)
    finally:
        if storage is not None:
            storage.close()


__all__ = ["ingest_text"]
