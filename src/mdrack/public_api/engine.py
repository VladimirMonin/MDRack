"""Embedded MDRack engine for host applications."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mdrack.application.compatibility import create_application_storage, embedding_space_id
from mdrack.application.indexing import IndexingService
from mdrack.application.query import ReadService
from mdrack.application.resources import (
    DuplicateResourceResult,
    ResourceQueryScope,
    ResourceQueryService,
    SimilarResourceResult,
)
from mdrack.application.retrieval import RetrievalService
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

    def search_text(self, query: str, *, limit: int = 20, offset: int = 0) -> RetrievalResult:
        return self.search_service.search_text(query, limit=limit, offset=offset)

    async def search_semantic(self, query: str, *, limit: int = 20) -> RetrievalResult:
        return await self.search_service.search_semantic(query, limit=limit)

    async def search_hybrid(
        self,
        query: str,
        *,
        limit: int = 20,
        reranker: None = None,
    ) -> RetrievalResult:
        return await self.search_service.search_hybrid(query, limit=limit, reranker=reranker)

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

    def close(self) -> None:
        self.storage.close()

    def __enter__(self) -> MDRackEngine:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
