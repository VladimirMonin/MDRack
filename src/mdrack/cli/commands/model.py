"""Model management commands for MDRack CLI."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import re
from inspect import isawaitable
from pathlib import Path
from typing import Any

import click

from mdrack.cli.commands.rebuild import rebuild_embeddings_in_db
from mdrack.config.loader import write_config
from mdrack.embeddings.lmstudio import LMStudioProvider
from mdrack.embeddings.protocol import EmbeddingError as ProviderEmbeddingError
from mdrack.embeddings.runtime import close_async_resource, create_lmstudio_control_client
from mdrack.indexing.indexer import run_indexer
from mdrack.output.envelope import error as envelope_error
from mdrack.output.envelope import success as envelope_success
from mdrack.output.errors import ConfigError, EmbeddingError, MDRackError
from mdrack.output.json_output import emit_json

logger = logging.getLogger(__name__)

_DOWNLOAD_DONE_STATES = {"completed", "downloaded", "finished", "ready"}
_DOWNLOAD_FAILED_STATES = {"failed", "error", "cancelled"}
_DEFAULT_PROFILE = "default"


def _model_name_matches(left: str, right: str) -> bool:
    left_norm = _normalize_model_name(left)
    right_norm = _normalize_model_name(right)
    return left_norm in right_norm or right_norm in left_norm


def create_model_control_client(ctx: click.Context) -> object:
    """Create the LM Studio model control client for CLI commands."""
    config = ctx.obj.get("config") if ctx.obj else None
    if config is None:
        raise ConfigError("Configuration not available")
    return create_lmstudio_control_client(config)


def _output(ctx: click.Context, payload: dict[str, Any]) -> None:
    json_flag: bool = ctx.obj.get("json_output", True) if ctx.obj else True
    emit_json(payload, pretty=not json_flag)


def _command_name(ctx: click.Context) -> str:
    parts: list[str] = []
    current: click.Context | None = ctx
    while current is not None:
        if current.info_name and current.info_name not in {"mdrack", "main"}:
            parts.append(current.info_name)
        current = current.parent
    return " ".join(reversed(parts)) or "mdrack"


def _to_jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _to_jsonable(item) for key, item in value.items()}
    return value


def _handle_error(ctx: click.Context, cmd: str, exc: Exception) -> None:
    if isinstance(exc, ProviderEmbeddingError):
        exc = EmbeddingError(str(exc))

    if isinstance(exc, MDRackError):
        logger.warning(
            "cli.command.failed command=%s code=%s reason=handled_error",
            cmd,
            exc.code,
        )
        _output(
            ctx,
            envelope_error(
                message=str(exc),
                code=exc.code,
                command=cmd,
                details=exc.details,
            ),
        )
        ctx.exit(1)
        return

    logger.exception(
        "cli.command.failed command=%s code=INTERNAL_ERROR reason=unexpected_exception",
        cmd,
    )
    _output(
        ctx,
        envelope_error(
            message=str(exc),
            code="INTERNAL_ERROR",
            command=cmd,
        ),
    )
    ctx.exit(1)


def _invoke_client_method(client: object, method_name: str, *args: Any) -> Any:
    alias_name = "loaded_models" if method_name == "list_loaded_models" else method_name
    method = getattr(client, method_name, None) or getattr(client, alias_name)
    result = method(*args)
    if isawaitable(result):
        return asyncio.run(result)
    return result


def _run_model_command(
    ctx: click.Context,
    *,
    method_name: str,
    collection_key: str,
    method_args: tuple[Any, ...] = (),
    empty_result: dict[str, Any] | None = None,
) -> None:
    cmd = _command_name(ctx)
    logger.info("cli.command.started command=%s", cmd)
    client: object | None = None
    try:
        client = create_model_control_client(ctx)
        result = _invoke_client_method(client, method_name, *method_args)
        if result is None:
            data = empty_result or {}
        elif isinstance(result, dict):
            data = _to_jsonable(result)
        else:
            data = {collection_key: _to_jsonable(result)}
    except Exception as exc:
        _handle_error(ctx, cmd, exc)
        return
    finally:
        try:
            asyncio.run(close_async_resource(client))
        except Exception:
            logger.debug("Failed to close model control client", exc_info=True)

    logger.info("cli.command.finished command=%s status=success", cmd)
    _output(ctx, envelope_success(data, command=cmd))


def _resolve_requested_model_name_from_models(models: list[Any], model_name: str) -> str:
    return _resolve_model_key(models, model_name) or model_name


def _resolve_requested_model_name(client: object, model_name: str) -> str:
    try:
        models = _invoke_client_method(client, "list_models")
    except Exception:
        logger.debug("Model alias resolution skipped after list_models failure", exc_info=True)
        return model_name
    return _resolve_requested_model_name_from_models(models, model_name)


def _build_switched_config(config: Any, model_name: str, dimensions: int) -> Any:
    embedding_config = config.embedding.model_copy(
        update={
            "provider": "lmstudio",
            "model": model_name,
            "dimensions": dimensions,
        }
    )
    return config.model_copy(update={"embedding": embedding_config})


def _find_model(models: list[Any], model_name: str) -> Any | None:
    resolved_key = _resolve_model_key(models, model_name)
    if resolved_key is None:
        return None
    for item in models:
        key = getattr(item, "key", None)
        if key == resolved_key:
            return item
    return None


def _model_is_loaded(model: Any | None) -> bool:
    if model is None:
        return False
    if bool(getattr(model, "loaded", False)):
        return True
    return bool(getattr(model, "instance_ids", ()) or ())


def _loaded_result_for_model(model: Any) -> dict[str, Any]:
    instance_ids = tuple(getattr(model, "instance_ids", ()) or ())
    return {
        "key": getattr(model, "key", None),
        "state": "already_loaded",
        "instance_id": instance_ids[0] if instance_ids else None,
    }


def _loaded_result_for_loaded_instance(item: Any) -> dict[str, Any]:
    return {
        "key": getattr(item, "key", None),
        "state": "already_loaded",
        "instance_id": getattr(item, "instance_id", None),
    }


def _normalize_model_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _resolve_model_key(models: list[Any], requested_name: str) -> str | None:
    requested_norm = _normalize_model_name(requested_name)
    substring_matches: list[str] = []

    for item in models:
        candidates = [
            getattr(item, "key", None),
            getattr(item, "display_name", None),
            getattr(item, "selected_variant", None),
        ]
        candidates.extend(getattr(item, "variants", ()) or ())
        for candidate in candidates:
            if not isinstance(candidate, str) or not candidate:
                continue
            if candidate.lower() == requested_name.lower():
                return getattr(item, "key", candidate)
            candidate_norm = _normalize_model_name(candidate)
            if candidate_norm == requested_norm:
                return getattr(item, "key", candidate)
            if candidate_norm in requested_norm or requested_norm in candidate_norm:
                substring_matches.append(getattr(item, "key", candidate))
                break

    unique_matches = list(dict.fromkeys(substring_matches))
    if len(unique_matches) == 1:
        return unique_matches[0]
    if len(unique_matches) > 1:
        raise EmbeddingError(
            f"Model name '{requested_name}' is ambiguous in LM Studio: {', '.join(unique_matches[:5])}"
        )
    return None


async def _wait_for_model_download(client: object, model_name: str) -> list[dict[str, Any]]:
    for _ in range(1800):
        result = client.get_download_status()
        downloads = await result if isawaitable(result) else result
        download_items = [_to_jsonable(item) for item in downloads]
        for item in downloads:
            key = getattr(item, "key", None)
            status = (getattr(item, "status", None) or "").lower()
            if not isinstance(key, str):
                continue
            if not _model_name_matches(key, model_name):
                continue
            if status in _DOWNLOAD_DONE_STATES:
                return download_items
            if status in _DOWNLOAD_FAILED_STATES:
                error_message = getattr(item, "error", None) or "download_failed"
                raise EmbeddingError(
                    f"LM Studio download failed for '{model_name}': {error_message}"
                )
        await asyncio.sleep(1.0)
    raise EmbeddingError(f"Timed out waiting for LM Studio to download '{model_name}'")


async def _wait_for_model_visibility(client: object, model_name: str) -> list[Any]:
    for _ in range(1800):
        list_result = client.list_models()
        models = await list_result if isawaitable(list_result) else list_result
        if _resolve_model_key(models, model_name) is not None:
            return models

        status_result = client.get_download_status()
        downloads = await status_result if isawaitable(status_result) else status_result
        for item in downloads:
            key = getattr(item, "key", None)
            if not isinstance(key, str):
                continue
            status = (getattr(item, "status", None) or "").lower()
            if not _model_name_matches(key, model_name):
                continue
            if status in _DOWNLOAD_FAILED_STATES:
                error_message = getattr(item, "error", None) or "download_failed"
                raise EmbeddingError(
                    f"LM Studio download failed for '{model_name}': {error_message}"
                )

        await asyncio.sleep(1.0)

    raise EmbeddingError(f"Timed out waiting for LM Studio to expose '{model_name}'")


def _already_loaded_result(
    client: object,
    resolved_model_name: str,
    existing_model: Any | None,
) -> dict[str, Any] | None:
    if _model_is_loaded(existing_model):
        return _loaded_result_for_model(existing_model)

    try:
        loaded_models = _invoke_client_method(client, "list_loaded_models")
    except Exception:
        logger.debug("Loaded-model fallback check failed", exc_info=True)
        return None

    for item in loaded_models:
        if getattr(item, "key", None) == resolved_model_name:
            return _loaded_result_for_loaded_instance(item)
    return None


def _loaded_instance_ids_for_model(client: object, model: Any | None) -> tuple[str, ...]:
    if model is None:
        return ()

    instance_ids = tuple(getattr(model, "instance_ids", ()) or ())
    if instance_ids:
        return instance_ids

    model_key = getattr(model, "key", None)
    if not isinstance(model_key, str) or not model_key:
        return ()

    try:
        loaded_models = _invoke_client_method(client, "list_loaded_models")
    except Exception:
        logger.debug("Loaded-model instance lookup failed", exc_info=True)
        return ()

    return tuple(
        instance_id
        for item in loaded_models
        for instance_id in (getattr(item, "instance_id", None),)
        if getattr(item, "key", None) == model_key and isinstance(instance_id, str) and instance_id
    )


def _unload_previous_model(
    client: object,
    *,
    previous_model_name: str,
    new_model_name: str,
    instance_ids: tuple[str, ...],
) -> dict[str, Any]:
    if previous_model_name == new_model_name:
        return {
            "attempted": False,
            "model": previous_model_name,
            "reason": "same_model",
        }

    if not instance_ids:
        return {
            "attempted": False,
            "model": previous_model_name,
            "reason": "previous_model_not_loaded",
        }

    unloaded: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for instance_id in instance_ids:
        try:
            result = _invoke_client_method(client, "unload_model", instance_id)
        except Exception as exc:
            logger.warning(
                "cli.model.switch.unload_previous.failed previous_model=%s new_model=%s instance_id=%s error_type=%s",
                previous_model_name,
                new_model_name,
                instance_id,
                type(exc).__name__,
            )
            errors.append(
                {
                    "instance_id": instance_id,
                    "message": str(exc),
                }
            )
            continue

        payload = _to_jsonable(result)
        if not isinstance(payload, dict):
            payload = {}
        payload.setdefault("instance_id", instance_id)
        payload.setdefault("status", "unloaded")
        unloaded.append(payload)

    status = "unloaded"
    if errors and unloaded:
        status = "partial_failure"
    elif errors:
        status = "failed"

    data: dict[str, Any] = {
        "attempted": True,
        "model": previous_model_name,
        "status": status,
        "results": unloaded,
    }
    if errors:
        data["errors"] = errors
    return data


def _run_switch_rebuild(
    *,
    ctx: click.Context,
    config: Any,
    switched_config: Any,
    model_name: str,
    dimensions: int,
    rebuild_mode: str,
) -> dict[str, Any]:
    root: Path = ctx.obj.get("root", Path(".")) if ctx.obj else Path(".")
    db_path: Path = ctx.obj.get("db_path", Path(".mdrack") / "knowledge.db") if ctx.obj else Path(
        ".mdrack"
    ) / "knowledge.db"

    if rebuild_mode == "none":
        return {
            "performed": False,
            "mode": rebuild_mode,
            "reason": "skipped_by_request",
        }

    if not db_path.exists():
        return {
            "performed": False,
            "mode": rebuild_mode,
            "reason": "database_missing",
        }

    provider = LMStudioProvider(
        endpoint=config.embedding.endpoint,
        model=model_name,
        dimensions=dimensions,
        timeout=config.embedding.timeout_secs,
    )
    try:
        if rebuild_mode == "full":
            result = run_indexer(
                root=root,
                config=switched_config,
                provider=provider,
                profile=_DEFAULT_PROFILE,
                force_reindex=True,
            )
            return {
                "performed": True,
                "mode": rebuild_mode,
                "files_seen": result.files_seen,
                "files_changed": result.files_changed,
                "files_deleted": result.files_deleted,
                "chunks_created": result.chunks_created,
                "errors_count": result.errors_count,
                "run_id": result.run_id,
            }

        data = rebuild_embeddings_in_db(db_path, provider, _DEFAULT_PROFILE)
        data.update({"performed": True, "mode": rebuild_mode})
        return data
    finally:
        try:
            asyncio.run(close_async_resource(provider))
        except Exception:
            logger.debug("Failed to close target embedding provider", exc_info=True)


@click.group()
@click.pass_context
def model(ctx: click.Context) -> None:
    """Download, load, unload, and switch LM Studio embedding models."""


@model.command("list")
@click.pass_context
def model_list(ctx: click.Context) -> None:
    """List LM Studio models visible through the configured endpoint."""
    _run_model_command(ctx, method_name="list_models", collection_key="models")


@model.command("loaded")
@click.pass_context
def model_loaded(ctx: click.Context) -> None:
    """Show loaded LM Studio model instances and their instance ids."""
    _run_model_command(ctx, method_name="list_loaded_models", collection_key="models")


@model.command("download")
@click.argument("model_name", metavar="MODEL")
@click.pass_context
def model_download(ctx: click.Context, model_name: str) -> None:
    """Request model download through LM Studio."""
    cmd = _command_name(ctx)
    logger.info("cli.command.started command=%s", cmd)
    client: object | None = None
    try:
        client = create_model_control_client(ctx)
        resolved_model_name = _resolve_requested_model_name(client, model_name)
        result = _invoke_client_method(client, "download_model", resolved_model_name)
        data = _to_jsonable(result) if isinstance(result, dict) else _to_jsonable(result)
        if not isinstance(data, dict):
            data = {"download": data}
    except Exception as exc:
        _handle_error(ctx, cmd, exc)
        return
    finally:
        try:
            asyncio.run(close_async_resource(client))
        except Exception:
            logger.debug("Failed to close model control client", exc_info=True)

    logger.info("cli.command.finished command=%s status=success", cmd)
    _output(ctx, envelope_success(data, command=cmd))


@model.command("download-status")
@click.pass_context
def model_download_status(ctx: click.Context) -> None:
    """Show active or recent LM Studio download tasks."""
    _run_model_command(
        ctx,
        method_name="get_download_status",
        collection_key="downloads",
    )


@model.command("load")
@click.argument("model_name", metavar="MODEL")
@click.pass_context
def model_load(ctx: click.Context, model_name: str) -> None:
    """Load a model into LM Studio unless it is already loaded."""
    cmd = _command_name(ctx)
    logger.info("cli.command.started command=%s", cmd)
    client: object | None = None
    try:
        client = create_model_control_client(ctx)
        models = _invoke_client_method(client, "list_models")
        resolved_model_name = _resolve_requested_model_name_from_models(models, model_name)
        existing_model = _find_model(models, resolved_model_name)
        loaded_result = _already_loaded_result(client, resolved_model_name, existing_model)
        if loaded_result is not None:
            data = loaded_result
        else:
            result = _invoke_client_method(client, "load_model", resolved_model_name)
            data = _to_jsonable(result) if isinstance(result, dict) else _to_jsonable(result)
        if not isinstance(data, dict):
            data = {"load": data}
    except Exception as exc:
        _handle_error(ctx, cmd, exc)
        return
    finally:
        try:
            asyncio.run(close_async_resource(client))
        except Exception:
            logger.debug("Failed to close model control client", exc_info=True)

    logger.info("cli.command.finished command=%s status=success", cmd)
    _output(ctx, envelope_success(data, command=cmd))


@model.command("unload")
@click.argument("instance_id", metavar="INSTANCE_ID")
@click.pass_context
def model_unload(ctx: click.Context, instance_id: str) -> None:
    """Unload a running LM Studio model instance from `model loaded`."""
    _run_model_command(
        ctx,
        method_name="unload_model",
        collection_key="unload",
        method_args=(instance_id,),
        empty_result={"instance_id": instance_id, "status": "unloaded"},
    )


@model.command("switch")
@click.argument("model_name", metavar="MODEL")
@click.option("--download", is_flag=True, default=False, help="Download the model first if missing.")
@click.option("--load/--no-load", default=True, help="Load the model before validation.")
@click.option(
    "--dimensions",
    type=int,
    default=None,
    help="Override the detected embedding dimension for the target model.",
)
@click.option(
    "--rebuild",
    "rebuild_mode",
    type=click.Choice(["embeddings", "full", "none"]),
    default="embeddings",
    show_default=True,
    help="How to rebuild the knowledge store after switching models.",
)
@click.option(
    "--yes",
    is_flag=True,
    default=False,
    help="Confirm dangerous switch modes such as '--rebuild none'.",
)
@click.pass_context
def model_switch(
    ctx: click.Context,
    model_name: str,
    download: bool,
    load: bool,
    dimensions: int | None,
    rebuild_mode: str,
    yes: bool,
) -> None:
    """Switch the active embedding model, rebuild vectors, and unload the previous model."""
    cmd = _command_name(ctx)
    config = ctx.obj.get("config") if ctx.obj else None
    config_path = ctx.obj.get("config_path") if ctx.obj else None

    if config is None or config_path is None:
        _handle_error(ctx, cmd, ConfigError("Configuration not available"))
        return

    if rebuild_mode == "none" and not yes:
        _handle_error(
            ctx,
            cmd,
            ConfigError("'--rebuild none' requires '--yes' because it can leave stale vectors"),
        )
        return

    logger.info("cli.command.started command=%s", cmd)
    client: object | None = None
    try:
        client = create_model_control_client(ctx)
        models = _invoke_client_method(client, "list_models")
        resolved_model_name = _resolve_model_key(models, model_name) or model_name
        existing_model = _find_model(models, model_name)
        previous_model = _find_model(models, config.embedding.model)
        previous_model_name = (
            getattr(previous_model, "key", None)
            or _resolve_model_key(models, config.embedding.model)
            or config.embedding.model
        )
        previous_instance_ids = _loaded_instance_ids_for_model(client, previous_model)
        download_info: list[dict[str, Any]] = []

        if existing_model is None:
            if not download:
                raise EmbeddingError(
                    f"Model '{model_name}' is not visible in LM Studio. Re-run with '--download' to fetch it."
                )
            _invoke_client_method(client, "download_model", model_name)
            download_info = asyncio.run(_wait_for_model_download(client, model_name))
            models = asyncio.run(_wait_for_model_visibility(client, model_name))
            resolved_model_name = _resolve_requested_model_name_from_models(models, model_name)
            existing_model = _find_model(models, resolved_model_name)
        elif download:
            _invoke_client_method(client, "download_model", resolved_model_name)
            download_info = asyncio.run(_wait_for_model_download(client, resolved_model_name))
            models = asyncio.run(_wait_for_model_visibility(client, resolved_model_name))
            resolved_model_name = _resolve_requested_model_name_from_models(models, model_name)
            existing_model = _find_model(models, resolved_model_name)

        load_result: Any = None
        if load:
            load_result = _already_loaded_result(client, resolved_model_name, existing_model)
            if load_result is None:
                load_result = _invoke_client_method(client, "load_model", resolved_model_name)

        if (
            not load
            and dimensions is None
            and _already_loaded_result(client, resolved_model_name, existing_model) is None
        ):
            raise ConfigError(
                "'--no-load' requires '--dimensions' when the target model is not already loaded"
            )

        detected_dimensions = dimensions
        if detected_dimensions is None:
            detected_dimensions = _invoke_client_method(
                client,
                "probe_embedding_dimensions",
                resolved_model_name,
            )

        if not isinstance(detected_dimensions, int) or detected_dimensions <= 0:
            raise EmbeddingError(f"Invalid embedding dimension detected for '{model_name}'")

        switched_config = _build_switched_config(config, resolved_model_name, detected_dimensions)
        rebuild_data = _run_switch_rebuild(
            ctx=ctx,
            config=config,
            switched_config=switched_config,
            model_name=resolved_model_name,
            dimensions=detected_dimensions,
            rebuild_mode=rebuild_mode,
        )

        write_config(switched_config, Path(config_path))
        if ctx.obj is not None:
            ctx.obj["config"] = switched_config

        unload_previous = _unload_previous_model(
            client,
            previous_model_name=previous_model_name,
            new_model_name=resolved_model_name,
            instance_ids=previous_instance_ids,
        )

        data = {
            "old_model": config.embedding.model,
            "requested_model": model_name,
            "new_model": resolved_model_name,
            "old_dimensions": config.embedding.dimensions,
            "new_dimensions": detected_dimensions,
            "config_path": str(config_path),
            "rebuild": rebuild_data,
            "download": download_info,
            "load": _to_jsonable(load_result) if load_result is not None else None,
            "unload_previous": unload_previous,
        }
    except Exception as exc:
        _handle_error(ctx, cmd, exc)
        return
    finally:
        try:
            asyncio.run(close_async_resource(client))
        except Exception:
            logger.debug("Failed to close model control client", exc_info=True)

    logger.info("cli.command.finished command=%s status=success", cmd)
    _output(ctx, envelope_success(data, command=cmd))
