from __future__ import annotations

import logging
from collections.abc import Mapping

import pytest

from mdrack_core.application import RetrievalService
from mdrack_core.domain import (
    BranchExecutionError,
    BranchScopeOverride,
    DegradationCategory,
    ErrorCategory,
    Facet,
    JSONValue,
    LexicalBranch,
    Locator,
    RankedCandidate,
    ScoreKind,
    SearchRequest,
    SearchScope,
    VectorBranch,
)


class SearchPortSpy:
    def __init__(
        self,
        outcomes: Mapping[str, list[RankedCandidate] | BaseException],
    ) -> None:
        self.outcomes = outcomes
        self.calls: list[tuple[str, object, SearchScope]] = []

    def search_lexical(
        self,
        branch: LexicalBranch,
        *,
        scope: SearchScope,
    ) -> list[RankedCandidate]:
        self.calls.append(("lexical", branch, scope))
        return self._outcome(branch.branch_id)

    def search_vector(
        self,
        branch: VectorBranch,
        *,
        scope: SearchScope,
    ) -> list[RankedCandidate]:
        self.calls.append(("vector", branch, scope))
        return self._outcome(branch.branch_id)

    def _outcome(self, branch_id: str) -> list[RankedCandidate]:
        outcome = self.outcomes[branch_id]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _candidate(
    unit_id: str,
    *,
    branch_id: str,
    rank: int,
    resource_id: str | None = None,
    metadata: Mapping[str, JSONValue] | None = None,
) -> RankedCandidate:
    return RankedCandidate(
        unit_id=unit_id,
        resource_id=resource_id or f"resource-{unit_id}",
        representation_id=f"representation-{unit_id}",
        rank=rank,
        raw_score=1.0 / rank,
        branch_id=branch_id,
        evidence_locator=Locator(kind="test", payload={"rank": rank}),
        metadata=metadata or {},
    )


def _request(
    *,
    lexical: tuple[LexicalBranch, ...] = (),
    vectors: tuple[VectorBranch, ...] = (),
    scope: SearchScope | None = None,
    target: str = "unit",
    limit: int = 10,
    evidence_limit: int = 3,
    allow_partial: bool = True,
    request_id: str | None = None,
) -> SearchRequest:
    return SearchRequest(
        lexical_branches=lexical,
        vector_branches=vectors,
        scope=scope or SearchScope(),
        target=target,
        limit=limit,
        rrf_k=10,
        evidence_limit_per_resource=evidence_limit,
        allow_partial=allow_partial,
        request_id=request_id,
    )


def test_forwards_the_exact_scope_to_every_arbitrary_branch() -> None:
    scope = SearchScope(
        resource_kinds=("document",),
        media_types=("text/markdown",),
        source_namespaces=("notes",),
        representation_kinds=("retrieval_text",),
        modalities=("text",),
        unit_kinds=("text_chunk",),
        facets_any=(Facet("topic", "python"),),
        facets_all=(Facet("status", "reviewed"),),
        facets_none=(Facet("visibility", "private"),),
    )
    lexical = (LexicalBranch("lexical-a", "PRIVATE_QUERY_SENTINEL"),)
    vectors = (
        VectorBranch("vector-a", "text-space", (0.1, 0.2)),
        VectorBranch("vector-b", "visual-space", (0.3, 0.4)),
    )
    outcomes: dict[str, list[RankedCandidate] | BaseException] = {
        branch.branch_id: [] for branch in lexical
    }
    outcomes.update({branch.branch_id: [] for branch in vectors})
    port = SearchPortSpy(outcomes)

    result = RetrievalService(port).search(
        _request(lexical=lexical, vectors=vectors, scope=scope),
    )

    assert result.items == ()
    assert [(kind, branch.branch_id) for kind, branch, _ in port.calls] == [
        ("lexical", "lexical-a"),
        ("vector", "vector-a"),
        ("vector", "vector-b"),
    ]
    assert all(call_scope is scope for _, _, call_scope in port.calls)


