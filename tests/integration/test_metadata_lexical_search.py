"""M3 local SQLite metadata lexical retrieval contracts."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from mdrack.application.compatibility import prepared_file_to_resource_batch
from mdrack.application.metadata_filters import MetadataFilter, MetadataFilters
from mdrack.application.metadata_projection import MetadataProjection, MetadataProjectionPolicy
from mdrack.application.resource_catalog import ResourceCatalogError
from mdrack.domain.blocks import JSONValue
from mdrack.domain.indexing import PreparedFile, StoredChunk, StoredSection
from mdrack.public_api import MDRackEngine
from mdrack_sqlite import SQLiteCatalog


def _prepared(
    resource_id: str,
    alias: str | None,
    body: str,
    status: str | None,
    *,
    summary: str | None = None,
) -> PreparedFile:
    source_metadata: dict[str, JSONValue] = {
        "secret": "PRIVATE_STORE_ONLY_SENTINEL"
    }
    if alias is not None:
        source_metadata["aliases"] = [alias]
    if status is not None:
        source_metadata["status"] = status
    if summary is not None:
        source_metadata["summary"] = summary
    return PreparedFile(
        record_id=f"row-{resource_id}",
        logical_id=resource_id,
        root_id="vault",
        relative_path=f"{resource_id}.md",
        title=resource_id,
        source_hash=f"hash-{resource_id}",
        indexed_at="2026-07-20T00:00:00+00:00",
        parser_name="markdown_it",
        parser_version="1",
        chunk_strategy_name="structural",
        chunk_strategy_version="1",
        index_run_id="run",
        sections=(
            StoredSection(
                f"section-{resource_id}",
                f"section-logical-{resource_id}",
                resource_id,
                (resource_id,),
                1,
                1,
                2,
                None,
            ),
        ),
        chunks=(
            StoredChunk(
                f"chunk-{resource_id}",
                f"unit-{resource_id}",
                f"section-{resource_id}",
                body,
                "text",
                0,
                (resource_id,),
                None,
                None,
                body,
                f"embedding-hash-{resource_id}",
                2,
                2,
                f"block-{resource_id}",
            ),
        ),
        source_metadata=source_metadata,
    )


class _MetadataStorage:
    def __init__(self, catalog: SQLiteCatalog) -> None:
        self.resource_store = catalog

    def close(self) -> None:
        pass


def _engine(catalog: SQLiteCatalog, tmp_path: Path) -> MDRackEngine:
    config = SimpleNamespace(
        search=SimpleNamespace(rrf_k=60, text_weight=1.0, semantic_weight=1.0)
    )
    return MDRackEngine(
        root=tmp_path,
        config=config,
        storage=_MetadataStorage(catalog),  # type: ignore[arg-type]
    )


def test_metadata_branch_finds_alias_without_store_only_or_body_pollution(tmp_path: Path) -> None:
    policy = MetadataProjectionPolicy(
        (
            MetadataProjection("/aliases", "lexical_text"),
            MetadataProjection("/summary", "lexical_text"),
            MetadataProjection("/status", "facet", "status"),
            MetadataProjection("/secret", "store_only"),
        )
    )
    database = tmp_path / "metadata.sqlite3"
    with SQLiteCatalog.create(database) as catalog:
        catalog.replace_resource(
            prepared_file_to_resource_batch(
                _prepared("alias-hit", "rare-alias", "ordinary body", "ready"),
                metadata_policy=policy,
            )
        )
        catalog.replace_resource(
            prepared_file_to_resource_batch(
                _prepared("body-hit", "other", "rare-alias body", "blocked"),
                metadata_policy=policy,
            )
        )
        engine = _engine(catalog, tmp_path)

        alias_only = engine.search_resources_text(
            "rare-alias", body_weight=0.0, metadata_weight=0.2
        )
        body_only = engine.search_resources_text(
            "rare-alias", body_weight=1.0, metadata_weight=0.0
        )
        body_first = engine.search_resources_text(
            "rare-alias", body_weight=1.0, metadata_weight=0.2
        )
        default_weight = engine.search_resources_text("rare-alias")
        metadata_first = engine.search_resources_text(
            "rare-alias", body_weight=1.0, metadata_weight=2.0
        )
        filtered = engine.search_resources_text(
            "rare-alias",
            metadata_filters=MetadataFilters(
                all=(MetadataFilter("status", "ready"),),
            ),
        )
        hidden = engine.search_resources_text("PRIVATE_STORE_ONLY_SENTINEL")
        inspection = engine.get_resource_metadata("alias-hit").to_dict()
        with pytest.raises(ResourceCatalogError) as exc_info:
            engine.get_resource_metadata("PRIVATE_MISSING_RESOURCE_SENTINEL")

    assert [item["resource_id"] for item in alias_only.results] == ["alias-hit"]
    assert [item["resource_id"] for item in body_only.results] == ["body-hit"]
    assert [item["resource_id"] for item in body_first.results] == ["body-hit", "alias-hit"]
    assert [item["resource_id"] for item in metadata_first.results] == [
        "alias-hit",
        "body-hit",
    ]
    assert default_weight.to_dict() == body_first.to_dict()
    assert [item["resource_id"] for item in filtered.results] == ["alias-hit"]
    assert hidden.results == ()
    assert set(alias_only.to_dict()) == {
        "query",
        "target",
        "results",
        "total_count",
        "degraded",
        "degraded_reason",
    }
    assert set(alias_only.results[0]) == {
        "logical_id",
        "resource_id",
        "unit_id",
        "score",
        "rank",
    }
    assert inspection == {
        "resource_id": "alias-hit",
        "title": "alias-hit",
        "source": {
            "aliases": ["rare-alias"],
            "status": "ready",
            "secret": "PRIVATE_STORE_ONLY_SENTINEL",
        },
        "facets": [
            {
                "namespace": "status",
                "value": "ready",
                "value_type": "string",
            }
        ],
    }
    assert all("rare-alias" not in repr(item) for item in alias_only.results)
    assert "PRIVATE_STORE_ONLY_SENTINEL" not in repr(alias_only.to_dict())
    assert "PRIVATE_MISSING_RESOURCE_SENTINEL" not in str(exc_info.value)


def test_metadata_lexical_ties_are_stable_without_missing_or_duplicate_field_bonus(
    tmp_path: Path,
) -> None:
    policy = MetadataProjectionPolicy(
        (
            MetadataProjection("/aliases", "lexical_text"),
            MetadataProjection("/summary", "lexical_text"),
        )
    )
    database = tmp_path / "metadata-ties.sqlite3"
    with SQLiteCatalog.create(database) as catalog:
        single_field = prepared_file_to_resource_batch(
            _prepared("single-field", "shared-token", "ordinary body", None),
            metadata_policy=policy,
        )
        duplicate_fields = prepared_file_to_resource_batch(
            _prepared(
                "duplicate-fields",
                "shared-token",
                "ordinary body",
                None,
                summary="shared-token",
            ),
            metadata_policy=policy,
        )
        assert single_field.representations[-1].text == "shared-token"
        assert duplicate_fields.representations[-1].text == "shared-token"
        catalog.replace_resource(single_field)
        catalog.replace_resource(duplicate_fields)
        catalog.replace_resource(
            prepared_file_to_resource_batch(
                _prepared("absent-metadata", None, "ordinary body", None),
                metadata_policy=policy,
            )
        )
        engine = _engine(catalog, tmp_path)

        first_result = engine.search_resources_text(
            "shared-token", body_weight=0.0, metadata_weight=0.2
        )
        second_result = engine.search_resources_text(
            "shared-token", body_weight=0.0, metadata_weight=0.2
        )

    assert first_result.to_dict() == second_result.to_dict()
    assert {item["resource_id"] for item in first_result.results} == {
        "single-field",
        "duplicate-fields",
    }
    assert [item["rank"] for item in first_result.results] == [1, 2]
    assert len(first_result.results) == 2
