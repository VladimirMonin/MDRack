"""Offline coverage for source-only v2 rebuild and one-way activation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from mdrack.adapters.sqlite.generation_runtime import GenerationRuntimeError, SQLiteGenerationRuntime
from mdrack.application.fresh_reindex import FreshCompactReindexService
from mdrack.application.generation_manager import (
    StoreGenerationManager,
    StoreGenerationManagerError,
    _legacy_manifest_digest,
)
from mdrack.application.store_generations import (
    ActiveGenerationPointer,
    GenerationContractKind,
    GenerationRetention,
    GenerationState,
    RetentionMode,
    StoreGeneration,
)
from mdrack.config.models import MDRackConfig, PathsConfig
from mdrack.embeddings.fake import FakeEmbeddingProvider
from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.migrations import (
    ACTIVE_MIGRATION_VERSION,
)
from mdrack_sqlite.contract_v2 import SQLITE_CATALOG_V2_SCHEMA_VERSION


def _manager_with_unreadable_retained_legacy(store_dir: Path) -> tuple[StoreGenerationManager, Path, str]:
    manager = StoreGenerationManager(
        store_dir,
        runtime=SQLiteGenerationRuntime(),
        clock=lambda: "2026-07-24T00:00:00Z",
        id_factory=lambda: "compact-fresh",
    )
    legacy_id = "retained-legacy"
    legacy_path = manager.database_path(legacy_id)
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_bytes(b"not an sqlite database; must never be opened during fresh cutover")
    legacy_digest = hashlib.sha256(legacy_path.read_bytes()).hexdigest()
    legacy = StoreGeneration(
        generation_id=legacy_id,
        contract_kind=GenerationContractKind.LEGACY_V0_2,
        migration_manifest_digest=_legacy_manifest_digest(),
        schema_version=ACTIVE_MIGRATION_VERSION,
        state=GenerationState.LEGACY_ONLY,
        created_at="2026-07-24T00:00:00Z",
        retention=GenerationRetention(
            mode=RetentionMode.RETAINED_READ_ONLY,
            retain_through_release="1.3.0",
        ),
    )
    manager.metadata_path(legacy_id).write_bytes(legacy.to_bytes())
    manager.pointer_path.write_bytes(
        ActiveGenerationPointer(legacy_id, GenerationContractKind.LEGACY_V0_2).to_bytes()
    )
    return manager, legacy_path, legacy_digest


def test_fresh_compact_reindex_builds_clean_f32_candidate_without_opening_old_store(
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    source = "# Guide\n\n## Stable\n\nAlpha retrieval phrase.\n"
    note = root / "guide.md"
    note.write_text(source, encoding="utf-8")
    source_digest = hashlib.sha256(note.read_bytes()).hexdigest()
    store_dir = tmp_path / "store"
    manager, legacy_path, legacy_digest = _manager_with_unreadable_retained_legacy(store_dir)
    config = MDRackConfig(paths=PathsConfig(root=".", store=str(store_dir)))

    service = FreshCompactReindexService(
        root=root,
        config=config,
        provider=FakeEmbeddingProvider(dimensions=8),
        manager=manager,
    )
    candidate = service.rebuild()

    assert candidate.generation.contract_kind is GenerationContractKind.RESOURCE_CORE_V2
    assert candidate.generation.state is GenerationState.READY
    assert candidate.source_count == 1
    assert note.read_text(encoding="utf-8") == source
    assert hashlib.sha256(note.read_bytes()).hexdigest() == source_digest
    assert hashlib.sha256(legacy_path.read_bytes()).hexdigest() == legacy_digest

    pointer = service.activate(candidate.generation.generation_id)
    assert pointer.generation_id == candidate.generation.generation_id
    assert hashlib.sha256(legacy_path.read_bytes()).hexdigest() == legacy_digest

    active_pointer, active_generation, active_path = manager.resolve_active()
    assert active_pointer == pointer
    assert active_generation.schema_version == SQLITE_CATALOG_V2_SCHEMA_VERSION
    connection = get_connection(active_path)
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name IN ('files', 'chunk_embeddings')"
        ).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM core_resources").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM core_search_units").fetchone()[0] > 0
        assert connection.execute("SELECT COUNT(*) FROM core_unit_embeddings").fetchone()[0] > 0
        assert [
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT length(embedding) FROM core_unit_embeddings"
            ).fetchall()
        ] == [32]
        metadata = json.loads(
            connection.execute("SELECT metadata_json FROM core_embedding_spaces").fetchone()[0]
        )
        assert metadata["vector_value_policy"] == "ieee754-f32-canonical-v1"
        assert metadata["vector_codec"] == "ieee754-f32-le-v1"
        persisted_fingerprints = tuple(
            row[0]
            for row in connection.execute(
                "SELECT fingerprint FROM core_embedding_spaces ORDER BY space_id"
            ).fetchall()
        )
        assert tuple(item.value for item in candidate.generation.fingerprints) == persisted_fingerprints
        assert tuple(item.name for item in candidate.generation.fingerprints) == tuple(
            f"embedding_space:{index:04d}" for index in range(len(persisted_fingerprints))
        )
        assert [row[0] for row in connection.execute("PRAGMA integrity_check").fetchall()] == ["ok"]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


def test_fresh_compact_reindex_reparse_failure_keeps_retained_pointer_and_source(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    note = root / "bad.md"
    note.write_bytes(b"# valid heading\n\n\xff")
    source_digest = hashlib.sha256(note.read_bytes()).hexdigest()
    store_dir = tmp_path / "store"
    manager, legacy_path, legacy_digest = _manager_with_unreadable_retained_legacy(store_dir)
    pointer_before = manager.pointer_path.read_bytes()
    config = MDRackConfig(paths=PathsConfig(root=".", store=str(store_dir)))
    service = FreshCompactReindexService(
        root=root,
        config=config,
        provider=FakeEmbeddingProvider(dimensions=8),
        manager=manager,
    )

    with pytest.raises(StoreGenerationManagerError, match="candidate_build_failed"):
        service.rebuild()

    assert manager.pointer_path.read_bytes() == pointer_before
    assert hashlib.sha256(legacy_path.read_bytes()).hexdigest() == legacy_digest
    assert hashlib.sha256(note.read_bytes()).hexdigest() == source_digest
    failed = manager.load_generation("compact-fresh")
    assert failed.state is GenerationState.FAILED


def test_one_way_cutover_reopens_verified_candidate_after_pointer_replace_interruption(
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "guide.md").write_text("# Guide\n\nResilient candidate.\n", encoding="utf-8")
    store_dir = tmp_path / "store"
    manager, legacy_path, legacy_digest = _manager_with_unreadable_retained_legacy(store_dir)
    config = MDRackConfig(paths=PathsConfig(root=".", store=str(store_dir)))
    service = FreshCompactReindexService(
        root=root,
        config=config,
        provider=FakeEmbeddingProvider(dimensions=8),
        manager=manager,
    )
    candidate = service.rebuild()

    def fail_after_pointer_replace(point: str) -> None:
        if point == "after_pointer_replace":
            raise RuntimeError("pointer cutover interrupted")

    manager.set_failure_hook(fail_after_pointer_replace)
    with pytest.raises(RuntimeError, match="pointer cutover interrupted"):
        service.activate(candidate.generation.generation_id)

    reopened = StoreGenerationManager(store_dir, runtime=SQLiteGenerationRuntime())
    pointer, active, _path = reopened.resolve_active()
    assert pointer.generation_id == candidate.generation.generation_id
    assert active.state is GenerationState.READY
    assert service.verify(candidate.generation.generation_id)
    assert hashlib.sha256(legacy_path.read_bytes()).hexdigest() == legacy_digest


def test_fresh_compact_verification_rejects_space_fingerprint_mismatch(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "guide.md").write_text("# Guide\n\nFingerprint contract.\n", encoding="utf-8")
    store_dir = tmp_path / "store"
    manager, legacy_path, legacy_digest = _manager_with_unreadable_retained_legacy(store_dir)
    service = FreshCompactReindexService(
        root=root,
        config=MDRackConfig(paths=PathsConfig(root=".", store=str(store_dir))),
        provider=FakeEmbeddingProvider(dimensions=8),
        manager=manager,
    )
    candidate = service.rebuild()
    connection = get_connection(manager.database_path(candidate.generation.generation_id))
    try:
        connection.execute(
            "UPDATE core_embedding_spaces SET fingerprint='mismatched-space-fingerprint'"
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(GenerationRuntimeError, match="candidate_fingerprint_mismatch"):
        service.verify(candidate.generation.generation_id)
    with pytest.raises(GenerationRuntimeError, match="candidate_fingerprint_mismatch"):
        service.activate(candidate.generation.generation_id)
    assert hashlib.sha256(legacy_path.read_bytes()).hexdigest() == legacy_digest


def test_one_way_cutover_fails_closed_when_candidate_corrupts_after_pointer_replace(
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "guide.md").write_text("# Guide\n\nPost-switch verification.\n", encoding="utf-8")
    store_dir = tmp_path / "store"
    manager, legacy_path, legacy_digest = _manager_with_unreadable_retained_legacy(store_dir)
    service = FreshCompactReindexService(
        root=root,
        config=MDRackConfig(paths=PathsConfig(root=".", store=str(store_dir))),
        provider=FakeEmbeddingProvider(dimensions=8),
        manager=manager,
    )
    candidate = service.rebuild()

    def corrupt_after_durable_pointer_replace(point: str) -> None:
        if point == "after_pointer_replace":
            manager.database_path(candidate.generation.generation_id).write_bytes(b"corrupted after switch")

    manager.set_failure_hook(corrupt_after_durable_pointer_replace)
    with pytest.raises(StoreGenerationManagerError, match="active_generation_invalid"):
        service.activate(candidate.generation.generation_id)

    assert manager.pointer_path.read_bytes() == ActiveGenerationPointer(
        candidate.generation.generation_id,
        GenerationContractKind.RESOURCE_CORE_V2,
    ).to_bytes()
    assert hashlib.sha256(legacy_path.read_bytes()).hexdigest() == legacy_digest
