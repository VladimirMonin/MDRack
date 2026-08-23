"""CLI composition for direct local WAVE audio ingestion."""

from __future__ import annotations

import logging
from pathlib import Path

import click

from mdrack.application.compatibility import ApplicationStoreError, create_application_storage
from mdrack.ingestion.raw_audio import RawAudioIngestionService
from mdrack.ingestion.raw_source_provenance import RawSourceError
from mdrack.output.envelope import error as envelope_error
from mdrack.output.envelope import success as envelope_success
from mdrack.output.json_output import emit_json

logger = logging.getLogger(__name__)


@click.command(name="audio")
@click.argument("source_path", type=click.Path(dir_okay=False, path_type=Path))
@click.option("--source-ref", required=True)
@click.option("--allow-external-stt", is_flag=True, default=False)
@click.option("--stt-command", default=None)
@click.option("--language", default=None)
@click.option("--producer", default="caller-supplied", show_default=True)
@click.pass_context
def ingest_audio(
    ctx: click.Context,
    source_path: Path,
    source_ref: str,
    allow_external_stt: bool,
    stt_command: str | None,
    language: str | None,
    producer: str,
) -> None:
    """Ingest one local RIFF/WAVE source through an authorized stdin STT command."""
    root = ctx.obj.get("root") if ctx.obj else None
    config = ctx.obj.get("config") if ctx.obj else None
    if not isinstance(root, Path) or config is None:
        emit_json(envelope_error("Configuration could not be loaded", "CONFIG_ERROR", "ingest audio"))
        ctx.exit(1)
    storage = None
    try:
        storage = create_application_storage(root, config, create=True)
        result = RawAudioIngestionService(storage.resource_store).ingest(
            source_path,
            source_ref=source_ref,
            root=root,
            stt_command=stt_command or "",
            allow_external_stt=allow_external_stt,
            language=language,
            producer=producer,
        )
        emit_json(envelope_success({**result.to_dict(), "persisted": True}, command="ingest audio"))
    except RawSourceError as exc:
        logger.error("cli.ingest.raw_audio.failed", extra={"status": "failed", "reason": exc.code.value})
        emit_json(envelope_error("Audio source could not be ingested", "RAW_AUDIO_INGEST_ERROR", "ingest audio"))
        ctx.exit(1)
    except (ApplicationStoreError, ValueError, OSError):
        logger.error("cli.ingest.raw_audio.failed", extra={"status": "failed", "reason": "operation_failed"})
        emit_json(envelope_error("Audio ingestion failed", "RAW_AUDIO_INGEST_ERROR", "ingest audio"))
        ctx.exit(1)
    finally:
        if storage is not None:
            storage.close()


__all__ = ["ingest_audio"]
