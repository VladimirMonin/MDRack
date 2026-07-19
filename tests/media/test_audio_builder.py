from __future__ import annotations

from dataclasses import replace

import pytest

from mdrack_core import Locator
from mdrack_media import (
    EmbeddingFingerprint,
    MediaResourceDescriptor,
    NormalizationFingerprint,
    ProducerFingerprint,
    TimedChunkingPolicy,
    TimedTextAtom,
    TokenCounterFingerprint,
    TranscriptArtifact,
    TranscriptBatchBuilderInput,
    atom_id,
    build_audio_transcript_batch,
    build_video_transcript_batch,
    group_timed_atoms,
    representation_id,
    resource_id,
)


class WordCounter:
    fingerprint = TokenCounterFingerprint.from_payload({"kind": "test-words", "version": 1})

    def count(self, text: str) -> int:
        return len(text.split())


def _input(
    policy: TimedChunkingPolicy | None = None,
    *,
    resource_name: str = "audio-1",
    resource_kind: str = "audio",
) -> tuple[TranscriptBatchBuilderInput, WordCounter]:
    counter = WordCounter()
    policy = policy or TimedChunkingPolicy(
        soft_min_tokens=1,
        target_tokens=3,
        soft_max_tokens=5,
        hard_max_tokens=8,
        soft_min_duration_ms=1,
        target_duration_ms=1_000,
        soft_max_duration_ms=3_000,
        hard_max_duration_ms=5_000,
    )
    resource = resource_id("fixture", resource_name)
    producer = ProducerFingerprint.from_payload({"engine": "fixture", "version": 1})
    normalization = NormalizationFingerprint.from_payload({"whitespace": "preserve"})
    atoms = tuple(
        TimedTextAtom(
            atom_id=atom_id(resource, producer.value, ordinal),
            resource_id=resource,
            start_ms=ordinal * 1_000,
            end_ms=(ordinal + 1) * 1_000,
            text=f"public transcript {ordinal}",
            ordinal=ordinal,
            producer_fingerprint=producer,
            normalization_fingerprint=normalization,
        )
        for ordinal in range(3)
    )
    grouped = group_timed_atoms(
        atoms,
        policy=policy,
        token_counter=counter,
        token_count_kind="exact",
        resource_identifier=resource,
        normalization_fingerprint=normalization,
    )
    transcript = TranscriptArtifact(
        resource_id=resource,
        representation_id=representation_id(
            resource, "audio_transcript", producer.value, normalization.value
        ),
        representation_kind="audio_transcript",
        atoms=atoms,
        producer_fingerprint=producer,
        normalization_fingerprint=normalization,
        duration_ms=3_000,
    )
    return (
        TranscriptBatchBuilderInput(
            resource=MediaResourceDescriptor(
                resource,
                resource_kind,
                "video/mp4" if resource_kind == "video" else "audio/wav",
                "fixture",
                Locator("relative", {"name": f"{resource_name}.wav"}),
            ),
            transcript=transcript,
            passage_representation_id=grouped.representation_id,
            passage_representation_kind="timed_passage",
            chunking_policy=policy,
            grouper_fingerprint=grouped.grouper_fingerprint,
            embedding_fingerprint=EmbeddingFingerprint.from_payload({"space": "test", "version": 1}),
        ),
        counter,
    )


def test_audio_builder_projects_passages_with_exact_half_open_times() -> None:
    input_value, counter = _input()
    input_value = replace(input_value, embedding_fingerprint=None)
    batch = build_audio_transcript_batch(input_value, token_counter=counter)

    assert batch.resource.resource_kind == "audio"
    assert batch.units
    assert all(unit.unit_kind == "time_segment" for unit in batch.units)
    assert batch.units[0].evidence_locator.payload == {
        "end_ms": 2_000,
        "start_ms": 0,
        "track": "audio",
    }
    assert batch.units[0].evidence_locator.payload["start_ms"] < batch.units[0].evidence_locator.payload["end_ms"]
    assert all(unit.representation_id == input_value.passage_representation_id for unit in batch.units)
    assert batch.representations[0].representation_id == input_value.passage_representation_id
    assert batch.representations[0].representation_kind == "timed_passage"
    assert "content_preview" not in batch.units[0].metadata
    assert batch.vectors == ()


def test_audio_builder_requires_exact_ready_vectors_and_rebuilds_on_fingerprint() -> None:
    input_value, counter = _input()
    no_vectors = build_audio_transcript_batch(
        replace(input_value, embedding_fingerprint=None), token_counter=counter
    )
    vectors = {unit.unit_id: (1.0, 0.0) for unit in no_vectors.units}
    with_vectors = build_audio_transcript_batch(input_value, token_counter=counter, vectors=vectors)
    assert len(with_vectors.spaces) == 1
    assert len(with_vectors.vectors) == len(with_vectors.units)
    other_input, other_counter = _input(resource_name="audio-2")
    other_no_vectors = build_audio_transcript_batch(
        replace(other_input, embedding_fingerprint=None), token_counter=other_counter
    )
    other_batch = build_audio_transcript_batch(
        other_input,
        token_counter=other_counter,
        vectors={unit.unit_id: (1.0, 0.0) for unit in other_no_vectors.units},
    )
    assert other_batch.spaces[0].space_id == with_vectors.spaces[0].space_id

    changed, changed_counter = _input(
        TimedChunkingPolicy(
            soft_min_tokens=1,
            target_tokens=2,
            soft_max_tokens=4,
            hard_max_tokens=8,
            soft_min_duration_ms=1,
            target_duration_ms=1_000,
            soft_max_duration_ms=3_000,
            hard_max_duration_ms=5_000,
        )
    )
    changed_batch = build_audio_transcript_batch(
        replace(changed, embedding_fingerprint=None), token_counter=changed_counter
    )
    assert changed_batch.units[0].unit_id != with_vectors.units[0].unit_id

    with pytest.raises(ValueError, match="exactly one vector"):
        build_audio_transcript_batch(input_value, token_counter=counter, vectors={})


def test_audio_builder_rejects_empty_transcript_without_search_units() -> None:
    input_value, counter = _input()
    empty = replace(
        input_value,
        transcript=replace(input_value.transcript, atoms=()),
        embedding_fingerprint=None,
    )

    with pytest.raises(ValueError, match="at least one timed passage"):
        build_audio_transcript_batch(empty, token_counter=counter)


def test_video_builder_reuses_transcript_projection_with_video_kind_and_seek_track() -> None:
    input_value, counter = _input(resource_name="video-1", resource_kind="video")
    batch = build_video_transcript_batch(
        replace(input_value, embedding_fingerprint=None), token_counter=counter
    )

    assert batch.resource.resource_kind == "video"
    assert batch.resource.media_type == "video/mp4"
    assert batch.units
    assert all(unit.evidence_locator.payload["track"] == "video" for unit in batch.units)
    assert all(unit.unit_kind == "time_segment" for unit in batch.units)
    assert batch.representations[0].text == "\n\n".join(unit.text for unit in batch.units)


def test_transcript_builders_reject_cross_kind_projection() -> None:
    audio_input, counter = _input()
    video_input, video_counter = _input(resource_name="video-1", resource_kind="video")

    with pytest.raises(ValueError, match="requires a video resource"):
        build_video_transcript_batch(audio_input, token_counter=counter)
    with pytest.raises(ValueError, match="requires a audio resource"):
        build_audio_transcript_batch(video_input, token_counter=video_counter)
