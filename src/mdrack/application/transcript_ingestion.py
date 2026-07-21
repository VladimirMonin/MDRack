"""Transcript graph preparation, atomic ingestion, and timed retrieval."""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Literal

from mdrack.ports.embeddings import EmbeddingError, EmbeddingProvider
from mdrack_core import (
    TARGET_RESOURCE,
    TARGET_UNIT,
    BranchExecutionError,
    BranchScopeOverride,
    EmbeddingSpaceRecord,
    LexicalBranch,
    Locator,
    PreparedResourceBatch,
    SearchRequest,
    SearchScope,
    VectorBranch,
)
from mdrack_core.application.indexing import CoreIndexingService
from mdrack_core.application.retrieval import RetrievalService as CoreRetrievalService
from mdrack_media import (
    EmbeddingFingerprint,
    MediaResourceDescriptor,
    TimedChunkingPolicy,
    TokenCounterFingerprint,
    TranscriptArtifact,
    TranscriptBatchBuilderInput,
    build_audio_transcript_batch,
    build_video_transcript_batch,
    group_timed_atoms,
)

logger = logging.getLogger(__name__)

TimedRetrievalMode = Literal["text", "semantic", "hybrid"]


class DeterministicWhitespaceCounter:
    """Stable application counter used by the balanced timed grouping policy."""

    fingerprint = TokenCounterFingerprint.from_payload(
        {"algorithm": "unicode-whitespace-v1", "version": 1}
    )

    def count(self, text: str) -> int:
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        return len(text.split())


@dataclass(frozen=True)
class TranscriptIngestionResult:
    resource_id: str
    resource_kind: str
    representation_count: int
    unit_count: int
    vector_count: int
    space_id: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "resource_id": self.resource_id,
            "resource_kind": self.resource_kind,
            "representation_count": self.representation_count,
            "unit_count": self.unit_count,
            "vector_count": self.vector_count,
            "space_id": self.space_id,
        }


@dataclass(frozen=True)
class TimedEvidence:
    unit_id: str
    representation_id: str
    start_ms: int
    end_ms: int
    track: str

    def to_dict(self) -> dict[str, object]:
        return {
            "unit_id": self.unit_id,
            "representation_id": self.representation_id,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "track": self.track,
            "timestamp_unit": "ms",
        }


@dataclass(frozen=True)
class TimedSearchItem:
    logical_id: str
    resource_id: str
    unit_id: str | None
    score: float
    rank: int
    evidence: tuple[TimedEvidence, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "logical_id": self.logical_id,
            "resource_id": self.resource_id,
            "unit_id": self.unit_id,
            "score": self.score,
            "rank": self.rank,
            "evidence": [item.to_dict() for item in self.evidence],
        }


@dataclass(frozen=True)
class TimedSearchResult:
    query: str
    mode: TimedRetrievalMode
    target: str
    results: tuple[TimedSearchItem, ...]
    degraded: bool = False
    degraded_reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "query": self.query,
            "mode": self.mode,
            "target": self.target,
            "results": [item.to_dict() for item in self.results],
            "total_count": len(self.results),
            "degraded": self.degraded,
            "degraded_reason": self.degraded_reason,
        }


