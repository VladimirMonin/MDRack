"""Privacy-safe artifact-cache status, verification, and guarded purge commands."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import click

from mdrack.application.artifact_cache import ArtifactCache, ArtifactCacheReport
from mdrack.output.envelope import error as envelope_error
from mdrack.output.envelope import success as envelope_success
from mdrack.output.json_output import emit_json

logger = logging.getLogger(__name__)


def _output(ctx: click.Context, payload: dict[str, Any]) -> None:
    emit_json(payload, pretty=not bool(ctx.obj.get("json_output", True) if ctx.obj else True))


def _cache(ctx: click.Context) -> ArtifactCache:
    config = ctx.obj.get("config") if ctx.obj else None
    root: Path = ctx.obj.get("root", Path(".")) if ctx.obj else Path(".")
    if config is None:
        raise ValueError("config_unavailable")
    directory = Path(config.cache.directory)
    if not directory.is_absolute():
        directory = root / directory
    cache_root = directory.resolve(strict=False)
    project_root = root.resolve(strict=False)
    configured_store = Path(config.paths.store)
    if not configured_store.is_absolute():
        configured_store = project_root / configured_store
    store_root = configured_store.resolve(strict=False)
    if (
        cache_root == project_root
        or cache_root == store_root
        or project_root.is_relative_to(cache_root)
        or store_root.is_relative_to(cache_root)
    ):
        raise ValueError("cache_root_overlaps_protected_root")
    return ArtifactCache(cache_root, max_entry_bytes=config.cache.max_entry_bytes)


def _report_data(report: ArtifactCacheReport) -> dict[str, int | bool]:
    return {
        "ok": report.ok,
        "entry_count": report.entry_count,
        "valid_entries": report.valid_entries,
        "corrupt_entries": report.corrupt_entries,
        "payload_bytes": report.payload_bytes,
    }


def _fixed_failure(ctx: click.Context, *, code: str, message: str, reason: str) -> None:
    logger.error("cli.cache.failed", extra={"status": "failed", "reason": reason})
    _output(ctx, envelope_error(message, code, "cache"))
    ctx.exit(1)


@click.group()
def cache() -> None:
    """Inspect or explicitly purge the standalone artifact cache."""


@cache.command(name="status")
@click.pass_context
def cache_status(ctx: click.Context) -> None:
    """Show count-only cache status without reading source or catalog data."""
    try:
        config = ctx.obj.get("config") if ctx.obj else None
        enabled = bool(config.cache.enabled) if config is not None else False
        report = _cache(ctx).status() if enabled else ArtifactCacheReport(0, 0, 0, 0)
        _output(
            ctx,
            envelope_success(
                {
                    "enabled": enabled,
                    "entry_count": report.entry_count,
                    "payload_bytes": report.payload_bytes,
                },
                command="cache status",
            ),
        )
    except Exception:
        _fixed_failure(
            ctx,
            code="CACHE_STATUS_ERROR",
            message="Cache status could not be read",
            reason="status_unavailable",
        )


@cache.command(name="verify")
@click.pass_context
def cache_verify(ctx: click.Context) -> None:
    """Hash all cache entries without deleting or repairing them."""
    try:
        report = _cache(ctx).verify()
        _output(ctx, envelope_success(_report_data(report), command="cache verify"))
        if not report.ok:
            ctx.exit(1)
    except click.exceptions.Exit:
        raise
    except Exception:
        _fixed_failure(
            ctx,
            code="CACHE_VERIFY_ERROR",
            message="Cache verification could not be completed",
            reason="verification_unavailable",
        )


@cache.command(name="purge")
@click.option("--confirm", is_flag=True, default=False, help="Confirm destructive cache deletion.")
@click.pass_context
def cache_purge(ctx: click.Context, confirm: bool) -> None:
    """Delete only cache entries; source and retrieval catalogs are untouched."""
    if not confirm:
        _fixed_failure(
            ctx,
            code="CACHE_CONFIRMATION_REQUIRED",
            message="Cache purge requires --confirm",
            reason="confirmation_missing",
        )
        return
    try:
        removed = _cache(ctx).purge(confirm=True)
        _output(
            ctx,
            envelope_success(
                {"purged": True, "removed_entries": removed},
                command="cache purge",
            ),
        )
    except Exception:
        _fixed_failure(
            ctx,
            code="CACHE_PURGE_ERROR",
            message="Cache purge could not be completed",
            reason="purge_failed",
        )


__all__ = ["cache"]
