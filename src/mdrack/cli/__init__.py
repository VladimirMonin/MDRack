"""CLI entrypoint for MDRack."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import click

from mdrack import __version__
from mdrack.cli.commands.cache import cache as cache_group
from mdrack.cli.commands.eval import evaluation as eval_group
from mdrack.cli.commands.files import files as files_group
from mdrack.cli.commands.images import image as image_group
from mdrack.cli.commands.metadata import metadata as metadata_group
from mdrack.cli.commands.model import model as model_group
from mdrack.cli.commands.read import read
from mdrack.cli.commands.rebuild import rebuild_embeddings_cmd, rebuild_fts_cmd
from mdrack.cli.commands.resource import resource as resource_group
from mdrack.cli.commands.resources import facets as facets_command
from mdrack.cli.commands.resources import find_similar as find_similar_command
from mdrack.cli.commands.resources import resources as resources_group
from mdrack.cli.commands.resources import similar as similar_command
from mdrack.cli.commands.scan import cli_scan
from mdrack.cli.commands.search import cli_search
from mdrack.cli.commands.transcript import ingest as ingest_group
from mdrack.cli.commands.video import ingest_video
from mdrack.config.loader import load_config, resolve_config_path
from mdrack.diagnostics.storage import analyze_application_storage
from mdrack.output.envelope import error as envelope_error
from mdrack.output.envelope import success as envelope_success
from mdrack.output.errors import ConfigError, MDRackError
from mdrack.output.json_output import emit_json

logger = logging.getLogger(__name__)

# Click context object keys
CTX_CONFIG = "config"
CTX_ROOT = "root"
CTX_JSON = "json_output"
CTX_STORE_DIR = "store_dir"
CTX_DB_PATH = "db_path"
CTX_CONFIG_PATH = "config_path"
CTX_CONFIG_FILE = "config_file"


def _resolve_store_dir(root: Path, store: str) -> Path:
    """Resolve the configured store path against the selected root."""
    store_path = Path(store)
    if store_path.is_absolute():
        return store_path
    return root / store_path


def _configure_logging() -> None:
    """Enable CLI logging when the application has no logger setup."""
    root_logger = logging.getLogger()
    if root_logger.handlers:
        return
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def _output(ctx: click.Context, payload: dict[str, Any]) -> None:
    """Print a JSON envelope to stdout (or the Click echo target).

    Respects the --json flag: when False, pretty-prints with indent=2.
    """
    json_flag: bool = ctx.obj.get(CTX_JSON, True) if ctx.obj else True
    emit_json(payload, pretty=not json_flag)


def _command_name(ctx: click.Context) -> str:
    """Derive the command name string from the Click context."""
    parts: list[str] = []
    current: click.Context | None = ctx
    while current is not None:
        if current.info_name and current.info_name != "mdrack":
            parts.append(current.info_name)
        current = current.parent
    return " ".join(reversed(parts)) or "mdrack"


def _emit_fixed_command_error(
    ctx: click.Context,
    *,
    code: str,
    message: str,
    event: str,
    reason: str,
) -> None:
    """Emit one privacy-safe command error without serializing the exception."""
    logger.error(
        event,
        extra={"status": "failed", "reason": reason},
    )
    _output(
        ctx,
        envelope_error(message=message, code=code, command=_command_name(ctx)),
    )
    ctx.exit(1)


def _handle_exception(ctx: click.Context, exc: Exception) -> None:
    """Catch exceptions and output JSON error envelope."""
    if isinstance(exc, MDRackError):
        cmd = _command_name(ctx)
        payload = envelope_error(message=str(exc), code=exc.code, command=cmd, details=exc.details)
        _output(ctx, payload)
        ctx.exit(1)
        return
    # Unexpected errors
    cmd = _command_name(ctx)
    payload = envelope_error(
        message=str(exc),
        code="INTERNAL_ERROR",
        command=cmd,
    )
    _output(ctx, payload)
    ctx.exit(1)


@click.group(invoke_without_command=True)
@click.version_option(version=__version__, prog_name="mdrack")
@click.option("--root", default=".", type=click.Path(exists=True, file_okay=False), help="Project root directory.")
@click.option("--json", "json_output", is_flag=True, default=True, help="Output JSON (default: True).")
@click.option(
    "--config-file",
    type=click.Path(dir_okay=False),
    default=None,
    help="Path to TOML config file.",
)
@click.pass_context
def main(ctx: click.Context, root: str, json_output: bool, config_file: str | None) -> None:
    """MDRack - Local command-line Markdown knowledge rack for AI agents."""
    _configure_logging()
    ctx.ensure_object(dict)
    resolved_root = Path(root).resolve()
    ctx.obj[CTX_ROOT] = resolved_root
    ctx.obj[CTX_JSON] = json_output
    ctx.obj[CTX_CONFIG_FILE] = config_file

    if ctx.invoked_subcommand == "storage-analyze":
        return

    # Load configuration
    try:
        toml_path = Path(config_file) if config_file else None
        resolved_config_path = resolve_config_path(root=resolved_root, toml_path=toml_path)
        if toml_path is not None and not resolved_config_path.is_file():
            raise ConfigError(f"Config file not found: {toml_path}")
        config = load_config(toml_path=toml_path, root=resolved_root)
        ctx.obj[CTX_CONFIG] = config
        ctx.obj[CTX_CONFIG_PATH] = resolved_config_path
        ctx.obj[CTX_STORE_DIR] = _resolve_store_dir(resolved_root, config.paths.store)
        ctx.obj[CTX_DB_PATH] = ctx.obj[CTX_STORE_DIR] / "catalog.sqlite3"
    except Exception:
        _emit_fixed_command_error(
            ctx,
            code="CONFIG_ERROR",
            message="Configuration could not be loaded",
            event="cli.config.failed",
            reason="config_invalid",
        )
        return

    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


# ---------------------------------------------------------------------------
# Command: init
# ---------------------------------------------------------------------------
@main.command()
@click.pass_context
def init(ctx: click.Context) -> None:
    """Initialize a local knowledge store."""
    cmd = _command_name(ctx)

    try:
        from mdrack.adapters.sqlite.canonical_catalog import open_application_catalog
        from mdrack_sqlite.contract_v2 import SQLITE_CATALOG_V2_SCHEMA_VERSION

        catalog = open_application_catalog(
            ctx.obj[CTX_ROOT],
            ctx.obj[CTX_CONFIG],
            create=True,
        )
        try:
            schema_version = SQLITE_CATALOG_V2_SCHEMA_VERSION
        finally:
            catalog.close()

        _output(ctx, envelope_success({
            "status": "initialized",
            "catalog_path": "catalog.sqlite3",
            "schema_version": schema_version,
        }, command=cmd))
    except Exception as exc:
        _handle_exception(ctx, ConfigError(f"Failed to initialize store: {exc}"))


# ---------------------------------------------------------------------------
# Command: scan (imported from cli.commands.scan)
# ---------------------------------------------------------------------------
main.add_command(cli_scan, name="scan")


# ---------------------------------------------------------------------------
# Command: search (imported from cli.commands.search)
# ---------------------------------------------------------------------------
main.add_command(cli_search, name="search")
main.add_command(ingest_group)
ingest_group.add_command(ingest_video)

# Explicit direct-image lifecycle is separate from Markdown scan.
main.add_command(image_group)
main.add_command(metadata_group)
main.add_command(cache_group)

main.add_command(resources_group)
main.add_command(find_similar_command)
main.add_command(similar_command, name="similar")
main.add_command(facets_command, name="facets")



# ---------------------------------------------------------------------------
# Group: read (imported from cli.commands.read)
# ---------------------------------------------------------------------------
main.add_command(read)
main.add_command(resource_group)
main.add_command(eval_group)


# ---------------------------------------------------------------------------
# Group: files (imported from cli.commands.files)
# ---------------------------------------------------------------------------
main.add_command(files_group)


# ---------------------------------------------------------------------------
# Group: model (imported from cli.commands.model)
# ---------------------------------------------------------------------------
main.add_command(model_group)


# ---------------------------------------------------------------------------
# Command: status
# ---------------------------------------------------------------------------
@main.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show index status summary."""
    cmd = _command_name(ctx)
    try:
        safe_status = _build_status_data(ctx)
        _output(ctx, envelope_success(safe_status, command=cmd))
    except Exception:
        _emit_fixed_command_error(
            ctx,
            code="STATUS_ERROR",
            message="Status could not be read",
            event="cli.status.failed",
            reason="status_unavailable",
        )


