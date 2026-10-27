"""Explicit native temporal CLI. Only outer synchronous Click boundaries run loops."""

from __future__ import annotations

import asyncio
from pathlib import Path

import click

from mdrack.application.media_search import MediaSearchRequest, StoredUnitInput
from mdrack.cli.commands.images import _close_native, _native_engine, _native_error_code, _output
from mdrack.domain.embedding_inputs import TextInput
from mdrack.domain.native_media import NativeMediaKind, NativeMediaPolicy
from mdrack.domain.profiles import IncompatibleEmbeddingProfileError
from mdrack.embeddings.synthetic import SyntheticTemporalProvider
from mdrack.output.envelope import error, success
from mdrack.public_api.engine import MDRackEngine
from mdrack_core import SearchScope
from mdrack_media import resource_id


def _kind(value: str) -> NativeMediaKind:
    if value == "frames":
        return "frames"
    if value == "audio":
        return "audio"
    if value == "video":
        return "video"
    raise ValueError("native_kind_invalid")


def _engine(ctx: click.Context, provider_name: str) -> tuple[MDRackEngine, object | None]:
    if provider_name == "synthetic":
        return MDRackEngine(
            root=ctx.obj["root"], config=ctx.obj["config"], prepared_embedding_provider=SyntheticTemporalProvider()
        ), None
    return _native_engine(ctx, provider_name)


@click.group(name="native")
def native() -> None:
    """Native frames, audio, and direct-video are separate typed routes.

    Synthetic is explicit deterministic byte-hash component evidence, not inference.
    The selected production LM Studio embedding contract remains text-only.
    """


@native.command(name="ingest")
@click.argument("path", type=click.Path(path_type=Path))
@click.option("--kind", required=True, type=click.Choice(["frames", "audio", "video"]))
@click.option("--source-namespace", required=True)
@click.option("--source-ref", required=True)
@click.option("--resource-id", "rid", default=None)
@click.option("--start-ms", type=click.IntRange(min=0), default=0)
@click.option("--end-ms", type=click.IntRange(min=1), default=None)
@click.option("--window-ms", type=click.IntRange(min=1), default=1000)
@click.option("--hop-ms", type=click.IntRange(min=1), default=1000)
@click.option("--frame-step-ms", type=click.IntRange(min=1), default=1000)
@click.option("--max-units", type=click.IntRange(1, 10000), default=128)
@click.option("--max-prepared-bytes", type=click.IntRange(min=1), default=16_777_216)
@click.option("--expected-revision", default=None)
@click.option("--provider", "provider_name", type=click.Choice(["lmstudio", "synthetic"]), default="lmstudio")
@click.pass_context
def ingest(
    ctx: click.Context,
    path: Path,
    kind: str,
    source_namespace: str,
    source_ref: str,
    rid: str | None,
    start_ms: int,
    end_ms: int | None,
    window_ms: int,
    hop_ms: int,
    frame_step_ms: int,
    max_units: int,
    max_prepared_bytes: int,
    expected_revision: str | None,
    provider_name: str,
) -> None:
    async def execute() -> dict[str, object]:
        policy = NativeMediaPolicy(
            start_ms=start_ms,
            end_ms=end_ms,
            window_ms=window_ms,
            hop_ms=hop_ms,
            frame_step_ms=frame_step_ms,
            max_units=max_units,
            max_prepared_bytes=max_prepared_bytes,
        )
        engine, provider = _engine(ctx, provider_name)
        try:
            result = await engine.ingest_native_media(
                path,
                kind=_kind(kind),
                resource_id=rid or resource_id(source_namespace, source_ref),
                source_namespace=source_namespace,
                source_ref=source_ref,
                policy=policy,
                expected_revision=expected_revision,
            )
            return result.to_dict()
        finally:
            await _close_native(engine, provider)

    try:
        result = asyncio.run(execute())
    except Exception as exc:
        _output(ctx, error("Native preparation failed", _native_error_code(exc, "NATIVE_MEDIA_ERROR"), "native ingest"))
        ctx.exit(1)
        return
    _output(ctx, success(result, command="native ingest"))


