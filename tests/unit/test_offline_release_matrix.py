"""Static contracts for the offline release matrix."""

from __future__ import annotations

import io
import json
import runpy
import tarfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_offline_matrix_covers_four_distributions_and_both_artifact_kinds() -> None:
    module = runpy.run_path(str(REPO_ROOT / "scripts" / "offline_release_matrix.py"))
    specs = module["PACKAGE_SPECS"]
    assert {name for name, _ in specs} == {"mdrack", "mdrack-core", "mdrack-media", "mdrack-sqlite"}
    assert callable(module["_metadata"])


def test_workflow_is_offline_and_covers_linux_windows_python_matrix() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "offline-release-matrix.yml").read_text(encoding="utf-8")
    assert 'echo "UV_OFFLINE=1" >> "$GITHUB_ENV"' in workflow
    assert "version: '0.11.15'" in workflow
    assert "os: [ubuntu-latest, windows-latest]" in workflow
    assert "python-version: ['3.11', '3.12']" in workflow
    assert "GIT_CONFIG_KEY_0: core.autocrlf" in workflow
    assert "GIT_CONFIG_VALUE_0: 'false'" in workflow
    assert "fetch-depth: 0" in workflow
    assert "uv lock --check" in workflow
    assert "uv sync --all-extras --frozen" in workflow
    assert "uv sync --all-extras --frozen --offline" not in workflow
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
    assert "dependency provisioning" in runbook
    assert "evidence upload are remote infrastructure" in runbook
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
    assert '"installed_runtime"' in source
    assert "re.sub(r'[-_.]+', '-', dist.metadata['Name']).lower()" in source
    assert 'cell_expected_runtime = expected_runtime if package == "mdrack" else {}' in source
    assert "_validate_installed_runtime(cell_expected_runtime," in source
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
    runtime, _ = module["_locked_runtime"]()
    assert runtime["click"] == "8.4.2"


def test_locked_runtime_is_exact_non_dev_closure_and_preserves_colorama_marker() -> None:
    module = runpy.run_path(str(REPO_ROOT / "scripts" / "offline_release_matrix.py"))
    runtime, markers = module["_locked_runtime"]()
    assert len(runtime) == 19
    assert {
        "annotated-types": "0.7.0",
        "certifi": "2026.6.17",
        "idna": "3.18",
        "pygments": "2.20.0",
        "typing-inspection": "0.4.2",
    }.items() <= runtime.items()
    assert not {"pytest", "mypy", "ruff", "pytest-asyncio"}.intersection(runtime)
    assert runtime["colorama"] == "0.4.6"
    assert markers["colorama"] == "sys_platform == 'win32'"


def test_runtime_ledger_mismatch_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    module = runpy.run_path(str(REPO_ROOT / "scripts" / "offline_release_matrix.py"))
    monkeypatch.setattr(module["Path"], "read_text", lambda self, **_: "")
    with pytest.raises(RuntimeError, match="runtime ledger mismatch"):
        module["_validate_runtime_ledger"]({"idna": "3.18"})


def test_installed_runtime_version_drift_fails_closed() -> None:
    module = runpy.run_path(str(REPO_ROOT / "scripts" / "offline_release_matrix.py"))
    with pytest.raises(RuntimeError, match="installed runtime closure mismatch"):
        module["_validate_installed_runtime"]({"idna": "3.18"}, {"idna": "3.19"})


def test_install_graph_separates_production_and_dev_extra_edges() -> None:
    module = runpy.run_path(str(REPO_ROOT / "scripts" / "offline_release_matrix.py"))
    graph = module["_install_graph"](
        [{
            "distribution": "mdrack",
            "dependencies": ["click>=8.1", "pytest>=8.0; extra == 'dev'"],
        }]
    )
    assert graph["production_edges"] == ["mdrack -> click"]
    assert graph["dev_extra_edges"] == ["mdrack -> pytest"]


def test_matrix_rejects_artifacts_inside_source_checkout() -> None:
    module = runpy.run_path(str(REPO_ROOT / "scripts" / "offline_release_matrix.py"))
    validate_output_dir = module["_validate_output_dir"]
    try:
        validate_output_dir(REPO_ROOT / ".release-artifacts")
    except ValueError as error:
        assert "outside the repository" in str(error)
    else:
        raise AssertionError("source-tree artifact output must be rejected")


def test_four_projects_declare_mit_and_ship_identical_license_sources() -> None:
    expected = (REPO_ROOT / "LICENSE").read_bytes()
    for path in (
        REPO_ROOT / "pyproject.toml",
        REPO_ROOT / "packages" / "mdrack-core" / "pyproject.toml",
        REPO_ROOT / "packages" / "mdrack-media" / "pyproject.toml",
        REPO_ROOT / "packages" / "mdrack-sqlite" / "pyproject.toml",
    ):
        assert 'license = "MIT"' in path.read_text(encoding="utf-8")
    for path in (
        REPO_ROOT / "LICENSE",
        REPO_ROOT / "packages" / "mdrack-core" / "LICENSE",
        REPO_ROOT / "packages" / "mdrack-media" / "LICENSE",
        REPO_ROOT / "packages" / "mdrack-sqlite" / "LICENSE",
    ):
        assert path.read_bytes() == expected


def _write_fake_artifacts(output_dir: Path, *, license_bytes: bytes, expression: str = "MIT") -> None:
    metadata = f"Metadata-Version: 2.4\nName: mdrack\nVersion: 1.3.0\nLicense-Expression: {expression}\n"
    with ZipFile(output_dir / "mdrack-1.3.0-py3-none-any.whl", "w", ZIP_DEFLATED) as archive:
        archive.writestr("mdrack/LICENSE", license_bytes)
        archive.writestr("mdrack-1.3.0.dist-info/METADATA", metadata)
    with tarfile.open(output_dir / "mdrack-1.3.0.tar.gz", "w:gz") as archive:
        for name, payload in (
            ("mdrack-1.3.0/LICENSE", license_bytes),
            ("mdrack-1.3.0/PKG-INFO", metadata.encode()),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))


@pytest.mark.parametrize("failure", ["license", "metadata"])
def test_artifact_audit_rejects_wrong_license_contract(tmp_path: Path, failure: str) -> None:
    module = runpy.run_path(str(REPO_ROOT / "scripts" / "offline_release_matrix.py"))
    license_bytes = module["CANONICAL_LICENSE"] if failure == "metadata" else b"wrong\n"
    expression = "MIT" if failure == "license" else "Apache-2.0"
    _write_fake_artifacts(tmp_path, license_bytes=license_bytes, expression=expression)
    module["PACKAGE_SPECS"] = (("mdrack", REPO_ROOT),)
    with pytest.raises(RuntimeError):
        module["_audit_artifacts"](tmp_path)


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
