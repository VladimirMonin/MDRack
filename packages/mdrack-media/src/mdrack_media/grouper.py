"""Deterministic grouping of caller-prepared timed text atoms."""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import cast

from .common import JSONValue, freeze_metadata, plain_json
from .fingerprints import (
    GrouperFingerprint,
    NormalizationFingerprint,
    TokenCounterFingerprint,
)
from .identifiers import ID_RESOURCE, passage_id, representation_id, validate_media_id
from .policies import TimedChunkingPolicy
from .ports import TokenCounter
from .records import (
    REPRESENTATION_TIMED_PASSAGE,
    TOKEN_COUNT_KINDS,
    TimedPassage,
    TimedTextAtom,
    TokenCount,
)

GROUPER_ALGORITHM = "deterministic-window-v1"
JOIN_POLICY = "preserve-boundary-whitespace-v1"
UNSPLITTABLE_REJECT = "reject"
UNSPLITTABLE_FLAG = "flag"
UNSPLITTABLE_POLICIES = frozenset({UNSPLITTABLE_REJECT, UNSPLITTABLE_FLAG})

_SCORE_WEIGHTS = {
    "hard_limit_risk": 100,
    "speaker_change": 45,
    "sentence_end": 40,
    "strong_pause": 35,
    "soft_max": 20,
    "medium_pause": 15,
    "line_break": 15,
    "target_tokens": 10,
    "target_duration": 10,
}
_SENTENCE_ENDINGS = frozenset(".!?。！？…")


