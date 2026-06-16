"""Read commands for MDRack CLI.

Provides click subcommands to retrieve chunks, sections, and files by ID
from the SQLite knowledge store.
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
from mdrack.storage.sqlite.repositories import (
    get_chunk,
    get_file,
    get_neighbors,
    get_section,
    list_chunks_by_section,
    list_sections,
)

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


def _output(ctx: click.Context, payload: dict[str, Any]) -> None:
    """Print a JSON envelope to stdout (or the Click echo target).

    Respects the --json flag: when False, pretty-prints with indent=2.
    """
    json_flag: bool = ctx.obj.get("json_output", True)
    if json_flag:
        click.echo(json.dumps(payload, ensure_ascii=False))
    else:
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# Group: read
# ---------------------------------------------------------------------------
@click.group()
@click.pass_context
def read(ctx: click.Context) -> None:
    """Read chunks, sections, or files by ID."""


# ---------------------------------------------------------------------------
# Subcommand: chunk
# ---------------------------------------------------------------------------
@read.command("chunk")
@click.argument("chunk_id")
@click.option(
    "--context",
    "context_mode",
    type=click.Choice(["none", "neighbors"], case_sensitive=False),
    default="none",
    help="Include context: 'neighbors' adds prev/next chunks.",
)
@click.pass_context
def read_chunk(ctx: click.Context, chunk_id: str, context_mode: str) -> None:
    """Read a chunk by ID.

    Optionally includes neighboring chunks when --context neighbors is used.
    """
    cmd = "read chunk"
    try:
        conn = _open_connection(ctx)
        try:
            chunk = get_chunk(conn, chunk_id)
            if chunk is None:
                payload = envelope_error(
                    message=f"Chunk '{chunk_id}' not found",
                    code="NOT_FOUND",
                    command=cmd,
                )
                _output(ctx, payload)
                ctx.exit(1)

            data: dict[str, Any] = {"chunk": chunk}

            if context_mode == "neighbors":
                neighbors = get_neighbors(conn, chunk_id, count=1)
                data["neighbors"] = neighbors

            payload = envelope_success(data, command=cmd)
            _output(ctx, payload)
        finally:
            conn.close()
    except MDRackError as exc:
        payload = envelope_error(
            message=str(exc),
            code=exc.code,
            command=cmd,
        )
        _output(ctx, payload)
        ctx.exit(1)


# ---------------------------------------------------------------------------
# Subcommand: section
# ---------------------------------------------------------------------------
@read.command("section")
@click.argument("section_id")
@click.pass_context
def read_section(ctx: click.Context, section_id: str) -> None:
    """Read a section and all its chunks by section ID."""
    cmd = "read section"
    try:
        conn = _open_connection(ctx)
        try:
            section = get_section(conn, section_id)
            if section is None:
                payload = envelope_error(
                    message=f"Section '{section_id}' not found",
                    code="NOT_FOUND",
                    command=cmd,
                )
                _output(ctx, payload)
                ctx.exit(1)

            chunks = list_chunks_by_section(conn, section_id)
            data: dict[str, Any] = {
                "section": section,
                "chunks": chunks,
            }

            payload = envelope_success(data, command=cmd)
            _output(ctx, payload)
        finally:
            conn.close()
    except MDRackError as exc:
        payload = envelope_error(
            message=str(exc),
            code=exc.code,
            command=cmd,
        )
        _output(ctx, payload)
        ctx.exit(1)


# ---------------------------------------------------------------------------
# Subcommand: file
# ---------------------------------------------------------------------------
@read.command("file")
@click.argument("file_id")
@click.pass_context
def read_file(ctx: click.Context, file_id: str) -> None:
    """Read file metadata and list of sections by file ID."""
    cmd = "read file"
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
                _output(ctx, payload)
                ctx.exit(1)

            sections = list_sections(conn, file_id)
            data: dict[str, Any] = {
                "file": file_record,
                "sections": sections,
            }

            payload = envelope_success(data, command=cmd)
            _output(ctx, payload)
        finally:
            conn.close()
    except MDRackError as exc:
        payload = envelope_error(
            message=str(exc),
            code=exc.code,
            command=cmd,
        )
        _output(ctx, payload)
        ctx.exit(1)
