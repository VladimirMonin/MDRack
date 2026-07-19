from __future__ import annotations

import importlib.util
import tomllib
from pathlib import Path
from typing import Any

import mdrack_media

REPO_ROOT = Path(__file__).resolve().parents[2]
MEDIA_PROJECT = REPO_ROOT / "packages" / "mdrack-media"
_SPEC = importlib.util.spec_from_file_location(
    "check_media_boundaries",
    REPO_ROOT / "scripts" / "check_media_boundaries.py",
)
assert _SPEC is not None and _SPEC.loader is not None
_BOUNDARY = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_BOUNDARY)
PACKAGE_ROOT = _BOUNDARY.PACKAGE_ROOT
violations = _BOUNDARY.violations


def _load_pyproject(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def test_media_distribution_metadata_and_app_workspace_dependency() -> None:
    package = _load_pyproject(MEDIA_PROJECT / "pyproject.toml")
    root = _load_pyproject(REPO_ROOT / "pyproject.toml")

    assert package["project"]["name"] == "mdrack-media"
    assert package["project"]["version"] == "1.0.0rc1"
    assert package["project"]["requires-python"] == ">=3.11"
    assert package["project"]["dependencies"] == ["mdrack-core==1.0.0rc1"]
    assert package["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == [
        "src/mdrack_media"
    ]
    assert "mdrack-media==1.0.0rc1" in root["project"]["dependencies"]
    assert root["tool"]["uv"]["sources"]["mdrack-media"] == {"workspace": True}
    assert "packages/mdrack-media" in root["tool"]["uv"]["workspace"]["members"]


def test_media_distribution_carries_docs_typing_and_frozen_exports() -> None:
    for relative_path in (
        "README.md",
        "API.md",
        "CHANGELOG.md",
        "examples/frame_builder_serialization.py",
        "examples/transcript_serialization.py",
        "src/mdrack_media/py.typed",
    ):
        assert (MEDIA_PROJECT / relative_path).exists()
    assert mdrack_media.__all__ == sorted(mdrack_media.__all__)
    assert all(hasattr(mdrack_media, name) for name in mdrack_media.__all__)
    assert mdrack_media.MEDIA_CONTRACT_VERSION == "1.0.0-rc.1"
    readme = (MEDIA_PROJECT / "README.md").read_text(encoding="utf-8")
    api = (MEDIA_PROJECT / "API.md").read_text(encoding="utf-8")
    for example in ("frame_builder_serialization.py", "transcript_serialization.py"):
        assert f"examples/{example}" in readme
        assert f"examples/{example}" in api


def test_repository_media_import_boundary_passes() -> None:
    assert violations(REPO_ROOT) == []


def test_media_boundary_rejects_app_third_party_and_infrastructure(tmp_path: Path) -> None:
    source = tmp_path / PACKAGE_ROOT / "leak.py"
    source.parent.mkdir(parents=True)
    source.write_text("import mdrack\nimport click\nimport os\nopen('value')\n", encoding="utf-8")

    findings = violations(tmp_path)

    assert any("reverse import mdrack" in finding for finding in findings)
    assert any("third-party import click" in finding for finding in findings)
    assert any("infrastructure import os" in finding for finding in findings)
    assert any("infrastructure call open" in finding for finding in findings)
    assert all(str(tmp_path) not in finding for finding in findings)


def test_verify_scripts_include_media_boundary_type_and_lint_gates_once() -> None:
    for verify_script in ("verify.sh", "verify.ps1"):
        content = (REPO_ROOT / "scripts" / verify_script).read_text(encoding="utf-8")
        assert content.count("uv run python scripts/check_media_boundaries.py") == 1
        assert content.count("uv run mypy packages/mdrack-media/src/mdrack_media") == 1
        assert content.count("uv run ruff check packages/mdrack-media/src/") == 1