class TimedGroupingError(ValueError):
    """A safe, categorical timed-grouping failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class GroupingMetrics:
    source_atom_count: int
    passage_count: int
    source_atom_reference_count: int
    duplicate_source_atom_count: int
    input_overlap_count: int
    output_overlap_count: int
    hard_limit_exceeded_count: int
    unsplittable_passage_count: int
    token_count_total: int
    max_passage_tokens: int
    max_passage_duration_ms: int
    boundary_reason_counts: Mapping[str, int]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "boundary_reason_counts",
            MappingProxyType(dict(sorted(self.boundary_reason_counts.items()))),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "boundary_reason_counts": dict(self.boundary_reason_counts),
            "duplicate_source_atom_count": self.duplicate_source_atom_count,
            "hard_limit_exceeded_count": self.hard_limit_exceeded_count,
            "input_overlap_count": self.input_overlap_count,
            "max_passage_duration_ms": self.max_passage_duration_ms,
            "max_passage_tokens": self.max_passage_tokens,
            "output_overlap_count": self.output_overlap_count,
            "passage_count": self.passage_count,
            "source_atom_count": self.source_atom_count,
            "source_atom_reference_count": self.source_atom_reference_count,
            "token_count_total": self.token_count_total,
            "unsplittable_passage_count": self.unsplittable_passage_count,
        }


@dataclass(frozen=True)
class TimedGroupingResult:
    resource_id: str
    representation_id: str
    atoms: tuple[TimedTextAtom, ...]
    passages: tuple[TimedPassage, ...]
    grouper_fingerprint: GrouperFingerprint
    fingerprint_payload: Mapping[str, JSONValue]
    metrics: GroupingMetrics

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "fingerprint_payload",
            freeze_metadata(self.fingerprint_payload, "fingerprint_payload"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "fingerprint_payload": plain_json(self.fingerprint_payload),
            "grouper_fingerprint": self.grouper_fingerprint.value,
            "metrics": self.metrics.to_dict(),
            "passages": [item.to_dict() for item in self.passages],
            "representation_id": self.representation_id,
            "resource_id": self.resource_id,
        }


@dataclass(frozen=True)
class GroupingPolicyVariant:
    name: str
    policy: TimedChunkingPolicy

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name or self.name.strip() != self.name:
            raise ValueError("variant name must be non-empty without outer whitespace")
        if not isinstance(self.policy, TimedChunkingPolicy):
            raise ValueError("variant policy must be a TimedChunkingPolicy")


@dataclass(frozen=True)
class GroupingVariantResult:
    variant: str
    result: TimedGroupingResult

    def to_dict(self) -> dict[str, object]:
        return {"result": self.result.to_dict(), "variant": self.variant}


@dataclass(frozen=True)
class _BoundaryCandidate:
    end_index: int
    text: str
    token_count: int
    end_ms: int
    score: int
    token_distance: int
    duration_distance: int


@dataclass(frozen=True)
class _SelectedBoundary:
    candidate: _BoundaryCandidate
    reason: str
    hard_limit_exceeded: bool = False
    unsplittable: bool = False


def _join_atom_texts(atoms: Sequence[TimedTextAtom]) -> str:
    if not atoms:
        raise AssertionError("at least one atom is required")
    value = atoms[0].text
    for item in atoms[1:]:
        if value[-1].isspace() or item.text[0].isspace():
            value += item.text
        else:
            value += " " + item.text
    return value


def _count_tokens(token_counter: TokenCounter, text: str) -> int:
    value = token_counter.count(text)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TimedGroupingError("invalid_token_count")
    return value


def _distance(value: int, target: int) -> int:
    return abs(value - target) * 10 // target


def _boundary_score(
    atoms: tuple[TimedTextAtom, ...],
    end_index: int,
    *,
    token_count: int,
    duration_ms: int,
    end_ms: int,
    policy: TimedChunkingPolicy,
    hard_limit_risk: bool,
) -> tuple[int, int, int]:
    current = atoms[end_index]
    following = atoms[end_index + 1] if end_index + 1 < len(atoms) else None
    score = 0
    if hard_limit_risk:
        score += _SCORE_WEIGHTS["hard_limit_risk"]
    if following is not None:
        if (
            current.speaker is not None
            and following.speaker is not None
            and current.speaker != following.speaker
        ):
            score += _SCORE_WEIGHTS["speaker_change"]
        gap_ms = following.start_ms - end_ms
        if gap_ms >= policy.strong_pause_ms:
            score += _SCORE_WEIGHTS["strong_pause"]
        elif gap_ms >= policy.medium_pause_ms:
            score += _SCORE_WEIGHTS["medium_pause"]
    if current.text.rstrip()[-1] in _SENTENCE_ENDINGS:
        score += _SCORE_WEIGHTS["sentence_end"]
    if "\n" in current.text:
        score += _SCORE_WEIGHTS["line_break"]
    if token_count >= policy.soft_max_tokens or duration_ms >= policy.soft_max_duration_ms:
        score += _SCORE_WEIGHTS["soft_max"]
    if token_count >= policy.target_tokens:
        score += _SCORE_WEIGHTS["target_tokens"]
    if duration_ms >= policy.target_duration_ms:
        score += _SCORE_WEIGHTS["target_duration"]
    token_distance = _distance(token_count, policy.target_tokens)
    duration_distance = _distance(duration_ms, policy.target_duration_ms)
    return score - token_distance - duration_distance, token_distance, duration_distance


def _select_candidate(candidates: Sequence[_BoundaryCandidate], reason: str) -> _SelectedBoundary:
    candidate = max(
        candidates,
        key=lambda item: (
            item.score,
            -item.token_distance,
            -item.duration_distance,
            -item.end_index,
        ),
    )
    return _SelectedBoundary(candidate=candidate, reason=reason)


def _fingerprint_payload(
    policy: TimedChunkingPolicy,
    token_counter_fingerprint: TokenCounterFingerprint,
    token_count_kind: str,
    unsplittable: str,
) -> dict[str, JSONValue]:
    return cast(
        dict[str, JSONValue],
        {
            "algorithm": GROUPER_ALGORITHM,
            "boundary_window": "soft-min-through-first-soft-max-or-hard-risk-v1",
            "distance_penalty": "floor(10*absolute_delta/target)-per-axis-v1",
            "input_order": "strict-start-ms-nondecreasing-v1",
            "join_policy": JOIN_POLICY,
            "output_overlap_atoms": 0,
            "policy": policy.to_dict(),
            "score_weights": dict(_SCORE_WEIGHTS),
            "tie_order": [
                "score_desc",
                "token_distance_asc",
                "duration_distance_asc",
                "boundary_asc",
            ],
            "token_count_kind": token_count_kind,
            "token_counter_fingerprint": token_counter_fingerprint.value,
            "unsplittable": unsplittable,
            "version": 1,
        },
    )


def _validate_atoms(
    values: tuple[TimedTextAtom, ...],
    resource_identifier: str | None,
) -> str:
    if resource_identifier is None:
        if not values:
            raise TimedGroupingError("resource_id_required_for_empty_input")
        resource_identifier = values[0].resource_id
    try:
        validate_media_id(resource_identifier, "resource_id", kind=ID_RESOURCE)
    except ValueError as error:
        raise TimedGroupingError("invalid_resource_id") from error
    if any(not isinstance(item, TimedTextAtom) for item in values):
        raise TimedGroupingError("invalid_atom_type")
    if not values:
        return resource_identifier
    first = values[0]
    if any(
        item.resource_id != resource_identifier
        or item.producer_fingerprint != first.producer_fingerprint
        or item.normalization_fingerprint != first.normalization_fingerprint
        for item in values
    ):
        raise TimedGroupingError("mixed_atom_contract")
    if tuple(item.ordinal for item in values) != tuple(range(len(values))):
        raise TimedGroupingError("atom_ordinals_not_canonical")
    if len({item.atom_id for item in values}) != len(values):
        raise TimedGroupingError("duplicate_atom_id")
    if any(current.start_ms < previous.start_ms for previous, current in zip(values, values[1:])):
        raise TimedGroupingError("atoms_not_ordered")
    return resource_identifier


def _candidate(
    atoms: tuple[TimedTextAtom, ...],
    start_index: int,
    end_index: int,
    end_ms: int,
    policy: TimedChunkingPolicy,
    token_counter: TokenCounter,
    *,
    hard_limit_risk: bool,
) -> _BoundaryCandidate:
    text = _join_atom_texts(atoms[start_index : end_index + 1])
    token_count = _count_tokens(token_counter, text)
    duration_ms = end_ms - atoms[start_index].start_ms
    score, token_distance, duration_distance = _boundary_score(
        atoms,
        end_index,
        token_count=token_count,
        duration_ms=duration_ms,
        end_ms=end_ms,
        policy=policy,
        hard_limit_risk=hard_limit_risk,
    )
    return _BoundaryCandidate(
        end_index=end_index,
        text=text,
        token_count=token_count,
        end_ms=end_ms,
        score=score,
        token_distance=token_distance,
        duration_distance=duration_distance,
    )


def _next_would_exceed_hard_limit(
    atoms: tuple[TimedTextAtom, ...],
    start_index: int,
    end_index: int,
    end_ms: int,
    policy: TimedChunkingPolicy,
    token_counter: TokenCounter,
) -> bool:
    if end_index + 1 >= len(atoms):
        return False
    next_end_ms = max(end_ms, atoms[end_index + 1].end_ms)
    next_text = _join_atom_texts(atoms[start_index : end_index + 2])
    return (
        _count_tokens(token_counter, next_text) > policy.hard_max_tokens
        or next_end_ms - atoms[start_index].start_ms > policy.hard_max_duration_ms
    )


def _find_boundary(
    atoms: tuple[TimedTextAtom, ...],
    start_index: int,
    policy: TimedChunkingPolicy,
    token_counter: TokenCounter,
    unsplittable: str,
) -> _SelectedBoundary:
    candidates: list[_BoundaryCandidate] = []
    end_ms = atoms[start_index].end_ms
    hard_exceeded_without_safe_boundary = False
    for end_index in range(start_index, len(atoms)):
        end_ms = max(end_ms, atoms[end_index].end_ms)
        current = _candidate(
            atoms,
            start_index,
            end_index,
            end_ms,
            policy,
            token_counter,
            hard_limit_risk=False,
        )
        duration_ms = end_ms - atoms[start_index].start_ms
        hard_exceeded = (
            current.token_count > policy.hard_max_tokens
            or duration_ms > policy.hard_max_duration_ms
        )
        next_atom = atoms[end_index + 1] if end_index + 1 < len(atoms) else None
        safe_boundary = next_atom is None or next_atom.start_ms >= end_ms

        if hard_exceeded:
            if candidates:
                last = candidates[-1]
                boosted = _candidate(
                    atoms,
                    start_index,
                    last.end_index,
                    last.end_ms,
                    policy,
                    token_counter,
                    hard_limit_risk=True,
                )
                candidates[-1] = boosted
                return _select_candidate(candidates, "hard_limit")
            hard_exceeded_without_safe_boundary = True
            if not safe_boundary:
                continue
            if unsplittable == UNSPLITTABLE_REJECT:
                raise TimedGroupingError("unsplittable_hard_limit")
            return _SelectedBoundary(
                candidate=current,
                reason="unsplittable",
                hard_limit_exceeded=True,
                unsplittable=True,
            )

        if not safe_boundary:
            continue

        eligible = (
            current.token_count >= policy.soft_min_tokens
            and duration_ms >= policy.soft_min_duration_ms
        )
        hard_risk = _next_would_exceed_hard_limit(
            atoms,
            start_index,
            end_index,
            end_ms,
            policy,
            token_counter,
        )
        soft_max_reached = (
            current.token_count >= policy.soft_max_tokens
            or duration_ms >= policy.soft_max_duration_ms
        )
        if eligible or hard_risk or soft_max_reached or next_atom is None:
            current = _candidate(
                atoms,
                start_index,
                end_index,
                end_ms,
                policy,
                token_counter,
                hard_limit_risk=hard_risk,
            )
            candidates.append(current)

        if hard_risk:
            return _select_candidate(candidates, "hard_limit")
        if soft_max_reached:
            return _select_candidate(candidates, "soft_max")
        if next_atom is None:
            return _select_candidate(candidates, "end_of_input")

    if hard_exceeded_without_safe_boundary:
        raise AssertionError("an unsplittable boundary must terminate at end of input")
    raise AssertionError("the final atom must terminate a passage")


def _build_metrics(
    atoms: tuple[TimedTextAtom, ...],
    passages: tuple[TimedPassage, ...],
) -> GroupingMetrics:
    references = tuple(source for passage in passages for source in passage.source_atom_ids)
    overlap_count = 0
    prior_end = 0
    for index, item in enumerate(atoms):
        if index and item.start_ms < prior_end:
            overlap_count += 1
        prior_end = max(prior_end, item.end_ms)
    output_overlap_count = sum(
        current.start_ms < previous.end_ms
        for previous, current in zip(passages, passages[1:])
    )
    reasons = Counter(str(item.metadata["boundary_reason"]) for item in passages)
    return GroupingMetrics(
        source_atom_count=len(atoms),
        passage_count=len(passages),
        source_atom_reference_count=len(references),
        duplicate_source_atom_count=len(references) - len(set(references)),
        input_overlap_count=overlap_count,
        output_overlap_count=output_overlap_count,
        hard_limit_exceeded_count=sum(
            item.metadata["hard_limit_exceeded"] is True for item in passages
        ),
        unsplittable_passage_count=sum(item.metadata["unsplittable"] is True for item in passages),
        token_count_total=sum(item.token_count.count for item in passages),
        max_passage_tokens=max((item.token_count.count for item in passages), default=0),
        max_passage_duration_ms=max(
            (item.end_ms - item.start_ms for item in passages),
            default=0,
        ),
        boundary_reason_counts=reasons,
    )


def group_timed_atoms(
    atoms: Sequence[TimedTextAtom],
    *,
    policy: TimedChunkingPolicy,
    token_counter: TokenCounter,
    token_count_kind: str,
    resource_identifier: str | None = None,
    normalization_fingerprint: NormalizationFingerprint | None = None,
    unsplittable: str = UNSPLITTABLE_REJECT,
) -> TimedGroupingResult:
    """Group canonical ordered atoms without sorting, repairing, or truncating them."""
    if not isinstance(policy, TimedChunkingPolicy):
        raise TimedGroupingError("invalid_chunking_policy")
    if policy.overlap_atoms != 0:
        raise TimedGroupingError("output_overlap_not_supported")
    if unsplittable not in UNSPLITTABLE_POLICIES:
        raise TimedGroupingError("invalid_unsplittable_policy")
    if token_count_kind not in TOKEN_COUNT_KINDS:
        raise TimedGroupingError("invalid_token_count_kind")
    if not isinstance(token_counter, TokenCounter):
        raise TimedGroupingError("invalid_token_counter")
    if not isinstance(token_counter.fingerprint, TokenCounterFingerprint):
        raise TimedGroupingError("invalid_token_counter_fingerprint")
    if not isinstance(atoms, (list, tuple)):
        raise TimedGroupingError("atoms_must_be_a_sequence")

    values = tuple(atoms)
    resource_identifier = _validate_atoms(values, resource_identifier)
    if normalization_fingerprint is not None and not isinstance(
        normalization_fingerprint,
        NormalizationFingerprint,
    ):
        raise TimedGroupingError("invalid_normalization_fingerprint")
    if values:
        if (
            normalization_fingerprint is not None
            and normalization_fingerprint != values[0].normalization_fingerprint
        ):
            raise TimedGroupingError("mixed_atom_contract")
        effective_normalization = values[0].normalization_fingerprint
    elif normalization_fingerprint is None:
        raise TimedGroupingError("normalization_fingerprint_required_for_empty_input")
    else:
        effective_normalization = normalization_fingerprint
    payload = _fingerprint_payload(
        policy,
        token_counter.fingerprint,
        token_count_kind,
        unsplittable,
    )
    grouper_fingerprint = GrouperFingerprint.from_payload(payload)
    passage_representation_id = representation_id(
        resource_identifier,
        REPRESENTATION_TIMED_PASSAGE,
        grouper_fingerprint.value,
        effective_normalization.value,
    )

    grouped: list[TimedPassage] = []
    start_index = 0
    while start_index < len(values):
        selected = _find_boundary(
            values,
            start_index,
            policy,
            token_counter,
            unsplittable,
        )
        candidate = selected.candidate
        source_atoms = values[start_index : candidate.end_index + 1]
        text_digest = "sha256:" + hashlib.sha256(candidate.text.encode("utf-8")).hexdigest()
        ordinal = len(grouped)
        grouped.append(
            TimedPassage(
                passage_id=passage_id(
                    resource_identifier,
                    grouper_fingerprint.value,
                    ordinal,
                    source_atoms[0].atom_id,
                    source_atoms[-1].atom_id,
                    source_atoms[0].start_ms,
                    candidate.end_ms,
                    text_digest,
                ),
                resource_id=resource_identifier,
                representation_id=passage_representation_id,
                start_ms=source_atoms[0].start_ms,
                end_ms=candidate.end_ms,
                text=candidate.text,
                ordinal=ordinal,
                token_count=TokenCount(
                    count=candidate.token_count,
                    kind=token_count_kind,
                    counter_fingerprint=token_counter.fingerprint,
                ),
                source_atom_ids=tuple(item.atom_id for item in source_atoms),
                grouper_fingerprint=grouper_fingerprint,
                metadata={
                    "boundary_reason": selected.reason,
                    "boundary_score": candidate.score,
                    "hard_limit_exceeded": selected.hard_limit_exceeded,
                    "unsplittable": selected.unsplittable,
                },
            )
        )
        start_index = candidate.end_index + 1

    passages = tuple(grouped)
    metrics = _build_metrics(values, passages)
    if tuple(source for item in passages for source in item.source_atom_ids) != tuple(
        item.atom_id for item in values
    ):
        raise AssertionError("grouping must preserve exact-once source atom order")
    if metrics.output_overlap_count:
        raise AssertionError("grouping must not emit overlapping passages")
    return TimedGroupingResult(
        resource_id=resource_identifier,
        representation_id=passage_representation_id,
        atoms=values,
        passages=passages,
        grouper_fingerprint=grouper_fingerprint,
        fingerprint_payload=payload,
        metrics=metrics,
    )


def provisional_abc_policies() -> tuple[GroupingPolicyVariant, ...]:
    """Return the roadmap's provisional A/B/C experiment policies, not a selected default."""
    return (
        GroupingPolicyVariant(
            "A",
            TimedChunkingPolicy(
                soft_min_tokens=128,
                target_tokens=256,
                soft_max_tokens=384,
                hard_max_tokens=512,
                soft_min_duration_ms=20_000,
                target_duration_ms=45_000,
                soft_max_duration_ms=67_500,
                hard_max_duration_ms=90_000,
            ),
        ),
        GroupingPolicyVariant("B", TimedChunkingPolicy()),
        GroupingPolicyVariant(
            "C",
            TimedChunkingPolicy(
                soft_min_tokens=180,
                target_tokens=480,
                soft_max_tokens=720,
                hard_max_tokens=1_000,
                soft_min_duration_ms=20_000,
                target_duration_ms=90_000,
                soft_max_duration_ms=120_000,
                hard_max_duration_ms=120_000,
            ),
        ),
    )


def run_grouping_variants(
    atoms: Sequence[TimedTextAtom],
    *,
    token_counter: TokenCounter,
    token_count_kind: str,
    variants: Sequence[GroupingPolicyVariant] | None = None,
    resource_identifier: str | None = None,
    normalization_fingerprint: NormalizationFingerprint | None = None,
    unsplittable: str = UNSPLITTABLE_REJECT,
) -> tuple[GroupingVariantResult, ...]:
    """Run deterministic policy variants and return plumbing metrics without quality claims."""
    selected_variants = tuple(provisional_abc_policies() if variants is None else variants)
    if len({item.name for item in selected_variants}) != len(selected_variants):
        raise TimedGroupingError("duplicate_variant_name")
    return tuple(
        GroupingVariantResult(
            variant=item.name,
            result=group_timed_atoms(
                atoms,
                policy=item.policy,
                token_counter=token_counter,
                token_count_kind=token_count_kind,
                resource_identifier=resource_identifier,
                normalization_fingerprint=normalization_fingerprint,
                unsplittable=unsplittable,
            ),
        )
        for item in selected_variants
    )
