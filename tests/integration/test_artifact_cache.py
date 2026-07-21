"""Local-filesystem integration contracts for provider artifact caching."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping

import pytest

from mdrack.application.artifact_cache import ArtifactCache
from mdrack.integrations.providers import (
    LMStudioTextProvider,
    ProviderHTTPResponse,
)
from mdrack.integrations.providers.contracts import ProviderCacheContext


def _fingerprint(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _context(prompt: str, **overrides: str) -> ProviderCacheContext:
    values = {
        "source_fingerprint": _fingerprint("source"),
        "producer_fingerprint": _fingerprint("producer"),
        "prompt_fingerprint": _fingerprint(prompt),
        "config_fingerprint": _fingerprint("config"),
        "preprocessing_fingerprint": _fingerprint("preprocess"),
        "artifact_kind": "frame_caption",
    }
    values.update(overrides)
    return ProviderCacheContext(**values)


class CountingTransport:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(
        self,
        endpoint: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout: float,
    ) -> ProviderHTTPResponse:
        self.calls += 1
        return ProviderHTTPResponse(
            200,
            {"choices": [{"message": {"content": f"result-{self.calls}"}}]},
        )


def _provider(cache: ArtifactCache, transport: CountingTransport, *, model: str = "model") -> LMStudioTextProvider:
    return LMStudioTextProvider(
        endpoint="http://local.invalid",
        model=model,
        transport=transport,
        artifact_cache=cache,
        max_retries=0,
    )


@pytest.mark.asyncio
async def test_durable_hit_suppresses_provider_call_across_instances(tmp_path: Path) -> None:
    cache = ArtifactCache(tmp_path / "cache")
    transport = CountingTransport()
    prompt = "PRIVATE PROMPT"
    context = _context(prompt)

    first = await _provider(cache, transport).generate(prompt, max_tokens=8, cache_context=context)
    second = await _provider(cache, transport).generate(prompt, max_tokens=8, cache_context=context)

    assert first.cached is False
    assert second.cached is True
    assert second.text == first.text
    assert transport.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_fingerprint", "source-drift"),
        ("producer_fingerprint", "producer-drift"),
        ("config_fingerprint", "config-drift"),
        ("preprocessing_fingerprint", "preprocess-drift"),
    ],
)
async def test_identity_drift_is_a_miss(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    cache = ArtifactCache(tmp_path / "cache")
    transport = CountingTransport()
    prompt = "prompt"
    base = _context(prompt)
    await _provider(cache, transport).generate(prompt, cache_context=base)
    drifted = _context(prompt, **{field: _fingerprint(value)})

    result = await _provider(cache, transport).generate(prompt, cache_context=drifted)

    assert result.cached is False
    assert transport.calls == 2


@pytest.mark.asyncio
async def test_prompt_model_and_runtime_config_drift_are_misses(tmp_path: Path) -> None:
    cache = ArtifactCache(tmp_path / "cache")
    transport = CountingTransport()
    await _provider(cache, transport).generate("one", max_tokens=8, cache_context=_context("one"))

    await _provider(cache, transport).generate("two", max_tokens=8, cache_context=_context("two"))
    await _provider(cache, transport, model="other").generate("one", max_tokens=8, cache_context=_context("one"))
    await _provider(cache, transport).generate("one", max_tokens=9, cache_context=_context("one"))

    assert transport.calls == 4


@pytest.mark.asyncio
async def test_corrupt_and_partial_entries_request_only_needed_artifacts(tmp_path: Path) -> None:
    cache = ArtifactCache(tmp_path / "cache")
    transport = CountingTransport()
    provider = _provider(cache, transport)
    await provider.generate("present", cache_context=_context("present"))
    await provider.generate("corrupt", cache_context=_context("corrupt"))
    assert transport.calls == 2

    corrupt_key = provider.cache_key("corrupt", 256, _context("corrupt"))
    (cache.entry_path(corrupt_key) / "payload.bin").write_bytes(b"corrupt")

    present = await _provider(cache, transport).generate("present", cache_context=_context("present"))
    rebuilt = await _provider(cache, transport).generate("corrupt", cache_context=_context("corrupt"))
    missing = await _provider(cache, transport).generate("missing", cache_context=_context("missing"))

    assert present.cached is True
    assert rebuilt.cached is False
    assert missing.cached is False
    assert transport.calls == 4
    assert cache.verify().ok is True


def test_cache_work_does_not_mutate_source_or_catalog(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    catalog = tmp_path / "catalog.sqlite3"
    source.write_bytes(b"PRIVATE SOURCE")
    catalog.write_bytes(b"PRIVATE CATALOG")
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (source, catalog)}

    cache = ArtifactCache(tmp_path / "cache")
    from mdrack.application.artifact_cache import ArtifactCacheKey

    cache.store(
        ArtifactCacheKey(
            artifact_kind="transcript",
            source_fingerprint=_fingerprint("source"),
            producer_fingerprint=_fingerprint("producer"),
            model_fingerprint=_fingerprint("model"),
            prompt_fingerprint=_fingerprint("prompt"),
            config_fingerprint=_fingerprint("config"),
            preprocessing_fingerprint=_fingerprint("preprocess"),
        ),
        b"PRIVATE ARTIFACT",
    )

    assert {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (source, catalog)} == before
