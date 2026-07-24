"""Standard-library exact vector search over canonical binary SQLite payloads."""

from __future__ import annotations

import math
import sqlite3
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from mdrack_core.domain import BranchExecutionError, EmbeddingSpaceRecord, ErrorCategory, VectorBranch, VectorRecord
from mdrack_sqlite.vector_backends import (
    SQLiteVectorCapabilities,
    SQLiteVectorGenerationContext,
    SQLiteVectorSchemaExtension,
)
from mdrack_sqlite.vector_codecs import VectorCodecRegistry, codec_id_from_metadata, decode_vector_payload


@dataclass(frozen=True)
class BuiltinExactSearchCounters:
    """Privacy-safe work counters for one builtin exact vector search."""

    candidate_rows: int
    decoded_vectors: int
    skipped_vectors: int
    decode_cpu_seconds: float
    score_seconds: float
    sort_seconds: float


@dataclass(frozen=True)
class BuiltinExactSearchResult:
    """Sorted pre-materialization rows and counters from one exact search."""

    scored_rows: tuple[tuple[float, sqlite3.Row], ...]
    counters: BuiltinExactSearchCounters
    all_candidates_zero_cosine: bool


class BuiltinExactVectorBackend:
    """The mandatory stdlib exact search path for binary float32 and float64 vectors."""

    backend_id = "builtin-exact-v1"

    def __init__(self, codec_registry: VectorCodecRegistry | None = None) -> None:
        if codec_registry is not None and not isinstance(codec_registry, VectorCodecRegistry):
            raise TypeError("codec_registry must be VectorCodecRegistry")
        self._codec_registry = codec_registry or VectorCodecRegistry.default()
        self._last_search_counters: BuiltinExactSearchCounters | None = None

    @property
    def last_search_counters(self) -> BuiltinExactSearchCounters | None:
        """Return counters from the latest successful search without source data."""
        return self._last_search_counters

    def capabilities(self) -> SQLiteVectorCapabilities:
        """Describe the mandatory extensionless exact baseline."""
        return SQLiteVectorCapabilities(
            backend_id=self.backend_id,
            exact=True,
            metrics=frozenset({"cosine", "dot", "l2"}),
            supports_facets_any=True,
            supports_facets_all=True,
            supports_facets_none=True,
            supported_scope_fields=frozenset(
                {
                    "resource_kinds",
                    "media_types",
                    "source_namespaces",
                    "representation_kinds",
                    "modalities",
                    "unit_kinds",
                }
            ),
            supports_extensionless_open=True,
            supports_atomic_replace=True,
            supports_atomic_delete=True,
        )

    def schema_extension(self) -> SQLiteVectorSchemaExtension | None:
        """Builtin search owns no plugin schema objects."""
        return None

    def initialize(self, connection: sqlite3.Connection, *, generation: SQLiteVectorGenerationContext) -> None:
        """Keep initialization explicit while the builtin strategy remains schema-free."""
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("connection must be sqlite3.Connection")
        del generation

    def replace_vectors(
        self,
        connection: sqlite3.Connection,
        *,
        resource_id: str,
        spaces: Sequence[EmbeddingSpaceRecord],
        vectors: Sequence[VectorRecord],
    ) -> None:
        """Canonical vector rows are already written by the catalog transaction owner."""
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("connection must be sqlite3.Connection")
        del resource_id, spaces, vectors

    def delete_vectors(self, connection: sqlite3.Connection, *, resource_id: str) -> None:
        """Builtin search has no derived rows to delete."""
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("connection must be sqlite3.Connection")
        del resource_id

    def verify(self, connection: sqlite3.Connection, *, generation: SQLiteVectorGenerationContext) -> None:
        """Builtin verification is covered by canonical catalog checks."""
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("connection must be sqlite3.Connection")
        del generation

    def search(
        self,
        connection: sqlite3.Connection,
        *,
        branch: VectorBranch,
        dimensions: int,
        metric: str,
        metadata: Mapping[str, object],
        clauses: Sequence[str],
        params: Sequence[object],
    ) -> BuiltinExactSearchResult:
        """Filter in SQLite, decode binary payloads, score, and deterministically limit."""
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("connection must be sqlite3.Connection")
        if not isinstance(branch, VectorBranch):
            raise TypeError("branch must be VectorBranch")
        if type(dimensions) is not int or dimensions < 1:
            raise ValueError("dimensions must be a positive integer")
        if metric not in {"cosine", "dot", "l2"}:
            raise ValueError("metric must be cosine, dot, or l2")

        codec = self._codec_registry.get(codec_id_from_metadata(metadata))
        query = tuple(branch.vector)
        codec.encode(query, dimensions=dimensions)
        if metric == "cosine" and self._norm(query) == 0.0:
            raise BranchExecutionError(ErrorCategory.INCOMPATIBLE_VECTOR_SPACE, branch_id=branch.branch_id)

        where = " AND ".join(clauses)
        rows = connection.execute(
            "SELECT u.*, p.resource_id AS representation_resource_id, "
            "p.modality AS representation_modality, e.embedding "
            "FROM core_unit_embeddings e "
            "JOIN core_search_units u ON u.unit_id = e.unit_id "
            "JOIN core_representations p ON p.representation_id = u.representation_id "
            "JOIN core_resources r ON r.resource_id = u.resource_id "
            f"WHERE e.space_id = ?{' AND ' if where else ''}{where}",
            (branch.space_id, *params),
        ).fetchall()

        decoded: list[tuple[tuple[float, ...], sqlite3.Row]] = []
        decoded_vectors = 0
        skipped_vectors = 0
        decode_started_cpu = time.process_time()
        for row in rows:
            candidate = decode_vector_payload(
                row["embedding"],
                dimensions=dimensions,
                metadata=metadata,
                registry=self._codec_registry,
            )
            decoded_vectors += 1
            if metric == "cosine" and self._norm(candidate) == 0.0:
                skipped_vectors += 1
                continue
            decoded.append((candidate, row))
        decode_cpu_seconds = time.process_time() - decode_started_cpu

        scored: list[tuple[float, sqlite3.Row]] = []
        score_started = time.process_time()
        for candidate, row in decoded:
            scored.append((self._score(query, candidate, metric, branch.branch_id), row))
        score_seconds = time.process_time() - score_started

        sort_started = time.process_time()
        scored.sort(key=lambda item: (-item[0], item[1]["unit_id"]))
        sort_seconds = time.process_time() - sort_started
        counters = BuiltinExactSearchCounters(
            candidate_rows=len(rows),
            decoded_vectors=decoded_vectors,
            skipped_vectors=skipped_vectors,
            decode_cpu_seconds=decode_cpu_seconds,
            score_seconds=score_seconds,
            sort_seconds=sort_seconds,
        )
        self._last_search_counters = counters
        return BuiltinExactSearchResult(
            scored_rows=tuple(scored[: branch.candidate_limit]),
            counters=counters,
            all_candidates_zero_cosine=bool(skipped_vectors and not scored),
        )

    @staticmethod
    def _norm(vector: Sequence[float]) -> float:
        return math.hypot(*vector)

    @classmethod
    def _score(
        cls,
        query: Sequence[float],
        candidate: Sequence[float],
        metric: str,
        branch_id: str,
    ) -> float:
        if metric == "dot":
            return sum(left * right for left, right in zip(query, candidate, strict=True))
        if metric == "l2":
            return -math.sqrt(sum((left - right) ** 2 for left, right in zip(query, candidate, strict=True)))
        if metric == "cosine":
            denominator = cls._norm(query) * cls._norm(candidate)
            if denominator == 0.0:
                raise BranchExecutionError(ErrorCategory.INCOMPATIBLE_VECTOR_SPACE, branch_id=branch_id)
            return sum(left * right for left, right in zip(query, candidate, strict=True)) / denominator
        raise BranchExecutionError(ErrorCategory.INCOMPATIBLE_VECTOR_SPACE, branch_id=branch_id)


__all__ = ["BuiltinExactSearchCounters", "BuiltinExactSearchResult", "BuiltinExactVectorBackend"]