def _build_status_data(ctx: click.Context) -> dict[str, object]:
    """Build the allowlisted status payload or raise to the command boundary."""
    config = ctx.obj.get(CTX_CONFIG) if ctx.obj else None
    configured_endpoint = config.embedding.endpoint if config is not None else None
    from mdrack.application.compatibility import resolve_application_database_path

    root: Path = ctx.obj.get(CTX_ROOT, Path("."))
    db_path = resolve_application_database_path(root, config)

    if not db_path.is_file():
        return {
            "catalog_state": "missing",
            "files_count": 0,
            "chunks_count": 0,
            "embeddings_count": 0,
            "active_profile": "default",
            "profile_model": None,
            "profile_dimensions": None,
            "configured_model": config.embedding.model if config is not None else None,
            "configured_dimensions": config.embedding.dimensions if config is not None else None,
            "endpoint_configured": configured_endpoint is not None,
            "endpoint_profile_recorded": False,
            "endpoint_match": None,
            "schema_version": None,
        }

    from mdrack.adapters.sqlite.canonical_catalog import open_application_catalog_readonly
    from mdrack.diagnostics.integrity import get_resource_core_v2_status

    catalog = open_application_catalog_readonly(root, config)
    try:
        status_data = get_resource_core_v2_status(catalog.connection)
    finally:
        catalog.close()

    profile_endpoint = status_data.pop("profile_endpoint", None)
    endpoint_match = None
    if configured_endpoint is not None and profile_endpoint is not None:
        endpoint_match = configured_endpoint == profile_endpoint
    result = {
        "catalog_state": "ready",
        "files_count": status_data["files_count"],
        "chunks_count": status_data["chunks_count"],
        "embeddings_count": status_data["embeddings_count"],
        "active_profile": status_data["active_profile"],
        "profile_model": status_data["profile_model"],
        "profile_dimensions": status_data["profile_dimensions"],
        "configured_model": config.embedding.model if config is not None else None,
        "configured_dimensions": config.embedding.dimensions if config is not None else None,
        "endpoint_configured": configured_endpoint is not None,
        "endpoint_profile_recorded": profile_endpoint is not None,
        "endpoint_match": endpoint_match,
        "schema_version": status_data["schema_version"],
    }
    result.update(
        {
            "resources_count": status_data["resources_count"],
            "representations_count": status_data["representations_count"],
            "units_count": status_data["units_count"],
            "vectors_count": status_data["vectors_count"],
            "embedding_spaces_count": status_data["embedding_spaces_count"],
            "fts_rows_count": status_data["fts_rows_count"],
        }
    )
    return result


