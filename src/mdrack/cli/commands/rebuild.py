"""Rebuild commands for the fixed MDRack catalog."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import click

from mdrack.application.compatibility import create_application_storage
from mdrack.domain.profiles import EmbeddingProfile
from mdrack.embeddings.runtime import (
    close_async_resource,
    create_embedding_provider,
    embedding_profile_from_config,
)
from mdrack.indexing.indexer import run_indexer
from mdrack.output.envelope import error as envelope_error
from mdrack.output.envelope import success as envelope_success
from mdrack.output.json_output import emit_json
from mdrack.ports.embeddings import EmbeddingProvider

logger = logging.getLogger(__name__)

# Retained only for imports from older tests; the legacy direct-database helper
# below now fails before opening any caller-supplied path.
DEFAULT_BATCH_SIZE = 32


def _output(ctx: click.Context, payload: dict[str, Any]) -> None:
    json_flag: bool = ctx.obj.get("json_output", True) if ctx.obj else True
    emit_json(payload, pretty=not json_flag)


def _profile_from_provider(
    profile_name: str,
    provider: object,
    config: Any | None = None,
) -> EmbeddingProfile:
    """Build the configured profile without reopening a legacy store."""
    if config is None:
        raise ValueError("embedding_profile_config_required")
    return embedding_profile_from_config(config, provider, profile_name)


def rebuild_embeddings_in_db(
    db_path: Path,
    provider: EmbeddingProvider,
    profile_name: str = "default",
    *,
    config: Any | None = None,
) -> dict[str, Any]:
    """Reject the retired direct-database helper before it can open a path."""
    del db_path, provider, profile_name, config
    raise RuntimeError("legacy_embedding_rebuild_unsupported")


@click.command()
@click.pass_context
def rebuild_fts_cmd(ctx: click.Context) -> None:
    """Rebuild the FTS index in the fixed application catalog."""
    cmd = "rebuild fts"
    config = ctx.obj.get("config") if ctx.obj else None
    root: Path = ctx.obj.get("root", Path(".")) if ctx.obj else Path(".")
    if config is None:
        return

    storage = create_application_storage(root, config, create=False)
    try:
        fts_count, chunk_count = storage.rebuild_fts_index()
        _output(
            ctx,
            envelope_success(
                {"fts_count": fts_count, "chunk_count": chunk_count},
                command=cmd,
            ),
        )
    finally:
        storage.close()


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
    """Reindex the fixed catalog with fresh embeddings."""
    cmd = "rebuild embeddings"
    config = ctx.obj.get("config") if ctx.obj else None
    if config is None:
        return

    provider_name: str = embedding_provider or config.embedding.provider
    provider = create_embedding_provider(provider_name, config)
    try:
        root: Path = ctx.obj.get("root", Path(".")) if ctx.obj else Path(".")
        result = run_indexer(
            root,
            config,
            provider=provider,
            profile=profile_name,
            force_reindex=True,
        )
        data = {
            "embedded_count": result.chunks_created,
            "total_chunks": result.chunks_created,
            "profile": profile_name,
            "provider": provider_name,
        }
        if result.status != "success":
            error_codes = tuple(sorted(set(result.error_codes)))
            code = error_codes[0] if error_codes else "INDEX_REBUILD_FAILED"
            _output(
                ctx,
                envelope_error(
                    "Embedding rebuild did not complete successfully",
                    code,
                    cmd,
                    details={
                        **data,
                        "status": result.status,
                        "error_codes": list(error_codes),
                    },
                ),
            )
            raise click.exceptions.Exit(1)
        _output(ctx, envelope_success(data, command=cmd))
    finally:
        try:
            asyncio.run(close_async_resource(provider))
        except Exception:
            logger.warning("cli.rebuild.cleanup.failed reason=provider_close_failed")
