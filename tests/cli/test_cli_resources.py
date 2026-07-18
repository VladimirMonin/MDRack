"""CLI and embedded API contracts for resource facets, duplicates, and similarity."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from mdrack.adapters.sqlite.resource_store import SQLiteResourceStore
from mdrack.application.compatibility import create_application_storage
from mdrack.application.resources import FacetFilter, ResourceQueryScope, ResourceQueryService
from mdrack.application.store_generations import (
    ActiveGenerationPointer,
    GenerationContractKind,
    GenerationState,
    StoreGeneration,
)
from mdrack.cli import main
from mdrack.config.models import MDRackConfig, PathsConfig
from mdrack.public_api import MDRackEngine
from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.migrations import (
    EXPECTED_MIGRATION_MANIFEST_DIGEST,
    EXPECTED_MIGRATION_VERSION,
    apply_candidate_migrations,
    get_migrations_dir,
)
from mdrack_core.domain import (
    CatalogExecutionError,
    EmbeddingSpaceRecord,
    ErrorCategory,
    Facet,
    Locator,
    PreparedResourceBatch,
    RepresentationRecord,
    ResourceFacet,
    ResourceRecord,
    SearchUnitRecord,
    VectorRecord,
)


def _ready_generation(store_dir: Path) -> None:
    generation_id = "g-s10-test"
    generations = store_dir / "generations"
    generations.mkdir(parents=True)
    database_path = generations / f"generation-{generation_id}.sqlite3"
    connection = get_connection(database_path)
    apply_candidate_migrations(connection, get_migrations_dir())
    connection.close()
    generation = StoreGeneration(
        generation_id=generation_id,
        contract_kind=GenerationContractKind.RESOURCE_CORE_V1,
        migration_manifest_digest=EXPECTED_MIGRATION_MANIFEST_DIGEST,
        schema_version=EXPECTED_MIGRATION_VERSION,
        state=GenerationState.READY,
        created_at="2026-07-18T00:00:00+00:00",
        verified_at="2026-07-18T00:00:01+00:00",
    )
    (generations / f"generation-{generation_id}.json").write_bytes(generation.to_bytes())
    (store_dir / "active-generation.json").write_bytes(
        ActiveGenerationPointer(generation_id, GenerationContractKind.RESOURCE_CORE_V1).to_bytes()
    )


def _batch(
    resource_id: str,
    *,
    kind: str = "document",
    content_hash: str = "sha256:shared",
    vector: tuple[float, ...] = (1.0, 0.0),
    private: bool = False,
) -> PreparedResourceBatch:
    representation_id = f"representation-{resource_id}"
    unit_id = f"unit-{resource_id}"
    modality = "image" if kind == "image" else "text"
    facets = [Facet("topic", "python"), Facet("status", "reviewed")]
    if private:
        facets.append(Facet("visibility", "private"))
    return PreparedResourceBatch(
        ResourceRecord(
            resource_id,
            kind,
            "image/png" if kind == "image" else "text/markdown",
            "fixture",
            Locator(
                "PRIVATE_LOCATOR_SENTINEL",
                {"PRIVATE_PATH_KEY": "PRIVATE_PATH_SENTINEL", "id": resource_id},
            ),
            content_hash,
            metadata={"PRIVATE_METADATA_KEY": "PRIVATE_METADATA_SENTINEL"},
        ),
        (
            RepresentationRecord(
                representation_id,
                resource_id,
                "visual" if kind == "image" else "retrieval_text",
                modality,
                None if kind == "image" else "PRIVATE_CONTENT_SENTINEL",
                producer_fingerprint="space-fingerprint",
            ),
        ),
        (
            SearchUnitRecord(
                unit_id,
                resource_id,
                representation_id,
                "whole_resource",
                modality,
                None if kind == "image" else "PRIVATE_CONTENT_SENTINEL",
                Locator("whole", {}),
                0,
            ),
        ),
        (EmbeddingSpaceRecord("shared-space", 2, "cosine", "space-fingerprint"),),
        (VectorRecord(unit_id, "shared-space", vector),),
        tuple(ResourceFacet(resource_id, facet, "user") for facet in facets),
    )


def test_cli_and_engine_resource_operations_have_exact_parity_and_zero_network(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "PRIVATE_ROOT_SENTINEL"
    root.mkdir()
    store_dir = tmp_path / "store"
    _ready_generation(store_dir)
    config_path = root / "mdrack.toml"
    config_path.write_text(f'[paths]\nstore = "{store_dir}"\n', encoding="utf-8")
    config = MDRackConfig(paths=PathsConfig(root=".", store=str(store_dir)))
    storage = create_application_storage(root, config)
    try:
        for batch in (
            _batch("query"),
            _batch("duplicate", vector=(0.9, 0.1)),
            _batch("similar-image", kind="image", content_hash="sha256:image", vector=(0.8, 0.2)),
            _batch("excluded-private", private=True),
        ):
            storage.resource_store.replace_resource(batch)  # type: ignore[attr-defined]
    finally:
        storage.close()

    network_requests = 0

    def blocked_network(*args, **kwargs):
        nonlocal network_requests
        del args, kwargs
        network_requests += 1
        raise AssertionError("network is blocked for provider-free resource operations")

    monkeypatch.setattr("socket.create_connection", blocked_network)
    scope = ResourceQueryScope(
        facets_any=(FacetFilter("status", "reviewed"),),
        facets_all=(FacetFilter("topic", "python"),),
        facets_none=(FacetFilter("visibility", "private"),),
    )
    engine = MDRackEngine(root=root, config=config)
    try:
        api_duplicates = engine.find_resource_duplicates("query", scope=scope, limit=5).to_dict()
        api_similarity = engine.find_similar_resources(
            "unit-query", "shared-space", scope=scope, limit=5
        ).to_dict()
    finally:
        engine.close()

    runner = CliRunner()
    common = ["--root", str(root), "--config-file", str(config_path)]
    filters = [
        "--facet-any", "status=reviewed",
        "--facet-all", "topic=python",
        "--facet-none", "visibility=private",
        "--limit", "5",
    ]
    duplicates = runner.invoke(main, [*common, "resources", "duplicates", "query", *filters])
    similarity = runner.invoke(
        main,
        [*common, "resources", "similar", "unit-query", "--space-id", "shared-space", *filters],
    )
    assert duplicates.exit_code == similarity.exit_code == 0
    assert json.loads(duplicates.output) == {
        "ok": True,
        "data": api_duplicates,
        "meta": {"command": "resources duplicates"},
    }
    assert json.loads(similarity.output) == {
        "ok": True,
        "data": api_similarity,
        "meta": {"command": "resources similar"},
    }
    assert api_duplicates["results"] == [{"resource_id": "duplicate"}]
    similarity_results = api_similarity["results"]
    assert isinstance(similarity_results, list)
    assert [item["resource_id"] for item in similarity_results] == [
        "duplicate",
        "similar-image",
    ]
    assert network_requests == 0
    captured = duplicates.output + similarity.output
    for sentinel in (
        "PRIVATE_ROOT_SENTINEL",
        "PRIVATE_PATH_SENTINEL",
        "PRIVATE_METADATA_SENTINEL",
        "PRIVATE_CONTENT_SENTINEL",
        "excluded-private",
        "sqlite",
    ):
        assert sentinel not in captured


@pytest.mark.parametrize(
    ("method_name", "operation"),
    (
        ("read_resource", "duplicates"),
        ("find_by_content_hash", "duplicates"),
        ("read_unit", "similar"),
        ("read_vector", "similar"),
    ),
)
@pytest.mark.parametrize(
    ("failure_kind", "expected_reason"),
    (
        ("catalog_error", "adapter_error"),
        ("catalog_timeout", "adapter_timeout"),
        ("raw_error", "adapter_error"),
        ("raw_timeout", "adapter_timeout"),
    ),
)
def test_catalog_failures_are_degraded_with_exact_cli_engine_parity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    method_name: str,
    operation: str,
    failure_kind: str,
    expected_reason: str,
) -> None:
    root = tmp_path / "PRIVATE_CATALOG_ROOT_SENTINEL"
    root.mkdir()
    store_dir = tmp_path / "store"
    _ready_generation(store_dir)
    config_path = root / "mdrack.toml"
    config_path.write_text(f'[paths]\nstore = "{store_dir}"\n', encoding="utf-8")
    config = MDRackConfig(paths=PathsConfig(root=".", store=str(store_dir)))
    storage = create_application_storage(root, config)
    try:
        storage.resource_store.replace_resource(_batch("query"))  # type: ignore[attr-defined]
    finally:
        storage.close()
    engine = MDRackEngine(root=root, config=config)
    cli_storage = create_application_storage(root, config)

    network_requests = 0

    def blocked_network(*args, **kwargs):
        nonlocal network_requests
        del args, kwargs
        network_requests += 1
        raise AssertionError("network is blocked for provider-free resource operations")

    def fail_catalog_call(*args, **kwargs):
        del args, kwargs
        if failure_kind == "catalog_error":
            raise CatalogExecutionError(ErrorCategory.CATALOG_ERROR)
        if failure_kind == "catalog_timeout":
            raise CatalogExecutionError(ErrorCategory.ADAPTER_TIMEOUT)
        if failure_kind == "raw_timeout":
            raise TimeoutError("PRIVATE_EXCEPTION_SENTINEL")
        raise RuntimeError("PRIVATE_EXCEPTION_SENTINEL")

    monkeypatch.setattr("socket.create_connection", blocked_network)
    monkeypatch.setattr(SQLiteResourceStore, method_name, fail_catalog_call)
    resources_module = sys.modules["mdrack.cli.commands.resources"]
    monkeypatch.setattr(
        resources_module,
        "_open_catalog",
        lambda ctx: (cli_storage, getattr(cli_storage, "resource_store")),
    )
    caplog.set_level(logging.INFO)

    try:
        if operation == "duplicates":
            app_result = ResourceQueryService(
                getattr(cli_storage, "resource_store")
            ).find_duplicates("query").to_dict()
            api_result = engine.find_resource_duplicates("query").to_dict()
        else:
            app_result = ResourceQueryService(
                getattr(cli_storage, "resource_store")
            ).find_similar("unit-query", "shared-space").to_dict()
            api_result = engine.find_similar_resources(
                "unit-query", "shared-space"
            ).to_dict()
    finally:
        engine.close()

    common = ["--root", str(root), "--config-file", str(config_path), "resources"]
    command = (
        [*common, "duplicates", "query"]
        if operation == "duplicates"
        else [*common, "similar", "unit-query", "--space-id", "shared-space"]
    )
    cli_result = CliRunner().invoke(main, command)

    assert cli_result.exit_code == 0
    assert app_result == api_result
    assert json.loads(cli_result.output) == {
        "ok": True,
        "data": api_result,
        "meta": {"command": f"resources {operation}"},
    }
    assert api_result["results"] == []
    assert api_result["degraded"] is True
    assert api_result["degraded_reason"] == expected_reason
    assert network_requests == 0
    captured = (
        cli_result.output
        + cli_result.stderr
        + caplog.text
        + json.dumps({"app": app_result, "engine": api_result}, sort_keys=True)
    )
    assert "PRIVATE_CATALOG_ROOT_SENTINEL" not in captured
    assert "PRIVATE_EXCEPTION_SENTINEL" not in captured


def test_cli_resource_failures_are_fixed_and_recursive_privacy_safe(
    tmp_path: Path,
    caplog,
) -> None:
    root = tmp_path / "PRIVATE_FAILURE_ROOT_SENTINEL"
    root.mkdir()
    store_dir = tmp_path / "store"
    _ready_generation(store_dir)
    config_path = root / "mdrack.toml"
    config_path.write_text(f'[paths]\nstore = "{store_dir}"\n', encoding="utf-8")
    caplog.set_level(logging.INFO)
    result = CliRunner().invoke(
        main,
        [
            "--root", str(root), "--config-file", str(config_path),
            "resources", "duplicates", "PRIVATE_RESOURCE_SENTINEL",
            "--facet-all", "PRIVATE_FACET_SENTINEL",
        ],
    )
    assert result.exit_code == 1
    assert json.loads(result.output) == {
        "ok": False,
        "error": {
            "message": "Resource duplicate lookup failed",
            "code": "RESOURCE_DUPLICATE_ERROR",
        },
        "meta": {"command": "resources duplicates"},
    }
    captured = result.output + caplog.text
    for sentinel in (
        "PRIVATE_FAILURE_ROOT_SENTINEL",
        "PRIVATE_RESOURCE_SENTINEL",
        "PRIVATE_FACET_SENTINEL",
    ):
        assert sentinel not in captured


def test_offline_installed_wheel_resource_cli_and_engine_behavior(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[2]
    uv = shutil.which("uv")
    assert uv is not None
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment["UV_OFFLINE"] = "1"

    root = tmp_path / "root"
    root.mkdir()
    store_dir = tmp_path / "store"
    _ready_generation(store_dir)
    config_path = root / "mdrack.toml"
    config_path.write_text(f'[paths]\nstore = "{store_dir}"\n', encoding="utf-8")
    config = MDRackConfig(paths=PathsConfig(root=".", store=str(store_dir)))
    storage = create_application_storage(root, config)
    try:
        for batch in (
            _batch("query"),
            _batch("duplicate", vector=(0.9, 0.1)),
            _batch(
                "similar-image",
                kind="image",
                content_hash="sha256:image",
                vector=(0.8, 0.2),
            ),
        ):
            storage.resource_store.replace_resource(batch)  # type: ignore[attr-defined]
    finally:
        storage.close()

    wheel_dir = tmp_path / "wheel"
    subprocess.run(
        [uv, "build", "--wheel", "--out-dir", str(wheel_dir)],
        cwd=repository,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    wheels = tuple(wheel_dir.glob("mdrack-*.whl"))
    assert len(wheels) == 1
    virtualenv = tmp_path / "venv"
    subprocess.run(
        [uv, "venv", "--python", sys.executable, str(virtualenv)],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    python = virtualenv / "bin" / "python"
    subprocess.run(
        [uv, "pip", "install", "--python", str(python), "--offline", str(wheels[0])],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    executable = virtualenv / "bin" / "mdrack"
    common = [str(executable), "--root", str(root), "--config-file", str(config_path)]
    duplicates = subprocess.run(
        [*common, "resources", "duplicates", "query"],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    similar = subprocess.run(
        [*common, "resources", "similar", "unit-query", "--space-id", "shared-space"],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(duplicates.stdout)["data"]["results"] == [{"resource_id": "duplicate"}]
    assert [
        item["resource_id"] for item in json.loads(similar.stdout)["data"]["results"]
    ] == ["duplicate", "similar-image"]

    script = f"""
