"""S6 application/core compatibility contracts."""

from __future__ import annotations

from dataclasses import replace

import pytest

from mdrack.application.compatibility import (
    CoreCompatibilityMapper,
    embedding_space_id,
    prepared_file_to_resource_batch,
)
from mdrack.application.vector_values import FLOAT32_VALUE_POLICY, canonicalize_float32
from mdrack.domain.indexing import PreparedFile, StoredChunk, StoredSection
from mdrack.domain.profiles import EmbeddingProfile
from mdrack_core.domain import (
    UNIT_WHOLE_RESOURCE,
    Degradation,
    DegradationCategory,
    Locator,
    RankedCandidate,
    SearchResult,
    SearchResultItem,
)
from mdrack_media import AggregationFingerprint, WholeResourceTextPolicy


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
        record_id="internal-file-row",
        logical_id="document-logical",
        root_id="vault",
        relative_path="docs/guide.md",
        title="Guide",
        source_hash="abc123",
        indexed_at="2026-07-18T00:00:00+00:00",
        parser_name="markdown_it",
        parser_version="1",
        chunk_strategy_name="structural",
        chunk_strategy_version="1",
        index_run_id="internal-run-row",
        sections=(
            StoredSection(
                record_id="internal-section-row",
                logical_id="section-logical",
                title="Heading",
                heading_path=("Guide", "Heading"),
                level=2,
                start_line=1,
                end_line=3,
                parent_record_id=None,
            ),
        ),
        chunks=(
            StoredChunk(
                record_id="internal-chunk-row",
                logical_id="chunk-logical",
                section_record_id="internal-section-row",
                content="Alpha searchable text",
                content_type="text",
                chunk_index=0,
                heading_path=("Guide", "Heading"),
                previous_record_id=None,
                next_record_id=None,
                embedding_text="Guide Heading Alpha searchable text",
                embedding_text_hash="embedding-hash",
                start_line=2,
                end_line=3,
                block_logical_id="block-logical",
                start_offset=8,
                end_offset=29,
                block_kind="paragraph",
                chunk_kind="text",
            ),
        ),
        vectors=((1.0, -0.0),),
        embedding_profile=_profile(),
        embedding_model="fake",
        embedding_dimensions=2,
    )


def test_prepared_file_projects_one_deterministic_complete_core_graph() -> None:
    first = prepared_file_to_resource_batch(_prepared())
    second = prepared_file_to_resource_batch(_prepared())

    assert first == second
    assert first.resource.resource_id == "document-logical"
    assert first.resource.locator.payload == {
        "document_logical_id": "document-logical",
        "root_id": "vault",
    }
    assert first.resource.metadata["relative_path"] == "docs/guide.md"
    assert len(first.representations) == 2
    assert [unit.unit_id for unit in first.units[:1]] == ["chunk-logical"]
    assert first.units[1].unit_kind == UNIT_WHOLE_RESOURCE
    assert first.units[0].evidence_locator.payload["heading_path"] == ("Guide", "Heading")
    assert first.vectors[0].unit_id == "chunk-logical"
    assert first.vectors[0].space_id == first.spaces[0].space_id
    assert first.spaces[0].fingerprint == _profile().fingerprint
    serialized = repr(first)
    for internal_id in ("internal-file-row", "internal-section-row", "internal-chunk-row", "internal-run-row"):
        assert internal_id not in serialized


def test_f32_policy_changes_profile_space_and_producer_identity_and_canonicalizes_vectors() -> None:
    baseline = prepared_file_to_resource_batch(_prepared())
    profile = replace(_profile(), vector_value_policy=FLOAT32_VALUE_POLICY)
    prepared = replace(_prepared(), embedding_profile=profile, vectors=((1.0 + 2**-30, -0.0),))

    batch = prepared_file_to_resource_batch(prepared)

    assert batch.spaces[0].space_id == embedding_space_id(
        profile.name,
        profile.fingerprint,
        FLOAT32_VALUE_POLICY,
    )
    assert batch.spaces[0].space_id != baseline.spaces[0].space_id
    assert batch.spaces[0].metadata == {
        "profile": profile.name,
        "vector_codec": "ieee754-f32-le-v1",
        "vector_value_policy": FLOAT32_VALUE_POLICY,
    }
    assert batch.representations[0].producer_fingerprint != baseline.representations[0].producer_fingerprint
    assert all(vector.vector == canonicalize_float32(vector.vector) for vector in batch.vectors)


