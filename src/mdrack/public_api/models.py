"""Public data models for embedded MDRack integrations."""

from mdrack.domain.indexing import IndexingResult, SourceLocator
from mdrack.domain.profiles import EmbeddingCapabilities, EmbeddingProfile
from mdrack.domain.retrieval import HybridRetrievalResult, RetrievalCandidate
from mdrack.search.text import TextSearchItem, TextSearchResult

__all__ = [
    "EmbeddingCapabilities",
    "EmbeddingProfile",
    "HybridRetrievalResult",
    "IndexingResult",
    "RetrievalCandidate",
    "SourceLocator",
    "TextSearchItem",
    "TextSearchResult",
]
