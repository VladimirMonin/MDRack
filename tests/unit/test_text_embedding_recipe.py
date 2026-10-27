"""Exact client formatting OFFLINE; opt-in runtime success is unqualified.

Previous recipe-positive transport assertions are retained in immutable P02
checkpoint/continuation01. Their desired qualified-runtime success is OPEN, not
proved by these formatter checks. No native producer or admission bypass is faked.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from mdrack.config.loader import load_config, write_config
from mdrack.config.models import EmbeddingConfig, MDRackConfig
from mdrack.domain.profiles import TextEmbeddingRecipe
from mdrack.embeddings.runtime import create_embedding_provider, embedding_profile_from_config
from mdrack.integrations.lmstudio import LMStudioProvider
from mdrack.ports.embeddings import EmbeddingError

RECIPE = {
    "version": "embeddinggemma2-search-v1",
    "document_prefix": "title: none | text: ",
    "document_suffix": "",
    "query_prefix": "task: search result | query: ",
    "query_suffix": "",
    "max_input_bytes": 1024,
}


def recipe_config(**changes: object) -> MDRackConfig:
    return MDRackConfig(embedding=EmbeddingConfig.model_validate({
        "model": "text-embedding-embeddinggemma-2", "dimensions": 2,
        "text_recipe": {**RECIPE, **changes},
    }))


def wire(monkeypatch: pytest.MonkeyPatch) -> tuple[list[bytes], list[httpx.AsyncClient]]:
    requests: list[bytes] = []
    clients: list[httpx.AsyncClient] = []
    original: Callable[..., httpx.AsyncClient] = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST" and request.url.path == "/v1/embeddings"
        requests.append(request.content)
        texts = json.loads(request.content)["input"]
        return httpx.Response(200, json={"data": [
            {"index": i, "embedding": [float(i + 1), 1.0]} for i in reversed(range(len(texts)))
        ]})

    def factory(**kwargs: object) -> httpx.AsyncClient:
        client = original(transport=httpx.MockTransport(handler), **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return requests, clients


def payload(texts: list[str]) -> bytes:
    return json.dumps({"model": "text-embedding-embeddinggemma-2", "input": texts},
                      ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


@pytest.mark.asyncio
async def test_offline_format_bytes_and_unqualified_runtime_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    requests, clients = wire(monkeypatch)
    # A real legacy positive control before diagnosing the new recipe.
    legacy = create_embedding_provider("lmstudio", MDRackConfig(embedding=EmbeddingConfig(
        model="text-embedding-embeddinggemma-2", dimensions=2,
    )))
    assert await legacy.embed(["control"]) == [[1.0, 1.0]]
    assert requests.pop() == payload(["control"])
    config = recipe_config()
    provider = LMStudioProvider(config.embedding.endpoint, config.embedding.model, 2,
                                text_recipe=config.embedding.text_recipe)
    body = " \tПривет e\u0301\r\n🌍  "
    # These exact prior expected bytes remain required, now at the honest
    # formatter boundary. They are not an outbound/native success claim.
    assert [payload(provider._format_role([body], query=False)),
            payload(provider._format_role([body], query=True))] == [
        payload(["title: none | text: " + body]), payload(["task: search result | query: " + body]),
    ]
    with pytest.raises(EmbeddingError, match="^embedding_input_contract_unqualified$"):
        await provider.embed([body])
    with pytest.raises(EmbeddingError, match="^embedding_input_contract_unqualified$"):
        await provider.embed_query(body)
    assert requests == []
    assert all(client.is_closed for client in clients)


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [1, 32, 33, 65])
async def test_single_multibatch_both_roles_no_guessing(monkeypatch: pytest.MonkeyPatch, count: int) -> None:
    requests, clients = wire(monkeypatch)
    config = recipe_config(document_suffix="\r\n D  ", query_suffix="\tQ ")
    provider = LMStudioProvider(config.embedding.endpoint, config.embedding.model, 2,
                                text_recipe=config.embedding.text_recipe)
    inputs = (["", " \t\r\n", "é e\u0301 中文 🌍  ", "title: none | text: user",
               "task: search result | query: user"] * 13)[:count]
    for query in (False, True):
        prefix = RECIPE["query_prefix" if query else "document_prefix"]
        suffix = "\tQ " if query else "\r\n D  "
        expected = [str(prefix) + text + suffix for text in inputs]
        formatted = provider._format_role(inputs, query=query)
        assert [payload(formatted[i:i + 32]) for i in range(0, count, 32)] == [
            payload(expected[i:i + 32]) for i in range(0, count, 32)
        ]
        with pytest.raises(EmbeddingError, match="^embedding_input_contract_unqualified$"):
            await (provider.embed_queries(inputs) if query else provider.embed(inputs))
        assert requests == []
    assert await provider.embed([]) == []
    assert await provider.embed_queries([]) == []
    assert requests == []
    assert all(client.is_closed for client in clients)


@pytest.mark.asyncio
async def test_explicit_empty_rules_do_not_fall_back_to_generic_instruction(monkeypatch: pytest.MonkeyPatch) -> None:
    requests, _ = wire(monkeypatch)
    config = recipe_config(document_prefix="", document_suffix="", query_prefix="", query_suffix="")
    provider = LMStudioProvider(config.embedding.endpoint, config.embedding.model, 2,
                                text_recipe=config.embedding.text_recipe)
    for text in ("", "  raw \r\n ", "task: search result | query: already looks wrapped"):
        assert payload(provider._format_role([text], query=False)) == payload([text])
        assert payload(provider._format_role([text], query=True)) == payload([text])
        with pytest.raises(EmbeddingError, match="^embedding_input_contract_unqualified$"):
            await provider.embed([text])
        with pytest.raises(EmbeddingError, match="^embedding_input_contract_unqualified$"):
            await provider.embed_query(text)
    assert requests == []


@pytest.mark.asyncio
async def test_bound_counts_formatted_utf8_not_chars_and_rejects_whole_batch_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests, _ = wire(monkeypatch)
    config = recipe_config(document_prefix="é", document_suffix="!", query_prefix="é", query_suffix="!",
                           max_input_bytes=7)
    provider = LMStudioProvider(config.embedding.endpoint, config.embedding.model, 2,
                                text_recipe=config.embedding.text_recipe)
    assert [payload(provider._format_role(["🌍"], query=role)) for role in (False, True)] == [
        payload(["é🌍!"]), payload(["é🌍!"]),
    ]
    with pytest.raises(EmbeddingError, match="^embedding_input_contract_unqualified$"):
        await provider.embed(["🌍"])
    assert requests == []
    for query in (False, True):
        with pytest.raises(EmbeddingError, match="^embedding_input_too_large$"):
            await (provider.embed_queries([""] * 32 + ["🌍x"]) if query
                   else provider.embed([""] * 32 + ["🌍x"]))
        assert requests == []
        with pytest.raises(EmbeddingError, match="^embedding_input_invalid_utf8$"):
            await (provider.embed_queries([""] * 32 + ["\ud800"]) if query
                   else provider.embed([""] * 32 + ["\ud800"]))
        assert requests == []


@pytest.mark.parametrize("field", ["version", "document_prefix", "document_suffix", "query_prefix", "query_suffix"])
@pytest.mark.parametrize("bad", ["missing", None])
def test_recipe_role_fields_are_required_not_nullable(field: str, bad: object) -> None:
    recipe: dict[str, object] = dict(RECIPE)
    if bad == "missing":
        recipe.pop(field)
    else:
        recipe[field] = bad
    with pytest.raises(ValidationError):
        EmbeddingConfig.model_validate({"text_recipe": recipe})


@pytest.mark.parametrize("change", [
    {"version": ""}, {"version": "  "}, {"max_input_bytes": 0}, {"max_input_bytes": True},
    {"max_input_bytes": "1024"}, {"max_input_bytes": 1.5}, {"template_owner": "server"},
    {"title_policy": "guess"}, {"long_input_policy": "truncate"}, {"query_prefx": "typo"},
])
def test_recipe_rejects_incomplete_unsafe_or_unknown_policy(change: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        recipe_config(**change)


def test_every_effective_recipe_field_participates_in_full_space_identity() -> None:
    config = recipe_config()
    profile = embedding_profile_from_config(config, None)
    assert profile.text_recipe is not None
    for field, value in asdict(profile.text_recipe).items():
        if field in {"template_owner", "title_policy", "long_input_policy"}:
            continue  # Unsupported values are refused, not an alternative identity.
        alternative = value + "  " if isinstance(value, str) else value + 1
        assert replace(profile, text_recipe=replace(profile.text_recipe, **{field: alternative})).fingerprint != (
            profile.fingerprint
        )
    assert replace(profile, text_recipe=None).fingerprint != profile.fingerprint
    assert asdict(profile.text_recipe) == asdict(TextEmbeddingRecipe(**RECIPE))


@pytest.mark.parametrize("explicit_none", [False, True])
@pytest.mark.parametrize("policy", [None, "ieee754-f32-canonical-v1"])
def test_full_legacy_serialization_and_fingerprint_stay_byte_exact(
    tmp_path: Path, explicit_none: bool, policy: str | None,
) -> None:
    config = MDRackConfig(embedding=EmbeddingConfig.model_validate({
        "query_instruction": "  Instruct: Task\nQuery: ", "vector_value_policy": policy,
        **({"text_recipe": None} if explicit_none else {}),
    }))
    profile = embedding_profile_from_config(config, None)
    # All historical keys, including the historically raw instruction hash.
    legacy = {"endpoint_family": "openai_embeddings", "model_family": "qwen3-embedding",
              "model_key": "qwen3-embedding-0.6b", "normalization_mode": "l2", "output_dimensions": 1024,
              "provider": "lmstudio", "quantization": "unknown", "instruction_profile": "retrieval-query-v1",
              "query_instruction_hash": hashlib.sha256(b"  Instruct: Task\nQuery: ").hexdigest(),
              "runtime": "lmstudio-gui", "schema_version": 1, "vector_value_policy": policy}
    expected = hashlib.sha256(json.dumps(legacy, ensure_ascii=False, separators=(",", ":"),
                                        sort_keys=True).encode()).hexdigest()
    assert profile.fingerprint == expected
    assert "text_recipe" not in config.model_dump()["embedding"]
    path = tmp_path / "legacy.toml"
    write_config(config, path)
    assert b"text_recipe" not in path.read_bytes()
    # TOML cannot serialize None, so retain the existing policy-None roundtrip
    # limitation as legacy behavior rather than attributing a repair to P02.
    if policy is not None:
        assert embedding_profile_from_config(load_config(toml_path=path), None).fingerprint == expected


@pytest.mark.asyncio
async def test_legacy_wire_rules_remain_stripped_instruction_plus_colon(monkeypatch: pytest.MonkeyPatch) -> None:
    requests, _ = wire(monkeypatch)
    provider = LMStudioProvider(endpoint="http://localhost:1234/v1", model="text-embedding-embeddinggemma-2",
                                dimensions=2, query_instruction="  Instruct: Task\nQuery: ")
    await provider.embed(["  document\r\n "])
    await provider.embed_query("  query\r\n ")
    assert requests == [payload(["  document\r\n "]), payload(["Instruct: Task\nQuery::   query\r\n "])]
    with pytest.raises(ValueError):
        LMStudioProvider(endpoint="http://localhost:1234/v1", model="anything", dimensions=2, query_instruction="")


def test_saved_profile_fresh_process_formats_same_bytes_but_refuses_dispatch(tmp_path: Path) -> None:
    path = tmp_path / "profile.toml"
    config = recipe_config(document_prefix=" D\t", document_suffix="\r\n  ", query_prefix="", query_suffix=" Q ")
    write_config(config, path)
    restored = load_config(toml_path=path)
    assert restored == config
    fingerprint = embedding_profile_from_config(config, None).fingerprint
    program = '''
import asyncio, json, sys, httpx
from pathlib import Path
from mdrack.config.loader import load_config
from mdrack.embeddings.runtime import create_embedding_provider, embedding_profile_from_config
from mdrack.integrations.lmstudio import LMStudioProvider
from mdrack.ports.embeddings import EmbeddingError
config = load_config(toml_path=Path(sys.argv[1]))
requests=[]
original=httpx.AsyncClient
def handler(request):
    requests.append(request.content.hex())
    return httpx.Response(200,json={"data":[{"index":0,"embedding":[1.,1.]}]})
def factory(**kw):
    return original(transport=httpx.MockTransport(handler),**kw)
httpx.AsyncClient=factory
errors=[]
provider=LMStudioProvider(config.embedding.endpoint,config.embedding.model,2,text_recipe=config.embedding.text_recipe)
body=" e\\u0301\\r\\n🌍  "
formatted=[json.dumps({"model":config.embedding.model,"input":provider._format_role([body],query=q)},
    ensure_ascii=False,separators=(",", ":")).encode().hex() for q in (False,True)]
async def main():
    for call in (provider.embed([body]),provider.embed_query(body)):
        try: await call
        except EmbeddingError as exc: errors.append(str(exc))
    try: create_embedding_provider("lmstudio",config)
    except EmbeddingError as exc: errors.append(str(exc))
    await provider.close()
asyncio.run(main())
print(json.dumps({"formatted":formatted,"requests":requests,"errors":errors,
                  "fingerprint":embedding_profile_from_config(config,None).fingerprint}))
'''
    env = {key: value for key, value in os.environ.items() if not key.startswith("MDRACK_")}
    result = subprocess.run([sys.executable, "-c", program, str(path)], cwd=tmp_path,
                            env=env, capture_output=True, check=True, text=True, encoding="utf-8")
    actual = json.loads(result.stdout)
    body = " e\u0301\r\n🌍  "
    assert actual == {"formatted": [payload([" D\t" + body + "\r\n  "]).hex(), payload([body + " Q "]).hex()],
                      "requests": [], "errors": ["embedding_input_contract_unqualified"] * 3,
                      "fingerprint": fingerprint}