class TranscriptIngestionService:
    """Compose existing media builders and perform one complete catalog replace."""

    def __init__(
        self,
        catalog: object,
        *,
        embedding_provider: EmbeddingProvider | None = None,
        embedding_fingerprint: str | None = None,
        profile: str = "default",
        token_counter: object | None = None,
        token_count_kind: Literal["exact", "estimated"] | None = None,
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
                _normalized_embedding_fingerprint(embedding_fingerprint)
            )
        )
        self._profile = profile
        if token_counter is None:
            if token_count_kind not in {None, "estimated"}:
                raise ValueError("the default whitespace counter is estimated")
            self._counter: object = DeterministicWhitespaceCounter()
            self._token_count_kind = "estimated"
        else:
            if token_count_kind not in {"exact", "estimated"}:
                raise ValueError(
                    "token_count_kind must be explicit for an injected token_counter"
                )
            self._counter = token_counter
            self._token_count_kind = token_count_kind
        self._indexing = CoreIndexingService(catalog)  # type: ignore[arg-type]

    def prepare(
        self,
        artifact: TranscriptArtifact,
        *,
        resource_kind: str,
        media_type: str,
        source_namespace: str,
        source_locator: Locator,
        chunking_policy: TimedChunkingPolicy | None = None,
    ) -> PreparedResourceBatch:
        """Build a provider-free lexical graph without reading or mutating a source."""
        policy = chunking_policy or TimedChunkingPolicy()
        descriptor = MediaResourceDescriptor(
            artifact.resource_id,
            resource_kind,
            media_type,
            source_namespace,
            source_locator,
        )
        grouped = group_timed_atoms(
            artifact.atoms,
            policy=policy,
            token_counter=self._counter,  # type: ignore[arg-type]
            token_count_kind=self._token_count_kind,
            resource_identifier=artifact.resource_id,
            normalization_fingerprint=artifact.normalization_fingerprint,
        )
        builder_input = TranscriptBatchBuilderInput(
            resource=descriptor,
            transcript=artifact,
            passage_representation_id=grouped.representation_id,
            passage_representation_kind="timed_passage",
            chunking_policy=policy,
            grouper_fingerprint=grouped.grouper_fingerprint,
        )
        return self._build(builder_input)

    async def ingest(
        self,
        artifact: TranscriptArtifact,
        *,
        resource_kind: str,
        media_type: str,
        source_namespace: str,
        source_locator: Locator,
        chunking_policy: TimedChunkingPolicy | None = None,
        embeddings: bool = True,
    ) -> TranscriptIngestionResult:
        """Finish all provider work, then atomically replace one complete graph."""
        lexical_batch = self.prepare(
            artifact,
            resource_kind=resource_kind,
            media_type=media_type,
            source_namespace=source_namespace,
            source_locator=source_locator,
            chunking_policy=chunking_policy,
        )
        batch = lexical_batch
        if embeddings:
            if self._provider is None or self._embedding_fingerprint is None:
                raise EmbeddingError("embedding_provider_unavailable")
            texts = [unit.text or "" for unit in lexical_batch.units]
            try:
                supplied = await self._provider.embed(texts, profile=self._profile)
            except EmbeddingError:
                raise
            except Exception:
                raise EmbeddingError("embedding_provider_error") from None
            if len(supplied) != len(lexical_batch.units):
                raise EmbeddingError("embedding_count_mismatch")
            vectors = {
                unit.unit_id: _validated_vector(vector)
                for unit, vector in zip(lexical_batch.units, supplied, strict=True)
            }
            builder_input = self._builder_input(
                artifact,
                resource_kind=resource_kind,
                media_type=media_type,
                source_namespace=source_namespace,
                source_locator=source_locator,
                chunking_policy=chunking_policy or TimedChunkingPolicy(),
                embedding_fingerprint=self._embedding_fingerprint,
            )
            batch = self._build(builder_input, vectors=vectors)

        logger.info(
            "transcript.ingest.started",
            extra={
                "representation_count": len(batch.representations),
                "unit_count": len(batch.units),
                "vector_count": len(batch.vectors),
            },
        )
        self._indexing.index(batch)
        logger.info(
            "transcript.ingest.completed",
            extra={
                "representation_count": len(batch.representations),
                "unit_count": len(batch.units),
                "vector_count": len(batch.vectors),
            },
        )
        return TranscriptIngestionResult(
            resource_id=batch.resource.resource_id,
            resource_kind=batch.resource.resource_kind,
            representation_count=len(batch.representations),
            unit_count=len(batch.units),
            vector_count=len(batch.vectors),
            space_id=batch.spaces[0].space_id if batch.spaces else None,
        )

    def _builder_input(
        self,
        artifact: TranscriptArtifact,
        *,
        resource_kind: str,
        media_type: str,
        source_namespace: str,
        source_locator: Locator,
        chunking_policy: TimedChunkingPolicy,
        embedding_fingerprint: EmbeddingFingerprint | None,
    ) -> TranscriptBatchBuilderInput:
        lexical = self.prepare(
            artifact,
            resource_kind=resource_kind,
            media_type=media_type,
            source_namespace=source_namespace,
            source_locator=source_locator,
            chunking_policy=chunking_policy,
        )
        representation = lexical.representations[0]
        descriptor = MediaResourceDescriptor(
            artifact.resource_id,
            resource_kind,
            media_type,
            source_namespace,
            source_locator,
        )
        grouped = group_timed_atoms(
            artifact.atoms,
            policy=chunking_policy,
            token_counter=self._counter,  # type: ignore[arg-type]
            token_count_kind=self._token_count_kind,
            resource_identifier=artifact.resource_id,
            normalization_fingerprint=artifact.normalization_fingerprint,
        )
        return TranscriptBatchBuilderInput(
            resource=descriptor,
            transcript=artifact,
            passage_representation_id=representation.representation_id,
            passage_representation_kind="timed_passage",
            chunking_policy=chunking_policy,
            grouper_fingerprint=grouped.grouper_fingerprint,
            embedding_fingerprint=embedding_fingerprint,
        )

    def _build(
        self,
        builder_input: TranscriptBatchBuilderInput,
        *,
        vectors: Mapping[str, Sequence[float]] | None = None,
    ) -> PreparedResourceBatch:
        builder = (
            build_audio_transcript_batch
            if builder_input.resource.resource_kind == "audio"
            else build_video_transcript_batch
        )
        return builder(
            builder_input,
            token_counter=self._counter,
            token_count_kind=self._token_count_kind,
            vectors=vectors,
        )