@pytest.mark.parametrize(
    "field_name",
    (
        "resource_kinds",
        "media_types",
        "source_namespaces",
        "representation_kinds",
        "modalities",
        "unit_kinds",
    ),
)
def test_branch_scope_override_only_narrows_categorical_scope_and_preserves_facets(
    field_name: str,
) -> None:
    required = Facet("topic", "python")
    forbidden = Facet("visibility", "private")
    global_scope = SearchScope(
        **{field_name: ("shared", "global-only")},
        facets_any=(required,),
        facets_all=(required,),
        facets_none=(forbidden,),
    )
    override = BranchScopeOverride(**{field_name: ("branch-only", "shared")})
    branch = LexicalBranch("lexical", "query", scope_override=override)
    port = SearchPortSpy({"lexical": []})

    result = RetrievalService(port).search(_request(lexical=(branch,), scope=global_scope))

    assert result.items == ()
    assert len(port.calls) == 1
    effective = port.calls[0][2]
    assert getattr(effective, field_name) == ("shared",)
    assert effective.facets_any == global_scope.facets_any
    assert effective.facets_all == global_scope.facets_all
    assert effective.facets_none == global_scope.facets_none


def test_empty_branch_categorical_intersection_skips_adapter_without_degradation() -> None:
    branch = LexicalBranch(
        "lexical",
        "query",
        scope_override=BranchScopeOverride(modalities=("image",)),
    )
    port = SearchPortSpy({"lexical": RuntimeError("must not execute")})

    result = RetrievalService(port).search(
        _request(
            lexical=(branch,),
            scope=SearchScope(modalities=("text",)),
        )
    )

    assert result.items == ()
    assert result.degradations == ()
    assert port.calls == []


def test_vector_branch_uses_narrowed_scope_and_skips_disjoint_override() -> None:
    matching = VectorBranch(
        "matching",
        "space",
        (1.0,),
        scope_override=BranchScopeOverride(representation_kinds=("transcript",)),
    )
    disjoint = VectorBranch(
        "disjoint",
        "space",
        (1.0,),
        scope_override=BranchScopeOverride(modalities=("image",)),
    )
    port = SearchPortSpy({"matching": [], "disjoint": RuntimeError("must not execute")})
    global_scope = SearchScope(
        representation_kinds=("transcript", "frame_caption"),
        modalities=("text",),
        facets_all=(Facet("project", "mdrack"),),
    )

    result = RetrievalService(port).search(
        _request(vectors=(matching, disjoint), scope=global_scope)
    )

    assert result.items == ()
    assert len(port.calls) == 1
    kind, called_branch, effective = port.calls[0]
    assert kind == "vector"
    assert called_branch is matching
    assert effective.representation_kinds == ("transcript",)
    assert effective.modalities == ("text",)
    assert effective.facets_all == global_scope.facets_all


def test_unit_fusion_combines_branch_evidence_and_applies_weights() -> None:
    lexical = LexicalBranch("lexical", "query", weight=1.0)
    vector = VectorBranch("vector", "space", (1.0,), weight=3.0)
    lexical_shared = _candidate("shared", branch_id="lexical", rank=1)
    vector_shared = _candidate("shared", branch_id="vector", rank=1)
    vector_only = _candidate("vector-only", branch_id="vector", rank=2)
    port = SearchPortSpy(
        {
            "lexical": [lexical_shared],
            "vector": [vector_shared, vector_only],
        }
    )

    result = RetrievalService(port).search(
        _request(lexical=(lexical,), vectors=(vector,)),
    )

    assert [item.logical_id for item in result.items] == ["shared", "vector-only"]
    assert result.items[0].score == pytest.approx(1 / 11 + 3 / 11)
    assert result.items[0].score_kind is ScoreKind.RRF
    assert result.items[0].unit_id == "shared"
    assert result.items[0].evidence == (lexical_shared, vector_shared)