def test_prepared_file_default_projection_has_deterministic_whole_text_similarity_basis() -> None:
    prepared = _prepared()
    before = repr(prepared)

    first = prepared_file_to_resource_batch(prepared)
    second = prepared_file_to_resource_batch(prepared)
    whole = [unit for unit in first.units if unit.unit_kind == UNIT_WHOLE_RESOURCE]

    assert first == second
    assert len(whole) == 1
    assert whole[0].modality == "text"
    assert whole[0].metadata["similarity_basis"] == "markdown_retrieval_text"
    assert whole[0].metadata["aggregation"] == "token_weighted_centroid_v1"
    assert any(vector.unit_id == whole[0].unit_id for vector in first.vectors)
    assert repr(prepared) == before


def test_compatibility_mapper_preserves_legacy_scores_ranks_locator_and_degradation() -> None:
    locator = Locator(
        "document_span",
        {
            "root_id": "vault",
            "relative_path": "docs/guide.md",
            "start_line": 2,
            "end_line": 3,
            "start_offset": 8,
            "end_offset": 29,
            "heading_path": ("Guide", "Heading"),
            "block_kind": "paragraph",
            "chunk_kind": "text",
            "block_logical_id": "block-logical",
            "chunk_logical_id": "chunk-logical",
        },
    )
    text = RankedCandidate(
        "chunk-logical",
        "document-logical",
        "representation-logical",
        1,
        0.75,
        "text",
        locator,
        {"content_preview": "Alpha", "section_title": "Heading"},
    )
    semantic = RankedCandidate(
        "chunk-logical",
        "document-logical",
        "representation-logical",
        2,
        0.5,
        "semantic",
        locator,
        {"content_preview": "Alpha", "section_title": "Heading"},
    )
    core = SearchResult(
        target="unit",
        items=(
            SearchResultItem(
                logical_id="chunk-logical",
                resource_id="document-logical",
                unit_id="chunk-logical",
                score=0.031,
                rank=1,
                evidence=(text, semantic),
                metadata=text.metadata,
            ),
        ),
        degradations=(Degradation("semantic", DegradationCategory.ADAPTER_TIMEOUT),),
    )

    mapped = CoreCompatibilityMapper().retrieval_result(
        query="Alpha",
        mode="hybrid",
        result=core,
    )

    item = mapped.results[0]
    assert item.logical_id == item.chunk_id == "chunk-logical"
    assert item.score == item.rrf_score == 0.031
    assert item.text_rank == 1
    assert item.semantic_rank == 2
    assert item.text_score == 0.75
    assert item.semantic_score == 0.5
    assert item.rrf_rank == 1
    assert item.source_locator.to_dict()["heading_path"] == ["Guide", "Heading"]
    assert mapped.degraded is True
    assert mapped.degraded_reason == "adapter_timeout"


def test_prepared_file_projects_short_whole_resource_with_explicit_basis() -> None:
    batch = prepared_file_to_resource_batch(
        _prepared(),
        whole_text_policy=WholeResourceTextPolicy(max_tokens=100),
        aggregation_fingerprint=AggregationFingerprint.from_payload({"policy": "short-v1"}),
        whole_vector=(0.0, 1.0),
    )

    whole = [unit for unit in batch.units if unit.unit_kind == UNIT_WHOLE_RESOURCE]
    assert len(whole) == 1
    assert len(batch.representations) == 2
    assert whole[0].metadata["similarity_basis"] == "markdown_retrieval_text"
    assert batch.vectors[-1].unit_id == whole[0].unit_id


def test_prepared_file_projects_long_whole_resource_by_weighted_centroid() -> None:
    batch = prepared_file_to_resource_batch(
        _prepared(),
        whole_text_policy=WholeResourceTextPolicy(max_tokens=1, overflow="caller_split"),
        aggregation_fingerprint=AggregationFingerprint.from_payload({"policy": "long-v1"}),
    )

    whole = [unit for unit in batch.units if unit.unit_kind == UNIT_WHOLE_RESOURCE]
    assert len(whole) == 1
    assert batch.vectors[-1].unit_id == whole[0].unit_id
    assert batch.vectors[-1].vector == (1.0, 0.0)


def test_prepared_file_rejects_long_whole_resource_without_vectors() -> None:
    prepared = PreparedFile(**{**_prepared().__dict__, "vectors": ()})
    with pytest.raises(ValueError, match="requires chunk vectors"):
        prepared_file_to_resource_batch(
            prepared,
            whole_text_policy=WholeResourceTextPolicy(max_tokens=1, overflow="caller_split"),
            aggregation_fingerprint=AggregationFingerprint.from_payload({"policy": "reject-v1"}),
        )
