"""Phase 1-2 contracts for stable provenance and reliable indexing."""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
from pathlib import Path

import pytest

from mdrack.adapters.sqlite.index_storage import SQLiteIndexStorage, create_sqlite_index_storage
from mdrack.application.indexing import IndexingService
from mdrack.config.models import ChunkingConfig, MDRackConfig, PathsConfig
from mdrack.embeddings.fake import FakeEmbeddingProvider
from mdrack.storage.sqlite.connection import get_connection


def _config(tmp_path: Path) -> MDRackConfig:
    return MDRackConfig(
        paths=PathsConfig(
            root=".",
            store=str(tmp_path / ".mdrack"),
            config_file=".mdrack/config.toml",
        )
    )


def _service(
    root: Path,
    config: MDRackConfig,
    provider: FakeEmbeddingProvider,
) -> IndexingService:
    storage = create_sqlite_index_storage(root, config)
    return IndexingService(root, config, storage, provider=provider)


def _snapshot(conn: sqlite3.Connection) -> tuple[list[tuple[object, ...]], list[tuple[object, ...]]]:
    sections = [
        tuple(row)
        for row in conn.execute(
            "SELECT logical_id, title, start_line, end_line FROM sections ORDER BY start_line"
        ).fetchall()
    ]
    chunks = [
        tuple(row)
        for row in conn.execute(
            "SELECT logical_id, content, start_line, end_line, block_logical_id "
            "FROM chunks ORDER BY chunk_index"
        ).fetchall()
    ]
    return sections, chunks


def test_logical_ids_and_locators_are_stable_across_forced_rescan(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "note.md").write_text("# Title\n\n## Topic\nStable body.\n", encoding="utf-8")
    config = _config(tmp_path)
    provider = FakeEmbeddingProvider(dimensions=16)

    service = _service(root, config, provider)
    first = service.scan(force_reindex=True)
    service.close()
    assert first.status == "success"

    db_path = Path(config.paths.store) / "knowledge.db"
    conn = get_connection(db_path)
    try:
        before = _snapshot(conn)
        locator = SQLiteIndexStorage(conn).get_chunk_source_locator(before[1][0][0])
    finally:
        conn.close()

    service = _service(root, config, provider)
    second = service.scan(force_reindex=True)
    service.close()
    assert second.status == "success"

    conn = get_connection(db_path)
    try:
        after = _snapshot(conn)
    finally:
        conn.close()

    assert after == before
    assert locator.root_id == "default"
    assert locator.relative_path == "note.md"
    assert locator.chunk_id == before[1][0][0]
    assert locator.block_id
    assert not Path(locator.relative_path).is_absolute()


