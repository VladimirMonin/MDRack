from __future__ import annotations

import pytest

from mdrack_core.application.fusion import (
    FusionBranch,
    FusionCandidate,
    weighted_rrf,
)
from mdrack_core.domain import Locator, RankedCandidate


def _candidate(
    logical_id: str,
    *,
    branch_id: str,
    rank: int,
    resource_id: str | None = None,
) -> RankedCandidate:
    return RankedCandidate(
        unit_id=logical_id,
        resource_id=resource_id or f"resource-{logical_id}",
        representation_id=f"representation-{logical_id}",
        rank=rank,
        raw_score=100.0 - rank,
        branch_id=branch_id,
        evidence_locator=Locator(kind="test", payload={"ordinal": rank}),
    )


def _fusion_candidate(candidate: RankedCandidate) -> FusionCandidate:
    return FusionCandidate(
        logical_id=candidate.unit_id,
        representative=candidate,
        evidence=(candidate,),
    )


def test_weighted_rrf_uses_arbitrary_branch_weights_and_candidate_ranks() -> None:
    a1 = _candidate("a", branch_id="lexical", rank=1)
    b1 = _candidate("b", branch_id="lexical", rank=2)
    b2 = _candidate("b", branch_id="visual", rank=1)
    a2 = _candidate("a", branch_id="visual", rank=4)

    fused = weighted_rrf(
        (
            FusionBranch("lexical", 1.0, (_fusion_candidate(a1), _fusion_candidate(b1))),
            FusionBranch("visual", 3.0, (_fusion_candidate(b2), _fusion_candidate(a2))),
        ),
        rrf_k=10,
        evidence_limit=3,
    )

    assert [item.logical_id for item in fused] == ["b", "a"]
    assert fused[0].score == pytest.approx(1 / 12 + 3 / 11)
    assert fused[1].score == pytest.approx(1 / 11 + 3 / 14)
    assert fused[0].representative is b1


def test_first_duplicate_per_branch_wins_and_contributes_only_once() -> None:
    first = _candidate("same", branch_id="branch", rank=8)
    duplicate = _candidate("same", branch_id="branch", rank=1)

    fused = weighted_rrf(
        (
            FusionBranch(
                "branch",
                2.0,
                (_fusion_candidate(first), _fusion_candidate(duplicate)),
            ),
        ),
        rrf_k=10,
        evidence_limit=2,
    )

    assert len(fused) == 1
    assert fused[0].score == pytest.approx(2 / 18)
    assert fused[0].representative is first


def test_ties_preserve_first_seen_before_logical_id() -> None:
    zulu = _candidate("zulu", branch_id="branch", rank=1)
    alpha = _candidate("alpha", branch_id="branch", rank=1)

    fused = weighted_rrf(
        (FusionBranch("branch", 1.0, (_fusion_candidate(zulu), _fusion_candidate(alpha))),),
        rrf_k=60,
        evidence_limit=1,
    )

    assert [item.logical_id for item in fused] == ["zulu", "alpha"]


def test_evidence_is_deduplicated_bounded_and_deterministically_ranked() -> None:
    high = _candidate("high", branch_id="one", rank=4, resource_id="resource")
    low = _candidate("low", branch_id="one", rank=2, resource_id="resource")
    other = _candidate("other", branch_id="two", rank=1, resource_id="resource")
    branches = (
        FusionBranch(
            "one",
            1.0,
            (FusionCandidate("resource", high, (high, low)),),
        ),
        FusionBranch(
            "two",
            1.0,
            (FusionCandidate("resource", other, (other, low)),),
        ),
    )

    fused = weighted_rrf(branches, rrf_k=60, evidence_limit=2)

    assert [candidate.unit_id for candidate in fused[0].evidence] == ["other", "low"]


@pytest.mark.parametrize("weight", [0.0, -1.0, float("inf"), float("nan"), True])
def test_fusion_branch_rejects_non_positive_or_non_finite_weight(weight: float) -> None:
    with pytest.raises(ValueError):
        FusionBranch("branch", weight, ())


@pytest.mark.parametrize("rrf_k,evidence_limit", [(0, 1), (1, 0)])
def test_weighted_rrf_rejects_invalid_limits(rrf_k: int, evidence_limit: int) -> None:
    with pytest.raises(ValueError):
        weighted_rrf((), rrf_k=rrf_k, evidence_limit=evidence_limit)
