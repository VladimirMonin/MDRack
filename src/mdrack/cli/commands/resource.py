"""Singular prepared-resource lifecycle commands through the configured catalog."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable

import click

from mdrack._observability import AppEventLogger
from mdrack.application.manifest import MAX_MANIFEST_BYTES, ManifestError
from mdrack.application.resource_catalog import ResourceCatalogError, ResourceCatalogErrorCode
from mdrack.output.envelope import error as envelope_error
from mdrack.output.envelope import success as envelope_success
from mdrack.output.json_output import emit_json
from mdrack.public_api import MDRackEngine

logger = AppEventLogger(logging.getLogger(__name__))


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
    elif operation in {
        "enrichment_binding", "enrich_prepared", "read_artifact", "publication_binding", "reindex", "withdraw",
    }:
        message = "Prepared resource operation failed"
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


@resource.command(name="ocr-binding")
@click.argument("resource_id")
@click.pass_context
def ocr_binding(ctx: click.Context, resource_id: str) -> None:
    """Return an explicit local source/lifecycle binding, never a creation grant."""
    engine = None
    try:
        engine = _engine(ctx)
        result = engine.ready_ocr_binding(resource_id)
    except Exception:
        _output(ctx, envelope_error("Ready OCR binding unavailable", "READY_OCR_ERROR", "resource ocr-binding"))
        ctx.exit(1)
    else:
        _output(ctx, envelope_success(result, command="resource ocr-binding"))
    finally:
        if engine is not None:
            engine.close()


@resource.command(name="enrich-ocr")
@click.argument("resource_id")
@click.argument("artifact_path", type=click.Path(path_type=Path))
@click.option("--variant", default="default", show_default=True)
@click.option("--source-image", "source_path", type=click.Path(path_type=Path), default=None)
@click.option("--lexical/--no-lexical", default=True, show_default=True)
@click.option("--dense/--no-dense", default=False, show_default=True)
@click.option("--require-durable", is_flag=True)
@click.option("--max-retries", type=click.IntRange(0, 3), default=1, show_default=True)
@click.pass_context
def enrich_ocr(
    ctx: click.Context, resource_id: str, artifact_path: Path, variant: str,
    lexical: bool, dense: bool, require_durable: bool, max_retries: int, source_path: Path | None,
) -> None:
    """Publish ready caller-owned OCR; never invoke OCR or embedding models."""
    engine = None
    try:
        engine = _engine(ctx)
        result = engine.enrich_ready_ocr(
            resource_id, artifact_path, variant=variant, lexical=lexical, dense=dense,
            require_durable=require_durable, max_retries=max_retries, source_path=source_path,
        )
    except Exception:
        _output(ctx, envelope_error("Ready OCR enrichment failed", "READY_OCR_ERROR", "resource enrich-ocr"))
        ctx.exit(1)
    else:
        _output(ctx, envelope_success(result, command="resource enrich-ocr"))
    finally:
        if engine is not None:
            engine.close()


@resource.command(name="enrichment-binding")
@click.argument("resource_id")
@click.pass_context
def enrichment_binding(ctx: click.Context, resource_id: str) -> None:
    """Return the live prepared-artifact binding, without creation authority."""
    _run(ctx,command="resource enrichment-binding",operation="enrichment_binding",
         action=lambda engine: engine.prepared_enrichment_binding(resource_id))


@resource.command(name="enrich-prepared")
@click.argument("resource_id")
@click.argument("artifact_path",type=click.Path(path_type=Path))
@click.option("--kind",type=click.Choice(["ocr","text","transcript","frames","video"]),required=True)
@click.option("--purpose",required=True)
@click.option("--variant",default="default",show_default=True)
@click.option("--lexical/--no-lexical",default=True,show_default=True)
@click.option("--dense/--no-dense",default=False,show_default=True)
@click.option("--artifact-store",type=click.Path(path_type=Path),default=None)
@click.option("--require-durable",is_flag=True)
@click.option("--inputs",default="[]",help='JSON list of [purpose, variant] immediate input slots.')
@click.option("--max-retries",type=click.IntRange(0,3),default=1,show_default=True)
@click.pass_context
def enrich_prepared(ctx: click.Context, resource_id: str, artifact_path: Path, *, kind: str, purpose: str,
                    variant: str, lexical: bool, dense: bool, artifact_store: Path | None,
                    require_durable: bool, inputs: str, max_retries: int) -> None:
    """Publish prepared artifacts/vectors without extraction or embedding calls."""
    def action(engine: MDRackEngine) -> dict[str, object]:
        selected = json.loads(inputs)
        if (type(selected) is not list or any(type(item) is not list or len(item) != 2
                or any(not isinstance(value,str) or not value.strip() for value in item) for item in selected)):
            raise ValueError("invalid_input_slots")
        return engine.enrich_prepared(resource_id,artifact_path,kind=kind,purpose=purpose,variant=variant,
            lexical=lexical,dense=dense,artifact_store=artifact_store,require_durable=require_durable,
            inputs=tuple(tuple(item) for item in selected),max_retries=max_retries)
    _run(ctx,command="resource enrich-prepared",operation="enrich_prepared",action=action)


@resource.command(name="read-artifact")
@click.argument("reference")
@click.option("--artifact-store",type=click.Path(path_type=Path),required=True)
@click.pass_context
def read_artifact(ctx: click.Context, reference: str, artifact_store: Path) -> None:
    """Read an explicitly selected historical host-owned artifact; never index it."""
    _run(ctx,command="resource read-artifact",operation="read_artifact",
         action=lambda engine: engine.read_enrichment_artifact(reference,artifact_store=artifact_store))


@resource.command(name="publication-binding")
@click.argument("resource_id")
@click.pass_context
def publication_binding(ctx: click.Context, resource_id: str) -> None:
    """Return the local CAS envelope for a new, explicit reindex operation."""
    _run(ctx,command="resource publication-binding",operation="publication_binding",
         action=lambda engine: engine.publication_binding(resource_id))


@resource.command(name="reindex")
@click.argument("resource_id")
@click.argument("artifact_path",type=click.Path(path_type=Path))
@click.option("--max-retries",type=click.IntRange(0,3),default=1,show_default=True)
@click.pass_context
def reindex(ctx: click.Context, resource_id: str, artifact_path: Path, max_retries: int) -> None:
    """Reconcile an explicitly bound prepared base; keep unrelated projections."""
    _run(ctx,command="resource reindex",operation="reindex",
         action=lambda engine: engine.reindex_resource_file(resource_id,artifact_path,max_retries=max_retries))


@resource.command(name="withdraw")
@click.argument("resource_id")
@click.pass_context
def withdraw(ctx: click.Context, resource_id: str) -> None:
    """Remove searchable projections while keeping the live logical lifecycle."""
    _run(ctx,command="resource withdraw",operation="withdraw",
         action=lambda engine: engine.withdraw_resource(resource_id))


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
