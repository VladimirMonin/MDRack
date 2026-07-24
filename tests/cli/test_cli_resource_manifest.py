"""Explicit clean-catalog resource CLI and Python facade contracts."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from mdrack.application.manifest import MAX_MANIFEST_BYTES
from mdrack.application.resource_catalog import PreparedResourceCatalog, ResourceCatalogError
from mdrack.cli import main
from mdrack_core.domain import LexicalBranch, SearchScope
from mdrack_sqlite import SQLiteCatalog


def _manifest() -> dict[str, Any]:
    return {
        "contract": "mdrack.prepared-resource",
        "version": 1,
        "resource": {
            "resource_id": "resource-1",
            "resource_kind": "document",
            "media_type": "text/plain",
            "source_namespace": "PRIVATE_NAMESPACE_SENTINEL",
            "locator": {
                "kind": "opaque",
                "payload": {"path": "/PRIVATE_SOURCE_SENTINEL.bin"},
            },
            "content_hash": "PRIVATE_CONTENT_HASH_SENTINEL",
            "title": "PRIVATE_TITLE_SENTINEL",
            "metadata": {"secret": "PRIVATE_METADATA_SENTINEL"},
        },
        "representations": [
            {
                "representation_id": "representation-1",
                "resource_id": "resource-1",
                "representation_kind": "retrieval_text",
                "modality": "text",
                "text": "needle PRIVATE_TEXT_SENTINEL",
                "producer_fingerprint": "PRIVATE_PRODUCER_SENTINEL",
                "metadata": {},
            }
        ],
        "units": [
            {
                "unit_id": "unit-1",
                "resource_id": "resource-1",
                "representation_id": "representation-1",
                "unit_kind": "text_chunk",
                "modality": "text",
                "text": "needle PRIVATE_TEXT_SENTINEL",
                "evidence_locator": {
                    "kind": "span",
                    "payload": {"start": 7, "end": 13, "secret": "PRIVATE_LOCATOR_SENTINEL"},
                },
                "ordinal": 0,
                "metadata": {},
            }
        ],
        "spaces": [
            {
                "space_id": "space-1",
                "dimensions": 2,
                "metric": "dot",
                "fingerprint": "PRIVATE_SPACE_SENTINEL",
                "metadata": {},
            }
        ],
        "vectors": [{"unit_id": "unit-1", "space_id": "space-1", "vector": [1.0, 0.0]}],
        "facets": [
            {
                "resource_id": "resource-1",
                "facet": {"namespace": "PRIVATE_FACET_NAMESPACE", "value": "PRIVATE_FACET_VALUE"},
                "origin": "user",
                "producer_fingerprint": "PRIVATE_FACET_PRODUCER",
            }
        ],
    }


def _write_manifest(path: Path, value: object | None = None) -> bytes:
    payload = json.dumps(_manifest() if value is None else value, separators=(",", ":")).encode()
    path.write_bytes(payload)
    return payload


def _create_catalog(path: Path) -> None:
    with SQLiteCatalog.create(path):
        pass


def test_standalone_resource_search_canonicalizes_an_explicit_f32_query(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog.sqlite3"
    manifest_path = tmp_path / "manifest.json"
    value = _manifest()
    value["spaces"][0]["metadata"] = {
        "vector_value_policy": "ieee754-f32-canonical-v1",
        "vector_codec": "ieee754-f32-le-v1",
    }
    value["vectors"][0]["vector"] = [1.0 + 2**-30, 0.0]
    _create_catalog(catalog_path)
    _write_manifest(manifest_path, value)

    with PreparedResourceCatalog.open(catalog_path) as catalog:
        catalog.import_file(manifest_path)
        result = catalog.search_vector((1.0 + 2**-30, 0.0), "space-1")

    assert [(item["resource_id"], item["unit_id"]) for item in result.results] == [
        ("resource-1", "unit-1")
    ]
    assert result.degraded is False


def test_benchmark_missing_catalog_returns_fixed_private_error_envelope(tmp_path: Path) -> None:
    catalog_path = tmp_path / "missing-catalog.sqlite3"

    result = CliRunner().invoke(main, ["benchmark", "--catalog", str(catalog_path)])

    assert result.exit_code == 1
    assert json.loads(result.stdout) == {
        "ok": False,
        "error": {
            "message": "Benchmark could not be completed",
            "code": "BENCHMARK_ERROR",
        },
        "meta": {"command": "benchmark"},
    }
    assert str(catalog_path) not in result.output


def test_benchmark_unopenable_catalog_returns_fixed_private_error_envelope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog_path = tmp_path / "private-catalog.sqlite3"
    _create_catalog(catalog_path)

    def deny_open(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise PermissionError("caller-controlled private path")

    monkeypatch.setattr(SQLiteCatalog, "open_readonly", deny_open)
    result = CliRunner().invoke(main, ["benchmark", "--catalog", str(catalog_path)])

    assert result.exit_code == 1
    assert json.loads(result.stdout) == {
        "ok": False,
        "error": {
            "message": "Benchmark could not be completed",
            "code": "BENCHMARK_ERROR",
        },
        "meta": {"command": "benchmark"},
    }
    assert "caller-controlled private path" not in result.output
    assert str(catalog_path) not in result.output


def _cli(*args: str):
    return CliRunner().invoke(main, ["resource", *args])


def test_explicit_catalog_commands_ignore_invalid_default_config(tmp_path: Path) -> None:
    root = tmp_path / "root"
    config_dir = root / ".mdrack"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text(
        'PRIVATE_CONFIG_SENTINEL = "unterminated',
        encoding="utf-8",
    )
    catalog_path = tmp_path / "catalog.sqlite3"
    manifest_path = tmp_path / "manifest.json"
    _create_catalog(catalog_path)
    _write_manifest(manifest_path)
    runner = CliRunner()

    imported = runner.invoke(
        main,
        [
            "--root",
            str(root),
            "resource",
            "import",
            str(manifest_path),
            "--catalog",
            str(catalog_path),
        ],
    )
    inspected = runner.invoke(
        main,
        [
            "--root",
            str(root),
            "resource",
            "inspect",
            "resource-1",
            "--catalog",
            str(catalog_path),
        ],
    )
    deleted = runner.invoke(
        main,
        [
            "--root",
            str(root),
            "resource",
            "delete",
            "resource-1",
            "--catalog",
            str(catalog_path),
        ],
    )

    assert imported.exit_code == inspected.exit_code == deleted.exit_code == 0
    assert json.loads(imported.stdout)["meta"] == {"command": "resource import"}
    assert json.loads(inspected.stdout)["meta"] == {"command": "resource inspect"}
    assert json.loads(deleted.stdout)["data"] == {"resource_id": "resource-1", "deleted": True}
    captured = imported.output + inspected.output + deleted.output
    assert "PRIVATE_" not in captured
    assert str(tmp_path) not in captured


def test_cli_and_python_import_inspect_delete_parity_with_reopen_search_and_no_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli_catalog = tmp_path / "cli.sqlite3"
    api_catalog = tmp_path / "api.sqlite3"
    manifest_path = tmp_path / "manifest.json"
    payload = _write_manifest(manifest_path)
    _create_catalog(cli_catalog)
    _create_catalog(api_catalog)

    network_requests = 0

    def blocked_network(*args: object, **kwargs: object) -> None:
        nonlocal network_requests
        del args, kwargs
        network_requests += 1
        raise AssertionError("network access is forbidden")

    monkeypatch.setattr(socket, "create_connection", blocked_network)

    imported = _cli("import", str(manifest_path), "--catalog", str(cli_catalog))
    assert imported.exit_code == 0
    imported_payload = json.loads(imported.stdout)

    with PreparedResourceCatalog.open(api_catalog) as catalog:
        api_import = catalog.import_bytes(payload).to_dict()
        api_inspection = catalog.inspect("resource-1").to_dict()

    assert imported_payload == {
        "ok": True,
        "data": api_import,
        "meta": {"command": "resource import"},
    }

    inspected = _cli("inspect", "resource-1", "--catalog", str(cli_catalog))
    assert inspected.exit_code == 0
    assert json.loads(inspected.stdout) == {
        "ok": True,
        "data": api_inspection,
        "meta": {"command": "resource inspect"},
    }
    assert api_inspection["counts"] == {
        "representations": 1,
        "units": 1,
        "spaces": 1,
        "vectors": 1,
        "facets": 1,
    }
    assert api_inspection["kinds"] == {
        "representations": ["retrieval_text"],
        "modalities": ["text"],
        "units": ["text_chunk"],
    }
    assert api_inspection["locator"]["kind"] == "opaque"
    assert api_inspection["locator"]["fingerprint"].startswith("sha256:")
    assert all(
        fingerprint.startswith("sha256:")
        for values in api_inspection["fingerprints"].values()
        for fingerprint in ([values] if isinstance(values, str) else values)
    )

    with SQLiteCatalog.open_readonly(cli_catalog) as reopened:
        candidates = reopened.search_lexical(
            LexicalBranch("installed-e2e", "needle", candidate_limit=5),
            scope=SearchScope(),
        )
    assert [(item.resource_id, item.unit_id) for item in candidates] == [("resource-1", "unit-1")]
    assert candidates[0].evidence_locator.kind == "span"
    assert candidates[0].evidence_locator.payload["start"] == 7

    with PreparedResourceCatalog.open(api_catalog) as catalog:
        api_delete = catalog.delete("resource-1").to_dict()
    deleted = _cli("delete", "resource-1", "--catalog", str(cli_catalog))
    assert deleted.exit_code == 0
    assert json.loads(deleted.stdout) == {
        "ok": True,
        "data": api_delete,
        "meta": {"command": "resource delete"},
    }
    assert api_delete == {"resource_id": "resource-1", "deleted": True}

    for path in (cli_catalog, api_catalog):
        with SQLiteCatalog.open_readonly(path) as reopened:
            assert reopened.read_resource("resource-1") is None

    captured = imported.stdout + imported.stderr + inspected.stdout + inspected.stderr + deleted.stdout + deleted.stderr
    assert "PRIVATE_" not in captured
    assert str(tmp_path) not in captured
    assert network_requests == 0


@pytest.mark.parametrize(
    ("case", "expected_code"),
    (
        ("malformed", "RESOURCE_MANIFEST_INVALID_JSON"),
        ("oversize", "RESOURCE_MANIFEST_PAYLOAD_TOO_LARGE"),
        ("missing_manifest", "RESOURCE_MANIFEST_UNAVAILABLE"),
        ("missing_catalog", "RESOURCE_CATALOG_UNAVAILABLE"),
    ),
)
def test_cli_import_error_matrix_is_one_safe_json_object(
    tmp_path: Path,
    case: str,
    expected_code: str,
) -> None:
    catalog_path = tmp_path / "PRIVATE_CATALOG_PATH.sqlite3"
    manifest_path = tmp_path / "PRIVATE_MANIFEST_PATH.json"
    _create_catalog(catalog_path)
    _write_manifest(manifest_path)

    if case == "malformed":
        manifest_path.write_bytes(b'{"PRIVATE_PAYLOAD_SENTINEL":')
    elif case == "oversize":
        manifest_path.write_bytes(b"x" * (MAX_MANIFEST_BYTES + 1))
    elif case == "missing_manifest":
        manifest_path.unlink()
    elif case == "missing_catalog":
        catalog_path.unlink()

    result = _cli("import", str(manifest_path), "--catalog", str(catalog_path))

    assert result.exit_code == 1
    parsed = json.loads(result.stdout)
    assert parsed == {
        "ok": False,
        "error": {
            "message": "Prepared resource import failed",
            "code": expected_code,
        },
        "meta": {"command": "resource import"},
    }
    assert result.stdout.count("\n") == 1
    assert "PRIVATE_" not in result.stdout + result.stderr
    assert str(tmp_path) not in result.stdout + result.stderr


def test_inspect_missing_resource_and_catalog_schema_fail_safely(tmp_path: Path) -> None:
    clean = tmp_path / "PRIVATE_CLEAN_SENTINEL.sqlite3"
    foreign = tmp_path / "PRIVATE_FOREIGN_SENTINEL.sqlite3"
    _create_catalog(clean)
    foreign.write_bytes(b"not sqlite PRIVATE_DATABASE_SENTINEL")

    missing = _cli("inspect", "PRIVATE_RESOURCE_ID_SENTINEL", "--catalog", str(clean))
    invalid = _cli("inspect", "PRIVATE_RESOURCE_ID_SENTINEL", "--catalog", str(foreign))

    assert missing.exit_code == invalid.exit_code == 1
    assert json.loads(missing.stdout)["error"] == {
        "message": "Resource was not found",
        "code": "RESOURCE_NOT_FOUND",
    }
    assert json.loads(invalid.stdout)["error"] == {
        "message": "Resource inspection failed",
        "code": "RESOURCE_CATALOG_UNAVAILABLE",
    }
    assert "PRIVATE_" not in missing.stdout + missing.stderr + invalid.stdout + invalid.stderr

    with PreparedResourceCatalog.open(clean) as catalog:
        with pytest.raises(ResourceCatalogError) as caught:
            catalog.inspect("PRIVATE_RESOURCE_ID_SENTINEL")
    assert str(caught.value) == "resource_not_found"
    assert "PRIVATE_" not in str(caught.value)


def test_explicit_catalog_python_facade_import_is_click_free() -> None:
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json,sys; "
                "import mdrack.application.resource_catalog; "
                "print(json.dumps({'click': 'click' in sys.modules, "
                "'cli': any(name == 'mdrack.cli' or name.startswith('mdrack.cli.') "
                "for name in sys.modules)}))"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(probe.stdout) == {"click": False, "cli": False}


def test_installed_wheels_explicit_catalog_manifest_e2e_outside_source_tree(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[2]
    uv = shutil.which("uv")
    assert uv is not None
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment["UV_OFFLINE"] = "1"

    wheel_dir = tmp_path / "wheels"
    subprocess.run(
        [uv, "build", "--wheel", "--all-packages", "--out-dir", str(wheel_dir)],
        cwd=repository,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    wheels = tuple(sorted(wheel_dir.glob("*.whl")))
    assert len(wheels) == 4

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
        [uv, "pip", "install", "--python", str(python), "--offline", *(str(path) for path in wheels)],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    catalog_path = tmp_path / "catalog.sqlite3"
    manifest_path = tmp_path / "manifest.json"
    _write_manifest(manifest_path)
    create = subprocess.run(
        [
            python,
            "-c",
            (
                "from mdrack_sqlite import SQLiteCatalog; "
                f"catalog=SQLiteCatalog.create({str(catalog_path)!r}); catalog.close()"
            ),
        ],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    executable = virtualenv / "bin" / "mdrack"
    imported = subprocess.run(
        [str(executable), "resource", "import", str(manifest_path), "--catalog", str(catalog_path)],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    inspected = subprocess.run(
        [str(executable), "resource", "inspect", "resource-1", "--catalog", str(catalog_path)],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    probe = subprocess.run(
        [
            python,
            "-c",
            (
                "import json,mdrack; "
                "from mdrack.application.resource_catalog import ("
                "FacetValue,PreparedResourceCatalog,ResourceCatalogError,"
                "ResourceCatalogErrorCode,ResourceDeleteResult,ResourceImportResult,"
                "ResourceInspection,ResourceSearchResult); "
                f"catalog=PreparedResourceCatalog.open({str(catalog_path)!r}); "
                "result=catalog.search_text('needle'); item=result.results[0]; "
                "assert FacetValue('ns','value',1).to_dict()['resource_count']==1; "
                "assert ResourceDeleteResult('r',False).to_dict()['deleted'] is False; "
                "assert ResourceImportResult('r','document',{}).to_dict()['resource_kind']=='document'; "
                "assert ResourceInspection('r','document','text/plain',{}, {}, {}, {}).to_dict()['resource_id']=='r'; "
                "assert ResourceSearchResult(None,'unit',()).to_dict()['total_count']==0; "
                "assert ResourceCatalogError("
                "ResourceCatalogErrorCode.RESOURCE_NOT_FOUND).code.value=='resource_not_found'; "
                "print(json.dumps({'origin':mdrack.__file__,'resource_id':item['resource_id'],"
                "'unit_id':item['unit_id'],'target':result.target})); catalog.close()"
            ),
        ],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    deleted = subprocess.run(
        [str(executable), "resource", "delete", "resource-1", "--catalog", str(catalog_path)],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    verify_deleted = subprocess.run(
        [
            python,
            "-c",
            (
                "from mdrack_sqlite import SQLiteCatalog; "
                f"catalog=SQLiteCatalog.open_readonly({str(catalog_path)!r}); "
                "assert catalog.read_resource('resource-1') is None; catalog.close()"
            ),
        ],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(imported.stdout)["data"]["resource_id"] == "resource-1"
    assert json.loads(inspected.stdout)["data"]["counts"]["units"] == 1
    assert json.loads(deleted.stdout)["data"] == {"resource_id": "resource-1", "deleted": True}
    probe_data = json.loads(probe.stdout)
    assert probe_data["resource_id"] == "resource-1"
    assert probe_data["unit_id"] == "unit-1"
    assert probe_data["target"] == "unit"
    assert "site-packages" in probe_data["origin"]
    assert str(repository) not in probe_data["origin"]
    captured = "".join(
        result.stdout + result.stderr
        for result in (create, imported, inspected, probe, deleted, verify_deleted)
    )
    assert "PRIVATE_" not in captured
