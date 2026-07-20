"""Explicit metadata inspection and projection commands."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import click

from mdrack.adapters.markdown_it import MarkdownItParser
from mdrack.application.compatibility import StoreGenerationManagerError, create_application_storage
from mdrack.application.metadata_projection import (
    MetadataScalar,
    metadata_projection_policy_from_config,
    resolve_json_pointer,
)
from mdrack.application.resource_catalog import MetadataCatalogService
from mdrack.output.envelope import error as envelope_error
from mdrack.output.envelope import success as envelope_success
from mdrack.output.json_output import emit_json

logger = logging.getLogger(__name__)


def _output(ctx: click.Context, payload: dict[str, Any]) -> None:
    json_flag: bool = ctx.obj.get("json_output", True) if ctx.obj else True
    emit_json(payload, pretty=not json_flag)


def _open_service(ctx: click.Context) -> tuple[Any, MetadataCatalogService]:
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
    return storage, MetadataCatalogService(catalog)


def _failure(ctx: click.Context, *, command: str, code: str, message: str) -> None:
    logger.error("cli.metadata.failed", extra={"reason": code.lower()})
    _output(ctx, envelope_error(message, code, command))
    ctx.exit(1)


@click.group(name="metadata")
def metadata() -> None:
    """Inspect intentional metadata payloads and projection behavior."""


@metadata.command(name="show")
@click.argument("resource_id")
@click.pass_context
def show(ctx: click.Context, resource_id: str) -> None:
    """Show exact source metadata for one logical resource."""
    command = "metadata show"
    storage = None
    try:
        storage, service = _open_service(ctx)
        inspection = service.inspect(resource_id)
        data = inspection.to_dict()
        logger.info(
            "cli.metadata.show.completed",
            extra={"facet_count": len(inspection.facets)},
        )
        _output(ctx, envelope_success(data, command=command))
    except Exception:
        _failure(
            ctx,
            command=command,
            code="METADATA_SHOW_ERROR",
            message="Metadata could not be read",
        )
    finally:
        if storage is not None:
            storage.close()


@metadata.command(name="facets")
@click.option("--namespace", default=None)
@click.pass_context
def facets(ctx: click.Context, namespace: str | None) -> None:
    """List decoded exact metadata facets in deterministic order."""
    command = "metadata facets"
    storage = None
    try:
        storage, service = _open_service(ctx)
        values = service.facets(namespace=namespace)
        logger.info("cli.metadata.facets.completed", extra={"facet_count": len(values)})
        _output(
            ctx,
            envelope_success(
                {"facets": [item.to_dict() for item in values]},
                command=command,
            ),
        )
    except Exception:
        _failure(
            ctx,
            command=command,
            code="METADATA_FACETS_ERROR",
            message="Metadata facets could not be read",
        )
    finally:
        if storage is not None:
            storage.close()


def _value_type(value: MetadataScalar) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if type(value) is int:
        return "integer"
    if isinstance(value, float):
        return "float"
    return "string"


@metadata.command(name="projection-check")
@click.argument("source", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.pass_context
def projection_check(ctx: click.Context, source: Path) -> None:
    """Preview configured projection paths without writing the catalog."""
    command = "metadata projection-check"
    config = ctx.obj.get("config") if ctx.obj else None
    try:
        if config is None:
            raise RuntimeError("config_unavailable")
        document = MarkdownItParser(
            metadata_invalid_policy=config.metadata.invalid_policy
        ).parse(
            source,
            document_id="metadata-projection-check",
            relative_path=source.name,
        )
        policy = metadata_projection_policy_from_config(config.metadata)
        projected = policy.project(document.frontmatter, fallback_title=document.title)
        lexical_paths = []
        store_only_paths = []
        ignored_paths = []
        facet_paths = []
        for projection in policy.projections:
            if projection.mode == "lexical_text":
                lexical_paths.append(projection.path)
            elif projection.mode == "store_only":
                store_only_paths.append(projection.path)
            elif projection.mode == "ignore":
                ignored_paths.append(projection.path)
            elif projection.mode in {"facet", "facet_many"}:
                try:
                    value = resolve_json_pointer(document.frontmatter, projection.path)
                except KeyError:
                    continue
                values = value if isinstance(value, (tuple, list)) else (value,)
                scalar = next(
                    (
                        item
                        for item in values
                        if item is None
                        or isinstance(item, (str, bool, float))
                        or type(item) is int
                    ),
                    None,
                )
                if scalar is not None or any(item is None for item in values):
                    facet_paths.append(
                        {
                            "namespace": projection.namespace,
                            "path": projection.path,
                            "value_type": _value_type(scalar),
                        }
                    )
        data = {
            "canonical_title": projected.canonical_title,
            "lexical_paths": lexical_paths,
            "facet_paths": facet_paths,
            "store_only_paths": store_only_paths,
            "ignored_paths": ignored_paths,
            "diagnostics": [
                {"category": item.category, "count": item.count}
                for item in document.metadata_diagnostics
            ],
            "projection_policy_fingerprint": projected.policy_fingerprint,
        }
        logger.info(
            "cli.metadata.projection_check.completed",
            extra={
                "lexical_path_count": len(lexical_paths),
                "facet_path_count": len(facet_paths),
                "diagnostic_count": sum(item.count for item in document.metadata_diagnostics),
            },
        )
        _output(ctx, envelope_success(data, command=command))
    except Exception:
        _failure(
            ctx,
            command=command,
            code="METADATA_PROJECTION_ERROR",
            message="Metadata projection could not be checked",
        )


__all__ = ["metadata"]
