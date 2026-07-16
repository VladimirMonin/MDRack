"""Stable embedded Python API."""

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
    "HybridRetrievalService",
    "IndexingResult",
    "MDRackEngine",
    "RetrievalCandidate",
    "RetrievalItem",
    "RetrievalResult",
    "SourceLocator",
]