class TimedRetrievalService:
    """Search timed transcript passages through the frozen core search owner."""

    def __init__(
        self,
        catalog: object,
        *,
        embedding_provider: EmbeddingProvider | None = None,
        embedding_fingerprint: str | None = None,
        profile: str = "default",
        rrf_k: int = 60,
        text_weight: float = 1.0,
        semantic_weight: float = 1.0,
    ) -> None:
        if not callable(getattr(catalog, "search_lexical", None)):
            raise TypeError("catalog must support lexical search")
        if not callable(getattr(catalog, "search_vector", None)):
            raise TypeError("catalog must support vector search")
        if rrf_k < 1:
            raise ValueError("rrf_k must be positive")
        for value in (text_weight, semantic_weight):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("search weights must be finite non-negative numbers")
            if not math.isfinite(float(value)) or value < 0:
                raise ValueError("search weights must be finite non-negative numbers")
        if text_weight == 0 and semantic_weight == 0:
            raise ValueError("at least one search weight must be positive")
        self._catalog = catalog
        self._retrieval = CoreRetrievalService(catalog)  # type: ignore[arg-type]
        self._provider = embedding_provider
        self._embedding_fingerprint = (
            None
            if embedding_fingerprint is None
            else _normalized_embedding_fingerprint(embedding_fingerprint)
        )
        self._profile = profile
        self._rrf_k = rrf_k
        self._text_weight = float(text_weight)
        self._semantic_weight = float(semantic_weight)

    async def search(
        self,
        query: str,
        *,
        mode: TimedRetrievalMode = "hybrid",
        target: str = TARGET_UNIT,
        scope: SearchScope | None = None,
        limit: int = 20,
    ) -> TimedSearchResult:
        if mode not in {"text", "semantic", "hybrid"}:
            raise ValueError("mode must be text, semantic, or hybrid")
        if target not in {TARGET_UNIT, TARGET_RESOURCE}:
            raise ValueError("target must be unit or resource")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be a positive integer")
        active_scope = _timed_scope(scope or SearchScope())
        lexical: tuple[LexicalBranch, ...] = ()
        if mode in {"text", "hybrid"} and self._text_weight > 0:
            lexical = (
                LexicalBranch(
                    "transcript_text",
                    query,
                    weight=self._text_weight,
                    candidate_limit=max(limit * 10, 100),
                    scope_override=BranchScopeOverride(
                        representation_kinds=("timed_passage",),
                        unit_kinds=("time_segment",),
                    ),
                ),
            )

        vector_branches: tuple[VectorBranch, ...] = ()
        degraded_reason: str | None = None
        if mode in {"semantic", "hybrid"} and self._semantic_weight > 0:
            vector, degraded_reason = await self._query_vector(query)
            if vector is not None:
                space = _resolve_embedding_space(
                    self._catalog,
                    self._embedding_fingerprint,
                    len(vector),
                )
                if space is None:
                    degraded_reason = "incompatible_embedding_profile"
                else:
                    vector_branches = (
                        VectorBranch(
                            "transcript_semantic",
                            space.space_id,
                            vector,
                            weight=self._semantic_weight,
                            candidate_limit=max(limit * 10, 100),
                            expected_fingerprint=self._embedding_fingerprint,
                            scope_override=BranchScopeOverride(
                                representation_kinds=("timed_passage",),
                                unit_kinds=("time_segment",),
                            ),
                        ),
                    )
        if not lexical and not vector_branches:
            return TimedSearchResult(
                query,
                mode,
                target,
                (),
                degraded=True,
                degraded_reason=degraded_reason or "branch_unavailable",
            )

        try:
            result = self._retrieval.search(
                SearchRequest(
                    lexical_branches=lexical,
                    vector_branches=vector_branches,
                    scope=active_scope,
                    target=target,
                    limit=limit,
                    rrf_k=self._rrf_k,
                    allow_partial=True,
                )
            )
        except BranchExecutionError as error:
            if not lexical:
                return TimedSearchResult(
                    query,
                    mode,
                    target,
                    (),
                    degraded=True,
                    degraded_reason=error.category.value,
                )
            result = self._retrieval.search(
                SearchRequest(
                    lexical_branches=lexical,
                    vector_branches=(),
                    scope=active_scope,
                    target=target,
                    limit=limit,
                    rrf_k=self._rrf_k,
                )
            )
            degraded_reason = error.category.value
        if result.degradations and degraded_reason is None:
            degraded_reason = result.degradations[0].category.value
        items = tuple(
            TimedSearchItem(
                item.logical_id,
                item.resource_id,
                item.unit_id,
                item.score,
                item.rank,
                tuple(_timed_evidence(candidate) for candidate in item.evidence),
            )
            for item in result.items
        )
        return TimedSearchResult(
            query,
            mode,
            target,
            items,
            degraded=degraded_reason is not None,
            degraded_reason=degraded_reason,
        )

    async def _query_vector(
        self, query: str
    ) -> tuple[tuple[float, ...] | None, str | None]:
        if self._provider is None or self._embedding_fingerprint is None:
            return None, "embedding_provider_unavailable"
        try:
            return _validated_vector(
                await self._provider.embed_query(query, profile=self._profile)
            ), None
        except EmbeddingError:
            return None, "embedding_provider_error"
        except Exception:
            return None, "semantic_search_error"


