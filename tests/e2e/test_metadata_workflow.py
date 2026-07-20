"""M3 Markdown-to-SQLite metadata workflow."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from mdrack.application.compatibility import create_application_storage
from mdrack.application.metadata_filters import MetadataFilter, MetadataFilters
from mdrack.application.store_generations import (
    ActiveGenerationPointer,
    GenerationContractKind,
    GenerationState,
    StoreGeneration,
)
from mdrack.cli import main
from mdrack.config.models import (
    MDRackConfig,
    MetadataConfig,
    MetadataProjectionConfig,
    PathsConfig,
)
from mdrack.public_api import MDRackEngine
from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.migrations import (
    EXPECTED_MIGRATION_MANIFEST_DIGEST,
    EXPECTED_MIGRATION_VERSION,
    apply_candidate_migrations,
    get_migrations_dir,
)


def _ready_generation(store_dir: Path) -> None:
    generation_id = "g-m3-e2e"
    generations = store_dir / "generations"
    generations.mkdir(parents=True)
    database_path = generations / f"generation-{generation_id}.sqlite3"
    connection = get_connection(database_path)
    apply_candidate_migrations(connection, get_migrations_dir())
    connection.close()
    generation = StoreGeneration(
        generation_id=generation_id,
        contract_kind=GenerationContractKind.RESOURCE_CORE_V1,
        migration_manifest_digest=EXPECTED_MIGRATION_MANIFEST_DIGEST,
        schema_version=EXPECTED_MIGRATION_VERSION,
        state=GenerationState.READY,
        created_at="2026-07-20T00:00:00+00:00",
        verified_at="2026-07-20T00:00:01+00:00",
    )
    (generations / f"generation-{generation_id}.json").write_bytes(generation.to_bytes())
    (store_dir / "active-generation.json").write_bytes(
        ActiveGenerationPointer(generation_id, GenerationContractKind.RESOURCE_CORE_V1).to_bytes()
    )


def test_markdown_alias_and_exact_filter_work_end_to_end_without_metadata_embedding(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    store = tmp_path / "store"
    _ready_generation(store)
    (root / "note.md").write_text(
        "---\naliases: [rare-alias]\nstatus: ready\nsecret: PRIVATE_STORE_ONLY_SENTINEL\n---\n"
        "# Public body\n\nOrdinary searchable body.\n",
        encoding="utf-8",
    )
    metadata = MetadataConfig(
        projections=[
            MetadataProjectionConfig(path="/aliases", mode="lexical_text"),
            MetadataProjectionConfig(path="/status", mode="facet", namespace="status"),
            MetadataProjectionConfig(path="/secret", mode="store_only"),
        ]
    )
    config = MDRackConfig(paths=PathsConfig(root=".", store=str(store)), metadata=metadata)
    config_path = root / "mdrack.toml"
    config_path.write_text(
        f"""[paths]
store = "{store}"

[[metadata.projections]]
path = "/aliases"
mode = "lexical_text"

[[metadata.projections]]
path = "/status"
mode = "facet"
namespace = "status"

[[metadata.projections]]
path = "/secret"
mode = "store_only"
""",
        encoding="utf-8",
    )

    engine = MDRackEngine(root=root, config=config)
    try:
        indexed = engine.scan(force_reindex=True)
        result = engine.search_resources_text(
            "rare-alias",
            metadata_filters=MetadataFilters(
                all=(MetadataFilter("status", "ready"),),
            ),
        ).to_dict()
    finally:
        engine.close()

    cli = CliRunner().invoke(
        main,
        [
            "--root",
            str(root),
            "--config-file",
            str(config_path),
            "search",
            "rare-alias",
            "--mode",
            "text",
            "--target",
            "resource",
            "--meta",
            '/status="ready"',
        ],
    )
    assert cli.exit_code == 0, cli.output
    assert indexed.status == "success"
    assert json.loads(cli.output)["data"] == result
    assert result["total_count"] == 1
    assert "PRIVATE_STORE_ONLY_SENTINEL" not in cli.output + cli.stderr

    storage = create_application_storage(root, config)
    try:
        connection = storage.resource_store.connection  # type: ignore[attr-defined]
        rows = connection.execute(
            "SELECT p.representation_kind,u.unit_kind,u.text_content,e.unit_id AS vector_unit "
            "FROM core_representations p JOIN core_search_units u USING(representation_id) "
            "LEFT JOIN core_unit_embeddings e USING(unit_id) ORDER BY p.representation_kind"
        ).fetchall()
    finally:
        storage.close()

    metadata_rows = [row for row in rows if row["representation_kind"] == "metadata_text"]
    body_rows = [row for row in rows if row["representation_kind"] == "retrieval_text"]
    assert len(metadata_rows) == 1
    assert metadata_rows[0]["unit_kind"] == "whole_resource"
    assert metadata_rows[0]["vector_unit"] is None
    assert metadata_rows[0]["text_content"] == "rare-alias"
    assert all("rare-alias" not in (row["text_content"] or "") for row in body_rows)
    assert all("PRIVATE_STORE_ONLY_SENTINEL" not in (row["text_content"] or "") for row in rows)
