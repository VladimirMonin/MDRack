"""Optional reranker port; no chat-completion emulation is part of this contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class RerankDocument:
    candidate_id: str
    text: str


@dataclass(frozen=True)
class RerankScore:
    candidate_id: str
    score: float


class RerankerError(Exception):
    """A privacy-safe reranker failure."""


class RerankerUnavailable(RerankerError):
    def __init__(self, reason: str = "unsupported_by_runtime") -> None:
        self.reason = reason
        super().__init__(reason)


@runtime_checkable
class RerankerProvider(Protocol):
    async def rerank(
        self,
        query: str,
        documents: list[RerankDocument],
        *,
        top_n: int,
    ) -> list[RerankScore]: ...
