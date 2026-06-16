"""Scan command for MDRack CLI.

Provides `mdrack scan` with --changed and --provider options.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import click

from mdrack.embeddings.fake import FakeEmbeddingProvider
from mdrack.indexing.indexer import IndexerResult, run_indexer
from mdrack.output.envelope import error as envelope_error
from mdrack.output.envelope import success as envelope_success

logger = logging.getLogger(__name__)


def _output(ctx: click.Context, payload: dict[str, Any]) -> None:
    json_flag: bool = ctx.obj.get("json_output", True) if ctx.obj else True
    if json_flag:
        click.echo(json.dumps(payload, ensure_ascii=False))
    else:
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))


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
    type=click.Choice(["fake"]),
    default=None,
    help="Embedding provider: 'fake' for testing (default: no embeddings).",
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

    provider: object | None = None
    if embedding_provider == "fake":
        provider = FakeEmbeddingProvider(
            dimensions=config.embedding.dimensions,
            provider_name="fake",
        )

    try:
        result: IndexerResult = run_indexer(
            root=root,
            config=config,
            provider=provider,
        )

        data: dict[str, Any] = {
            "run_id": result.run_id,
            "files_seen": result.files_seen,
            "files_changed": result.files_changed,
            "files_deleted": result.files_deleted,
            "chunks_created": result.chunks_created,
        }
        _output(ctx, envelope_success(data, command=cmd))
    except Exception as exc:
        logger.exception("Scan command failed")
        _output(ctx, envelope_error(str(exc), "INTERNAL_ERROR", cmd))
        ctx.exit(1)
