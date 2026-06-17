"""Rebuild commands for MDRack CLI — FTS and embedding index rebuild."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import click

from mdrack.embeddings.protocol import EmbeddingProvider
from mdrack.embeddings.runtime import close_async_resource, create_embedding_provider
from mdrack.output.envelope import success as envelope_success
from mdrack.output.json_output import emit_json
from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.fts import rebuild_fts
from mdrack.storage.sqlite.migrations import apply_migrations
from mdrack.storage.sqlite.repositories import count_chunks

logger = logging.getLogger(__name__)

_MIGRATIONS_DIR = (
    Path(__file__).resolve().parents[2]
    / "storage"
    / "sqlite"
    / "migrations"
)

DEFAULT_BATCH_SIZE = 32


def _output(ctx: click.Context, payload: dict[str, Any]) -> None:
    json_flag: bool = ctx.obj.get("json_output", True) if ctx.obj else True
    emit_json(payload, pretty=not json_flag)


def _ensure_embedding_profile(conn: Any, profile_name: str, provider: object) -> None:
    existing = conn.execute(
        "SELECT name, model, dimensions, endpoint FROM embedding_profiles WHERE name = ?",
        (profile_name,),
    ).fetchone()

    dimensions = getattr(provider, "dimensions", 768)
    model_name = getattr(
        provider, "model_name", getattr(provider, "_model_name", "default")
    )
    endpoint = getattr(provider, "endpoint", getattr(provider, "_endpoint", None))

    if existing is None:
        conn.execute(
            "INSERT INTO embedding_profiles (name, model, dimensions, endpoint) VALUES (?, ?, ?, ?)",
            (profile_name, str(model_name), dimensions, endpoint),
        )
        logger.info("Created embedding profile: %s (dims=%d)", profile_name, dimensions)
        return

    if (
        existing["model"] == str(model_name)
        and existing["dimensions"] == dimensions
        and existing["endpoint"] == endpoint
    ):
        return

    conn.execute(
        "UPDATE embedding_profiles SET model = ?, dimensions = ?, endpoint = ? WHERE name = ?",
        (str(model_name), dimensions, endpoint, profile_name),
    )
    logger.info("Updated embedding profile metadata: %s (dims=%d)", profile_name, dimensions)


def _upsert_vectors(conn: Any, profile_name: str, chunk_ids: list[str], vectors: list[list[float]]) -> None:
    now = None
    rows: list[tuple[str, str, bytes, str]] = []
    for chunk_id, vector in zip(chunk_ids, vectors):
        payload = json.dumps(vector).encode("utf-8")
        if now is None:
            from datetime import datetime, timezone

            now = datetime.now(timezone.utc).isoformat()
        rows.append((chunk_id, profile_name, payload, now))

    conn.executemany(
        """
        INSERT INTO chunk_embeddings (chunk_id, profile_name, embedding, embedded_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (chunk_id, profile_name)
        DO UPDATE SET embedding = excluded.embedding,
                     embedded_at = excluded.embedded_at
        """,
        rows,
    )


def rebuild_embeddings_in_db(
    db_path: Path,
    provider: EmbeddingProvider,
    profile_name: str = "default",
) -> dict[str, Any]:
    """Rebuild every embedding vector for the selected profile in batches."""
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = get_connection(db_path)
    try:
        apply_migrations(conn, _MIGRATIONS_DIR)

        rows = conn.execute(
            "SELECT id, embedding_text FROM chunks WHERE embedding_text IS NOT NULL ORDER BY id",
        ).fetchall()
        total_chunks = count_chunks(conn)

        if not rows:
            return {
                "embedded_count": 0,
                "total_chunks": total_chunks,
                "profile": profile_name,
            }

        conn.execute("BEGIN")
        try:
            _ensure_embedding_profile(conn, profile_name, provider)
            conn.execute(
                "DELETE FROM chunk_embeddings WHERE profile_name = ?",
                (profile_name,),
            )

            embedded_count = 0
            for start in range(0, len(rows), DEFAULT_BATCH_SIZE):
                batch_rows = rows[start : start + DEFAULT_BATCH_SIZE]
                chunk_ids = [row["id"] for row in batch_rows]
                texts = [row["embedding_text"] for row in batch_rows]
                vectors = asyncio.run(provider.embed(texts, profile=profile_name))
                _upsert_vectors(conn, profile_name, chunk_ids, vectors)
                embedded_count += len(chunk_ids)

            conn.commit()
        except Exception:
            conn.rollback()
            raise

        return {
            "embedded_count": embedded_count,
            "total_chunks": total_chunks,
            "profile": profile_name,
        }
    finally:
        conn.close()


@click.command()
@click.pass_context
def rebuild_fts_cmd(ctx: click.Context) -> None:
    """Rebuild the FTS index from the chunks table."""
    cmd = "rebuild fts"
    db_path = ctx.obj.get("db_path") if ctx.obj else None

    if db_path is None:
        return

    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = get_connection(db_path)
    try:
        apply_migrations(conn, _MIGRATIONS_DIR)
        rebuild_fts(conn)
        cursor = conn.execute("SELECT COUNT(*) FROM chunks_fts")
        fts_count = cursor.fetchone()[0]
        chunk_count = count_chunks(conn)
        _output(
            ctx,
            envelope_success(
                {"fts_count": fts_count, "chunk_count": chunk_count},
                command=cmd,
            ),
        )
    finally:
        conn.close()


@click.command()
@click.option(
    "--provider",
    "embedding_provider",
    type=click.Choice(["lmstudio", "fake"]),
    default=None,
    help="Embedding provider for rebuild (default from config).",
)
@click.option(
    "--profile",
    "profile_name",
    type=str,
    default="default",
    help="Embedding profile name (default: 'default').",
)
@click.pass_context
def rebuild_embeddings_cmd(
    ctx: click.Context,
    embedding_provider: str | None,
    profile_name: str,
) -> None:
    """Rebuild all embeddings for the current active profile."""
    cmd = "rebuild embeddings"
    config = ctx.obj.get("config") if ctx.obj else None
    db_path = ctx.obj.get("db_path") if ctx.obj else None

    if config is None or db_path is None:
        return

    provider_name: str = embedding_provider or config.embedding.provider
    provider = create_embedding_provider(provider_name, config)
    try:
        data = rebuild_embeddings_in_db(db_path, provider, profile_name)
        data["provider"] = provider_name

        _output(
            ctx,
            envelope_success(data, command=cmd),
        )
    finally:
        try:
            asyncio.run(close_async_resource(provider))
        except Exception:
            logger.debug("Failed to close embedding provider", exc_info=True)