import json
import importlib
import socket
from pathlib import Path
from click.testing import CliRunner
from mdrack.adapters.sqlite.resource_store import SQLiteResourceStore
from mdrack.application.compatibility import create_application_storage
from mdrack.cli import main
from mdrack.config.models import MDRackConfig, PathsConfig
from mdrack.public_api import MDRackEngine, ResourceQueryScope
from mdrack_core.domain import CatalogExecutionError, ErrorCategory

network_requests = []
def blocked(*args, **kwargs):
    network_requests.append((args, kwargs))
    raise AssertionError('network blocked')
socket.create_connection = blocked
config = MDRackConfig(paths=PathsConfig(root='.', store={str(store_dir)!r}))
engine = MDRackEngine(root=Path({str(root)!r}), config=config)
try:
    duplicate_result = engine.find_resource_duplicates('query', scope=ResourceQueryScope()).to_dict()
    similar_result = engine.find_similar_resources('unit-query', 'shared-space').to_dict()
finally:
    engine.close()

failure_results = {{}}
resources_module = importlib.import_module('mdrack.cli.commands.resources')
for failure_kind, expected_reason in (
    ('catalog_error', 'adapter_error'),
    ('catalog_timeout', 'adapter_timeout'),
    ('raw_error', 'adapter_error'),
    ('raw_timeout', 'adapter_timeout'),
):
    failure_engine = MDRackEngine(root=Path({str(root)!r}), config=config)
    failure_cli_storage = create_application_storage(Path({str(root)!r}), config)
    original_read_resource = SQLiteResourceStore.read_resource
    def fail_read_resource(self, resource_id, *, _failure_kind=failure_kind):
        if _failure_kind == 'catalog_error':
            raise CatalogExecutionError(ErrorCategory.CATALOG_ERROR)
        if _failure_kind == 'catalog_timeout':
            raise CatalogExecutionError(ErrorCategory.ADAPTER_TIMEOUT)
        if _failure_kind == 'raw_timeout':
            raise TimeoutError('PRIVATE_EXCEPTION_SENTINEL')
        raise RuntimeError('PRIVATE_EXCEPTION_SENTINEL')
    SQLiteResourceStore.read_resource = fail_read_resource
    resources_module._open_catalog = lambda ctx: (
        failure_cli_storage,
        getattr(failure_cli_storage, 'resource_store'),
    )
    try:
        failure_api = failure_engine.find_resource_duplicates('query').to_dict()
        failure_cli = CliRunner().invoke(main, [
            '--root', {str(root)!r}, '--config-file', {str(config_path)!r},
            'resources', 'duplicates', 'query',
        ])
    finally:
        failure_engine.close()
        SQLiteResourceStore.read_resource = original_read_resource
    failure_results[failure_kind] = {{
        'api': failure_api,
        'expected_reason': expected_reason,
        'cli_exit_code': failure_cli.exit_code,
        'cli_stdout': failure_cli.stdout,
        'cli_stderr': failure_cli.stderr,
        'cli_exception': None if failure_cli.exception is None else type(failure_cli.exception).__name__,
    }}
