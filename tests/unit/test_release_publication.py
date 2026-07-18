"""S11 publication, public-export, documentation, and release-evidence gates."""

from __future__ import annotations

import json
import re
import runpy
from pathlib import Path

from mdrack.eval.privacy import scan_privacy

REPO_ROOT = Path(__file__).resolve().parents[2]
CURRENT_DOCS = REPO_ROOT / "docs" / "current-architecture"
EXPECTED_PUBLIC_API_EXPORTS = (
    "EmbeddingCapabilities",
    "EmbeddingProfile",
    "DuplicateResourceItem",
    "DuplicateResourceResult",
    "FacetFilter",
    "HybridRetrievalService",
    "ExtractedImageText",
    "ImageEmbeddingSpace",
    "ImageExtractor",
    "ImageIngestionResult",
    "ImageSearchItem",
    "ImageSearchResult",
    "IndexingResult",
    "MDRackEngine",
    "RetrievalCandidate",
    "RetrievalItem",
    "RetrievalResult",
    "ResourceQueryScope",
    "SimilarResourceItem",
    "SimilarResourceResult",
    "SourceLocator",
    "StaticImageExtractor",
    "VisualEmbeddingProvider",
)
FORBIDDEN_RELEASE_KEYS = {
    "id",
    "query",
    "content",
    "path",
    "root",
    "endpoint",
    "url",
    "host",
    "port",
    "vector",
    "metadata",
    "facet",
    "body",
    "exception",
    "sqlite_id",
}


def _slug(heading: str) -> str:
    value = heading.strip().lower()
    value = re.sub(r"[^\w\- ]", "", value, flags=re.UNICODE)
    return re.sub(r"\s+", "-", value)


def test_public_api_exports_v03_image_and_resource_contracts() -> None:
    import mdrack.public_api as public_api

    assert tuple(public_api.__all__) == EXPECTED_PUBLIC_API_EXPORTS
    assert all(hasattr(public_api, name) for name in EXPECTED_PUBLIC_API_EXPORTS)


def test_installed_oracle_matches_documented_compatibility_inventory() -> None:
    registry = (REPO_ROOT / "docs" / "compatibility" / "v0.3-compatibility-registry.md").read_text(
        encoding="utf-8"
    )
    match = re.search(r"```json compatibility-oracle\n(.*?)\n```", registry, flags=re.DOTALL)
    assert match is not None
    documented = json.loads(match.group(1))
    script = runpy.run_path(str(REPO_ROOT / "scripts" / "check_installed_package.py"), run_name="oracle")
    frozen_modules = {name: list(symbols) for name, symbols in script["REGISTRY_IMPORTS"].items()}
    assert documented["modules"] == frozen_modules
    assert documented["public_api_all"] == list(script["EXPECTED_PUBLIC_API_EXPORTS"])
    assert documented["public_api_models_all"] == list(script["EXPECTED_PUBLIC_API_MODEL_EXPORTS"])


def test_current_documentation_has_no_stale_v02_or_asset_pipeline_claims() -> None:
    rendered = "\n".join(path.read_text(encoding="utf-8") for path in sorted(CURRENT_DOCS.glob("*.md")))
    system_overview = (CURRENT_DOCS / "system-overview.md").read_text(encoding="utf-8")
    asset_contract = (CURRENT_DOCS / "assets.md").read_text(encoding="utf-8")
    sqlite_contract = (CURRENT_DOCS / "sqlite-persistence.md").read_text(encoding="utf-8")
    data_instruction = (REPO_ROOT / "instructions" / "DATA.sqlite.instructions.md").read_text(
        encoding="utf-8"
    )
    architecture_instruction = (REPO_ROOT / "instructions" / "ARCH.system.instructions.md").read_text(
        encoding="utf-8"
    )
    quality_instruction = (REPO_ROOT / "instructions" / "TEST.quality-gates.instructions.md").read_text(
        encoding="utf-8"
    )
    current_instructions = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((REPO_ROOT / "instructions").glob("*.instructions.md"))
    )
    cli_contract = (REPO_ROOT / "docs" / "cli-contracts.md").read_text(encoding="utf-8")
    assert "checked-out MDRack v0.2" not in rendered
    assert "build_asset_graph" not in rendered
    assert "creates exactly one bounded\n`image_reference`" not in rendered
    assert "no asset CLI" not in rendered
    assert "migrations `0000`–`0006`, current" not in rendered
    assert "asset graph construction" not in system_overview
    assert "Asset discovery is local and offline" not in system_overview
    assert "creates no asset graph and never inspects referenced files" in system_overview
    assert "profiles, assets, and retrieval DTOs" not in system_overview
    assert "Production Markdown indexing creates no asset graph" in asset_contract
    assert "Current Markdown indexing does not populate or maintain them" in sqlite_contract
    assert "Markdown replacement neither writes nor\ndeletes" in sqlite_contract
    assert "removes stale asset" not in sqlite_contract
    assert "vectors, assets, and references" not in sqlite_contract
    assert "Markdown indexing has no production owner" in data_instruction
    assert "does not resolve image targets or create, update, or delete rows" in data_instruction
    assert "Asset references reject external" not in data_instruction
    assert "Ambiguous chunk-to-asset mapping" not in data_instruction
    assert "orchestrates indexing, chunking, assets" not in architecture_instruction
    assert "Asset discovery is local/offline" not in architecture_instruction
    assert "neither inspects referenced files nor creates an asset graph" in architecture_instruction
    assert "FTS/vector/profile/asset integrity" not in quality_instruction
    assert "unambiguous asset-to-chunk ownership" not in quality_instruction
    assert "no asset graph/reference or image\n  resource is created" in quality_instruction
    for stale_claim in (
        "orchestrates indexing, chunking, assets",
        "Asset discovery is local/offline",
        "FTS/vector/profile/asset integrity",
        "unambiguous asset-to-chunk ownership",
        "Asset references reject external",
        "Ambiguous chunk-to-asset mapping",
    ):
        assert stale_claim not in current_instructions
    assert "Production v0.2" not in cli_contract
    assert "production v0.2" not in cli_contract
    assert "Current v0.3 preserves the legacy-compatible RRF-only behavior" in cli_contract


