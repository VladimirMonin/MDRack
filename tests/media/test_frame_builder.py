from __future__ import annotations

from dataclasses import replace

import pytest

from mdrack_core import (
    BranchScopeOverride,
    LexicalBranch,
    Locator,
    SearchRequest,
    SearchScope,
    VectorBranch,
)
from mdrack_core.application.retrieval import RetrievalService
from mdrack_media import (
    TOKEN_COUNT_ESTIMATED,
    AggregationFingerprint,
    EmbeddingFingerprint,
    FrameBatchBuilderInput,
    FrameCaptionArtifact,
    FrameCaptionObservation,
    MediaResourceDescriptor,
    NormalizationFingerprint,
    ProducerFingerprint,
    TokenCount,
    TokenCounterFingerprint,
    VideoFrameLocator,
    WholeResourceTextPolicy,
    build_video_frame_caption_batch,
    frame_id,
    representation_id,
    resource_id,
)
from mdrack_sqlite import SQLiteCatalog

PRODUCER = ProducerFingerprint.from_payload({"engine": "caption-fixture", "version": 1})
NORMALIZATION = NormalizationFingerprint.from_payload({"whitespace": "preserve", "version": 1})
EMBEDDING = EmbeddingFingerprint.from_payload({"dimensions": 2, "space": "caption-fixture"})
VIDEO_ID = resource_id("fixture", "video-caption.mp4")
REPRESENTATION_ID = representation_id(
    VIDEO_ID, "frame_caption", PRODUCER.value, NORMALIZATION.value
)


def _input(*captions: str) -> FrameBatchBuilderInput:
    observations = tuple(
        FrameCaptionObservation(
            frame_id=frame_id(VIDEO_ID, PRODUCER.value, ordinal, ordinal * 1_000, f"obs-{ordinal}"),
            resource_id=VIDEO_ID,
            timestamp_ms=ordinal * 1_000,
            observation_identity=f"obs-{ordinal}",
            caption=caption,
            ordinal=ordinal,
            token_count=TokenCount(
                count=len(caption.split()),
                kind=TOKEN_COUNT_ESTIMATED,
                counter_fingerprint=TokenCounterFingerprint.from_payload({"counter": "fixture"}),
            ),
            producer_fingerprint=PRODUCER,
            normalization_fingerprint=NORMALIZATION,
        )
        for ordinal, caption in enumerate(captions)
    )
    artifact = FrameCaptionArtifact(
        resource_id=VIDEO_ID,
        representation_id=REPRESENTATION_ID,
        representation_kind="frame_caption",
        observations=observations,
        producer_fingerprint=PRODUCER,
        normalization_fingerprint=NORMALIZATION,
    )
    return FrameBatchBuilderInput(
        resource=MediaResourceDescriptor(
            resource_id=VIDEO_ID,
            resource_kind="video",
            media_type="video/mp4",
            source_namespace="fixture",
            locator=Locator("host_ref", {"opaque": "video-caption"}),
        ),
        frames=artifact,
        embedding_fingerprint=EMBEDDING,
    )


def test_frame_builder_is_stable_and_preserves_timestamp_evidence() -> None:
    input_value = _input("opening title", "diagram of the pipeline")
    vectors = {
        input_value.frames.observations[0].frame_id: (1.0, 0.0),
        input_value.frames.observations[1].frame_id: (0.0, 1.0),
    }
    first = build_video_frame_caption_batch(input_value, vectors=vectors)
    second = build_video_frame_caption_batch(input_value, vectors=vectors)

    assert first == second
    assert tuple(unit.unit_id for unit in first.units) == tuple(vectors)
    assert first.units[1].unit_kind == "frame"
    assert first.units[1].evidence_locator == VideoFrameLocator(
        1_000, input_value.frames.observations[1].frame_id
    ).to_core_locator()
    assert first.vectors[0].space_id == first.spaces[0].space_id

    replacement = build_video_frame_caption_batch(
        replace(_input("replacement caption"), embedding_fingerprint=None)
    )
    assert replacement.resource.content_hash != first.resource.content_hash
    regenerated = _input("replacement caption")
    assert regenerated.frames.observations[0].frame_id == input_value.frames.observations[0].frame_id
    assert (
        regenerated.frames.observations[0].content_fingerprint
        != input_value.frames.observations[0].content_fingerprint
    )
    assert replacement.units[0].metadata["content_fingerprint"] == (
        regenerated.frames.observations[0].content_fingerprint.value
    )


def test_empty_frame_artifact_is_a_valid_non_searchable_replacement() -> None:
    empty = build_video_frame_caption_batch(replace(_input(), embedding_fingerprint=None))
    assert empty.resource.resource_kind == "video"
    assert empty.representations[0].text == ""
    assert empty.units == ()
    assert empty.spaces == ()


def test_frame_builder_rejects_vector_shape_and_fingerprint_mismatch() -> None:
    input_value = _input("one frame")
    frame = input_value.frames.observations[0].frame_id
    try:
        build_video_frame_caption_batch(
            input_value,
            vectors={frame: (1.0, 0.0), "frame_missing": (0.0, 1.0)},
        )
    except ValueError as error:
        assert "exactly one vector" in str(error)
    else:
        raise AssertionError("expected a vector for an unknown frame to be rejected")

    try:
        build_video_frame_caption_batch(input_value)
    except ValueError as error:
        assert "embedding_fingerprint" in str(error)
    else:
        raise AssertionError("expected an embedding fingerprint without vectors to be rejected")


def test_frame_caption_and_transcript_branches_fuse_with_scope_and_evidence(tmp_path) -> None:
    input_value = _input("diagram pipeline", "title card")
    frame_ids = [item.frame_id for item in input_value.frames.observations]
    batch = build_video_frame_caption_batch(
        input_value,
        vectors={frame_ids[0]: (1.0, 0.0), frame_ids[1]: (0.0, 1.0)},
    )
    catalog = SQLiteCatalog.create(tmp_path / "frames.sqlite3")
    catalog.replace_resource(batch)
    request = SearchRequest(
        lexical_branches=(
            LexicalBranch(
                "frame-caption",
                "pipeline",
                weight=2.0,
                scope_override=BranchScopeOverride(
                    resource_kinds=("video",), unit_kinds=("frame",)
                ),
            ),
        ),
        vector_branches=(
            VectorBranch("frame-semantic", batch.spaces[0].space_id, (1.0, 0.0), weight=1.0),
        ),
        scope=SearchScope(resource_kinds=("video",)),
        target="unit",
        limit=2,
        evidence_limit_per_resource=2,
    )
    try:
        result = RetrievalService(catalog).search(request)
        assert result.degradations == ()
        assert result.items[0].unit_id == frame_ids[0]
        assert {item.branch_id for item in result.items[0].evidence} == {
            "frame-caption",
            "frame-semantic",
        }
        assert all(item.evidence_locator.kind == "video_frame" for item in result.items[0].evidence)
    finally:
        catalog.close()


def test_long_frame_caption_resource_rejects_partial_or_mismatched_vectors() -> None:
    input_value = _input("one frame", "another frame")
    frame_ids = [item.frame_id for item in input_value.frames.observations]
    long_input = replace(
        input_value,
        whole_text_policy=WholeResourceTextPolicy(max_tokens=1, overflow="caller_split"),
        aggregation_fingerprint=AggregationFingerprint.from_payload({"policy": "long-v1"}),
    )

    for vectors in ({frame_ids[0]: (1.0, 0.0)}, {"frame_missing": (1.0, 0.0)}):
        with pytest.raises(ValueError, match="exactly one vector"):
            build_video_frame_caption_batch(long_input, vectors=vectors)
