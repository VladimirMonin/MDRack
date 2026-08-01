"""E2E smoke coverage for the canonical catalog through the public CLI."""

from __future__ import annotations

import json
import shutil
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest
from click.testing import CliRunner

from mdrack.cli import main

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "markdown"
FIXTURE_FILE_NAMES = ("simple_headings.md", "frontmatter.md", "mixed_content.md", "code_blocks.md")


@pytest.fixture(scope="module")
def seeded_catalog() -> Generator[Path, None, None]:
    root = Path(tempfile.mkdtemp(prefix="mdrack_e2e_smoke_"))
    for filename in FIXTURE_FILE_NAMES:
        shutil.copy2(FIXTURES_DIR / filename, root / filename)
    result = CliRunner().invoke(main, ["--root", str(root), "scan", "--provider", "fake"])
    assert result.exit_code == 0, result.output
    assert (root / ".mdrack" / "catalog.sqlite3").is_file()
    yield root
    shutil.rmtree(root, ignore_errors=True)


def _search(root: Path, query: str, mode: str, *, limit: int = 10) -> dict[str, object]:
    args = [
        "--root",
        str(root),
        "search",
        query,
        "--mode",
        mode,
        "--limit",
        str(limit),
    ]
    if mode != "text":
        args.extend(("--provider", "fake"))
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    data = payload["data"]
    assert isinstance(data, dict)
    return data


class TestTextSearchSmoke:
    def test_search_finds_markdown_body_content(self, seeded_catalog: Path) -> None:
        assert _search(seeded_catalog, "Final", "text")["results"]

    def test_search_finds_code_adjacent_prose(self, seeded_catalog: Path) -> None:
        assert _search(seeded_catalog, "Code", "text")["results"]

    def test_search_finds_frontmatter_document_body(self, seeded_catalog: Path) -> None:
        assert _search(seeded_catalog, "Body", "text")["results"]

    def test_search_finds_mixed_document_body(self, seeded_catalog: Path) -> None:
        assert _search(seeded_catalog, "Final", "text")["results"]

    def test_search_results_have_portable_provenance(self, seeded_catalog: Path) -> None:
        data = _search(seeded_catalog, "Final", "text")
        results = data["results"]
        assert isinstance(results, list) and results
        for item in results:
            assert isinstance(item, dict)
            assert item["logical_id"] == item["chunk_id"]
            assert isinstance(item["score"], float)
            locator = item["source_locator"]
            assert isinstance(locator, dict)
            assert locator["relative_path"].endswith(".md")

    def test_search_respects_limit(self, seeded_catalog: Path) -> None:
        data = _search(seeded_catalog, "Fourth", "text", limit=2)
        results = data["results"]
        assert isinstance(results, list)
        assert len(results) <= 2


class TestSemanticSearchSmoke:
    def test_semantic_returns_results(self, seeded_catalog: Path) -> None:
        assert _search(seeded_catalog, "code examples", "semantic")["results"]

    def test_semantic_has_chunk_ids(self, seeded_catalog: Path) -> None:
        data = _search(seeded_catalog, "heading", "semantic", limit=5)
        results = data["results"]
        assert isinstance(results, list) and results
        for item in results:
            assert isinstance(item, dict)
            assert item["logical_id"] == item["chunk_id"]
            assert item["file"].endswith(".md")

    def test_semantic_no_degradation(self, seeded_catalog: Path) -> None:
        data = _search(seeded_catalog, "content", "semantic", limit=5)
        assert data["degraded"] is False
        assert data["degraded_reason"] is None


class TestHybridSearchSmoke:
    def test_hybrid_returns_results(self, seeded_catalog: Path) -> None:
        assert _search(seeded_catalog, "code examples", "hybrid")["results"]

    def test_hybrid_has_score_fields(self, seeded_catalog: Path) -> None:
        data = _search(seeded_catalog, "Subtitle", "hybrid", limit=5)
        results = data["results"]
        assert isinstance(results, list) and results
        for item in results:
            assert isinstance(item, dict)
            assert isinstance(item["score"], float)
            assert item["score"] >= 0

    def test_hybrid_has_provenance(self, seeded_catalog: Path) -> None:
        data = _search(seeded_catalog, "Code", "hybrid", limit=5)
        results = data["results"]
        assert isinstance(results, list) and results
        for item in results:
            assert isinstance(item, dict)
            locator = item["source_locator"]
            assert isinstance(locator, dict)
            assert locator["relative_path"].endswith(".md")

    def test_hybrid_respects_limit(self, seeded_catalog: Path) -> None:
        data = _search(seeded_catalog, "Fourth", "hybrid", limit=2)
        results = data["results"]
        assert isinstance(results, list)
        assert len(results) <= 2
