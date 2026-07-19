"""Provider-free audio transcript projection into the core resource graph."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence

from mdrack_core import (
    MODALITY_TEXT,
    REPRESENTATION_TRANSCRIPT_TEXT,
    UNIT_TIME_SEGMENT,
    UNIT_WHOLE_RESOURCE,
    EmbeddingSpaceRecord,
    Locator,
    PreparedResourceBatch,
    RepresentationRecord,
    ResourceRecord,
    SearchUnitRecord,
    VectorRecord,
)

from .aggregation import weighted_centroid
from .builders import RESOURCE_AUDIO, RESOURCE_VIDEO, TranscriptBatchBuilderInput
from .common import canonical_json
from .grouper import group_timed_atoms
from .identifiers import representation_id, whole_resource_id
from .locators import TRACK_AUDIO, TRACK_VIDEO, TimeSegmentLocator
from .records import REPRESENTATION_TIMED_PASSAGE, TOKEN_COUNT_EXACT, TimedPassage


def _space_id(fingerprint: str, dimensions: int, metric: str) -> str:
    return (
        "space_"
        + hashlib.sha256(
            canonical_json({"dimensions": dimensions, "fingerprint": fingerprint, "metric": metric}).encode("utf-8")
        ).hexdigest()
    )


def _content_hash(input_value: TranscriptBatchBuilderInput) -> str:
    payload = input_value.transcript.to_dict()
    return "sha256:" + hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _whole_text(passages: Sequence[TimedPassage]) -> str:
    return "\n\n".join(passage.text for passage in passages)


def _build_transcript_batch(
    input_value: TranscriptBatchBuilderInput,
    *,
    token_counter: object,
    vectors: Mapping[str, Sequence[float]] | None = None,
    metric: str = "cosine",
    resource_kind: str,
    track: str,
) -> PreparedResourceBatch:
    """Build an immutable timed-transcript graph from caller-owned text and vectors.

    The builder never reads or resolves the media locator. Passage units are the
    only retrieval units unless the input explicitly enables a whole-resource
    policy. Every fingerprint participates in the generated representation,
    unit, and vector-space identities, so changing extraction/grouping/vector
    preparation creates a replacement graph instead of silently reusing one.
    """
    if not isinstance(input_value, TranscriptBatchBuilderInput):
        raise TypeError("input_value must be a TranscriptBatchBuilderInput")
    if input_value.resource.resource_kind != resource_kind:
        raise ValueError(f"transcript builder requires a {resource_kind} resource")
    if not isinstance(vectors, (Mapping, type(None))):
        raise TypeError("vectors must be a mapping or None")
    if metric not in {"cosine", "dot", "l2"}:
        raise ValueError("metric must be cosine, dot, or l2")

    grouped = group_timed_atoms(
        input_value.transcript.atoms,
        policy=input_value.chunking_policy,
        token_counter=token_counter,  # type: ignore[arg-type]
        token_count_kind=TOKEN_COUNT_EXACT,
        resource_identifier=input_value.resource.resource_id,
        normalization_fingerprint=input_value.transcript.normalization_fingerprint,
    )
    if grouped.grouper_fingerprint != input_value.grouper_fingerprint:
        raise ValueError("grouper_fingerprint does not match the effective grouping policy")
    if not grouped.passages:
        raise ValueError("transcript must contain at least one timed passage")
    transcript = input_value.transcript
    resource_id = input_value.resource.resource_id
    passage_representation_id = input_value.passage_representation_id

    representations = [
        RepresentationRecord(
            representation_id=passage_representation_id,
            resource_id=resource_id,
            representation_kind=REPRESENTATION_TIMED_PASSAGE,
            modality=MODALITY_TEXT,
            text=_whole_text(grouped.passages),
            language=transcript.language,
            producer_fingerprint=grouped.grouper_fingerprint.value,
            token_count=sum(item.token_count.count for item in grouped.passages),
            token_count_kind=TOKEN_COUNT_EXACT,
            metadata={
                "transcript_representation_id": transcript.representation_id,
                "normalization_fingerprint": transcript.normalization_fingerprint.value,
            },
        ),
    ]
    units = [
        SearchUnitRecord(
            unit_id=passage.passage_id,
            resource_id=resource_id,
            representation_id=passage_representation_id,
            unit_kind=UNIT_TIME_SEGMENT,
            modality=MODALITY_TEXT,
            text=passage.text,
            evidence_locator=TimeSegmentLocator(passage.start_ms, passage.end_ms, track=track).to_core_locator(),
            ordinal=passage.ordinal,
            token_count=passage.token_count.count,
            token_count_kind=passage.token_count.kind,
            metadata={
                **dict(passage.metadata),
                "source_atom_ids": tuple(passage.source_atom_ids),
            },
        )
        for passage in grouped.passages
    ]

    whole_vector: tuple[float, ...] | None = None
    if input_value.whole_text_policy is not None:
        total_tokens = sum(item.token_count.count for item in grouped.passages)
        is_long = total_tokens > input_value.whole_text_policy.max_tokens
        if is_long and input_value.whole_text_policy.overflow == "reject":
            raise ValueError("whole transcript exceeds whole_text_policy.max_tokens")
        whole_id = whole_resource_id(
            resource_id,
            transcript.representation_id,
            input_value.aggregation_fingerprint.value,  # type: ignore[union-attr]
        )
        whole_representation_id = representation_id(
            resource_id,
            REPRESENTATION_TRANSCRIPT_TEXT,
            input_value.aggregation_fingerprint.value,  # type: ignore[union-attr]
            transcript.normalization_fingerprint.value,
        )
        representations.append(
            RepresentationRecord(
                representation_id=whole_representation_id,
                resource_id=resource_id,
                representation_kind=REPRESENTATION_TRANSCRIPT_TEXT,
                modality=MODALITY_TEXT,
                text=_whole_text(grouped.passages),
                language=transcript.language,
                producer_fingerprint=input_value.aggregation_fingerprint.value,  # type: ignore[union-attr]
                token_count=total_tokens,
                token_count_kind=TOKEN_COUNT_EXACT,
            )
        )
        units.append(
            SearchUnitRecord(
                unit_id=whole_id,
                resource_id=resource_id,
                representation_id=whole_representation_id,
                unit_kind=UNIT_WHOLE_RESOURCE,
                modality=MODALITY_TEXT,
                text=_whole_text(grouped.passages),
                evidence_locator=Locator("whole_media", {}),
                ordinal=0,
                token_count=total_tokens,
                token_count_kind=TOKEN_COUNT_EXACT,
                metadata={"similarity_basis": "transcript_text"},
            )
        )
        if is_long:
            if vectors is None:
                raise ValueError("long transcript requires passage vectors for centroid aggregation")
            passage_units = units[:-1]
            if set(vectors) != {unit.unit_id for unit in passage_units}:
                raise ValueError("vectors must contain exactly one vector per indexed transcript unit")
            whole_vector = weighted_centroid(
                {unit.unit_id: vectors[unit.unit_id] for unit in passage_units},
                {unit.unit_id: unit.token_count or 1 for unit in passage_units},
                normalize=metric == "cosine",
            )

    spaces: tuple[EmbeddingSpaceRecord, ...] = ()
    vector_records: list[VectorRecord] = []
    if vectors is not None:
        expected_ids = {unit.unit_id for unit in units if unit.unit_kind == UNIT_TIME_SEGMENT}
        if input_value.whole_text_policy is not None and whole_vector is None:
            expected_ids.add(units[-1].unit_id)
        if set(vectors) != expected_ids:
            raise ValueError("vectors must contain exactly one vector per indexed transcript unit")
        dimensions = {len(vector) for vector in vectors.values()}
        if whole_vector is not None:
            dimensions.add(len(whole_vector))
        if len(dimensions) != 1:
            raise ValueError("vectors must have one shared dimension")
        dimension = dimensions.pop()
        fingerprint = input_value.embedding_fingerprint.value if input_value.embedding_fingerprint else "unspecified"
        space_id = _space_id(fingerprint, dimension, metric)
        spaces = (EmbeddingSpaceRecord(space_id, dimension, metric, fingerprint, {"modality": "text"}),)
        vector_map = dict(vectors)
        if whole_vector is not None:
            vector_map[units[-1].unit_id] = whole_vector
        vector_records = [VectorRecord(unit.unit_id, space_id, tuple(vector_map[unit.unit_id])) for unit in units]
    elif input_value.embedding_fingerprint is not None:
        raise ValueError("embedding_fingerprint requires vectors")

    return PreparedResourceBatch(
        resource=ResourceRecord(
            resource_id=resource_id,
            resource_kind=resource_kind,
            media_type=input_value.resource.media_type,
            source_namespace=input_value.resource.source_namespace,
            locator=input_value.resource.locator,
            content_hash=_content_hash(input_value),
            metadata={
                "producer_fingerprint": transcript.producer_fingerprint.value,
                "normalization_fingerprint": transcript.normalization_fingerprint.value,
                "grouper_fingerprint": grouped.grouper_fingerprint.value,
            },
        ),
        representations=tuple(representations),
        units=tuple(units),
        spaces=spaces,
        vectors=tuple(vector_records),
        facets=(),
    )


def build_audio_transcript_batch(
    input_value: TranscriptBatchBuilderInput,
    *,
    token_counter: object,
    vectors: Mapping[str, Sequence[float]] | None = None,
    metric: str = "cosine",
) -> PreparedResourceBatch:
    """Build an immutable audio transcript graph with audio seek evidence."""
    return _build_transcript_batch(
        input_value,
        token_counter=token_counter,
        vectors=vectors,
        metric=metric,
        resource_kind=RESOURCE_AUDIO,
        track=TRACK_AUDIO,
    )


def build_video_transcript_batch(
    input_value: TranscriptBatchBuilderInput,
    *,
    token_counter: object,
    vectors: Mapping[str, Sequence[float]] | None = None,
    metric: str = "cosine",
) -> PreparedResourceBatch:
    """Build an immutable video transcript graph with video seek evidence."""
    return _build_transcript_batch(
        input_value,
        token_counter=token_counter,
        vectors=vectors,
        metric=metric,
        resource_kind=RESOURCE_VIDEO,
        track=TRACK_VIDEO,
    )


__all__ = ("build_audio_transcript_batch", "build_video_transcript_batch")
