"""Deterministic weighted reciprocal-rank fusion for core retrieval."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from ..domain.search import RankedCandidate


@dataclass(frozen=True)
class FusionCandidate:
    """One logical fusion candidate with bounded supporting evidence."""

    logical_id: str
    representative: RankedCandidate
    evidence: tuple[RankedCandidate, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.logical_id, str) or not self.logical_id.strip():
            raise ValueError("logical_id must be a non-empty string")
        if not isinstance(self.representative, RankedCandidate):
            raise ValueError("representative must be a RankedCandidate")
        if not isinstance(self.evidence, (list, tuple)) or any(
            not isinstance(item, RankedCandidate) for item in self.evidence
        ):
            raise ValueError("evidence must contain only RankedCandidate values")
        object.__setattr__(self, "evidence", tuple(self.evidence))


@dataclass(frozen=True)
class FusionBranch:
    """A branch after branch-local duplicate suppression or grouping."""

    branch_id: str
    weight: float
    candidates: tuple[FusionCandidate, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.branch_id, str) or not self.branch_id.strip():
            raise ValueError("branch_id must be a non-empty string")
        if isinstance(self.weight, bool) or not isinstance(self.weight, (int, float)):
            raise ValueError("weight must be a finite positive number")
        weight = float(self.weight)
        if not math.isfinite(weight) or weight <= 0:
            raise ValueError("weight must be a finite positive number")
        if not isinstance(self.candidates, (list, tuple)) or any(
            not isinstance(item, FusionCandidate) for item in self.candidates
        ):
            raise ValueError("candidates must contain only FusionCandidate values")
        object.__setattr__(self, "weight", weight)
        object.__setattr__(self, "candidates", tuple(self.candidates))


@dataclass(frozen=True)
class FusedCandidate:
    """One deterministic fusion result before public result projection."""

    logical_id: str
    score: float
    first_seen: int
    representative: RankedCandidate
    evidence: tuple[RankedCandidate, ...]


def weighted_rrf(
    branches: Sequence[FusionBranch],
    *,
    rrf_k: int,
    evidence_limit: int,
) -> tuple[FusedCandidate, ...]:
    """Fuse arbitrary ranked branches, counting each logical ID once per branch."""
    if rrf_k < 1:
        raise ValueError("rrf_k must be positive")
    if evidence_limit < 1:
        raise ValueError("evidence_limit must be positive")

    scores: dict[str, float] = {}
    first_seen: dict[str, int] = {}
    representatives: dict[str, RankedCandidate] = {}
    evidence_by_id: dict[str, list[tuple[int, int, RankedCandidate]]] = {}
    candidate_ordinal = 0
    evidence_ordinal = 0

    for branch in branches:
        seen_in_branch: set[str] = set()
        for candidate in branch.candidates:
            logical_id = candidate.logical_id
            if logical_id in seen_in_branch:
                continue
            seen_in_branch.add(logical_id)
            first_seen.setdefault(logical_id, candidate_ordinal)
            representatives.setdefault(logical_id, candidate.representative)
            scores[logical_id] = scores.get(logical_id, 0.0) + (
                branch.weight / (rrf_k + candidate.representative.rank)
            )
            evidence = evidence_by_id.setdefault(logical_id, [])
            known_evidence = {
                (item.branch_id, item.unit_id) for _, _, item in evidence
            }
            for item in candidate.evidence:
                evidence_key = (item.branch_id, item.unit_id)
                if evidence_key not in known_evidence:
                    evidence.append((item.rank, evidence_ordinal, item))
                    known_evidence.add(evidence_key)
                evidence_ordinal += 1
            candidate_ordinal += 1

    fused = [
        FusedCandidate(
            logical_id=logical_id,
            score=score,
            first_seen=first_seen[logical_id],
            representative=representatives[logical_id],
            evidence=tuple(
                item
                for _, _, item in sorted(
                    evidence_by_id.get(logical_id, []),
                    key=lambda entry: (entry[0], entry[1], entry[2].unit_id),
                )[:evidence_limit]
            ),
        )
        for logical_id, score in scores.items()
    ]
    fused.sort(key=lambda item: (-item.score, item.first_seen, item.logical_id))
    return tuple(fused)
