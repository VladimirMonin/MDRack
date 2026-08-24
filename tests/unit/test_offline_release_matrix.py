"""Static contracts for the offline release matrix."""

from __future__ import annotations

import json
import runpy
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_offline_matrix_covers_four_distributions_and_both_artifact_kinds() -> None:
    module = runpy.run_path(str(REPO_ROOT / "scripts" / "offline_release_matrix.py"))
    specs = module["PACKAGE_SPECS"]
    assert {name for name, _ in specs} == {"mdrack", "mdrack-core", "mdrack-media", "mdrack-sqlite"}
    assert callable(module["_metadata"])


def test_workflow_is_offline_and_covers_linux_windows_python_matrix() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "offline-release-matrix.yml").read_text(encoding="utf-8")
    assert "UV_OFFLINE: '1'" in workflow
    assert "version: '0.11.15'" in workflow
    assert "os: [ubuntu-latest, windows-latest]" in workflow
    assert "python-version: ['3.11', '3.12']" in workflow
    assert "uv sync --all-extras --frozen --offline" in workflow
    assert "offline_release_matrix.py --output-dir \"${{ runner.temp }}/mdrack-release-artifacts\"" in workflow
    assert "check_v13_release_packet.py --artifacts-dir" in workflow
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
        "git diff --check",
    ):
        assert gate in workflow
    assert "v0.4-release-packet" not in workflow
    runbook = (REPO_ROOT / "docs" / "offline-release-verification.md").read_text(encoding="utf-8")
    assert "hosted CI" in runbook
    assert "evidence upload are remote CI infrastructure" in runbook
    assert "workflow_dispatch" in runbook and "pull_request" in runbook
    assert "not provide process-wide network-attempt telemetry" in runbook


def test_release_uses_a_committable_uv_lockfile() -> None:
    assert (REPO_ROOT / "uv.lock").is_file()
    ignored_lines = {
        line.strip()
        for line in (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert "uv.lock" not in ignored_lines


def test_current_verification_wrappers_use_the_v13_packet_gate() -> None:
    for path in (REPO_ROOT / "scripts" / "verify.sh", REPO_ROOT / "scripts" / "verify.ps1"):
        source = path.read_text(encoding="utf-8")
        assert "check_v13_release_packet.py" in source
        assert "v0.4-release-packet" not in source
        assert "w5-offline-release-matrix" not in source
        assert "check_release_docs.py" not in source


def test_matrix_script_has_no_network_enabled_default() -> None:
    source = (REPO_ROOT / "scripts" / "offline_release_matrix.py").read_text(encoding="utf-8")
    assert '"telemetry": "not_measured"' in source
    assert "installed-smoke-socket-block" in source
    assert "--offline" in source
    assert '"SOURCE_DATE_EPOCH"' in source
    assert '"PYTHONPATH": ""' in source
    assert '"cell_count": len(cells)' in source
    assert '"install_graph"' in source
    assert "_check_expected_hashes" in source
    assert "_materialize_candidate" in source
    assert "--candidate-packet" in source


def test_matrix_install_graph_accepts_the_root_sqlite_rc2_edge() -> None:
    module = runpy.run_path(str(REPO_ROOT / "scripts" / "offline_release_matrix.py"))
    assert module["EXPECTED_LOCAL_DEPENDENCIES"]["mdrack-sqlite"] == (
        "mdrack-core==1.0.0rc1",
    )
    assert "mdrack-sqlite==1.0.0rc2" in (
        REPO_ROOT / "pyproject.toml"
    ).read_text(encoding="utf-8")
    source = (REPO_ROOT / "scripts" / "offline_release_matrix.py").read_text(encoding="utf-8")
    assert "mdrack-sqlite==1.0.0rc2" in source


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


@pytest.mark.skip(
    reason=(
        "historical v0.4 candidate references retired pre-one-store build inputs "
        "and is not a current release artifact"
    )
)
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
