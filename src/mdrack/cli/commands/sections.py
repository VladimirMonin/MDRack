"""Sections commands for MDRack CLI.

Provides commands to inspect section structure of indexed files.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

import click

from mdrack.config.loader import load_config
from mdrack.output.envelope import error as envelope_error
from mdrack.output.envelope import success as envelope_success
from mdrack.output.errors import MDRackError, StorageError
from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.repositories import get_file, list_sections

logger = logging.getLogger(__name__)


def _open_connection(ctx: click.Context) -> sqlite3.Connection:
    """Open a SQLite connection using the root from Click context.

    Args:
        ctx: Click context containing project root.

    Returns:
        An open SQLite connection.

    Raises:
        StorageError: If the database file does not exist.
    """
    root: Path = ctx.obj["root"]
    cfg = load_config()
    db_path = root / cfg.paths.store / "knowledge.db"
    if not db_path.exists():
        raise StorageError(
            f"Database not found at {db_path}. Run 'mdrack init' first.",
        )
    return get_connection(db_path)


def _echo_json(payload: dict[str, Any]) -> None:
    """Print a JSON payload to stdout."""
    click.echo(json.dumps(payload, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Group: sections
# ---------------------------------------------------------------------------
@click.group()
@click.pass_context
def sections(ctx: click.Context) -> None:
    """Inspect section structure of indexed files."""


# ---------------------------------------------------------------------------
# Command: sections list
# ---------------------------------------------------------------------------
@sections.command("list")
@click.argument("file_id")
@click.pass_context
def sections_list(ctx: click.Context, file_id: str) -> None:
    """List all sections for a file."""
    cmd = "sections list"
    try:
        conn = _open_connection(ctx)
        try:
            # Verify file exists
            file_record = get_file(conn, file_id)
            if file_record is None:
                payload = envelope_error(
                    message=f"File '{file_id}' not found",
                    code="NOT_FOUND",
                    command=cmd,
                )
                _echo_json(payload)
                return

            sections = list_sections(conn, file_id)
            payload = envelope_success({"sections": sections}, command=cmd)
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