def test_resource_grouping_happens_per_branch_before_fusion() -> None:
    lexical = LexicalBranch("lexical", "query")
    vector = VectorBranch("vector", "space", (1.0,))
    long_units = [
        _candidate(f"long-{rank}", branch_id="lexical", rank=rank, resource_id="long")
        for rank in (1, 2, 3)
    ]
    short_lexical = _candidate("short-lexical", branch_id="lexical", rank=4, resource_id="short")
    short_vector = _candidate("short-vector", branch_id="vector", rank=1, resource_id="short")
    port = SearchPortSpy(
        {
            "lexical": [*long_units, short_lexical],
            "vector": [short_vector],
        }
    )

    result = RetrievalService(port).search(
        _request(
            lexical=(lexical,),
            vectors=(vector,),
            target="resource",
            evidence_limit=2,
        ),
    )

    assert [item.logical_id for item in result.items] == ["short", "long"]
    assert result.items[0].score == pytest.approx(1 / 12 + 1 / 11)
    assert result.items[1].score == pytest.approx(1 / 11)
    assert result.items[0].unit_id is None
    assert [candidate.unit_id for candidate in result.items[0].evidence] == [
        "short-vector",
        "short-lexical",
    ]
    assert [candidate.unit_id for candidate in result.items[1].evidence] == [
        "long-1",
        "long-2",
    ]


def test_backend_overproduction_is_bounded_by_branch_candidate_limit() -> None:
    branch = LexicalBranch("lexical", "query", candidate_limit=1)
    port = SearchPortSpy(
        {
            "lexical": [
                _candidate("first", branch_id="lexical", rank=1),
                _candidate("second", branch_id="lexical", rank=2),
            ]
        }
    )

    result = RetrievalService(port).search(_request(lexical=(branch,)))

    assert [item.logical_id for item in result.items] == ["first"]


@pytest.mark.parametrize(
    "bad_outcome",
    [
        [_candidate("unit", branch_id="lexical", rank=2)],
        [
            _candidate("second", branch_id="lexical", rank=2),
            _candidate("first", branch_id="lexical", rank=1),
        ],
        [
            _candidate("first", branch_id="lexical", rank=1),
            _candidate("third", branch_id="lexical", rank=3),
        ],
        [
            _candidate("first", branch_id="lexical", rank=1),
            _candidate("second", branch_id="lexical", rank=1),
        ],
        [
            _candidate("duplicate", branch_id="lexical", rank=1),
            _candidate("duplicate", branch_id="lexical", rank=2),
        ],
    ],
    ids=("missing-first", "shuffled", "gap", "duplicate-rank", "duplicate-unit"),
)
def test_malformed_adapter_rank_contract_becomes_safe_adapter_error(
    bad_outcome: list[RankedCandidate],
) -> None:
    branch = LexicalBranch("lexical", "query")
    port = SearchPortSpy({"lexical": bad_outcome})

    with pytest.raises(BranchExecutionError) as captured:
        RetrievalService(port).search(_request(lexical=(branch,)))

    assert captured.value.category is ErrorCategory.ADAPTER_ERROR
    assert captured.value.branch_id == branch.branch_id
    assert str(captured.value) == "adapter_error"


@pytest.mark.parametrize(
    ("error_category", "degradation_category"),
    [
        (ErrorCategory.BRANCH_UNAVAILABLE, DegradationCategory.BRANCH_UNAVAILABLE),
        (
            ErrorCategory.INCOMPATIBLE_VECTOR_SPACE,
            DegradationCategory.INCOMPATIBLE_VECTOR_SPACE,
        ),
        (ErrorCategory.ADAPTER_TIMEOUT, DegradationCategory.ADAPTER_TIMEOUT),
        (ErrorCategory.ADAPTER_ERROR, DegradationCategory.ADAPTER_ERROR),
    ],
)
def test_partial_search_returns_other_branches_with_stable_degradation(
    error_category: ErrorCategory,
    degradation_category: DegradationCategory,
) -> None:
    failed = LexicalBranch("failed", "query")
    healthy = VectorBranch("healthy", "space", (1.0,))
    port = SearchPortSpy(
        {
            "failed": BranchExecutionError(error_category, branch_id="failed"),
            "healthy": [_candidate("unit", branch_id="healthy", rank=1)],
        }
    )

    result = RetrievalService(port).search(
        _request(lexical=(failed,), vectors=(healthy,), allow_partial=True),
    )

    assert [item.logical_id for item in result.items] == ["unit"]
    assert result.degradations[0].branch_id == "failed"
    assert result.degradations[0].category is degradation_category