def test_file_transaction_rolls_back_after_mid_write_failure(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    note = root / "note.md"
    note.write_text("# Title\n\nOriginal body.\n", encoding="utf-8")
    config = _config(tmp_path)
    provider = FakeEmbeddingProvider(dimensions=16)
    service = _service(root, config, provider)
    assert service.scan().status == "success"
    service.close()

    db_path = Path(config.paths.store) / "knowledge.db"
    conn = get_connection(db_path)
    try:
        before = conn.execute(
            "SELECT f.source_hash, c.content FROM files f JOIN chunks c ON c.file_id = f.id"
        ).fetchall()
        storage = SQLiteIndexStorage(conn)
        original_write_chunk = storage._write_chunk
        calls = 0

        def fail_after_first_chunk(*args, **kwargs):
            nonlocal calls
            calls += 1
            original_write_chunk(*args, **kwargs)
            raise RuntimeError("injected write failure")

        monkeypatch.setattr(storage, "_write_chunk", fail_after_first_chunk)
        note.write_text("# Title\n\nChanged body.\n", encoding="utf-8")
        result = IndexingService(root, config, storage, provider=provider).scan()
        after = conn.execute(
            "SELECT f.source_hash, c.content FROM files f JOIN chunks c ON c.file_id = f.id"
        ).fetchall()
    finally:
        conn.close()

    assert result.status == "failed"
    assert result.files_failed == 1
    assert [tuple(row) for row in after] == [tuple(row) for row in before]


def test_scan_reports_partial_success_with_honest_counts(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "good.md").write_text("# Good\n\nReadable.\n", encoding="utf-8")
    (root / "bad.md").write_bytes(b"\xff\xfe\x00")
    config = _config(tmp_path)

    service = _service(root, config, FakeEmbeddingProvider(dimensions=16))
    result = service.scan()
    service.close()

    assert result.status == "partial_success"
    assert result.files_seen == 2
    assert result.files_changed == 2
    assert result.files_indexed == 1
    assert result.files_failed == 1
    assert result.errors_count == 1
    db_path = Path(config.paths.store) / "knowledge.db"
    conn = get_connection(db_path)
    try:
        active_paths = [row["relative_path"] for row in conn.execute("SELECT relative_path FROM files")]
    finally:
        conn.close()
    assert active_paths == ["good.md"]


def test_indexing_logs_and_diagnostics_do_not_expose_private_values(tmp_path: Path, caplog) -> None:
    root = tmp_path / "private-vault-name"
    root.mkdir()
    private_name = "customer-secret-note.md"
    (root / private_name).write_bytes(b"\xff\xfe")
    config = _config(tmp_path)
    caplog.set_level(logging.DEBUG)

    service = _service(root, config, FakeEmbeddingProvider(dimensions=16))
    result = service.scan()
    service.close()

    db_path = Path(config.paths.store) / "knowledge.db"
    conn = get_connection(db_path)
    try:
        details = " ".join(
            str(row["details"])
            for row in conn.execute("SELECT details FROM diagnostics").fetchall()
        )
    finally:
        conn.close()

    captured = caplog.text + details
    assert result.status == "failed"
    assert str(root) not in captured
    assert str(db_path) not in captured
    assert private_name not in captured
    assert "customer-secret" not in captured


def test_repeated_identical_chunks_are_distinct_and_stable(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "duplicates.md").write_text(
        "# Duplicate\n\nrepeat repeat\n\nrepeat repeat\n",
        encoding="utf-8",
    )
    config = MDRackConfig(
        paths=PathsConfig(root=".", store=str(tmp_path / ".mdrack")),
        chunking=ChunkingConfig(min_chunk_chars=1, target_chunk_chars=8, hard_limit_chars=8, overlap_chars=0),
    )
    service = _service(root, config, FakeEmbeddingProvider(dimensions=16))
    assert service.scan(force_reindex=True).status == "success"
    service.close()

    db_path = Path(config.paths.store) / "knowledge.db"
    conn = get_connection(db_path)
    try:
        before = [
            tuple(row)
            for row in conn.execute(
                "SELECT logical_id, start_line, end_line FROM chunks ORDER BY chunk_index"
            ).fetchall()
        ]
    finally:
        conn.close()

    service = _service(root, config, FakeEmbeddingProvider(dimensions=16))
    assert service.scan(force_reindex=True).status == "success"
    service.close()
    conn = get_connection(db_path)
    try:
        after = [
            tuple(row)
            for row in conn.execute(
                "SELECT logical_id, start_line, end_line FROM chunks ORDER BY chunk_index"
            ).fetchall()
        ]
    finally:
        conn.close()

    assert len(before) >= 2
    assert len({row[0] for row in before}) == len(before)
    assert {row[1] for row in before} >= {3, 5}
    assert after == before


def _stored_counts(db_path: Path) -> tuple[int, int]:
    conn = get_connection(db_path)
    try:
        return (
            conn.execute("SELECT COUNT(*) FROM files").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
        )
    finally:
        conn.close()


@pytest.mark.parametrize(
    "failure",
    [PermissionError("permission denied"), OSError("traversal failed")],
    ids=["inaccessible", "traversal-failed"],
)
def test_corpus_traversal_failure_preserves_last_good_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: OSError,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "note.md").write_text("# Kept\n\nLast good content.\n", encoding="utf-8")
    config = _config(tmp_path)
    provider = FakeEmbeddingProvider(dimensions=16)
    service = _service(root, config, provider)
    assert service.scan().status == "success"
    service.close()

    db_path = Path(config.paths.store) / "knowledge.db"
    before = _stored_counts(db_path)

    def failing_walk(*args, **kwargs):
        kwargs["onerror"](failure)
        return iter(())

    monkeypatch.setattr(os, "walk", failing_walk)
    service = _service(root, config, provider)
    result = service.scan()
    service.close()

    assert result.status == "failed"
    assert result.files_deleted == 0
    assert result.errors_count == 1
    assert _stored_counts(db_path) == before


def test_missing_corpus_root_preserves_last_good_index(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "note.md").write_text("# Kept\n\nLast good content.\n", encoding="utf-8")
    config = _config(tmp_path)
    provider = FakeEmbeddingProvider(dimensions=16)
    service = _service(root, config, provider)
    assert service.scan().status == "success"
    service.close()

    db_path = Path(config.paths.store) / "knowledge.db"
    before = _stored_counts(db_path)
    shutil.rmtree(root)

    service = _service(root, config, provider)
    result = service.scan()
    service.close()

    assert result.status == "failed"
    assert result.files_seen == 0
    assert result.files_deleted == 0
    assert result.errors_count == 1
    assert _stored_counts(db_path) == before


def test_valid_empty_corpus_applies_deletions_explicitly(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    note = root / "note.md"
    note.write_text("# Removed\n\nContent.\n", encoding="utf-8")
    config = _config(tmp_path)
    provider = FakeEmbeddingProvider(dimensions=16)
    service = _service(root, config, provider)
    assert service.scan().status == "success"
    service.close()
    note.unlink()

    service = _service(root, config, provider)
    result = service.scan()
    service.close()

    assert result.status == "success"
    assert result.files_seen == 0
    assert result.files_deleted == 1
    assert result.errors_count == 0
    assert _stored_counts(Path(config.paths.store) / "knowledge.db") == (0, 0)
