from __future__ import annotations

from typing import Any, Mapping

import pytest

from mdrack.integrations.providers import (
    LMStudioTextProvider,
    OpenRouterTextProvider,
    OptionalProviderError,
    ProviderHTTPResponse,
)


class FakeTransport:
    def __init__(self, responses: list[ProviderHTTPResponse | Exception]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, str], dict[str, object], float]] = []

    async def __call__(
        self,
        endpoint: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout: float,
    ) -> ProviderHTTPResponse:
        self.calls.append((endpoint, dict(headers), dict(payload), timeout))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def response(text: str = "ok", status: int = 200) -> ProviderHTTPResponse:
    return ProviderHTTPResponse(status, {"choices": [{"message": {"content": text}}]})


@pytest.mark.asyncio
async def test_lmstudio_contract_is_offline_injectable_and_cache_isolated() -> None:
    fake = FakeTransport([response("answer")])
    provider = LMStudioTextProvider(
        endpoint="http://local.invalid",
        model="local-model",
        transport=fake,
        max_retries=0,
    )

    first = await provider.generate("private prompt", max_tokens=8)
    second = await provider.generate("private prompt", max_tokens=8)

    assert first.text == second.text == "answer"
    assert first.cached is False
    assert second.cached is True
    assert first.fingerprint == second.fingerprint
    assert len(fake.calls) == 1
    assert fake.calls[0][1] == {"content-type": "application/json"}
    assert provider.cache_size() == 1
    provider.clear_cache()
    assert provider.cache_size() == 0


@pytest.mark.asyncio
async def test_openrouter_key_is_only_sent_to_injected_transport() -> None:
    fake = FakeTransport([response("remote")])
    provider = OpenRouterTextProvider(
        endpoint="https://api.invalid",
        model="remote-model",
        api_key="secret-sentinel",
        transport=fake,
        max_retries=0,
    )

    result = await provider.generate("prompt", max_tokens=4)

    assert result.text == "remote"
    assert fake.calls[0][1]["authorization"] == "Bearer secret-sentinel"
    assert "secret-sentinel" not in repr(result)
    assert "prompt" not in repr(result)


@pytest.mark.asyncio
async def test_server_failures_retry_with_bounded_attempts() -> None:
    fake = FakeTransport([response(status=503), response(status=503), response(status=503)])
    provider = LMStudioTextProvider(
        endpoint="http://local.invalid",
        model="model",
        transport=fake,
        max_retries=2,
    )

    with pytest.raises(OptionalProviderError) as exc_info:
        await provider.generate("prompt")

    assert exc_info.value.category == "server_error"
    assert len(fake.calls) == 3


@pytest.mark.asyncio
async def test_invalid_response_and_limits_fail_closed_without_cache() -> None:
    fake = FakeTransport([ProviderHTTPResponse(200, {})])
    provider = LMStudioTextProvider(
        endpoint="http://local.invalid",
        model="model",
        transport=fake,
        max_retries=0,
        max_input_chars=4,
        max_output_chars=3,
    )

    with pytest.raises(OptionalProviderError) as invalid:
        await provider.generate("too long")
    assert invalid.value.category == "input_limit"
    assert fake.calls == []

    provider.max_input_chars = 100
    with pytest.raises(OptionalProviderError) as malformed:
        await provider.generate("ok", max_tokens=2)
    assert malformed.value.category == "invalid_response"
    assert provider.cache_size() == 0


@pytest.mark.asyncio
async def test_import_and_construction_do_not_open_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_socket(*args: object, **kwargs: object) -> None:
        raise AssertionError("socket opened")

    monkeypatch.setattr("socket.socket", fail_socket)
    provider = LMStudioTextProvider(endpoint="http://local.invalid", model="model")
    assert provider.cache_size() == 0
