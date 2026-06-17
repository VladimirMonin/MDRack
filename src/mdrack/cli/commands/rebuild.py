"""Rebuild commands for MDRack CLI — FTS and embedding index rebuild."""

from __future__ import annotations

import asyncio
import json
import logging
from inspect import isawaitable
from pathlib import Path
from typing import Any

import click

from mdrack.embeddings.fake import FakeEmbeddingProvider
from mdrack.embeddings.lmstudio import LMStudioProvider
from mdrack.embeddings.protocol import EmbeddingProvider
from mdrack.output.envelope import success as envelope_success
from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.fts import rebuild_fts
from mdrack.storage.sqlite.migrations import apply_migrations
from mdrack.storage.sqlite.repositories import count_chunks
from mdrack.storage.sqlite.vector import VectorIndex

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
    if json_flag:
        click.echo(json.dumps(payload, ensure_ascii=False))
    else:
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))


def _create_provider(provider_name: str, config: Any) -> EmbeddingProvider:
    if provider_name == "fake":
        return FakeEmbeddingProvider(
            dimensions=config.embedding.dimensions,
            provider_name="fake",
        )
    return LMStudioProvider(
        endpoint=config.embedding.endpoint,
        model=config.embedding.model,
        dimensions=config.embedding.dimensions,
        timeout=config.embedding.timeout_secs,
    )


async def _close_provider(provider: EmbeddingProvider | None) -> None:
    if provider is None:
        return
    close = getattr(provider, "close", None)
    if close is None:
        return
    result = close()
    if isawaitable(result):
        await result


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
        conn.commit()
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
    conn.commit()
    logger.info("Updated embedding profile metadata: %s (dims=%d)", profile_name, dimensions)


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

    db_path.parent.mkdir(parents=True, exist_ok=True)

    provider_name: str = embedding_provider or config.embedding.provider
    provider = _create_provider(provider_name, config)

    conn = get_connection(db_path)
    try:
        apply_migrations(conn, _MIGRATIONS_DIR)
        _ensure_embedding_profile(conn, profile_name, provider)

        rows = conn.execute(
            "SELECT id, embedding_text FROM chunks WHERE embedding_text IS NOT NULL",
        ).fetchall()

        chunk_ids: list[str] = []
        texts: list[str] = []
        for row in rows:
            chunk_ids.append(row["id"])
            texts.append(row["embedding_text"])

        if not texts:
            _output(
                ctx,
                envelope_success(
                    {
                        "embedded_count": 0,
                        "total_chunks": count_chunks(conn),
                        "profile": profile_name,
                        "provider": provider_name,
                    },
                    command=cmd,
                ),
            )
            return

        vectors = asyncio.run(provider.embed(texts, profile=profile_name))
        vi = VectorIndex(conn)

        for chunk_id, vec in zip(chunk_ids, vectors):
            vi.upsert(chunk_id, profile_name, vec)

        _output(
            ctx,
            envelope_success(
                {
                    "embedded_count": len(chunk_ids),
                    "total_chunks": count_chunks(conn),
                    "profile": profile_name,
                    "provider": provider_name,
                },
                command=cmd,
            ),
        )
    finally:
        conn.close()
        try:
            asyncio.run(_close_provider(provider))
        except Exception:
            logger.debug("Failed to close embedding provider", exc_info=True)
