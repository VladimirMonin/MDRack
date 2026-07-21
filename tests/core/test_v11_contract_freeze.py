from __future__ import annotations

import importlib.util
import tomllib
from pathlib import Path

import mdrack_core
import mdrack_media
import mdrack_sqlite

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE = "989aaaeaf029d7bdd2d9704ae578c9a19efe0f94"
PLAN_SHA256 = "f540960507b92a7d790aacae91b363f7884740978d792c6b3071f8b5a9821491"
B0_DOCS = (
    REPO_ROOT / "docs" / "decisions" / "0011-v1.1-application-contracts.md",
    REPO_ROOT / "docs" / "contracts" / "v1.1-application-contract.md",
    REPO_ROOT / "docs" / "compatibility" / "v1.1-compatibility-ledger.md",
    REPO_ROOT / "docs" / "evidence" / "v1.1-entry-gate.md",
)


def _project(path: Path) -> dict[str, object]:
    return tomllib.loads(path.read_text(encoding="utf-8"))["project"]


def test_b0_freezes_exact_baseline_without_claiming_final_v10() -> None:
    rendered = "\n".join(path.read_text(encoding="utf-8") for path in B0_DOCS)

    assert all(path.is_file() for path in B0_DOCS)
    assert BASELINE in rendered
    assert PLAN_SHA256 in rendered
    assert "final 1.0" in rendered
    assert "not ready" in rendered
    assert "app/src/mdrack" in rendered
    assert "not a valid implementation path" in rendered


def test_existing_package_contracts_remain_release_candidate_foundations() -> None:
    app = _project(REPO_ROOT / "pyproject.toml")
    core = _project(REPO_ROOT / "packages" / "mdrack-core" / "pyproject.toml")
    media = _project(REPO_ROOT / "packages" / "mdrack-media" / "pyproject.toml")
    sqlite = _project(REPO_ROOT / "packages" / "mdrack-sqlite" / "pyproject.toml")

    assert app["version"] == "1.2.0"
    assert core["version"] == media["version"] == sqlite["version"] == "1.0.0rc1"
    assert core["dependencies"] == []
    assert media["dependencies"] == ["mdrack-core==1.0.0rc1"]
    assert sqlite["dependencies"] == ["mdrack-core==1.0.0rc1"]
    assert mdrack_core.CORE_CONTRACT_VERSION == "1.0.0-rc.1"
    assert mdrack_media.MEDIA_CONTRACT_VERSION == "1.0.0-rc.1"
    assert mdrack_sqlite.SQLITE_CATALOG_API_VERSION == "1.0.0rc1"
    assert mdrack_sqlite.SQLITE_CATALOG_SCHEMA_ID == "mdrack_sqlite_catalog_v1"
    assert mdrack_sqlite.SQLITE_CATALOG_SCHEMA_VERSION == "0003"


def test_b0_contract_preserves_current_import_roots_and_public_records() -> None:
    assert importlib.util.find_spec("mdrack") is not None
    assert importlib.util.find_spec("mdrack_core") is not None
    assert importlib.util.find_spec("mdrack_media") is not None
    assert importlib.util.find_spec("mdrack_sqlite") is not None

    for name in (
        "ResourceRecord",
        "RepresentationRecord",
        "SearchUnitRecord",
        "EmbeddingSpaceRecord",
        "VectorRecord",
        "ResourceFacet",
        "Locator",
        "PreparedResourceBatch",
        "SearchRequest",
        "SearchScope",
        "LexicalBranch",
        "VectorBranch",
        "SearchResult",
        "SimilarityResult",
    ):
        assert hasattr(mdrack_core, name)

    for name in (
        "TimedTextAtom",
        "TimedPassage",
        "TranscriptArtifact",
        "FrameCaptionObservation",
        "TimeSegmentLocator",
        "VideoFrameLocator",
    ):
        assert hasattr(mdrack_media, name)


def test_owned_b0_paths_do_not_use_stale_app_source_root() -> None:
    paths = (*B0_DOCS, REPO_ROOT / "tests" / "unit" / "test_v11_application_contracts.py")
    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert "app/src/mdrack/" not in text, f"stale application target in {path}"
