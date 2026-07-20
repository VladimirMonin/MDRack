from __future__ import annotations

import inspect
from dataclasses import fields
from pathlib import Path

from mdrack.application.manifest import (
    MANIFEST_CONTRACT,
    MANIFEST_VERSION,
    PreparedResourceFacade,
    decode_prepared_resource_manifest,
)
from mdrack.application.resource_catalog import PreparedResourceCatalog
from mdrack.domain.documents import Document
from mdrack.domain.indexing import PreparedFile
from mdrack.public_api import MDRackEngine

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT = REPO_ROOT / "docs" / "contracts" / "v1.1-application-contract.md"
LEDGER = REPO_ROOT / "docs" / "compatibility" / "v1.1-compatibility-ledger.md"


def test_current_application_owners_are_the_frozen_extension_points() -> None:
    assert Document.__module__ == "mdrack.domain.documents"
    assert PreparedFile.__module__ == "mdrack.domain.indexing"
    assert PreparedResourceFacade.__module__ == "mdrack.application.manifest"
    assert PreparedResourceCatalog.__module__ == "mdrack.application.resource_catalog"
    assert MDRackEngine.__module__ == "mdrack.public_api.engine"
    assert "frontmatter" in {field.name for field in fields(Document)}


def test_manifest_v1_import_contract_is_preserved_for_future_u1_round_trip() -> None:
    assert MANIFEST_CONTRACT == "mdrack.prepared-resource"
    assert MANIFEST_VERSION == 1
    assert inspect.isfunction(decode_prepared_resource_manifest)
    assert hasattr(PreparedResourceFacade, "import_manifest")
    assert hasattr(PreparedResourceCatalog, "import_bytes")
    assert hasattr(PreparedResourceCatalog, "search_text")
    assert hasattr(PreparedResourceCatalog, "search_vector")


def test_missing_application_seams_have_one_stage_owner_and_no_package_fork() -> None:
    contract = CONTRACT.read_text(encoding="utf-8")
    ledger = LEDGER.read_text(encoding="utf-8")

    for stage in ("M1", "M2", "M3", "R1", "I1", "V1", "S1", "A1", "U1"):
        assert f"| {stage} |" in contract
    for gap in range(1, 11):
        assert f"G-{gap:02d}" in ledger

    for required in (
        "src/mdrack/application/metadata_normalization.py",
        "src/mdrack/application/metadata_projection.py",
        "src/mdrack/application/metadata_filters.py",
        "src/mdrack/ingestion/transcripts/",
        "src/mdrack/application/transcript_ingestion.py",
        "src/mdrack/application/video_composition.py",
        "src/mdrack/application/artifact_cache.py",
        "textual_content",
        "no incompatible second v1 grammar",
    ):
        assert required in contract

    combined = f"{contract}\n{ledger}".lower()
    for forbidden in (
        "`mdrack_sqlite` does not import `mdrack`",
        "no JSON scan",
        "no image/audio/video embedding capability",
        "PostgreSQL/pgvector",
    ):
        assert forbidden.lower() in combined


def test_b0_contract_freezes_safety_and_compatibility_invariants() -> None:
    rendered = " ".join(CONTRACT.read_text(encoding="utf-8").split())

    for invariant in (
        "SQLite is the only persistent database",
        "compiled to existing core facets before limits",
        "Provider calls finish before catalog mutation",
        "logical IDs and portable locators",
        "Existing CLI envelopes and public imports are preserved",
        "No default network, paid-provider, private-source, or destructive action",
    ):
        assert invariant in rendered
