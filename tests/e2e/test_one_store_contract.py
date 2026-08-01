"""RED acceptance contract for MDRack's one-start, one-catalog recovery."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import shutil
import subprocess
import sys
import threading
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import click
import pytest
from click.testing import CliRunner

import mdrack.adapters.sqlite.canonical_catalog as canonical_catalog_module
import mdrack.diagnostics.storage as storage_diagnostics
from mdrack.adapters.sqlite.canonical_catalog import open_application_catalog
from mdrack.application.compatibility import (
    ApplicationStoreError,
    create_application_storage,
    embedding_space_id,
)
from mdrack.application.resources import UnifiedTextScopeName
from mdrack.cli import main
from mdrack.config.models import MDRackConfig
from mdrack.embeddings.fake import FakeEmbeddingProvider
from mdrack.embeddings.runtime import embedding_profile_from_config
from mdrack.eval.privacy import build_safe_diagnostic_record, scan_privacy, serialize_safe_json
from mdrack.ingestion.images import ExtractedImageText, ImageIngestionService, StaticImageExtractor
from mdrack.ingestion.media_manifests import read_video_resource_manifest
from mdrack.ingestion.transcripts import read_whisper_json
from mdrack.public_api import MDRackEngine
from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.migrations import apply_candidate_migrations, get_migrations_dir
from mdrack_media import ProducerFingerprint, resource_id
from mdrack_sqlite import SQLiteCatalog
from mdrack_sqlite.contract_v2 import SQLITE_CATALOG_V2_SCHEMA_ID

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = REPOSITORY_ROOT / "tests" / "fixtures" / "one_store_v1"
MANIFEST_PATH = FIXTURE_ROOT / "manifest.json"


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _fixture_manifest() -> dict[str, Any]:
    return _load_json(MANIFEST_PATH)


def _payload_hashes(manifest: dict[str, Any]) -> dict[str, str]:
    payload_hashes = manifest["payload_sha256"]
    assert isinstance(payload_hashes, dict)
    assert all(isinstance(path, str) and isinstance(digest, str) for path, digest in payload_hashes.items())
    return payload_hashes


def _copy_fixture(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    root = tmp_path / "fixture-root"
    shutil.copytree(FIXTURE_ROOT, root)
    manifest = _fixture_manifest()
    return root, {
        relative_path: hashlib.sha256((root / relative_path).read_bytes()).hexdigest()
        for relative_path in _payload_hashes(manifest)
    }


def _assert_fixture_hashes(root: Path, expected_hashes: dict[str, str]) -> None:
    actual_hashes = {
        relative_path: hashlib.sha256((root / relative_path).read_bytes()).hexdigest()
        for relative_path in expected_hashes
    }
    assert actual_hashes == expected_hashes


def _invoke(runner: CliRunner, root: Path, *args: str) -> dict[str, Any]:
    result = runner.invoke(main, ["--root", str(root), *args])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert isinstance(payload, dict)
    assert payload["ok"] is True
    data = payload["data"]
    assert isinstance(data, dict)
    return data


def _sqlite_topology(store_dir: Path) -> tuple[str, ...]:
    if not store_dir.exists():
        return ()
    reserved_suffixes = set(_fixture_manifest()["topology"]["reserved_sqlite_main_suffixes"])
    return tuple(
        sorted(
            path.relative_to(store_dir).as_posix()
            for path in store_dir.rglob("*")
            if path.is_file()
            and (
                path.read_bytes()[:16] == b"SQLite format 3\x00"
                or path.suffix in reserved_suffixes
            )
        )
    )


def _assert_one_catalog_topology(root: Path) -> None:
    manifest = _fixture_manifest()
    topology = _sqlite_topology(root / ".mdrack")
    assert topology == ("catalog.sqlite3",), (
        "one-store topology requires exactly catalog.sqlite3; "
        f"observed {topology!r}"
    )
    for forbidden_path in manifest["topology"]["forbidden_paths"]:
        assert isinstance(forbidden_path, str)
        assert not (root / forbidden_path).exists(), (
            "one-store topology contains a forbidden lifecycle artifact: "
            f"{forbidden_path}"
        )


def _walk_commands(
    command: click.Command,
    prefix: tuple[str, ...] = (),
) -> Iterator[tuple[tuple[str, ...], click.Command]]:
    yield prefix, command
    if isinstance(command, click.Group):
        for name, child in command.commands.items():
            yield from _walk_commands(child, (*prefix, name))


def _option_owners(command: click.Command, option: str) -> tuple[str, ...]:
    return tuple(
        " ".join(path) or "main"
        for path, candidate in _walk_commands(command)
        if any(option in parameter.opts for parameter in candidate.params if isinstance(parameter, click.Option))
    )


def _result_items(payload: object) -> list[dict[str, object]]:
    if not isinstance(payload, dict):
        return []
    results = payload.get("results")
    if not isinstance(results, list):
        return []
    return [item for item in results if isinstance(item, dict)]


@pytest.mark.e2e
def test_fixture_manifest_is_complete_and_byte_frozen() -> None:
    manifest = _fixture_manifest()
    payload_hashes = _payload_hashes(manifest)
    actual_paths = {
        path.relative_to(FIXTURE_ROOT).as_posix()
        for path in FIXTURE_ROOT.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    }

    assert manifest["schema"] == "mdrack.one-store-fixture.v1"
    assert manifest["fixture_version"] == "1.0.0"
    assert manifest["provenance"]["candidate_results_allowed"] is False
    assert manifest["manifest_self_hash"] == "excluded_to_avoid_self_reference"
    assert set(payload_hashes) == actual_paths
    assert all(digest.startswith("sha256:") and len(digest) == 71 for digest in payload_hashes.values())
    assert all(
        "sha256:" + hashlib.sha256((FIXTURE_ROOT / path).read_bytes()).hexdigest() == digest
        for path, digest in payload_hashes.items()
    )
    assert (FIXTURE_ROOT / "images" / "image.png").read_bytes() == (
        FIXTURE_ROOT / "images" / "image-copy.png"
    ).read_bytes()
    audio = read_whisper_json(
        (FIXTURE_ROOT / "transcripts" / "audio.whisper.json").read_bytes(),
        resource_id=resource_id("one-store-fixture", "audio-001"),
        producer_fingerprint=ProducerFingerprint.from_payload({"fixture": "one-store-audio-v1"}),
        strict=True,
    )
    assert len(audio.artifact.atoms) == 2
    assert all(
        read_video_resource_manifest((FIXTURE_ROOT / "video" / name).read_bytes()) is not None
        for name in ("video-resource.json", "video-resource-near.json")
    )

    queries = _load_json(FIXTURE_ROOT / "queries.json")
    cells = queries["similarity_matrix"]
    assert isinstance(cells, list) and len(cells) == manifest["similarity_contract"]["required_cells"] == 16
    assert {
        (cell["source_kind"], cell["target_kind"])
        for cell in cells
        if isinstance(cell, dict)
    } == {
        (source_kind, target_kind)
        for source_kind in manifest["similarity_contract"]["source_kinds"]
        for target_kind in manifest["similarity_contract"]["source_kinds"]
    }


@pytest.mark.e2e
def test_first_init_creates_only_canonical_catalog(tmp_path: Path) -> None:
    root, _ = _copy_fixture(tmp_path)
    _invoke(CliRunner(), root, "init")

    catalog = SQLiteCatalog.open(root / ".mdrack" / "catalog.sqlite3")
    try:
        assert [row[0] for row in catalog.connection.execute("PRAGMA integrity_check")] == ["ok"]
        assert catalog.connection.execute("PRAGMA foreign_key_check").fetchone() is None
        catalog.verify()
    finally:
        catalog.close()
    _assert_one_catalog_topology(root)


@pytest.mark.e2e
def test_failed_initial_catalog_create_cleans_database_files_and_preserves_root_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    hermes_marker = root / ".hermes" / "state.json"
    hermes_marker.parent.mkdir()
    hermes_marker.write_text('{"keep": true}', encoding="utf-8")
    original_create = SQLiteCatalog.create_v2

    def fail_after_reservation(point: str) -> None:
        if point == "after_exclusive_create":
            raise RuntimeError("injected_create_failure")

    def injected_create(catalog_path: Path) -> SQLiteCatalog:
        return original_create(catalog_path, failure_hook=fail_after_reservation)

    monkeypatch.setattr(SQLiteCatalog, "create_v2", injected_create)
    with pytest.raises(ApplicationStoreError, match="catalog_create_failed"):
        open_application_catalog(root, MDRackConfig(), create=True)

    assert _sqlite_topology(root / ".mdrack") == ()
    assert not (root / ".mdrack" / "catalog.sqlite3-wal").exists()
    assert not (root / ".mdrack" / "catalog.sqlite3-shm").exists()
    assert hermes_marker.read_text(encoding="utf-8") == '{"keep": true}'


@pytest.mark.e2e
@pytest.mark.parametrize("legacy_kind", ["v1", "bridge"])
def test_existing_fixed_path_rejects_non_v2_catalog(
    tmp_path: Path,
    legacy_kind: str,
) -> None:
    root = tmp_path / legacy_kind
    store_dir = root / ".mdrack"
    store_dir.mkdir(parents=True)
    catalog_path = store_dir / "catalog.sqlite3"
    if legacy_kind == "v1":
        SQLiteCatalog.create(catalog_path).close()
    else:
        connection = get_connection(catalog_path)
        try:
            apply_candidate_migrations(connection, get_migrations_dir())
        finally:
            connection.close()

    with pytest.raises(ApplicationStoreError, match="catalog_schema_unsupported"):
        open_application_catalog(root, MDRackConfig(), create=True)

    runner = CliRunner()
    for command in (("init",), ("status",), ("doctor",), ("benchmark",), ("storage-analyze",)):
        result = runner.invoke(main, ["--root", str(root), *command])
        assert result.exit_code != 0, (command, result.output)
        payload = json.loads(result.output)
        assert payload["ok"] is False, command

    assert _sqlite_topology(store_dir) == ("catalog.sqlite3",)


@pytest.mark.e2e
@pytest.mark.parametrize(
    ("extra_name", "expected_error"),
    [
        ("knowledge.db", "legacy_store_unsupported"),
        ("other.sqlite3", "multiple_sqlite_stores_unsupported"),
        ("empty.sqlite3", "multiple_sqlite_stores_unsupported"),
    ],
)
def test_existing_catalog_rejects_mixed_sqlite_topology(
    tmp_path: Path,
    extra_name: str,
    expected_error: str,
) -> None:
    root = tmp_path / "root"
    store_dir = root / ".mdrack"
    store_dir.mkdir(parents=True)
    SQLiteCatalog.create_v2(store_dir / "catalog.sqlite3").close()
    if extra_name == "empty.sqlite3":
        (store_dir / extra_name).touch()
    else:
        SQLiteCatalog.create(store_dir / extra_name).close()

    with pytest.raises(ApplicationStoreError, match=expected_error):
        open_application_catalog(root, MDRackConfig(), create=True)
    with pytest.raises(ApplicationStoreError, match=expected_error):
        open_application_catalog(root, MDRackConfig(), create=False)

    runner = CliRunner()
    for command in (("init",), ("status",), ("scan", "--provider", "fake")):
        result = runner.invoke(main, ["--root", str(root), *command])
        assert result.exit_code != 0, (command, result.output)
        payload = json.loads(result.output)
        assert payload["ok"] is False, command

    engine = MDRackEngine(
        root=root,
        config=MDRackConfig(),
        embedding_provider=FakeEmbeddingProvider(dimensions=MDRackConfig().embedding.dimensions),
    )
    try:
        with pytest.raises(ApplicationStoreError, match=expected_error):
            engine.scan()
    finally:
        engine.close()

    assert _sqlite_topology(store_dir) == ("catalog.sqlite3", extra_name)


@pytest.mark.e2e
def test_concurrent_first_create_waits_for_verified_v2_catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    reserved = threading.Event()
    release_creator = threading.Event()
    original_create = SQLiteCatalog.create_v2
    original_open = SQLiteCatalog.open
    opener_attempted = threading.Event()
    schema_ids: list[str] = []
    errors: list[BaseException] = []

    def pause_after_reservation(point: str) -> None:
        if point == "after_exclusive_create":
            reserved.set()
            if not release_creator.wait(timeout=5):
                raise RuntimeError("creator release timed out")

    def paused_create(catalog_path: Path) -> SQLiteCatalog:
        return original_create(catalog_path, failure_hook=pause_after_reservation)

    def observed_open(catalog_path: Path) -> SQLiteCatalog:
        opener_attempted.set()
        return original_open(catalog_path)

    def open_catalog() -> None:
        try:
            catalog = open_application_catalog(root, MDRackConfig(), create=True)
            try:
                schema_ids.append(catalog.schema_id)
            finally:
                catalog.close()
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    monkeypatch.setattr(SQLiteCatalog, "create_v2", paused_create)
    monkeypatch.setattr(SQLiteCatalog, "open", staticmethod(observed_open))
    creator = threading.Thread(target=open_catalog)
    creator.start()
    assert reserved.wait(timeout=5)

    opener = threading.Thread(target=open_catalog)
    opener.start()
    assert opener_attempted.wait(timeout=5)
    release_creator.set()
    creator.join(timeout=5)
    opener.join(timeout=5)

    assert not creator.is_alive() and not opener.is_alive()
    assert errors == []
    assert schema_ids == [SQLITE_CATALOG_V2_SCHEMA_ID] * 2
    _assert_one_catalog_topology(root)


@pytest.mark.e2e
def test_incomplete_reserved_catalog_fails_after_bounded_wait(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    store_dir = root / ".mdrack"
    store_dir.mkdir(parents=True)
    (store_dir / "catalog.sqlite3").touch()
    monkeypatch.setattr(canonical_catalog_module, "_RACE_OPEN_TIMEOUT_SECONDS", 0.03)
    monkeypatch.setattr(canonical_catalog_module, "_RACE_OPEN_INTERVAL_SECONDS", 0.005)

    with pytest.raises(ApplicationStoreError, match="catalog_open_after_create_failed"):
        open_application_catalog(root, MDRackConfig(), create=False)


@pytest.mark.e2e
@pytest.mark.parametrize("symlink_kind", ["catalog", "store"])
def test_application_store_rejects_symlinked_physical_paths(
    tmp_path: Path,
    symlink_kind: str,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    SQLiteCatalog.create_v2(outside / "catalog.sqlite3").close()
    if symlink_kind == "store":
        (root / ".mdrack").symlink_to(outside, target_is_directory=True)
    else:
        store_dir = root / ".mdrack"
        store_dir.mkdir()
        (store_dir / "catalog.sqlite3").symlink_to(outside / "catalog.sqlite3")

    with pytest.raises(ApplicationStoreError, match="catalog_path_invalid"):
        open_application_catalog(root, MDRackConfig(), create=False)


@pytest.mark.e2e
def test_engine_scan_creates_only_canonical_catalog(tmp_path: Path) -> None:
    root, _ = _copy_fixture(tmp_path)
    config = MDRackConfig()
    provider = FakeEmbeddingProvider(dimensions=config.embedding.dimensions)

    with MDRackEngine(root=root, config=config, embedding_provider=provider) as engine:
        engine.scan()

    _assert_one_catalog_topology(root)


@pytest.mark.e2e
def test_engine_scan_then_text_read_uses_the_same_catalog(tmp_path: Path) -> None:
    root, _ = _copy_fixture(tmp_path)
    config = MDRackConfig()
    provider = FakeEmbeddingProvider(dimensions=config.embedding.dimensions)

    with MDRackEngine(root=root, config=config, embedding_provider=provider) as engine:
        scan = engine.scan()
        result = engine.search_text("canonical one-store recovery")

    assert scan.status == "success"
    assert result.total_count >= 1
    assert result.results
    _assert_one_catalog_topology(root)


@pytest.mark.e2e
def test_engine_read_of_missing_catalog_fails_without_creating_a_database(tmp_path: Path) -> None:
    root = tmp_path / "empty-root"
    root.mkdir()
    engine = MDRackEngine(root=root, config=MDRackConfig())
    try:
        with pytest.raises(ApplicationStoreError, match="catalog_missing"):
            engine.search_text("not indexed")
    finally:
        engine.close()

    assert _sqlite_topology(root / ".mdrack") == ()


@pytest.mark.e2e
def test_public_surface_ledger_has_no_normal_candidate_or_catalog_bypass() -> None:
    manifest = _fixture_manifest()
    ledger = manifest["public_surface_ledger"]
    assert isinstance(ledger, dict)

    retained_commands = {" ".join(path) for path, _command in _walk_commands(main) if path}
    removed_commands = set(ledger["removed_cli_paths"])
    assert retained_commands == set(ledger["retained_cli_paths"])
    assert not retained_commands & removed_commands

    retained_methods = {
        name
        for name in dir(MDRackEngine)
        if not name.startswith("_") and callable(getattr(MDRackEngine, name))
    }
    removed_methods = set(ledger["removed_engine_methods"])
    assert retained_methods == set(ledger["retained_engine_methods"])
    assert not retained_methods & removed_methods
    assert set(ledger["catalog_backed_cli_paths"]) <= retained_commands
    assert set(ledger["catalog_backed_engine_methods"]) <= retained_methods

    for option in ledger["normal_application_forbidden_options"]:
        assert isinstance(option, str)
        owners = _option_owners(main, option)
        assert not owners, f"RED public-surface ledger: {option} remains exposed by {owners!r}"


@pytest.mark.e2e
def test_production_runtime_has_no_legacy_store_or_generation_composition() -> None:
    """Keep deleted lifecycle code from surviving as an unregistered product path."""
    source_root = REPOSITORY_ROOT / "src" / "mdrack"
    canonical_lifecycle = source_root / "adapters" / "sqlite" / "canonical_catalog.py"

    assert not (source_root / "application" / "fresh_reindex.py").exists()
    assert not (source_root / "cli" / "commands" / "sections.py").exists()
    assert (source_root / "cli" / "commands" / "resource.py").is_file()
    assert (source_root / "cli" / "commands" / "eval.py").is_file()
    for source_path in sorted(source_root.rglob("*.py")):
        source = source_path.read_text(encoding="utf-8")
        assert "generation_manager" not in source, source_path
        assert "store_generations" not in source, source_path
        assert "generation_runtime" not in source, source_path
        assert '@click.option("--catalog"' not in source, source_path
        if source_path != canonical_lifecycle:
            assert "knowledge.db" not in source, source_path
            assert "active-generation.json" not in source, source_path
            assert '"generations"' not in source, source_path


@pytest.mark.e2e
def test_current_public_docs_describe_only_the_fixed_catalog() -> None:
    """Operational/API guidance must not prescribe the removed lifecycle."""
    docs_root = REPOSITORY_ROOT / "docs"
    operations = (docs_root / "operations.md").read_text(encoding="utf-8")
    getting_started = (docs_root / "getting-started.md").read_text(encoding="utf-8")
    development = (docs_root / "development.md").read_text(encoding="utf-8")
    unified_contract = (docs_root / "contracts" / "v1.2-unified-search.md").read_text(encoding="utf-8")
    interfaces = (docs_root / "current-architecture" / "public-interfaces.md").read_text(encoding="utf-8")

    assert "catalog.sqlite3" in operations
    assert "storage rebuild-fresh" not in operations
    assert "storage verify" not in operations
    assert "storage activate" not in operations
    assert "generation pointers" not in operations
    assert "recovery.md" not in getting_started
    assert "generation orchestration" not in development
    assert "SQLite/schema/generations" not in development
    assert "ready resource-core generation" not in unified_contract
    assert "ready generation" not in unified_contract
    assert "search --catalog" not in unified_contract
    assert (
        "It does not expose CLI diagnostics, status, model lifecycle, rebuild, benchmark,\n"
        "or storage-analysis methods."
    ) not in interfaces


@pytest.mark.e2e
def test_ported_file_and_chunk_readers_use_only_the_fixed_catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, source_hashes = _copy_fixture(tmp_path)
    runner = CliRunner()
    _invoke(runner, root, "init")
    _invoke(runner, root, "scan", "--provider", "fake")

    def legacy_connection_opened(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("legacy SQLite reader was opened")

    monkeypatch.setattr("mdrack.storage.sqlite.connection.get_connection", legacy_connection_opened)
    listed = _invoke(runner, root, "files", "list")
    file_records = listed["files"]
    assert isinstance(file_records, list) and file_records
    file_record = file_records[0]
    assert isinstance(file_record, dict)
    file_id = str(file_record["logical_id"])

    info = _invoke(runner, root, "files", "info", file_id)
    file_read = _invoke(runner, root, "read", "file", file_id)
    assert info["file"] == file_read["file"] == file_record
    assert "sections" not in file_read

    search = _invoke(runner, root, "search", "canonical one-store", "--mode", "text", "--provider", "fake")
    results = search["results"]
    assert isinstance(results, list) and results
    first_result = results[0]
    assert isinstance(first_result, dict)
    chunk_id = str(first_result["logical_id"])
    chunk_read = _invoke(runner, root, "read", "chunk", chunk_id, "--context", "neighbors")
    assert chunk_read["chunk"]["logical_id"] == chunk_id
    assert isinstance(chunk_read["neighbors"], list)

    _assert_one_catalog_topology(root)
    _assert_fixture_hashes(root, source_hashes)


@pytest.mark.e2e
def test_storage_analysis_uses_the_verified_catalog_handle_until_complete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A replacement after verification must not redirect diagnostic reads to v1."""
    root, _ = _copy_fixture(tmp_path)
    runner = CliRunner()
    _invoke(runner, root, "init")
    _invoke(runner, root, "scan", "--provider", "fake")

    catalog_path = root / ".mdrack" / "catalog.sqlite3"
    original_open = storage_diagnostics.open_application_catalog_readonly
    original_close = SQLiteCatalog.close
    verified_catalog: SQLiteCatalog | None = None

    def swap_on_close(catalog: SQLiteCatalog) -> None:
        original_close(catalog)
        if catalog is not verified_catalog:
            return
        for path in (
            catalog_path.with_name(f"{catalog_path.name}-wal"),
            catalog_path.with_name(f"{catalog_path.name}-shm"),
            catalog_path,
        ):
            path.unlink(missing_ok=True)
        SQLiteCatalog.create(catalog_path).close()

    def open_verified_then_swap(root: Path, config: MDRackConfig) -> SQLiteCatalog:
        nonlocal verified_catalog
        verified_catalog = original_open(root, config)
        return verified_catalog

    monkeypatch.setattr(storage_diagnostics, "open_application_catalog_readonly", open_verified_then_swap)
    monkeypatch.setattr(SQLiteCatalog, "close", swap_on_close)

    report = storage_diagnostics.analyze_application_storage(root, MDRackConfig())

    assert verified_catalog is not None and verified_catalog.closed
    assert report.records["core"]["resources"] >= 1