def test_compatibility_registry_assigns_markdown_projection_not_asset_ownership() -> None:
    registry = (REPO_ROOT / "docs" / "compatibility" / "v0.3-compatibility-registry.md").read_text(
        encoding="utf-8"
    )
    markdown_row = next(
        line for line in registry.splitlines() if line.startswith("| `mdrack.markdown.parser`,")
    )
    assert "parser/chunker/projection tests" in markdown_row
    assert "Markdown compatibility/projection owner; no asset owner remains" in markdown_row
    assert "explicit direct-image ingestion uses a separate resource path" in markdown_row
    assert "no asset/image-reference behavior retained in production" in markdown_row
    assert "Markdown/asset owner" not in registry
    assert "parser/chunker/asset tests" not in registry


def test_v03_contract_status_and_migration_ledger_match_implemented_checkout() -> None:
    contract_paths = (
        REPO_ROOT / "docs" / "decisions" / "0002-provider-storage-neutral-core.md",
        REPO_ROOT / "docs" / "compatibility" / "v0.3-compatibility-registry.md",
        REPO_ROOT / "instructions" / "ARCH.system.instructions.md",
        REPO_ROOT / "instructions" / "DATA.sqlite.instructions.md",
    )
    rendered = "\n".join(path.read_text(encoding="utf-8") for path in contract_paths)
    for stale_claim in (
        "implementation not yet started",
        "not yet implemented",
        "Until its reviewed implementation lands",
        "No `0007` SQL may be authored",
        "does not claim any compatibility mapper",
    ):
        assert stale_claim not in rendered
    assert "Accepted and implemented for the v0.3 compatibility release" in rendered
    assert "`0007`: provider-neutral resources" in rendered
    migrations = REPO_ROOT / "src" / "mdrack" / "storage" / "sqlite" / "migrations.py"
    assert "0007_resource_core.sql" in migrations.read_text(encoding="utf-8")


def test_published_markdown_links_and_heading_anchors_resolve() -> None:
    documents = [REPO_ROOT / "README.md", REPO_ROOT / "docs" / "recovery.md", *sorted(CURRENT_DOCS.glob("*.md"))]
    link_pattern = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
    for document in documents:
        text = document.read_text(encoding="utf-8")
        for target in link_pattern.findall(text):
            if "://" in target or target.startswith("mailto:"):
                continue
            relative, _, anchor = target.partition("#")
            destination = (document.parent / relative).resolve() if relative else document.resolve()
            assert destination.exists(), f"broken link in {document}: {target}"
            if anchor and destination.suffix == ".md":
                headings = {
                    _slug(line.lstrip("# "))
                    for line in destination.read_text(encoding="utf-8").splitlines()
                    if line.startswith("#")
                }
                assert anchor in headings, f"broken anchor in {document}: {target}"


def test_release_json_uses_closed_safe_diagnostic_schema() -> None:
    path = REPO_ROOT / "docs" / "evidence" / "v0.3-release-gate.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert set(payload) == {"schema_version", "generated_for", "status", "checks"}
    assert payload["schema_version"] == 1
    assert payload["generated_for"] == "release"
    assert payload["status"] == "ok"
    assert payload["checks"]
    for check in payload["checks"]:
        assert set(check) <= {"code", "status", "reason_code", "counts", "dimensions", "fingerprint"}
        assert check["status"] in {"ok", "empty", "degraded", "failed"}
    assert scan_privacy(payload).safe
    keys: set[str] = set()

    def collect(value: object) -> None:
        if isinstance(value, dict):
            keys.update(str(key).lower() for key in value)
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(payload)
    assert keys.isdisjoint(FORBIDDEN_RELEASE_KEYS)
