"""Singular prepared-resource lifecycle commands through the configured catalog."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

import click

from mdrack.application.manifest import MAX_MANIFEST_BYTES, ManifestError
from mdrack.application.resource_catalog import ResourceCatalogError, ResourceCatalogErrorCode
from mdrack.output.envelope import error as envelope_error
from mdrack.output.envelope import success as envelope_success
from mdrack.output.json_output import emit_json
from mdrack.public_api import MDRackEngine

logger = logging.getLogger(__name__)


def _output(ctx: click.Context, payload: dict[str, Any]) -> None:
    json_flag: bool = ctx.obj.get("json_output", True) if ctx.obj else True
    emit_json(payload, pretty=not json_flag)


def _engine(ctx: click.Context) -> MDRackEngine:
    config = ctx.obj.get("config") if ctx.obj else None
    root = ctx.obj.get("root") if ctx.obj else None
    if config is None or not isinstance(root, Path):
        raise RuntimeError("configuration_unavailable")
    return MDRackEngine(root=root, config=config)


def _failure(ctx: click.Context, *, command: str, operation: str, error: Exception) -> None:
    code = f"RESOURCE_{operation.upper()}_ERROR"
    message = f"Resource {operation} failed"
    reason = "operation_failed"
    if isinstance(error, ManifestError):
        code = f"RESOURCE_MANIFEST_{error.code.value.upper()}"
        message = "Prepared resource import failed"
        reason = error.code.value
    elif isinstance(error, ResourceCatalogError):
        reason = error.code.value
        if error.code is ResourceCatalogErrorCode.MANIFEST_UNAVAILABLE:
            code, message = "RESOURCE_MANIFEST_UNAVAILABLE", "Prepared resource import failed"
        elif error.code is ResourceCatalogErrorCode.MANIFEST_OUTPUT_UNAVAILABLE:
            code, message = "RESOURCE_MANIFEST_OUTPUT_UNAVAILABLE", "Prepared resource export failed"
        elif error.code is ResourceCatalogErrorCode.RESOURCE_NOT_FOUND:
            code, message = "RESOURCE_NOT_FOUND", "Resource was not found"
        else:
            code, message = "RESOURCE_CATALOG_UNAVAILABLE", "Resource catalog is unavailable"
    else:
        code, message = "RESOURCE_CATALOG_UNAVAILABLE", "Resource catalog is unavailable"
    logger.error("cli.resource.%s.failed", operation, extra={"reason": reason})
    _output(ctx, envelope_error(message, code, command))
    ctx.exit(1)


def _run(
    ctx: click.Context,
    *,
    command: str,
    operation: str,
    action: Callable[[MDRackEngine], dict[str, object]],
) -> None:
    engine = None
    try:
        engine = _engine(ctx)
        data = action(engine)
        logger.info("cli.resource.%s.completed", operation)
        _output(ctx, envelope_success(data, command=command))
    except Exception as error:
        _failure(ctx, command=command, operation=operation, error=error)
    finally:
        if engine is not None:
            engine.close()


def _read_manifest(path: str) -> bytes:
    try:
        with Path(path).open("rb") as stream:
            payload = stream.read(MAX_MANIFEST_BYTES + 1)
    except (OSError, TypeError, ValueError):
        raise ResourceCatalogError(ResourceCatalogErrorCode.MANIFEST_UNAVAILABLE) from None
    if len(payload) > MAX_MANIFEST_BYTES:
        raise ResourceCatalogError(ResourceCatalogErrorCode.MANIFEST_UNAVAILABLE)
    return payload


@click.group(name="resource")
def resource() -> None:
    """Import, export, inspect, or delete one configured catalog resource."""


@resource.command(name="import")
@click.argument("manifest_path")
@click.pass_context
def import_resource(ctx: click.Context, manifest_path: str) -> None:
    """Import one bounded prepared-resource manifest."""
    _run(
        ctx,
        command="resource import",
        operation="import",
        action=lambda engine: engine.import_resource_manifest(_read_manifest(manifest_path)).to_dict(),
    )


@resource.command(name="export")
@click.argument("resource_id")
@click.option("--output", "output_path", required=True, metavar="PATH")
@click.option("--include-vectors/--no-vectors", default=True, show_default=True)
@click.option("--include-text/--no-text", default=True, show_default=True)
@click.option("--redact-source-metadata", is_flag=True, default=False)
@click.pass_context
def export_resource(
    ctx: click.Context,
    resource_id: str,
    output_path: str,
    include_vectors: bool,
    include_text: bool,
    redact_source_metadata: bool,
) -> None:
    """Export one resource through the existing manifest-v1 grammar."""
    _run(
        ctx,
        command="resource export",
        operation="export",
        action=lambda engine: engine.export_resource_manifest_file(
            resource_id,
            output_path,
            include_vectors=include_vectors,
            include_text=include_text,
            redact_source_metadata=redact_source_metadata,
        ).to_dict(),
    )


@resource.command(name="inspect")
@click.argument("resource_id")
@click.pass_context
def inspect_resource(ctx: click.Context, resource_id: str) -> None:
    """Inspect redacted aggregate counts, kinds, and fingerprints."""
    _run(
        ctx,
        command="resource inspect",
        operation="inspect",
        action=lambda engine: engine.inspect_resource(resource_id).to_dict(),
    )


@resource.command(name="delete")
@click.argument("resource_id")
@click.pass_context
def delete_resource(ctx: click.Context, resource_id: str) -> None:
    """Delete one logical resource graph atomically."""
    _run(
        ctx,
        command="resource delete",
        operation="delete",
        action=lambda engine: engine.delete_resource(resource_id).to_dict(),
    )
