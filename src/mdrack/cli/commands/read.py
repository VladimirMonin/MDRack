"""Read commands for MDRack CLI.

Provides click subcommands to retrieve chunks, sections, and files by ID
from the SQLite knowledge store.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

import click

from mdrack.adapters.sqlite.index_storage import SQLiteIndexStorage
from mdrack.output.envelope import error as envelope_error
from mdrack.output.envelope import success as envelope_success
from mdrack.output.errors import MDRackError, StorageError
from mdrack.output.json_output import emit_json
from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.repositories import (
    get_file,
    get_neighbors,
    get_section,
    list_chunks_by_section,
    list_sections,
)

logger = logging.getLogger(__name__)


def _record_id(conn: sqlite3.Connection, table: str, public_id: str) -> str | None:
    if table not in {"files", "sections", "chunks"}:
        raise ValueError("unsupported read table")
    row = conn.execute(
        f"SELECT id FROM {table} WHERE logical_id = ? OR (logical_id IS NULL AND id = ?)",
        (public_id, public_id),
    ).fetchone()
    return str(row["id"]) if row is not None else None


def _public_file(record: dict[str, Any]) -> dict[str, Any]:
    public_id = record.get("logical_id") or record["id"]
    return {
        key: value
        for key, value in record.items()
        if key not in {"index_run_id", "logical_id", "id"}
    } | {"id": public_id, "logical_id": public_id}


def _public_section(record: dict[str, Any]) -> dict[str, Any]:
    public_id = record.get("logical_id") or record["id"]
    return {
        key: value
        for key, value in record.items()
        if key not in {"file_id", "parent_id", "logical_id", "id"}
    } | {"id": public_id, "logical_id": public_id}


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


def _output(ctx: click.Context, payload: dict[str, Any]) -> None:
    """Print a JSON envelope to stdout (or the Click echo target).

    Respects the --json flag: when False, pretty-prints with indent=2.
    """
    json_flag: bool = ctx.obj.get("json_output", True)
    emit_json(payload, pretty=not json_flag)


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
            storage = SQLiteIndexStorage(conn)
            record_id = _record_id(conn, "chunks", chunk_id)
            chunk = storage.get_chunk_by_logical_id(chunk_id)
            if chunk is None:
                payload = envelope_error(
                    message="Chunk not found",
                    code="NOT_FOUND",
                    command=cmd,
                )
                _output(ctx, payload)
                ctx.exit(1)

            data: dict[str, Any] = {"chunk": chunk}

            if context_mode == "neighbors":
                neighbors = get_neighbors(conn, record_id or chunk_id, count=1)
                data["neighbors"] = [
                    storage.get_chunk_by_logical_id(str(item.get("logical_id") or item["id"]))
                    for item in neighbors
                ]

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
            record_id = _record_id(conn, "sections", section_id)
            section = get_section(conn, record_id or section_id)
            if section is None:
                payload = envelope_error(
                    message="Section not found",
                    code="NOT_FOUND",
                    command=cmd,
                )
                _output(ctx, payload)
                ctx.exit(1)

            storage = SQLiteIndexStorage(conn)
            chunks = [
                storage.get_chunk_by_logical_id(str(item.get("logical_id") or item["id"]))
                for item in list_chunks_by_section(conn, record_id or section_id)
            ]
            data: dict[str, Any] = {
                "section": _public_section(section),
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
            record_id = _record_id(conn, "files", file_id)
            file_record = get_file(conn, record_id or file_id)
            if file_record is None:
                payload = envelope_error(
                    message="File not found",
                    code="NOT_FOUND",
                    command=cmd,
                )
                _output(ctx, payload)
                ctx.exit(1)

            sections = [
                _public_section(item)
                for item in list_sections(conn, record_id or file_id)
            ]
            data: dict[str, Any] = {
                "file": _public_file(file_record),
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
