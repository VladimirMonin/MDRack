"""M3 allowlisted metadata lexical graph contracts."""

from __future__ import annotations

from mdrack.application.compatibility import prepared_file_to_resource_batch
from mdrack.application.metadata_projection import MetadataProjection, MetadataProjectionPolicy
from mdrack.domain.indexing import PreparedFile, StoredChunk, StoredSection
from mdrack.domain.profiles import EmbeddingProfile


def _profile() -> EmbeddingProfile:
    return EmbeddingProfile(
        name="default",
        provider="fake",
        runtime="offline-test",
        model_key="fake",
        model_family="test",
        quantization="none",
        output_dimensions=2,
        query_instruction="query",
        normalization_mode="l2",
        endpoint_family="offline",
    )


def _prepared() -> PreparedFile:
    return PreparedFile(
        record_id="row",
        logical_id="resource",
        root_id="vault",
        relative_path="note.md",
        title="Fallback",
        source_hash="abc123",
        indexed_at="2026-07-20T00:00:00+00:00",
        parser_name="markdown_it",
        parser_version="1",
        chunk_strategy_name="structural",
        chunk_strategy_version="1",
        index_run_id="run",
        sections=(
            StoredSection(
                "section",
                "section-logical",
                "Body",
                ("Body",),
                1,
                1,
                2,
                None,
            ),
        ),
        chunks=(
            StoredChunk(
                "chunk",
                "unit-body",
                "section",
                "ordinary body",
                "text",
                0,
                ("Body",),
                None,
                None,
                "ordinary body",
                "hash",
                2,
                2,
                "block",
            ),
        ),
        source_metadata={
            "aliases": ["Second alias", "First alias", "Second alias"],
            "summary": "Selected summary",
            "secret": "PRIVATE_STORE_ONLY_SENTINEL",
        },
        vectors=((1.0, 0.0),),
        embedding_profile=_profile(),
        embedding_model="fake",
        embedding_dimensions=2,
    )


def test_metadata_text_is_separate_deterministic_and_never_embedded_by_default() -> None:
    policy = MetadataProjectionPolicy(
        (
            MetadataProjection("/aliases", "lexical_text"),
            MetadataProjection("/summary", "lexical_text"),
            MetadataProjection("/secret", "store_only"),
        )
    )

    first = prepared_file_to_resource_batch(_prepared(), metadata_policy=policy)
    second = prepared_file_to_resource_batch(_prepared(), metadata_policy=policy)

    assert first == second
    metadata_representations = [
        item for item in first.representations if item.representation_kind == "metadata_text"
    ]
    metadata_units = [
        item
        for item in first.units
        if item.representation_id == metadata_representations[0].representation_id
    ]
    assert len(metadata_representations) == len(metadata_units) == 1
    assert metadata_representations[0].text == "Second alias\nFirst alias\nSelected summary"
    assert metadata_units[0].text == metadata_representations[0].text
    assert metadata_units[0].representation_id == metadata_representations[0].representation_id
    assert [vector.unit_id for vector in first.vectors] == [
        unit.unit_id
        for unit in first.units
        if unit.representation_id != metadata_representations[0].representation_id
    ]
    assert first.representations[0].representation_kind == "retrieval_text"
    assert first.representations[0].text == "ordinary body"
    assert first.units[0].text == "ordinary body"
    assert "PRIVATE_STORE_ONLY_SENTINEL" not in repr(first.representations)
    assert "PRIVATE_STORE_ONLY_SENTINEL" not in repr(first.units)


def test_metadata_text_is_absent_when_no_allowlisted_value_is_projected() -> None:
    batch = prepared_file_to_resource_batch(
        _prepared(),
        metadata_policy=MetadataProjectionPolicy(
            (
                MetadataProjection("/missing", "lexical_text"),
                MetadataProjection("/secret", "store_only"),
            )
        ),
    )

    assert [item.representation_kind for item in batch.representations] == [
        "retrieval_text",
        "whole_resource_text",
    ]
    assert [item.unit_kind for item in batch.units] == ["text_chunk", "whole_resource"]
    assert [vector.unit_id for vector in batch.vectors] == [item.unit_id for item in batch.units]
