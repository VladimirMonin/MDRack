from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
CORE_PROJECT = REPO_ROOT / "packages" / "mdrack-core"
CORE_SOURCE = CORE_PROJECT / "src" / "mdrack_core"


def _load_pyproject(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def test_core_distribution_metadata_matches_frozen_contract() -> None:
    core = _load_pyproject(CORE_PROJECT / "pyproject.toml")
    project = core["project"]

    assert project["name"] == "mdrack-core"
    assert project["version"] == "1.0.0rc1"
    assert project["requires-python"] == ">=3.11"
    assert project["dependencies"] == []
    assert core["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == [
        "src/mdrack_core"
    ]
    assert 'CORE_CONTRACT_VERSION = "1.0.0-rc.1"' in (
        CORE_SOURCE / "contract.py"
    ).read_text(encoding="utf-8")


def test_app_depends_on_workspace_core_without_packaging_a_second_copy() -> None:
    root = _load_pyproject(REPO_ROOT / "pyproject.toml")

    assert "mdrack-core==1.0.0rc1" in root["project"]["dependencies"]
    assert root["tool"]["uv"]["sources"]["mdrack-core"] == {"workspace": True}
    assert root["tool"]["uv"]["workspace"]["members"] == ["packages/mdrack-core"]
    assert root["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == [
        "src/mdrack"
    ]
    assert "/packages" in root["tool"]["hatch"]["build"]["targets"]["sdist"][
        "exclude"
    ]
    assert not (REPO_ROOT / "src" / "mdrack_core").exists()
    assert CORE_SOURCE.is_dir()


def test_core_distribution_carries_public_docs_and_typing_marker() -> None:
    for relative_path in (
        "README.md",
        "API.md",
        "CHANGELOG.md",
        "src/mdrack_core/py.typed",
    ):
        assert (CORE_PROJECT / relative_path).exists()

    readme = (CORE_PROJECT / "README.md").read_text(encoding="utf-8")
    api = (CORE_PROJECT / "API.md").read_text(encoding="utf-8")
    changelog = (CORE_PROJECT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "1.0.0rc1" in readme
    assert "1.0.0rc1" in api
    assert "1.0.0rc1" in changelog
    assert "no runtime dependencies" in readme.lower()
