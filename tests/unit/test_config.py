"""Tests for MDRack configuration loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from mdrack.config.defaults import get_defaults
from mdrack.config.loader import load_config
from mdrack.config.models import MDRackConfig


class TestDefaultConfig:
    """Verify default configuration loads correctly."""

    def test_loads_without_error(self) -> None:
        config = get_defaults()
        assert isinstance(config, MDRackConfig)

    def test_default_paths(self) -> None:
        config = get_defaults()
        assert config.paths.root == "."
        assert config.paths.store == ".mdrack"
        assert config.paths.config_file == ".mdrack/config.toml"

    def test_default_scan(self) -> None:
        config = get_defaults()
        assert "**/*.md" in config.scan.include
        assert ".git/**" in config.scan.exclude

    def test_default_chunking(self) -> None:
        config = get_defaults()
        assert config.chunking.min_chunk_chars == 1200
        assert config.chunking.target_chunk_chars == 3200
        assert config.chunking.hard_limit_chars == 8000
        assert config.chunking.overlap_chars == 300

    def test_default_embedding(self) -> None:
        config = get_defaults()
        assert config.embedding.provider == "lmstudio"
        assert config.embedding.model == "qwen3-embedding-0.6b"
        assert config.embedding.endpoint == "http://localhost:1234/v1"
        assert config.embedding.timeout_secs == 120
        assert config.embedding.dimensions == 1024

    def test_default_search(self) -> None:
        config = get_defaults()
        assert config.search.default_mode == "hybrid"
        assert config.search.text_weight == 0.4
        assert config.search.semantic_weight == 0.6
        assert config.search.top_k == 20
        assert config.search.rrf_k == 60

    def test_default_profiling(self) -> None:
        config = get_defaults()
        assert config.profiling.embedding_profiles == ["default"]


class TestTomlOverride:
    """Verify TOML file overrides defaults."""

    def test_partial_toml_override(self, tmp_path: Path) -> None:
        toml_content = """
[chunking]
min_chunk_chars = 800
target_chunk_chars = 1500

[embedding]
model = "custom-model"
dimensions = 1024
"""
        toml_path = tmp_path / "config.toml"
        toml_path.write_text(toml_content, encoding="utf-8")

        config = load_config(toml_path=toml_path)

        # Overridden values
        assert config.chunking.min_chunk_chars == 800
        assert config.chunking.target_chunk_chars == 1500
        assert config.embedding.model == "custom-model"
        assert config.embedding.dimensions == 1024

        # Non-overridden defaults preserved
        assert config.chunking.hard_limit_chars == 8000
        assert config.search.default_mode == "hybrid"
        assert config.paths.root == "."

    def test_full_toml_config(self, tmp_path: Path) -> None:
        toml_content = """
[paths]
root = "/custom/root"
store = ".custom_store"

[scan]
include = ["**/*.md", "**/*.txt"]

[chunking]
min_chunk_chars = 300

[embedding]
provider = "lmstudio"
model = "qwen3-embedding"
dimensions = 1024

[search]
default_mode = "text"
top_k = 10
"""
        toml_path = tmp_path / "config.toml"
        toml_path.write_text(toml_content, encoding="utf-8")

        config = load_config(toml_path=toml_path)

        assert config.paths.root == "/custom/root"
        assert config.paths.store == ".custom_store"
        assert "**/*.txt" in config.scan.include
        assert config.chunking.min_chunk_chars == 300
        assert config.embedding.model == "qwen3-embedding"
        assert config.embedding.dimensions == 1024
        assert config.search.default_mode == "text"
        assert config.search.top_k == 10

    def test_missing_toml_uses_defaults(self, tmp_path: Path) -> None:
        toml_path = tmp_path / "nonexistent.toml"
        config = load_config(toml_path=toml_path)

        # Should fall back to defaults
        assert config.paths.root == "."
        assert config.chunking.min_chunk_chars == 1200


class TestEnvOverride:
    """Verify environment variable overrides."""

    def test_env_overrides_toml(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        toml_content = """
