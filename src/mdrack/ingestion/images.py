"""Explicit local image ingestion and image-scoped retrieval orchestration."""

from __future__ import annotations

import hashlib
import logging
import math
import mimetypes
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from mdrack.domain.identifiers import logical_id
from mdrack.ports.embeddings import EmbeddingError, EmbeddingProvider
from mdrack_core.application.indexing import CoreIndexingService
from mdrack_core.application.retrieval import RetrievalService as CoreRetrievalService
from mdrack_core.domain import (
    METRIC_COSINE,
    MODALITY_IMAGE,
    MODALITY_TEXT,
    REPRESENTATION_CAPTION_TEXT,
    REPRESENTATION_OCR_TEXT,
    REPRESENTATION_VISUAL,
    RESOURCE_IMAGE,
    TARGET_RESOURCE,
    UNIT_WHOLE_RESOURCE,
    EmbeddingSpaceRecord,
    LexicalBranch,
    Locator,
    PreparedResourceBatch,
    RepresentationRecord,
    ResourceRecord,
    SearchRequest,
    SearchResult,
    SearchScope,
    SearchUnitRecord,
    VectorBranch,
    VectorRecord,
)

logger = logging.getLogger(__name__)

_SUPPORTED_MEDIA_TYPES = frozenset({"image/gif", "image/jpeg", "image/png", "image/webp"})
_TEXT_REPRESENTATION_KINDS = frozenset({REPRESENTATION_CAPTION_TEXT, REPRESENTATION_OCR_TEXT})


@dataclass(frozen=True)
class ExtractedImageText:
    """One complete, bounded OCR or caption output prepared outside the core."""

    kind: str
    text: str
    producer_fingerprint: str
    language: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in _TEXT_REPRESENTATION_KINDS:
            raise ValueError("image text kind must be ocr_text or caption_text")
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("image text must be non-empty")
        if not isinstance(self.producer_fingerprint, str) or not self.producer_fingerprint:
            raise ValueError("producer_fingerprint must be non-empty")


@dataclass(frozen=True)
class ImageEmbeddingSpace:
    """App-owned identity for one already configured embedding space."""

    space_id: str
    dimensions: int
    fingerprint: str
    metric: str = METRIC_COSINE
    profile_name: str | None = None

    def __post_init__(self) -> None:
        EmbeddingSpaceRecord(
            self.space_id,
            self.dimensions,
            self.metric,
            self.fingerprint,
        )

    def core_record(self, *, modality: str) -> EmbeddingSpaceRecord:
        metadata = (
            {"profile": self.profile_name}
            if modality == MODALITY_TEXT and self.profile_name is not None
            else {"modality": modality}
        )
        return EmbeddingSpaceRecord(
            self.space_id,
            self.dimensions,
            self.metric,
            self.fingerprint,
            metadata,
        )


@runtime_checkable
class ImageExtractor(Protocol):
    async def extract(self, content: bytes, *, media_type: str) -> Sequence[ExtractedImageText]: ...


@runtime_checkable
class VisualEmbeddingProvider(Protocol):
    async def embed_image(self, content: bytes, *, profile: str = "default") -> Sequence[float]: ...


class StaticImageExtractor:
    """Deterministic caller-supplied extraction used by explicit offline workflows."""

    def __init__(self, outputs: Sequence[ExtractedImageText]) -> None:
        self._outputs = tuple(outputs)

    async def extract(self, content: bytes, *, media_type: str) -> Sequence[ExtractedImageText]:
        del content, media_type
        return self._outputs


@dataclass(frozen=True)
class ImageIngestionResult:
    resource_id: str
    content_hash: str
    media_type: str
    byte_size: int
    representation_ids: tuple[str, ...]
    unit_ids: tuple[str, ...]
    text_space_id: str | None
    visual_space_id: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "resource_id": self.resource_id,
            "content_hash": self.content_hash,
            "media_type": self.media_type,
            "byte_size": self.byte_size,
            "representation_ids": list(self.representation_ids),
            "unit_ids": list(self.unit_ids),
            "text_space_id": self.text_space_id,
            "visual_space_id": self.visual_space_id,
        }


@dataclass(frozen=True)
class ImageSearchItem:
    resource_id: str
    score: float
    rank: int
    source_ref: str
    evidence: tuple[Mapping[str, object], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "resource_id": self.resource_id,
            "score": self.score,
            "rank": self.rank,
            "source_ref": self.source_ref,
            "evidence": [dict(item) for item in self.evidence],
        }