print(json.dumps({{
    'duplicates': duplicate_result,
    'similar': similar_result,
    'failures': failure_results,
    'network_requests': len(network_requests),
}}))
"""
    embedded = subprocess.run(
        [python, "-c", script],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "PRIVATE_EXCEPTION_SENTINEL" not in embedded.stdout + embedded.stderr
    payload = json.loads(embedded.stdout)
    assert payload["duplicates"] == json.loads(duplicates.stdout)["data"]
    assert payload["similar"] == json.loads(similar.stdout)["data"]
    for failure_kind, expected_reason in (
        ("catalog_error", "adapter_error"),
        ("catalog_timeout", "adapter_timeout"),
        ("raw_error", "adapter_error"),
        ("raw_timeout", "adapter_timeout"),
    ):
        failure = payload["failures"][failure_kind]
        assert failure["cli_exit_code"] == 0
        assert failure["expected_reason"] == expected_reason
        assert failure["api"]["degraded_reason"] == expected_reason
        assert failure["cli_exception"] is None
        assert json.loads(failure["cli_stdout"]) == {
            "ok": True,
            "data": failure["api"],
            "meta": {"command": "resources duplicates"},
        }
        assert "PRIVATE_" not in failure["cli_stderr"]
    assert payload["network_requests"] == 0
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