# ---------------------------------------------------------------------------
# Command: doctor
# ---------------------------------------------------------------------------
@main.command()
@click.pass_context
def doctor(ctx: click.Context) -> None:
    """Run diagnostics on the knowledge store."""
    cmd = _command_name(ctx)
    try:
        report_data = _build_doctor_data(ctx)
        _output(ctx, envelope_success(report_data, command=cmd))
    except Exception:
        _emit_fixed_command_error(
            ctx,
            code="DOCTOR_ERROR",
            message="Diagnostics could not be completed",
            event="cli.doctor.failed",
            reason="diagnostics_unavailable",
        )


def _build_doctor_data(ctx: click.Context) -> dict[str, object]:
    """Build the allowlisted doctor payload or raise to the command boundary."""
    config = ctx.obj.get(CTX_CONFIG) if ctx.obj else None

    from mdrack.adapters.sqlite.canonical_catalog import open_application_catalog_readonly
    from mdrack.application.compatibility import resolve_application_database_path
    from mdrack.diagnostics.doctor import (
        DoctorFinding,
        DoctorReport,
        report_to_dict,
        run_clean_catalog_doctor,
    )


    root: Path = ctx.obj.get(CTX_ROOT, Path("."))
    db_path = resolve_application_database_path(root, config)

    if not db_path.is_file():
        report = DoctorReport(
            findings=[
                DoctorFinding(
                    severity="error",
                    code="DATABASE_NOT_FOUND",
                    message="Knowledge store database was not found",
                    details={"reason_code": "database_missing"},
                )
            ],
            ok=False,
        )
        return report_to_dict(report)

    catalog = open_application_catalog_readonly(root, config)
    try:
        report = run_clean_catalog_doctor(catalog.connection)
    finally:
        catalog.close()

    return report_to_dict(report)


