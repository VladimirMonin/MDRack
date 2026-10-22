"""Embedded MDRack engine for host applications."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import TracebackType
from typing import Any, cast

from mdrack.adapters.sqlite.canonical_catalog import ApplicationSearchReadSessions
from mdrack.application.compatibility import create_application_storage
from mdrack.application.indexing import IndexingService
from mdrack.application.manifest import PreparedResourceFacade
from mdrack.application.media_search import (
    MediaSearchRequest,
    MediaSearchResult,
    MediaSearchService,
    ingest_native_image,
)
from mdrack.application.metadata_filters import MetadataFilters, compile_metadata_filters
from mdrack.application.query import ReadService
from mdrack.application.ready_ocr import ReadyOCROperation, ReadyOCRService
from mdrack.application.resource_catalog import (
    MetadataCatalogService,
    MetadataFacetValue,
    MetadataInspection,
    PreparedResourceExportService,
    ResourceDeleteResult,
    ResourceExportResult,
    ResourceImportResult,
    ResourceInspection,
    ResourceSearchResult,
    delete_resource,
    inspect_resource,
    project_unit,
)
from mdrack.application.resources import (
    DuplicateResourceResult,
    ResourcePresetSearchResult,
    ResourceQueryScope,
    ResourceQueryService,
    SimilarResourceResult,
    TextualSimilarityResult,
    UnifiedTextEvidence,
    UnifiedTextScopeName,
    UnifiedTextSearchItem,
    UnifiedTextSearchMode,
    UnifiedTextSearchResult,
    UnifiedTextSearchService,
)
from mdrack.application.retrieval import (
    ResourcePresetSearchService,
    ResourceSearchMode,
    ResourceSearchPresetName,
    RetrievalService,
)
from mdrack.application.source_resolution import SourceResolver
from mdrack.application.textual_embedding_space import CanonicalTextEmbeddingSpace
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
from mdrack.diagnostics.storage import StorageAnalysis, analyze_application_storage
from mdrack.domain.indexing import IndexingResult, SourceLocator
from mdrack.domain.profiles import EmbeddingProfile
from mdrack.domain.retrieval import RetrievalResult
from mdrack.embeddings.prepared import LegacyTextEmbeddingAdapter
from mdrack.embeddings.runtime import embedding_profile_from_config
from mdrack.ingestion.images import (
    ExtractedImageText,
    ImageEmbeddingSpace,
    ImageExtractor,
    ImageIngestionResult,
    ImageIngestionService,
    ImageSearchResult,
    VisualEmbeddingProvider,
)
from mdrack.ports.embeddings import EmbeddingProvider, PreparedEmbeddingProvider
from mdrack.ports.storage import KnowledgeStorage, ReadStorage, RetrievalStorage
from mdrack.public_api.models import UnifiedTextSimilarityResult
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
        prepared_embedding_provider: PreparedEmbeddingProvider | None = None,
    ) -> None:
        self.root = root.resolve()
        self.config = config
        self.embedding_provider = embedding_provider
        self.profile = profile
        self.root_id = root_id
        self._storage = storage
        self._search_index = search_index
        self._read_storage = read_storage
        # Injected legacy storage has its own capability boundary; never silently
        # substitute a different on-disk catalog for it.
        self._search_read_sessions = (
            ApplicationSearchReadSessions(self.root, config)
            if storage is None and search_index is None
            else getattr(search_index if search_index is not None else storage, "search_read_sessions", None)
        )
        self.prepared_embedding_provider = prepared_embedding_provider
        self.image_extractor = image_extractor
        self.visual_embedding_provider = visual_embedding_provider
        self.visual_embedding_space = visual_embedding_space
        self._images: ImageIngestionService | None = None
        self._search_service: RetrievalService | None = None
        self._text_embedding_profile: EmbeddingProfile | None = None

    def upgrade_catalog(self, backup_path: Path, *, maintenance: bool) -> bool:
        """Explicit maintenance; caller must stop/close every store consumer.

        Construct a fresh engine with no injected/open storage for this operation.
        No model, source resolution or automatic fallback is performed.
        """
        if any(handle is not None for handle in (self._storage, self._search_index, self._read_storage, self._images)):
            raise ValueError("maintenance_requires_closed_handles")
        from mdrack.adapters.sqlite.canonical_catalog import upgrade_application_catalog
        return upgrade_application_catalog(self.root, self.config, backup_path, maintenance=maintenance)

    def _active_embedding_profile(self) -> EmbeddingProfile | None:
        if self.embedding_provider is None:
            return None
        if self._text_embedding_profile is None:
            self._text_embedding_profile = embedding_profile_from_config(
                self.config,
                self.embedding_provider,
                self.profile,
            )
        return self._text_embedding_profile

    def _application_storage(self, *, create: bool) -> KnowledgeStorage:
        if self._storage is None:
            self._storage = create_application_storage(self.root, self.config, create=create)
        return self._storage

    @property
    def storage(self) -> KnowledgeStorage:
        """Open the fixed catalog only when an operation needs it."""
        return self._application_storage(create=False)

    @property
    def search_index(self) -> RetrievalStorage:
        if self._search_index is not None:
            return self._search_index
        return cast(RetrievalStorage, self.storage)

    @property
    def read_storage(self) -> ReadStorage:
        if self._read_storage is not None:
            return self._read_storage
        return cast(ReadStorage, self.storage)

    @property
    def search_service(self) -> RetrievalService:
        if self._search_service is not None:
            return self._search_service
        embedding_profile = self._active_embedding_profile()
        self._search_service = RetrievalService(
            cast(RetrievalStorage, None) if self._search_read_sessions is not None else self.search_index,
            read_sessions=self._search_read_sessions,
            embedding_provider=self.embedding_provider,
            profile=self.profile,
            profile_fingerprint=None if embedding_profile is None else embedding_profile.fingerprint,
            rrf_k=self.config.search.rrf_k,
            text_weight=self.config.search.text_weight,
            semantic_weight=self.config.search.semantic_weight,
        )
        return self._search_service

    def scan(self, *, force_reindex: bool = False) -> IndexingResult:
        service = IndexingService(
            self.root,
            self.config,
            self._application_storage(create=True),
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
            None if self._search_read_sessions is not None else self._transcript_catalog(),
            read_sessions=self._search_read_sessions,
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

    async def search_unified(
        self,
        query: str,
        *,
        scope: UnifiedTextScopeName = "all",
        mode: UnifiedTextSearchMode = "hybrid",
        limit: int = 20,
    ) -> UnifiedTextSearchResult:
        """Search every supported textual resource class through the active generation."""
        return await UnifiedTextSearchService(
            None if self._search_read_sessions is not None else self._transcript_catalog(),
            read_sessions=self._search_read_sessions,
            embedding_provider=self.embedding_provider,
            embedding_fingerprint=self._transcript_embedding_fingerprint(),
            profile=self.profile,
            rrf_k=self.config.search.rrf_k,
        ).search(query, scope=scope, mode=mode, limit=limit)

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
        service = (
            MetadataCatalogService(None, read_sessions=self._search_read_sessions)
            if self._search_read_sessions is not None else self._metadata_service()
        )
        return service.search(
            query,
            metadata_filters=metadata_filters,
            body_weight=body_weight,
            metadata_weight=metadata_weight,
            limit=limit,
        )

    def ready_ocr_binding(self, resource_id: str) -> dict[str, object]:
        """Return the current local source/lifecycle binding for a ready OCR file."""
        from mdrack_core.ports.publication import PublicationPort
        return ReadyOCRService(cast(PublicationPort, self._transcript_catalog())).binding(resource_id)

    def prepare_ready_ocr(
        self, resource_id: str, artifact_path: Path, *, variant: str = "default",
        lexical: bool = True, dense: bool = False, require_durable: bool = False,
        source_path: Path | None = None,
    ) -> ReadyOCROperation:
        """Capture publication authority before caller-owned artifact I/O."""
        from mdrack_core.ports.publication import PublicationPort
        return ReadyOCRService(cast(PublicationPort, self._transcript_catalog())).prepare(
            resource_id, artifact_path, variant=variant, lexical=lexical, dense=dense,
            require_durable=require_durable, source_path=source_path,
        )

    def publish_ready_ocr(self, operation: ReadyOCROperation, *, max_retries: int = 1) -> dict[str, object]:
        """Publish a captured ready result; never authorize a new lifecycle."""
        from mdrack_core.ports.publication import PublicationPort
        return ReadyOCRService(cast(PublicationPort, self._transcript_catalog())).publish(
            operation, max_retries=max_retries,
        )

    def enrich_ready_ocr(
        self, resource_id: str, artifact_path: Path, *, variant: str = "default",
        lexical: bool = True, dense: bool = False, require_durable: bool = False,
        max_retries: int = 1, source_path: Path | None = None,
    ) -> dict[str, object]:
        """Publish a bound caller-owned ready OCR file without extraction/embedding."""
        from mdrack_core.ports.publication import PublicationPort

        return ReadyOCRService(cast(PublicationPort, self._transcript_catalog())).enrich(
            resource_id, artifact_path, variant=variant, lexical=lexical, dense=dense,
            require_durable=require_durable, max_retries=max_retries, source_path=source_path,
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

    def export_resource_manifest_file(
        self,
        resource_id: str,
        output_path: str | Path,
        *,
        include_vectors: bool = True,
        include_text: bool = True,
        redact_source_metadata: bool = False,
    ) -> ResourceExportResult:
        """Atomically export one active resource through the manifest-v1 grammar."""
        return PreparedResourceExportService(self._transcript_catalog()).export_file(
            resource_id,
            output_path,
            include_vectors=include_vectors,
            include_text=include_text,
            redact_source_metadata=redact_source_metadata,
        )

    def inspect_resource(self, resource_id: str) -> ResourceInspection:
        """Return a redacted aggregate lifecycle projection for one resource."""
        return inspect_resource(self._transcript_catalog(), resource_id)

    def delete_resource(self, resource_id: str) -> ResourceDeleteResult:
        """Delete one active logical resource graph without exposing storage IDs."""
        return delete_resource(self._transcript_catalog(), resource_id)

    def _prepared_provider(self) -> PreparedEmbeddingProvider:
        if self.prepared_embedding_provider is not None:
            return self.prepared_embedding_provider
        profile = self._active_embedding_profile()
        if self.embedding_provider is None or profile is None:
            raise ValueError("embedding_provider_unavailable")
        return LegacyTextEmbeddingAdapter(self.embedding_provider, profile)

    async def ingest_native_image(
        self, path: Path | None, *, resource_id: str, source_namespace: str, source_ref: str,
        expected_revision: str | None = None, resolver: SourceResolver | None = None,
        derived_text: tuple[ExtractedImageText, ...] = (),
        derived_lexical: bool = True, derived_dense: bool = False,
    ) -> ImageIngestionResult:
        return await ingest_native_image(
            self._prepared_provider(),
            lambda: getattr(self._application_storage(create=True), "resource_store"), path,
            resource_id=resource_id, source_namespace=source_namespace, source_ref=source_ref,
            expected_revision=expected_revision, resolver=resolver,
            derived_text=derived_text, derived_lexical=derived_lexical, derived_dense=derived_dense,
        )

    async def search_media(self, request: MediaSearchRequest) -> MediaSearchResult:
        return await MediaSearchService(
            self._prepared_provider(), read_sessions=self._search_read_sessions,
            catalog=None if self._search_read_sessions is not None else self._transcript_catalog(),
        ).search(request)

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
        return self._image_service(search=True).search_text(query, limit=limit)

    async def search_images_semantic(self, query: str, *, limit: int = 20) -> ImageSearchResult:
        return await self._image_service(search=True).search_semantic(query, limit=limit)

    async def search_images_hybrid(self, query: str, *, limit: int = 20) -> ImageSearchResult:
        return await self._image_service(search=True).search_hybrid(query, limit=limit)

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

    def find_similar_resource(
        self,
        resource_id: str,
        *,
        scope: UnifiedTextScopeName = "all",
        limit: int = 20,
    ) -> UnifiedTextSimilarityResult:
        """Find provider-free textual matches by resource ID with portable evidence only."""
        if self._search_read_sessions is not None:
            with self._search_read_sessions.open() as session:
                return MDRackEngine(
                    root=self.root, config=self.config, storage=cast(KnowledgeStorage, session.storage),
                ).find_similar_resource(resource_id, scope=scope, limit=limit)
        catalog = self._transcript_catalog()
        resolver = cast(Any, self.storage) if callable(
            getattr(self.storage, "resolve_textual_whole_resource_units", None)
        ) else None
        try:
            result = ResourceQueryService(
                catalog,  # type: ignore[arg-type]
                whole_resource_resolver=resolver,
            ).find_similar_resource(resource_id, scope=scope, limit=limit)
            return UnifiedTextSimilarityResult(
                resource_id,
                scope,
                self._safe_unified_similarity_items(result, catalog),
                result.degraded,
                result.degraded_reason,
            )
        except Exception:
            return UnifiedTextSimilarityResult(resource_id, scope, (), True, "adapter_error")

    def get_file_by_path(self, relative_path: str) -> dict[str, Any] | None:
        public_reader = getattr(self.read_storage, "get_public_file_by_path", None)
        if callable(public_reader):
            result = public_reader(relative_path)
            return result if isinstance(result, dict) or result is None else None
        return ReadService(self.read_storage).get_file_by_path(relative_path)

    def get_file_outline(self, logical_id_value: str) -> dict[str, Any] | None:
        """Read canonical document headings through a file logical ID."""
        reader = getattr(self.read_storage, "get_file_outline", None)
        if not callable(reader):
            raise NotImplementedError("read storage does not support outlines")
        result = reader(logical_id_value)
        return result if isinstance(result, dict) or result is None else None

    def get_chunk(self, logical_id: str) -> dict[str, Any] | None:
        """Read one chunk by its public logical identity."""
        reader = getattr(self.read_storage, "get_chunk_by_logical_id", None)
        if not callable(reader):
            raise NotImplementedError("read storage does not support logical chunk reads")
        result = reader(logical_id)
        return result if isinstance(result, dict) or result is None else None

    def read_unit(self, unit_id: str) -> dict[str, object] | None:
        """Read any logical search unit without Markdown chunk projection."""
        if self._search_read_sessions is not None:
            with self._search_read_sessions.open() as session:
                unit = session.catalog.read_unit(unit_id)
                return None if unit is None else project_unit(unit).to_dict()
        unit = cast(Any, self._transcript_catalog()).read_unit(unit_id)
        return None if unit is None else project_unit(unit).to_dict()

    def get_chunk_source_locator(self, chunk_id: str) -> SourceLocator:
        return ReadService(self.read_storage).get_chunk_source_locator(chunk_id)

    def _image_service(self, *, search: bool = False) -> ImageIngestionService:
        if self._images is not None:
            return self._images
        scoped_search = search and self._search_read_sessions is not None
        catalog = None if scoped_search else getattr(self.storage, "resource_store", None)
        if catalog is None and not scoped_search:
            raise RuntimeError("active resource-core generation is required for image operations")
        text_space = None
        profile = self._active_embedding_profile()
        if profile is not None:
            canonical_space = CanonicalTextEmbeddingSpace(profile)
            text_space = ImageEmbeddingSpace(
                canonical_space.space_id,
                profile.output_dimensions,
                profile.fingerprint,
                profile_name=profile.name,
                vector_value_policy=profile.vector_value_policy,
            )
        service = ImageIngestionService(
            catalog,
            extractor=self.image_extractor,
            text_embedding_provider=self.embedding_provider,
            text_space=text_space,
            visual_embedding_provider=self.visual_embedding_provider,
            visual_space=self.visual_embedding_space,
            profile=self.profile,
            read_sessions=self._search_read_sessions,
            conditional_publication=isinstance(self._search_read_sessions, ApplicationSearchReadSessions),
        )
        if not scoped_search:
            self._images = service
        return service

    def _resource_query_service(self) -> ResourceQueryService:
        if self._search_read_sessions is not None:
            return ResourceQueryService(cast(Any, None), read_sessions=self._search_read_sessions)
        catalog = getattr(self.storage, "resource_store", None)
        if catalog is None:
            raise RuntimeError("active resource-core generation is required for resource operations")
        return ResourceQueryService(catalog)

    @staticmethod
    def _safe_unified_similarity_items(
        result: TextualSimilarityResult,
        catalog: object,
    ) -> tuple[UnifiedTextSearchItem, ...]:
        reader = cast(Any, catalog)
        items: list[UnifiedTextSearchItem] = []
        for item in result.results:
            resource = reader.read_resource(item.resource_id)
            if resource is None:
                continue
            evidence: list[UnifiedTextEvidence] = []
            for raw_evidence in item.evidence:
                unit = reader.read_unit(raw_evidence.unit_id)
                if unit is None:
                    continue
                evidence.append(
                    UnifiedTextEvidence(
                        raw_evidence.branch_id,
                        raw_evidence.unit_id,
                        raw_evidence.representation_id,
                        unit.unit_kind,
                        MDRackEngine._portable_evidence_locator(raw_evidence.locator),
                    )
                )
            if evidence:
                items.append(
                    UnifiedTextSearchItem(
                        item.resource_id,
                        resource.resource_kind,
                        item.score,
                        item.rank,
                        tuple(evidence),
                    )
                )
        return tuple(items)

    @staticmethod
    def _portable_evidence_locator(locator: object) -> dict[str, object]:
        if not isinstance(locator, Mapping):
            return {"kind": "opaque", "payload": {}}
        kind = locator.get("kind")
        payload = locator.get("payload")
        if not isinstance(kind, str) or not isinstance(payload, Mapping):
            return {"kind": "opaque", "payload": {}}
        safe_payload: dict[str, object] = {}
        if kind in {"document_span", "whole_resource"}:
            relative_path = payload.get("relative_path")
            if (
                isinstance(relative_path, str)
                and relative_path
                and not relative_path.startswith(("/", "\\"))
                and "\\" not in relative_path
                and ":" not in relative_path
                and all(part not in {"", ".", ".."} for part in relative_path.split("/"))
            ):
                safe_payload["relative_path"] = relative_path
            for field_name in ("start_line", "end_line", "start_char", "end_char"):
                value = payload.get(field_name)
                if type(value) is int and value >= 0:
                    safe_payload[field_name] = value
        elif kind == "time_segment":
            for field_name in ("start_ms", "end_ms"):
                value = payload.get(field_name)
                if type(value) is int and value >= 0:
                    safe_payload[field_name] = value
            track = payload.get("track")
            if track in {"audio", "video"}:
                safe_payload["track"] = track
        elif kind == "video_frame":
            timestamp = payload.get("timestamp_ms")
            if type(timestamp) is int and timestamp >= 0:
                safe_payload["timestamp_ms"] = timestamp
        elif kind not in {"whole_image", "whole_media"}:
            return {"kind": "opaque", "payload": {}}
        return {"kind": kind, "payload": safe_payload}

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
        profile = self._active_embedding_profile()
        return None if profile is None else profile.fingerprint

    def _transcript_embedding_value_policy(self) -> str | None:
        profile = self._active_embedding_profile()
        return None if profile is None else profile.vector_value_policy

    def _transcript_ingestion_service(self) -> TranscriptIngestionService:
        return TranscriptIngestionService(
            self._transcript_catalog(),
            embedding_provider=self.embedding_provider,
            embedding_profile=self._active_embedding_profile(),
            profile=self.profile,
            vector_value_policy=self._transcript_embedding_value_policy(),
        )

    def _timed_retrieval_service(self) -> TimedRetrievalService:
        return TimedRetrievalService(
            None if self._search_read_sessions is not None else self._transcript_catalog(),
            read_sessions=self._search_read_sessions,
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
            embedding_profile=self._active_embedding_profile(),
            profile=self.profile,
            vector_value_policy=self._transcript_embedding_value_policy(),
        )

    def analyze_storage(self) -> StorageAnalysis:
        """Return read-only aggregate diagnostics for the fixed application catalog."""
        return analyze_application_storage(self.root, self.config)

    def close(self) -> None:
        if self._storage is not None:
            self._storage.close()

    def __enter__(self) -> MDRackEngine:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        self.close()
