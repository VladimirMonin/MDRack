"""Stage C release-contract regression tests for MDRack 1.2."""

from __future__ import annotations

import tomllib
from pathlib import Path

import mdrack

REPO_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_VERSION = "1.2.0"


def test_v12_runtime_and_build_metadata_are_synchronized() -> None:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["project"]["version"] == EXPECTED_VERSION
    assert mdrack.__version__ == EXPECTED_VERSION


def test_v12_unified_search_contract_is_current_and_linked() -> None:
    contract_path = REPO_ROOT / "docs" / "contracts" / "v1.2-unified-search.md"
    assert contract_path.is_file()
    contract = contract_path.read_text(encoding="utf-8")
    cli_contract = (REPO_ROOT / "docs" / "cli-contracts.md").read_text(encoding="utf-8")
    interfaces = (REPO_ROOT / "docs" / "current-architecture" / "public-interfaces.md").read_text(
        encoding="utf-8"
    )
    current_index = (REPO_ROOT / "docs" / "current-architecture" / "README.md").read_text(encoding="utf-8")
    limitations = (REPO_ROOT / "docs" / "current-architecture" / "limitations.md").read_text(encoding="utf-8")

    for required in (
        "mdrack search QUERY --scope all|notes|audio|video|frames|images",
        "mdrack find-similar RESOURCE_ID --scope all|notes|audio|video|images",
        "MDRackEngine.search_unified",
        "MDRackEngine.find_similar_resource",
        "Provider-free resource similarity",
        "frames",
        "--mode text|semantic|hybrid",
        "source bytes",
    ):
        assert required in contract

    assert "## 3f. Unified text search" in cli_contract
    assert "## 3g. Unified provider-free resource similarity" in cli_contract
    assert "v1.2-unified-search.md" in interfaces
    assert "v1.2-unified-search.md" in current_index
    assert "raw audio" in limitations
    assert "pixel" in limitations
