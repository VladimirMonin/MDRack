"""Registered caller-selected mixed-folder action."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import click

from mdrack.adapters.sqlite.canonical_catalog import application_store_dir
from mdrack.application.folder_import import FolderImportError, FolderImportResult, FolderImportService
from mdrack.cli.commands.images import _close_native, _native_engine, _output
from mdrack.domain.profiles import IncompatibleEmbeddingProfileError
from mdrack.output.envelope import error as envelope_error
from mdrack.output.envelope import success as envelope_success

logger = logging.getLogger(__name__)

_COMMAND = "folder add"


def _parse_folders(values: tuple[str, ...]) -> tuple[tuple[str, Path], ...]:
    selections: list[tuple[str, Path]] = []
    for value in values:
        namespace, separator, raw_path = value.partition("=")
        if not separator or not namespace or not raw_path:
            raise FolderImportError("folder_selection_invalid")
        selections.append((namespace, Path(raw_path)))
    return tuple(selections)


@click.group(name="folder")
def folder() -> None:
    """Add an explicitly selected mixed source folder."""


@folder.command(name="add")
@click.argument("folders", nargs=-1, required=True, metavar="NAMESPACE=PATH")
@click.option(
    "--exclude",
    "exclude",
    multiple=True,
    help="Additional relative glob exclusion; configured exclusions always apply.",
)
@click.option(
    "--provider",
    "provider_name",
    type=click.Choice(["external", "synthetic"]),
    default="external",
    show_default=True,
    help="Prepared document/image provider. Synthetic is offline contract testing only.",
)
@click.option(
    "--host-config",
    type=click.Path(path_type=Path),
    default=None,
    help="Trusted caller-owned CUDA encoder config; requires --provider external.",
)
@click.pass_context
def add(
    ctx: click.Context,
    folders: tuple[str, ...],
    exclude: tuple[str, ...],
    provider_name: str,
    host_config: Path | None,
) -> None:
    """Add one or more explicit NAMESPACE=PATH folders in one registered action.

    Markdown and UTF-8 TXT enter the document branch; ordinary JPEG/PNG/WEBP
    enter the existing visual branch. No per-file resource ID, caption or
    service JSON is required. PATH is used as given; relative paths resolve
    from the current working directory. The source bytes are never modified.
    """

    async def execute() -> FolderImportResult:
        selections = _parse_folders(folders)
        config = ctx.obj["config"]
        root = ctx.obj["root"]
        cache_value = Path(config.cache.directory)
        cache_dir = cache_value if cache_value.is_absolute() else root / cache_value
        # Root/derived-state admission precedes any provider construction.
        FolderImportService.validate_selection(
            selections,
            store_dir=application_store_dir(root, config),
            cache_dir=cache_dir,
        )
        engine, provider = (
            _native_engine(ctx, provider_name)
            if host_config is None
            else _native_engine(ctx, provider_name, host_config)
        )
        try:
            return await engine.import_folders(selections, exclude=exclude)
        finally:
            await _close_native(engine, provider)

    try:
        result = asyncio.run(execute())
    except IncompatibleEmbeddingProfileError:
        logger.error("folder.add.failed reason=incompatible_embedding_profile")
        _output(ctx, envelope_error(
            "Embedding profile is incompatible; use a new collection",
            "EMBEDDING_ERROR",
            _COMMAND,
            details={"reason_code": "incompatible_embedding_profile"},
        ))
        ctx.exit(1)
        return
    except FolderImportError as exc:
        logger.error("folder.add.failed reason=%s", exc.reason)
        _output(ctx, envelope_error(
            "Folder import failed",
            "FOLDER_IMPORT_ERROR",
            _COMMAND,
            details={"reason_code": exc.reason},
        ))
        ctx.exit(1)
        return
    except ValueError as exc:
        reason = exc.args[0] if len(exc.args) == 1 and isinstance(exc.args[0], str) else "operation_failed"
        if reason == "unsupported_embedding_route":
            code = "UNSUPPORTED_EMBEDDING_ROUTE"
        else:
            code = "FOLDER_IMPORT_ERROR"
        logger.error("folder.add.failed reason=%s", reason)
        _output(ctx, envelope_error(
            "Folder import failed",
            code,
            _COMMAND,
            details={"reason_code": reason},
        ))
        ctx.exit(1)
        return
    except Exception:
        logger.error("folder.add.failed reason=operation_failed")
        _output(ctx, envelope_error(
            "Folder import failed",
            "FOLDER_IMPORT_ERROR",
            _COMMAND,
            details={"reason_code": "operation_failed"},
        ))
        ctx.exit(1)
        return

    data = result.to_dict()
    _output(ctx, envelope_success(data, command=_COMMAND))
    if data["status"] in {"partial_success", "failed"}:
        ctx.exit(1)


__all__ = ["folder"]
