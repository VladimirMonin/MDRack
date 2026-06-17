"""Files commands for MDRack CLI.

Provides commands to list and inspect indexed files.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

import click

from mdrack.output.envelope import error as envelope_error
from mdrack.output.envelope import success as envelope_success
from mdrack.output.errors import MDRackError, StorageError
from mdrack.output.json_output import emit_json
from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.repositories import count_files, get_file, list_files

logger = logging.getLogger(__name__)


def _open_connection(ctx: click.Context) -> sqlite3.Connection:
    """Open a SQLite connection using the resolved path from Click context.

    Args:
        ctx: Click context containing the resolved database path.

    Returns:
        An open SQLite connection.

    Raises:
        StorageError: If the database file does not exist.
    """
    db_path: Path = ctx.obj["db_path"]
    if not db_path.exists():
        raise StorageError(
            f"Database not found at {db_path}. Run 'mdrack init' first.",
        )
    return get_connection(db_path)


def _echo_json(payload: dict[str, Any]) -> None:
    """Print a JSON payload to stdout."""
    emit_json(payload)


# ---------------------------------------------------------------------------
# Group: files
# ---------------------------------------------------------------------------
@click.group()
@click.pass_context
def files(ctx: click.Context) -> None:
    """List and inspect indexed files."""


# ---------------------------------------------------------------------------
# Command: files list
# ---------------------------------------------------------------------------
@files.command("list")
@click.option("--page", type=int, default=0, help="Page number (0-indexed).")
@click.option("--page-size", type=int, default=20, help="Number of items per page.")
@click.pass_context
def files_list(ctx: click.Context, page: int, page_size: int) -> None:
    """List all indexed files with pagination."""
    cmd = "files list"
    try:
        if page < 0:
            raise ValueError("Page number must be non-negative")
        if page_size <= 0:
            raise ValueError("Page size must be positive")

        conn = _open_connection(ctx)
        try:
            offset = page * page_size
            files = list_files(conn, offset=offset, limit=page_size)
            total = count_files(conn)

            data = {
                "files": files,
                "pagination": {
                    "page": page,
                    "page_size": page_size,
                    "total": total,
                    "has_next": (offset + page_size) < total,
                },
            }
            payload = envelope_success(data, command=cmd)
            _echo_json(payload)
        finally:
            conn.close()
    except MDRackError as exc:
        payload = envelope_error(
            message=str(exc),
            code=exc.code,
            command=cmd,
        )
        _echo_json(payload)
        ctx.exit(1)
    except ValueError as exc:
        payload = envelope_error(
            message=str(exc),
            code="VALIDATION_ERROR",
            command=cmd,
        )
        _echo_json(payload)
        ctx.exit(1)


# ---------------------------------------------------------------------------
# Command: files info
# ---------------------------------------------------------------------------
@files.command("info")
@click.argument("file_id")
@click.pass_context
def files_info(ctx: click.Context, file_id: str) -> None:
    """Show metadata for a specific file."""
    cmd = "files info"
    try:
        conn = _open_connection(ctx)
        try:
            file_record = get_file(conn, file_id)
            if file_record is None:
                payload = envelope_error(
                    message=f"File '{file_id}' not found",
                    code="NOT_FOUND",
                    command=cmd,
                )
                _echo_json(payload)
                return

            payload = envelope_success({"file": file_record}, command=cmd)
            _echo_json(payload)
        finally:
            conn.close()
    except MDRackError as exc:
        payload = envelope_error(
            message=str(exc),
            code=exc.code,
            command=cmd,
        )
        _echo_json(payload)
        ctx.exit(1)