def _validated_vector(value: Sequence[float]) -> tuple[float, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise EmbeddingError("invalid_embedding_vector")
    vector = tuple(float(item) for item in value)
    if not vector or any(not math.isfinite(item) for item in vector):
        raise EmbeddingError("invalid_embedding_vector")
    return vector


def _normalized_embedding_fingerprint(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("embedding_fingerprint must be non-empty")
    return value if value.startswith("sha256:") else f"sha256:{value}"


def _timed_scope(scope: SearchScope) -> SearchScope:
    unit_kinds = _intersect(scope.unit_kinds, ("time_segment",))
    representation_kinds = _intersect(
        scope.representation_kinds,
        ("timed_passage",),
    )
    resource_kinds = _intersect(scope.resource_kinds, ("audio", "video"))
    return replace(
        scope,
        resource_kinds=resource_kinds,
        representation_kinds=representation_kinds,
        unit_kinds=unit_kinds,
    )


def _intersect(current: tuple[str, ...], required: tuple[str, ...]) -> tuple[str, ...]:
    if not current:
        return required
    allowed = set(required)
    intersection = tuple(item for item in current if item in allowed)
    return intersection or ("__mdrack_no_match__",)


def _resolve_embedding_space(
    catalog: object,
    fingerprint: str | None,
    dimensions: int,
) -> EmbeddingSpaceRecord | None:
    resolver = getattr(catalog, "resolve_embedding_space", None)
    if not callable(resolver) or fingerprint is None:
        return None
    resolved = resolver(fingerprint=fingerprint, dimensions=dimensions)
    if not isinstance(resolved, EmbeddingSpaceRecord):
        return None
    if resolved.fingerprint != fingerprint or resolved.dimensions != dimensions:
        return None
    return resolved


def _timed_evidence(candidate: object) -> TimedEvidence:
    locator = getattr(candidate, "evidence_locator", None)
    payload = getattr(locator, "payload", None)
    if getattr(locator, "kind", None) != "time_segment" or not isinstance(
        payload, Mapping
    ):
        raise ValueError("timed result is missing a time_segment locator")
    start_ms = payload.get("start_ms")
    end_ms = payload.get("end_ms")
    track = payload.get("track")
    if type(start_ms) is not int or type(end_ms) is not int or end_ms <= start_ms:
        raise ValueError("timed result has an invalid interval")
    if track not in {"audio", "video"}:
        raise ValueError("timed result has an invalid track")
    unit_id = getattr(candidate, "unit_id", None)
    representation_id = getattr(candidate, "representation_id", None)
    if not isinstance(unit_id, str) or not isinstance(representation_id, str):
        raise ValueError("timed result has invalid logical identities")
    return TimedEvidence(unit_id, representation_id, start_ms, end_ms, track)


__all__ = [
    "DeterministicWhitespaceCounter",
    "TimedEvidence",
    "TimedRetrievalMode",
    "TimedRetrievalService",
    "TimedSearchItem",
    "TimedSearchResult",
    "TranscriptIngestionResult",
    "TranscriptIngestionService",
]
