from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from click.testing import CliRunner

from mdrack.adapters.sqlite.generation_runtime import SQLiteGenerationRuntime
from mdrack.application.generation_manager import StoreGenerationManager
from mdrack.application.vector_values import (
    FLOAT32_VALUE_POLICY,
    canonicalize_float32,
    value_policy_metadata,
)
from mdrack.cli import main
from mdrack.config.models import MDRackConfig, PathsConfig
from mdrack.diagnostics.storage import analyze_application_storage, analyze_standalone_catalog
from mdrack.public_api import MDRackEngine
from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.migrations import (
    apply_candidate_migrations,
    apply_migrations,
    get_migrations_dir,
)
from mdrack_core.domain import (
    EmbeddingSpaceRecord,
    Locator,
    PreparedResourceBatch,
    RepresentationRecord,
    ResourceRecord,
    SearchUnitRecord,
    VectorRecord,
)
from mdrack_sqlite import SQLiteCatalog


def _config() -> MDRackConfig:
    return MDRackConfig(paths=PathsConfig(root=".", store=".mdrack"))


def _create_app_database(root: Path) -> Path:
    database_path = root / ".mdrack" / "knowledge.db"
    database_path.parent.mkdir(parents=True)
    connection = get_connection(database_path)
    try:
        apply_candidate_migrations(connection, get_migrations_dir())
    finally:
        connection.close()
    return database_path


def _create_legacy_app_database(root: Path) -> Path:
    database_path = root / ".mdrack" / "knowledge.db"
    database_path.parent.mkdir(parents=True)
    connection = get_connection(database_path)
    try:
        apply_migrations(connection, get_migrations_dir())
    finally:
        connection.close()
    return database_path


def _create_ready_core_generation(root: Path) -> Path:
    store_dir = root / ".mdrack"
    manager = StoreGenerationManager(store_dir, runtime=SQLiteGenerationRuntime())
    legacy_path = manager.database_path("legacy-1")
    legacy_path.parent.mkdir(parents=True)
    connection = get_connection(legacy_path)
    try:
        apply_migrations(connection, get_migrations_dir())
    finally:
        connection.close()
    manager.register_legacy_generation("legacy-1", retain_through_release="v1.2-compatibility")
    manager.initialize_legacy_pointer("legacy-1")
    candidate = manager.build_candidate(lambda _connection: None)
    manager.activate_candidate(candidate.generation_id)
    return manager.database_path(candidate.generation_id)


def _seed_legacy_vectors(connection: object) -> None:
    execute = getattr(connection, "execute")
    execute(
        "INSERT INTO files (id, relative_path, source_hash, indexed_at) VALUES (?, ?, ?, ?)",
        (
            "legacy-file",
            "PRIVATE_SOURCE_PATH_SENTINEL.md",
            "PRIVATE_SOURCE_HASH_SENTINEL",
            "2026-07-24T00:00:00Z",
        ),
    )
    execute(
        "INSERT INTO chunks (id, file_id, content, content_type, chunk_index) VALUES (?, ?, ?, ?, ?)",
        (
            "legacy-chunk",
            "legacy-file",
            "PRIVATE_TEXT_SENTINEL",
            "text",
            0,
        ),
    )
    execute(
        "INSERT INTO embedding_profiles (name, model, dimensions, endpoint) VALUES (?, ?, ?, ?)",
        (
            "PRIVATE_PROFILE_SENTINEL",
            "PRIVATE_MODEL_SENTINEL",
            2,
            "PRIVATE_ENDPOINT_SENTINEL",
        ),
    )
    execute(
        "INSERT INTO chunk_embeddings (chunk_id, profile_name, embedding, embedded_at) VALUES (?, ?, ?, ?)",
        (
            "legacy-chunk",
            "PRIVATE_PROFILE_SENTINEL",
            b"[1.0,0.0]",
            "2026-07-24T00:00:00Z",
        ),
    )


