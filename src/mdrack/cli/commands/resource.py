"""Singular prepared-resource lifecycle commands for an explicit clean catalog."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import click

from mdrack.application.manifest import ManifestError
from mdrack.application.resource_catalog import (
    PreparedResourceCatalog,
    ResourceCatalogError,
    ResourceCatalogErrorCode,
)
from mdrack.output.envelope import error as envelope_error
from mdrack.output.envelope import success as envelope_success
from mdrack.output.json_output import emit_json

logger = logging.getLogger(__name__)


def _output(ctx: click.Context, payload: dict[str, Any]) -> None:
    json_flag: bool = ctx.obj.get("json_output", True) if ctx.obj else True
    emit_json(payload, pretty=not json_flag)


def _failure(
    ctx: click.Context,
    *,
    command: str,
    operation: str,
    error: Exception,
) -> None:
    code = f"RESOURCE_{operation.upper()}_ERROR"
    message = (
        "Prepared resource import failed"
        if operation == "import"
        else f"Resource {operation} failed"
    )
    reason = "operation_failed"
    if isinstance(error, ManifestError):
        code = f"RESOURCE_MANIFEST_{error.code.value.upper()}"
        message = "Prepared resource import failed"
        reason = error.code.value
    elif isinstance(error, ResourceCatalogError):
        if error.code is ResourceCatalogErrorCode.MANIFEST_UNAVAILABLE:
            code = "RESOURCE_MANIFEST_UNAVAILABLE"
            message = "Prepared resource import failed"
        elif error.code is ResourceCatalogErrorCode.RESOURCE_NOT_FOUND:
            code = "RESOURCE_NOT_FOUND"
            message = "Resource was not found"
        else:
            code = "RESOURCE_CATALOG_UNAVAILABLE"
        reason = error.code.value
    else:
        code = "RESOURCE_CATALOG_UNAVAILABLE"
        reason = "catalog_unavailable"
    logger.error(
        f"cli.resource.{operation}.failed",
        extra={"status": "failed", "reason": reason},
    )
    _output(ctx, envelope_error(message, code, command))
    ctx.exit(1)


def _run(
    ctx: click.Context,
    *,
    command: str,
    operation: str,
    catalog_path: str,
    action: Callable[[PreparedResourceCatalog], dict[str, object]],
) -> None:
    try:
        with PreparedResourceCatalog.open(catalog_path) as catalog:
            data = action(catalog)
        logger.info(
            f"cli.resource.{operation}.completed",
            extra={"status": "completed", "operation": operation},
        )
        _output(ctx, envelope_success(data, command=command))
    except Exception as error:
        _failure(
            ctx,
            command=command,
            operation=operation,
            error=error,
        )


@click.group(name="resource")
def resource() -> None:
    """Import, inspect, or delete one resource in an explicit clean catalog."""


@resource.command(name="import")
@click.argument("manifest_path")
@click.option("--catalog", "catalog_path", required=True, metavar="PATH")
@click.pass_context
def import_resource(ctx: click.Context, manifest_path: str, catalog_path: str) -> None:
    """Import one bounded prepared-resource manifest."""
    _run(
        ctx,
        command="resource import",
        operation="import",
        catalog_path=catalog_path,
        action=lambda catalog: catalog.import_file(manifest_path).to_dict(),
    )


@resource.command(name="inspect")
@click.argument("resource_id")
@click.option("--catalog", "catalog_path", required=True, metavar="PATH")
@click.pass_context
def inspect_resource(ctx: click.Context, resource_id: str, catalog_path: str) -> None:
    """Inspect redacted counts, kinds, and fingerprints for one resource."""
    _run(
        ctx,
        command="resource inspect",
        operation="inspection",
        catalog_path=catalog_path,
        action=lambda catalog: catalog.inspect(resource_id).to_dict(),
    )


@resource.command(name="delete")
@click.argument("resource_id")
@click.option("--catalog", "catalog_path", required=True, metavar="PATH")
@click.pass_context
def delete_resource(ctx: click.Context, resource_id: str, catalog_path: str) -> None:
    """Delete one logical resource graph atomically."""
    _run(
        ctx,
        command="resource delete",
        operation="delete",
        catalog_path=catalog_path,
        action=lambda catalog: catalog.delete(resource_id).to_dict(),
    )


__all__ = ["resource"]
