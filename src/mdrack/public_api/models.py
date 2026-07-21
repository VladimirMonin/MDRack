"""Public data models for embedded MDRack integrations."""

from mdrack.application.metadata_filters import MetadataFilter, MetadataFilters
from mdrack.application.resource_catalog import (
    MetadataFacetValue,
    MetadataInspection,
    ResourceSearchResult,
)
from mdrack.application.resources import (
    ResourcePresetEvidence,
    ResourcePresetSearchItem,
    ResourcePresetSearchResult,
    TextualSimilarityResult,
    TextualSimilarResourceItem,
)
from mdrack.application.transcript_ingestion import (
    TimedEvidence,
    TimedSearchItem,
    TimedSearchResult,
    TranscriptIngestionResult,
)
from mdrack.application.video_composition import VideoCompositionResult
from mdrack.domain.indexing import IndexingResult, SourceLocator
from mdrack.domain.profiles import EmbeddingCapabilities, EmbeddingProfile
from mdrack.domain.retrieval import RetrievalCandidate, RetrievalItem, RetrievalResult

__all__ = [
    "EmbeddingCapabilities",
    "EmbeddingProfile",
    "IndexingResult",
    "MetadataFacetValue",
    "MetadataFilter",
    "MetadataFilters",
    "MetadataInspection",
    "RetrievalCandidate",
    "RetrievalItem",
    "RetrievalResult",
    "ResourcePresetEvidence",
    "ResourcePresetSearchItem",
    "ResourcePresetSearchResult",
    "ResourceSearchResult",
    "SourceLocator",
    "TimedEvidence",
    "TimedSearchItem",
    "TimedSearchResult",
    "TextualSimilarityResult",
    "TextualSimilarResourceItem",
    "TranscriptIngestionResult",
    "VideoCompositionResult",
]
