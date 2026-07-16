"""Regression matrix for production embedding truthfulness paths."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from click.testing import CliRunner

from mdrack.adapters.sqlite.index_storage import SQLiteIndexStorage
from mdrack.application.retrieval import RetrievalService
from mdrack.cli import main
from mdrack.cli.commands.model import _build_switched_config
from mdrack.cli.commands.rebuild import _profile_from_provider
from mdrack.config.models import EmbeddingConfig, MDRackConfig
from mdrack.domain.profiles import EmbeddingProfile
from mdrack.embeddings.fake import FakeEmbeddingProvider
from mdrack.embeddings.lmstudio import LMStudioProvider
from mdrack.embeddings.runtime import create_embedding_provider, embedding_profile_from_config
from mdrack.public_api import MDRackEngine
from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.migrations import apply_migrations, get_migrations_dir
from mdrack.storage.sqlite.vector import VectorIndex


def _config(**overrides: object) -> MDRackConfig:
    values = {
        "model": "qwen3-embedding-0.6b",
        "dimensions": 2,
        "requested_dimensions": None,
        "dimensions_capability": "not_tested",
        "instruction_profile": "retrieval-query-v1",
        "profile_schema_version": 1,
    }
    values.update(overrides)
    return MDRackConfig(embedding=EmbeddingConfig(**values))


def test_runtime_factory_wires_validated_dimension_evidence() -> None:
    provider = create_embedding_provider(
        "lmstudio",
        _config(requested_dimensions=2, dimensions_capability="tested"),
    )

    assert isinstance(provider, LMStudioProvider)
    assert provider.requested_dimensions == 2
    assert provider._dimensions_capability == "tested"


def test_model_switch_resets_stale_dimension_evidence() -> None:
    switched = _build_switched_config(
        _config(requested_dimensions=2, dimensions_capability="tested"),
        "qwen3-embedding-4b",
        2560,
    )

    assert switched.embedding.requested_dimensions is None
    assert switched.embedding.dimensions_capability == "not_tested"


def test_complete_profile_builder_covers_config_identity_matrix() -> None:
    provider = FakeEmbeddingProvider(dimensions=2)
    baseline = embedding_profile_from_config(_config(), provider, "default")

    assert baseline.instruction_profile == "retrieval-query-v1"
    assert baseline.schema_version == 1
    assert embedding_profile_from_config(
        _config(instruction_profile="retrieval-query-v2"), provider, "default"
    ).fingerprint != baseline.fingerprint
    assert embedding_profile_from_config(
        _config(profile_schema_version=2), provider, "default"
    ).fingerprint != baseline.fingerprint

    rebuilt = _profile_from_provider(
        "default",
        provider,
        _config(instruction_profile="rebuild-v2", profile_schema_version=3),
    )
    assert rebuilt.instruction_profile == "rebuild-v2"
    assert rebuilt.schema_version == 3


def _seed_vector(db_path: Path, profile: EmbeddingProfile) -> SQLiteIndexStorage:
    connection = get_connection(db_path)
    apply_migrations(connection, get_migrations_dir())
    connection.execute(
        "INSERT INTO files (id, relative_path, source_hash, indexed_at) VALUES (?, ?, ?, ?)",
        ("file", "safe.md", "hash", "2026-01-01T00:00:00Z"),
    )
    connection.execute(
        "INSERT INTO chunks (id, logical_id, file_id, content, content_type, chunk_index) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("chunk", "logical", "file", "safe", "text", 0),
    )
    connection.execute(
        """
        INSERT INTO embedding_profiles (
            name, model, dimensions, fingerprint, provider, runtime, model_key,
            model_family, quantization, query_instruction_hash,
            normalization_mode, endpoint_family
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            profile.name,
            profile.model_key,
            profile.output_dimensions,
            profile.fingerprint,
            profile.provider,
            profile.runtime,
            profile.model_key,
            profile.model_family,
            profile.quantization,
            profile.query_instruction_hash,
            profile.normalization_mode,
            profile.endpoint_family,
        ),
    )
    VectorIndex(connection).upsert("chunk", profile.name, [0.5, 0.5])
    connection.commit()
    return SQLiteIndexStorage(connection)


def test_retrieval_rejects_same_dimension_cross_profile_with_stable_reason(tmp_path: Path) -> None:
    provider = FakeEmbeddingProvider(dimensions=2)
    stored = embedding_profile_from_config(_config(), provider, "default")
    requested = embedding_profile_from_config(
        _config(instruction_profile="retrieval-query-v2"), provider, "default"
    )
    storage = _seed_vector(tmp_path / "knowledge.db", stored)
    service = RetrievalService(
        storage,
        embedding_provider=provider,
        profile="default",
        profile_fingerprint=requested.fingerprint,
    )

    result = asyncio.run(service.search_semantic("safe", limit=1))

    assert result.results == ()
    assert result.degraded is True
    assert result.degraded_reason == "incompatible_embedding_profile"
    storage.close()


def test_cli_and_embedded_surfaces_preserve_incompatible_profile_reason(tmp_path: Path) -> None:
    provider = FakeEmbeddingProvider(dimensions=2)
    stored_config = _config()
    requested_config = _config(instruction_profile="retrieval-query-v2")
    stored = embedding_profile_from_config(stored_config, provider, "default")
    store = tmp_path / ".mdrack"
    store.mkdir()
    storage = _seed_vector(store / "knowledge.db", stored)

    engine = MDRackEngine(
        root=tmp_path,
        config=requested_config,
        embedding_provider=provider,
        storage=storage,
    )
    embedded = asyncio.run(engine.search_semantic("safe", limit=1))
    engine.close()

    (store / "config.toml").write_text(
        "\n".join(
            [
                "[embedding]",
                'model = "qwen3-embedding-0.6b"',
                "dimensions = 2",
                'instruction_profile = "retrieval-query-v2"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    cli = CliRunner().invoke(
        main,
        ["--root", str(tmp_path), "search", "safe", "--mode", "semantic", "--provider", "fake"],
    )
    payload = json.loads(cli.output)

    assert embedded.degraded_reason == "incompatible_embedding_profile"
    assert payload["ok"] is False
    assert payload["error"]["details"] == {"reason": "incompatible_embedding_profile"}
