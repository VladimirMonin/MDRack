"""Embedded MDRack engine for host applications."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from mdrack.application.compatibility import create_application_storage, embedding_space_id
from mdrack.application.indexing import IndexingService
from mdrack.application.manifest import PreparedResourceFacade
from mdrack.application.metadata_filters import MetadataFilters, compile_metadata_filters
from mdrack.application.query import ReadService
from mdrack.application.resource_catalog import (
    MetadataCatalogService,
    MetadataFacetValue,
    MetadataInspection,
    PreparedResourceExportService,
    ResourceImportResult,
    ResourceSearchResult,
)
from mdrack.application.resources import (
    DuplicateResourceResult,
    ResourcePresetSearchResult,
    ResourceQueryScope,
    ResourceQueryService,
    SimilarResourceResult,
    TextualSimilarityResult,
)
from mdrack.application.retrieval import (
    ResourcePresetSearchService,
    ResourceSearchMode,
    ResourceSearchPresetName,
    RetrievalService,
)
from mdrack.application.transcript_ingestion import (
    TimedRetrievalMode,
    TimedRetrievalService,
    TimedSearchResult,
    TranscriptIngestionResult,
    TranscriptIngestionService,
)
from mdrack.application.video_composition import (
    VideoCompositionResult,
    VideoCompositionService,
)
from mdrack.domain.indexing import IndexingResult, SourceLocator
from mdrack.domain.retrieval import RetrievalResult
from mdrack.embeddings.runtime import embedding_profile_from_config
from mdrack.ingestion.images import (
    ImageEmbeddingSpace,
    ImageExtractor,
    ImageIngestionResult,
    ImageIngestionService,
    ImageSearchResult,
    VisualEmbeddingProvider,
)
from mdrack.ports.embeddings import EmbeddingProvider
from mdrack.ports.storage import KnowledgeStorage, ReadStorage, RetrievalStorage
from mdrack_core import (
    JSONValue,
    Locator,
    PreparedResourceBatch,
    ResourceWritePort,
    SearchScope,
)
from mdrack_media import FrameCaptionArtifact, TimedChunkingPolicy, TranscriptArtifact


class MDRackEngine:
    """Reusable Python facade that does not import Click or CLI modules."""

    def __init__(
        self,
        *,
        root: Path,
        config: Any,
        embedding_provider: EmbeddingProvider | None = None,
        profile: str = "default",
        root_id: str = "default",
        storage: KnowledgeStorage | None = None,
        search_index: RetrievalStorage | None = None,
        read_storage: ReadStorage | None = None,
        image_extractor: ImageExtractor | None = None,
        visual_embedding_provider: VisualEmbeddingProvider | None = None,
        visual_embedding_space: ImageEmbeddingSpace | None = None,
    ) -> None:
        self.root = root.resolve()
        self.config = config
        self.embedding_provider = embedding_provider
        self.profile = profile
        self.root_id = root_id
        if storage is None:
            storage = create_application_storage(self.root, config)
        self.storage = storage
        self.search_index = search_index or storage
        self.read_storage = read_storage or storage
        self.image_extractor = image_extractor
        self.visual_embedding_provider = visual_embedding_provider
        self.visual_embedding_space = visual_embedding_space
        self._images: ImageIngestionService | None = None
        self.search_service = RetrievalService(
            self.search_index,
            embedding_provider=self.embedding_provider,
            profile=self.profile,
            profile_fingerprint=(
                embedding_profile_from_config(config, self.embedding_provider, self.profile).fingerprint
                if self.embedding_provider is not None
                else None
            ),
            rrf_k=self.config.search.rrf_k,
            text_weight=self.config.search.text_weight,
            semantic_weight=self.config.search.semantic_weight,
        )

    def scan(self, *, force_reindex: bool = False) -> IndexingResult:
        service = IndexingService(
            self.root,
            self.config,
            self.storage,
            provider=self.embedding_provider,
            profile=self.profile,
            root_id=self.root_id,
        )
        return service.scan(force_reindex=force_reindex)

    def search_text(
        self,
        query: str,
        *,
        limit: int = 20,
        offset: int = 0,
        metadata_filters: MetadataFilters | None = None,
    ) -> RetrievalResult:
        return self.search_service.search_text(
            query,
            limit=limit,
            offset=offset,
            metadata_filters=metadata_filters,
        )

    async def search_semantic(
        self,
        query: str,
        *,
        limit: int = 20,
        metadata_filters: MetadataFilters | None = None,
    ) -> RetrievalResult:
        return await self.search_service.search_semantic(
            query,
            limit=limit,
            metadata_filters=metadata_filters,
        )

    async def search_hybrid(
        self,
        query: str,
        *,
        limit: int = 20,
        reranker: None = None,
        metadata_filters: MetadataFilters | None = None,
    ) -> RetrievalResult:
        return await self.search_service.search_hybrid(
            query,
            limit=limit,
            reranker=reranker,
            metadata_filters=metadata_filters,
        )

    async def ingest_transcript(
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
        """Build and atomically replace one complete timed-transcript graph."""
        return await self._transcript_ingestion_service().ingest(
            artifact,
            resource_kind=resource_kind,
            media_type=media_type,
            source_namespace=source_namespace,
            source_locator=source_locator,
            chunking_policy=chunking_policy,
            embeddings=embeddings,
        )

    async def search_transcripts(
        self,
        query: str,
        *,
        mode: TimedRetrievalMode = "hybrid",
        target: str = "unit",
        scope: SearchScope | None = None,
        limit: int = 20,
    ) -> TimedSearchResult:
        """Search transcript passages and return typed millisecond evidence."""
        return await self._timed_retrieval_service().search(
            query,
            mode=mode,
            target=target,
            scope=scope,
            limit=limit,
        )

    async def search_resource_content(
        self,
        query: str,
        *,
        preset: ResourceSearchPresetName = "balanced",
        mode: ResourceSearchMode = "hybrid",
        scope: SearchScope | None = None,
        metadata_filters: MetadataFilters | None = None,
        limit: int = 20,
    ) -> ResourcePresetSearchResult:
        """Search text-media branches with one explicit deterministic preset."""
        return await ResourcePresetSearchService(
            self._transcript_catalog(),
            embedding_provider=self.embedding_provider,
            embedding_fingerprint=self._transcript_embedding_fingerprint(),
            profile=self.profile,
            rrf_k=self.config.search.rrf_k,
        ).search(
            query,
            preset=preset,
            mode=mode,
            scope=compile_metadata_filters(
                metadata_filters or MetadataFilters(),
                base_scope=scope,
            ),
            limit=limit,
        )

    async def ingest_video(
        self,
        transcript: TranscriptArtifact,
        frame_captions: FrameCaptionArtifact,
        *,
        media_type: str,
        source_namespace: str,
        source_locator: Locator,
        source_metadata: Mapping[str, JSONValue] | None = None,
        title: str | None = None,
        chunking_policy: TimedChunkingPolicy | None = None,
        embeddings: bool = True,
    ) -> VideoCompositionResult:
        """Build and atomically replace one complete text-only video graph."""
        return await self._video_composition_service().ingest(
            transcript,
            frame_captions,
            media_type=media_type,
            source_namespace=source_namespace,
            source_locator=source_locator,
            source_metadata=source_metadata,
            title=title,
            chunking_policy=chunking_policy,
            embeddings=embeddings,
        )

    def get_resource_metadata(self, resource_id: str) -> MetadataInspection:
        return self._metadata_service().inspect(resource_id)

    def list_metadata_facets(
        self,
        *,
        namespace: str | None = None,
    ) -> tuple[MetadataFacetValue, ...]:
        return self._metadata_service().facets(namespace=namespace)

    def search_resources_text(
        self,
        query: str,
        *,
        metadata_filters: MetadataFilters | None = None,
        body_weight: float = 1.0,
        metadata_weight: float = 0.2,
        limit: int = 20,
    ) -> ResourceSearchResult:
        return self._metadata_service().search(
            query,
            metadata_filters=metadata_filters,
            body_weight=body_weight,
            metadata_weight=metadata_weight,
            limit=limit,
        )

    def import_resource_manifest(self, payload: bytes) -> ResourceImportResult:
        """Import one existing manifest-v1 payload into the active resource catalog."""
        batch = PreparedResourceFacade(
            cast(ResourceWritePort, self._transcript_catalog())
        ).import_manifest(payload)
        return ResourceImportResult(
            resource_id=batch.resource.resource_id,
            resource_kind=batch.resource.resource_kind,
            counts=self._prepared_batch_counts(batch),
        )

    def export_resource_manifest(
        self,
        resource_id: str,
        *,
        include_vectors: bool = True,
        include_text: bool = True,
        redact_source_metadata: bool = False,
    ) -> bytes:
        """Export one active resource through the existing manifest-v1 grammar."""
        return PreparedResourceExportService(self._transcript_catalog()).export_bytes(
            resource_id,
            include_vectors=include_vectors,
            include_text=include_text,
            redact_source_metadata=redact_source_metadata,
        )

    async def ingest_image(
        self,
        path: Path,
        *,
        resource_id: str,
        source_namespace: str,
        source_ref: str,
        title: str | None = None,
        media_type: str | None = None,
    ) -> ImageIngestionResult:
        return await self._image_service().ingest(
            path,
            resource_id=resource_id,
            source_namespace=source_namespace,
            source_ref=source_ref,
            title=title,
            media_type=media_type,
        )

    def delete_image(self, resource_id: str) -> None:
        self._image_service().delete(resource_id)

    def search_images_text(self, query: str, *, limit: int = 20) -> ImageSearchResult:
        return self._image_service().search_text(query, limit=limit)

    async def search_images_semantic(self, query: str, *, limit: int = 20) -> ImageSearchResult:
        return await self._image_service().search_semantic(query, limit=limit)

    async def search_images_hybrid(self, query: str, *, limit: int = 20) -> ImageSearchResult:
        return await self._image_service().search_hybrid(query, limit=limit)

    def find_resource_duplicates(
        self,
        resource_id: str,
        *,
        scope: ResourceQueryScope | None = None,
        limit: int = 20,
    ) -> DuplicateResourceResult:
        return self._resource_query_service().find_duplicates(
            resource_id,
            scope=scope,
            limit=limit,
        )

    def find_similar_resources(
        self,
        query_unit_id: str,
        space_id: str,
        *,
        scope: ResourceQueryScope | None = None,
        limit: int = 20,
        exclude_same_resource: bool = True,
    ) -> SimilarResourceResult:
        return self._resource_query_service().find_similar(
            query_unit_id,
            space_id,
            scope=scope,
            limit=limit,
            exclude_same_resource=exclude_same_resource,
        )

    def find_textually_similar_resources(
        self,
        query_unit_id: str,
        space_id: str,
        *,
        aggregation: str,
        expected_fingerprint: str,
        scope: ResourceQueryScope | None = None,
        limit: int = 20,
        exclude_same_resource: bool = True,
    ) -> TextualSimilarityResult:
        """Search explicit whole-resource vectors and label the basis as text."""
        return self._resource_query_service().find_textual_similarity(
            query_unit_id,
            space_id,
            aggregation=aggregation,
            expected_fingerprint=expected_fingerprint,
            scope=scope,
            limit=limit,
            exclude_same_resource=exclude_same_resource,
        )

    def get_file_by_path(self, relative_path: str) -> dict[str, Any] | None:
        public_reader = getattr(self.read_storage, "get_public_file_by_path", None)
        if callable(public_reader):
            result = public_reader(relative_path)
            return result if isinstance(result, dict) or result is None else None
        return ReadService(self.read_storage).get_file_by_path(relative_path)

    def get_chunk(self, logical_id: str) -> dict[str, Any] | None:
        """Read one chunk by its public logical identity."""
        reader = getattr(self.read_storage, "get_chunk_by_logical_id", None)
        if not callable(reader):
            raise NotImplementedError("read storage does not support logical chunk reads")
        result = reader(logical_id)
        return result if isinstance(result, dict) or result is None else None

    def get_chunk_source_locator(self, chunk_id: str) -> SourceLocator:
        return ReadService(self.read_storage).get_chunk_source_locator(chunk_id)

    def _image_service(self) -> ImageIngestionService:
        if self._images is not None:
            return self._images
        catalog = getattr(self.storage, "resource_store", None)
        if catalog is None:
            raise RuntimeError("active resource-core generation is required for image operations")
        text_space = None
        if self.embedding_provider is not None:
            profile = embedding_profile_from_config(
                self.config,
                self.embedding_provider,
                self.profile,
            )
            text_space = ImageEmbeddingSpace(
                embedding_space_id(profile.name, profile.fingerprint),
                profile.output_dimensions,
                profile.fingerprint,
                profile_name=profile.name,
            )
        self._images = ImageIngestionService(
            catalog,
            extractor=self.image_extractor,
            text_embedding_provider=self.embedding_provider,
            text_space=text_space,
            visual_embedding_provider=self.visual_embedding_provider,
            visual_space=self.visual_embedding_space,
            profile=self.profile,
        )
        return self._images

    def _resource_query_service(self) -> ResourceQueryService:
        catalog = getattr(self.storage, "resource_store", None)
        if catalog is None:
            raise RuntimeError("active resource-core generation is required for resource operations")
        return ResourceQueryService(catalog)

    def _metadata_service(self) -> MetadataCatalogService:
        catalog = getattr(self.storage, "resource_store", None)
        if catalog is None:
            raise RuntimeError("active resource-core generation is required for metadata operations")
        return MetadataCatalogService(catalog)

    def _transcript_catalog(self) -> object:
        catalog = getattr(self.storage, "resource_store", None)
        if catalog is None:
            raise RuntimeError(
                "active resource-core generation is required for transcript operations"
            )
        return catalog

    @staticmethod
    def _prepared_batch_counts(batch: PreparedResourceBatch) -> dict[str, int]:
        return {
            "representations": len(batch.representations),
            "units": len(batch.units),
            "spaces": len(batch.spaces),
            "vectors": len(batch.vectors),
            "facets": len(batch.facets),
        }

    def _transcript_embedding_fingerprint(self) -> str | None:
        if self.embedding_provider is None:
            return None
        return embedding_profile_from_config(
            self.config,
            self.embedding_provider,
            self.profile,
        ).fingerprint

    def _transcript_ingestion_service(self) -> TranscriptIngestionService:
        return TranscriptIngestionService(
            self._transcript_catalog(),
            embedding_provider=self.embedding_provider,
            embedding_fingerprint=self._transcript_embedding_fingerprint(),
            profile=self.profile,
        )

    def _timed_retrieval_service(self) -> TimedRetrievalService:
        return TimedRetrievalService(
            self._transcript_catalog(),
            embedding_provider=self.embedding_provider,
            embedding_fingerprint=self._transcript_embedding_fingerprint(),
            profile=self.profile,
            rrf_k=self.config.search.rrf_k,
            text_weight=self.config.search.text_weight,
            semantic_weight=self.config.search.semantic_weight,
        )

    def _video_composition_service(self) -> VideoCompositionService:
        return VideoCompositionService(
            self._transcript_catalog(),
            embedding_provider=self.embedding_provider,
            embedding_fingerprint=self._transcript_embedding_fingerprint(),
            profile=self.profile,
        )

    def close(self) -> None:
        self.storage.close()

    def __enter__(self) -> MDRackEngine:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
