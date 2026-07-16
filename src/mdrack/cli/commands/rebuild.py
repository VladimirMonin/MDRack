"""Rebuild commands for MDRack CLI — FTS and embedding index rebuild."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import click

from mdrack.domain.profiles import EmbeddingProfile
from mdrack.embeddings.protocol import EmbeddingProvider
from mdrack.embeddings.runtime import (
    close_async_resource,
    create_embedding_provider,
    embedding_profile_from_config,
)
from mdrack.output.envelope import success as envelope_success
from mdrack.output.json_output import emit_json
from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.fts import rebuild_fts
from mdrack.storage.sqlite.migrations import apply_migrations, get_migrations_dir
from mdrack.storage.sqlite.repositories import count_chunks

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 32


def _output(ctx: click.Context, payload: dict[str, Any]) -> None:
    json_flag: bool = ctx.obj.get("json_output", True) if ctx.obj else True
    emit_json(payload, pretty=not json_flag)


def _profile_from_provider(
    profile_name: str,
    provider: object,
    config: Any | None = None,
) -> EmbeddingProfile:
    if config is not None:
        return embedding_profile_from_config(config, provider, profile_name)
    dimensions = getattr(provider, "dimensions", 768)
    model_name = getattr(provider, "model_name", getattr(provider, "_model_name", "default"))
    provider_name = getattr(provider, "provider_name", getattr(provider, "_provider_name", "unknown"))
    return EmbeddingProfile(
        name=profile_name,
        provider=str(provider_name),
        runtime="lmstudio-gui" if provider_name == "lmstudio" else "offline-test",
        model_key=str(model_name),
        model_family="qwen3-embedding" if "qwen3" in str(model_name).lower() else "unknown",
        quantization="unknown",
        output_dimensions=dimensions,
        query_instruction="Represent the query for retrieval",
        normalization_mode="l2",
        endpoint_family="openai_embeddings",
    )


def _ensure_embedding_profile(conn: Any, profile: EmbeddingProfile, provider: object) -> None:
    existing = conn.execute(
        "SELECT name FROM embedding_profiles WHERE name = ?",
        (profile.name,),
    ).fetchone()
    endpoint = getattr(provider, "endpoint", getattr(provider, "_endpoint", None))

    if existing is None:
        conn.execute(
            """
            INSERT INTO embedding_profiles (
                name, model, dimensions, endpoint, fingerprint, provider, runtime,
                model_key, model_family, quantization, query_instruction_hash,
                normalization_mode, endpoint_family
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                profile.name,
                profile.model_key,
                profile.output_dimensions,
                endpoint,
                profile.fingerprint,
                profile.provider,
                profile.runtime,
                profile.model_key,
                profile.model_family,
                profile.quantization,
                profile.query_instruction_hash,
                profile.normalization_mode,
                profile.endpoint_family,
            ),
        )
        logger.info(
            "embedding.profile.created profile=%s dimensions=%d",
            profile.name,
            profile.output_dimensions,
        )
        return

    conn.execute(
        """
        UPDATE embedding_profiles
        SET model = ?, dimensions = ?, endpoint = ?, fingerprint = ?, provider = ?,
            runtime = ?, model_key = ?, model_family = ?, quantization = ?,
            query_instruction_hash = ?, normalization_mode = ?, endpoint_family = ?
        WHERE name = ?
        """,
        (
            profile.model_key,
            profile.output_dimensions,
            endpoint,
            profile.fingerprint,
            profile.provider,
            profile.runtime,
            profile.model_key,
            profile.model_family,
            profile.quantization,
            profile.query_instruction_hash,
            profile.normalization_mode,
            profile.endpoint_family,
            profile.name,
        ),
    )
    logger.info(
        "embedding.profile.updated profile=%s dimensions=%d",
        profile.name,
        profile.output_dimensions,
    )


def _upsert_vectors(
    conn: Any,
    profile: EmbeddingProfile,
    chunk_ids: list[str],
    vectors: list[list[float]],
) -> None:
    now = None
    rows: list[tuple[str, str, bytes, str, str]] = []
    for chunk_id, vector in zip(chunk_ids, vectors):
        if len(vector) != profile.output_dimensions:
            raise ValueError("embedding vector dimension does not match active profile")
        payload = json.dumps(vector).encode("utf-8")
        if now is None:
            from datetime import datetime, timezone

            now = datetime.now(timezone.utc).isoformat()
        rows.append((chunk_id, profile.name, payload, now, profile.fingerprint))

    conn.executemany(
        """
        INSERT INTO chunk_embeddings (
            chunk_id, profile_name, embedding, embedded_at, profile_fingerprint
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (chunk_id, profile_name)
        DO UPDATE SET embedding = excluded.embedding,
                     embedded_at = excluded.embedded_at,
                     profile_fingerprint = excluded.profile_fingerprint
        """,
        rows,
    )


def rebuild_embeddings_in_db(
    db_path: Path,
    provider: EmbeddingProvider,
    profile_name: str = "default",
    *,
    config: Any | None = None,
) -> dict[str, Any]:
    """Rebuild every embedding vector for the selected profile in batches."""
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = get_connection(db_path)
    try:
        apply_migrations(conn, get_migrations_dir())

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
            profile = _profile_from_provider(profile_name, provider, config)
            conn.execute(
                "DELETE FROM chunk_embeddings WHERE profile_name = ?",
                (profile_name,),
            )
            _ensure_embedding_profile(conn, profile, provider)

            embedded_count = 0
            for start in range(0, len(rows), DEFAULT_BATCH_SIZE):
                batch_rows = rows[start : start + DEFAULT_BATCH_SIZE]
                chunk_ids = [row["id"] for row in batch_rows]
                texts = [row["embedding_text"] for row in batch_rows]
                vectors = asyncio.run(provider.embed(texts, profile=profile_name))
                _upsert_vectors(conn, profile, chunk_ids, vectors)
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
        apply_migrations(conn, get_migrations_dir())
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
        data = rebuild_embeddings_in_db(db_path, provider, profile_name, config=config)
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
