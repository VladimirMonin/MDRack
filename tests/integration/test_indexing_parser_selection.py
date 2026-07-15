"""A/B selection contracts for legacy and structural parser pipelines."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from mdrack.adapters.sqlite.index_storage import create_sqlite_index_storage
from mdrack.application.indexing import IndexingService
from mdrack.config.loader import load_config
from mdrack.config.models import MDRackConfig, ParsingConfig, PathsConfig
from mdrack.embeddings.fake import FakeEmbeddingProvider
from mdrack.storage.sqlite.connection import get_connection


def _config(tmp_path: Path, backend: Literal["markdown_it", "legacy"]) -> MDRackConfig:
    return MDRackConfig(
        paths=PathsConfig(root=".", store=str(tmp_path / f".{backend}")),
        parsing=ParsingConfig(backend=backend),
    )


def _index_run_identity(root: Path, config: MDRackConfig) -> tuple[str, str]:
    storage = create_sqlite_index_storage(root, config)
    service = IndexingService(root, config, storage, provider=FakeEmbeddingProvider(dimensions=8))
    result = service.scan(force_reindex=True)
    service.close()
    assert result.status == "success"
    connection = get_connection(Path(config.paths.store) / "knowledge.db")
    try:
        row = connection.execute(
            "SELECT parser_name, chunk_strategy_name FROM index_runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        return row["parser_name"], row["chunk_strategy_name"]
    finally:
        connection.close()


def test_default_pipeline_is_markdown_it_structural(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "note.md").write_text("# Title\n\nBody.\n", encoding="utf-8")
    config = _config(tmp_path, "markdown_it")
    assert _index_run_identity(root, config) == ("markdown_it", "structural_blocks")


def test_legacy_pipeline_remains_selectable(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "note.md").write_text("# Title\n\nBody.\n", encoding="utf-8")
    config = _config(tmp_path, "legacy")
    assert _index_run_identity(root, config) == ("legacy_markdown", "buffered_blocks")


def test_parser_backend_can_be_selected_from_toml(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('[parsing]\nbackend = "legacy"\n', encoding="utf-8")
    assert load_config(toml_path=config_path).parsing.backend == "legacy"
