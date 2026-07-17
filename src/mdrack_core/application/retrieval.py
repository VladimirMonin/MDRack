"""Provider- and persistence-neutral multi-branch retrieval orchestration."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from ..domain.errors import (
    BranchExecutionError,
    DegradationCategory,
    ErrorCategory,
)
from ..domain.results import Degradation, SearchResult, SearchResultItem
from ..domain.search import (
    TARGET_RESOURCE,
    LexicalBranch,
    RankedCandidate,
    SearchRequest,
    VectorBranch,
)
from ..observability import (
    LifecycleStatus,
    SafeEvent,
    emit_event,
    safe_fingerprint,
)
from ..ports.search import SearchPort
from .fusion import FusionBranch, FusionCandidate, weighted_rrf

_ERROR_TO_DEGRADATION = {
    ErrorCategory.BRANCH_UNAVAILABLE: DegradationCategory.BRANCH_UNAVAILABLE,
    ErrorCategory.INCOMPATIBLE_VECTOR_SPACE: DegradationCategory.INCOMPATIBLE_VECTOR_SPACE,
    ErrorCategory.ADAPTER_TIMEOUT: DegradationCategory.ADAPTER_TIMEOUT,
    ErrorCategory.ADAPTER_ERROR: DegradationCategory.ADAPTER_ERROR,
}


@dataclass(frozen=True)
class _ExecutedBranch:
    branch_id: str
    weight: float
    candidates: tuple[RankedCandidate, ...]


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
        for candidate in candidates[: branch.candidate_limit]:
            if not isinstance(candidate, RankedCandidate):
                raise TypeError("search ports must return RankedCandidate values")
            if candidate.branch_id != branch.branch_id:
                raise ValueError("candidate branch_id does not match the executed branch")
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
