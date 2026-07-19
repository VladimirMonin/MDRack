"""Provider-free frame-caption projection into the core resource graph."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence

from mdrack_core import (
    MODALITY_TEXT,
    UNIT_FRAME,
    EmbeddingSpaceRecord,
    PreparedResourceBatch,
    RepresentationRecord,
    ResourceRecord,
    SearchUnitRecord,
    VectorRecord,
)

from .builders import RESOURCE_VIDEO, FrameBatchBuilderInput
from .common import JSONValue, canonical_json
from .locators import VideoFrameLocator
from .records import (
    REPRESENTATION_FRAME_CAPTION,
    TOKEN_COUNT_ESTIMATED,
    FrameCaptionObservation,
)


def _space_id(fingerprint: str, dimensions: int, metric: str) -> str:
    digest = hashlib.sha256(
        canonical_json(
            {"dimensions": dimensions, "fingerprint": fingerprint, "metric": metric}
        ).encode("utf-8")
    ).hexdigest()
    return f"space_{digest}"


def _content_hash(input_value: FrameBatchBuilderInput) -> str:
    return "sha256:" + hashlib.sha256(
        canonical_json(input_value.frames.to_dict()).encode("utf-8")
    ).hexdigest()


def _unit_metadata(observation: FrameCaptionObservation) -> dict[str, JSONValue]:
    assert observation.content_fingerprint is not None
    return {
        **dict(observation.metadata),
        "content_fingerprint": observation.content_fingerprint.value,
        "observation_identity": observation.observation_identity,
    }


def build_video_frame_caption_batch(
    input_value: FrameBatchBuilderInput,
    *,
    vectors: Mapping[str, Sequence[float]] | None = None,
    metric: str = "cosine",
) -> PreparedResourceBatch:
    """Build a deterministic text-search graph from timed frame captions.

    The builder accepts only caller-prepared captions and vectors. It never reads
    frames, resolves the media locator, calls a provider, or persists data. Empty
    frame artifacts are valid and produce a resource plus representation with no
    searchable units; non-empty vector mappings must match frame IDs exactly.
    """
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
        representation_id=artifact.representation_id,
        resource_id=resource.resource_id,
        representation_kind=REPRESENTATION_FRAME_CAPTION,
        modality=MODALITY_TEXT,
        text=representation_text,
        producer_fingerprint=artifact.producer_fingerprint.value,
        token_count=sum(item.token_count.count for item in observations) if observations else None,
        token_count_kind=(
            next(iter(token_kinds)) if len(token_kinds) == 1 else TOKEN_COUNT_ESTIMATED
        ) if observations else None,
        metadata={
            **dict(artifact.metadata),
            "normalization_fingerprint": artifact.normalization_fingerprint.value,
        },
    )
    units = tuple(
        SearchUnitRecord(
            unit_id=observation.frame_id,
            resource_id=resource.resource_id,
            representation_id=artifact.representation_id,
            unit_kind=UNIT_FRAME,
            modality=MODALITY_TEXT,
            text=observation.caption,
            evidence_locator=VideoFrameLocator(
                timestamp_ms=observation.timestamp_ms,
                frame_id=observation.frame_id,
            ).to_core_locator(),
            ordinal=observation.ordinal,
            token_count=observation.token_count.count,
            token_count_kind=observation.token_count.kind,
            metadata=_unit_metadata(observation),
        )
        for observation in observations
    )

    spaces: tuple[EmbeddingSpaceRecord, ...] = ()
    vector_records: tuple[VectorRecord, ...] = ()
    if vectors is not None:
        expected_ids = {unit.unit_id for unit in units}
        if set(vectors) != expected_ids:
            raise ValueError("vectors must contain exactly one vector per indexed frame")
        if expected_ids:
            dimensions = {len(vector) for vector in vectors.values()}
            if len(dimensions) != 1:
                raise ValueError("vectors must have one shared dimension")
            dimension = dimensions.pop()
            fingerprint = (
                input_value.embedding_fingerprint.value
                if input_value.embedding_fingerprint is not None
                else "unspecified"
            )
            space_id = _space_id(fingerprint, dimension, metric)
            spaces = (
                EmbeddingSpaceRecord(
                    space_id,
                    dimension,
                    metric,
                    fingerprint,
                    {"modality": MODALITY_TEXT, "unit_kind": UNIT_FRAME},
                ),
            )
            vector_records = tuple(
                VectorRecord(unit.unit_id, space_id, tuple(vectors[unit.unit_id]))
                for unit in units
            )
    elif input_value.embedding_fingerprint is not None:
        raise ValueError("embedding_fingerprint requires vectors")

    return PreparedResourceBatch(
        resource=ResourceRecord(
            resource_id=resource.resource_id,
            resource_kind=RESOURCE_VIDEO,
            media_type=resource.media_type,
            source_namespace=resource.source_namespace,
            locator=resource.locator,
            content_hash=_content_hash(input_value),
            metadata={
                **dict(artifact.metadata),
                "producer_fingerprint": artifact.producer_fingerprint.value,
                "normalization_fingerprint": artifact.normalization_fingerprint.value,
            },
        ),
        representations=(representation,),
        units=units,
        spaces=spaces,
        vectors=vector_records,
        facets=(),
    )


__all__ = ("build_video_frame_caption_batch",)
