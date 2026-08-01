"""CLI semantic and hybrid search contracts over the canonical catalog."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

from click.testing import CliRunner

from mdrack.cli import main
from mdrack.domain.indexing import SourceLocator
from mdrack.domain.retrieval import RetrievalItem, RetrievalResult
from mdrack.embeddings.fake import FakeEmbeddingProvider


def _write_fixture(root: Path) -> None:
    (root / "python.md").write_text(
        "# Python\n\nPython is a high-level programming language.\n",
        encoding="utf-8",
    )
    (root / "javascript.md").write_text(
        "# JavaScript\n\nJavaScript is a scripting language for the web.\n",
        encoding="utf-8",
    )


def _index_fixture(root: Path) -> None:
    _write_fixture(root)
    result = CliRunner().invoke(main, ["--root", str(root), "scan", "--provider", "fake"])
    assert result.exit_code == 0, result.output
    assert (root / ".mdrack" / "catalog.sqlite3").is_file()


def _search(root: Path, query: str, mode: str) -> tuple[int, dict[str, object]]:
    result = CliRunner().invoke(
        main,
        ["--root", str(root), "search", query, "--mode", mode, "--provider", "fake"],
    )
    return result.exit_code, json.loads(result.output)


def test_hybrid_zero_semantic_weight_does_not_create_provider(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _index_fixture(tmp_path)
    config_path = tmp_path / "config.toml"
    config_path.write_text("[search]\ntext_weight = 1.0\nsemantic_weight = 0.0\n", encoding="utf-8")

    def forbidden_provider(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("semantic provider must not be created")

    monkeypatch.setattr("mdrack.cli.commands.search.create_embedding_provider", forbidden_provider)
    result = CliRunner().invoke(
        main,
        [
            "--root",
            str(tmp_path),
            "--config-file",
            str(config_path),
            "search",
            "Python",
            "--mode",
            "hybrid",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["ok"] is True


def test_semantic_search_returns_valid_json(tmp_path: Path) -> None:
    _index_fixture(tmp_path)

    exit_code, payload = _search(tmp_path, "Python", "semantic")

    assert exit_code == 0
    assert payload["ok"] is True
    data = payload["data"]
    assert isinstance(data, dict)
    assert data["mode"] == "semantic"
    assert data["results"]


def test_semantic_search_output_format_has_portable_locator(tmp_path: Path) -> None:
    _index_fixture(tmp_path)

    exit_code, payload = _search(tmp_path, "Python", "semantic")

    assert exit_code == 0
    data = payload["data"]
    assert isinstance(data, dict)
    assert set(data) == {
        "query",
        "mode",
        "results",
        "total_count",
        "degraded",
        "degraded_reason",
    }
    results = data["results"]
    assert isinstance(results, list) and results
    item = results[0]
    assert isinstance(item, dict)
    assert item["logical_id"] == item["chunk_id"]
    assert isinstance(item["content_preview"], str)
    locator = item["source_locator"]
    assert isinstance(locator, dict)
    assert locator["relative_path"].endswith(".md")


def test_semantic_search_top_result_is_relevant(tmp_path: Path, monkeypatch) -> None:
    class KeywordProvider(FakeEmbeddingProvider):
        def _text_to_vector(self, text: str) -> list[float]:
            return [1.0, 0.0] if "python" in text.casefold() else [0.0, 1.0]

    _write_fixture(tmp_path)
    monkeypatch.setattr(
        "mdrack.cli.commands.scan._create_provider",
        lambda *_args, **_kwargs: KeywordProvider(dimensions=2),
    )
    monkeypatch.setattr(
        "mdrack.cli.commands.search.create_embedding_provider",
        lambda *_args, **_kwargs: KeywordProvider(dimensions=2),
    )
    scan = CliRunner().invoke(main, ["--root", str(tmp_path), "scan", "--provider", "fake"])
    assert scan.exit_code == 0, scan.output

    exit_code, payload = _search(tmp_path, "Python programming language", "semantic")

    assert exit_code == 0
    data = payload["data"]
    assert isinstance(data, dict)
    results = data["results"]
    assert isinstance(results, list) and results
    assert results[0]["file"] == "python.md"
    assert isinstance(results[0]["semantic_score"], float)


def test_semantic_search_with_empty_catalog_returns_safe_embedding_error(tmp_path: Path) -> None:
    scan = CliRunner().invoke(main, ["--root", str(tmp_path), "scan", "--provider", "fake"])
    assert scan.exit_code == 0, scan.output

    exit_code, payload = _search(tmp_path, "Python", "semantic")

    assert exit_code == 0
    assert payload["ok"] is False
    assert payload["error"] == {
        "message": "Semantic search failed",
        "code": "EMBEDDING_ERROR",
        "details": {"reason": "incompatible_embedding_profile"},
    }


def test_semantic_search_no_catalog(tmp_path: Path) -> None:
    exit_code, payload = _search(tmp_path, "Python", "semantic")

    assert exit_code == 1
    assert payload["ok"] is False
    assert "not found" in payload["error"]["message"].lower()


def test_hybrid_search_reports_degraded_mode(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _index_fixture(tmp_path)
    locator = SourceLocator("root", "python.md", 1, 2, (), "block", "chunk-001")
    monkeypatch.setattr(
        "mdrack.cli.commands.search.RetrievalService.search_hybrid",
        AsyncMock(
            return_value=RetrievalResult(
                query="Python",
                mode="hybrid",
                results=(
                    RetrievalItem(
                        logical_id="chunk-001",
                        score=0.9,
                        source_locator=locator,
                        text_rank=1,
                        semantic_rank=None,
                        text_score=0.9,
                        semantic_score=None,
                        content_preview="Python is a high-level programming language.",
                    ),
                ),
                total_count=1,
                degraded=True,
                degraded_reason="embedding_provider_error",
            )
        ),
    )

    exit_code, payload = _search(tmp_path, "Python", "hybrid")

    assert exit_code == 0
    assert payload["ok"] is True
    data = payload["data"]
    assert isinstance(data, dict)
    assert data["degraded"] is True
    assert data["degraded_reason"] == "embedding_provider_error"