@pytest.mark.e2e
def test_status_doctor_and_storage_analysis_read_the_fixed_catalog(tmp_path: Path) -> None:
    """Retained diagnostics agree with the engine and do not create another store."""
    root, source_hashes = _copy_fixture(tmp_path)
    runner = CliRunner()
    _invoke(runner, root, "init")
    _invoke(runner, root, "scan", "--provider", "fake")

    status = _invoke(runner, root, "status")
    doctor = _invoke(runner, root, "doctor")
    cli_analysis = _invoke(runner, root, "storage-analyze")
    with MDRackEngine(root=root, config=MDRackConfig()) as engine:
        engine_analysis = engine.analyze_storage().to_dict()

    assert status["catalog_state"] == "ready"
    assert "generation_state" not in status
    assert doctor["ok"] is True
    assert {finding["code"] for finding in doctor["findings"]} >= {
        "RESOURCE_CORE_V2_SCHEMA_LATEST",
        "RESOURCE_CORE_V2_INTEGRITY_OK",
    }
    assert cli_analysis == engine_analysis
    assert cli_analysis["readiness"] == {"state": "ready"}
    assert cli_analysis["records"]["core"]["resources"] >= 1
    _assert_one_catalog_topology(root)
    _assert_fixture_hashes(root, source_hashes)


