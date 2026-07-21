"""Complete video graph composition over provider-neutral media artifacts."""

from __future__ import annotations

import hashlib
import logging
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from mdrack.application.transcript_ingestion import DeterministicWhitespaceCounter
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
    build_video_frame_caption_batch,
    build_video_transcript_batch,
    canonical_json,
    group_timed_atoms,
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
        profile: str = "default",
    ) -> None:
        if not callable(getattr(catalog, "replace_resource", None)):
            raise TypeError("catalog must support complete resource replacement")
        if (embedding_provider is None) != (embedding_fingerprint is None):
            raise ValueError(
                "embedding_provider and embedding_fingerprint must be supplied together"
            )
        self._catalog = catalog
        self._provider = embedding_provider
        self._embedding_fingerprint = (
            None
            if embedding_fingerprint is None
            else EmbeddingFingerprint.from_dict(
                embedding_fingerprint
                if embedding_fingerprint.startswith("sha256:")
                else f"sha256:{embedding_fingerprint}"
            )
        )
        self._profile = profile
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
        )
        transcript_input = TranscriptBatchBuilderInput(
            resource=descriptor,
            transcript=transcript,
            passage_representation_id=grouped.representation_id,
            passage_representation_kind=REPRESENTATION_TIMED_PASSAGE,
            chunking_policy=policy,
            grouper_fingerprint=grouped.grouper_fingerprint,
            embedding_fingerprint=None,
        )
        lexical_transcript = build_video_transcript_batch(
            transcript_input,
            token_counter=self._counter,
            token_count_kind="estimated",
        )
        transcript_ids = {unit.unit_id for unit in lexical_transcript.units}
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
            )
        )

        frame_batch = None
        if frames.observations:
            frame_input = FrameBatchBuilderInput(
                resource=descriptor,
                frames=frames,
                embedding_fingerprint=self._embedding_fingerprint if vectors is not None else None,
            )
            frame_batch = build_video_frame_caption_batch(
                frame_input,
                vectors=(
                    None
                    if vectors is None
                    else {unit_id: vectors[unit_id] for unit_id in frame_ids}
                ),
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
        content_hash = "sha256:" + hashlib.sha256(
            canonical_json(
                {
                    "frames": frames.to_dict(),
                    "source": source,
                    "transcript": transcript.to_dict(),
                }
            ).encode("utf-8")
        ).hexdigest()
        batches = (transcript_batch,) if frame_batch is None else (transcript_batch, frame_batch)
        spaces_by_id: dict[str, EmbeddingSpaceRecord] = {}
        for batch in batches:
            for space in batch.spaces:
                previous = spaces_by_id.setdefault(space.space_id, space)
                if previous != space:
                    raise ValueError("video branches produced incompatible embedding spaces")
        return PreparedResourceBatch(
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
            representations=tuple(
                representation for batch in batches for representation in batch.representations
            ),
            units=tuple(unit for batch in batches for unit in batch.units),
            spaces=tuple(spaces_by_id.values()),
            vectors=tuple(vector for batch in batches for vector in batch.vectors),
            facets=(),
        )

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
            texts = [unit.text or "" for unit in lexical.units]
            try:
                supplied = await self._provider.embed(texts, profile=self._profile)
            except EmbeddingError:
                raise
            except Exception:
                raise EmbeddingError("embedding_provider_error") from None
            if len(supplied) != len(lexical.units):
                raise EmbeddingError("embedding_count_mismatch")
            vectors = {
                unit.unit_id: _validated_vector(vector)
                for unit, vector in zip(lexical.units, supplied, strict=True)
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


def _validated_vector(value: Sequence[float]) -> tuple[float, ...]:
    if isinstance(value, (str, bytes, bytearray)):
        raise EmbeddingError("invalid_embedding_vector")
    vector = tuple(float(item) for item in value)
    if not vector or any(not math.isfinite(item) for item in vector):
        raise EmbeddingError("invalid_embedding_vector")
    return vector


__all__ = ["VideoCompositionResult", "VideoCompositionService"]