@pytest.mark.parametrize(
    "category",
    [
        ErrorCategory.BRANCH_UNAVAILABLE,
        ErrorCategory.INCOMPATIBLE_VECTOR_SPACE,
        ErrorCategory.ADAPTER_TIMEOUT,
        ErrorCategory.ADAPTER_ERROR,
    ],
)
def test_disallow_partial_fails_fast_without_calling_later_branches(
    category: ErrorCategory,
) -> None:
    failed = LexicalBranch("failed", "query")
    later = VectorBranch("later", "space", (1.0,))
    port = SearchPortSpy(
        {
            "failed": BranchExecutionError(
                category,
                branch_id="adapter-private-id",
            ),
            "later": [_candidate("unit", branch_id="later", rank=1)],
        }
    )

    with pytest.raises(BranchExecutionError) as captured:
        RetrievalService(port).search(
            _request(lexical=(failed,), vectors=(later,), allow_partial=False),
        )

    assert captured.value.category is category
    assert captured.value.branch_id == "failed"
    assert [kind for kind, _, _ in port.calls] == ["lexical"]


def test_all_failed_branches_raise_even_when_partial_is_allowed() -> None:
    timeout = LexicalBranch("timeout", "query")
    adapter = VectorBranch("adapter", "space", (1.0,))
    port = SearchPortSpy(
        {
            "timeout": TimeoutError("PRIVATE_EXCEPTION_SENTINEL"),
            "adapter": RuntimeError("PRIVATE_EXCEPTION_SENTINEL"),
        }
    )

    with pytest.raises(BranchExecutionError) as captured:
        RetrievalService(port).search(
            _request(lexical=(timeout,), vectors=(adapter,), allow_partial=True),
        )

    assert captured.value.category is ErrorCategory.ADAPTER_TIMEOUT
    assert captured.value.branch_id == "timeout"
    assert len(port.calls) == 2


def test_all_successful_empty_branches_return_successful_empty_result() -> None:
    lexical = LexicalBranch("lexical", "query")
    vector = VectorBranch("vector", "space", (1.0,))
    port = SearchPortSpy({"lexical": [], "vector": []})

    result = RetrievalService(port).search(
        _request(lexical=(lexical,), vectors=(vector,), allow_partial=False),
    )

    assert result.items == ()
    assert result.degradations == ()


@pytest.mark.parametrize(
    "bad_outcome",
    [
        [_candidate("unit", branch_id="wrong", rank=1)],
        [object()],
        (_candidate("unit", branch_id="lexical", rank=1),),
    ],
)
def test_invalid_adapter_results_become_safe_adapter_errors(
    bad_outcome: object,
) -> None:
    branch = LexicalBranch("lexical", "query")
    port = SearchPortSpy({"lexical": bad_outcome})  # type: ignore[arg-type]

    with pytest.raises(BranchExecutionError) as captured:
        RetrievalService(port).search(_request(lexical=(branch,)))

    assert captured.value.category is ErrorCategory.ADAPTER_ERROR
    assert str(captured.value) == "adapter_error"


