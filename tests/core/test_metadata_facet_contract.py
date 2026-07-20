"""M2 app projection onto the frozen core facet contract."""

from __future__ import annotations

from mdrack.application.compatibility import prepared_file_to_resource_batch
from mdrack.application.metadata_projection import (
    FACET_SCALAR_CODEC,
    MetadataProjection,
    MetadataProjectionPolicy,
)
from mdrack.domain.indexing import PreparedFile, StoredChunk, StoredSection
from mdrack_core.domain import FACET_ORIGIN_SOURCE


def _prepared() -> PreparedFile:
    return PreparedFile(
        record_id="row",
        logical_id="resource",
        root_id="vault",
        relative_path="note.md",
        title="Fallback title",
        source_hash="abc123",
        indexed_at="2026-07-20T00:00:00+00:00",
        parser_name="markdown_it",
        parser_version="1",
        chunk_strategy_name="structural",
        chunk_strategy_version="1",
        index_run_id="run",
        sections=(StoredSection("section", "section-logical", "Title", ("Title",), 1, 1, 2, None),),
        chunks=(
            StoredChunk(
                "chunk",
                "unit",
                "section",
                "needle",
                "text",
                0,
                ("Title",),
                None,
                None,
                "needle",
                "hash",
                2,
                2,
                "block",
            ),
        ),
        source_metadata={
            "title": "Projected title",
            "tags": ["python", "python", 3, 3.0, False, None],
            "nested": {"status": "ready"},
            "opaque": {"private": "PRIVATE_METADATA_SENTINEL"},
        },
    )


def test_projection_populates_existing_core_facets_without_schema_or_core_changes() -> None:
    policy = MetadataProjectionPolicy(
        (
            MetadataProjection("/title", "canonical_title"),
            MetadataProjection("/tags", "facet_many", "tag"),
            MetadataProjection("/nested/status", "facet", "status"),
            MetadataProjection("/opaque", "store_only"),
        )
    )
    batch = prepared_file_to_resource_batch(_prepared(), metadata_policy=policy)

    assert batch.resource.title == "Projected title"
    assert batch.resource.metadata["source"]["title"] == "Projected title"
    assert batch.resource.metadata["source"]["tags"] == (
        "python",
        "python",
        3,
        3.0,
        False,
        None,
    )
    assert batch.resource.metadata["source"]["opaque"] == {
        "private": "PRIVATE_METADATA_SENTINEL"
    }
    assert batch.resource.metadata["ingestion"]["projection_policy_fingerprint"] == policy.fingerprint
    assert [
        (assignment.facet.namespace, FACET_SCALAR_CODEC.decode(assignment.facet.value))
        for assignment in batch.facets
    ] == [
        ("tag", "python"),
        ("tag", 3),
        ("tag", 3.0),
        ("tag", False),
        ("tag", None),
        ("status", "ready"),
    ]
    assert all(assignment.origin == FACET_ORIGIN_SOURCE for assignment in batch.facets)
    assert all(assignment.producer_fingerprint == policy.fingerprint for assignment in batch.facets)
    assert all("PRIVATE_METADATA_SENTINEL" not in (unit.text or "") for unit in batch.units)
    assert all(
        "PRIVATE_METADATA_SENTINEL" not in (representation.text or "")
        for representation in batch.representations
    )


def test_default_projection_provides_observer_neutral_title_tags_and_aliases() -> None:
    batch = prepared_file_to_resource_batch(_prepared())

    assert batch.resource.title == "Projected title"
    assert [FACET_SCALAR_CODEC.decode(item.facet.value) for item in batch.facets] == [
        "python",
        3,
        3.0,
        False,
        None,
    ]
    assert batch.resource.metadata["source"]["opaque"] == {
        "private": "PRIVATE_METADATA_SENTINEL"
    }
