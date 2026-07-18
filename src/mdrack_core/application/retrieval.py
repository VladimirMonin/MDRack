"""Provider- and persistence-neutral multi-branch retrieval orchestration."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from ..domain.errors import (
    BranchExecutionError,
    CatalogExecutionError,
    DegradationCategory,
    ErrorCategory,
)
from ..domain.resources import UNIT_WHOLE_RESOURCE, ResourceRecord
from ..domain.results import (
    Degradation,
    SearchResult,
    SearchResultItem,
    SimilarityRequest,
    SimilarityResult,
)
from ..domain.search import (
    TARGET_RESOURCE,
    LexicalBranch,
    RankedCandidate,
    SearchRequest,
    SearchScope,
    VectorBranch,
)
from ..observability import (
    LifecycleStatus,
    SafeEvent,
    emit_event,
    safe_fingerprint,
)
from ..ports.catalog import ResourceReadPort
from ..ports.search import SearchPort, VectorSearchPort
from .fusion import FusionBranch, FusionCandidate, weighted_rrf

_ERROR_TO_DEGRADATION = {
    ErrorCategory.BRANCH_UNAVAILABLE: DegradationCategory.BRANCH_UNAVAILABLE,
    ErrorCategory.INCOMPATIBLE_VECTOR_SPACE: DegradationCategory.INCOMPATIBLE_VECTOR_SPACE,
    ErrorCategory.ADAPTER_TIMEOUT: DegradationCategory.ADAPTER_TIMEOUT,
    ErrorCategory.ADAPTER_ERROR: DegradationCategory.ADAPTER_ERROR,
}

_CATALOG_ERROR_TO_DEGRADATION = {
    ErrorCategory.CATALOG_ERROR: DegradationCategory.ADAPTER_ERROR,
    ErrorCategory.ADAPTER_TIMEOUT: DegradationCategory.ADAPTER_TIMEOUT,
}


@dataclass(frozen=True)
class _ExecutedBranch:
    branch_id: str
    weight: float
    candidates: tuple[RankedCandidate, ...]


class ResourceDiscoveryService:
    """Provider-free exact-duplicate and whole-resource similarity queries."""

    _SIMILARITY_BRANCH = "similarity"

    def __init__(
        self,
        catalog: ResourceReadPort,
        search_port: VectorSearchPort | None = None,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self._catalog = catalog
        self._search_port = search_port or cast(VectorSearchPort, catalog)
        self._logger = logger or logging.getLogger(__name__)

    def find_duplicates(
        self,
        resource_id: str,
        *,
        scope: SearchScope,
        limit: int,
    ) -> tuple[ResourceRecord, ...]:
        """Return other resources with the same byte hash in logical-ID order."""
        if not isinstance(resource_id, str) or not resource_id.strip():
            raise ValueError("resource_id must be a non-empty string")
        if not isinstance(scope, SearchScope):
            raise ValueError("scope must be a SearchScope")
        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be a positive integer")
        try:
            resource = self._catalog.read_resource(resource_id)
            if resource is None or resource.content_hash is None:
                return ()
            matches = self._catalog.find_by_content_hash(resource.content_hash, scope=scope)
        except CatalogExecutionError:
            raise
        except TimeoutError:
            raise CatalogExecutionError(ErrorCategory.ADAPTER_TIMEOUT) from None
        except Exception:
            raise CatalogExecutionError(ErrorCategory.CATALOG_ERROR) from None
        return tuple(
            sorted(
                (item for item in matches if item.resource_id != resource_id),
                key=lambda item: item.resource_id,
            )[:limit]
        )

    def similar(self, request: SimilarityRequest) -> SimilarityResult:
        """Search from an existing whole-resource vector without provider calls."""
        if not isinstance(request, SimilarityRequest):
            raise ValueError("request must be a SimilarityRequest")
        started = time.perf_counter()
        self._emit_similarity(
            "core.similarity.started",
            request,
            status=LifecycleStatus.STARTED,
            requested_limit=request.limit,
            scope_filter_count=self._scope_filter_count(request.scope),
        )
        try:
            unit = self._catalog.read_unit(request.query_unit_id)
            if unit is None or unit.unit_kind != UNIT_WHOLE_RESOURCE:
                return self._unavailable(request, started)
            vector = self._catalog.read_vector(request.query_unit_id, request.space_id)
            if vector is None:
                return self._unavailable(request, started)
        except CatalogExecutionError as error:
            return self._completed(
                request,
                (),
                (
                    Degradation(
                        self._SIMILARITY_BRANCH,
                        _CATALOG_ERROR_TO_DEGRADATION[error.category],
                    ),
                ),
                started,
            )
        except TimeoutError:
            return self._completed(
                request,
                (),
                (Degradation(self._SIMILARITY_BRANCH, DegradationCategory.ADAPTER_TIMEOUT),),
                started,
            )
        except Exception:
            return self._completed(
                request,
                (),
                (Degradation(self._SIMILARITY_BRANCH, DegradationCategory.ADAPTER_ERROR),),
                started,
            )

        scope = self._whole_resource_scope(request.scope)
        if scope is None:
            return self._completed(request, (), (), started)
        candidate_limit = request.limit + (1 if request.exclude_same_resource else 0)
        selected: list[RankedCandidate] = []
        try:
            while True:
                branch = VectorBranch(
                    self._SIMILARITY_BRANCH,
                    request.space_id,
                    vector.vector,
                    candidate_limit=candidate_limit,
                )
                raw_candidates = self._search_port.search_vector(branch, scope=scope)
                candidates = RetrievalService._validate_candidates(branch, raw_candidates)
                selected = [
                    candidate
                    for candidate in candidates
                    if not request.exclude_same_resource
                    or candidate.resource_id != unit.resource_id
                ][: request.limit]
                if len(selected) == request.limit or len(candidates) < candidate_limit:
                    break
                candidate_limit *= 2
        except BranchExecutionError as error:
            return self._completed(
                request,
                (),
                (Degradation(self._SIMILARITY_BRANCH, _ERROR_TO_DEGRADATION[error.category]),),
                started,
            )
        except TimeoutError:
            return self._completed(
                request,
                (),
                (Degradation(self._SIMILARITY_BRANCH, DegradationCategory.ADAPTER_TIMEOUT),),
                started,
            )
        except Exception:
            return self._completed(
                request,
                (),
                (Degradation(self._SIMILARITY_BRANCH, DegradationCategory.ADAPTER_ERROR),),
                started,
            )

        items = tuple(
            SearchResultItem(
                logical_id=candidate.resource_id,
                resource_id=candidate.resource_id,
                unit_id=candidate.unit_id,
                score=candidate.raw_score,
                rank=rank,
                evidence=(candidate,),
                metadata={},
            )
            for rank, candidate in enumerate(selected, start=1)
        )
        return self._completed(request, items, (), started)

    @staticmethod
    def _whole_resource_scope(scope: SearchScope) -> SearchScope | None:
        if scope.unit_kinds and UNIT_WHOLE_RESOURCE not in scope.unit_kinds:
            return None
        return SearchScope(
            resource_kinds=scope.resource_kinds,
            media_types=scope.media_types,
            source_namespaces=scope.source_namespaces,
            representation_kinds=scope.representation_kinds,
            modalities=scope.modalities,
            unit_kinds=(UNIT_WHOLE_RESOURCE,),
            facets_any=scope.facets_any,
            facets_all=scope.facets_all,
            facets_none=scope.facets_none,
        )

    def _unavailable(self, request: SimilarityRequest, started: float) -> SimilarityResult:
        return self._completed(
            request,
            (),
            (Degradation(self._SIMILARITY_BRANCH, DegradationCategory.BRANCH_UNAVAILABLE),),
            started,
        )

    def _completed(
        self,
        request: SimilarityRequest,
        items: tuple[SearchResultItem, ...],
        degradations: tuple[Degradation, ...],
        started: float,
    ) -> SimilarityResult:
        self._emit_similarity(
            "core.similarity.completed",
            request,
            status=LifecycleStatus.DEGRADED if degradations else LifecycleStatus.COMPLETED,
            result_count=len(items),
            degraded_branch_count=len(degradations),
            elapsed_ms=RetrievalService._elapsed_ms(started),
        )
        return SimilarityResult(
            request.query_unit_id,
            request.space_id,
            items,
            degradations,
        )

    def _emit_similarity(
        self,
        name: str,
        request: SimilarityRequest,
        **fields: object,
    ) -> None:
        emit_event(
            self._logger,
            SafeEvent(
                name=name,
                fields={
                    "branch_fingerprint": safe_fingerprint(request.query_unit_id),
                    "space_fingerprint": safe_fingerprint(request.space_id),
                    **fields,
                },
            ),
        )

    @staticmethod
    def _scope_filter_count(scope: SearchScope) -> int:
        return sum(
            len(values)
            for values in (
                scope.resource_kinds,
                scope.media_types,
                scope.source_namespaces,
                scope.representation_kinds,
                scope.modalities,
                scope.unit_kinds,
                scope.facets_any,
                scope.facets_all,
                scope.facets_none,
            )
        )


class RetrievalService:
    """Execute ready lexical/vector branches and return deterministic fused results."""

    def __init__(self, search_port: SearchPort, *, logger: logging.Logger | None = None) -> None:
        self._search_port = search_port
        self._logger = logger or logging.getLogger(__name__)

    def search(self, request: SearchRequest) -> SearchResult:
        """Execute a validated request without preparing queries or vectors."""
        if not isinstance(request, SearchRequest):
            raise ValueError("request must be a SearchRequest")

        started = time.perf_counter()
        self._emit(
            "core.search.started",
            request,
            status=LifecycleStatus.STARTED,
            branch_count=len(request.lexical_branches) + len(request.vector_branches),
            lexical_branch_count=len(request.lexical_branches),
            vector_branch_count=len(request.vector_branches),
            scope_filter_count=self._scope_filter_count(request),
            requested_limit=request.limit,
            candidate_limit_total=(
                sum(branch.candidate_limit for branch in request.lexical_branches)
                + sum(branch.candidate_limit for branch in request.vector_branches)
            ),
            rrf_k=request.rrf_k,
        )

        successful: list[_ExecutedBranch] = []
        degradations: list[Degradation] = []
        failures: list[BranchExecutionError] = []

        for lexical_branch in request.lexical_branches:
            result = self._execute_branch(
                request,
                lexical_branch,
                lambda: self._search_port.search_lexical(
                    lexical_branch,
                    scope=request.scope,
                ),
            )
            self._collect_result(
                request,
                lexical_branch,
                result,
                successful,
                degradations,
                failures,
            )
            if failures and not request.allow_partial:
                self._emit_failure(request, failures[0], started, len(failures))
                raise failures[0]

        for vector_branch in request.vector_branches:
            result = self._execute_branch(
                request,
                vector_branch,
                lambda: self._search_port.search_vector(
                    vector_branch,
                    scope=request.scope,
                ),
            )
            self._collect_result(
                request,
                vector_branch,
                result,
                successful,
                degradations,
                failures,
            )
            if failures and not request.allow_partial:
                self._emit_failure(request, failures[0], started, len(failures))
                raise failures[0]

        if not successful:
            failure = failures[0]
            self._emit_failure(request, failure, started, len(failures))
            raise failure

        fusion_branches = tuple(
            self._prepare_fusion_branch(branch, request)
            for branch in successful
        )
        fusion_input_count = sum(len(branch.candidates) for branch in fusion_branches)
        fused = weighted_rrf(
            fusion_branches,
            rrf_k=request.rrf_k,
            evidence_limit=(
                request.evidence_limit_per_resource
                if request.target == TARGET_RESOURCE
                else len(successful)
            ),
        )
        selected = fused[: request.limit]
        items = tuple(
            SearchResultItem(
                logical_id=item.logical_id,
                resource_id=item.representative.resource_id,
                unit_id=None if request.target == TARGET_RESOURCE else item.representative.unit_id,
                score=item.score,
                rank=rank,
                evidence=item.evidence,
                metadata=item.representative.metadata,
            )
            for rank, item in enumerate(selected, start=1)
        )

        unique_units = {
            candidate.unit_id
            for branch in successful
            for candidate in branch.candidates
        }
        unique_resources = {
            candidate.resource_id
            for branch in successful
            for candidate in branch.candidates
        }
        self._emit(
            "core.search.fusion.completed",
            request,
            status=LifecycleStatus.COMPLETED,
            fusion_input_count=fusion_input_count,
            unique_unit_count=len(unique_units),
            unique_resource_count=len(unique_resources),
            result_count=len(items),
            rrf_k=request.rrf_k,
            degraded_branch_count=len(degradations),
        )
        self._emit(
            "core.search.completed",
            request,
            status=LifecycleStatus.COMPLETED,
            result_count=len(items),
            degraded_branch_count=len(degradations),
            elapsed_ms=self._elapsed_ms(started),
        )
        return SearchResult(
            target=request.target,
            items=items,
            degradations=tuple(degradations),
            request_id=request.request_id,
        )

    def _execute_branch(
        self,
        request: SearchRequest,
        branch: LexicalBranch | VectorBranch,
        operation: Callable[[], list[RankedCandidate]],
    ) -> tuple[RankedCandidate, ...] | BranchExecutionError:
        started = time.perf_counter()
        try:
            raw_candidates = operation()
            candidates = self._validate_candidates(branch, raw_candidates)
        except BranchExecutionError as error:
            failure = BranchExecutionError(error.category, branch_id=branch.branch_id)
            if request.allow_partial:
                self._emit_degradation(request, branch.branch_id, failure, started)
            return failure
        except TimeoutError:
            failure = BranchExecutionError(ErrorCategory.ADAPTER_TIMEOUT, branch_id=branch.branch_id)
            if request.allow_partial:
                self._emit_degradation(request, branch.branch_id, failure, started)
            return failure
        except Exception:
            failure = BranchExecutionError(ErrorCategory.ADAPTER_ERROR, branch_id=branch.branch_id)
            if request.allow_partial:
                self._emit_degradation(request, branch.branch_id, failure, started)
            return failure

        self._emit(
            "core.search.branch.completed",
            request,
            status=LifecycleStatus.COMPLETED,
            branch_fingerprint=safe_fingerprint(branch.branch_id),
            candidate_count=len(candidates),
            elapsed_ms=self._elapsed_ms(started),
        )
        return candidates

    @staticmethod
    def _validate_candidates(
        branch: LexicalBranch | VectorBranch,
        candidates: object,
    ) -> tuple[RankedCandidate, ...]:
        if not isinstance(candidates, list):
            raise TypeError("search ports must return a list")
        validated: list[RankedCandidate] = []
        seen_unit_ids: set[str] = set()
        for expected_rank, candidate in enumerate(
            candidates[: branch.candidate_limit],
            start=1,
        ):
            if not isinstance(candidate, RankedCandidate):
                raise TypeError("search ports must return RankedCandidate values")
            if candidate.branch_id != branch.branch_id:
                raise ValueError("candidate branch_id does not match the executed branch")
            if candidate.rank != expected_rank:
                raise ValueError("candidate ranks must match their 1-based positions")
            if candidate.unit_id in seen_unit_ids:
                raise ValueError("candidate unit_id values must be unique")
            seen_unit_ids.add(candidate.unit_id)
            validated.append(candidate)
        return tuple(validated)

    @staticmethod
    def _collect_result(
        request: SearchRequest,
        branch: LexicalBranch | VectorBranch,
        result: tuple[RankedCandidate, ...] | BranchExecutionError,
        successful: list[_ExecutedBranch],
        degradations: list[Degradation],
        failures: list[BranchExecutionError],
    ) -> None:
        if isinstance(result, BranchExecutionError):
            failures.append(result)
            if request.allow_partial:
                degradations.append(
                    Degradation(
                        branch_id=branch.branch_id,
                        category=_ERROR_TO_DEGRADATION[result.category],
                    )
                )
            return
        successful.append(
            _ExecutedBranch(
                branch_id=branch.branch_id,
                weight=branch.weight,
                candidates=result,
            )
        )

    def _prepare_fusion_branch(
        self,
        branch: _ExecutedBranch,
        request: SearchRequest,
    ) -> FusionBranch:
        if request.target == TARGET_RESOURCE:
            candidates = self._group_resources(
                branch.candidates,
                evidence_limit=request.evidence_limit_per_resource,
            )
        else:
            candidates = self._deduplicate_units(branch.candidates)
        return FusionBranch(
            branch_id=branch.branch_id,
            weight=branch.weight,
            candidates=candidates,
        )

    @staticmethod
    def _deduplicate_units(
        candidates: tuple[RankedCandidate, ...],
    ) -> tuple[FusionCandidate, ...]:
        seen: set[str] = set()
        unique: list[FusionCandidate] = []
        for candidate in candidates:
            if candidate.unit_id in seen:
                continue
            seen.add(candidate.unit_id)
            unique.append(
                FusionCandidate(
                    logical_id=candidate.unit_id,
                    representative=candidate,
                    evidence=(candidate,),
                )
            )
        return tuple(unique)

    @staticmethod
    def _group_resources(
        candidates: tuple[RankedCandidate, ...],
        *,
        evidence_limit: int,
    ) -> tuple[FusionCandidate, ...]:
        by_resource: dict[str, list[tuple[int, RankedCandidate]]] = {}
        seen_units: set[str] = set()
        for first_seen, candidate in enumerate(candidates):
            if candidate.unit_id in seen_units:
                continue
            seen_units.add(candidate.unit_id)
            by_resource.setdefault(candidate.resource_id, []).append((first_seen, candidate))

        grouped: list[tuple[int, FusionCandidate]] = []
        for resource_id, entries in by_resource.items():
            ordered = sorted(entries, key=lambda item: (item[1].rank, item[0], item[1].unit_id))
            representative = ordered[0][1]
            grouped.append(
                (
                    min(first_seen for first_seen, _ in entries),
                    FusionCandidate(
                        logical_id=resource_id,
                        representative=representative,
                        evidence=tuple(item for _, item in ordered[:evidence_limit]),
                    ),
                )
            )
        grouped.sort(key=lambda item: (item[0], item[1].logical_id))
        return tuple(item for _, item in grouped)

    def _emit_degradation(
        self,
        request: SearchRequest,
        branch_id: str,
        failure: BranchExecutionError,
        started: float,
    ) -> None:
        self._emit(
            "core.search.branch.degraded",
            request,
            status=LifecycleStatus.DEGRADED,
            branch_fingerprint=safe_fingerprint(branch_id),
            category=_ERROR_TO_DEGRADATION[failure.category],
            elapsed_ms=self._elapsed_ms(started),
        )

    def _emit_failure(
        self,
        request: SearchRequest,
        failure: BranchExecutionError,
        started: float,
        failure_count: int,
    ) -> None:
        self._emit(
            "core.search.failed",
            request,
            status=LifecycleStatus.FAILED,
            category=failure.category,
            degraded_branch_count=failure_count,
            elapsed_ms=self._elapsed_ms(started),
        )

    def _emit(self, name: str, request: SearchRequest, **fields: object) -> None:
        payload = {"target": request.target, **fields}
        if request.request_id is not None:
            payload["request_id"] = request.request_id
        emit_event(self._logger, SafeEvent(name=name, fields=payload))

    @staticmethod
    def _scope_filter_count(request: SearchRequest) -> int:
        scope = request.scope
        return sum(
            len(values)
            for values in (
                scope.resource_kinds,
                scope.media_types,
                scope.source_namespaces,
                scope.representation_kinds,
                scope.modalities,
                scope.unit_kinds,
                scope.facets_any,
                scope.facets_all,
                scope.facets_none,
            )
        )

    @staticmethod
    def _elapsed_ms(started: float) -> float:
        return max(0.0, (time.perf_counter() - started) * 1000.0)
