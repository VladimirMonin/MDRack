"""Fixed-catalog chunk and file readers for MDRack CLI."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import click

from mdrack.application.compatibility import ApplicationStoreError, create_application_storage
from mdrack.application.resource_catalog import project_unit
from mdrack.output.envelope import error as envelope_error
from mdrack.output.envelope import success as envelope_success
from mdrack.output.errors import MDRackError, StorageError
from mdrack.output.json_output import emit_json
from mdrack_core.domain import UNIT_TEXT_CHUNK

logger = logging.getLogger(__name__)


def _output(ctx: click.Context, payload: dict[str, Any]) -> None:
    """Print a JSON envelope using the caller's requested formatting."""
    json_flag: bool = ctx.obj.get("json_output", True) if ctx.obj else True
    emit_json(payload, pretty=not json_flag)


def _open_storage(ctx: click.Context) -> Any:
    """Open the fixed catalog for read-only access without creating it."""
    config = ctx.obj.get("config") if ctx.obj else None
    root: Path = ctx.obj.get("root", Path(".")) if ctx.obj else Path(".")
    try:
        return create_application_storage(root, config, create=False)
    except ApplicationStoreError:
        raise StorageError("Catalog not initialized. Run 'mdrack init' first.") from None


@click.group()
@click.pass_context
def read(ctx: click.Context) -> None:
    """Read units, chunks, or document resources by logical identity."""


@read.command("unit")
@click.argument("unit_id")
@click.pass_context
def read_unit(ctx: click.Context, unit_id: str) -> None:
    """Read any search unit by logical identity (including timed/frame units)."""
    command = "read unit"
    try:
        storage = _open_storage(ctx)
        try:
            unit = storage.resource_store.read_unit(unit_id)
        finally:
            storage.close()
    except MDRackError as exc:
        _output(ctx, envelope_error(str(exc), exc.code, command))
        ctx.exit(1)
    except Exception:
        logger.error("cli.read.unit.failed reason=catalog_unavailable")
        _output(ctx, envelope_error("Catalog could not be read", "STORAGE_ERROR", command))
        ctx.exit(1)
    if unit is None:
        _output(ctx, envelope_error("Unit not found", "NOT_FOUND", command))
        ctx.exit(1)
    _output(ctx, envelope_success({"unit": project_unit(unit).to_dict()}, command=command))


@read.command("chunk")
@click.argument("chunk_id")
@click.option(
    "--context",
    "context_mode",
    type=click.Choice(["none", "neighbors"], case_sensitive=False),
    default="none",
    help="Include adjacent chunks; neighbors are supported only by read chunk.",
)
@click.pass_context
def read_chunk(ctx: click.Context, chunk_id: str, context_mode: str) -> None:
    """Read a text chunk by logical identity."""
    command = "read chunk"
    non_chunk = False
    try:
        storage = _open_storage(ctx)
        try:
            unit = storage.resource_store.read_unit(chunk_id)
            non_chunk = unit is not None and unit.unit_kind != UNIT_TEXT_CHUNK
            if non_chunk:
                chunk = None
                neighbors = ()
            else:
                chunk = storage.get_chunk_by_logical_id(chunk_id)
                neighbors = storage.get_chunk_neighbors(chunk_id) if context_mode == "neighbors" else ()
        finally:
            storage.close()
    except MDRackError as exc:
        _output(ctx, envelope_error(str(exc), exc.code, command))
        ctx.exit(1)
    except Exception:
        logger.error("cli.read.chunk.failed reason=catalog_unavailable")
        _output(ctx, envelope_error("Catalog could not be read", "STORAGE_ERROR", command))
        ctx.exit(1)
    if non_chunk:
        _output(
            ctx,
            envelope_error(
                "Only text chunks can be read with 'read chunk'; use 'read unit'",
                "VALIDATION_ERROR",
                command,
            ),
        )
        ctx.exit(1)
    if chunk is None:
        _output(ctx, envelope_error("Chunk not found", "NOT_FOUND", command))
        ctx.exit(1)
    data: dict[str, Any] = {"chunk": chunk}
    if context_mode == "neighbors":
        data["neighbors"] = list(neighbors)
    _output(ctx, envelope_success(data, command=command))


@read.command("file")
@click.argument("file_id")
@click.pass_context
def read_file(ctx: click.Context, file_id: str) -> None:
    """Read document metadata by its logical identity."""
    command = "read file"
    try:
        storage = _open_storage(ctx)
        try:
            record = storage.get_public_file_by_logical_id(file_id)
        finally:
            storage.close()
    except MDRackError as exc:
        _output(ctx, envelope_error(str(exc), exc.code, command))
        ctx.exit(1)
    except Exception:
        logger.error("cli.read.file.failed reason=catalog_unavailable")
        _output(ctx, envelope_error("Catalog could not be read", "STORAGE_ERROR", command))
        ctx.exit(1)
    if record is None:
        _output(ctx, envelope_error("File not found", "NOT_FOUND", command))
        ctx.exit(1)
    _output(ctx, envelope_success({"file": record}, command=command))


@read.command("outline")
@click.argument("file_id")
@click.pass_context
def read_outline(ctx: click.Context, file_id: str) -> None:
    """Read canonical document headings by the file logical identity."""
    command = "read outline"
    try:
        storage = _open_storage(ctx)
        try:
            outline = storage.get_file_outline(file_id)
        finally:
            storage.close()
    except MDRackError as exc:
        _output(ctx, envelope_error(str(exc), exc.code, command))
        ctx.exit(1)
    except Exception:
        logger.error("cli.read.outline.failed reason=catalog_unavailable")
        _output(ctx, envelope_error("Catalog could not be read", "STORAGE_ERROR", command))
        ctx.exit(1)
    if outline is None:
        _output(ctx, envelope_error("File not found", "NOT_FOUND", command))
        ctx.exit(1)
    _output(ctx, envelope_success(outline, command=command))
