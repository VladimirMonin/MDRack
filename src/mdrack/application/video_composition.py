"""Complete video graph composition over provider-neutral media artifacts."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from mdrack.application.retrieval import validate_embedding_vector
from mdrack.application.textual_embedding_space import CanonicalTextEmbeddingSpace
from mdrack.application.transcript_ingestion import (
    DeterministicWhitespaceCounter,
    _aggregation_fingerprint,
    _persist_whole_aggregation,
    _whole_text_aggregation,
)
from mdrack.application.vector_values import apply_vector_value_policy, validate_vector_value_policy
from mdrack.domain.profiles import EmbeddingProfile
from mdrack.ingestion.frame_captions import validate_frame_caption_artifact
from mdrack.ports.embeddings import EmbeddingError, EmbeddingProvider
from mdrack_core import (
    EmbeddingSpaceRecord,
    JSONValue,
    Locator,
    PreparedResourceBatch,
    ResourceRecord,
)
from mdrack_core.application.indexing import CoreIndexingService
from mdrack_media import (
    REPRESENTATION_TIMED_PASSAGE,
    EmbeddingFingerprint,
    FrameBatchBuilderInput,
    FrameCaptionArtifact,
    MediaResourceDescriptor,
    TimedChunkingPolicy,
    TranscriptArtifact,
    TranscriptBatchBuilderInput,
    WholeResourceTextPolicy,
    build_video_frame_caption_batch,
    build_video_transcript_batch,
    canonical_json,
    group_timed_atoms,
    weighted_centroid,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VideoCompositionResult:
    resource_id: str
    representation_count: int
    transcript_unit_count: int
    frame_unit_count: int
    vector_count: int
    space_id: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "resource_id": self.resource_id,
            "representation_count": self.representation_count,
            "transcript_unit_count": self.transcript_unit_count,
            "frame_unit_count": self.frame_unit_count,
            "unit_count": self.transcript_unit_count + self.frame_unit_count,
            "vector_count": self.vector_count,
            "space_id": self.space_id,
        }


class VideoCompositionService:
    """Build transcript and frame text as one graph and replace it once."""

    def __init__(
        self,
        catalog: object,
        *,
        embedding_provider: EmbeddingProvider | None = None,
        embedding_fingerprint: str | None = None,
        embedding_profile: EmbeddingProfile | None = None,
        profile: str = "default",
        vector_value_policy: str | None = None,
    ) -> None:
        if not callable(getattr(catalog, "replace_resource", None)):
            raise TypeError("catalog must support complete resource replacement")
        if embedding_profile is not None and embedding_fingerprint is not None:
            raise ValueError("embedding_profile and embedding_fingerprint cannot be combined")
        if (embedding_provider is None) != (embedding_fingerprint is None) and embedding_profile is None:
            raise ValueError("embedding_provider and embedding_fingerprint must be supplied together")
        if embedding_profile is not None and embedding_provider is None:
            raise ValueError("embedding_provider and embedding_profile must be supplied together")
        self._catalog = catalog
        self._provider = embedding_provider
        self._text_space = (
            None if embedding_profile is None else CanonicalTextEmbeddingSpace(embedding_profile)
        )
        self._embedding_fingerprint = (
            self._text_space.media_fingerprint
            if self._text_space is not None
            else None
            if embedding_fingerprint is None
            else EmbeddingFingerprint.from_dict(
                embedding_fingerprint
                if embedding_fingerprint.startswith("sha256:")
                else f"sha256:{embedding_fingerprint}"
            )
        )
        self._profile = profile if self._text_space is None else self._text_space.profile.name
        profile_policy = None if self._text_space is None else self._text_space.profile.vector_value_policy
        if vector_value_policy is not None and profile_policy not in {None, vector_value_policy}:
            raise ValueError("vector_value_policy must match embedding_profile")
        self._vector_value_policy = validate_vector_value_policy(profile_policy or vector_value_policy)
        self._counter = DeterministicWhitespaceCounter()
        self._indexing = CoreIndexingService(catalog)  # type: ignore[arg-type]

    def prepare(
        self,
        transcript: TranscriptArtifact,
        frames: FrameCaptionArtifact,
        *,
        media_type: str,
        source_namespace: str,
        source_locator: Locator,
        source_metadata: Mapping[str, JSONValue] | None = None,
        title: str | None = None,
        chunking_policy: TimedChunkingPolicy | None = None,
        vectors: Mapping[str, Sequence[float]] | None = None,
    ) -> PreparedResourceBatch:
        """Prepare one complete, text-only video graph without source I/O."""
        validate_frame_caption_artifact(frames)
        if transcript.resource_id != frames.resource_id:
            raise ValueError("transcript and frames must belong to the same resource")
        policy = chunking_policy or TimedChunkingPolicy()
        descriptor = MediaResourceDescriptor(
            transcript.resource_id,
            "video",
            media_type,
            source_namespace,
            source_locator,
        )
        grouped = group_timed_atoms(
            transcript.atoms,
            policy=policy,
            token_counter=self._counter,
            token_count_kind="estimated",
            resource_identifier=transcript.resource_id,
            normalization_fingerprint=transcript.normalization_fingerprint,
            unsplittable="flag",
        )
        whole_text_policy = WholeResourceTextPolicy(overflow="caller_split")
        frame_token_count = sum(self._counter.count(observation.caption) for observation in frames.observations)
        aggregation = _whole_text_aggregation(
            sum(passage.token_count.count for passage in grouped.passages) + frame_token_count,
            whole_text_policy,
        )
        include_whole = aggregation == "direct_text_v1" or vectors is not None
        transcript_input = TranscriptBatchBuilderInput(
            resource=descriptor,
            transcript=transcript,
            passage_representation_id=grouped.representation_id,
            passage_representation_kind=REPRESENTATION_TIMED_PASSAGE,
            chunking_policy=policy,
            grouper_fingerprint=grouped.grouper_fingerprint,
            embedding_fingerprint=None,
            whole_text_policy=whole_text_policy if include_whole else None,
            aggregation_fingerprint=(
                _aggregation_fingerprint(aggregation, whole_text_policy) if include_whole else None
            ),
        )
        lexical_transcript_input = (
            replace(
                transcript_input,
                whole_text_policy=None,
                aggregation_fingerprint=None,
            )
            if vectors is not None and aggregation != "direct_text_v1"
            else transcript_input
        )
        lexical_transcript = build_video_transcript_batch(
            lexical_transcript_input,
            token_counter=self._counter,
            token_count_kind="estimated",
            unsplittable="flag",
        )
        transcript_ids = {
            unit.unit_id
            for unit in lexical_transcript.units
            if aggregation == "direct_text_v1" or unit.unit_kind != "whole_resource"
        }
        frame_ids = {item.frame_id for item in frames.observations}
        expected_ids = transcript_ids | frame_ids
        if vectors is not None and set(vectors) != expected_ids:
            raise ValueError("vectors must contain exactly one vector per video search unit")
        transcript_batch = (
            lexical_transcript
            if vectors is None
            else build_video_transcript_batch(
                replace(
                    transcript_input,
                    embedding_fingerprint=self._embedding_fingerprint,
                ),
                token_counter=self._counter,
                token_count_kind="estimated",
                vectors={unit_id: vectors[unit_id] for unit_id in transcript_ids},
                unsplittable="flag",
            )
        )
        if include_whole:
            transcript_batch = _persist_whole_aggregation(transcript_batch, aggregation)

        frame_batch = None
        if frames.observations:
            frame_input = FrameBatchBuilderInput(
                resource=descriptor,
                frames=frames,
                embedding_fingerprint=self._embedding_fingerprint if vectors is not None else None,
            )
            frame_batch = build_video_frame_caption_batch(
                frame_input,
                vectors=(None if vectors is None else {unit_id: vectors[unit_id] for unit_id in frame_ids}),
            )

        source = dict(source_metadata or {})
        metadata: Mapping[str, JSONValue] = {
            "source": source,
            "ingestion": {
                "adapter": "video_composer",
                "adapter_version": 1,
                "transcript_producer_fingerprint": transcript.producer_fingerprint.value,
                "frame_producer_fingerprint": frames.producer_fingerprint.value,
                "grouper_fingerprint": grouped.grouper_fingerprint.value,
            },
            "derived": {
                "transcript_unit_count": len(transcript_batch.units),
                "frame_unit_count": len(frames.observations),
            },
        }
        content_hash = (
            "sha256:"
            + hashlib.sha256(
                canonical_json(
                    {
                        "frames": frames.to_dict(),
                        "source": source,
                        "transcript": transcript.to_dict(),
                    }
                ).encode("utf-8")
            ).hexdigest()
        )
        batches = (transcript_batch,) if frame_batch is None else (transcript_batch, frame_batch)
        spaces_by_id: dict[str, EmbeddingSpaceRecord] = {}
        for batch in batches:
            for space in batch.spaces:
                previous = spaces_by_id.setdefault(space.space_id, space)
                if previous != space:
                    raise ValueError("video branches produced incompatible embedding spaces")
        batch = PreparedResourceBatch(
            resource=ResourceRecord(
                resource_id=descriptor.resource_id,
                resource_kind="video",
                media_type=descriptor.media_type,
                source_namespace=descriptor.source_namespace,
                locator=descriptor.locator,
                content_hash=content_hash,
                title=title,
                metadata=metadata,
            ),
            representations=tuple(representation for batch in batches for representation in batch.representations),
            units=tuple(unit for batch in batches for unit in batch.units),
            spaces=tuple(spaces_by_id.values()),
            vectors=tuple(vector for batch in batches for vector in batch.vectors),
            facets=(),
        )
        if include_whole:
            batch = _aggregate_video_whole_resource(batch, aggregation)
        if self._text_space is not None:
            batch = self._text_space.rekey_batch(batch)
        return apply_vector_value_policy(batch, self._vector_value_policy)

    async def ingest(
        self,
        transcript: TranscriptArtifact,
        frames: FrameCaptionArtifact,
        *,
        media_type: str,
        source_namespace: str,
        source_locator: Locator,
        source_metadata: Mapping[str, JSONValue] | None = None,
        title: str | None = None,
        chunking_policy: TimedChunkingPolicy | None = None,
        embeddings: bool = True,
    ) -> VideoCompositionResult:
        lexical = self.prepare(
            transcript,
            frames,
            media_type=media_type,
            source_namespace=source_namespace,
            source_locator=source_locator,
            source_metadata=source_metadata,
            title=title,
            chunking_policy=chunking_policy,
        )
        batch = lexical
        if embeddings:
            if self._provider is None or self._embedding_fingerprint is None:
                raise EmbeddingError("embedding_provider_unavailable")
            whole_unit = next(
                (unit for unit in lexical.units if unit.unit_kind == "whole_resource"),
                None,
            )
            aggregation = (
                str(whole_unit.metadata["aggregation"])
                if whole_unit is not None
                else _whole_text_aggregation(
                    sum(unit.token_count or 0 for unit in lexical.units if unit.unit_kind == "time_segment"),
                    WholeResourceTextPolicy(overflow="caller_split"),
                )
            )
            embedding_units = (
                lexical.units
                if aggregation == "direct_text_v1"
                else tuple(unit for unit in lexical.units if unit.unit_kind != "whole_resource")
            )
            texts = [unit.text or "" for unit in embedding_units]
            try:
                supplied = await self._provider.embed(texts, profile=self._profile)
            except EmbeddingError:
                raise
            except Exception:
                raise EmbeddingError("embedding_provider_error") from None
            if len(supplied) != len(embedding_units):
                raise EmbeddingError("embedding_count_mismatch")
            vectors = {
                unit.unit_id: validate_embedding_vector(vector)
                for unit, vector in zip(embedding_units, supplied, strict=True)
            }
            batch = self.prepare(
                transcript,
                frames,
                media_type=media_type,
                source_namespace=source_namespace,
                source_locator=source_locator,
                source_metadata=source_metadata,
                title=title,
                chunking_policy=chunking_policy,
                vectors=vectors,
            )

        transcript_units = sum(unit.unit_kind == "time_segment" for unit in batch.units)
        frame_units = sum(unit.unit_kind == "frame" for unit in batch.units)
        logger.info(
            "video.compose.started",
            extra={
                "representation_count": len(batch.representations),
                "transcript_unit_count": transcript_units,
                "frame_unit_count": frame_units,
                "vector_count": len(batch.vectors),
            },
        )
        self._indexing.index(batch)
        logger.info(
            "video.compose.completed",
            extra={
                "representation_count": len(batch.representations),
                "transcript_unit_count": transcript_units,
                "frame_unit_count": frame_units,
                "vector_count": len(batch.vectors),
            },
        )
        return VideoCompositionResult(
            resource_id=batch.resource.resource_id,
            representation_count=len(batch.representations),
            transcript_unit_count=transcript_units,
            frame_unit_count=frame_units,
            vector_count=len(batch.vectors),
            space_id=batch.spaces[0].space_id if batch.spaces else None,
        )


def _aggregate_video_whole_resource(
    batch: PreparedResourceBatch,
    aggregation: str,
) -> PreparedResourceBatch:
    """Make the video-level textual unit cover transcript passages and frame captions."""
    whole_units = tuple(unit for unit in batch.units if unit.unit_kind == "whole_resource")
    if len(whole_units) != 1:
        raise ValueError("video batch must contain exactly one whole_resource unit")
    whole_unit = whole_units[0]
    textual_units = tuple(unit for unit in batch.units if unit.unit_id != whole_unit.unit_id)
    whole_text = "\n\n".join(unit.text or "" for unit in textual_units if unit.text)
    token_count = sum(
        unit.token_count if unit.token_count is not None else len((unit.text or "").split())
        for unit in textual_units
    )
    metadata = {
        **dict(whole_unit.metadata),
        "aggregation": aggregation,
        "similarity_basis": "textual_content",
    }
    whole_unit = replace(
        whole_unit,
        text=whole_text,
        token_count=token_count,
        metadata=metadata,
    )
    representations = tuple(
        replace(
            representation,
            text=whole_text,
            token_count=token_count,
            metadata={**dict(representation.metadata), **metadata},
        )
        if representation.representation_id == whole_unit.representation_id
        else representation
        for representation in batch.representations
    )
    vectors = batch.vectors
    if aggregation == "token_weighted_centroid_v1" and vectors:
        component_vectors = {
            vector.unit_id: vector.vector
            for vector in vectors
            if vector.unit_id != whole_unit.unit_id
        }
        weights = {
            unit.unit_id: (
                unit.token_count if unit.token_count is not None else len((unit.text or "").split())
            )
            for unit in textual_units
        }
        if set(component_vectors) != set(weights):
            raise ValueError("video whole-resource centroid requires every textual component vector")
        centroid = weighted_centroid(component_vectors, weights)
        vectors = tuple(
            replace(vector, vector=centroid) if vector.unit_id == whole_unit.unit_id else vector
            for vector in vectors
        )
    return replace(
        batch,
        representations=representations,
        units=tuple(
            whole_unit if unit.unit_id == whole_unit.unit_id else unit
            for unit in batch.units
        ),
        vectors=vectors,
    )


__all__ = ["VideoCompositionResult", "VideoCompositionService"]
