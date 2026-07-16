"""CLI and embedded facade parity for the shared retrieval service."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from mdrack.cli import main
from mdrack.config.models import MDRackConfig, PathsConfig
from mdrack.embeddings.fake import FakeEmbeddingProvider
from mdrack.embeddings.runtime import embedding_profile_from_config
from mdrack.public_api import MDRackEngine
from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.migrations import apply_migrations

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "src" / "mdrack" / "storage" / "sqlite" / "migrations"


def _seed_database(root: Path) -> None:
    store = root / ".mdrack"
    store.mkdir()
    connection = get_connection(store / "knowledge.db")
    apply_migrations(connection, MIGRATIONS_DIR)
    provider = FakeEmbeddingProvider(dimensions=1024)
    config = MDRackConfig(paths=PathsConfig(root=".", store=str(store)))
    profile = embedding_profile_from_config(config, provider, "default")
    content = "Python retrieval parity"
    vector = provider._text_to_vector(content)
    connection.execute(
        "INSERT INTO embedding_profiles (name, model, dimensions, fingerprint) VALUES (?, ?, ?, ?)",
        ("default", "fake", 1024, profile.fingerprint),
    )
    connection.execute(
        "INSERT INTO files (id, logical_id, root_id, relative_path, source_hash, indexed_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("file-record", "file_logical", "root", "docs/parity.md", "hash", "2026-01-01T00:00:00Z"),
    )
    connection.execute(
        "INSERT INTO chunks "
        "(id, logical_id, file_id, content, content_type, chunk_index, heading_path, "
        "start_line, end_line, block_logical_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("record", "chunk_logical", "file-record", content, "text", 0, json.dumps(["Parity"]), 2, 3, "block"),
    )
    connection.execute(
        "INSERT INTO chunks_fts (chunk_id, content, content_type, heading_path) VALUES (?, ?, ?, ?)",
        ("record", content, "text", "Parity"),
    )
    connection.execute(
        "INSERT INTO chunk_embeddings "
        "(chunk_id, profile_name, embedding, embedded_at, profile_fingerprint) VALUES (?, ?, ?, ?, ?)",
        (
            "record",
            "default",
            json.dumps(vector).encode("utf-8"),
            "2026-01-01T00:00:00Z",
            profile.fingerprint,
        ),
    )
    connection.commit()
    connection.close()


@pytest.mark.parametrize("mode", ["text", "semantic", "hybrid"])
def test_cli_and_embedded_results_are_byte_for_byte_equivalent(tmp_path: Path, mode: str) -> None:
    _seed_database(tmp_path)
    config = MDRackConfig(paths=PathsConfig(root=".", store=str(tmp_path / ".mdrack")))
    engine = MDRackEngine(
        root=tmp_path,
        config=config,
        embedding_provider=FakeEmbeddingProvider(dimensions=1024),
    )

    if mode == "text":
        embedded = engine.search_text("Python", limit=10).to_dict()
    elif mode == "semantic":
        embedded = asyncio.run(engine.search_semantic("Python", limit=10)).to_dict()
    else:
        embedded = asyncio.run(engine.search_hybrid("Python", limit=10, reranker=None)).to_dict()
    cli = CliRunner().invoke(
        main,
        ["--root", str(tmp_path), "search", "Python", "--mode", mode, "--provider", "fake", "--limit", "10"],
    )
    engine.close()

    assert cli.exit_code == 0, cli.output
    payload = json.loads(cli.output)
    assert payload["data"] == embedded
    assert embedded["results"][0]["heading_path"] == ["Parity"]
    assert embedded["results"][0]["heading_path"] == embedded["results"][0]["source_locator"]["heading_path"]
