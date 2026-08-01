"""Canonical catalog indexing reliability contracts."""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from mdrack.application.compatibility import create_application_storage
from mdrack.application.indexing import IndexingService
from mdrack.config.models import ChunkingConfig, MDRackConfig, PathsConfig
from mdrack.embeddings.fake import FakeEmbeddingProvider


def _config(tmp_path: Path, *, chunking: ChunkingConfig | None = None) -> MDRackConfig:
    return MDRackConfig(
        paths=PathsConfig(
            root=".",
            store=str(tmp_path / ".mdrack"),
            config_file=".mdrack/config.toml",
        ),
        chunking=chunking or ChunkingConfig(),
    )


def _service(root: Path, config: MDRackConfig) -> IndexingService:
    return IndexingService(
        root,
        config,
        create_application_storage(root, config, create=True),
        provider=FakeEmbeddingProvider(dimensions=16),
    )


def _catalog_rows(root: Path, config: MDRackConfig) -> list[tuple[object, ...]]:
    storage = create_application_storage(root, config)
    try:
        return [
            tuple(row)
            for row in storage.connection.execute(
                "SELECT unit_id, resource_id, text_content, evidence_locator_json "
                "FROM core_search_units WHERE unit_kind='text_chunk' ORDER BY unit_id"
            ).fetchall()
        ]
    finally:
        storage.close()


def _stored_counts(root: Path, config: MDRackConfig) -> tuple[int, int]:
    storage = create_application_storage(root, config)
    try:
        resources = int(
            storage.connection.execute(
                "SELECT COUNT(*) FROM core_resources WHERE resource_kind='document'"
            ).fetchone()[0]
        )
        chunks = int(
            storage.connection.execute(
                "SELECT COUNT(*) FROM core_search_units WHERE unit_kind='text_chunk'"
            ).fetchone()[0]
        )
        return resources, chunks
    finally:
        storage.close()


def test_logical_ids_and_locators_are_stable_across_forced_rescan(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "note.md").write_text("# Title\n\n## Topic\nStable body.\n", encoding="utf-8")
    config = _config(tmp_path)

    service = _service(root, config)
    try:
        assert service.scan(force_reindex=True).status == "success"
    finally:
        service.close()
    before = _catalog_rows(root, config)

    service = _service(root, config)
    try:
        assert service.scan(force_reindex=True).status == "success"
    finally:
        service.close()
    after = _catalog_rows(root, config)

    assert after == before
    assert before
    locator = json.loads(str(before[0][3]))
    assert locator["relative_path"] == "note.md"
    assert locator["start_line"] >= 1
    assert not Path(locator["relative_path"]).is_absolute()


def test_file_failure_before_catalog_replacement_preserves_last_good_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    note = root / "note.md"
    note.write_text("# Title\n\nOriginal body.\n", encoding="utf-8")
    config = _config(tmp_path)

    service = _service(root, config)
    try:
        assert service.scan().status == "success"
    finally:
        service.close()
    before = _catalog_rows(root, config)

    note.write_text("# Title\n\nChanged body.\n", encoding="utf-8")
    service = _service(root, config)
    try:
        core_indexing = getattr(service.storage, "core_indexing")
        monkeypatch.setattr(
            core_indexing,
            "index",
            lambda _batch: (_ for _ in ()).throw(RuntimeError("injected catalog failure")),
        )
        result = service.scan()
    finally:
        service.close()

    assert result.status == "failed"
    assert result.files_failed == 1
    assert _catalog_rows(root, config) == before


def test_scan_reports_partial_success_with_honest_counts(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "good.md").write_text("# Good\n\nReadable.\n", encoding="utf-8")
    (root / "bad.md").write_bytes(b"\xff\xfe\x00")
    config = _config(tmp_path)

    service = _service(root, config)
    try:
        result = service.scan()
    finally:
        service.close()

    assert result.status == "partial_success"
    assert result.files_seen == 2
    assert result.files_changed == 2
    assert result.files_indexed == 1
    assert result.files_failed == 1
    assert result.errors_count == 1
    assert _stored_counts(root, config)[0] == 1


def test_indexing_logs_do_not_expose_private_values(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    root = tmp_path / "private-vault-name"
    root.mkdir()
    private_name = "customer-secret-note.md"
    (root / private_name).write_bytes(b"\xff\xfe")
    config = _config(tmp_path)
    caplog.set_level(logging.DEBUG)

    service = _service(root, config)
    try:
        result = service.scan()
    finally:
        service.close()

    assert result.status == "failed"
    captured = caplog.text
    assert str(root) not in captured
    assert str(tmp_path / ".mdrack" / "catalog.sqlite3") not in captured
    assert private_name not in captured
    assert "customer-secret" not in captured


def test_repeated_identical_chunks_are_distinct_and_stable(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "duplicates.md").write_text(
        "# Duplicate\n\nrepeat repeat\n\nrepeat repeat\n",
        encoding="utf-8",
    )
    config = _config(
        tmp_path,
        chunking=ChunkingConfig(
            min_chunk_chars=1,
            target_chunk_chars=8,
            hard_limit_chars=8,
            overlap_chars=0,
        ),
    )

    service = _service(root, config)
    try:
        assert service.scan(force_reindex=True).status == "success"
    finally:
        service.close()
    before = _catalog_rows(root, config)

    service = _service(root, config)
    try:
        assert service.scan(force_reindex=True).status == "success"
    finally:
        service.close()
    after = _catalog_rows(root, config)

    assert len(before) >= 2
    assert len({row[0] for row in before}) == len(before)
    assert after == before


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

    service = _service(root, config)
    try:
        assert service.scan().status == "success"
    finally:
        service.close()
    before = _stored_counts(root, config)

    def failing_walk(*args: Any, **kwargs: Any):
        kwargs["onerror"](failure)
        return iter(())

    monkeypatch.setattr(os, "walk", failing_walk)
    service = _service(root, config)
    try:
        result = service.scan()
    finally:
        service.close()

    assert result.status == "failed"
    assert result.files_deleted == 0
    assert result.errors_count == 1
    assert _stored_counts(root, config) == before


def test_missing_corpus_root_preserves_last_good_index(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "note.md").write_text("# Kept\n\nLast good content.\n", encoding="utf-8")
    config = _config(tmp_path)
    service = _service(root, config)
    try:
        assert service.scan().status == "success"
    finally:
        service.close()
    before = _stored_counts(root, config)
    shutil.rmtree(root)

    service = _service(root, config)
    try:
        result = service.scan()
    finally:
        service.close()

    assert result.status == "failed"
    assert result.files_seen == 0
    assert result.files_deleted == 0
    assert result.errors_count == 1
    assert _stored_counts(root, config) == before


def test_valid_empty_corpus_applies_deletions_explicitly(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    note = root / "note.md"
    note.write_text("# Removed\n\nContent.\n", encoding="utf-8")
    config = _config(tmp_path)
    service = _service(root, config)
    try:
        assert service.scan().status == "success"
    finally:
        service.close()
    note.unlink()

    service = _service(root, config)
    try:
        result = service.scan()
    finally:
        service.close()

    assert result.status == "success"
    assert result.files_seen == 0
    assert result.files_deleted == 1
    assert result.errors_count == 0
    assert _stored_counts(root, config) == (0, 0)