def test_malformed_adapter_output_degrades_partially_without_private_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    malformed = LexicalBranch("malformed", "PRIVATE_QUERY_SENTINEL")
    healthy = VectorBranch("healthy", "space", (1.0,))
    private_unit = "PRIVATE_UNIT_SENTINEL"
    port = SearchPortSpy(
        {
            "malformed": [_candidate(private_unit, branch_id="malformed", rank=2)],
            "healthy": [_candidate("healthy-unit", branch_id="healthy", rank=1)],
        }
    )
    logger = logging.getLogger("tests.core.retrieval.malformed.partial")

    with caplog.at_level(logging.INFO, logger=logger.name):
        result = RetrievalService(port, logger=logger).search(
            _request(lexical=(malformed,), vectors=(healthy,), allow_partial=True),
        )

    assert [item.logical_id for item in result.items] == ["healthy-unit"]
    assert result.degradations[0].branch_id == malformed.branch_id
    assert result.degradations[0].category is DegradationCategory.ADAPTER_ERROR
    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert private_unit not in messages
    assert malformed.query not in messages


def test_malformed_adapter_output_fails_safely_without_private_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    branch = LexicalBranch("malformed", "PRIVATE_QUERY_SENTINEL")
    private_unit = "PRIVATE_UNIT_SENTINEL"
    port = SearchPortSpy(
        {"malformed": [_candidate(private_unit, branch_id="malformed", rank=2)]}
    )
    logger = logging.getLogger("tests.core.retrieval.malformed.failed")

    with caplog.at_level(logging.INFO, logger=logger.name):
        with pytest.raises(BranchExecutionError) as captured:
            RetrievalService(port, logger=logger).search(_request(lexical=(branch,)))

    assert captured.value.category is ErrorCategory.ADAPTER_ERROR
    assert str(captured.value) == "adapter_error"
    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert private_unit not in messages
    assert branch.query not in messages


def test_events_expose_only_frozen_safe_fields_and_no_private_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    query = "PRIVATE_QUERY_SENTINEL"
    request_id = "123E4567-E89B-12D3-A456-426614174000"
    branch = LexicalBranch("PRIVATE_BRANCH_SENTINEL", query)
    port = SearchPortSpy(
        {
            branch.branch_id: RuntimeError(
                "PRIVATE_EXCEPTION_SENTINEL /private/home/sentinel "
                "https://secret-host.invalid:1234/path [0.123456, 0.654321]"
            )
        }
    )
    logger = logging.getLogger("tests.core.retrieval.privacy")

    with caplog.at_level(logging.INFO, logger=logger.name):
        with pytest.raises(BranchExecutionError):
            RetrievalService(port, logger=logger).search(
                _request(lexical=(branch,), request_id=request_id),
            )

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "core.search.started" in messages
    assert "core.search.branch.degraded" in messages
    assert "core.search.failed" in messages
    for sentinel in (
        query,
        branch.branch_id,
        "PRIVATE_EXCEPTION_SENTINEL",
        "/private/home/sentinel",
        "secret-host.invalid",
        "0.123456",
    ):
        assert sentinel not in messages
    assert request_id.lower() in messages
    assert "sha256:" in messages
    assert "adapter_error" in messages, messages


@pytest.mark.parametrize("with_candidate", [False, True])
def test_success_and_empty_events_do_not_log_query_scope_or_metadata_values(
    caplog: pytest.LogCaptureFixture,
    with_candidate: bool,
) -> None:
    query = "PRIVATE_SUCCESS_QUERY_SENTINEL"
    metadata_value = "PRIVATE_METADATA_SENTINEL"
    facet_value = "PRIVATE_FACET_SENTINEL"
    branch = LexicalBranch("success-branch", query)
    candidates = (
        [
            _candidate(
                "unit",
                branch_id=branch.branch_id,
                rank=1,
                metadata={"private": metadata_value},
            )
        ]
        if with_candidate
        else []
    )
    port = SearchPortSpy({branch.branch_id: candidates})
    logger = logging.getLogger(f"tests.core.retrieval.success.{with_candidate}")
    scope = SearchScope(facets_any=(Facet("private", facet_value),))

    with caplog.at_level(logging.INFO, logger=logger.name):
        RetrievalService(port, logger=logger).search(
            _request(lexical=(branch,), scope=scope),
        )

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "core.search.completed" in messages
    for sentinel in (query, metadata_value, facet_value):
        assert sentinel not in messages