@dataclass(frozen=True)
class ImageSearchResult:
    mode: Literal["text", "semantic", "hybrid"]
    results: tuple[ImageSearchItem, ...]
    degraded: bool = False
    degraded_reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "results": [item.to_dict() for item in self.results],
            "total_count": len(self.results),
            "degraded": self.degraded,
            "degraded_reason": self.degraded_reason,
        }


class ImageIngestionService:
    """Prepare image graphs outside core and persist/search them through frozen ports."""

    def __init__(
        self,
        catalog: object,
        *,
        extractor: ImageExtractor | None = None,
        text_embedding_provider: EmbeddingProvider | None = None,
        text_space: ImageEmbeddingSpace | None = None,
        visual_embedding_provider: VisualEmbeddingProvider | None = None,
        visual_space: ImageEmbeddingSpace | None = None,
        profile: str = "default",
        max_image_bytes: int = 32 * 1024 * 1024,
        max_text_tokens: int = 8_000,
    ) -> None:
        if max_image_bytes < 1 or max_text_tokens < 1:
            raise ValueError("image and text limits must be positive")
        if (text_embedding_provider is None) != (text_space is None):
            raise ValueError("text provider and text space must be supplied together")
        if (visual_embedding_provider is None) != (visual_space is None):
            raise ValueError("visual provider and visual space must be supplied together")
        if text_space is not None and visual_space is not None and text_space.space_id == visual_space.space_id:
            raise ValueError("text and visual embedding spaces must be distinct")
        self._catalog = catalog
        self._indexing = CoreIndexingService(catalog)  # type: ignore[arg-type]
        self._retrieval = CoreRetrievalService(catalog)  # type: ignore[arg-type]
        self._extractor = extractor
        self._text_provider = text_embedding_provider
        self._text_space = text_space
        self._visual_provider = visual_embedding_provider
        self._visual_space = visual_space
        self._profile = profile
        self._max_image_bytes = max_image_bytes
        self._max_text_tokens = max_text_tokens

    async def ingest(
        self,
        path: Path,
        *,
        resource_id: str,
        source_namespace: str,
        source_ref: str,
        title: str | None = None,
        media_type: str | None = None,
    ) -> ImageIngestionResult:
        """Read one bounded local image and atomically replace its complete graph."""
        self._require_non_empty(resource_id, "resource_id")
        self._require_non_empty(source_namespace, "source_namespace")
        self._require_non_empty(source_ref, "source_ref")
        resolved_media_type = self._resolve_media_type(path, media_type)
        content = self._read_bounded(path)
        content_hash = "sha256:" + hashlib.sha256(content).hexdigest()
        extracted = await self._extract(content, resolved_media_type)
        token_counts = tuple(self._estimated_tokens(item.text) for item in extracted)
        if any(count > self._max_text_tokens for count in token_counts):
            raise ValueError("image text exceeds the configured whole-resource limit")

        representations: list[RepresentationRecord] = []
        units: list[SearchUnitRecord] = []
        spaces: dict[str, EmbeddingSpaceRecord] = {}
        vectors: list[VectorRecord] = []

        text_vectors: Sequence[Sequence[float]] = ()
        if extracted and self._text_provider is not None:
            try:
                text_vectors = await self._text_provider.embed(
                    [item.text for item in extracted],
                    profile=self._profile,
                )
            except Exception:
                logger.warning(
                    "image.ingest.provider_failed",
                    extra={"reason": "embedding_provider_error"},
                )
                raise EmbeddingError("embedding_provider_error") from None
            if len(text_vectors) != len(extracted):
                raise ValueError("text embedding count mismatch")
            assert self._text_space is not None
            spaces[self._text_space.space_id] = self._text_space.core_record(modality=MODALITY_TEXT)

        for index, (item, token_count) in enumerate(zip(extracted, token_counts, strict=True)):
            representation_id = logical_id(
                "image-representation",
                resource_id,
                item.kind,
                item.producer_fingerprint,
                index,
            )
            unit_id = logical_id("image-unit", representation_id, UNIT_WHOLE_RESOURCE)
            representations.append(
                RepresentationRecord(
                    representation_id,
                    resource_id,
                    item.kind,
                    MODALITY_TEXT,
                    item.text,
                    item.language,
                    item.producer_fingerprint,
                    token_count,
                    "estimated",
                    {},
                )
            )
            units.append(
                SearchUnitRecord(
                    unit_id,
                    resource_id,
                    representation_id,
                    UNIT_WHOLE_RESOURCE,
                    MODALITY_TEXT,
                    item.text,
                    Locator("whole_image", {"source_ref": source_ref}),
                    0,
                    token_count,
                    "estimated",
                    {"representation_kind": item.kind},
                )
            )
            if text_vectors:
                assert self._text_space is not None
                vector = self._validated_vector(text_vectors[index], self._text_space.dimensions)
                vectors.append(VectorRecord(unit_id, self._text_space.space_id, vector))

        if self._visual_provider is not None:
            assert self._visual_space is not None
            representation_id = logical_id(
                "image-representation",
                resource_id,
                REPRESENTATION_VISUAL,
                self._visual_space.fingerprint,
            )
            unit_id = logical_id("image-unit", representation_id, UNIT_WHOLE_RESOURCE)
            try:
                visual = await self._visual_provider.embed_image(content, profile=self._profile)
            except Exception:
                logger.warning(
                    "image.ingest.provider_failed",
                    extra={"reason": "visual_embedding_provider_error"},
                )
                raise EmbeddingError("visual_embedding_provider_error") from None
            vector = self._validated_vector(visual, self._visual_space.dimensions)
            representations.append(
                RepresentationRecord(
                    representation_id,
                    resource_id,
                    REPRESENTATION_VISUAL,
                    MODALITY_IMAGE,
                    None,
                    producer_fingerprint=self._visual_space.fingerprint,
                )
            )
            units.append(
                SearchUnitRecord(
                    unit_id,
                    resource_id,
                    representation_id,
                    UNIT_WHOLE_RESOURCE,
                    MODALITY_IMAGE,
                    None,
                    Locator("whole_image", {"source_ref": source_ref}),
                    0,
                    metadata={"representation_kind": REPRESENTATION_VISUAL},
                )
            )
            spaces[self._visual_space.space_id] = self._visual_space.core_record(modality=MODALITY_IMAGE)
            vectors.append(VectorRecord(unit_id, self._visual_space.space_id, vector))

        if not representations:
            raise ValueError("image ingestion requires text extraction or a visual vector")

        batch = PreparedResourceBatch(
            ResourceRecord(
                resource_id,
                RESOURCE_IMAGE,
                resolved_media_type,
                source_namespace,
                Locator("local_image", {"source_ref": source_ref}),
                content_hash,
                title,
                {"byte_size": len(content)},
            ),
            tuple(representations),
            tuple(units),
            tuple(spaces.values()),
            tuple(vectors),
            (),
        )
        logger.info(
            "image.ingest.started",
            extra={
                "representation_count": len(representations),
                "unit_count": len(units),
                "vector_count": len(vectors),
                "byte_size": len(content),
            },
        )
        self._indexing.index(batch)
        logger.info(
            "image.ingest.completed",
            extra={
                "representation_count": len(representations),
                "unit_count": len(units),
                "vector_count": len(vectors),
                "byte_size": len(content),
            },
        )
        return ImageIngestionResult(
            resource_id,
            content_hash,
            resolved_media_type,
            len(content),
            tuple(item.representation_id for item in representations),
            tuple(item.unit_id for item in units),
            self._text_space.space_id if text_vectors and self._text_space is not None else None,
            (
                self._visual_space.space_id
                if self._visual_provider is not None and self._visual_space is not None
                else None
            ),
        )

    def delete(self, resource_id: str) -> None:
        self._indexing.delete(resource_id)

    def search_text(self, query: str, *, limit: int = 20) -> ImageSearchResult:
        request = self._request(
            query=query,
            vector=None,
            mode="text",
            limit=limit,
        )
        return self._map_search("text", self._retrieval.search(request))

    async def search_semantic(self, query: str, *, limit: int = 20) -> ImageSearchResult:
        vector, reason = await self._query_vector(query)
        if vector is None:
            return ImageSearchResult("semantic", (), True, reason)
        request = self._request(query=None, vector=vector, mode="semantic", limit=limit)
        return self._map_search("semantic", self._retrieval.search(request))

    async def search_hybrid(self, query: str, *, limit: int = 20) -> ImageSearchResult:
        vector, reason = await self._query_vector(query)
        request = self._request(query=query, vector=vector, mode="hybrid", limit=limit)
        result = self._map_search("hybrid", self._retrieval.search(request))
        if reason is None:
            return result
        return ImageSearchResult("hybrid", result.results, True, reason)

    async def _extract(self, content: bytes, media_type: str) -> tuple[ExtractedImageText, ...]:
        if self._extractor is None:
            return ()
        try:
            outputs = await self._extractor.extract(content, media_type=media_type)
        except Exception:
            logger.warning(
                "image.ingest.extraction_failed",
                extra={"reason": "image_extraction_error"},
            )
            raise ValueError("image_extraction_error") from None
        if not isinstance(outputs, (list, tuple)):
            raise ValueError("image extractor must return an ordered sequence")
        if any(not isinstance(item, ExtractedImageText) for item in outputs):
            raise ValueError("image extractor returned an invalid value")
        return tuple(outputs)

    async def _query_vector(self, query: str) -> tuple[tuple[float, ...] | None, str | None]:
        if self._text_provider is None or self._text_space is None:
            return None, "embedding_provider_unavailable"
        try:
            vector = await self._text_provider.embed_query(query, profile=self._profile)
            return self._validated_vector(vector, self._text_space.dimensions), None
        except EmbeddingError:
            logger.warning("image.search.degraded", extra={"reason": "embedding_provider_error"})
            return None, "embedding_provider_error"
        except Exception:
            logger.warning("image.search.degraded", extra={"reason": "semantic_search_error"})
            return None, "semantic_search_error"

    def _request(
        self,
        *,
        query: str | None,
        vector: tuple[float, ...] | None,
        mode: Literal["text", "semantic", "hybrid"],
        limit: int,
    ) -> SearchRequest:
        if limit < 1:
            raise ValueError("limit must be positive")
        candidate_limit = max(1, limit * 2)
        lexical = () if query is None else (LexicalBranch("text", query, candidate_limit=candidate_limit),)
        vector_branches: tuple[VectorBranch, ...] = ()
        if vector is not None:
            assert self._text_space is not None
            vector_branches = (
                VectorBranch("semantic", self._text_space.space_id, vector, candidate_limit=candidate_limit),
            )
        if mode == "semantic" and not vector_branches:
            raise ValueError("semantic search requires a query vector")
        return SearchRequest(
            lexical,
            vector_branches,
            SearchScope(resource_kinds=(RESOURCE_IMAGE,)),
            TARGET_RESOURCE,
            limit,
            allow_partial=False,
        )

    @staticmethod
    def _map_search(
        mode: Literal["text", "semantic", "hybrid"],
        result: SearchResult,
    ) -> ImageSearchResult:
        items: list[ImageSearchItem] = []
        for item in result.items:
            evidence = tuple(
                {
                    "branch": candidate.branch_id,
                    "rank": candidate.rank,
                    "score": candidate.raw_score,
                    "unit_id": candidate.unit_id,
                    "representation_id": candidate.representation_id,
                    "representation_kind": candidate.metadata.get("representation_kind"),
                }
                for candidate in item.evidence
            )
            representative = item.evidence[0]
            source_ref = representative.evidence_locator.payload.get("source_ref")
            if not isinstance(source_ref, str) or not source_ref:
                raise ValueError("image result source_ref is invalid")
            score = item.score if mode == "hybrid" else representative.raw_score
            items.append(ImageSearchItem(item.resource_id, score, item.rank, source_ref, evidence))
        return ImageSearchResult(mode, tuple(items))

    def _read_bounded(self, path: Path) -> bytes:
        if not isinstance(path, Path) or not path.is_file():
            raise ValueError("image source must be a local file")
        try:
            with path.open("rb") as handle:
                content = handle.read(self._max_image_bytes + 1)
        except OSError:
            raise ValueError("image source is unavailable") from None
        if len(content) > self._max_image_bytes:
            raise ValueError("image exceeds the configured byte limit")
        if not content:
            raise ValueError("image source must not be empty")
        return content

    @staticmethod
    def _resolve_media_type(path: Path, requested: str | None) -> str:
        media_type = requested or mimetypes.guess_type(path.name)[0]
        if media_type not in _SUPPORTED_MEDIA_TYPES:
            raise ValueError("unsupported image media type")
        return media_type

    @staticmethod
    def _validated_vector(vector: Sequence[float], dimensions: int) -> tuple[float, ...]:
        if not isinstance(vector, (list, tuple)):
            raise ValueError("embedding provider must return an ordered vector")
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            for value in vector
        ):
            raise ValueError("embedding vector must contain finite numbers")
        frozen = tuple(float(value) for value in vector)
        if len(frozen) != dimensions:
            raise ValueError("embedding vector dimension mismatch")
        EmbeddingSpaceRecord("validation", dimensions, METRIC_COSINE, "validation")
        VectorRecord("validation", "validation", frozen)
        return frozen

    @staticmethod
    def _estimated_tokens(text: str) -> int:
        return len(text.split())

    @staticmethod
    def _require_non_empty(value: object, field_name: str) -> str:
        if not isinstance(value, str) or not value:
            raise ValueError(f"{field_name} must be non-empty")
        return value


__all__ = [
    "ExtractedImageText",
    "ImageEmbeddingSpace",
    "ImageExtractor",
    "ImageIngestionResult",
    "ImageIngestionService",
    "ImageSearchItem",
    "ImageSearchResult",
    "StaticImageExtractor",
    "VisualEmbeddingProvider",
]
