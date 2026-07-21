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
from mdrack.application.resources import FacetFilter, ResourceQueryScope, ResourceQueryService
from mdrack.output.envelope import error as envelope_error
from mdrack.output.envelope import success as envelope_success
from mdrack.output.json_output import emit_json
from mdrack_sqlite import SQLiteCatalog

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


def _facet_filters(values: tuple[str, ...]) -> tuple[FacetFilter, ...]:
    parsed = []
    for value in values:
        namespace, separator, facet_value = value.partition("=")
        if not separator or not namespace or not facet_value:
            raise ValueError("facet_filter_invalid")
        parsed.append(FacetFilter(namespace, facet_value))
    return tuple(parsed)


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


@resource.command(name="similar")
@click.argument("query_unit_id")
@click.option("--catalog", "catalog_path", required=True, metavar="PATH")
@click.option("--space-id", required=True)
@click.option("--embedding-fingerprint", required=True)
@click.option(
    "--aggregation",
    type=click.Choice(["direct-text", "token-weighted-centroid"]),
    required=True,
)
@click.option(
    "--basis",
    type=click.Choice(["textual-content"]),
    default="textual-content",
    show_default=True,
)
@click.option("--kind", "resource_kinds", multiple=True)
@click.option("--namespace", "source_namespaces", multiple=True)
@click.option("--facet-any", "facets_any", multiple=True, metavar="NAMESPACE=VALUE")
@click.option("--facet-all", "facets_all", multiple=True, metavar="NAMESPACE=VALUE")
@click.option("--facet-none", "facets_none", multiple=True, metavar="NAMESPACE=VALUE")
@click.option("--limit", type=click.IntRange(min=1), default=20, show_default=True)
@click.pass_context
def similar_resource(
    ctx: click.Context,
    query_unit_id: str,
    catalog_path: str,
    space_id: str,
    embedding_fingerprint: str,
    aggregation: str,
    basis: str,
    resource_kinds: tuple[str, ...],
    source_namespaces: tuple[str, ...],
    facets_any: tuple[str, ...],
    facets_all: tuple[str, ...],
    facets_none: tuple[str, ...],
    limit: int,
) -> None:
    """Find whole-resource similarity explicitly based on textual content."""
    del basis
    command = "resource similar"
    try:
        with SQLiteCatalog.open_readonly(catalog_path) as catalog:
            result = ResourceQueryService(catalog).find_textual_similarity(
                query_unit_id,
                space_id,
                aggregation=f"{aggregation.replace('-', '_')}_v1",
                expected_fingerprint=embedding_fingerprint,
                scope=ResourceQueryScope(
                    resource_kinds=resource_kinds,
                    source_namespaces=source_namespaces,
                    facets_any=_facet_filters(facets_any),
                    facets_all=_facet_filters(facets_all),
                    facets_none=_facet_filters(facets_none),
                ),
                limit=limit,
            )
        logger.info(
            "cli.resource.similar.completed",
            extra={"status": "completed", "result_count": len(result.results)},
        )
        _output(ctx, envelope_success(result.to_dict(), command=command))
    except Exception as error:
        _failure(
            ctx,
            command=command,
            operation="similarity",
            error=error,
        )


__all__ = ["resource"]
