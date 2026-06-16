"""E2E smoke test: index → search with fake embeddings → verify results."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from mdrack.config.defaults import get_defaults
from mdrack.config.models import MDRackConfig, PathsConfig, ScanConfig
from mdrack.embeddings.fake import FakeEmbeddingProvider
from mdrack.indexing.indexer import run_indexer
from mdrack.search.hybrid import HybridSearchResult, hybrid_search
from mdrack.search.semantic import SemanticSearchResult, semantic_search
from mdrack.search.text import TextSearchResult, text_search
from mdrack.storage.sqlite.connection import get_connection

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "markdown"

FIXTURE_FILE_NAMES = [
    "simple_headings.md",
    "frontmatter.md",
    "mixed_content.md",
    "code_blocks.md",
]


@pytest.fixture(scope="module")
def seeded_store_with_fake() -> tuple:
    """Create a temp store, copy fixture files, index with fake provider."""
    tmp_root = Path(tempfile.mkdtemp(prefix="mdrack_e2e_smoke_"))
    store_dir = tmp_root / ".mdrack"

    for fname in FIXTURE_FILE_NAMES:
        src = FIXTURES_DIR / fname
        if src.is_file():
            shutil.copy2(str(src), str(tmp_root / fname))

    config = MDRackConfig(
        paths=PathsConfig(store=str(store_dir)),
        scan=ScanConfig(
            include=["**/*.md"],
            exclude=["node_modules/**", ".git/**", ".venv/**"],
        ),
        chunking=get_defaults().chunking,
        embedding=get_defaults().embedding,
        search=get_defaults().search,
        profiling=get_defaults().profiling,
    )

    provider = FakeEmbeddingProvider(dimensions=128, provider_name="fake")

    result = run_indexer(root=tmp_root, config=config, provider=provider, profile="default")
    assert result.files_seen >= 4
    assert result.chunks_created > 0

    db_path = store_dir / "knowledge.db"
    conn = get_connection(db_path)

    yield conn, provider, config, tmp_root

    conn.close()
    shutil.rmtree(tmp_root, ignore_errors=True)


class TestTextSearchSmoke:
    """Smoke tests for text search on indexed fixtures."""

    def test_search_finds_heading_content(self, seeded_store_with_fake: tuple) -> None:
        conn, _, _, _ = seeded_store_with_fake
        result = text_search(conn, "Subtitle", limit=10)
        assert isinstance(result, TextSearchResult)
        assert len(result.results) > 0

    def test_search_finds_code_blocks_heading(self, seeded_store_with_fake: tuple) -> None:
        conn, _, _, _ = seeded_store_with_fake
        result = text_search(conn, "Code", limit=10)
        assert isinstance(result, TextSearchResult)
        assert len(result.results) > 0

    def test_search_finds_frontmatter_content(self, seeded_store_with_fake: tuple) -> None:
        conn, _, _, _ = seeded_store_with_fake
        result = text_search(conn, "Content", limit=10)
        assert isinstance(result, TextSearchResult)
        assert len(result.results) > 0

    def test_search_finds_mixed_content(self, seeded_store_with_fake: tuple) -> None:
        conn, _, _, _ = seeded_store_with_fake
        result = text_search(conn, "Introduction", limit=10)
        assert isinstance(result, TextSearchResult)
        assert len(result.results) > 0

    def test_search_results_have_provenance(self, seeded_store_with_fake: tuple) -> None:
        conn, _, _, _ = seeded_store_with_fake
        result = text_search(conn, "Subtitle", limit=10)
        assert len(result.results) > 0
        for item in result.results:
            assert item.chunk_id
            assert item.file_relative_path
            assert isinstance(item.score, float)

    def test_search_respects_limit(self, seeded_store_with_fake: tuple) -> None:
        conn, _, _, _ = seeded_store_with_fake
        result = text_search(conn, "Fourth", limit=2)
        assert len(result.results) <= 2


@pytest.mark.asyncio()
class TestSemanticSearchSmoke:
    """Smoke tests for semantic search on indexed fixtures."""

    async def test_semantic_returns_results(self, seeded_store_with_fake: tuple) -> None:
        conn, provider, _, _ = seeded_store_with_fake
        result = await semantic_search(conn, "code examples", provider, limit=10)
        assert isinstance(result, SemanticSearchResult)
        assert len(result.results) > 0

    async def test_semantic_has_chunk_ids(self, seeded_store_with_fake: tuple) -> None:
        conn, provider, _, _ = seeded_store_with_fake
        result = await semantic_search(conn, "heading", provider, limit=5)
        assert len(result.results) > 0
        for item in result.results:
            assert item.chunk_id
            assert item.file_relative_path

    async def test_semantic_no_error(self, seeded_store_with_fake: tuple) -> None:
        conn, provider, _, _ = seeded_store_with_fake
        result = await semantic_search(conn, "content", provider, limit=5)
        assert result.error is None


@pytest.mark.asyncio()
class TestHybridSearchSmoke:
    """Smoke tests for hybrid search on indexed fixtures."""

    async def test_hybrid_returns_results(self, seeded_store_with_fake: tuple) -> None:
        conn, provider, config, _ = seeded_store_with_fake
        result = await hybrid_search(conn, "code examples", provider, config, limit=10)
        assert isinstance(result, HybridSearchResult)
        assert len(result.results) > 0

    async def test_hybrid_has_score_fields(self, seeded_store_with_fake: tuple) -> None:
        conn, provider, config, _ = seeded_store_with_fake
        result = await hybrid_search(conn, "Subtitle", provider, config, limit=5)
        assert len(result.results) > 0
        for item in result.results:
            assert isinstance(item.combined_score, float)
            assert item.combined_score >= 0

    async def test_hybrid_has_provenance(self, seeded_store_with_fake: tuple) -> None:
        conn, provider, config, _ = seeded_store_with_fake
        result = await hybrid_search(conn, "Code", provider, config, limit=5)
        assert len(result.results) > 0
        for item in result.results:
            assert item.chunk_id
            assert item.file_relative_path

    async def test_hybrid_respects_limit(self, seeded_store_with_fake: tuple) -> None:
        conn, provider, config, _ = seeded_store_with_fake
        result = await hybrid_search(conn, "Fourth", provider, config, limit=2)
        assert isinstance(result, HybridSearchResult)
        assert len(result.results) <= 2
