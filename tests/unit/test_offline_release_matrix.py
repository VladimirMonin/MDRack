"""Static contracts for the offline release matrix."""

from __future__ import annotations

import json
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
    assert "offline_release_matrix.py --output-dir \"${{ runner.temp }}/mdrack-release-artifacts\"" in workflow
    assert "--candidate-packet docs/evidence/v0.4-release-packet.json" in workflow
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
    assert "_materialize_candidate" in source
    assert "--candidate-packet" in source


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


def test_candidate_materialization_separates_build_inputs_from_publication_outputs(
    tmp_path: Path,
) -> None:
    packet = json.loads(
        (REPO_ROOT / "docs/evidence/v0.4-release-packet.json").read_text(encoding="utf-8")
    )
    matrix = runpy.run_path(str(REPO_ROOT / "scripts" / "offline_release_matrix.py"))
    candidate = tmp_path / "candidate"
    matrix["_materialize_candidate"](
        REPO_ROOT / "docs/evidence/v0.4-release-packet.json",
        candidate,
    )

    snapshot = packet["candidate_snapshot"]
    build_paths = {item["path"] for item in snapshot["build_inputs"]}
    publication_paths = set(snapshot["publication_outputs"])
    assert build_paths.isdisjoint(publication_paths)
    assert all(
        (candidate / path).read_bytes() == (REPO_ROOT / path).read_bytes()
        for path in build_paths
    )
    assert not (candidate / "docs/evidence/v0.4-release-packet.json").exists()
    assert (
        candidate / "docs/evidence/w5-offline-release-matrix.json"
    ).read_bytes() != (
        REPO_ROOT / "docs/evidence/w5-offline-release-matrix.json"
    ).read_bytes()
    assert not (candidate / "docs/plans/2026-07-20-v1.1-implementation-plan.md").exists()
