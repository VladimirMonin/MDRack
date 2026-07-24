"""Stage 16A release-contract regressions for the compact builtin 1.3.0 base."""

from __future__ import annotations

import hashlib
import json
import tomllib
from pathlib import Path

import mdrack
from mdrack_sqlite.contract_v2 import (
    SQLITE_CATALOG_V2_SCHEMA_ID,
    SQLITE_CATALOG_V2_SCHEMA_VERSION,
    SQLITE_V2_MIGRATION_MANIFEST,
    SQLITE_V2_MIGRATION_MANIFEST_DIGEST,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_VERSION = "1.3.0"
SQLITE_VERSION = "1.0.0rc2"
PACKET = REPO_ROOT / "docs" / "evidence" / "v1.3.0-base-release-packet.json"
RELEASE_NOTES = REPO_ROOT / "docs" / "release-1.3.md"


def _artifact_matrix_digest(items: object) -> str:
    encoded = json.dumps(items, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def test_v13_runtime_and_distribution_metadata_are_synchronized() -> None:
    app_project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    sqlite_project = tomllib.loads(
        (REPO_ROOT / "packages" / "mdrack-sqlite" / "pyproject.toml").read_text(encoding="utf-8")
    )
    future_host_project = tomllib.loads(
        (REPO_ROOT / "tests" / "fixtures" / "future_host" / "pyproject.toml").read_text(encoding="utf-8")
    )

    assert app_project["project"]["version"] == APP_VERSION
    assert mdrack.__version__ == APP_VERSION
    assert app_project["project"]["dependencies"].count(f"mdrack-sqlite=={SQLITE_VERSION}") == 1
    assert sqlite_project["project"]["version"] == SQLITE_VERSION
    assert f"mdrack-sqlite=={SQLITE_VERSION}" in future_host_project["project"]["dependencies"]


def test_v13_release_packet_covers_only_the_base_builtin_distribution() -> None:
    packet = json.loads(PACKET.read_text(encoding="utf-8"))

    assert set(packet) == {
        "schema_version",
        "packet_kind",
        "classification",
        "release",
        "source_plan",
        "package_artifacts",
        "artifact_matrix_sha256",
        "clean_v2_schema",
        "evidence",
        "non_claims",
    }
    assert packet["schema_version"] == 1
    assert packet["packet_kind"] == "mdrack-1.3.0-base-release-candidate"
    assert packet["classification"] == {
        "status": "ready_for_independent_review",
        "published": False,
        "sqlite_vec": "excluded_experimental_non_dependency",
    }
    assert packet["release"] == {
        "mdrack": APP_VERSION,
        "mdrack-core": "1.0.0rc1",
        "mdrack-media": "1.0.0rc1",
        "mdrack-sqlite": SQLITE_VERSION,
    }
    assert packet["source_plan"] == {
        "path": "docs/plans/2026-07-24-v1.3-compact-storage-sqlite-vec.md",
        "sha256": hashlib.sha256(
            (REPO_ROOT / "docs" / "plans" / "2026-07-24-v1.3-compact-storage-sqlite-vec.md").read_bytes()
        ).hexdigest(),
    }

    artifacts = packet["package_artifacts"]
    assert isinstance(artifacts, list)
    expected_artifacts = {
        (distribution, kind)
        for distribution in ("mdrack", "mdrack-core", "mdrack-media", "mdrack-sqlite")
        for kind in ("wheel", "sdist")
    }
    assert {(item["distribution"], item["kind"]) for item in artifacts} == expected_artifacts
    assert all(set(item) == {"distribution", "version", "kind", "filename", "bytes", "sha256"} for item in artifacts)
    assert all(item["version"] == packet["release"][item["distribution"]] for item in artifacts)
    assert all(len(item["sha256"]) == 64 and item["bytes"] > 0 for item in artifacts)
    assert packet["artifact_matrix_sha256"] == _artifact_matrix_digest(artifacts)

    assert packet["clean_v2_schema"] == {
        "schema_id": SQLITE_CATALOG_V2_SCHEMA_ID,
        "schema_version": SQLITE_CATALOG_V2_SCHEMA_VERSION,
        "migration_manifest": [
            {"name": name, "sha256": digest} for name, digest in SQLITE_V2_MIGRATION_MANIFEST
        ],
        "manifest_digest": SQLITE_V2_MIGRATION_MANIFEST_DIGEST,
    }

    evidence = packet["evidence"]
    assert set(evidence) == {
        "storage_analyzer",
        "benchmark",
        "quality",
        "privacy",
        "source_hash",
        "installed_package",
    }
    for report_name in ("storage_analyzer", "benchmark"):
        report = evidence[report_name]
        assert (REPO_ROOT / report["path"]).is_file()
        assert report["sha256"] == hashlib.sha256((REPO_ROOT / report["path"]).read_bytes()).hexdigest()
    assert evidence["source_hash"] == {
        "scope": "synthetic_multimodal_fixture",
        "unchanged": True,
        "report": "benchmark.parity_oracle",
    }
    assert evidence["installed_package"]["status"] == "passed"
    assert evidence["privacy"]["status"] == "passed"

    rendered = json.dumps(packet, sort_keys=True)
    for forbidden in ("PRIVATE_", "/home/", "localhost", "http://", "https://", "sqlite-vec=="):
        assert forbidden not in rendered
    assert any("No package was published" in item for item in packet["non_claims"])
    assert any("No existing v1 or 0007 database" in item for item in packet["non_claims"])


def test_v13_current_docs_describe_compact_builtin_cutover_without_false_rollback() -> None:
    release_notes = RELEASE_NOTES.read_text(encoding="utf-8")
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    architecture_index = (REPO_ROOT / "docs" / "current-architecture" / "README.md").read_text(encoding="utf-8")
    limitations = (REPO_ROOT / "docs" / "current-architecture" / "limitations.md").read_text(encoding="utf-8")
    recovery = (REPO_ROOT / "docs" / "recovery.md").read_text(encoding="utf-8")
    data_instruction = (REPO_ROOT / "instructions" / "DATA.sqlite.instructions.md").read_text(encoding="utf-8")

    for text in (release_notes, readme, architecture_index):
        assert "MDRack 1.3.0" in text
    assert "builtin exact" in release_notes
    assert "sqlite-vec" in release_notes
    assert "one-way" in release_notes
    assert "ieee754-f32-le-v1" in recovery
    assert "one-way" in recovery
    assert "runtime rollback" in recovery
    assert "Retained legacy generations are not automatically deleted" in limitations
    assert "Semantic retrieval linearly scans canonical binary vectors" in limitations
    assert "mdrack_sqlite_catalog_v2" in data_instruction
    assert "mdrack-sqlite-vec" not in (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
