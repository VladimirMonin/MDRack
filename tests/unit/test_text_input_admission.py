"""Missing native producer means refusal, not simulated native qualification."""

from __future__ import annotations

import json

import httpx
import pytest

from mdrack.config.models import EmbeddingConfig, MDRackConfig
from mdrack.embeddings.runtime import create_embedding_provider
from mdrack.integrations.lmstudio import LMStudioProvider
from mdrack.ports.embeddings import EmbeddingError

RECIPE = {
    "version": "embeddinggemma2-search-v1", "document_prefix": "title: none | text: ", "document_suffix": "",
    "query_prefix": "task: search result | query: ", "query_suffix": "", "max_input_bytes": 1024,
}


def configuration() -> MDRackConfig:
    return MDRackConfig(embedding=EmbeddingConfig.model_validate({"model": "fixture-gemma", "dimensions": 2,
                                                                "text_recipe": RECIPE}))


@pytest.mark.asyncio
@pytest.mark.parametrize("query", [False, True])
@pytest.mark.parametrize("claimed_metadata", [None, {"qualified": True},
                                            {"instance": "stale-or-wrong", "context_length": 2048}])
async def test_missing_or_untrusted_contract_never_dispatches(
    monkeypatch: pytest.MonkeyPatch, query: bool, claimed_metadata: object,
) -> None:
    requests: list[bytes] = []
    clients: list[httpx.AsyncClient] = []
    original = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.content)
        # Deliberately untrusted response decoration, NOT a supported protocol,
        # native producer or an input-contract-success fixture.
        return httpx.Response(200, json={"data": [
            {"index": i, "embedding": [1.0, 1.0]} for i, _ in enumerate(json.loads(request.content)["input"])
        ], "unsupported_input_contract_claim": claimed_metadata})

    def client(**kwargs: object) -> httpx.AsyncClient:
        value = original(transport=httpx.MockTransport(handler), **kwargs)
        clients.append(value)
        return value

    monkeypatch.setattr(httpx, "AsyncClient", client)
    legacy = create_embedding_provider("lmstudio", MDRackConfig(embedding=EmbeddingConfig(
        model="fixture-gemma", dimensions=2,
    )))
    assert await legacy.embed(["legacy positive"]) == [[1.0, 1.0]]
    assert await legacy.embed_query("legacy positive") == [1.0, 1.0]
    assert json.loads(requests[0])["input"] == ["legacy positive"]
    assert json.loads(requests[1])["input"] == ["Represent the query for retrieval: legacy positive"]
    assert all(c.is_closed for c in clients)
    requests.clear()
    clients.clear()
    provider = LMStudioProvider("http://fixture.invalid/v1", "fixture-gemma", 2,
                                text_recipe=configuration().embedding.text_recipe)
    for count in (1, 33, 65):
        with pytest.raises(EmbeddingError, match="^embedding_input_contract_unqualified$"):
            await (provider.embed_queries([" \te\u0301\r\n🌍  "] * count) if query
                   else provider.embed([" \te\u0301\r\n🌍  "] * count))
        assert requests == [] and clients == []
    assert provider.returned_dimensions is None and provider.vector_length_valid is None


@pytest.mark.asyncio
async def test_factory_cannot_present_an_unqualified_recipe_as_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    dispatched: list[object] = []
    original = httpx.AsyncClient

    def client(**kwargs: object) -> httpx.AsyncClient:
        dispatched.append("client")
        return original(transport=httpx.MockTransport(lambda request: httpx.Response(
            200, json={"data": [{"index": 0, "embedding": [1.0, 1.0]}]},
        )), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    with pytest.raises(EmbeddingError, match="^embedding_input_contract_unqualified$"):
        provider = create_embedding_provider("lmstudio", configuration())
        await provider.embed(["must not dispatch"])
    assert dispatched == []


@pytest.mark.asyncio
async def test_raw_batch_sender_and_health_cannot_bypass_missing_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    clients: list[object] = []

    def forbidden_client(**kwargs: object) -> httpx.AsyncClient:
        clients.append(kwargs)
        raise AssertionError("unqualified route constructed an HTTP client")

    monkeypatch.setattr(httpx, "AsyncClient", forbidden_client)
    provider = LMStudioProvider("http://fixture.invalid/v1", "fixture-gemma", 2,
                                text_recipe=configuration().embedding.text_recipe)
    for count in (1, 33, 65):
        with pytest.raises(EmbeddingError, match="^embedding_input_contract_unqualified$"):
            await provider._embed_raw(["already formatted"] * count, "default")
    health = await provider.health()
    assert health.ok is False and health.error == "provider_unavailable"
    assert health.returned_dimensions is None and health.vector_length_valid is None
    assert clients == []
