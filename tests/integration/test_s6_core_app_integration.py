"""S6 production composition through a ready resource generation."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import click
import pytest
from click.testing import CliRunner

from mdrack.adapters.sqlite.index_storage import SQLiteIndexStorage
from mdrack.adapters.sqlite.resource_store import SQLiteResourceStore
from mdrack.application.compatibility import CoreCompatibilityStorage, create_application_storage
from mdrack.application.generation_manager import StoreGenerationManagerError
from mdrack.application.resources import ResourceQueryService
from mdrack.application.retrieval import RetrievalService
from mdrack.application.store_generations import (
    ActiveGenerationPointer,
    GenerationContractKind,
    GenerationState,
    StoreGeneration,
)
from mdrack.cli import main
from mdrack.cli.commands.model import _run_switch_rebuild
from mdrack.config.models import MDRackConfig, PathsConfig
from mdrack.embeddings.fake import FakeEmbeddingProvider
from mdrack.embeddings.runtime import embedding_profile_from_config
from mdrack.indexing.indexer import IndexerResult, run_indexer
from mdrack.public_api import MDRackEngine
from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.migrations import (
    EXPECTED_MIGRATION_MANIFEST_DIGEST,
    EXPECTED_MIGRATION_VERSION,
    apply_candidate_migrations,
    get_migrations_dir,
)


def _generation(store_dir: Path, *, state: GenerationState) -> Path:
    generation_id = "g-s6-test"
    generations = store_dir / "generations"
    generations.mkdir(parents=True)
    database_path = generations / f"generation-{generation_id}.sqlite3"
    connection = get_connection(database_path)
    apply_candidate_migrations(connection, get_migrations_dir())
    connection.close()
    generation = StoreGeneration(
        generation_id=generation_id,
        contract_kind=GenerationContractKind.RESOURCE_CORE_V1,
        migration_manifest_digest=EXPECTED_MIGRATION_MANIFEST_DIGEST,
        schema_version=EXPECTED_MIGRATION_VERSION,
        state=state,
        created_at="2026-07-18T00:00:00+00:00",
        verified_at="2026-07-18T00:00:01+00:00" if state is GenerationState.READY else None,
    )
    (generations / f"generation-{generation_id}.json").write_bytes(generation.to_bytes())
    (store_dir / "active-generation.json").write_bytes(
        ActiveGenerationPointer(generation_id, GenerationContractKind.RESOURCE_CORE_V1).to_bytes()
    )
    return database_path


def _assert_public_results_equivalent(
    actual: tuple[dict[str, object], ...],
    expected: tuple[dict[str, object], ...],
) -> None:
    assert len(actual) == len(expected)
    score_keys = ("score", "text_score", "semantic_score", "rrf_score", "rerank_score")
    for actual_result, expected_result in zip(actual, expected, strict=True):
        actual_envelope = dict(actual_result)
        expected_envelope = dict(expected_result)
        actual_items = actual_envelope.pop("results")
        expected_items = expected_envelope.pop("results")
        assert actual_envelope == expected_envelope
        assert isinstance(actual_items, list)
        assert isinstance(expected_items, list)
        assert len(actual_items) == len(expected_items)
        for actual_item, expected_item in zip(actual_items, expected_items, strict=True):
            assert isinstance(actual_item, dict)
            assert isinstance(expected_item, dict)
            actual_fields = dict(actual_item)
            expected_fields = dict(expected_item)
            for key in score_keys:
                actual_score = actual_fields.pop(key)
                expected_score = expected_fields.pop(key)
                if expected_score is None:
                    assert actual_score is None
                else:
                    assert isinstance(actual_score, (int, float))
                    assert isinstance(expected_score, (int, float))
                    assert actual_score == pytest.approx(expected_score, abs=1e-7)
            assert actual_fields == expected_fields


def test_scan_and_all_query_modes_use_ready_core_generation_with_legacy_parity(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    source = "# Guide\n\n## Stable\n\nAlpha retrieval phrase.\n\n![diagram](diagram.png)\n"
    note = root / "guide.md"
    note.write_text(source, encoding="utf-8")
    (root / "diagram.png").write_bytes(b"not-a-real-image")
    store_dir = tmp_path / "store"
    database_path = _generation(store_dir, state=GenerationState.READY)
    config = MDRackConfig(paths=PathsConfig(root=".", store=str(store_dir)))
    provider = FakeEmbeddingProvider(dimensions=8)
    profile = embedding_profile_from_config(config, provider, "default")

    scan = run_indexer(root, config, provider=provider)
    connection = get_connection(database_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM chunk_embeddings").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM core_unit_embeddings").fetchone()[0] == (
            scan.chunks_created + 1
        )
        embedding_metadata = json.loads(
            connection.execute("SELECT metadata_json FROM core_embedding_spaces").fetchone()[0]
        )
        payload_sizes = connection.execute(
            "SELECT DISTINCT length(embedding) FROM core_unit_embeddings"
        ).fetchall()
        assert embedding_metadata["vector_value_policy"] == "ieee754-f32-canonical-v1"
        assert embedding_metadata["vector_codec"] == "ieee754-f32-le-v1"
        assert [tuple(row) for row in payload_sizes] == [(32,)]
    finally:
        connection.close()
    config_path = root / "mdrack.toml"
    config_path.write_text(
        f'[paths]\nstore = "{store_dir}"\n[embedding]\ndimensions = 8\n',
        encoding="utf-8",
    )
    rebuilt = CliRunner().invoke(
        main,
        [
            "--root",
            str(root),
            "--config-file",
            str(config_path),
            "rebuild",
            "embeddings",
            "--provider",
            "fake",
        ],
    )
    assert rebuilt.exit_code == 0, rebuilt.output
    assert json.loads(rebuilt.output)["data"]["embedded_count"] == scan.chunks_created
    storage = create_application_storage(root, config)
    assert isinstance(storage, CoreCompatibilityStorage)
    retrieval = RetrievalService(
        storage,
        embedding_provider=provider,
        profile="default",
        profile_fingerprint=profile.fingerprint,
        rrf_k=60,
    )
    text = retrieval.search_text("Alpha", limit=5)
    semantic = asyncio.run(retrieval.search_semantic("Alpha", limit=5))
    hybrid = asyncio.run(retrieval.search_hybrid("Alpha", limit=5))

    assert scan.status == "success"
    assert note.read_text(encoding="utf-8") == source
    assert [item.logical_id for item in text.results]
    assert [item.logical_id for item in semantic.results]
    assert [item.logical_id for item in hybrid.results]
    text_locators = {item.logical_id: item.source_locator for item in text.results}
    semantic_locators = {item.logical_id: item.source_locator for item in semantic.results}
    shared = text_locators.keys() & semantic_locators.keys()
    assert shared
    assert all(text_locators[item_id] == semantic_locators[item_id] for item_id in shared)
    assert hybrid.results[0].rrf_rank == 1
    assert hybrid.results[0].text_rank == 1
    assert hybrid.results[0].semantic_rank is not None
    resource_id = str(storage.connection.execute("SELECT resource_id FROM core_resources").fetchone()[0])
    projections = storage.resolve_textual_whole_resource_units(resource_id)
    assert len(projections) == 1
    assert projections[0].resource_id == resource_id
    assert projections[0].space.fingerprint == profile.fingerprint
    storage.close()

    moved = root / "moved.md"
    note.rename(moved)
    renamed = run_indexer(root, config, provider=provider)
    assert renamed.status == "success"
    assert renamed.files_deleted == 0

    connection = get_connection(database_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM chunk_embeddings").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM core_resources").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM core_search_units").fetchone()[0] == scan.chunks_created + 1
        assert connection.execute("SELECT COUNT(*) FROM core_unit_embeddings").fetchone()[0] == scan.chunks_created + 1
        assert connection.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM asset_references").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM asset_descriptions").fetchone()[0] == 0
        metadata = json.loads(
            connection.execute("SELECT metadata_json FROM core_resources").fetchone()[0]
        )
        assert metadata["relative_path"] == "moved.md"
    finally:
        connection.close()

    moved.unlink()
    deleted = run_indexer(root, config, provider=provider)
    assert deleted.status == "success"
    assert deleted.files_deleted == 1
    connection = get_connection(database_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM chunk_embeddings").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM core_resources").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 0
    finally:
        connection.close()


def test_active_generation_model_embedding_rebuild_uses_core_reindex(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    store_dir = tmp_path / "store"
    database_path = _generation(store_dir, state=GenerationState.READY)
    config = MDRackConfig(paths=PathsConfig(root=".", store=str(store_dir)))
    context = click.Context(click.Command("model"), obj={"root": root, "db_path": database_path})
    called: dict[str, object] = {}

    def fake_reindex(**kwargs: object) -> SimpleNamespace:
        called.update(kwargs)
        return SimpleNamespace(
            files_seen=1,
            files_changed=1,
            files_deleted=0,
            chunks_created=2,
            errors_count=0,
            run_id="core-reindex",
        )

    monkeypatch.setattr("mdrack.cli.commands.model.run_indexer", fake_reindex)
    monkeypatch.setattr(
        "mdrack.cli.commands.model.rebuild_embeddings_in_db",
        lambda *args, **kwargs: pytest.fail("active core rebuild must not write legacy vectors"),
    )

    result = _run_switch_rebuild(
        ctx=context,
        config=config,
        switched_config=config,
        model_name="fixture-model",
        dimensions=8,
        rebuild_mode="embeddings",
    )

    assert result == {
        "performed": True,
        "mode": "embeddings",
        "files_seen": 1,
        "files_changed": 1,
        "files_deleted": 0,
        "chunks_created": 2,
        "errors_count": 0,
        "run_id": "core-reindex",
    }
    assert called["root"] == root
    assert called["config"] is config
    assert called["force_reindex"] is True


def test_ready_core_resolves_provider_free_textual_resource_similarity(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "first.md").write_text("# First\n\nShared searchable topic.\n", encoding="utf-8")
    (root / "second.md").write_text("# Second\n\nShared searchable topic.\n", encoding="utf-8")
    store_dir = tmp_path / "store"
    _generation(store_dir, state=GenerationState.READY)
    config = MDRackConfig(paths=PathsConfig(root=".", store=str(store_dir)))
    assert run_indexer(root, config, provider=FakeEmbeddingProvider(dimensions=8)).status == "success"
    storage = create_application_storage(root, config)
    assert isinstance(storage, CoreCompatibilityStorage)
    try:
        resource_ids = tuple(
            str(row[0])
            for row in storage.connection.execute(
                "SELECT resource_id FROM core_resources ORDER BY resource_id"
            ).fetchall()
        )
        result = ResourceQueryService(
            storage.resource_store,
            whole_resource_resolver=storage,
        ).find_similar_resource(resource_ids[0], scope="notes", limit=1)
    finally:
        storage.close()

    assert result.degraded is False
    assert [item.resource_id for item in result.results] == [resource_ids[1]]


def test_active_non_ready_resource_generation_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    store_dir = tmp_path / "store"
    _generation(store_dir, state=GenerationState.BUILDING)
    config = MDRackConfig(paths=PathsConfig(root=".", store=str(store_dir)))

    with pytest.raises(StoreGenerationManagerError):
        create_application_storage(root, config)
    with pytest.raises(StoreGenerationManagerError):
        run_indexer(root, config, provider=FakeEmbeddingProvider(dimensions=8))
    with pytest.raises(StoreGenerationManagerError):
        MDRackEngine(root=root, config=config)


@pytest.mark.parametrize("mode", ["text", "semantic", "hybrid"])
def test_active_generation_cli_and_engine_keep_exact_result_parity(
    tmp_path: Path,
    mode: str,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "guide.md").write_text("# Guide\n\nAlpha parity phrase.\n", encoding="utf-8")
    store_dir = tmp_path / "store"
    _generation(store_dir, state=GenerationState.READY)
    config_path = root / "mdrack.toml"
    config_path.write_text(f'[paths]\nstore = "{store_dir}"\n', encoding="utf-8")
    config = MDRackConfig(paths=PathsConfig(root=".", store=str(store_dir)))
    provider = FakeEmbeddingProvider(dimensions=1024)
    assert run_indexer(root, config, provider=provider).status == "success"

    engine = MDRackEngine(
        root=root,
        config=config,
        embedding_provider=FakeEmbeddingProvider(dimensions=1024),
    )
    if mode == "text":
        embedded = engine.search_text("Alpha", limit=5).to_dict()
    elif mode == "semantic":
        embedded = asyncio.run(engine.search_semantic("Alpha", limit=5)).to_dict()
    else:
        embedded = asyncio.run(engine.search_hybrid("Alpha", limit=5)).to_dict()
    engine.close()

    cli = CliRunner().invoke(
        main,
        [
            "--root",
            str(root),
            "--config-file",
            str(config_path),
            "search",
            "Alpha",
            "--mode",
            mode,
            "--provider",
            "fake",
            "--limit",
            "5",
        ],
    )
    assert cli.exit_code == 0, cli.output
    assert json.loads(cli.output)["data"] == embedded


@pytest.mark.parametrize("mode", ["text", "hybrid"])
def test_active_generation_keeps_invalid_text_query_error_envelope(
    tmp_path: Path,
    mode: str,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "guide.md").write_text("# Guide\n\nAlpha.\n", encoding="utf-8")
    store_dir = tmp_path / "store"
    _generation(store_dir, state=GenerationState.READY)
    config_path = root / "mdrack.toml"
    config_path.write_text(f'[paths]\nstore = "{store_dir}"\n', encoding="utf-8")
    config = MDRackConfig(paths=PathsConfig(root=".", store=str(store_dir)))
    assert run_indexer(root, config, provider=FakeEmbeddingProvider(dimensions=1024)).status == "success"

    result = CliRunner().invoke(
        main,
        [
            "--root",
            str(root),
            "--config-file",
            str(config_path),
            "search",
            "(",
            "--mode",
            mode,
            "--provider",
            "fake",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["error"] == {
        "message": "Invalid text search query",
        "code": "FTS_ERROR",
    }
    assert payload["meta"]["command"] == "search"


def test_ready_core_text_semantic_and_hybrid_match_legacy_values(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "guide.md").write_text(
        "# Guide\n\nAlpha retrieval phrase with enough surrounding words for a snippet.\n",
        encoding="utf-8",
    )
    legacy_config = MDRackConfig(
        paths=PathsConfig(root=".", store=str(tmp_path / "legacy-store"))
    )
    core_store = tmp_path / "core-store"
    _generation(core_store, state=GenerationState.READY)
    core_config = MDRackConfig(paths=PathsConfig(root=".", store=str(core_store)))
    provider = FakeEmbeddingProvider(dimensions=1024)
    assert run_indexer(root, legacy_config, provider=provider).status == "success"
    assert run_indexer(root, core_config, provider=provider).status == "success"

    async def results(config: MDRackConfig) -> tuple[dict[str, object], ...]:
        storage = create_application_storage(root, config)
        service = RetrievalService(
            storage,
            embedding_provider=FakeEmbeddingProvider(dimensions=1024),
        )
        try:
            return (
                service.search_text("Alpha", limit=5).to_dict(),
                (await service.search_semantic("Alpha", limit=5)).to_dict(),
                (await service.search_hybrid("Alpha", limit=5)).to_dict(),
            )
        finally:
            storage.close()

    legacy, ready = asyncio.run(results(legacy_config)), asyncio.run(results(core_config))
    _assert_public_results_equivalent(ready, legacy)
    text_item = ready[0]["results"][0]  # type: ignore[index]
    assert text_item["score"] < 0
    assert "<b>Alpha</b>" in text_item["content_preview"]


def test_ready_core_preserves_legacy_lexical_ties_and_plain_punctuation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    for ordinal in range(12):
        (root / f"guide-{ordinal:02d}.md").write_text(
            f"# Guide {ordinal}\n\nAlpha retrieval shared phrase.\n",
            encoding="utf-8",
        )

    legacy_config = MDRackConfig(
        paths=PathsConfig(root=".", store=str(tmp_path / "legacy-store"))
    )
    core_store = tmp_path / "core-store"
    _generation(core_store, state=GenerationState.READY)
    core_config = MDRackConfig(paths=PathsConfig(root=".", store=str(core_store)))
    provider = FakeEmbeddingProvider(dimensions=1024)
    assert run_indexer(root, legacy_config, provider=provider).status == "success"
    assert run_indexer(root, core_config, provider=provider).status == "success"

    async def results(config: MDRackConfig, query: str) -> tuple[dict[str, object], ...]:
        storage = create_application_storage(root, config)
        service = RetrievalService(
            storage,
            embedding_provider=FakeEmbeddingProvider(dimensions=1024),
        )
        try:
            return (
                service.search_text(query, limit=20).to_dict(),
                (await service.search_hybrid(query, limit=20)).to_dict(),
            )
        finally:
            storage.close()

    for query in ("Alpha", "Alpha - retrieval", "Alpha/retrieval", "Alpha.retrieval"):
        legacy = asyncio.run(results(legacy_config, query))
        ready = asyncio.run(results(core_config, query))
        _assert_public_results_equivalent(ready, legacy)

    config_path = root / "mdrack.toml"
    config_path.write_text(f'[paths]\nstore = "{core_store}"\n', encoding="utf-8")
    engine = MDRackEngine(
        root=root,
        config=core_config,
        embedding_provider=FakeEmbeddingProvider(dimensions=1024),
    )
    try:
        for query in ("Alpha - retrieval", "Alpha/retrieval", "Alpha.retrieval"):
            for mode in ("text", "hybrid"):
                if mode == "text":
                    embedded = engine.search_text(query, limit=20).to_dict()
                else:
                    embedded = asyncio.run(engine.search_hybrid(query, limit=20)).to_dict()
                cli = CliRunner().invoke(
                    main,
                    [
                        "--root",
                        str(root),
                        "--config-file",
                        str(config_path),
                        "search",
                        query,
                        "--mode",
                        mode,
                        "--provider",
                        "fake",
                        "--limit",
                        "20",
                    ],
                )
                assert cli.exit_code == 0, cli.output
                assert json.loads(cli.output)["data"] == embedded
    finally:
        engine.close()


@pytest.mark.parametrize("operation", ["replace_file", "delete_file"])
@pytest.mark.parametrize(
    "failure_boundary",
    ["core_before_commit", "before_legacy", "after_legacy"],
)
def test_ready_core_and_legacy_graphs_roll_back_together_on_projection_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    failure_boundary: str,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    note = root / "guide.md"
    note.write_text("# Guide\n\nAlpha prior graph.\n", encoding="utf-8")
    store_dir = tmp_path / "store"
    database_path = _generation(store_dir, state=GenerationState.READY)
    config = MDRackConfig(paths=PathsConfig(root=".", store=str(store_dir)))
    provider = FakeEmbeddingProvider(dimensions=1024)
    assert run_indexer(root, config, provider=provider).status == "success"

    def graph() -> tuple[tuple[object, ...], ...]:
        connection = get_connection(database_path)
        try:
            return (
                tuple(tuple(row) for row in connection.execute(
                    "SELECT resource_id, content_hash FROM core_resources ORDER BY resource_id"
                ).fetchall()),
                tuple(tuple(row) for row in connection.execute(
                    "SELECT unit_id, text_content FROM core_search_units ORDER BY unit_id"
                ).fetchall()),
                tuple(tuple(row) for row in connection.execute(
                    "SELECT logical_id, source_hash FROM files WHERE status='active' ORDER BY logical_id"
                ).fetchall()),
                tuple(tuple(row) for row in connection.execute(
                    "SELECT logical_id, content FROM chunks ORDER BY logical_id"
                ).fetchall()),
            )
        finally:
            connection.close()

    prior = graph()
    if failure_boundary == "core_before_commit":
        core_operation = "replace_resource" if operation == "replace_file" else "delete_resource"
        original_core = getattr(SQLiteResourceStore, core_operation)

        def fail_core(self: SQLiteResourceStore, *args: object, **kwargs: object) -> None:
            def hook(point: str) -> None:
                if point == "before_commit":
                    assert graph() == prior
                    raise RuntimeError("injected core projection failure")

            self.set_failure_hook(hook)
            original_core(self, *args, **kwargs)

        monkeypatch.setattr(SQLiteResourceStore, core_operation, fail_core)
        restore_owner = SQLiteResourceStore
        restore_name = core_operation
        restore_value = original_core
    else:
        original_legacy = getattr(SQLiteIndexStorage, operation)

        def fail_legacy(self: SQLiteIndexStorage, *args: object, **kwargs: object) -> None:
            if failure_boundary == "after_legacy":
                original_legacy(self, *args, **kwargs)
                assert graph() == prior
            raise RuntimeError("injected legacy projection failure")

        monkeypatch.setattr(SQLiteIndexStorage, operation, fail_legacy)
        restore_owner = SQLiteIndexStorage
        restore_name = operation
        restore_value = original_legacy
    if operation == "replace_file":
        note.write_text("# Guide\n\nBeta replacement graph.\n", encoding="utf-8")
    else:
        note.unlink()
    failed = run_indexer(root, config, provider=provider)
    assert failed.status == "failed"
    assert graph() == prior

    monkeypatch.setattr(restore_owner, restore_name, restore_value)
    succeeded = run_indexer(root, config, provider=provider)
    assert succeeded.status == "success"
    current = graph()
    if operation == "replace_file":
        assert current != prior
        assert any("Beta replacement graph" in str(row) for row in current[1])
        assert any("Beta replacement graph" in str(row) for row in current[3])
    else:
        assert current == ((), (), (), ())


def test_rebuild_fts_targets_only_the_ready_active_generation(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "guide.md").write_text("# Guide\n\nAlpha active FTS.\n", encoding="utf-8")
    store_dir = tmp_path / "store"
    database_path = _generation(store_dir, state=GenerationState.READY)
    config_path = root / "mdrack.toml"
    config_path.write_text(f'[paths]\nstore = "{store_dir}"\n', encoding="utf-8")
    config = MDRackConfig(paths=PathsConfig(root=".", store=str(store_dir)))
    assert run_indexer(root, config, provider=FakeEmbeddingProvider(dimensions=1024)).status == "success"
    connection = get_connection(database_path)
    connection.execute("DELETE FROM core_search_units_fts")
    connection.commit()
    expected = connection.execute("SELECT COUNT(*) FROM core_search_units").fetchone()[0]
    connection.close()

    result = CliRunner().invoke(
        main,
        ["--root", str(root), "--config-file", str(config_path), "rebuild", "fts"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["data"] == {"fts_count": expected, "chunk_count": expected}
    assert not (store_dir / "knowledge.db").exists()
    connection = get_connection(database_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM core_search_units_fts").fetchone()[0] == expected
    finally:
        connection.close()


def test_active_generation_embedding_rebuild_propagates_indexer_failure(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "bad.md").write_bytes(b"\xff\xfe")
    store_dir = tmp_path / "store"
    _generation(store_dir, state=GenerationState.READY)
    config_path = root / "mdrack.toml"
    config_path.write_text(f'[paths]\nstore = "{store_dir}"\n', encoding="utf-8")

    result = CliRunner().invoke(
        main,
        [
            "--root",
            str(root),
            "--config-file",
            str(config_path),
            "rebuild",
            "embeddings",
            "--provider",
            "fake",
        ],
    )
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "FILE_DECODE_ERROR"
    assert payload["error"]["details"] == {
        "embedded_count": 0,
        "total_chunks": 0,
        "profile": "default",
        "provider": "fake",
        "status": "failed",
        "error_codes": ["FILE_DECODE_ERROR"],
    }


def test_active_generation_embedding_rebuild_propagates_partial_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    store_dir = tmp_path / "store"
    _generation(store_dir, state=GenerationState.READY)
    config_path = root / "mdrack.toml"
    config_path.write_text(f'[paths]\nstore = "{store_dir}"\n', encoding="utf-8")
    monkeypatch.setattr(
        "mdrack.cli.commands.rebuild.run_indexer",
        lambda *args, **kwargs: IndexerResult(
            run_id="safe-run-id",
            chunks_created=2,
            status="partial_success",
            files_indexed=1,
            files_failed=1,
            error_codes=("FILE_INDEX_ERROR",),
        ),
    )

    result = CliRunner().invoke(
        main,
        [
            "--root",
            str(root),
            "--config-file",
            str(config_path),
            "rebuild",
            "embeddings",
            "--provider",
            "fake",
        ],
    )
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "FILE_INDEX_ERROR"
    assert payload["error"]["details"]["status"] == "partial_success"
    assert payload["error"]["details"]["error_codes"] == ["FILE_INDEX_ERROR"]