# ---------------------------------------------------------------------------
# Group: rebuild
# ---------------------------------------------------------------------------
@main.group()
@click.pass_context
def rebuild(ctx: click.Context) -> None:
    """Rebuild FTS and vector indexes."""


rebuild.add_command(rebuild_fts_cmd, name="fts")
rebuild.add_command(rebuild_embeddings_cmd, name="embeddings")


# ---------------------------------------------------------------------------
# Command: benchmark
# ---------------------------------------------------------------------------
@main.command()
@click.pass_context
def benchmark(ctx: click.Context) -> None:
    """Run a provider-free local catalog health benchmark."""
    command = "benchmark"
    started = time.perf_counter()
    catalog = None
    try:
        from mdrack.adapters.sqlite.canonical_catalog import open_application_catalog_readonly

        catalog = open_application_catalog_readonly(
            ctx.obj[CTX_ROOT],
            ctx.obj[CTX_CONFIG],
        )
        verification = catalog.verify()
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        _output(ctx, envelope_success({
            "provider_free": True,
            "operation": "catalog_verify",
            "elapsed_ms": elapsed_ms,
            "counts": {
                "resources": verification.resources,
                "representations": verification.representations,
                "units": verification.units,
                "vectors": verification.vectors,
                "facets": verification.facets,
                "fts_rows": verification.fts_rows,
            },
        }, command=command))
    except Exception:
        logger.error("cli.benchmark.failed", extra={"reason": "catalog_benchmark_failed"})
        _output(ctx, envelope_error("Benchmark could not be completed", "BENCHMARK_ERROR", command))
        ctx.exit(1)
    finally:
        if catalog is not None:
            catalog.close()


def _load_storage_analyzer_config(ctx: click.Context) -> Any:
    root = ctx.obj[CTX_ROOT]
    config_file = ctx.obj[CTX_CONFIG_FILE]
    if not isinstance(root, Path) or (config_file is not None and not isinstance(config_file, str)):
        raise ConfigError("Configuration could not be loaded")
    toml_path = Path(config_file) if config_file else None
    resolved_config_path = resolve_config_path(root=root, toml_path=toml_path)
    if toml_path is not None and not resolved_config_path.is_file():
        raise ConfigError("Configuration could not be loaded")
    return load_config(toml_path=toml_path, root=root)


@main.command(name="storage-analyze")
@click.pass_context
def storage_analyze(ctx: click.Context) -> None:
    """Report privacy-safe aggregate storage diagnostics without modifying SQLite."""
    command = "storage-analyze"
    try:
        config = _load_storage_analyzer_config(ctx)
        report = analyze_application_storage(ctx.obj[CTX_ROOT], config)
        _output(ctx, envelope_success(report.to_dict(), command=command))
    except Exception:
        logger.error(
            "cli.storage_analyze.failed",
            extra={"status": "failed", "reason": "storage_analysis_failed"},
        )
        _output(
            ctx,
            envelope_error(
                "Storage analysis could not be completed",
                "STORAGE_ANALYSIS_ERROR",
                command,
            ),
        )
        ctx.exit(1)
