"""Embedding-profile truthfulness paths through the canonical catalog."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from click.testing import CliRunner

from mdrack.cli import main
from mdrack.cli.commands.model import _build_switched_config
from mdrack.cli.commands.rebuild import _profile_from_provider
from mdrack.config.models import EmbeddingConfig, MDRackConfig
from mdrack.embeddings.fake import FakeEmbeddingProvider
from mdrack.embeddings.lmstudio import LMStudioProvider
from mdrack.embeddings.runtime import create_embedding_provider, embedding_profile_from_config
from mdrack.indexing.indexer import run_indexer
from mdrack.public_api import MDRackEngine


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


def _index_catalog(root: Path, config: MDRackConfig) -> None:
    (root / "safe.md").write_text("# Safe\n\nSafe retrieval text.\n", encoding="utf-8")
    result = run_indexer(root, config, provider=FakeEmbeddingProvider(dimensions=2))
    assert result.status == "success"
    assert (root / ".mdrack" / "catalog.sqlite3").is_file()


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


def test_retrieval_rejects_same_dimension_cross_profile_with_stable_reason(tmp_path: Path) -> None:
    stored_config = _config()
    requested_config = _config(instruction_profile="retrieval-query-v2")
    _index_catalog(tmp_path, stored_config)
    engine = MDRackEngine(
        root=tmp_path,
        config=requested_config,
        embedding_provider=FakeEmbeddingProvider(dimensions=2),
    )
    try:
        result = asyncio.run(engine.search_semantic("safe", limit=1))
    finally:
        engine.close()

    assert result.results == ()
    assert result.degraded is True
    assert result.degraded_reason == "incompatible_embedding_profile"


def test_cli_and_embedded_surfaces_preserve_incompatible_profile_reason(tmp_path: Path) -> None:
    stored_config = _config()
    requested_config = _config(instruction_profile="retrieval-query-v2")
    _index_catalog(tmp_path, stored_config)
    engine = MDRackEngine(
        root=tmp_path,
        config=requested_config,
        embedding_provider=FakeEmbeddingProvider(dimensions=2),
    )
    try:
        embedded = asyncio.run(engine.search_semantic("safe", limit=1))
    finally:
        engine.close()

    config_path = tmp_path / ".mdrack" / "config.toml"
    config_path.write_text(
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
    assert payload["error"] == {
        "message": "Semantic search failed",
        "code": "EMBEDDING_ERROR",
        "details": {"reason": "incompatible_embedding_profile"},
    }
