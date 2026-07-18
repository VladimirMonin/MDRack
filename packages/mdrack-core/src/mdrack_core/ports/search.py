"""Search ports receiving scope before adapter candidate limits."""

from __future__ import annotations

from typing import Protocol

from ..domain.search import LexicalBranch, RankedCandidate, SearchScope, VectorBranch


class LexicalSearchPort(Protocol):
    def search_lexical(
        self,
        branch: LexicalBranch,
        *,
        scope: SearchScope,
    ) -> list[RankedCandidate]: ...


class VectorSearchPort(Protocol):
    def search_vector(
        self,
        branch: VectorBranch,
        *,
        scope: SearchScope,
    ) -> list[RankedCandidate]: ...


class SearchPort(LexicalSearchPort, VectorSearchPort, Protocol):
    """Complete shared candidate-search surface."""
