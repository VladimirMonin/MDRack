"""CLI entrypoint for MDRack."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import click

from mdrack import __version__
from mdrack.cli.commands.files import files as files_group
from mdrack.cli.commands.read import read
from mdrack.cli.commands.rebuild import rebuild_embeddings_cmd, rebuild_fts_cmd
from mdrack.cli.commands.scan import cli_scan
from mdrack.cli.commands.search import cli_search
from mdrack.cli.commands.sections import sections as sections_group
from mdrack.config.loader import load_config
from mdrack.output.envelope import error as envelope_error
from mdrack.output.envelope import success as envelope_success
from mdrack.output.errors import ConfigError, MDRackError

logger = logging.getLogger(__name__)

# Click context object keys
CTX_CONFIG = "config"
CTX_ROOT = "root"
CTX_JSON = "json_output"


def _output(ctx: click.Context, payload: dict[str, Any]) -> None:
    """Print a JSON envelope to stdout (or the Click echo target).

    Respects the --json flag: when False, pretty-prints with indent=2.
    """
    json_flag: bool = ctx.obj.get(CTX_JSON, True) if ctx.obj else True
    if json_flag:
        click.echo(json.dumps(payload, ensure_ascii=False))
    else:
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))


def _command_name(ctx: click.Context) -> str:
    """Derive the command name string from the Click context."""
    parts: list[str] = []
    current: click.Context | None = ctx
    while current is not None:
        if current.info_name and current.info_name != "mdrack":
            parts.append(current.info_name)
        current = current.parent
    return " ".join(reversed(parts)) or "mdrack"


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
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Path to TOML config file.",
)
@click.pass_context
def main(ctx: click.Context, root: str, json_output: bool, config_file: str | None) -> None:
    """MDRack — Local command-line Markdown knowledge rack for AI agents."""
    ctx.ensure_object(dict)
    ctx.obj[CTX_ROOT] = Path(root).resolve()
    ctx.obj[CTX_JSON] = json_output

    # Load configuration
    try:
        toml_path = Path(config_file) if config_file else None
        ctx.obj[CTX_CONFIG] = load_config(toml_path=toml_path)
    except Exception as exc:
        _handle_exception(ctx, ConfigError(f"Failed to load config: {exc}"))
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
    _output(ctx, envelope_success({"status": "not yet implemented"}, command=cmd))


# ---------------------------------------------------------------------------
# Command: scan (imported from cli.commands.scan)
# ---------------------------------------------------------------------------
main.add_command(cli_scan, name="scan")


# ---------------------------------------------------------------------------
# Command: search (imported from cli.commands.search)
# ---------------------------------------------------------------------------
main.add_command(cli_search, name="search")


# ---------------------------------------------------------------------------
# Group: read (imported from cli.commands.read)
# ---------------------------------------------------------------------------
main.add_command(read)


# ---------------------------------------------------------------------------
# Group: files (imported from cli.commands.files)
# ---------------------------------------------------------------------------
main.add_command(files_group)


# ---------------------------------------------------------------------------
# Group: sections (imported from cli.commands.sections)
# ---------------------------------------------------------------------------
main.add_command(sections_group)


# ---------------------------------------------------------------------------
# Command: status
# ---------------------------------------------------------------------------
@main.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show index status summary."""
    cmd = _command_name(ctx)
    cfg = ctx.obj.get(CTX_CONFIG)
    root: Path = ctx.obj.get(CTX_ROOT, Path("."))

    store_dir = root / cfg.paths.store
    db_path = store_dir / "knowledge.db"

    if not db_path.is_file():
        payload = envelope_success(
            {
                "files_count": 0,
                "chunks_count": 0,
                "embeddings_count": 0,
                "active_profile": None,
                "schema_version": None,
            },
            command=cmd,
        )
        _output(ctx, payload)
        return

    from mdrack.diagnostics.integrity import get_store_status
    from mdrack.storage.sqlite.connection import get_connection

    conn = get_connection(db_path)
    try:
        status_data = get_store_status(conn)
    finally:
        conn.close()

    _output(ctx, envelope_success(status_data, command=cmd))


# ---------------------------------------------------------------------------
# Command: doctor
# ---------------------------------------------------------------------------
@main.command()
@click.pass_context
def doctor(ctx: click.Context) -> None:
    """Run diagnostics on the knowledge store."""
    cmd = _command_name(ctx)
    _output(ctx, envelope_success({"status": "not yet implemented"}, command=cmd))


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
# Command: eval
# ---------------------------------------------------------------------------
@main.command()
@click.pass_context
def eval_cmd(ctx: click.Context) -> None:
    """Run retrieval evaluation queries."""
    cmd = _command_name(ctx)
    _output(ctx, envelope_success({"status": "not yet implemented"}, command=cmd))
