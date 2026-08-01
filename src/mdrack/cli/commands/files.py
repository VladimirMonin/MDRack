"""Fixed-catalog file inspection commands for MDRack CLI."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import click

from mdrack.application.compatibility import ApplicationStoreError, create_application_storage
from mdrack.output.envelope import error as envelope_error
from mdrack.output.envelope import success as envelope_success
from mdrack.output.errors import MDRackError, StorageError
from mdrack.output.json_output import emit_json

logger = logging.getLogger(__name__)


def _output(ctx: click.Context, payload: dict[str, Any]) -> None:
    """Print a JSON envelope using the caller's requested formatting."""
    json_flag: bool = ctx.obj.get("json_output", True) if ctx.obj else True
    emit_json(payload, pretty=not json_flag)


def _open_storage(ctx: click.Context) -> Any:
    """Open the only normal application catalog without creating it."""
    config = ctx.obj.get("config") if ctx.obj else None
    root: Path = ctx.obj.get("root", Path(".")) if ctx.obj else Path(".")
    try:
        return create_application_storage(root, config, create=False)
    except ApplicationStoreError:
        raise StorageError("Catalog not initialized. Run 'mdrack init' first.") from None


@click.group()
@click.pass_context
def files(ctx: click.Context) -> None:
    """List and inspect document resources in the fixed catalog."""


@files.command("list")
@click.option("--page", type=int, default=0, help="Page number (0-indexed).")
@click.option("--page-size", type=int, default=20, help="Number of items per page.")
@click.pass_context
def files_list(ctx: click.Context, page: int, page_size: int) -> None:
    """List document resources with stable path ordering and pagination."""
    command = "files list"
    try:
        if page < 0:
            raise ValueError("Page number must be non-negative")
        if page_size <= 0:
            raise ValueError("Page size must be positive")
        storage = _open_storage(ctx)
        try:
            offset = page * page_size
            records = storage.list_public_files(offset=offset, limit=page_size)
            total = storage.count_public_files()
        finally:
            storage.close()
        _output(
            ctx,
            envelope_success(
                {
                    "files": list(records),
                    "pagination": {
                        "page": page,
                        "page_size": page_size,
                        "total": total,
                        "has_next": (offset + page_size) < total,
                    },
                },
                command=command,
            ),
        )
    except ValueError as exc:
        _output(ctx, envelope_error(str(exc), "VALIDATION_ERROR", command))
        ctx.exit(1)
    except MDRackError as exc:
        _output(ctx, envelope_error(str(exc), exc.code, command))
        ctx.exit(1)
    except Exception:
        logger.error("cli.files.list.failed reason=catalog_unavailable")
        _output(ctx, envelope_error("Catalog could not be read", "STORAGE_ERROR", command))
        ctx.exit(1)


@files.command("info")
@click.argument("file_id")
@click.pass_context
def files_info(ctx: click.Context, file_id: str) -> None:
    """Show one document resource by its logical identity."""
    command = "files info"
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
        logger.error("cli.files.info.failed reason=catalog_unavailable")
        _output(ctx, envelope_error("Catalog could not be read", "STORAGE_ERROR", command))
        ctx.exit(1)
    if record is None:
        _output(ctx, envelope_error("File not found", "NOT_FOUND", command))
        ctx.exit(1)
    _output(ctx, envelope_success({"file": record}, command=command))