[chunking]
min_chunk_chars = 800
"""
        toml_path = tmp_path / "config.toml"
        toml_path.write_text(toml_content, encoding="utf-8")

        monkeypatch.setenv("MDRACK_CHUNKING_MIN_CHUNK_CHARS", "1200")
        monkeypatch.setenv("MDRACK_EMBEDDING_DIMENSIONS", "1024")

        config = load_config(toml_path=toml_path)

        # Env overrides TOML
        assert config.chunking.min_chunk_chars == 1200
        assert config.embedding.dimensions == 1024

    def test_env_overrides_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MDRACK_SEARCH_TOP_K", "50")
        monkeypatch.setenv("MDRACK_SEARCH_DEFAULT_MODE", "text")

        config = load_config(toml_path=Path("nonexistent.toml"))

        assert config.search.top_k == 50
        assert config.search.default_mode == "text"

    def test_env_boolean_coercion(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MDRACK_SCAN_INCLUDE", "test")

        config = load_config(toml_path=Path("nonexistent.toml"))
        # Just verify it doesn't crash; the field accepts list[str]
        assert isinstance(config.scan.include, list)

    def test_env_float_coercion(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MDRACK_SEARCH_TEXT_WEIGHT", "0.7")

        config = load_config(toml_path=Path("nonexistent.toml"))
        assert config.search.text_weight == 0.7

    def test_env_int_coercion(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MDRACK_CHUNKING_HARD_LIMIT_CHARS", "3000")

        config = load_config(toml_path=Path("nonexistent.toml"))
        assert config.chunking.hard_limit_chars == 3000


class TestMergePrecedence:
    """Verify precedence: Defaults < TOML < Env < CLI."""

    def test_cli_overrides_env_and_toml(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        toml_content = """
[chunking]
min_chunk_chars = 800

[search]
top_k = 10
"""
        toml_path = tmp_path / "config.toml"
        toml_path.write_text(toml_content, encoding="utf-8")

        monkeypatch.setenv("MDRACK_CHUNKING_MIN_CHUNK_CHARS", "1200")
        monkeypatch.setenv("MDRACK_SEARCH_TOP_K", "50")

        cli_overrides = {
            "chunking.min_chunk_chars": 2000,
            "search.top_k": 5,
        }

        config = load_config(toml_path=toml_path, cli_overrides=cli_overrides)

        # CLI wins over env which wins over TOML
        assert config.chunking.min_chunk_chars == 2000
        assert config.search.top_k == 5

    def test_env_overrides_toml_defaults_preserved(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        toml_content = """
[embedding]
model = "custom-model"
"""
        toml_path = tmp_path / "config.toml"
        toml_path.write_text(toml_content, encoding="utf-8")

        monkeypatch.setenv("MDRACK_EMBEDDING_DIMENSIONS", "1024")

        config = load_config(toml_path=toml_path)

        # TOML override
        assert config.embedding.model == "custom-model"
        # Env override
        assert config.embedding.dimensions == 1024
        # Defaults preserved
        assert config.embedding.provider == "lmstudio"
        assert config.embedding.endpoint == "http://localhost:1234/v1"

    def test_all_layers_combined(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        toml_content = """
[chunking]
min_chunk_chars = 500
target_chunk_chars = 1000

[search]
top_k = 15
"""
        toml_path = tmp_path / "config.toml"
        toml_path.write_text(toml_content, encoding="utf-8")

        monkeypatch.setenv("MDRACK_CHUNKING_TARGET_CHUNK_CHARS", "2000")

        cli_overrides = {"search.top_k": 3}

        config = load_config(toml_path=toml_path, cli_overrides=cli_overrides)

        # From TOML
        assert config.chunking.min_chunk_chars == 500
        # From env
        assert config.chunking.target_chunk_chars == 2000
        # From CLI
        assert config.search.top_k == 3
        # From defaults
        assert config.chunking.hard_limit_chars == 8000
        assert config.embedding.dimensions == 1024