@native.command(name="search")
@click.argument("path", type=click.Path(path_type=Path))
@click.option("--kind", required=True, type=click.Choice(["frames", "audio", "video"]))
@click.option("--start-ms", type=click.IntRange(min=0), default=0)
@click.option("--end-ms", required=True, type=click.IntRange(min=1))
@click.option("--candidates", type=click.Choice(["all", "text", "image", "audio", "video"]), default="all")
@click.option("--target", type=click.Choice(["resource", "unit"]), default="resource")
@click.option("--limit", type=click.IntRange(1, 10000), default=20)
@click.option("--candidate-budget", type=click.IntRange(1, 10000), default=100)
@click.option("--provider", "provider_name", type=click.Choice(["lmstudio", "synthetic"]), default="lmstudio")
@click.pass_context
def search(
    ctx: click.Context,
    path: Path,
    kind: str,
    start_ms: int,
    end_ms: int,
    candidates: str,
    target: str,
    limit: int,
    candidate_budget: int,
    provider_name: str,
) -> None:
    async def execute() -> dict[str, object]:
        engine, provider = _engine(ctx, provider_name)
        try:
            policy = NativeMediaPolicy(
                start_ms=start_ms,
                end_ms=end_ms,
                window_ms=end_ms - start_ms,
                hop_ms=end_ms - start_ms,
                frame_step_ms=end_ms - start_ms,
                max_units=1,
            )
            result = await engine.search_native_media(
                path,
                kind=_kind(kind),
                policy=policy,
                scope=SearchScope(modalities=() if candidates == "all" else (candidates,)),
                target=target,
                limit=limit,
                candidate_budget=candidate_budget,
            )
            return result.to_dict()
        finally:
            await _close_native(engine, provider)

    try:
        result = asyncio.run(execute())
    except Exception as exc:
        _output(ctx, error("Native query failed", _native_error_code(exc, "NATIVE_MEDIA_ERROR"), "native search"))
        ctx.exit(1)
        return
    _output(ctx, success(result, command="native search"))


@native.command(name="query")
@click.option("--text", default=None)
@click.option("--unit-id", default=None)
@click.option("--candidates", type=click.Choice(["all", "text", "image", "audio", "video"]), default="all")
@click.option("--target", type=click.Choice(["resource", "unit"]), default="resource")
@click.option("--limit", type=click.IntRange(1, 10000), default=20)
@click.option("--candidate-budget", type=click.IntRange(1, 10000), default=100)
@click.option("--provider", "provider_name", type=click.Choice(["lmstudio", "synthetic"]), default="lmstudio")
@click.pass_context
def query(
    ctx: click.Context,
    text: str | None,
    unit_id: str | None,
    candidates: str,
    target: str,
    limit: int,
    candidate_budget: int,
    provider_name: str,
) -> None:
    async def execute() -> dict[str, object]:
        if (text is None) == (unit_id is None):
            raise ValueError("one_query_required")
        engine, provider = _engine(ctx, provider_name)
        try:
            value = StoredUnitInput(unit_id) if unit_id is not None else TextInput(text or "", "query")
            result = await engine.search_media(
                MediaSearchRequest(
                    value,
                    SearchScope(modalities=() if candidates == "all" else (candidates,)),
                    target,
                    "semantic",
                    False,
                    limit,
                    candidate_budget,
                )
            )
            return result.to_dict()
        finally:
            await _close_native(engine, provider)

    try:
        result = asyncio.run(execute())
    except Exception as exc:
        _output(ctx, error("Native query failed", _native_error_code(exc, "NATIVE_MEDIA_ERROR"), "native query"))
        ctx.exit(1)
        return
    _output(ctx, success(result, command="native query"))


@native.command(name="text-ingest")
@click.argument("path", type=click.Path(path_type=Path))
@click.option("--source-namespace", required=True)
@click.option("--source-ref", required=True)
@click.option("--resource-id", default=None, help="Optional stable logical ID; derived if omitted.")
@click.option("--expected-revision", default=None)
@click.option("--provider", "provider_name", type=click.Choice(["external", "synthetic"]), default="external")
@click.option("--host-config", type=click.Path(path_type=Path), default=None,
              help="Trusted caller-owned CUDA encoder config; requires --provider external.")
