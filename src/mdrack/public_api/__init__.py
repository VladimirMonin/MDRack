"""Stable embedded Python API."""

from mdrack.application.resources import (
    DuplicateResourceItem,
    DuplicateResourceResult,
    FacetFilter,
    ResourceQueryScope,
    SimilarResourceItem,
    SimilarResourceResult,
)
from mdrack.application.retrieval import HybridRetrievalService
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
    "EmbeddingCapabilities",
    "EmbeddingProfile",
    "DuplicateResourceItem",
    "DuplicateResourceResult",
    "FacetFilter",
    "HybridRetrievalService",
    "IndexingResult",
    "MDRackEngine",
    "RetrievalCandidate",
    "RetrievalItem",
    "RetrievalResult",
    "ResourceQueryScope",
    "SimilarResourceItem",
    "SimilarResourceResult",
    "SourceLocator",
]
