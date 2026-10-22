"""Stable embedded Python API."""

from mdrack.application.durable_artifacts import DurableArtifactStore
from mdrack.application.media_search import MediaSearchRequest, MediaSearchResult, StoredUnitInput
from mdrack.application.prepared_enrichment import PreparedEnrichmentOperation
from mdrack.application.ready_ocr import ReadyOCRError, ReadyOCROperation
from mdrack.application.reindexing import PreparedReindex
from mdrack.application.resources import (
    DuplicateResourceItem,
    DuplicateResourceResult,
    FacetFilter,
    ResourceQueryScope,
    SimilarResourceItem,
    SimilarResourceResult,
)
from mdrack.application.retrieval import HybridRetrievalService
from mdrack.application.source_resolution import SourceResolver
from mdrack.domain.embedding_inputs import (
    EmbeddingResult,
    EncodingRole,
    ImageInput,
    InputCapability,
    RouteCapabilities,
    TextInput,
)
from mdrack.embeddings.prepared import LegacyTextEmbeddingAdapter
from mdrack.ingestion.images import (
    ExtractedImageText,
    ImageEmbeddingSpace,
    ImageExtractor,
    ImageIngestionResult,
    ImageSearchItem,
    ImageSearchResult,
    StaticImageExtractor,
    VisualEmbeddingProvider,
)
from mdrack.ports.embeddings import PreparedEmbeddingProvider
from mdrack.public_api.engine import MDRackEngine
from mdrack.public_api.models import (
    EmbeddingCapabilities,
    EmbeddingProfile,
    IndexingResult,
    RetrievalCandidate,
    RetrievalItem,
    RetrievalResult,
    SourceLocator,
)

__all__ = [
    "ReadyOCRError", "ReadyOCROperation",
    "MediaSearchRequest", "MediaSearchResult", "StoredUnitInput", "SourceResolver",
    "EmbeddingResult", "EncodingRole", "ImageInput", "InputCapability",
    "RouteCapabilities", "TextInput", "LegacyTextEmbeddingAdapter", "PreparedEmbeddingProvider",
    "EmbeddingCapabilities",
    "EmbeddingProfile",
    "DuplicateResourceItem",
    "DuplicateResourceResult",
    "FacetFilter",
    "HybridRetrievalService",
    "ExtractedImageText",
    "ImageEmbeddingSpace",
    "ImageExtractor",
    "ImageIngestionResult",
    "ImageSearchItem",
    "ImageSearchResult",
    "IndexingResult",
    "MDRackEngine",
    "RetrievalCandidate",
    "RetrievalItem",
    "RetrievalResult",
    "ResourceQueryScope",
    "SimilarResourceItem",
    "SimilarResourceResult",
    "SourceLocator",
    "StaticImageExtractor",
    "VisualEmbeddingProvider",
    "DurableArtifactStore", "PreparedEnrichmentOperation", "PreparedReindex",
]
