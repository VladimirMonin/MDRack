from __future__ import annotations

from mdrack_core import (
    Locator,
    PreparedResourceBatch,
    RepresentationRecord,
    ResourceRecord,
    SearchScope,
    SearchUnitRecord,
)
from mdrack_media import (
    TOKEN_COUNT_ESTIMATED,
    EmbeddingFingerprint,
    FrameBatchBuilderInput,
    FrameCaptionArtifact,
    FrameCaptionObservation,
    MediaResourceDescriptor,
    NormalizationFingerprint,
    ProducerFingerprint,
    TokenCount,
    TokenCounterFingerprint,
    build_video_frame_caption_batch,
    frame_id,
    representation_id,
    resource_id,
    retrieve_media,
)

PRODUCER = ProducerFingerprint.from_payload({"producer": "fixture"})
NORMALIZATION = NormalizationFingerprint.from_payload({"normalization": "fixture"})
TOKEN_COUNTER = TokenCounterFingerprint.from_payload({"counter": "fixture"})
EMBEDDING = EmbeddingFingerprint.from_payload({"embedding": "fixture"})
VIDEO_ID = resource_id("fixture", "video.mp4")


def _frame_batch(
    *captions: str, timestamps_ms: tuple[int, ...] | None = None
) -> PreparedResourceBatch:
    timestamps = timestamps_ms if timestamps_ms is not None else tuple(
        ordinal * 1_000 for ordinal in range(len(captions))
    )
    assert len(timestamps) == len(captions)
    observations = tuple(
        FrameCaptionObservation(
            frame_id=frame_id(VIDEO_ID, PRODUCER.value, ordinal, timestamp_ms, f"obs-{ordinal}"),
            resource_id=VIDEO_ID,
            timestamp_ms=timestamp_ms,
            observation_identity=f"obs-{ordinal}",
            caption=caption,
            ordinal=ordinal,
            token_count=TokenCount(len(caption.split()), TOKEN_COUNT_ESTIMATED, TOKEN_COUNTER),
            producer_fingerprint=PRODUCER,
            normalization_fingerprint=NORMALIZATION,
        )
        for ordinal, (caption, timestamp_ms) in enumerate(zip(captions, timestamps))
    )
    artifact = FrameCaptionArtifact(
        VIDEO_ID,
        representation_id(VIDEO_ID, "frame_caption", PRODUCER.value, NORMALIZATION.value),
        "frame_caption",
        observations,
        PRODUCER,
        NORMALIZATION,
    )
    return build_video_frame_caption_batch(
        FrameBatchBuilderInput(
            MediaResourceDescriptor(VIDEO_ID, "video", "video/mp4", "fixture", Locator("opaque", {})),
            artifact,
            EMBEDDING,
        ),
        vectors={item.frame_id: (1.0, 0.0) for item in observations},
    )


def _transcript_batch() -> PreparedResourceBatch:
    resource = resource_id("fixture", "audio.wav")
    representation = "transcript-representation"
    units = tuple(
        SearchUnitRecord(
            f"transcript-unit-{ordinal}",
            resource,
            representation,
            "time_segment",
            "text",
            text,
            Locator("time_segment", {"start_ms": ordinal * 1_000, "end_ms": (ordinal + 1) * 1_000}),
            ordinal,
        )
        for ordinal, text in enumerate(("pipeline transcript", "other words"))
    )
    return PreparedResourceBatch(
        ResourceRecord(resource, "audio", "audio/wav", "fixture", Locator("opaque", {})),
        (RepresentationRecord(representation, resource, "timed_passage", "text", "pipeline transcript"),),
        units,
    )


def test_retrieval_filters_before_limit_and_returns_stable_frame_ids() -> None:
    batch = _frame_batch("pipeline diagram", "irrelevant", "pipeline title")
    result = retrieve_media(
        (batch,),
        "pipeline",
        mode="frame",
        scope=SearchScope(resource_kinds=("video",), unit_kinds=("frame",)),
        limit=2,
    )
    assert [item.unit_id for item in result.items] == sorted(
        (batch.units[0].unit_id, batch.units[2].unit_id)
    )
    assert [item.rank for item in result.items] == [1, 2]


def test_hybrid_weighting_and_nearby_frames_are_deterministic() -> None:
    frame_batch = _frame_batch("pipeline diagram", "nearby", "pipeline title")
    first = retrieve_media(
        (frame_batch, _transcript_batch()),
        "pipeline",
        mode="hybrid",
        frame_weight=3.0,
        limit=1,
        nearby_frame_limit=1,
    )
    second = retrieve_media(
        (frame_batch, _transcript_batch()),
        "pipeline",
        mode="hybrid",
        frame_weight=3.0,
        limit=1,
        nearby_frame_limit=1,
    )
    assert first == second
    assert first.items[0].unit_id in {frame_batch.units[0].unit_id, frame_batch.units[2].unit_id}
    assert first.nearby_frames[0].unit_id == frame_batch.units[1].unit_id
    assert first.nearby_frames[0].unit_id not in {item.unit_id for item in first.items}


def test_empty_query_has_empty_core_and_nearby_results() -> None:
    result = retrieve_media((_frame_batch("pipeline"),), "  ", mode="frame", nearby_frame_limit=2)
    assert result.items == ()
    assert result.nearby_frames == ()


def test_nearby_frames_use_minimum_distance_to_all_selected_hits() -> None:
    batch = _frame_batch(
        "pipeline early",
        "near early",
        "near late",
        "pipeline late",
        timestamps_ms=(0, 100, 9_000, 10_000),
    )

    result = retrieve_media(
        (batch,),
        "pipeline",
        mode="frame",
        limit=2,
        nearby_frame_limit=2,
    )

    assert {item.unit_id for item in result.items} == {
        batch.units[0].unit_id,
        batch.units[3].unit_id,
    }
    assert [item.unit_id for item in result.nearby_frames] == [
        batch.units[1].unit_id,
        batch.units[2].unit_id,
    ]


def test_public_evidence_serialization_is_typed_and_privacy_safe() -> None:
    batch = _frame_batch("pipeline diagram")
    unit = batch.units[0]
    result = retrieve_media((batch,), "pipeline", mode="frame", limit=1)

    item = result.items[0]
    assert item.evidence is not None
    evidence = item.evidence.to_dict()
    assert evidence["source_type"] == "frame"
    assert evidence["unit_kind"] == "frame"
    assert evidence["timestamp_ms"] == 0
    assert evidence["timestamp_unit"] == "ms"
    assert evidence["frame_id"] == unit.unit_id
    assert "/private" not in repr(result.to_dict())
    assert "fixture" not in repr(result.to_dict())
    assert "evidence_locator" not in result.to_dict()["items"][0]


def test_hybrid_public_evidence_explains_both_ranking_branches() -> None:
    result = retrieve_media(
        (_frame_batch("pipeline frame"), _transcript_batch()),
        "pipeline",
        mode="hybrid",
        limit=2,
    )

    for item in result.items:
        assert item.evidence is not None
        branches = {entry["branch_id"] for entry in item.evidence.provenance}
        assert branches <= {"transcript", "frame"}
        assert branches
    payload = result.to_dict()
    assert payload["mode"] == "hybrid"
    assert all("provenance" in item["evidence"] for item in payload["items"])
