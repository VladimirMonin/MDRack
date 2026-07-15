"""The four model roles remain independent typed ports."""

from __future__ import annotations

from mdrack.adapters.lmstudio.fakes import (
    DeterministicReranker,
    FakeEmbeddingProvider,
    FakeModelCatalogProvider,
    FakeModelLifecycleProvider,
)
from mdrack.ports.embeddings import EmbeddingProvider
from mdrack.ports.model_catalog import ModelCatalogProvider
from mdrack.ports.model_lifecycle import ModelLifecycleProvider
from mdrack.ports.reranker import RerankerProvider


def test_model_roles_are_separate_runtime_checkable_ports() -> None:
    assert isinstance(FakeEmbeddingProvider(dimensions=4), EmbeddingProvider)
    assert isinstance(DeterministicReranker(), RerankerProvider)
    assert isinstance(FakeModelCatalogProvider(), ModelCatalogProvider)
    assert isinstance(FakeModelLifecycleProvider(), ModelLifecycleProvider)
    assert not isinstance(DeterministicReranker(), EmbeddingProvider)
