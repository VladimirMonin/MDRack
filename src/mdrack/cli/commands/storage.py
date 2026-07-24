"""Explicit fresh compact-storage lifecycle commands."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import click

from mdrack.adapters.sqlite.generation_runtime import SQLiteGenerationRuntime
from mdrack.application.fresh_reindex import FreshCompactReindexService
from mdrack.application.generation_manager import StoreGenerationManager
from mdrack.application.store_generations import GenerationContractKind, GenerationState
from mdrack.embeddings.runtime import close_async_resource, create_embedding_provider
from mdrack.output.envelope import error as envelope_error
from mdrack.output.envelope import success as envelope_success
from mdrack.output.json_output import emit_json

logger = logging.getLogger(__name__)


def _output(ctx: click.Context, payload: dict[str, Any]) -> None:
    json_flag: bool = ctx.obj.get("json_output", True) if ctx.obj else True
    emit_json(payload, pretty=not json_flag)


def _manager(ctx: click.Context) -> StoreGenerationManager:
    store_dir: Path | None = ctx.obj.get("store_dir") if ctx.obj else None
    if store_dir is None:
        raise RuntimeError("storage configuration unavailable")
    return StoreGenerationManager(store_dir, runtime=SQLiteGenerationRuntime())


def _emit_failure(ctx: click.Context, command: str, code: str, reason: str) -> None:
    logger.error("cli.storage.failed", extra={"status": "failed", "reason": reason})
    _output(ctx, envelope_error("Storage operation could not be completed", code, command))
    ctx.exit(1)


@click.group()
def storage() -> None:
    """Build, verify, and explicitly activate fresh compact storage candidates."""


@storage.command("rebuild-fresh")
@click.option(
    "--provider",
    "embedding_provider",
    type=click.Choice(["lmstudio", "fake"]),
    default=None,
    help="Embedding provider used to re-embed immutable source Markdown.",
)
@click.option("--profile", "profile_name", default="default", show_default=True)
@click.option(
    "--candidate-name",
    type=str,
    default=None,
    help="Optional deterministic candidate generation identity.",
)
@click.option(
    "--vector-codec",
    type=click.Choice(["float32"]),
    default="float32",
    show_default=True,
)
@click.option(
    "--vector-backend",
    type=click.Choice(["builtin"]),
    default="builtin",
    show_default=True,
)
@click.pass_context
def rebuild_fresh(
    ctx: click.Context,
    embedding_provider: str | None,
    profile_name: str,
    candidate_name: str | None,
    vector_codec: str,
    vector_backend: str,
) -> None:
    """Reparse source files into one inactive clean v2 float32 candidate."""
    command = "storage rebuild-fresh"
    config = ctx.obj.get("config") if ctx.obj else None
    root: Path = ctx.obj.get("root", Path(".")) if ctx.obj else Path(".")
    if config is None:
        _emit_failure(ctx, command, "CONFIG_ERROR", "config_unavailable")
        return
    provider: object | None = None
    try:
        provider_name = embedding_provider or config.embedding.provider
        provider = create_embedding_provider(provider_name, config)
        manager = _manager(ctx)
        if candidate_name is not None:
            manager = StoreGenerationManager(
                manager.store_dir,
                runtime=SQLiteGenerationRuntime(),
                id_factory=lambda: candidate_name,
            )
        candidate = FreshCompactReindexService(
            root=root,
            config=config,
            provider=provider,
            manager=manager,
            profile_name=profile_name,
        ).rebuild()
        _output(
            ctx,
            envelope_success(
                {
                    "generation_id": candidate.generation.generation_id,
                    "state": candidate.generation.state.value,
                    "source_count": candidate.source_count,
                    "vector_codec": vector_codec,
                    "vector_backend": vector_backend,
                },
                command=command,
            ),
        )
    except click.exceptions.Exit:
        raise
    except Exception:
        _emit_failure(ctx, command, "FRESH_REBUILD_ERROR", "fresh_rebuild_failed")
    finally:
        if provider is not None:
            try:
                asyncio.run(close_async_resource(provider))
            except Exception:
                logger.warning("cli.storage.cleanup.failed reason=provider_close_failed")


@storage.command("verify")
@click.argument("generation_id")
@click.pass_context
def verify(ctx: click.Context, generation_id: str) -> None:
    """Reopen and verify an inactive or active clean v2 candidate."""
    command = "storage verify"
    try:
        manager = _manager(ctx)
        generation = manager.load_generation(generation_id)
        if (
            generation.contract_kind is not GenerationContractKind.RESOURCE_CORE_V2
            or generation.state is not GenerationState.READY
        ):
            raise RuntimeError("fresh candidate is not ready")
        counts = manager.verify_generation(generation_id)
        _output(
            ctx,
            envelope_success(
                {
                    "generation_id": generation.generation_id,
                    "state": generation.state.value,
                    "counts": dict(counts),
                },
                command=command,
            ),
        )
    except click.exceptions.Exit:
        raise
    except Exception:
        _emit_failure(ctx, command, "STORAGE_VERIFY_ERROR", "candidate_verify_failed")


@storage.command("activate")
@click.argument("generation_id")
@click.pass_context
def activate(ctx: click.Context, generation_id: str) -> None:
    """Atomically perform the one-way cutover to a verified v2 candidate."""
    command = "storage activate"
    try:
        pointer = _manager(ctx).activate_candidate_one_way(generation_id)
        _output(
            ctx,
            envelope_success(
                {
                    "generation_id": pointer.generation_id,
                    "contract_kind": pointer.contract_kind.value,
                },
                command=command,
            ),
        )
    except click.exceptions.Exit:
        raise
    except Exception:
        _emit_failure(ctx, command, "STORAGE_ACTIVATE_ERROR", "one_way_activation_failed")


__all__ = ["storage"]
