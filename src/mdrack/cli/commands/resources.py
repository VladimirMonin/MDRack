"""Logical resource duplicate and provider-free similarity commands."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

import click

from mdrack.application.compatibility import StoreGenerationManagerError, create_application_storage
from mdrack.application.resources import FacetFilter, ResourceQueryScope, ResourceQueryService
from mdrack.output.envelope import error as envelope_error
from mdrack.output.envelope import success as envelope_success
from mdrack.output.json_output import emit_json

logger = logging.getLogger(__name__)
_F = TypeVar("_F", bound=Callable[..., object])


def _output(ctx: click.Context, payload: dict[str, Any]) -> None:
    json_flag: bool = ctx.obj.get("json_output", True) if ctx.obj else True
    emit_json(payload, pretty=not json_flag)


def _open_catalog(ctx: click.Context) -> tuple[Any, Any]:
    config = ctx.obj.get("config") if ctx.obj else None
    root = ctx.obj.get("root") if ctx.obj else None
    if config is None or not isinstance(root, Path):
        raise RuntimeError("config_unavailable")
    try:
        storage = create_application_storage(root, config)
    except StoreGenerationManagerError:
        raise RuntimeError("resource_generation_unavailable") from None
    catalog = getattr(storage, "resource_store", None)
    if catalog is None:
        storage.close()
        raise RuntimeError("resource_generation_unavailable")
    return storage, catalog


def _scope_options(function: _F) -> _F:
    options = (
        click.option("--resource-kind", "resource_kinds", multiple=True),
        click.option("--media-type", "media_types", multiple=True),
        click.option("--source-namespace", "source_namespaces", multiple=True),
        click.option("--representation-kind", "representation_kinds", multiple=True),
        click.option("--modality", "modalities", multiple=True),
        click.option("--unit-kind", "unit_kinds", multiple=True),
        click.option("--facet-any", "facets_any", multiple=True, metavar="NAMESPACE=VALUE"),
        click.option("--facet-all", "facets_all", multiple=True, metavar="NAMESPACE=VALUE"),
        click.option("--facet-none", "facets_none", multiple=True, metavar="NAMESPACE=VALUE"),
    )
    decorated: Callable[..., object] = function
    for option in reversed(options):
        decorated = option(decorated)
    return decorated  # type: ignore[return-value]


def _facets(values: tuple[str, ...]) -> tuple[FacetFilter, ...]:
    parsed = []
    for value in values:
        namespace, separator, facet_value = value.partition("=")
        if not separator or not namespace or not facet_value:
            raise ValueError("facet_filter_invalid")
        parsed.append(FacetFilter(namespace, facet_value))
    return tuple(parsed)


def _scope(
    resource_kinds: tuple[str, ...],
    media_types: tuple[str, ...],
    source_namespaces: tuple[str, ...],
    representation_kinds: tuple[str, ...],
    modalities: tuple[str, ...],
    unit_kinds: tuple[str, ...],
    facets_any: tuple[str, ...],
    facets_all: tuple[str, ...],
    facets_none: tuple[str, ...],
) -> ResourceQueryScope:
    return ResourceQueryScope(
        resource_kinds=resource_kinds,
        media_types=media_types,
        source_namespaces=source_namespaces,
        representation_kinds=representation_kinds,
        modalities=modalities,
        unit_kinds=unit_kinds,
        facets_any=_facets(facets_any),
        facets_all=_facets(facets_all),
        facets_none=_facets(facets_none),
    )


@click.group(name="resources")
def resources() -> None:
    """Query logical resource duplicates and existing-vector similarity."""


@resources.command(name="duplicates")
@click.argument("resource_id")
@click.option("--limit", type=click.IntRange(min=1), default=20, show_default=True)
@_scope_options
@click.pass_context
def duplicates(
    ctx: click.Context,
    resource_id: str,
    limit: int,
    **filters: tuple[str, ...],
) -> None:
    """Find other resources with the selected resource's exact byte hash."""
    command = "resources duplicates"
    storage = None
    try:
        storage, catalog = _open_catalog(ctx)
        result = ResourceQueryService(catalog).find_duplicates(
            resource_id,
            scope=_scope(**filters),
            limit=limit,
        )
        logger.info(
            "cli.resources.duplicates.completed",
            extra={"result_count": len(result.results)},
        )
        _output(ctx, envelope_success(result.to_dict(), command=command))
    except Exception:
        logger.error(
            "cli.resources.duplicates.failed",
            extra={"reason": "resource_duplicate_error"},
        )
        _output(
            ctx,
            envelope_error("Resource duplicate lookup failed", "RESOURCE_DUPLICATE_ERROR", command),
        )
        ctx.exit(1)
    finally:
        if storage is not None:
            storage.close()


@resources.command(name="similar")
@click.argument("query_unit_id")
@click.option("--space-id", required=True)
@click.option("--limit", type=click.IntRange(min=1), default=20, show_default=True)
@click.option("--include-same-resource", is_flag=True, default=False)
@_scope_options
@click.pass_context
def similar(
    ctx: click.Context,
    query_unit_id: str,
    space_id: str,
    limit: int,
    include_same_resource: bool,
    **filters: tuple[str, ...],
) -> None:
    """Search from an existing whole-resource vector without a provider call."""
    command = "resources similar"
    storage = None
    try:
        storage, catalog = _open_catalog(ctx)
        result = ResourceQueryService(catalog).find_similar(
            query_unit_id,
            space_id,
            scope=_scope(**filters),
            limit=limit,
            exclude_same_resource=not include_same_resource,
        )
        logger.info(
            "cli.resources.similar.completed",
            extra={"result_count": len(result.results), "degraded": result.degraded},
        )
        _output(ctx, envelope_success(result.to_dict(), command=command))
    except Exception:
        logger.error(
            "cli.resources.similar.failed",
            extra={"reason": "resource_similarity_error"},
        )
        _output(
            ctx,
            envelope_error("Resource similarity lookup failed", "RESOURCE_SIMILARITY_ERROR", command),
        )
        ctx.exit(1)
    finally:
        if storage is not None:
            storage.close()


__all__ = ["resources"]
