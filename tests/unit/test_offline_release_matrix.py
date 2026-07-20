"""Static contracts for the offline release matrix."""

from __future__ import annotations

import runpy
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_offline_matrix_covers_four_distributions_and_both_artifact_kinds() -> None:
    module = runpy.run_path(str(REPO_ROOT / "scripts" / "offline_release_matrix.py"))
    specs = module["PACKAGE_SPECS"]
    assert {name for name, _ in specs} == {"mdrack", "mdrack-core", "mdrack-media", "mdrack-sqlite"}
    assert callable(module["_metadata"])


def test_workflow_is_offline_and_covers_linux_windows_python_matrix() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "offline-release-matrix.yml").read_text(encoding="utf-8")
    assert "UV_OFFLINE: '1'" in workflow
    assert "os: [ubuntu-latest, windows-latest]" in workflow
    assert "python-version: ['3.11', '3.12']" in workflow
    assert "uv sync --all-extras --frozen --offline" in workflow
    assert "offline_release_matrix.py --output-dir \"${{ runner.temp }}/mdrack-release-artifacts\" --smoke" in workflow
    assert "--expected-manifest docs/evidence/w5-offline-release-matrix.json" in workflow
    for gate in (
        "ruff check",
        "mypy",
        "pytest -m 'not e2e and not privacy'",
        "pytest -m e2e",
        "pytest -m privacy",
        "check_no_forbidden_deps.py",
        "check_core_boundaries.py",
        "check_sqlite_boundaries.py",
        "check_media_boundaries.py",
        "compileall",
        "check_release_docs.py",
        "git diff --check",
    ):
        assert gate in workflow
    assert "test -s docs/evidence/w5-offline-release-matrix.md" in workflow
    assert "test -s docs/evidence/w5-offline-release-matrix.json" in workflow


def test_matrix_script_has_no_network_enabled_default() -> None:
    source = (REPO_ROOT / "scripts" / "offline_release_matrix.py").read_text(encoding="utf-8")
    assert '"network": {"allowed": False, "attempts": 0}' in source
    assert "--offline" in source
    assert '"SOURCE_DATE_EPOCH"' in source
    assert '"PYTHONPATH": ""' in source
    assert '"cell_count": len(cells)' in source
    assert '"install_graph"' in source
    assert "_check_expected_hashes" in source


def test_matrix_rejects_artifacts_inside_source_checkout() -> None:
    module = runpy.run_path(str(REPO_ROOT / "scripts" / "offline_release_matrix.py"))
    validate_output_dir = module["_validate_output_dir"]
    try:
        validate_output_dir(REPO_ROOT / ".release-artifacts")
    except ValueError as error:
        assert "outside the repository" in str(error)
    else:
        raise AssertionError("source-tree artifact output must be rejected")


def test_root_sdist_excludes_agent_and_generated_release_outputs() -> None:
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"/.hermes"' in pyproject
    assert '"/.release-artifacts"' in pyproject
