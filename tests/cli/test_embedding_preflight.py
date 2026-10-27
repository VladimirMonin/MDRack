"""CLI refusal and honest rebuild activation with only external edges replaced."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from mdrack.adapters.sqlite.canonical_catalog import open_application_catalog_readonly
from mdrack.application.compatibility import create_application_storage
from mdrack.cli import main
from mdrack.config.loader import load_config, write_config
from mdrack.config.models import EmbeddingConfig, MDRackConfig
from mdrack.embeddings.fake import FakeEmbeddingProvider
from mdrack.embeddings.lmstudio import LMStudioModelInfo
from mdrack.indexing.indexer import run_indexer
from mdrack.public_api import MDRackEngine

OLD_MODEL = "synthetic-text-a"
NEW_MODEL = "synthetic-text-b"


class Provider(FakeEmbeddingProvider):
    def __init__(self, *, model=OLD_MODEL, dimensions=2, events=None, failure=None, **kwargs):
        super().__init__(dimensions, provider_name="lmstudio")
        self._model_name = model
        self.events = events if events is not None else []
        self.failure = failure
        self.events.append("provider_created")

    async def embed(self, texts, profile="default"):
        self.events.append("embed")
        if self.failure == "embed" and any("PRIVATE_PROVIDER_INPUT" in text for text in texts):
            raise RuntimeError("PRIVATE_PROVIDER_EXCEPTION")
        return await super().embed(texts, profile)

    async def close(self):
        self.events.append("provider_closed")


class Control:
    def __init__(self, events, *, failure=None):
        self.events = events
        self.failure = failure

    def _call(self, name):
        self.events.append(name)
        if self.failure == name:
            raise RuntimeError("PRIVATE_CONTROL_EXCEPTION")

    def list_models(self):
        self._call("list_models")
        return [
            LMStudioModelInfo(key=OLD_MODEL, state="loaded", loaded=True, instance_ids=("old-instance",)),
            LMStudioModelInfo(key=NEW_MODEL, state="downloaded", loaded=False),
        ]

    def load_model(self, model):
        self._call("load")
        return {"key": model, "state": "loaded"}

    def probe_embedding_dimensions(self, model):
        self._call("probe")
        return 2

    def unload_model(self, instance):
        self._call("unload")

    async def close(self):
        self._call("control_closed")


def prepare(root: Path, *, populated=True):
    config = MDRackConfig(embedding=EmbeddingConfig(model=OLD_MODEL, dimensions=2))
    create_application_storage(root, config, create=True).close()
    if populated:
        (root / "kept.md").write_text("# Kept\n\nRollback searchable evidence.\n", encoding="utf-8")
        assert run_indexer(root, config, provider=Provider()).status == "success"
    path = root / ".mdrack" / "config.toml"
    write_config(config, path)
    return config, path


def snapshot(root: Path):
    with open_application_catalog_readonly(root, MDRackConfig()) as catalog:
        rows = tuple(catalog.connection.iterdump())
    return (root / ".mdrack" / "catalog.sqlite3").read_bytes(), rows


def external_edges(monkeypatch, events, *, failure=None):
    def control_factory(ctx):
        events.append("control_created")
        return Control(events, failure=failure)

    def provider_factory(**kwargs):
        return Provider(events=events, failure=failure, **kwargs)

    monkeypatch.setattr("mdrack.cli.commands.model.create_model_control_client", control_factory)
    monkeypatch.setattr("mdrack.cli.commands.model.LMStudioProvider", provider_factory)
    for command in ("scan", "rebuild"):
        monkeypatch.setattr(
            f"mdrack.cli.commands.{command}.create_embedding_provider",
            lambda name, config: provider_factory(model=config.embedding.model, dimensions=config.embedding.dimensions),
        )


def useful_reopen(root: Path, config: MDRackConfig):
    with MDRackEngine(root=root, config=config, embedding_provider=Provider(model=config.embedding.model)) as engine:
        text = engine.search_text("Rollback", limit=1)
        semantic = asyncio.run(engine.search_semantic("Rollback", limit=1))
        assert text.results and semantic.results and not semantic.degraded
        return text, semantic


@pytest.mark.parametrize("options", [
    [], ["--rebuild", "embeddings"], ["--rebuild", "full"],
    ["--rebuild", "none", "--yes"], ["--download", "--yes"],
    ["--no-load", "--dimensions", "2", "--yes"],
])
@pytest.mark.parametrize("target", [NEW_MODEL, "synthetic-text"])
def test_switch_refuses_before_any_control_or_provider_edge_and_preserves_reopen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, options: list[str], target: str,
) -> None:
    config, path = prepare(tmp_path)
    expected = useful_reopen(tmp_path, config)
    before_config, before_catalog = path.read_bytes(), snapshot(tmp_path)
    events = []
    external_edges(monkeypatch, events)
    result = CliRunner().invoke(main, ["--root", str(tmp_path), "model", "switch", target, *options])
    assert events == [], "incompatible profile reached an external edge before catalog preflight"
    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "EMBEDDING_ERROR"
    assert payload["error"]["details"]["reason_code"] == "incompatible_embedding_profile"
    assert "new collection" in payload["error"]["message"].lower()
    assert path.read_bytes() == before_config
    assert snapshot(tmp_path) == before_catalog
    assert useful_reopen(tmp_path, load_config(root=tmp_path)) == expected
    next_scan = run_indexer(tmp_path, config, provider=Provider())
    assert next_scan.status == "success" and next_scan.files_changed == 0


@pytest.mark.parametrize("command", [["scan"], ["scan", "--changed"], ["rebuild", "embeddings"]])
def test_manual_config_edit_refused_before_provider_creation_without_repairing_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: list[str],
) -> None:
    config, path = prepare(tmp_path)
    changed = config.model_copy(update={"embedding": config.embedding.model_copy(update={"model": NEW_MODEL})})
    write_config(changed, path)
    before_config, before_catalog = path.read_bytes(), snapshot(tmp_path)
    (tmp_path / "kept.md").unlink()
    events = []
    external_edges(monkeypatch, events)
    result = CliRunner().invoke(main, ["--root", str(tmp_path), *command])
    assert events == []
    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "EMBEDDING_ERROR"
    assert payload["error"]["details"]["reason_code"] == "incompatible_embedding_profile"
    assert path.read_bytes() == before_config
    assert snapshot(tmp_path) == before_catalog
    assert useful_reopen(tmp_path, config)[0].results


@pytest.mark.parametrize("status", ["partial_success", "failed"])
@pytest.mark.parametrize("file_failure", ["decode", "provider"])
@pytest.mark.parametrize("cleanup_failure", [False, True])
def test_non_success_rebuild_never_activates_config_or_unloads_old_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str, file_failure: str,
    cleanup_failure: bool, caplog: pytest.LogCaptureFixture,
) -> None:
    config, path = prepare(tmp_path, populated=False)
    before_config = path.read_bytes()
    if file_failure == "decode":
        (tmp_path / "bad.md").write_bytes(b"\xff\xfe")
    else:
        (tmp_path / "bad.md").write_text("# Bad\n\nPRIVATE_PROVIDER_INPUT", encoding="utf-8")
    if status == "partial_success":
        (tmp_path / "good.md").write_text("# Good\n\nRollback searchable evidence.\n", encoding="utf-8")
    events = []
    external_edges(monkeypatch, events, failure="embed" if file_failure == "provider" else None)
    if cleanup_failure:
        def control_factory(ctx):
            events.append("control_created")
            return Control(events, failure="control_closed")
        monkeypatch.setattr("mdrack.cli.commands.model.create_model_control_client", control_factory)
    result = CliRunner().invoke(main, ["--root", str(tmp_path), "model", "switch", NEW_MODEL])
    assert result.exit_code == 1, "a non-success IndexingResult was treated as activation success"
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "EMBEDDING_ERROR"
    assert payload["error"]["details"] == {"reason_code": "embedding_rebuild_incomplete", "status": status}
    assert "did not complete" in payload["error"]["message"]
    for sentinel in (
        "PRIVATE_PROVIDER_INPUT", "PRIVATE_PROVIDER_EXCEPTION", "PRIVATE_CONTROL_EXCEPTION", str(tmp_path),
    ):
        assert sentinel not in result.output
        assert sentinel not in caplog.text
    assert path.read_bytes() == before_config
    assert load_config(root=tmp_path).embedding.model == OLD_MODEL
    assert "unload" not in events
    assert events[-1] == "control_closed"
    assert "provider_closed" in events
    with open_application_catalog_readonly(tmp_path, config) as catalog:
        count = catalog.connection.execute("SELECT COUNT(*) FROM core_resources").fetchone()[0]
        assert count == (1 if status == "partial_success" else 0)
    if status == "partial_success":
        requested = config.model_copy(update={"embedding": config.embedding.model_copy(update={"model": NEW_MODEL})})
        assert useful_reopen(tmp_path, requested)[0].results  # Useful work survives, not rolled back/deleted.
    (tmp_path / "bad.md").unlink()
    (tmp_path / "good.md").write_text("# Good\n\nRollback searchable evidence.\n", encoding="utf-8")
    completed = CliRunner().invoke(main, ["--root", str(tmp_path), "model", "switch", NEW_MODEL])
    assert completed.exit_code == 0, completed.output
    assert json.loads(completed.output)["data"]["rebuild"]["status"] == "success"
    assert load_config(root=tmp_path).embedding.model == NEW_MODEL
    assert useful_reopen(tmp_path, load_config(root=tmp_path))[0].results


@pytest.mark.parametrize("failure", ["list_models", "load", "probe"])
def test_control_exception_does_not_activate_or_unload_and_preserves_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    config, path = prepare(tmp_path, populated=False)
    before_config, before_catalog = path.read_bytes(), snapshot(tmp_path)
    events = []
    external_edges(monkeypatch, events, failure=failure)
    result = CliRunner().invoke(main, ["--root", str(tmp_path), "model", "switch", NEW_MODEL])
    assert result.exit_code == 1
    assert json.loads(result.output)["ok"] is False
    assert "PRIVATE_CONTROL_EXCEPTION" not in result.output
    assert "unload" not in events
    assert "provider_created" not in events
    assert events[-1] == "control_closed"
    assert path.read_bytes() == before_config
    assert snapshot(tmp_path) == before_catalog
    # A fresh allowed operation after the exception can still publish normally.
    (tmp_path / "note.md").write_text("# Kept\n\nRollback searchable evidence.", encoding="utf-8")
    assert run_indexer(tmp_path, config, provider=Provider()).status == "success"
    assert useful_reopen(tmp_path, config)[0].results


@pytest.mark.parametrize("mode", ["embeddings", "full", "none"])
def test_compatible_switch_preserves_config_identity_and_does_not_probe_unknown_dimensions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str,
) -> None:
    config, path = prepare(tmp_path)
    before = useful_reopen(tmp_path, config)
    events = []
    external_edges(monkeypatch, events)
    result = CliRunner().invoke(
        main, ["--root", str(tmp_path), "model", "switch", OLD_MODEL, "--rebuild", mode, "--yes"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert "probe" not in events  # A populated collection pins the already-proven full dimensions.
    assert "unload" not in events
    assert load_config(root=tmp_path).embedding == config.embedding
    assert useful_reopen(tmp_path, config) == before


def test_scan_partial_envelope_stays_ok_true_exit_zero_with_explicit_status(tmp_path: Path) -> None:
    config, path = prepare(tmp_path, populated=False)
    before_config = path.read_bytes()
    (tmp_path / "good.md").write_text("# Good\n\nRollback evidence.", encoding="utf-8")
    (tmp_path / "bad.md").write_bytes(b"\xff\xfe")
    result = CliRunner().invoke(main, ["--root", str(tmp_path), "scan", "--provider", "fake"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["data"]["status"] == "partial_success"
    assert payload["data"]["files_indexed"] == 1 and payload["data"]["files_failed"] == 1
    assert path.read_bytes() == before_config
    with MDRackEngine(root=tmp_path, config=config) as engine:
        assert engine.search_text("Rollback").results


def test_config_write_exception_after_success_never_unloads_old_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, path = prepare(tmp_path, populated=False)
    before_config = path.read_bytes()
    (tmp_path / "good.md").write_text("# Good\n\nRollback searchable evidence.", encoding="utf-8")
    events = []
    external_edges(monkeypatch, events)
    replace_path = Path.replace

    def fail_config_replace(source, target):
        if Path(target) == path:
            raise OSError("PRIVATE_CONFIG_PATH_FAILURE")
        return replace_path(source, target)

    monkeypatch.setattr(Path, "replace", fail_config_replace)
    result = CliRunner().invoke(main, ["--root", str(tmp_path), "model", "switch", NEW_MODEL])
    assert result.exit_code == 1
    assert json.loads(result.output)["ok"] is False
    assert "PRIVATE_CONFIG_PATH_FAILURE" not in result.output
    assert path.read_bytes() == before_config
    assert "unload" not in events
    requested = config.model_copy(update={"embedding": config.embedding.model_copy(update={"model": NEW_MODEL})})
    assert useful_reopen(tmp_path, requested)[0].results


@pytest.mark.parametrize("mode", ["embeddings", "full", "none"])
def test_empty_collection_allows_switch_in_each_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str,
) -> None:
    config, path = prepare(tmp_path, populated=False)
    (tmp_path / "good.md").write_text("# Good\n\nRollback searchable evidence.", encoding="utf-8")
    events = []
    external_edges(monkeypatch, events)
    result = CliRunner().invoke(main, [
        "--root", str(tmp_path), "model", "switch", NEW_MODEL, "--rebuild", mode, "--yes",
    ])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert load_config(root=tmp_path).embedding.model == NEW_MODEL
    if mode == "none":
        assert payload["data"]["rebuild"]["performed"] is False
        assert run_indexer(tmp_path, load_config(root=tmp_path), provider=Provider(model=NEW_MODEL)).status == "success"
    else:
        assert payload["data"]["rebuild"]["status"] == "success"
    assert useful_reopen(tmp_path, load_config(root=tmp_path))[0].results


def test_same_model_different_dimensions_refuses_before_probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, path = prepare(tmp_path)
    before_config, before_catalog = path.read_bytes(), snapshot(tmp_path)
    events = []
    external_edges(monkeypatch, events)
    result = CliRunner().invoke(main, [
        "--root", str(tmp_path), "model", "switch", OLD_MODEL, "--dimensions", "3", "--yes",
    ])
    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["details"]["reason_code"] == "incompatible_embedding_profile"
    assert events == []
    assert path.read_bytes() == before_config
    assert snapshot(tmp_path) == before_catalog


def test_unknown_store_is_not_treated_as_an_empty_collection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = tmp_path / ".mdrack"
    store.mkdir()
    legacy = store / "knowledge.db"
    legacy.write_bytes(b"synthetic unsupported store")
    events = []
    external_edges(monkeypatch, events)
    result = CliRunner().invoke(main, ["--root", str(tmp_path), "model", "switch", NEW_MODEL])
    assert result.exit_code == 1
    assert json.loads(result.output)["ok"] is False
    assert events == []
    assert legacy.read_bytes() == b"synthetic unsupported store"
    assert not (store / "catalog.sqlite3").exists()
    assert not (store / "config.toml").exists()
