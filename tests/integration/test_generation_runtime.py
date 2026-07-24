"""Integration tests for candidate generation durability, cutover, and recovery."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from mdrack.adapters.sqlite.generation_runtime import SQLiteGenerationRuntime
from mdrack.adapters.sqlite.resource_store import SQLiteResourceStore
from mdrack.application.generation_manager import (
    StoreGenerationManager,
    StoreGenerationManagerError,
)
from mdrack.application.store_generations import (
    GenerationContractKind,
    GenerationFingerprint,
    GenerationState,
    StoreGeneration,
)
from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.migrations import apply_migrations, get_migrations_dir
from mdrack_sqlite.contract_v2 import (
    SQLITE_CATALOG_V2_SCHEMA_VERSION,
    SQLITE_V2_MIGRATION_MANIFEST_DIGEST,
)


def _manager(store_dir: Path, *, generation_id: str = "candidate-1") -> StoreGenerationManager:
    return StoreGenerationManager(
        store_dir,
        runtime=SQLiteGenerationRuntime(),
        clock=lambda: "2026-07-18T00:00:00Z",
        id_factory=lambda: generation_id,
    )


def _seed_candidate(connection: sqlite3.Connection) -> None:
    locator_json = '{"path":"fixture"}'
    locator_fingerprint = "sha256:" + hashlib.sha256(locator_json.encode("utf-8")).hexdigest()
    connection.execute(
        "INSERT INTO core_resources(resource_id,resource_kind,media_type,source_namespace,"
        "locator_kind,locator_json,locator_fingerprint,content_hash,title,metadata_json,indexed_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            "resource-1",
            "document",
            "text/plain",
            "fixture",
            "relative",
            locator_json,
            locator_fingerprint,
            "sha256:content",
            "fixture",
            "{}",
            "2026-07-18T00:00:00Z",
        ),
    )
    connection.execute(
        "INSERT INTO core_representations(representation_id,resource_id,representation_kind,"
        "modality,text_content,language,producer_fingerprint,token_count,token_count_kind,metadata_json) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            "representation-1",
            "resource-1",
            "retrieval_text",
            "text",
            "fixture text",
            "en",
            "producer-fingerprint",
            2,
            "exact",
            "{}",
        ),
    )
    connection.execute(
        "INSERT INTO core_search_units(unit_id,resource_id,representation_id,unit_kind,modality,"
        "text_content,evidence_locator_kind,evidence_locator_json,ordinal,token_count,token_count_kind,metadata_json) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "unit-1",
            "resource-1",
            "representation-1",
            "text_chunk",
            "text",
            "fixture text",
            "span",
            '{"end":12,"start":0}',
            0,
            2,
            "exact",
            "{}",
        ),
    )
    connection.execute(
        "INSERT INTO core_search_units_fts(unit_id,content) VALUES(?,?)",
        ("unit-1", "fixture text"),
    )
    connection.execute(
        "INSERT INTO core_embedding_spaces(space_id,dimensions,metric,fingerprint,metadata_json) "
        "VALUES(?,?,?,?,?)",
        ("space-1", 2, "dot", "space-fingerprint", "{}"),
    )
    connection.execute(
        "INSERT INTO core_unit_embeddings(unit_id,space_id,embedding,embedded_at) VALUES(?,?,?,?)",
        ("unit-1", "space-1", b"[0.0,-0.0]", "2026-07-18T00:00:00Z"),
    )
    connection.execute(
        "INSERT INTO core_facets(namespace,value) VALUES(?,?)",
        ("tag", "fixture"),
    )
    facet_id = connection.execute(
        "SELECT facet_id FROM core_facets WHERE namespace=? AND value=?",
        ("tag", "fixture"),
    ).fetchone()[0]
    connection.execute(
        "INSERT INTO core_resource_facets(resource_id,facet_id,origin,producer_is_null,"
        "producer_value,confidence_json) VALUES(?,?,?,?,?,?)",
        ("resource-1", facet_id, "extractor", 0, "producer-fingerprint", b"1.0"),
    )
    connection.commit()


def _insert_resource(
    connection: sqlite3.Connection,
    resource_id: str,
) -> None:
    locator_json = json.dumps(
        {"path": resource_id},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    locator_fingerprint = "sha256:" + hashlib.sha256(locator_json.encode("utf-8")).hexdigest()
    connection.execute(
        "INSERT INTO core_resources(resource_id,resource_kind,media_type,source_namespace,"
        "locator_kind,locator_json,locator_fingerprint,content_hash,title,metadata_json,indexed_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            resource_id,
            "document",
            "text/plain",
            "fixture",
            "relative",
            locator_json,
            locator_fingerprint,
            f"sha256:{resource_id}",
            resource_id,
            "{}",
            "2026-07-18T00:00:00Z",
        ),
    )


def _insert_representation(
    connection: sqlite3.Connection,
    resource_id: str,
) -> str:
    representation_id = f"representation-{resource_id}"
    connection.execute(
        "INSERT INTO core_representations(representation_id,resource_id,representation_kind,"
        "modality,text_content,language,producer_fingerprint,token_count,token_count_kind,metadata_json) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            representation_id,
            resource_id,
            "retrieval_text",
            "text",
            f"text for {resource_id}",
            "en",
            "producer-fingerprint",
            3,
            "exact",
            "{}",
        ),
    )
    return representation_id


def _insert_search_unit(
    connection: sqlite3.Connection,
    resource_id: str,
    representation_id: str,
) -> None:
    unit_id = f"unit-{resource_id}"
    content = f"text for {resource_id}"
    connection.execute(
        "INSERT INTO core_search_units(unit_id,resource_id,representation_id,unit_kind,modality,"
        "text_content,evidence_locator_kind,evidence_locator_json,ordinal,token_count,token_count_kind,metadata_json) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            unit_id,
            resource_id,
            representation_id,
            "text_chunk",
            "text",
            content,
            "span",
            '{"end":1,"start":0}',
            0,
            3,
            "exact",
            "{}",
        ),
    )
    connection.execute(
        "INSERT INTO core_search_units_fts(unit_id,content) VALUES(?,?)",
        (unit_id, content),
    )


def _prepare_legacy(manager: StoreGenerationManager, generation_id: str = "legacy-1") -> tuple[Path, str, int]:
    path = manager.database_path(generation_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = get_connection(path)
    try:
        apply_migrations(connection, get_migrations_dir())
        connection.execute(
            "INSERT INTO files(id,relative_path,source_hash,indexed_at) VALUES(?,?,?,?)",
            ("legacy-file", "fixture.md", "legacy-hash", "2026-07-18T00:00:00Z"),
        )
        connection.commit()
    finally:
        connection.close()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    inode = path.stat().st_ino
    manager.register_legacy_generation(
        generation_id,
        retain_through_release="v0.3-compatibility",
    )
    manager.initialize_legacy_pointer(generation_id)
    return path, digest, inode


def _fail_once(point_to_fail: str):
    failed = False

    def hook(point: str) -> None:
        nonlocal failed
        if point == point_to_fail and not failed:
            failed = True
            raise RuntimeError("PRIVATE_PATH_VECTOR_EXCEPTION_SENTINEL")

    return hook


def test_candidate_build_cutover_reader_visibility_rollback_and_retention(tmp_path: Path) -> None:
    manager = _manager(tmp_path / "store")
    legacy_path, legacy_digest, legacy_inode = _prepare_legacy(manager)
    old_reader = sqlite3.connect(f"file:{legacy_path.as_posix()}?mode=ro", uri=True)

    candidate = manager.build_candidate(
        _seed_candidate,
        fingerprints=(
            GenerationFingerprint("representation", "producer-fingerprint"),
            GenerationFingerprint("space", "space-fingerprint"),
        ),
    )

    assert candidate.state is GenerationState.READY
    assert candidate.contract_kind is GenerationContractKind.RESOURCE_CORE_V2
    assert candidate.schema_version == SQLITE_CATALOG_V2_SCHEMA_VERSION
    assert candidate.migration_manifest_digest == SQLITE_V2_MIGRATION_MANIFEST_DIGEST
    candidate_path = manager.database_path(candidate.generation_id)
    assert candidate_path.is_file()
    assert not candidate_path.with_name(candidate_path.name + "-wal").exists()
    assert not candidate_path.with_name(candidate_path.name + "-shm").exists()
    readback = sqlite3.connect(f"file:{candidate_path.as_posix()}?mode=ro", uri=True)
    try:
        store = SQLiteResourceStore(readback)
        assert store.read_resource("resource-1") is not None
        assert store.read_unit("unit-1") is not None
        assert store.read_vector("unit-1", "space-1") is not None
    finally:
        readback.close()
    assert old_reader.execute("SELECT id FROM files").fetchone()[0] == "legacy-file"

    manager.activate_candidate(candidate.generation_id)
    pointer, active, active_path = manager.resolve_active()
    assert pointer.generation_id == candidate.generation_id
    assert active.state is GenerationState.READY
    new_reader = sqlite3.connect(f"file:{active_path.as_posix()}?mode=ro", uri=True)
    assert new_reader.execute("SELECT resource_id FROM core_resources").fetchone()[0] == "resource-1"
    assert old_reader.execute("SELECT id FROM files").fetchone()[0] == "legacy-file"
    old_reader.close()
    new_reader.close()

    with pytest.raises(StoreGenerationManagerError, match="rollback_unsupported"):
        manager.rollback("legacy-1")
    pointer, active, active_path = manager.resolve_active()
    assert pointer.generation_id == candidate.generation_id
    assert active.contract_kind is GenerationContractKind.RESOURCE_CORE_V2
    v2_reader = sqlite3.connect(f"file:{active_path.as_posix()}?mode=ro", uri=True)
    try:
        assert v2_reader.execute(
            "SELECT schema_version FROM mdrack_sqlite_schema WHERE singleton=1"
        ).fetchone()[0] == SQLITE_CATALOG_V2_SCHEMA_VERSION
        assert v2_reader.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name='schema_migrations'"
        ).fetchone()[0] == 0
    finally:
        v2_reader.close()

    assert legacy_path.stat().st_ino == legacy_inode
    assert hashlib.sha256(legacy_path.read_bytes()).hexdigest() == legacy_digest
    assert candidate_path.exists()
    assert legacy_path.exists()


@pytest.mark.parametrize(
    "failure_point",
    [
        "after_exclusive_create",
        "after_candidate_open",
        "after_building_metadata",
        "after_candidate_migrations",
        "after_rebuild",
        "after_candidate_verification",
        "before_checkpoint",
        "after_checkpoint",
        "after_candidate_close",
        "before_database_fsync",
        "after_database_fsync",
        "before_directory_fsync",
        "after_directory_fsync",
        "before_metadata_temporary_create",
        "after_metadata_temporary_write",
        "after_metadata_temporary_fsync",
        "before_metadata_replace",
        "after_metadata_replace",
        "after_metadata_directory_fsync",
    ],
)
def test_interruption_at_every_candidate_durability_boundary_never_switches_active(
    tmp_path: Path,
    failure_point: str,
) -> None:
    runtime = SQLiteGenerationRuntime()
    manager = StoreGenerationManager(
        tmp_path / "store",
        runtime=runtime,
        clock=lambda: "2026-07-18T00:00:00Z",
        id_factory=lambda: "candidate-failed",
    )
    _prepare_legacy(manager)
    old_pointer = manager.pointer_path.read_bytes()
    hook = _fail_once(failure_point)
    runtime.set_failure_hook(hook)
    manager.set_failure_hook(hook)

    with pytest.raises(StoreGenerationManagerError, match="candidate_build_failed"):
        manager.build_candidate(_seed_candidate)

    assert manager.pointer_path.read_bytes() == old_pointer
    pointer, _generation, _path = manager.resolve_active()
    assert pointer.generation_id == "legacy-1"
    metadata_path = manager.metadata_path("candidate-failed")
    if metadata_path.exists():
        generation = StoreGeneration.from_bytes(metadata_path.read_bytes())
        assert generation.state in {GenerationState.BUILDING, GenerationState.FAILED}


@pytest.mark.parametrize(
    "failure_point",
    [
        "after_ready_metadata_replace",
        "after_ready_metadata_directory_fsync",
        "after_ready_metadata",
    ],
)
def test_post_ready_interruption_preserves_durable_inactive_ready_candidate(
    tmp_path: Path,
    failure_point: str,
) -> None:
    manager = _manager(tmp_path / "store", generation_id="candidate-ready")
    _prepare_legacy(manager)
    old_pointer = manager.pointer_path.read_bytes()
    manager.set_failure_hook(_fail_once(failure_point))

    with pytest.raises(StoreGenerationManagerError, match="candidate_build_interrupted"):
        manager.build_candidate(_seed_candidate)

    assert manager.pointer_path.read_bytes() == old_pointer
    generation = manager.load_generation("candidate-ready")
    assert generation.state is GenerationState.READY
    assert generation.verified_at == "2026-07-18T00:00:00Z"
    assert manager.resolve_active()[0].generation_id == "legacy-1"


@pytest.mark.parametrize(
    ("corruption", "statement", "parameters"),
    [
        (
            "locator_fingerprint",
            "UPDATE core_resources SET locator_fingerprint=?",
            ("sha256:" + "a" * 64,),
        ),
        (
            "locator_json",
            "UPDATE core_resources SET locator_json=?",
            ('{"path": "fixture"}',),
        ),
        (
            "canonical_json_utf8",
            "UPDATE core_resources SET metadata_json=CAST(X'80' AS TEXT)",
            (),
        ),
        (
            "source",
            "UPDATE core_resources SET source_namespace=?",
            (" ",),
        ),
        (
            "space_fingerprint",
            "UPDATE core_embedding_spaces SET fingerprint=?",
            (" ",),
        ),
        (
            "vector",
            "UPDATE core_unit_embeddings SET embedding=?",
            (b"[0.0, -0.0]",),
        ),
        (
            "representation_producer_fingerprint",
            "UPDATE core_representations SET producer_fingerprint=?",
            (" ",),
        ),
        (
            "facet_producer_fingerprint",
            "UPDATE core_resource_facets SET producer_is_null=1,producer_value=?",
            ("corrupt",),
        ),
    ],
)
def test_candidate_verification_rejects_every_adapter_unreadable_graph_family(
    tmp_path: Path,
    corruption: str,
    statement: str,
    parameters: tuple[object, ...],
) -> None:
    manager = _manager(tmp_path / "store", generation_id=f"invalid-{corruption}")

    def seed_corrupt(connection: sqlite3.Connection) -> None:
        _seed_candidate(connection)
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(statement, parameters)
        connection.commit()

    with pytest.raises(StoreGenerationManagerError, match="candidate_build_failed"):
        manager.build_candidate(
            seed_corrupt,
            fingerprints=(
                GenerationFingerprint("representation", "producer-fingerprint"),
                GenerationFingerprint("space", "space-fingerprint"),
            ),
        )

    assert manager.load_generation(f"invalid-{corruption}").state is GenerationState.FAILED


def test_candidate_verification_rejects_missing_supplied_producer_fingerprint(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path / "store", generation_id="fingerprint-mismatch")

    with pytest.raises(StoreGenerationManagerError, match="candidate_build_failed"):
        manager.build_candidate(
            _seed_candidate,
            fingerprints=(GenerationFingerprint("representation", "missing-fingerprint"),),
        )

    assert manager.load_generation("fingerprint-mismatch").state is GenerationState.FAILED


@pytest.mark.parametrize(
    "graph_shape",
    [
        "resource_without_representation_or_unit",
        "representation_without_unit",
        "valid_resource_plus_orphan_resource",
    ],
)
def test_candidate_verification_rejects_incomplete_per_resource_graphs(
    tmp_path: Path,
    graph_shape: str,
) -> None:
    generation_id = f"incomplete-{graph_shape}"
    manager = _manager(tmp_path / "store", generation_id=generation_id)
    _prepare_legacy(manager)
    old_pointer = manager.pointer_path.read_bytes()

    def seed_incomplete(connection: sqlite3.Connection) -> None:
        if graph_shape == "valid_resource_plus_orphan_resource":
            _seed_candidate(connection)
        _insert_resource(connection, "orphan-resource")
        if graph_shape == "representation_without_unit":
            _insert_representation(connection, "orphan-resource")
        connection.commit()

    with pytest.raises(StoreGenerationManagerError, match="candidate_build_failed"):
        manager.build_candidate(seed_incomplete)

    assert manager.load_generation(generation_id).state is GenerationState.FAILED
    assert manager.pointer_path.read_bytes() == old_pointer
    assert manager.resolve_active()[0].generation_id == "legacy-1"
    database_path = manager.database_path(generation_id)
    connection = sqlite3.connect(f"file:{database_path.as_posix()}?mode=ro", uri=True)
    try:
        assert SQLiteResourceStore(connection).read_resource("orphan-resource") is not None
    finally:
        connection.close()


def test_candidate_verification_accepts_valid_multi_resource_graph(tmp_path: Path) -> None:
    manager = _manager(tmp_path / "store", generation_id="valid-multi-resource")

    def seed_valid_graph(connection: sqlite3.Connection) -> None:
        _seed_candidate(connection)
        _insert_resource(connection, "resource-2")
        representation_id = _insert_representation(connection, "resource-2")
        _insert_search_unit(connection, "resource-2", representation_id)
        connection.commit()

    generation = manager.build_candidate(seed_valid_graph)

    assert generation.state is GenerationState.READY
    database_path = manager.database_path(generation.generation_id)
    connection = sqlite3.connect(f"file:{database_path.as_posix()}?mode=ro", uri=True)
    try:
        store = SQLiteResourceStore(connection)
        assert store.read_resource("resource-1") is not None
        assert store.read_resource("resource-2") is not None
        assert store.read_unit("unit-1") is not None
        assert store.read_unit("unit-resource-2") is not None
    finally:
        connection.close()


def test_candidate_activation_rejects_wrong_v2_contract_metadata_without_switching_pointer(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path / "store", generation_id="wrong-v2-contract")
    _prepare_legacy(manager)
    old_pointer = manager.pointer_path.read_bytes()
    candidate = manager.build_candidate(_seed_candidate)
    corrupt = {
        **json.loads(candidate.to_bytes()),
        "schema_version": "0007",
    }
    manager.metadata_path(candidate.generation_id).write_bytes(
        json.dumps(corrupt, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )

    with pytest.raises(StoreGenerationManagerError, match="candidate_not_ready"):
        manager.activate_candidate(candidate.generation_id)

    assert manager.pointer_path.read_bytes() == old_pointer
    assert manager.resolve_active()[0].generation_id == "legacy-1"


@pytest.mark.parametrize(
    "failure_point",
    [
        "before_pointer_temporary_create",
        "after_pointer_temporary_write",
        "after_pointer_temporary_fsync",
        "before_pointer_replace",
    ],
)
def test_pointer_interruption_before_replace_keeps_old_pointer(
    tmp_path: Path,
    failure_point: str,
) -> None:
    manager = _manager(tmp_path / "store")
    _prepare_legacy(manager)
    candidate = manager.build_candidate(_seed_candidate)
    old_pointer = manager.pointer_path.read_bytes()
    manager.set_failure_hook(_fail_once(failure_point))

    with pytest.raises(RuntimeError, match="PRIVATE_PATH_VECTOR_EXCEPTION_SENTINEL"):
        manager.activate_candidate(candidate.generation_id)

    assert manager.pointer_path.read_bytes() == old_pointer
    assert manager.resolve_active()[0].generation_id == "legacy-1"


@pytest.mark.parametrize("failure_point", ["after_pointer_replace", "after_pointer_directory_fsync"])
def test_pointer_interruption_after_replace_recovers_from_new_durable_pointer(
    tmp_path: Path,
    failure_point: str,
) -> None:
    manager = _manager(tmp_path / "store")
    _prepare_legacy(manager)
    candidate = manager.build_candidate(_seed_candidate)
    manager.set_failure_hook(_fail_once(failure_point))

    with pytest.raises(RuntimeError, match="PRIVATE_PATH_VECTOR_EXCEPTION_SENTINEL"):
        manager.activate_candidate(candidate.generation_id)

    assert manager.resolve_active()[0].generation_id == candidate.generation_id


@pytest.mark.parametrize("corruption", ["missing", "invalid", "non_ready", "wrong_manifest"])
def test_missing_corrupt_non_ready_and_wrong_manifest_pointer_fail_closed(
    tmp_path: Path,
    corruption: str,
) -> None:
    manager = _manager(tmp_path / "store")
    _prepare_legacy(manager)
    candidate = manager.build_candidate(_seed_candidate)
    manager.activate_candidate(candidate.generation_id)

    if corruption == "missing":
        manager.pointer_path.unlink()
    elif corruption == "invalid":
        manager.pointer_path.write_bytes(b"PRIVATE_PATH_VECTOR_EXCEPTION_SENTINEL")
    else:
        generation = manager.load_generation(candidate.generation_id)
        changed = {
            **json.loads(generation.to_bytes()),
            "state": "building" if corruption == "non_ready" else generation.state.value,
            "verified_at": None if corruption == "non_ready" else generation.verified_at,
            "migration_manifest_digest": (
                generation.migration_manifest_digest
                if corruption == "non_ready"
                else "f" * 64
            ),
        }
        manager.metadata_path(candidate.generation_id).write_bytes(
            json.dumps(changed, sort_keys=True, separators=(",", ":")).encode()
        )

    with pytest.raises(StoreGenerationManagerError, match="active_generation_invalid"):
        manager.resolve_active()
    status = manager.status().to_dict()
    assert status["pointer_status"] in {"missing", "invalid"}
    assert "PRIVATE_PATH_VECTOR_EXCEPTION_SENTINEL" not in json.dumps(status)


def test_competing_generation_writer_fails_busy_without_mutation(tmp_path: Path) -> None:
    first = _manager(tmp_path / "store", generation_id="first")
    second = _manager(tmp_path / "store", generation_id="second")

    with first._writer_lease():
        with pytest.raises(StoreGenerationManagerError, match="generation_manager_busy"):
            second.build_candidate(_seed_candidate)

    assert not second.database_path("second").exists()


def test_candidate_failure_logs_and_metadata_expose_only_stable_reason_codes(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    manager = _manager(tmp_path / "store", generation_id="private-safe")

    def fail_rebuild(_connection: sqlite3.Connection) -> None:
        raise RuntimeError("PRIVATE_PATH_VECTOR_EXCEPTION_SENTINEL")

    with pytest.raises(StoreGenerationManagerError, match="candidate_build_failed"):
        manager.build_candidate(fail_rebuild)

    generation = manager.load_generation("private-safe")
    assert generation.failure_reason_code == "rebuild_failed"
    assert "PRIVATE_PATH_VECTOR_EXCEPTION_SENTINEL" not in caplog.text
    assert str(tmp_path) not in caplog.text


def test_candidate_switch_precondition_rejects_a_live_reader_handle(tmp_path: Path) -> None:
    manager = _manager(tmp_path / "store", generation_id="reader-busy")
    readers: list[sqlite3.Connection] = []

    def rebuild_with_reader(connection: sqlite3.Connection) -> None:
        _seed_candidate(connection)
        reader = sqlite3.connect(manager.database_path("reader-busy"))
        reader.execute("BEGIN")
        reader.execute("SELECT * FROM core_resources").fetchall()
        readers.append(reader)

    try:
        with pytest.raises(StoreGenerationManagerError, match="candidate_build_failed"):
            manager.build_candidate(rebuild_with_reader)
    finally:
        for reader in readers:
            reader.close()

    generation = manager.load_generation("reader-busy")
    assert generation.state is GenerationState.FAILED
    assert generation.failure_reason_code == "candidate_checkpoint_busy"
