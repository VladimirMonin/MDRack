from __future__ import annotations

import math

from mdrack_core.domain import (
    BranchExecutionError,
    CatalogExecutionError,
    EmbeddingSpaceRecord,
    ErrorCategory,
    LexicalBranch,
    PreparedResourceBatch,
    RankedCandidate,
    ResourceRecord,
    SearchScope,
    SearchUnitRecord,
    VectorBranch,
    VectorRecord,
)
from mdrack_core.domain.common import canonical_json


class MemoryCatalog:
    """Deterministic test-only catalog implementing the frozen catalog ports."""

    def __init__(self, *, enforce_resource_contract: bool = False) -> None:
        self._batches: dict[str, PreparedResourceBatch] = {}
        self._spaces: dict[str, EmbeddingSpaceRecord] = {}
        self._enforce_resource_contract = enforce_resource_contract
        self.replace_calls: list[PreparedResourceBatch] = []
        self.delete_calls: list[str] = []
        self._replace_failure: BaseException | None = None
        self._delete_failure: BaseException | None = None

    def inject_replace_failure(self, error: BaseException) -> None:
        self._replace_failure = error

    def inject_delete_failure(self, error: BaseException) -> None:
        self._delete_failure = error

    def replace_resource(self, batch: PreparedResourceBatch) -> None:
        self.replace_calls.append(batch)
        if self._replace_failure is not None:
            error = self._replace_failure
            self._replace_failure = None
            raise error
        try:
            resource_id = batch.resource.resource_id
            source_key = self._source_key(batch)
            current = self._batches.get(resource_id)
            if self._enforce_resource_contract and current is not None and self._source_key(current) != source_key:
                raise ValueError("resource_id is bound to another source identity")
            for other_id, other in self._batches.items():
                if (
                    self._enforce_resource_contract
                    and other_id != resource_id
                    and self._source_key(other) == source_key
                ):
                    raise ValueError("source identity is bound to another resource_id")
            spaces = dict(self._spaces)
            for space in batch.spaces:
                existing = spaces.get(space.space_id)
                if self._enforce_resource_contract and existing is not None and existing != space:
                    raise ValueError("embedding space identity mismatch")
                spaces[space.space_id] = space
            candidate = dict(self._batches)
            candidate[resource_id] = batch
            self._batches = candidate
            self._spaces = spaces
        except CatalogExecutionError:
            raise
        except Exception:
            raise CatalogExecutionError(ErrorCategory.CATALOG_ERROR) from None

    def delete_resource(self, resource_id: str) -> None:
        self.delete_calls.append(resource_id)
        candidate = dict(self._batches)
        candidate.pop(resource_id, None)
        if self._delete_failure is not None:
            error = self._delete_failure
            self._delete_failure = None
            raise error
        self._batches = candidate

    def read_resource(self, resource_id: str) -> ResourceRecord | None:
        batch = self._batches.get(resource_id)
        return None if batch is None else batch.resource

    def read_unit(self, unit_id: str) -> SearchUnitRecord | None:
        for batch in self._ordered_batches():
            for unit in batch.units:
                if unit.unit_id == unit_id:
                    return unit
        return None

    def read_vector(self, unit_id: str, space_id: str) -> VectorRecord | None:
        for batch in self._ordered_batches():
            for vector in batch.vectors:
                if vector.unit_id == unit_id and vector.space_id == space_id:
                    return vector
        return None

    def resolve_embedding_spaces(
        self,
        *,
        fingerprint: str,
        dimensions: int,
    ) -> tuple[EmbeddingSpaceRecord, ...]:
        fingerprints = {fingerprint}
        raw_digest = fingerprint.removeprefix("sha256:")
        if len(raw_digest) == 64 and all(character in "0123456789abcdef" for character in raw_digest.lower()):
            fingerprints.update({raw_digest, f"sha256:{raw_digest}"})
        return tuple(
            space
            for space in sorted(self._spaces.values(), key=lambda item: item.space_id)
            if space.fingerprint in fingerprints and space.dimensions == dimensions
        )

    def resolve_embedding_space(
        self,
        *,
        fingerprint: str,
        dimensions: int,
    ) -> EmbeddingSpaceRecord | None:
        spaces = self.resolve_embedding_spaces(
            fingerprint=fingerprint,
            dimensions=dimensions,
        )
        return spaces[0] if len(spaces) == 1 else None

    def find_by_content_hash(
        self,
        content_hash: str,
        *,
        scope: SearchScope,
    ) -> list[ResourceRecord]:
        return [
            batch.resource
            for batch in self._ordered_batches()
            if batch.resource.content_hash == content_hash
            and any(self._matches_unit_scope(batch, unit, scope) for unit in batch.units)
        ]

    def search_lexical(
        self,
        branch: LexicalBranch,
        *,
        scope: SearchScope,
    ) -> list[RankedCandidate]:
        scored: list[tuple[float, SearchUnitRecord]] = []
        query = branch.query.casefold()
        for batch in self._ordered_batches():
            for unit in batch.units:
                if not self._matches_unit_scope(batch, unit, scope):
                    continue
                score = 0.0 if unit.text is None else float(unit.text.casefold().count(query))
                if score > 0.0:
                    scored.append((score, unit))
        scored.sort(key=lambda item: (-item[0], item[1].unit_id))
        return [
            RankedCandidate(
                unit.unit_id,
                unit.resource_id,
                unit.representation_id,
                rank,
                score,
                branch.branch_id,
                unit.evidence_locator,
                unit.metadata,
            )
            for rank, (score, unit) in enumerate(scored[: branch.candidate_limit], start=1)
        ]

    def search_vector(
        self,
        branch: VectorBranch,
        *,
        scope: SearchScope,
    ) -> list[RankedCandidate]:
        scored: list[tuple[float, SearchUnitRecord]] = []
        skipped_zero_cosine = False
        for batch in self._ordered_batches():
            spaces = {space.space_id: space for space in batch.spaces}
            space = spaces.get(branch.space_id)
            if space is None:
                continue
            if len(branch.vector) != space.dimensions or (
                branch.expected_fingerprint is not None and branch.expected_fingerprint != space.fingerprint
            ):
                raise BranchExecutionError(
                    ErrorCategory.INCOMPATIBLE_VECTOR_SPACE,
                    branch_id=branch.branch_id,
                )
            if space.metric == "cosine" and math.hypot(*branch.vector) == 0.0:
                raise BranchExecutionError(
                    ErrorCategory.INCOMPATIBLE_VECTOR_SPACE,
                    branch_id=branch.branch_id,
                )
            units = {unit.unit_id: unit for unit in batch.units}
            for vector in batch.vectors:
                if vector.space_id != branch.space_id:
                    continue
                unit = units[vector.unit_id]
                if not self._matches_unit_scope(batch, unit, scope):
                    continue
                if space.metric == "cosine" and math.hypot(*vector.vector) == 0.0:
                    skipped_zero_cosine = True
                    continue
                score = self._vector_score(
                    branch.vector,
                    vector.vector,
                    space.metric,
                    branch.branch_id,
                )
                scored.append((score, unit))
        if skipped_zero_cosine and not scored:
            raise BranchExecutionError(
                ErrorCategory.INCOMPATIBLE_VECTOR_SPACE,
                branch_id=branch.branch_id,
            )
        scored.sort(key=lambda item: (-item[0], item[1].unit_id))
        return [
            RankedCandidate(
                unit.unit_id,
                unit.resource_id,
                unit.representation_id,
                rank,
                score,
                branch.branch_id,
                unit.evidence_locator,
                unit.metadata,
            )
            for rank, (score, unit) in enumerate(scored[: branch.candidate_limit], start=1)
        ]

    def batch(self, resource_id: str) -> PreparedResourceBatch | None:
        """Expose one immutable graph for adapter-contract assertions only."""
        return self._batches.get(resource_id)

    def _ordered_batches(self) -> tuple[PreparedResourceBatch, ...]:
        return tuple(self._batches[key] for key in sorted(self._batches))

    @staticmethod
    def _source_key(batch: PreparedResourceBatch) -> tuple[str, str, str]:
        resource = batch.resource
        return (
            resource.source_namespace,
            resource.locator.kind,
            canonical_json(resource.locator.payload),
        )

    @staticmethod
    def _matches_unit_scope(
        batch: PreparedResourceBatch,
        unit: SearchUnitRecord,
        scope: SearchScope,
    ) -> bool:
        resource = batch.resource
        if scope.resource_kinds and resource.resource_kind not in scope.resource_kinds:
            return False
        if scope.media_types and resource.media_type not in scope.media_types:
            return False
        if scope.source_namespaces and resource.source_namespace not in scope.source_namespaces:
            return False

        representation = next(
            item for item in batch.representations if item.representation_id == unit.representation_id
        )
        if scope.representation_kinds and representation.representation_kind not in scope.representation_kinds:
            return False
        if scope.modalities and unit.modality not in scope.modalities:
            return False
        if scope.unit_kinds and unit.unit_kind not in scope.unit_kinds:
            return False

        facets = {item.facet for item in batch.facets}
        if scope.facets_any and facets.isdisjoint(scope.facets_any):
            return False
        if scope.facets_all and not set(scope.facets_all).issubset(facets):
            return False
        if scope.facets_none and not facets.isdisjoint(scope.facets_none):
            return False
        return True

    @staticmethod
    def _vector_score(
        query: tuple[float, ...],
        candidate: tuple[float, ...],
        metric: str,
        branch_id: str,
    ) -> float:
        if metric == "dot":
            return sum(left * right for left, right in zip(query, candidate, strict=True))
        if metric == "l2":
            return -math.sqrt(sum((left - right) ** 2 for left, right in zip(query, candidate, strict=True)))
        denominator = math.sqrt(sum(value * value for value in query)) * math.sqrt(
            sum(value * value for value in candidate)
        )
        if denominator == 0.0:
            raise BranchExecutionError(
                ErrorCategory.INCOMPATIBLE_VECTOR_SPACE,
                branch_id=branch_id,
            )
        return sum(left * right for left, right in zip(query, candidate, strict=True)) / denominator