@click.pass_context
def text_ingest(
    ctx: click.Context,
    path: Path,
    source_namespace: str,
    source_ref: str,
    resource_id: str | None,
    expected_revision: str | None,
    provider_name: str,
    host_config: Path | None,
) -> None:
    """Encode one ordinary UTF-8 Markdown document through the selected native host."""
    async def execute() -> dict[str, object]:
        engine, provider = (_native_engine(ctx, provider_name) if host_config is None
                            else _native_engine(ctx, provider_name, host_config))
        try:
            result = await engine.ingest_native_text(
                path,
                resource_id=resource_id,
                source_namespace=source_namespace,
                source_ref=source_ref,
                expected_revision=expected_revision,
            )
            return result.to_dict()
        finally:
            await _close_native(engine, provider)

    try:
        result = asyncio.run(execute())
    except IncompatibleEmbeddingProfileError:
        _output(ctx, error(
            "Embedding profile is incompatible; use a new collection",
            "EMBEDDING_ERROR",
            "native text-ingest",
            details={"reason_code": "incompatible_embedding_profile"},
        ))
        ctx.exit(1)
        return
    except Exception as exc:
        _output(ctx, error(
            "Native text preparation failed",
            _native_error_code(exc, "NATIVE_TEXT_ERROR"),
            "native text-ingest",
        ))
        ctx.exit(1)
        return
    _output(ctx, success(result, command="native text-ingest"))


@native.command(name="text-search")
@click.option("--text", required=True)
@click.option("--candidates", type=click.Choice(["text", "image", "all"]), default="text")
@click.option("--target", type=click.Choice(["resource", "unit"]), default="resource")
@click.option("--mode", type=click.Choice(["text", "semantic", "hybrid"]), default="semantic")
@click.option("--allow-partial", is_flag=True)
@click.option("--limit", type=click.IntRange(1, 10000), default=20)
@click.option("--candidate-budget", type=click.IntRange(1, 10000), default=100)
@click.option("--provider", "provider_name", type=click.Choice(["external", "synthetic"]), default="external")
@click.option("--host-config", type=click.Path(path_type=Path), default=None,
              help="Trusted caller-owned CUDA encoder config; requires --provider external.")
@click.pass_context
def text_search(
    ctx: click.Context,
    text: str,
    candidates: str,
    target: str,
    mode: str,
    allow_partial: bool,
    limit: int,
    candidate_budget: int,
    provider_name: str,
    host_config: Path | None,
) -> None:
    """Query the selected native text/image union from a fresh process."""
    async def execute() -> dict[str, object]:
        engine, provider = (_native_engine(ctx, provider_name) if host_config is None
                            else _native_engine(ctx, provider_name, host_config))
        try:
            result = await engine.search_media(
                MediaSearchRequest(
                    TextInput(text, "query"),
                    SearchScope(modalities=() if candidates == "all" else (candidates,)),
                    target,
                    mode,
                    allow_partial,
                    limit,
                    candidate_budget,
                )
            )
            return result.to_dict()
        finally:
            await _close_native(engine, provider)

    try:
        result = asyncio.run(execute())
    except Exception as exc:
        _output(ctx, error(
            "Native text query failed",
            _native_error_code(exc, "NATIVE_TEXT_ERROR"),
            "native text-search",
        ))
        ctx.exit(1)
        return
    _output(ctx, success(result, command="native text-search"))


@native.command(name="capabilities")
@click.option("--provider", "provider_name", type=click.Choice(["lmstudio", "synthetic"]), default="lmstudio")
@click.pass_context
def capabilities(ctx: click.Context, provider_name: str) -> None:
    async def execute() -> dict[str, object]:
        engine, provider = _engine(ctx, provider_name)
        try:
            return {
                **engine.native_media_capabilities(),
                "boundary": "synthetic-offline-only" if provider_name == "synthetic" else "selected-textual-adapter",
            }
        finally:
            await _close_native(engine, provider)

    try:
        result = asyncio.run(execute())
    except Exception:
        _output(ctx, error("Capabilities unavailable", "NATIVE_MEDIA_ERROR", "native capabilities"))
        ctx.exit(1)
        return
    _output(ctx, success(result, command="native capabilities"))
