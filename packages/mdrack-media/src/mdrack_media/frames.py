"""Provider-free frame-caption projection into the core resource graph."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence

from mdrack_core import (
    MODALITY_TEXT,
    UNIT_FRAME,
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
from .builders import RESOURCE_VIDEO, FrameBatchBuilderInput
from .common import JSONValue, canonical_json
from .identifiers import representation_id, whole_resource_id
from .records import REPRESENTATION_FRAME_CAPTION, TOKEN_COUNT_ESTIMATED, FrameCaptionObservation


def _space_id(fingerprint: str, dimensions: int, metric: str) -> str:
    digest = hashlib.sha256(
        canonical_json({"dimensions": dimensions, "fingerprint": fingerprint, "metric": metric}).encode("utf-8")
    ).hexdigest()
    return f"space_{digest}"


def _content_hash(input_value: FrameBatchBuilderInput) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(input_value.frames.to_dict()).encode("utf-8")).hexdigest()


def _unit_metadata(observation: FrameCaptionObservation) -> dict[str, JSONValue]:
    assert observation.content_fingerprint is not None
    return {
        **dict(observation.metadata),
        "content_fingerprint": observation.content_fingerprint.value,
        "observation_identity": observation.observation_identity,
    }


def build_video_frame_caption_batch(
    input_value: FrameBatchBuilderInput, *, vectors: Mapping[str, Sequence[float]] | None = None, metric: str = "cosine"
) -> PreparedResourceBatch:
    """Build a deterministic text-search graph, optionally with whole-resource text."""
    if not isinstance(input_value, FrameBatchBuilderInput):
        raise TypeError("input_value must be a FrameBatchBuilderInput")
    if not isinstance(vectors, (Mapping, type(None))):
        raise TypeError("vectors must be a mapping or None")
    if metric not in {"cosine", "dot", "l2"}:
        raise ValueError("metric must be cosine, dot, or l2")
    resource = input_value.resource
    artifact = input_value.frames
    observations = artifact.observations
    representation_text = "\n\n".join(item.caption for item in observations)
    token_kinds = {item.token_count.kind for item in observations}
    representation = RepresentationRecord(
        artifact.representation_id,
        resource.resource_id,
        REPRESENTATION_FRAME_CAPTION,
        MODALITY_TEXT,
        representation_text,
        producer_fingerprint=artifact.producer_fingerprint.value,
        token_count=sum(item.token_count.count for item in observations) if observations else None,
        token_count_kind=(next(iter(token_kinds)) if len(token_kinds) == 1 else TOKEN_COUNT_ESTIMATED)
        if observations
        else None,
        metadata={**dict(artifact.metadata), "normalization_fingerprint": artifact.normalization_fingerprint.value},
    )
    units = tuple(
        SearchUnitRecord(
            observation.frame_id,
            resource.resource_id,
            artifact.representation_id,
            UNIT_FRAME,
            MODALITY_TEXT,
            observation.caption,
            Locator("video_frame", {"frame_id": observation.frame_id, "timestamp_ms": observation.timestamp_ms}),
            observation.ordinal,
            observation.token_count.count,
            observation.token_count.kind,
            _unit_metadata(observation),
        )
        for observation in observations
    )
    whole_representation = None
    whole_vector = None
    if input_value.whole_text_policy is not None:
        aggregation = input_value.aggregation_fingerprint
        assert aggregation is not None
        total_tokens = sum(item.token_count.count for item in observations)
        is_long = total_tokens > input_value.whole_text_policy.max_tokens
        if is_long and input_value.whole_text_policy.overflow == "reject":
            raise ValueError("frame captions exceed whole_text_policy.max_tokens")
        whole_representation_id = representation_id(
            resource.resource_id,
            "caption_text",
            aggregation.value,
            artifact.normalization_fingerprint.value,
        )
        whole_representation = RepresentationRecord(
            whole_representation_id,
            resource.resource_id,
            "caption_text",
            MODALITY_TEXT,
            representation_text,
            producer_fingerprint=aggregation.value,
            token_count=total_tokens,
            token_count_kind=next(iter(token_kinds)) if len(token_kinds) == 1 else TOKEN_COUNT_ESTIMATED,
            metadata={"similarity_basis": "frame_caption_text"},
        )
        whole_id = whole_resource_id(
            resource.resource_id, artifact.representation_id, aggregation.value
        )
        units = units + (
            SearchUnitRecord(
                whole_id,
                resource.resource_id,
                whole_representation_id,
                UNIT_WHOLE_RESOURCE,
                MODALITY_TEXT,
                representation_text,
                Locator("whole_media", {}),
                0,
                total_tokens,
                next(iter(token_kinds)) if len(token_kinds) == 1 else TOKEN_COUNT_ESTIMATED,
                {"similarity_basis": "frame_caption_text"},
            ),
        )
        if is_long:
            if vectors is None:
                raise ValueError("long frame captions require vectors for centroid aggregation")
            if set(vectors) != {unit.unit_id for unit in units[:-1]}:
                raise ValueError("vectors must contain exactly one vector per indexed frame")
            whole_vector = weighted_centroid(
                {unit.unit_id: vectors[unit.unit_id] for unit in units[:-1]},
                {unit.unit_id: unit.token_count or 1 for unit in units[:-1]},
                normalize=metric == "cosine",
            )
    spaces: tuple[EmbeddingSpaceRecord, ...] = ()
    vector_records: tuple[VectorRecord, ...] = ()
    if vectors is not None:
        expected_ids = {unit.unit_id for unit in units}
        if whole_vector is not None:
            expected_ids.remove(units[-1].unit_id)
        if set(vectors) != expected_ids:
            raise ValueError("vectors must contain exactly one vector per indexed frame")
        dimensions = {len(vector) for vector in vectors.values()}
        if whole_vector is not None:
            dimensions.add(len(whole_vector))
        if len(dimensions) != 1:
            raise ValueError("vectors must have one shared dimension")
        dimension = dimensions.pop()
        fingerprint = (
            input_value.embedding_fingerprint.value if input_value.embedding_fingerprint is not None else "unspecified"
        )
        space_id = _space_id(fingerprint, dimension, metric)
        spaces = (EmbeddingSpaceRecord(space_id, dimension, metric, fingerprint, {"modality": MODALITY_TEXT}),)
        vector_map = dict(vectors)
        if whole_vector is not None:
            vector_map[units[-1].unit_id] = whole_vector
        vector_records = tuple(VectorRecord(unit.unit_id, space_id, tuple(vector_map[unit.unit_id])) for unit in units)
    elif input_value.embedding_fingerprint is not None:
        raise ValueError("embedding_fingerprint requires vectors")
    return PreparedResourceBatch(
        resource=ResourceRecord(
            resource.resource_id,
            RESOURCE_VIDEO,
            resource.media_type,
            resource.source_namespace,
            resource.locator,
            _content_hash(input_value),
            metadata={
                **dict(artifact.metadata),
                "producer_fingerprint": artifact.producer_fingerprint.value,
                "normalization_fingerprint": artifact.normalization_fingerprint.value,
            },
        ),
        representations=(representation,) if whole_representation is None else (representation, whole_representation),
        units=units,
        spaces=spaces,
        vectors=vector_records,
        facets=(),
    )


__all__ = ("build_video_frame_caption_batch",)
