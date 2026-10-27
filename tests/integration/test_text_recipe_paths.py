"""Real legacy paths and unqualified opt-in refusal with real SQLite/filesystem.

Intentional continuation02 contract transition: prior qualified-recipe E2E
success expectations remain immutable in continuation01 and are OPEN/NOT_RUN
until a real native producer exists. We do NOT fake that producer or bypass its
admission. Strong exact D/Q expectations survive at the formatter-only boundary;
actual factory/CLI/engine/prepared routes below must now refuse the opt-in path.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from mdrack.adapters.sqlite.canonical_catalog import open_application_catalog_readonly
from mdrack.application.compatibility import create_application_storage
from mdrack.cli import main
from mdrack.config.loader import load_config, write_config
from mdrack.config.models import EmbeddingConfig, MDRackConfig, PathsConfig
from mdrack.domain.embedding_inputs import EncodingRole, TextInput
from mdrack.domain.profiles import IncompatibleEmbeddingProfileError
from mdrack.embeddings.prepared import LegacyTextEmbeddingAdapter
from mdrack.embeddings.runtime import create_embedding_provider, embedding_profile_from_config
from mdrack.indexing.indexer import run_indexer
from mdrack.integrations.lmstudio import LMStudioModelInfo, LMStudioProvider
from mdrack.integrations.lmstudio.client import LMStudioInputContractError
from mdrack.ports.embeddings import EmbeddingError
from mdrack.public_api import MDRackEngine

DOCUMENT = "title: none | text: "
QUERY = "task: search result | query: "
MODEL = "text-embedding-embeddinggemma-2"


def config_for(*, recipe: bool = True) -> MDRackConfig:
    embedding: dict[str, object] = {"model": MODEL, "dimensions": 2, "query_instruction": "  LEGACY custom  "}
    if recipe:
        embedding["text_recipe"] = {
            "version": "embeddinggemma2-search-v1", "document_prefix": DOCUMENT, "document_suffix": "",
            "query_prefix": QUERY, "query_suffix": "", "max_input_bytes": 1024,
        }
    return MDRackConfig(embedding=EmbeddingConfig.model_validate(embedding))


@pytest.fixture
def outbound(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    requests: list[list[str]] = []
    original = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/embeddings"
        payload = json.loads(request.content)
        assert payload["model"] == MODEL
        texts = payload["input"]
        requests.append(texts)
        return httpx.Response(200, json={"data": [
            {"index": i, "embedding": [1.0, 1.0]} for i in reversed(range(len(texts)))
        ]})

    def factory(**kwargs: object) -> httpx.AsyncClient:
        return original(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return requests


class Control:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def list_models(self):
        self.events.append("list")
        return [LMStudioModelInfo(key=MODEL, state="loaded", loaded=True, instance_ids=("fixture-only",))]

    async def close(self):
        self.events.append("closed")


def initialize(root: Path, config: MDRackConfig) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    create_application_storage(root, config, create=True).close()
    (root / "kept.md").write_text("# Kept\n\nRollback searchable evidence.\n", encoding="utf-8")
    path = root / ".mdrack/config.toml"
    write_config(config, path)
    return path


def snapshot(root: Path, config: MDRackConfig):
    with open_application_catalog_readonly(root, config) as catalog:
        dump = tuple(catalog.connection.iterdump())
    return (root / config.paths.store / "catalog.sqlite3").read_bytes(), dump


def useful_search(root: Path, config: MDRackConfig):
    provider = create_embedding_provider("lmstudio", config)
    with MDRackEngine(root=root, config=config, embedding_provider=provider) as engine:
        lexical = engine.search_text("Rollback")
        semantic = asyncio.run(engine.search_semantic("Rollback"))
        assert lexical.results and semantic.results and not semantic.degraded
        return lexical, semantic


@pytest.mark.parametrize("entry", ["engine", "wrapper", "scan", "rebuild", "switch"])
def test_legacy_construction_paths_remain_useful(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outbound: list[list[str]], entry: str,
) -> None:
    config = config_for(recipe=False)
    initialize(tmp_path, config)
    config = load_config(root=tmp_path)
    events: list[str] = []
    monkeypatch.setattr("mdrack.cli.commands.model.create_model_control_client", lambda ctx: Control(events))
    if entry == "engine":
        with MDRackEngine(root=tmp_path, config=config,
                          embedding_provider=create_embedding_provider("lmstudio", config)) as engine:
            assert engine.scan().status == "success"
    elif entry == "wrapper":
        assert run_indexer(tmp_path, config, provider=create_embedding_provider("lmstudio", config)).status == "success"
    else:
        args = {"scan": ["scan"], "rebuild": ["rebuild", "embeddings"],
                "switch": ["model", "switch", MODEL, "--dimensions", "2"]}[entry]
        result = CliRunner().invoke(main, ["--root", str(tmp_path), *args])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["ok"] is True
    assert outbound == [["Kept\n\nRollback searchable evidence."]]
    useful_search(tmp_path, load_config(root=tmp_path))
    assert outbound[-1] == ["LEGACY custom: Rollback"]
    if entry == "switch":
        assert events == ["list", "closed"]


@pytest.mark.parametrize("entry", ["engine-factory", "wrapper-factory", "scan", "rebuild", "switch", "none", "full"])
def test_unqualified_saved_recipe_refuses_before_dispatch_or_activation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outbound: list[list[str]], entry: str,
) -> None:
    config = config_for()
    path = initialize(tmp_path, config)
    config = load_config(root=tmp_path)
    before_config, before_catalog = path.read_bytes(), snapshot(tmp_path, config)
    controls: list[str] = []

    def control(ctx):
        controls.append("constructed")
        return Control(controls)

    monkeypatch.setattr("mdrack.cli.commands.model.create_model_control_client", control)
    if entry in {"engine-factory", "wrapper-factory"}:
        with pytest.raises(LMStudioInputContractError, match="^embedding_input_contract_unqualified$"):
            create_embedding_provider("lmstudio", config)
    else:
        args = {"scan": ["scan"], "rebuild": ["rebuild", "embeddings"],
                "switch": ["model", "switch", MODEL, "--dimensions", "2"],
                "none": ["model", "switch", MODEL, "--dimensions", "2", "--rebuild", "none", "--yes"],
                "full": ["model", "switch", MODEL, "--dimensions", "2", "--rebuild", "full", "--yes"]}[entry]
        result = CliRunner().invoke(main, ["--root", str(tmp_path), *args])
        assert result.exit_code == 1, result.output
        payload = json.loads(result.output)
        assert payload["ok"] is False
        assert payload["error"]["code"] == "EMBEDDING_ERROR"
        assert payload["error"]["details"]["reason_code"] == "embedding_input_contract_unqualified"
        assert "unqualified" in payload["error"]["message"]
    assert outbound == [] and controls == []
    assert path.read_bytes() == before_config
    assert snapshot(tmp_path, config) == before_catalog


@pytest.mark.parametrize("entry", ["engine", "wrapper"])
def test_direct_injection_fails_honestly_without_publishing_vectors(
    tmp_path: Path, outbound: list[list[str]], entry: str,
) -> None:
    config = config_for()
    path = initialize(tmp_path, config)
    before_config = path.read_bytes()
    provider = LMStudioProvider(config.embedding.endpoint, MODEL, 2, text_recipe=config.embedding.text_recipe)
    if entry == "engine":
        with MDRackEngine(root=tmp_path, config=config, embedding_provider=provider) as engine:
            result = engine.scan()
            assert not engine.search_text("Rollback").results
    else:
        result = run_indexer(tmp_path, config, provider=provider)
    # Preserve the existing direct indexing per-file error envelope. No native
    # qualification/partial success is inferred from an error being caught.
    assert result.status == "failed" and result.files_indexed == 0
    assert result.error_codes == ("FILE_INDEX_ERROR",)
    assert outbound == [] and path.read_bytes() == before_config
    with open_application_catalog_readonly(tmp_path, config) as catalog:
        assert catalog.connection.execute("SELECT count(*) FROM core_unit_embeddings").fetchone()[0] == 0
        assert catalog.connection.execute("SELECT count(*) FROM core_resources").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_prepared_roles_keep_exact_offline_strings_but_refuse_unqualified_encoding(
    outbound: list[list[str]],
) -> None:
    config = config_for()
    provider = LMStudioProvider(config.embedding.endpoint, MODEL, 2, text_recipe=config.embedding.text_recipe)
    adapter = LegacyTextEmbeddingAdapter(provider, embedding_profile_from_config(config, provider))
    text = " \tUnicode 🌍\r\n  "
    assert [provider._format_role([text], query=q) for q in (False, True)] == [[DOCUMENT + text], [QUERY + text]]
    for role in (EncodingRole.DOCUMENT, EncodingRole.QUERY):
        with pytest.raises(EmbeddingError, match="^embedding_provider_error$"):
            await adapter.encode(TextInput(text, "synthetic-recipe-input"), role)
    assert outbound == []


@pytest.mark.parametrize("configured_recipe", [False, True])
def test_manual_http_injection_cannot_mislabel_actual_recipe(
    tmp_path: Path, outbound: list[list[str]], configured_recipe: bool,
) -> None:
    config = config_for(recipe=configured_recipe)
    initialize(tmp_path, config)
    before = snapshot(tmp_path, config)
    other = config_for(recipe=not configured_recipe)
    injected = LMStudioProvider(config.embedding.endpoint, MODEL, 2, text_recipe=other.embedding.text_recipe)
    with MDRackEngine(root=tmp_path, config=config, embedding_provider=injected) as engine:
        with pytest.raises(IncompatibleEmbeddingProfileError):
            engine.scan(force_reindex=True)
    assert outbound == [] and snapshot(tmp_path, config) == before


@pytest.mark.parametrize("entry", ["engine", "wrapper", "scan", "force", "rebuild", "switch-none", "switch-full"])
@pytest.mark.parametrize("change", ["query_suffix", "document_prefix"])
def test_legacy_collection_recipe_change_preserves_reopen_next_search_and_separate_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outbound: list[list[str]], entry: str, change: str,
) -> None:
    original = config_for(recipe=False)
    path = initialize(tmp_path, original)
    assert run_indexer(tmp_path, original, provider=create_embedding_provider("lmstudio", original)).status == "success"
    expected = useful_search(tmp_path, original)
    base = config_for()
    replacement = MDRackConfig(embedding=EmbeddingConfig.model_validate({
        **base.embedding.model_dump(),
        "text_recipe": {**base.embedding.model_dump()["text_recipe"], change: " changed "},
    }))
    write_config(replacement, path)
    before_config, before_catalog = path.read_bytes(), snapshot(tmp_path, original)
    outbound.clear()
    traversals: list[object] = []
    walk = os.walk

    def observe_walk(top, *args, **kwargs):
        traversals.append(top)
        return walk(top, *args, **kwargs)

    monkeypatch.setattr(os, "walk", observe_walk)
    factories: list[str] = []

    def forbidden_factory(*args, **kwargs):
        factories.append("external_created")
        raise AssertionError("mismatch reached external construction")

    for command in ("scan", "rebuild"):
        monkeypatch.setattr(f"mdrack.cli.commands.{command}.create_embedding_provider", forbidden_factory)
    monkeypatch.setattr("mdrack.cli.commands.model.create_model_control_client", forbidden_factory)
    provider = LMStudioProvider(replacement.embedding.endpoint, MODEL, 2, text_recipe=replacement.embedding.text_recipe)
    if entry == "engine":
        with MDRackEngine(root=tmp_path, config=replacement, embedding_provider=provider) as engine:
            with pytest.raises(IncompatibleEmbeddingProfileError):
                engine.scan(force_reindex=True)
    elif entry == "wrapper":
        with pytest.raises(IncompatibleEmbeddingProfileError):
            run_indexer(tmp_path, replacement, provider=provider, force_reindex=True)
    else:
        args = {"scan": ["scan"], "force": ["scan", "--changed"], "rebuild": ["rebuild", "embeddings"],
                "switch-none": ["model", "switch", MODEL, "--rebuild", "none", "--yes"],
                "switch-full": ["model", "switch", MODEL, "--rebuild", "full", "--yes"]}[entry]
        result = CliRunner().invoke(main, ["--root", str(tmp_path), *args])
        assert result.exit_code == 1, result.output
        assert json.loads(result.output)["error"]["details"]["reason_code"] == "incompatible_embedding_profile"
    assert outbound == [] and traversals == [] and factories == []
    assert path.read_bytes() == before_config and snapshot(tmp_path, original) == before_catalog
    assert useful_search(tmp_path, original) == expected
    assert run_indexer(tmp_path, original, provider=create_embedding_provider("lmstudio", original)).files_changed == 0
    # Store isolation still works for the useful legacy route; a NEW opt-in
    # store does not pretend its missing native producer has become available.
    new = original.model_copy(update={"paths": PathsConfig(store="new-legacy-collection")})
    assert run_indexer(tmp_path, new, provider=create_embedding_provider("lmstudio", new)).status == "success"
    assert useful_search(tmp_path, new)[0].results
    blocked = replacement.model_copy(update={"paths": PathsConfig(store="unqualified-new-collection")})
    with pytest.raises(LMStudioInputContractError):
        create_embedding_provider("lmstudio", blocked)
    assert not (tmp_path / "unqualified-new-collection").exists()
    assert snapshot(tmp_path, original) == before_catalog and path.read_bytes() == before_config
