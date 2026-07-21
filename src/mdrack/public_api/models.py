"""Public data models for embedded MDRack integrations."""

from dataclasses import dataclass

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
    UnifiedTextEvidence,
    UnifiedTextScopeName,
    UnifiedTextSearchItem,
    UnifiedTextSearchResult,
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


@dataclass(frozen=True)
class UnifiedTextSimilarityResult:
    """Provider-free unified similarity result with the same safe item shape as search."""

    query_resource_id: str
    scope: UnifiedTextScopeName
    results: tuple[UnifiedTextSearchItem, ...]
    degraded: bool = False
    degraded_reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "query_resource_id": self.query_resource_id,
            "scope": self.scope,
            "results": [item.to_dict() for item in self.results],
            "total_count": len(self.results),
            "degraded": self.degraded,
            "degraded_reason": self.degraded_reason,
        }

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
    "UnifiedTextEvidence",
    "UnifiedTextSearchItem",
    "UnifiedTextSearchResult",
    "UnifiedTextSimilarityResult",
    "UnifiedTextScopeName",
    "VideoCompositionResult",
]
