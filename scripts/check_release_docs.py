#!/usr/bin/env python3
"""Validate the privacy-safe, offline release evidence contract."""

from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKDOWN = ROOT / "docs/evidence/w5-offline-release-matrix.md"
MANIFEST = ROOT / "docs/evidence/w5-offline-release-matrix.json"
PACKET_MARKDOWN = ROOT / "docs/evidence/v0.4-release-packet.md"
PACKET = ROOT / "docs/evidence/v0.4-release-packet.json"
LEDGER = ROOT / "docs/compatibility/v0.4-public-surface-ledger.json"
QUERY_MANIFEST = ROOT / "tests/evaluation/queries-v1/queries.json"


def _sha256(relative_path: str) -> str:
    return hashlib.sha256((ROOT / relative_path).read_bytes()).hexdigest()


def _relative_links_exist(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", text):
        if "://" in target or target.startswith(("mailto:", "#")):
            continue
        relative_target = target.split("#", 1)[0]
        if relative_target and not (path.parent / relative_target).resolve().exists():
            return False
    return True


def _markdown_table_sha256(path: Path, label: str) -> str | None:
    pattern = rf"^\|\s*{re.escape(label)}\s*\|\s*`([0-9a-f]{{64}})`\s*\|\s*$"
    matches = re.findall(pattern, path.read_text(encoding="utf-8"), flags=re.MULTILINE)
    return matches[0] if len(matches) == 1 else None


def _markdown_unit_offline_passed_count(path: Path) -> int | None:
    pattern = r"^\|\s*`unit/offline`\s*\|\s*([0-9][0-9,]*) tests passed\b[^|]*\|"
    matches = re.findall(pattern, path.read_text(encoding="utf-8"), flags=re.MULTILINE)
    if len(matches) != 1:
        return None
    rendered = matches[0]
    try:
        count = int(rendered.replace(",", ""))
    except ValueError:
        return None
    return count if rendered == f"{count:,}" else None


def _packet_full_pytest_passed_count(packet: dict[str, object]) -> int | None:
    verification = packet.get("verification")
    if not isinstance(verification, dict):
        return None
    rendered = verification.get("candidate_full_pytest")
    if not isinstance(rendered, str):
        return None
    match = re.fullmatch(r"([0-9]+) passed", rendered)
    return int(match.group(1)) if match else None


def _exports(relative_path: str) -> list[str]:
    tree = ast.parse((ROOT / relative_path).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__all__"
            for target in node.targets
        ):
            return list(ast.literal_eval(node.value))
    raise ValueError("missing public export ledger")


def main() -> int:
    text = MARKDOWN.read_text(encoding="utf-8")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    packet = json.loads(PACKET.read_text(encoding="utf-8"))
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    query_manifest = json.loads(QUERY_MANIFEST.read_text(encoding="utf-8"))
    markdown_passed_count = _markdown_unit_offline_passed_count(PACKET_MARKDOWN)
    packet_passed_count = _packet_full_pytest_passed_count(packet)
    if markdown_passed_count is None or markdown_passed_count != packet_passed_count:
        return 1
    required_phrases = (
        "scripts/offline_release_matrix.py",
        "mdrack-core",
        "mdrack-media",
        "mdrack-sqlite",
        "Windows or Python 3.12 were executed",
    )
    if any(phrase not in text for phrase in required_phrases):
        return 1
    if manifest.get("status") != "ok":
        return 1
    provenance = manifest.get("provenance", {})
    if provenance.get("network_allowed") is not False:
        return 1
    if provenance.get("network_attempts") != 0:
        return 1
    if set(manifest.get("distributions", [])) != {
        "mdrack",
        "mdrack-core",
        "mdrack-media",
        "mdrack-sqlite",
    }:
        return 1
    if set(manifest.get("artifact_kinds", [])) != {"wheel", "sdist"}:
        return 1
    if {item.get("stage") for item in packet.get("stage_coverage", [])} != set(
        range(15)
    ):
        return 1
    if {item.get("row") for item in packet.get("definition_of_done", [])} != set(
        range(1, 23)
    ):
        return 1
    if {item.get("release") for item in packet.get("release_coverage", [])} != {
        "0.3.1",
        "0.4",
        "0.5",
        "0.6",
        "1.0",
    }:
        return 1
    required_artifacts = {
        (distribution, kind)
        for distribution in ("mdrack", "mdrack-core", "mdrack-media", "mdrack-sqlite")
        for kind in ("wheel", "sdist")
    }
    manifest_artifacts = manifest.get("artifacts", [])
    packet_artifacts = packet.get("package_artifacts", [])
    manifest_hashes = {
        (item.get("distribution"), item.get("kind")): str(item.get("sha256", "")).removeprefix("sha256:")
        for item in manifest_artifacts
    }
    packet_hashes = {
        (item.get("distribution"), item.get("kind")): str(item.get("sha256", "")).removeprefix("sha256:")
        for item in packet_artifacts
    }
    if (
        len(manifest_artifacts) != 8
        or len(packet_artifacts) != 8
        or set(manifest_hashes) != required_artifacts
        or set(packet_hashes) != required_artifacts
        or manifest_hashes != packet_hashes
        or any(re.fullmatch(r"[0-9a-f]{64}", digest) is None for digest in manifest_hashes.values())
    ):
        return 1
    snapshot = packet.get("candidate_snapshot", {})
    if snapshot.get("committed_base_revision") != ledger.get("committed_base_revision"):
        return 1
    if (
        snapshot.get("verification_scope")
        != "committed_base_plus_content_addressed_build_inputs"
        or snapshot.get("build_input_status") != "uncommitted"
        or snapshot.get("artifact_identity_contract")
        != "publication_outputs_excluded_from_candidate_bytes"
        or snapshot.get("excluded_paths")
        != ["docs/plans/2026-07-20-v1.1-implementation-plan.md"]
    ):
        return 1
    build_inputs = snapshot.get("build_inputs", [])
    build_paths = [item.get("path") for item in build_inputs]
    publication_outputs = snapshot.get("publication_outputs", [])
    required_publication_outputs = {
        "README.md",
        "docs/current-architecture/README.md",
        "docs/current-architecture/public-interfaces.md",
        "docs/compatibility/v0.4-public-surface-ledger.json",
        "docs/evidence/w5-offline-release-matrix.json",
        "docs/evidence/v0.4-release-packet.md",
        "docs/evidence/v0.4-release-packet.json",
        "docs/offline-release-verification.md",
        "scripts/check_release_docs.py",
        "tests/unit/test_release_publication.py",
        "docs/decisions/0005-timed-text-granularity.md",
        "docs/decisions/0006-temporal-locator-specification.md",
        "docs/decisions/0007-no-overlap-default.md",
        "docs/decisions/0008-whole-resource-text-aggregation.md",
        "docs/decisions/0009-text-only-media-capability.md",
        "docs/decisions/0010-clean-standalone-sqlite-catalog.md",
    }
    if (
        not build_inputs
        or len(build_paths) != len(set(build_paths))
        or len(publication_outputs) != len(set(publication_outputs))
        or set(publication_outputs) != required_publication_outputs
        or set(build_paths) & set(publication_outputs)
        or "scripts/offline_release_matrix.py" not in build_paths
        or "docs/evidence/w5-offline-release-matrix.json" in build_paths
    ):
        return 1
    if any(item.get("sha256") != _sha256(item.get("path", "")) for item in build_inputs):
        return 1
    expected_hashes = {
        "corpus_manifest_file_sha256": "tests/evaluation/corpus-v1/manifest.json",
        "query_set_file_sha256": "tests/evaluation/queries-v1/queries.json",
        "benchmark_config_file_sha256": "tests/evaluation/benchmark-v1/manifest.json",
        "sqlite_report_file_sha256": "docs/evaluation/w5-sqlite-envelope.json",
        "release_matrix_manifest_file_sha256": (
            "docs/evidence/w5-offline-release-matrix.json"
        ),
        "prepared_manifest_schema_file_sha256": (
            "docs/contracts/prepared-resource-manifest-v1.schema.json"
        ),
    }
    fingerprints = packet.get("fingerprints", {})
    release_matrix_path = "docs/evidence/w5-offline-release-matrix.json"
    release_matrix_digest = _sha256(release_matrix_path)
    manifest_references = {
        fingerprints.get("release_matrix_manifest_file_sha256"),
        _markdown_table_sha256(PACKET_MARKDOWN, "Offline artifact manifest bytes"),
    }
    if manifest_references != {release_matrix_digest}:
        return 1
    if any(
        fingerprints.get(key) != _sha256(path)
        for key, path in expected_hashes.items()
    ):
        return 1
    source_plan = packet.get("source_plan", {})
    if source_plan.get("sha256") != _sha256(source_plan.get("path", "")):
        return 1
    if fingerprints.get("query_set_contract_ref") != query_manifest.get("contract_digest"):
        return 1
    stage_by_number = {item.get("stage"): item for item in packet.get("stage_coverage", [])}
    if stage_by_number[9].get("revision") != "4b2b72806a2db818a655360809b969900cc7f1ba":
        return 1
    expected_decisions = {
        "timed_text_granularity": "docs/decisions/0005-timed-text-granularity.md",
        "temporal_locator_specification": "docs/decisions/0006-temporal-locator-specification.md",
        "no_overlap_default": "docs/decisions/0007-no-overlap-default.md",
        "whole_resource_text_aggregation": "docs/decisions/0008-whole-resource-text-aggregation.md",
        "text_only_media_capability": "docs/decisions/0009-text-only-media-capability.md",
        "clean_standalone_sqlite": "docs/decisions/0010-clean-standalone-sqlite-catalog.md",
    }
    decisions = packet.get("decision_records", [])
    if {item.get("topic"): item.get("path") for item in decisions} != expected_decisions:
        return 1
    if any(
        item.get("status") != "accepted"
        or item.get("sha256") != _sha256(item.get("path", ""))
        for item in decisions
    ):
        return 1
    required_modules = {
        "mdrack.public_api",
        "mdrack.application.resource_catalog",
        "mdrack_core",
        "mdrack_media",
        "mdrack_sqlite",
    }
    surfaces = ledger.get("surfaces", [])
    if {surface.get("public_module") for surface in surfaces} != required_modules:
        return 1
    required_surface_fields = {
        "public_module",
        "owner_distribution",
        "owner_package",
        "distribution_version",
        "contract_version",
        "introduced_version",
        "deprecated_version",
        "removal_gate",
        "installed_test",
        "symbols",
    }
    if any(not required_surface_fields.issubset(surface) for surface in surfaces):
        return 1
    if any(
        not surface["symbols"]
        or len(surface["symbols"]) != len(set(surface["symbols"]))
        for surface in surfaces
    ):
        return 1
    surface_by_module = {surface["public_module"]: surface for surface in surfaces}
    catalog_test = (
        "tests/cli/test_cli_resource_manifest.py::"
        "test_installed_wheels_explicit_catalog_manifest_e2e_outside_source_tree"
    )
    if surface_by_module["mdrack.application.resource_catalog"].get("installed_test") != catalog_test:
        return 1
    catalog_test_source = (ROOT / catalog_test.split("::", 1)[0]).read_text(encoding="utf-8")
    if (
        "from mdrack.application.resource_catalog import" not in catalog_test_source
        or "PreparedResourceCatalog.open" not in catalog_test_source
    ):
        return 1
    export_sources = {
        "mdrack.public_api": "src/mdrack/public_api/__init__.py",
        "mdrack_core": "packages/mdrack-core/src/mdrack_core/__init__.py",
        "mdrack_media": "packages/mdrack-media/src/mdrack_media/__init__.py",
        "mdrack_sqlite": "packages/mdrack-sqlite/src/mdrack_sqlite/__init__.py",
    }
    if any(
        set(surface_by_module[module]["symbols"]) != set(_exports(path))
        for module, path in export_sources.items()
    ):
        return 1
    if not all(
        _relative_links_exist(path)
        for path in (
            PACKET_MARKDOWN,
            ROOT / "README.md",
            ROOT / "docs/current-architecture/README.md",
        )
    ):
        return 1
    print("Release evidence documentation contract passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