@pytest.mark.e2e
def test_fresh_process_reopens_the_same_catalog(tmp_path: Path) -> None:
    root, _ = _copy_fixture(tmp_path)
    _invoke(CliRunner(), root, "init")

    command = (
        "import json,sys; from click.testing import CliRunner; from mdrack.cli import main; "
        "result=CliRunner().invoke(main,['--root',sys.argv[1],'status']); "
        "print(result.output,end=''); raise SystemExit(result.exit_code)"
    )
    reopened = subprocess.run(
        [sys.executable, "-c", command, str(root)],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    assert reopened.returncode == 0, reopened.stdout + reopened.stderr
    reopened_payload = json.loads(reopened.stdout)
    assert reopened_payload["ok"] is True

    _assert_one_catalog_topology(root)


@pytest.mark.e2e
def test_selected_resource_similarity_requires_all_16_textual_cells(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise all textual resource kinds through the one normal catalog."""

    class FixtureFeatureEmbeddingProvider(FakeEmbeddingProvider):
        async def embed(self, texts: Sequence[str], profile: str = "default") -> list[list[float]]:
            del profile
            return [self._feature_vector(text) for text in texts]

        async def embed_query(self, text: str, profile: str = "default") -> list[float]:
            del profile
            return self._feature_vector(text)

        def _feature_vector(self, text: str) -> list[float]:
            normalized = text.casefold()
            vector = [0.0] * self.dimensions
            vector[1 if "near match" in normalized or "reopen after first initialization" in normalized else 0] = 1.0
            return vector

    def create_fixture_provider(_name: str, config: MDRackConfig) -> FixtureFeatureEmbeddingProvider:
        return FixtureFeatureEmbeddingProvider(dimensions=config.embedding.dimensions)

    for factory_path in (
        "mdrack.cli.commands.scan.create_embedding_provider",
        "mdrack.cli.commands.images.create_embedding_provider",
        "mdrack.cli.commands.transcript.create_embedding_provider",
        "mdrack.cli.commands.video.create_embedding_provider",
    ):
        monkeypatch.setattr(factory_path, create_fixture_provider)

    root, source_hashes = _copy_fixture(tmp_path)
    runner = CliRunner()
    _invoke(runner, root, "init")
    _invoke(runner, root, "scan", "--provider", "fake")


    prepared_image = _load_json(root / "prepared" / "image-caption-ocr.json")
    for resource_id_value, image_name in (
        ("one-store-image-001", "image.png"),
        ("one-store-image-001-copy", "image-copy.png"),
    ):
        _invoke(
            runner,
            root,
            "image",
            "ingest",
            str(root / "images" / image_name),
            "--resource-id",
            resource_id_value,
            "--source-namespace",
            str(prepared_image["source_namespace"]),
            "--source-ref",
            f"{prepared_image['source_ref']}-{resource_id_value}",
            "--caption",
            str(prepared_image["caption"]),
            "--ocr",
            str(prepared_image["ocr"]),
            "--provider",
            "fake",
        )

    audio_resource_id = resource_id("one-store-fixture", "audio-001")
    for resource_id_value, source_ref, transcript_name in (
        (audio_resource_id, "audio-001", "audio.whisper.json"),
        (resource_id("one-store-fixture", "audio-002"), "audio-002", "audio-near.whisper.json"),
    ):
        _invoke(
            runner,
            root,
            "ingest",
            "transcript",
            str(root / "transcripts" / transcript_name),
            "--resource-id",
            resource_id_value,
            "--kind",
            "audio",
            "--media-type",
            "audio/wav",
            "--namespace",
            "one-store-fixture",
            "--source-ref",
            source_ref,
            "--provider",
            "fake",
        )

    for video_name in ("video-resource.json", "video-resource-near.json"):
        _invoke(runner, root, "ingest", "video", str(root / "video" / video_name), "--provider", "fake")

    queries = _load_json(root / "queries.json")
    matrix = queries["similarity_matrix"]
    assert isinstance(matrix, list) and len(matrix) == 16
    assert all(isinstance(cell, dict) for cell in matrix)
    matrix_cells = [cell for cell in matrix if isinstance(cell, dict)]
    expected_target_rank = _fixture_manifest()["similarity_contract"]["exact_target_rank"]
    assert expected_target_rank == 1

    config = MDRackConfig()
    provider = FixtureFeatureEmbeddingProvider(dimensions=config.embedding.dimensions)
    profile = embedding_profile_from_config(config, provider, "default")
    scope_by_target_kind: dict[str, UnifiedTextScopeName] = {
        "document": "notes",
        "image": "images",
        "audio": "audio",
        "video_with_frame_text": "video",
    }
    resource_kind_by_target_kind = {
        "document": "document",
        "image": "image",
        "audio": "audio",
        "video_with_frame_text": "video",
    }
    observed_orders: dict[tuple[str, str], tuple[tuple[str, str, int], ...]] = {}
    search_cases = queries["search_cases"]
    assert isinstance(search_cases, list) and len(search_cases) == 4
    assert all(isinstance(case, dict) for case in search_cases)
    with MDRackEngine(root=root, config=config, embedding_provider=provider) as engine:
        observed_case_ids: set[str] = set()
        for case in search_cases:
            assert isinstance(case, dict)
            case_id = str(case["id"])
            query = str(case["query"])
            scope = str(case["scope"])
            mode = str(case["mode"])
            expected_resource_kind = str(case["expected_resource_kind"])
            result = asyncio.run(
                engine.search_unified(
                    query,
                    scope=scope,  # type: ignore[arg-type]
                    mode=mode,  # type: ignore[arg-type]
                    limit=20,
                )
            )
            result_items = _result_items(result.to_dict())
            assert result_items, case_id
            assert all(item["resource_kind"] == expected_resource_kind for item in result_items), case_id
            observed_case_ids.add(case_id)
        assert observed_case_ids == {"document-text", "image-semantic", "audio-hybrid", "video-frame-text"}

        for cell in matrix_cells:
            source_resource_id = str(cell["source_resource_id"])
            target_resource_id = str(cell["target_resource_id"])
            target_kind = str(cell["target_kind"])
            result = engine.find_similar_resource(
                source_resource_id,
                scope=scope_by_target_kind[target_kind],
                limit=20,
            )
            assert result.degraded is False
            assert result.to_dict() == engine.find_similar_resource(
                source_resource_id,
                scope=scope_by_target_kind[target_kind],
                limit=20,
            ).to_dict()
            result_items = _result_items(result.to_dict())
            assert result_items
            first = result_items[0]
            assert first["resource_id"] == target_resource_id
            assert first["resource_kind"] == resource_kind_by_target_kind[target_kind]
            assert first["rank"] == expected_target_rank
            observed_items: list[tuple[str, str, int]] = []
            for item in result_items:
                result_resource_id = item.get("resource_id")
                resource_kind = item.get("resource_kind")
                rank = item.get("rank")
                assert isinstance(result_resource_id, str)
                assert isinstance(resource_kind, str)
                assert isinstance(rank, int)
                observed_items.append((result_resource_id, resource_kind, rank))
            observed_orders[(source_resource_id, target_resource_id)] = tuple(observed_items)

    reopened_provider = FixtureFeatureEmbeddingProvider(dimensions=config.embedding.dimensions)
    with MDRackEngine(root=root, config=config, embedding_provider=reopened_provider) as reopened_engine:
        for cell in matrix_cells:
            source_resource_id = str(cell["source_resource_id"])
            target_resource_id = str(cell["target_resource_id"])
            target_kind = str(cell["target_kind"])
            result = reopened_engine.find_similar_resource(
                source_resource_id,
                scope=scope_by_target_kind[target_kind],
                limit=20,
            )
            reopened_items: list[tuple[str, str, int]] = []
            for item in _result_items(result.to_dict()):
                result_resource_id = item.get("resource_id")
                resource_kind = item.get("resource_kind")
                rank = item.get("rank")
                if isinstance(result_resource_id, str) and isinstance(resource_kind, str) and isinstance(rank, int):
                    reopened_items.append((result_resource_id, resource_kind, rank))
            reopened_order = tuple(reopened_items)
            assert result.degraded is False
            assert reopened_order == observed_orders[(source_resource_id, target_resource_id)]
            assert reopened_order[0] == (
                target_resource_id,
                resource_kind_by_target_kind[target_kind],
                expected_target_rank,
            )

    representation_by_kind = {
        "document": "whole_resource_text",
        "image": "image_text_aggregate",
        "audio": "transcript_text",
        "video_with_frame_text": "transcript_text",
    }
    expected_representations = {
        str(cell["source_resource_id"]): representation_by_kind[str(cell["source_kind"])]
        for cell in matrix_cells
    }
    video_manifest = _load_json(root / "video" / "video-resource.json")
    video_resource_id = str(video_manifest["resource"]["resource_id"])
    catalog = SQLiteCatalog.open(root / ".mdrack" / "catalog.sqlite3")
    try:
        placeholders = ", ".join("?" for _ in expected_representations)
        rows = catalog.connection.execute(
            """
            SELECT units.resource_id, representations.representation_kind, spaces.space_id,
                   spaces.fingerprint, units.text_content
            FROM core_search_units AS units
            JOIN core_representations AS representations
              ON representations.representation_id = units.representation_id
            JOIN core_unit_embeddings AS embeddings ON embeddings.unit_id = units.unit_id
            JOIN core_embedding_spaces AS spaces ON spaces.space_id = embeddings.space_id
            WHERE units.resource_id IN ({placeholders})
              AND units.unit_kind = 'whole_resource'
            """.format(placeholders=placeholders),
            tuple(sorted(expected_representations)),
        ).fetchall()
        frame_texts = [
            str(row[0])
            for row in catalog.connection.execute(
                """
                SELECT text_content
                FROM core_search_units
                WHERE resource_id = ? AND unit_kind = 'frame'
                ORDER BY ordinal
                """,
                (video_resource_id,),
            ).fetchall()
        ]
    finally:
        catalog.close()
    canonical_rows = [
        row
        for row in rows
        if expected_representations.get(str(row[0])) == str(row[1])
    ]
    assert len(canonical_rows) == 4
    assert {row[2] for row in canonical_rows} == {
        embedding_space_id(profile.name, profile.fingerprint, profile.vector_value_policy)
    }
    assert {row[3] for row in canonical_rows} == {profile.fingerprint}

    captions = video_manifest["frame_captions"]["observations"]
    assert isinstance(captions, list)
    assert all(
        isinstance(item, dict)
        and isinstance(item.get("caption"), str)
        and item["caption"] in frame_texts
        for item in captions
    )
    _assert_fixture_hashes(root, source_hashes)


@pytest.mark.e2e
def test_source_bytes_remain_unchanged_through_prepared_media_ingests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, source_hashes = _copy_fixture(tmp_path)
    runner = CliRunner()
    _invoke(runner, root, "init")

    def provider_must_not_be_created(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("prepared media ingestion must not create an embedding provider")

    monkeypatch.setattr(
        "mdrack.cli.commands.transcript.create_embedding_provider",
        provider_must_not_be_created,
    )
    monkeypatch.setattr(
        "mdrack.cli.commands.video.create_embedding_provider",
        provider_must_not_be_created,
    )

    prepared_image = _load_json(root / "prepared" / "image-caption-ocr.json")
    storage = create_application_storage(root, MDRackConfig(), create=False)
    try:
        image_service = ImageIngestionService(
            storage.resource_store,
            extractor=StaticImageExtractor(
                (
                    ExtractedImageText("caption_text", str(prepared_image["caption"]), "prepared-v1"),
                    ExtractedImageText("ocr_text", str(prepared_image["ocr"]), "prepared-v1"),
                )
            ),
        )
        asyncio.run(
            image_service.ingest(
                root / "images" / "image.png",
                resource_id=str(prepared_image["resource_id"]),
                source_namespace=str(prepared_image["source_namespace"]),
                source_ref=str(prepared_image["source_ref"]),
            )
        )
    finally:
        storage.close()
    _assert_fixture_hashes(root, source_hashes)

    for resource_id_value, source_ref, transcript_name in (
        (resource_id("one-store-fixture", "audio-001"), "audio-001", "audio.whisper.json"),
        (resource_id("one-store-fixture", "audio-002"), "audio-002", "audio-near.whisper.json"),
    ):
        _invoke(
            runner,
            root,
            "ingest",
            "transcript",
            str(root / "transcripts" / transcript_name),
            "--resource-id",
            resource_id_value,
            "--kind",
            "audio",
            "--media-type",
            "audio/wav",
            "--namespace",
            "one-store-fixture",
            "--source-ref",
            source_ref,
            "--no-embeddings",
        )
        _assert_fixture_hashes(root, source_hashes)

    for video_name in ("video-resource.json", "video-resource-near.json"):
        _invoke(
            runner,
            root,
            "ingest",
            "video",
            str(root / "video" / video_name),
            "--no-embeddings",
        )
        _assert_fixture_hashes(root, source_hashes)



@pytest.mark.e2e
@pytest.mark.privacy
def test_json_outputs_logs_diagnostics_and_evidence_hide_privacy_sentinels(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    manifest = _fixture_manifest()
    privacy = _load_json(FIXTURE_ROOT / str(manifest["privacy_policy"]["sentinel_file"]))
    sentinels = privacy["sentinels"]
    assert isinstance(sentinels, list) and all(isinstance(value, str) for value in sentinels)

    root = tmp_path / "PRIVATE_ONE_STORE_ROOT_SENTINEL"
    root.mkdir()
    (root / "PRIVATE_ONE_STORE_SOURCE_PATH_SENTINEL.md").write_text(
        "PRIVATE_ONE_STORE_CONTENT_SENTINEL",
        encoding="utf-8",
    )
    runner = CliRunner()
    caplog.set_level(logging.DEBUG)
    init = runner.invoke(main, ["--root", str(root), "init"])
    scan = runner.invoke(main, ["--root", str(root), "scan", "--provider", "fake"])
    assert init.exit_code == scan.exit_code == 0

    class ProviderFailure(FakeEmbeddingProvider):
        async def embed_query(self, text: str, profile: str = "default") -> list[float]:
            del text, profile
            raise RuntimeError("PRIVATE_ONE_STORE_PROVIDER_BODY_SENTINEL")

        async def close(self) -> None:
            raise RuntimeError("PRIVATE_ONE_STORE_PROVIDER_BODY_SENTINEL")

    class VectorFailure(FakeEmbeddingProvider):
        async def embed_query(self, text: str, profile: str = "default") -> list[float]:
            del text, profile
            return ["PRIVATE_ONE_STORE_VECTOR_SENTINEL"] * self.dimensions  # type: ignore[list-item]

    monkeypatch.setattr(
        "mdrack.cli.commands.search.create_embedding_provider",
        lambda _name, config: ProviderFailure(dimensions=config.embedding.dimensions),
    )
    provider_failure = runner.invoke(
        main,
        ["--root", str(root), "search", "private query", "--mode", "semantic", "--provider", "fake"],
    )
    monkeypatch.setattr(
        "mdrack.cli.commands.search.create_embedding_provider",
        lambda _name, config: VectorFailure(dimensions=config.embedding.dimensions),
    )
    vector_failure = runner.invoke(
        main,
        ["--root", str(root), "search", "private query", "--mode", "semantic", "--provider", "fake"],
    )
    monkeypatch.setattr(
        "mdrack.cli.commands.metadata.MetadataCatalogService.inspect",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("PRIVATE_ONE_STORE_METADATA_SENTINEL")
        ),
    )
    metadata_failure = runner.invoke(
        main,
        ["--root", str(root), "metadata", "show", "document-with-private-metadata"],
    )

    payloads = [
        json.loads(result.output)
        for result in (init, scan, provider_failure, vector_failure, metadata_failure)
    ]
    assert payloads[0]["ok"] is payloads[1]["ok"] is True
    assert payloads[2]["ok"] is payloads[3]["ok"] is payloads[4]["ok"] is False
    diagnostic = build_safe_diagnostic_record(
        generated_for="release",
        status="degraded",
        checks=[{"code": "privacy", "status": "degraded", "reason_code": "provider_error"}],
    )
    evidence = {"diagnostic": diagnostic, "outputs": payloads}
    assert serialize_safe_json(evidence, forbidden_values=sentinels)
    assert scan_privacy(caplog.text, forbidden_values=sentinels).safe
    for result in (init, scan, provider_failure, vector_failure, metadata_failure):
        assert scan_privacy(result.stdout, forbidden_values=sentinels).safe
        assert scan_privacy(result.stderr, forbidden_values=sentinels).safe
    unsafe_diagnostic = build_safe_diagnostic_record(
        generated_for="release",
        status="failed",
        checks=[
            {
                "code": "privacy",
                "status": "failed",
                "reason_code": "PRIVATE_ONE_STORE_METADATA_SENTINEL",
            }
        ],
    )
    assert scan_privacy(unsafe_diagnostic, forbidden_values=sentinels).safe is False
    with pytest.raises(ValueError, match="evidence contains private data"):
        serialize_safe_json(unsafe_diagnostic, forbidden_values=sentinels)
