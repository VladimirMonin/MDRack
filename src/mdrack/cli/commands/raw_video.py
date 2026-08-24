"""CLI composition for direct local ISO-BMFF video ingestion."""

from __future__ import annotations

import logging
from pathlib import Path

import click

from mdrack.application.compatibility import ApplicationStoreError, create_application_storage
from mdrack.ingestion.raw_source_provenance import RawSourceError
from mdrack.ingestion.raw_video import RawVideoIngestionService
from mdrack.output.envelope import error as envelope_error
from mdrack.output.envelope import success as envelope_success
from mdrack.output.json_output import emit_json

logger = logging.getLogger(__name__)


@click.command(name="raw-video")
@click.argument("source_path", type=click.Path(dir_okay=False, path_type=Path))
@click.option("--source-ref", required=True)
@click.option("--allow-external-video-extractor", is_flag=True, default=False)
@click.option("--video-extractor-command", default=None)
@click.option("--producer", default="caller-supplied", show_default=True)
@click.pass_context
def ingest_raw_video(ctx: click.Context, source_path: Path, source_ref: str,
                     allow_external_video_extractor: bool, video_extractor_command: str | None,
                     producer: str) -> None:
    """Ingest one local ISO-BMFF video through an authorized stdin extractor."""
    root = ctx.obj.get("root") if ctx.obj else None
    config = ctx.obj.get("config") if ctx.obj else None
    storage = None
    try:
        if not isinstance(root, Path) or config is None:
            raise ValueError("config")
        storage = create_application_storage(root, config, create=True)
        result = RawVideoIngestionService(storage.resource_store).ingest(
            source_path, source_ref=source_ref, root=root,
            video_extractor_command=video_extractor_command or "",
            allow_external_video_extractor=allow_external_video_extractor,
            producer=producer,
        )
        emit_json(envelope_success({**result.to_dict(), "persisted": True}, command="ingest raw-video"))
    except (RawSourceError, ApplicationStoreError, ValueError, OSError):
        logger.error("cli.ingest.raw_video.failed", extra={"status": "failed", "reason": "operation_failed"})
        emit_json(envelope_error("Video ingestion failed", "RAW_VIDEO_INGEST_ERROR", "ingest raw-video"))
        ctx.exit(1)
    finally:
        if storage is not None:
            storage.close()


__all__ = ["ingest_raw_video"]
