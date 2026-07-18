"""Package metadata, exports, and dependency direction for ``mdrack-sqlite``."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import mdrack_sqlite
from mdrack.storage.sqlite.fts import plain_query_fallback as legacy_plain_query_fallback
from mdrack_sqlite.fts import plain_query_fallback
from scripts.check_sqlite_boundaries import PACKAGE_ROOT, violations

REPO_ROOT = Path(__file__).resolve().parents[2]
SQLITE_PROJECT = REPO_ROOT / "packages" / "mdrack-sqlite"


def _load_pyproject(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def test_sqlite_distribution_metadata_and_app_workspace_dependency() -> None:
    package = _load_pyproject(SQLITE_PROJECT / "pyproject.toml")
    root = _load_pyproject(REPO_ROOT / "pyproject.toml")

    assert package["project"]["name"] == "mdrack-sqlite"
    assert package["project"]["version"] == "1.0.0rc1"
    assert package["project"]["requires-python"] == ">=3.11"
    assert package["project"]["dependencies"] == ["mdrack-core==1.0.0rc1"]
    assert package["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == [
        "src/mdrack_sqlite"
    ]
    assert "mdrack-sqlite==1.0.0rc1" in root["project"]["dependencies"]
    assert root["tool"]["uv"]["sources"]["mdrack-sqlite"] == {"workspace": True}
    assert "packages/mdrack-sqlite" in root["tool"]["uv"]["workspace"]["members"]


def test_sqlite_root_exports_and_legacy_fts_helper_are_frozen() -> None:
    assert mdrack_sqlite.__all__ == [
        "SQLITE_BRIDGE_SCHEMA_ID",
        "SQLITE_CATALOG_API_VERSION",
        "SQLITE_CATALOG_SCHEMA_ID",
        "SQLITE_CATALOG_SCHEMA_VERSION",
        "SQLITE_MIGRATION_MANIFEST",
        "SQLITE_MIGRATION_MANIFEST_DIGEST",
        "SQLiteCatalog",
        "SQLiteCatalogError",
        "SQLiteErrorCode",
        "SQLiteMigrationError",
        "SQLiteResourceStore",
        "SQLiteVerification",
    ]
    assert mdrack_sqlite.SQLITE_CATALOG_API_VERSION == "1.0.0rc1"
    assert legacy_plain_query_fallback is plain_query_fallback
    assert plain_query_fallback("plain words") == '"plain words"'
    assert plain_query_fallback("title:value") is None


def test_sqlite_distribution_carries_public_docs_and_typing_marker() -> None:
    for relative_path in (
        "README.md",
        "API.md",
        "CHANGELOG.md",
        "src/mdrack_sqlite/py.typed",
        "src/mdrack_sqlite/migrations/0000_identity.sql",
        "src/mdrack_sqlite/migrations/0001_catalog.sql",
        "src/mdrack_sqlite/migrations/0002_vectors_facets.sql",
        "src/mdrack_sqlite/migrations/0003_search.sql",
    ):
        assert (SQLITE_PROJECT / relative_path).exists()


def test_repository_sqlite_import_boundary_passes() -> None:
    assert violations(REPO_ROOT) == []


def test_sqlite_import_boundary_rejects_mdrack_and_third_party(tmp_path: Path) -> None:
    source = tmp_path / PACKAGE_ROOT / "leak.py"
    source.parent.mkdir(parents=True)
    source.write_text("import mdrack\nimport click\n", encoding="utf-8")

    findings = violations(tmp_path)

    assert any("reverse import mdrack" in finding for finding in findings)
    assert any("third-party import click" in finding for finding in findings)
    assert all(str(tmp_path) not in finding for finding in findings)


def test_verify_scripts_include_sqlite_boundary_and_type_gates_once() -> None:
    for verify_script in ("verify.sh", "verify.ps1"):
        content = (REPO_ROOT / "scripts" / verify_script).read_text(encoding="utf-8")
        assert content.count("uv run python scripts/check_sqlite_boundaries.py") == 1
        assert content.count("uv run mypy packages/mdrack-sqlite/src/mdrack_sqlite") == 1
        assert content.count("uv run ruff check packages/mdrack-sqlite/src/") == 1
