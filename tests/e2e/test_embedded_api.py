"""Embedded API contracts independent of Click."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from mdrack.config.models import MDRackConfig, PathsConfig
from mdrack.domain.indexing import SourceLocator
from mdrack.domain.retrieval import RetrievalCandidate
from mdrack.embeddings.fake import FakeEmbeddingProvider
from mdrack.public_api import MDRackEngine


def test_embedded_engine_scans_and_searches_without_click(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "guide.md").write_text(
        "# Guide\n\n## Transactions\nSQLite rollback keeps the old index usable.\n",
        encoding="utf-8",
    )
    config = MDRackConfig(
        paths=PathsConfig(
            root=".",
            store=str(tmp_path / ".mdrack"),
            config_file=".mdrack/config.toml",
        )
    )

    engine = MDRackEngine(
        root=root,
        config=config,
        embedding_provider=FakeEmbeddingProvider(dimensions=16),
    )
    scan = engine.scan()
    results = engine.search_text("rollback", limit=5)

    assert scan.status == "success"
    assert scan.files_indexed == 1
    assert results.total_count == 1
    locator = results.results[0].source_locator
    assert locator.relative_path == "guide.md"
    assert locator.start_line >= 1
    assert locator.end_line >= locator.start_line
    assert locator.chunk_id
    assert locator.block_id


class _FakeStorage:
    def __init__(self) -> None:
        self.prepared = []
        self.closed = False

    def start_run(self, **kwargs) -> str:
        return "fake-run"

    def plan_changes(self, scanned, root):
        return SimpleNamespace(new_files=scanned, changed_files=[], unchanged_files=[], deleted_files=[])

    def get_file_by_path(self, relative_path: str):
        return None

    def replace_file(self, prepared) -> None:
        self.prepared.append(prepared)

    def delete_file(self, relative_path: str) -> None:
        raise AssertionError("unexpected deletion")

    def record_error(self, run_id: str, code: str, *, file_ref: str) -> None:
        raise AssertionError("unexpected error")

    def finish_run(self, run_id: str, *, status: str, stats, error_codes) -> None:
        assert status == "success"

    def retrieve_text_candidates(self, query: str, *, limit: int, offset: int = 0) -> list[RetrievalCandidate]:
        locator = SourceLocator("default", "injected.md", 1, 1, (), "block_fake", "chunk_fake")
        return [RetrievalCandidate("chunk_fake", 1.0, "safe", locator)]

    def retrieve_semantic_candidates(
        self,
        query_vector,
        *,
        profile: str,
        profile_fingerprint: str | None,
        limit: int,
    ):
        return []

    def search_text(self, query: str, *, limit: int, offset: int = 0):
        raise AssertionError("legacy search path must not be used")

    def list_assets_for_file(self, relative_path: str):
        return []

    def list_asset_references(self, relative_path: str):
        return []

    def get_chunk_source_locator(self, chunk_id: str) -> SourceLocator:
        return SourceLocator("default", "injected.md", 1, 1, (), "block_fake", "chunk_fake")

    def close(self) -> None:
        self.closed = True


def test_embedded_engine_accepts_replacement_storage_without_sqlite(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "injected.md").write_text("# Injected\n\nStorage port.\n", encoding="utf-8")
    config = MDRackConfig(paths=PathsConfig(root=".", store=str(tmp_path / "must-not-exist")))
    storage = _FakeStorage()

    engine = MDRackEngine(
        root=root,
        config=config,
        storage=storage,
        embedding_provider=FakeEmbeddingProvider(dimensions=16),
    )
    scan = engine.scan()
    results = engine.search_text("port")
    locator = engine.get_chunk_source_locator("chunk_fake")
    engine.close()

    assert scan.files_indexed == 1
    assert results.results[0].source_locator == locator
    assert len(storage.prepared) == 1
    assert storage.closed is True
    assert not Path(config.paths.store).exists()