def _seed_core_vectors(connection: object) -> None:
    execute = getattr(connection, "execute")
    execute(
        """
        INSERT INTO core_resources (
            resource_id, resource_kind, media_type, source_namespace, locator_kind,
            locator_json, locator_fingerprint, content_hash, title, metadata_json, indexed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "core-resource",
            "document",
            "text/plain",
            "PRIVATE_NAMESPACE_SENTINEL",
            "opaque",
            '{"PRIVATE_LOCATOR_SENTINEL":true}',
            "sha256:" + "0" * 64,
            "PRIVATE_CONTENT_HASH_SENTINEL",
            "PRIVATE_TITLE_SENTINEL",
            "PRIVATE_CORRUPT_METADATA_SENTINEL",
            "2026-07-24T00:00:00Z",
        ),
    )
    for ordinal, dimensions, embedding in (
        (0, 2, b"[1.0,0.0]"),
        (1, 3, b"[0.25,1.5,2.75]"),
    ):
        representation_id = f"core-representation-{ordinal}"
        unit_id = f"core-unit-{ordinal}"
        space_id = f"PRIVATE_SPACE_ID_SENTINEL-{ordinal}"
        execute(
            """
            INSERT INTO core_representations (
                representation_id, resource_id, representation_kind, modality, text_content,
                language, producer_fingerprint, token_count, token_count_kind, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                representation_id,
                "core-resource",
                "retrieval_text",
                "text",
                "PRIVATE_REPRESENTATION_TEXT_SENTINEL",
                None,
                None,
                None,
                None,
                "{}",
            ),
        )
        execute(
            """
            INSERT INTO core_search_units (
                unit_id, resource_id, representation_id, unit_kind, modality, text_content,
                evidence_locator_kind, evidence_locator_json, ordinal, token_count,
                token_count_kind, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                unit_id,
                "core-resource",
                representation_id,
                "text_chunk",
                "text",
                "PRIVATE_UNIT_TEXT_SENTINEL",
                "opaque",
                "{}",
                0,
                None,
                None,
                "{}",
            ),
        )
        execute(
            "INSERT INTO core_embedding_spaces (space_id, dimensions, metric, fingerprint, metadata_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                space_id,
                dimensions,
                "cosine",
                f"PRIVATE_FINGERPRINT_SENTINEL-{ordinal}",
                "PRIVATE_CORRUPT_SPACE_METADATA_SENTINEL",
            ),
        )
        execute(
            "INSERT INTO core_unit_embeddings (unit_id, space_id, embedding, embedded_at) VALUES (?, ?, ?, ?)",
            (unit_id, space_id, embedding, "2026-07-24T00:00:00Z"),
        )


def test_application_storage_analyzer_reports_empty_legacy_and_core_metrics(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    database_path = _create_app_database(root)

    empty = analyze_application_storage(root, _config()).to_dict()

    assert empty["readiness"]["state"] == "legacy_only"
    assert empty["records"] == {
        "legacy": {"files": 0, "sections": 0, "chunks": 0, "vectors": 0},
        "core": {"resources": 0, "representations": 0, "units": 0, "spaces": 0, "vectors": 0},
    }
    assert empty["database_bytes"]["main"] == database_path.stat().st_size
    assert empty["vector_payload"]["legacy"]["count"] == 0
    assert empty["vector_payload"]["core"]["count"] == 0


def test_application_storage_analyzer_reports_dual_write_without_private_values(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    database_path = _create_app_database(root)
    connection = get_connection(database_path)
    try:
        _seed_legacy_vectors(connection)
        _seed_core_vectors(connection)
        connection.commit()
    finally:
        connection.close()

    report = analyze_application_storage(root, _config()).to_dict()
    serialized = json.dumps(report, sort_keys=True)

    assert report["records"] == {
        "legacy": {"files": 1, "sections": 0, "chunks": 1, "vectors": 1},
        "core": {"resources": 1, "representations": 2, "units": 2, "spaces": 2, "vectors": 2},
    }
    assert report["duplication"] == {
        "state": "dual_write",
        "legacy_vector_count": 1,
        "core_vector_count": 2,
        "combined_vector_count": 3,
        "legacy_payload_bytes": 9,
        "core_payload_bytes": 24,
        "combined_payload_bytes": 33,
    }
    assert [space["dimensions"] for space in report["vectors_by_space"]] == [2, 2, 3]
    assert all(space["metric"] in {"cosine", None} for space in report["vectors_by_space"])
    assert report["codec_backend_registry"] == [
        {
            "contour": "legacy",
            "codec": "json_utf8",
            "backend": "sqlite_python_exact",
            "vectors": 1,
        },
        {
            "contour": "core",
            "codec": "json_utf8",
            "backend": "sqlite_python_exact",
            "vectors": 2,
        },
    ]
    for sentinel in (
        "PRIVATE_SOURCE_PATH_SENTINEL",
        "PRIVATE_SOURCE_HASH_SENTINEL",
        "PRIVATE_TEXT_SENTINEL",
        "PRIVATE_PROFILE_SENTINEL",
        "PRIVATE_MODEL_SENTINEL",
        "PRIVATE_ENDPOINT_SENTINEL",
        "PRIVATE_NAMESPACE_SENTINEL",
        "PRIVATE_LOCATOR_SENTINEL",
        "PRIVATE_CONTENT_HASH_SENTINEL",
        "PRIVATE_TITLE_SENTINEL",
        "PRIVATE_CORRUPT_METADATA_SENTINEL",
        "PRIVATE_CORRUPT_SPACE_METADATA_SENTINEL",
        "PRIVATE_FINGERPRINT_SENTINEL",
        "PRIVATE_SPACE_ID_SENTINEL",
        str(root),
        str(database_path),
    ):
        assert sentinel not in serialized


def test_application_storage_analyzer_selects_the_ready_active_generation(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    active_database = _create_ready_core_generation(root)

    report = analyze_application_storage(root, _config())

    assert report.readiness == {"state": "ready"}
    assert report.database_bytes["main"] == active_database.stat().st_size
    assert report.records["core"]["resources"] == 0


def test_standalone_catalog_analyzer_reports_core_only_metrics(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    catalog = SQLiteCatalog.create(database_path)
    try:
        _seed_core_vectors(catalog.connection)
        catalog.connection.commit()
    finally:
        catalog.close()

    report = analyze_standalone_catalog(database_path).to_dict()

    assert report["readiness"] == {"state": "ready"}
    assert report["records"]["legacy"] == {"files": 0, "sections": 0, "chunks": 0, "vectors": 0}
    assert report["records"]["core"] == {
        "resources": 1,
        "representations": 2,
        "units": 2,
        "spaces": 2,
        "vectors": 2,
    }
    assert report["duplication"]["state"] == "core_only"
    assert report["database_bytes"]["main"] == database_path.stat().st_size


def test_storage_analyzer_reports_real_wal_and_shm_bytes_without_writing(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    database_path = _create_app_database(root)

    before = analyze_application_storage(root, _config()).to_dict()
    assert before["database_bytes"]["wal"] == 0
    assert before["database_bytes"]["shm"] == 0

    writer = get_connection(database_path)
    try:
        writer.execute("CREATE TABLE wal_probe (id INTEGER PRIMARY KEY)")
        writer.execute("INSERT INTO wal_probe (id) VALUES (1)")
        writer.commit()
        report = analyze_application_storage(root, _config()).to_dict()
        wal_path = database_path.with_name(f"{database_path.name}-wal")
        shm_path = database_path.with_name(f"{database_path.name}-shm")
        assert report["database_bytes"]["wal"] == wal_path.stat().st_size
        assert report["database_bytes"]["shm"] == shm_path.stat().st_size
    finally:
        writer.close()


def test_standalone_storage_analyzer_reports_the_actual_v2_f32_codec_and_backend(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "f32-catalog.sqlite3"
    canonical = canonicalize_float32((1.0 + 2**-30, 0.0))
    space = EmbeddingSpaceRecord(
        "f32-space",
        2,
        "cosine",
        "f32-fingerprint",
        value_policy_metadata(FLOAT32_VALUE_POLICY),
    )
    catalog = SQLiteCatalog.create_v2(database_path)
    try:
        catalog.replace_resource(
            PreparedResourceBatch(
                ResourceRecord(
                    "f32-resource",
                    "document",
                    "text/plain",
                    "fixture",
                    Locator("fixture", {"resource_id": "f32-resource"}),
                ),
                (
                    RepresentationRecord(
                        "f32-representation",
                        "f32-resource",
                        "retrieval_text",
                        "text",
                        "float32 analyzer resource",
                    ),
                ),
                (
                    SearchUnitRecord(
                        "f32-unit",
                        "f32-resource",
                        "f32-representation",
                        "text_chunk",
                        "text",
                        "float32 analyzer resource",
                        Locator("fixture", {"unit_id": "f32-unit"}),
                        0,
                    ),
                ),
                (space,),
                (VectorRecord("f32-unit", "f32-space", canonical),),
            )
        )
        backend_id = "builtin-exact-v1"
    finally:
        catalog.close()

    report = analyze_standalone_catalog(database_path).to_dict()

    assert report["vector_payload"]["core"] == {
        "count": 1,
        "total_bytes": 8,
        "min_bytes": 8,
        "median_bytes": 8.0,
        "p95_bytes": 8,
        "max_bytes": 8,
    }
    assert report["codec_backend_registry"] == [
        {
            "contour": "core",
            "codec": "ieee754-f32-le-v1",
            "backend": backend_id,
            "vectors": 1,
        }
    ]


def test_cli_and_engine_storage_analyzer_have_exact_safe_parity(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    _create_legacy_app_database(root)
    config = _config()

    with MDRackEngine(root=root, config=config) as engine:
        api_data = engine.analyze_storage().to_dict()
        result = CliRunner().invoke(main, ["--root", str(root), "storage-analyze"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload == {
        "ok": True,
        "data": api_data,
        "meta": {"command": "storage-analyze"},
    }
    assert str(root) not in result.output


def test_storage_analyzer_cli_failure_is_fixed_and_private(tmp_path: Path) -> None:
    catalog_path = tmp_path / "PRIVATE_MISSING_CATALOG_SENTINEL.sqlite3"

    result = CliRunner().invoke(main, ["storage-analyze", "--catalog", str(catalog_path)])

    assert result.exit_code == 1
    assert json.loads(result.output) == {
        "ok": False,
        "error": {
            "message": "Storage analysis could not be completed",
            "code": "STORAGE_ANALYSIS_ERROR",
        },
        "meta": {"command": "storage-analyze"},
    }
    assert str(catalog_path) not in result.output
    assert "PRIVATE_MISSING_CATALOG_SENTINEL" not in result.output


def test_offline_installed_wheel_runs_storage_analyzer_outside_source_tree(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[2]
    uv = shutil.which("uv")
    assert uv is not None
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment["UV_OFFLINE"] = "1"

    catalog_path = tmp_path / "PRIVATE_CATALOG_PATH_SENTINEL.sqlite3"
    catalog = SQLiteCatalog.create(catalog_path)
    catalog.close()

    wheel_dir = tmp_path / "wheels"
    subprocess.run(
        [uv, "build", "--wheel", "--all-packages", "--out-dir", str(wheel_dir)],
        cwd=repository,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    wheels = tuple(wheel_dir.glob("mdrack-*.whl"))
    core_wheels = tuple(wheel_dir.glob("mdrack_core-*.whl"))
    media_wheels = tuple(wheel_dir.glob("mdrack_media-*.whl"))
    sqlite_wheels = tuple(wheel_dir.glob("mdrack_sqlite-*.whl"))
    assert len(wheels) == 1
    assert len(core_wheels) == 1
    assert len(media_wheels) == 1
    assert len(sqlite_wheels) == 1

    virtualenv = tmp_path / "venv"
    subprocess.run(
        [uv, "venv", "--python", sys.executable, str(virtualenv)],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    python = virtualenv / "bin" / "python"
    source_site_packages = (
        Path(sys.prefix)
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
    )
    target_site_packages = (
        virtualenv
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
    )
    assert source_site_packages.is_dir()
    (target_site_packages / "mdrack-test-dependencies.pth").write_text(
        f"{source_site_packages}\n",
        encoding="utf-8",
    )
    subprocess.run(
        [
            uv,
            "pip",
            "install",
            "--python",
            str(python),
            "--no-deps",
            str(core_wheels[0]),
            str(media_wheels[0]),
            str(sqlite_wheels[0]),
            str(wheels[0]),
        ],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    result = subprocess.run(
        [virtualenv / "bin" / "mdrack", "storage-analyze", "--catalog", str(catalog_path)],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["meta"] == {"command": "storage-analyze"}
    assert payload["data"]["readiness"] == {"state": "ready"}
    assert payload["data"]["records"]["core"]["resources"] == 0
    assert str(catalog_path) not in result.stdout + result.stderr
    assert "PRIVATE_CATALOG_PATH_SENTINEL" not in result.stdout + result.stderr

    origin = subprocess.run(
        [python, "-c", "import mdrack; print(mdrack.__file__)"],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "site-packages" in origin.stdout
    assert str(repository) not in origin.stdout
