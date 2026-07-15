"""Scan command for MDRack CLI.

Provides `mdrack scan` with --changed and --provider options.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import click

from mdrack.embeddings.runtime import close_async_resource, create_embedding_provider
from mdrack.indexing.indexer import IndexerResult, run_indexer
from mdrack.output.envelope import error as envelope_error
from mdrack.output.envelope import success as envelope_success
from mdrack.output.json_output import emit_json

logger = logging.getLogger(__name__)


def _output(ctx: click.Context, payload: dict[str, Any]) -> None:
    json_flag: bool = ctx.obj.get("json_output", True) if ctx.obj else True
    emit_json(payload, pretty=not json_flag)


def _create_provider(provider_name: str, config: Any) -> object:
    """Compatibility wrapper used by tests and command code."""
    return create_embedding_provider(provider_name, config)


@click.command()
@click.option(
    "--changed",
    is_flag=True,
    default=False,
    help="Scan only changed files.",
)
@click.option(
    "--provider",
    "embedding_provider",
    type=click.Choice(["lmstudio", "fake"]),
    default=None,
    help="Embedding provider for scan embeddings (default from config).",
)
@click.pass_context
def cli_scan(
    ctx: click.Context,
    changed: bool,
    embedding_provider: str | None,
) -> None:
    """Scan Markdown files and build/update the knowledge index."""
    cmd = "scan"
    config = ctx.obj.get("config") if ctx.obj else None
    root: Path = ctx.obj.get("root", Path(".")) if ctx.obj else Path(".")

    if config is None:
        _output(ctx, envelope_error("Configuration not available", "CONFIG_ERROR", cmd))
        ctx.exit(1)
        return

    provider_name: str = embedding_provider or config.embedding.provider
    provider = _create_provider(provider_name, config)

    try:
        result: IndexerResult = run_indexer(
            root=root,
            config=config,
            provider=provider,
        )

        data: dict[str, Any] = {
            "run_id": result.run_id,
            "status": result.status,
            "files_seen": result.files_seen,
            "files_changed": result.files_changed,
            "files_indexed": result.files_indexed,
            "files_failed": result.files_failed,
            "files_deleted": result.files_deleted,
            "chunks_created": result.chunks_created,
            "errors_count": result.errors_count,
        }
        if "CORPUS_SCAN_FAILED" in result.error_codes:
            _output(ctx, envelope_error("Corpus scan failed", "CORPUS_SCAN_FAILED", cmd))
            raise click.exceptions.Exit(1)
        _output(ctx, envelope_success(data, command=cmd))
        if result.status == "failed":
            raise click.exceptions.Exit(1)
    except click.exceptions.Exit:
        raise
    except Exception:
        logger.error("cli.scan.failed", extra={"status": "failed", "reason": "internal_error"})
        _output(ctx, envelope_error("Scan failed", "INTERNAL_ERROR", cmd))
        ctx.exit(1)
    finally:
        if provider is not None:
            try:
                import asyncio

                asyncio.run(close_async_resource(provider))
            except Exception:
                logger.debug("embedding.provider.close_failed reason=cleanup_error")
